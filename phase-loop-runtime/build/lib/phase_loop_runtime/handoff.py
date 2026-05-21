from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .discovery import (
    PLAN_RE,
    WORKFLOW_EXECUTE_SKILLS,
    WORKFLOW_PLAN_SKILLS,
    latest_workflow_handoff,
    repo_identity,
)
from .events import event_path, read_events
from .git_topology import attach_git_topology
from .models import StateSnapshot
from .observability import (
    phase_loop_metrics_path,
    read_run_heartbeat,
    read_task_snapshot,
    read_work_unit_metrics,
    summarize_work_unit_metrics,
)
from .runtime_paths import (
    ensure_phase_loop_excluded,
    phase_loop_runs_dir,
    phase_loop_runs_dirs,
    phase_loop_stop_file,
    phase_loop_tui_handoff_file,
)
from .state import state_path


def tui_handoff_path(repo: Path) -> Path:
    return phase_loop_tui_handoff_file(repo)


def write_tui_handoff(
    repo: Path,
    roadmap: Path,
    snapshot: StateSnapshot,
    *,
    action: str,
    results: Iterable[Any] = (),
    mode: str = "product",
) -> Path:
    ensure_phase_loop_excluded(repo)
    snapshot = attach_git_topology(repo, snapshot)
    path = tui_handoff_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_tui_handoff(repo, roadmap, snapshot, action=action, results=results, mode=mode), encoding="utf-8")
    return path


def render_tui_handoff(
    repo: Path,
    roadmap: Path,
    snapshot: StateSnapshot,
    *,
    action: str,
    results: Iterable[Any] = (),
    mode: str = "product",
) -> str:
    latest = _latest_artifacts(repo, results)
    latest_launch = _read_launch_metadata(latest.get("metadata"))
    recent_metrics = read_work_unit_metrics(repo, limit=50)
    latest_metric = recent_metrics[-1] if recent_metrics else None
    current_status = _current_status(snapshot)
    roadmap_complete = current_status == "complete" and snapshot.current_phase is None
    lines = [
        "# Phase Loop TUI Handoff",
        "",
        f"Updated: {snapshot.timestamp}",
        f"Mode: {mode}",
        f"Action: {action}",
        f"Repo: {repo}",
        f"Roadmap: {_display_path(repo, roadmap)}",
        f"Current phase: {snapshot.current_phase or 'none'}",
        f"Current status: {current_status}",
        "",
        "## Machine Sources",
        "",
        f"- State JSON: `{_display_path(repo, state_path(repo))}`",
        f"- Event ledger: `{_display_path(repo, event_path(repo))}`",
        f"- Runs directory: `{_display_path(repo, phase_loop_runs_dir(repo))}`",
        f"- Metrics artifact: `{_display_path(repo, phase_loop_metrics_path(repo))}`",
        f"- Stop file: `{_display_path(repo, phase_loop_stop_file(repo))}`",
    ]
    if latest.get("metadata"):
        lines.append(f"- Latest launch metadata: `{_display_path(repo, Path(str(latest['metadata'])))}`")
    if latest.get("log"):
        lines.append(f"- Latest run log: `{_display_path(repo, Path(str(latest['log'])))}`")
    if latest.get("heartbeat"):
        lines.append(f"- Latest heartbeat: `{_display_path(repo, Path(str(latest['heartbeat'])))}`")
    if latest.get("terminal"):
        lines.append(f"- Latest terminal summary: `{_display_path(repo, Path(str(latest['terminal'])))}`")

    lines.extend(["", "## Current Status", ""])
    for phase, status in snapshot.phases.items():
        marker = "*" if phase == snapshot.current_phase else "-"
        lines.append(f"{marker} {phase}: {status}")

    lines.extend(["", "## What Happened", "", _what_happened(snapshot, action, current_status)])

    reentry_lines = [] if roadmap_complete else _reentry_lines(repo, roadmap)
    if reentry_lines:
        lines.extend(["", "## Reentry Context", ""])
        lines.extend(reentry_lines)

    if snapshot.human_required or current_status in {"blocked", "awaiting_phase_closeout"}:
        lines.extend(["", "## Required Action", "", _required_action(snapshot)])
        if snapshot.blocker_class:
            lines.append("")
            lines.append(f"Blocker class: `{snapshot.blocker_class}`")
        if snapshot.blocker_summary:
            lines.append(f"Blocker summary: {snapshot.blocker_summary}")
        if snapshot.required_human_inputs:
            lines.append("")
            lines.append("Required human inputs:")
            for item in snapshot.required_human_inputs:
                lines.append(f"- {item}")
    if snapshot.dirty_paths:
        lines.extend(["", "## Dirty Path Classification", ""])
        lines.append(f"- phase-owned dirty: `{str(snapshot.phase_owned_dirty).lower()}`")
        lines.append(f"- dirty paths: `{', '.join(snapshot.dirty_paths)}`")
        if snapshot.phase_owned_dirty_paths:
            lines.append(f"- phase-owned paths: `{', '.join(snapshot.phase_owned_dirty_paths)}`")
        if snapshot.unowned_dirty_paths:
            lines.append(f"- unowned paths: `{', '.join(snapshot.unowned_dirty_paths)}`")
        if snapshot.pre_existing_dirty_paths:
            lines.append(f"- pre-existing paths: `{', '.join(snapshot.pre_existing_dirty_paths)}`")

    terminal_summary = _current_terminal_summary(snapshot)
    if terminal_summary:
        lines.extend(["", "## Terminal Summary", ""])
        lines.extend(_terminal_summary_lines(terminal_summary))

    if latest_metric or recent_metrics:
        lines.extend(["", "## Metrics", ""])
        if latest_metric:
            lines.append(f"- latest_metric_id: `{latest_metric.get('metric_id')}`")
            lines.append(f"- metrics_artifact: `{_display_path(repo, phase_loop_metrics_path(repo))}`")
        lines.extend(_metrics_summary_lines(summarize_work_unit_metrics(recent_metrics)))

    if snapshot.latest_work_unit:
        lines.extend(["", "## Latest Work Unit", ""])
        lines.extend(_latest_work_unit_lines(snapshot.latest_work_unit))

    if latest_launch and not roadmap_complete:
        injection_lines = _injection_lines(latest_launch)
        if injection_lines:
            lines.extend(["", "## Injected Context", ""])
            lines.extend(injection_lines)
        taskledger_lines = _taskledger_lines(repo, latest_launch)
        if taskledger_lines:
            lines.extend(["", "## Native Team Ledger", ""])
            lines.extend(taskledger_lines)
        delegation_lines = _delegation_lines(latest_launch)
        if delegation_lines:
            lines.extend(["", "## Delegation Lineage", ""])
            lines.extend(delegation_lines)

    if snapshot.closeout_summary:
        lines.extend(["", "## Latest Closeout Decision", ""])
        lines.extend(_closeout_summary_lines(snapshot.closeout_summary))

    if snapshot.access_attempts:
        lines.extend(["", "## Access Attempts", ""])
        lines.append("Redacted metadata only:")
        lines.append("```json")
        lines.append(json.dumps(snapshot.access_attempts, indent=2, sort_keys=True))
        lines.append("```")

    if snapshot.git_topology:
        lines.extend(["", "## Git Topology", ""])
        lines.extend(_git_topology_lines(snapshot.git_topology))

    heartbeat = None if roadmap_complete else read_run_heartbeat(latest.get("heartbeat"))
    if heartbeat and _should_render_heartbeat(snapshot, heartbeat):
        lines.extend(["", "## Observed Liveness", ""])
        lines.extend(_heartbeat_lines(heartbeat))

    lines.extend(["", "## Monitor Command", "", "```bash"])
    lines.append(f"phase-loop monitor --repo {repo} --roadmap {_display_path(repo, roadmap)} --once --json")
    lines.append("```")

    lines.extend(["", "## Verify Before Resume", ""])
    lines.extend(_verify_commands(snapshot, current_status))

    lines.extend(["", "## Resume Command", ""])
    if current_status in {"blocked", "awaiting_phase_closeout"} or snapshot.human_required:
        lines.append("Resolve the required action first, then run:")
    elif current_status == "complete" and snapshot.current_phase is None:
        lines.append("No resume command is required; this roadmap is complete.")
        lines.append("")
        return _finish_handoff(lines, latest, repo, snapshot)
    lines.append("")
    lines.append("```bash")
    lines.append(f"phase-loop run --repo {repo} --roadmap {_display_path(repo, roadmap)} --max-phases 1 --observe")
    lines.append("```")

    return _finish_handoff(lines, latest, repo, snapshot)


def _finish_handoff(lines: list[str], latest: dict[str, str], repo: Path, snapshot: StateSnapshot) -> str:
    if latest:
        lines.extend(["", "## Latest Run Artifacts", ""])
        for key in ("root", "metadata", "log", "terminal"):
            if latest.get(key):
                lines.append(f"- {key}: `{_display_path(repo, Path(str(latest[key])))}`")
        if latest.get("heartbeat"):
            lines.append(f"- heartbeat: `{_display_path(repo, Path(str(latest['heartbeat'])))}`")

    if snapshot.ledger_warnings:
        lines.extend(["", "## Ledger Warnings", ""])
        lines.append("```json")
        lines.append(json.dumps(snapshot.ledger_warnings, indent=2, sort_keys=True))
        lines.append("```")

    lines.append("")
    return "\n".join(lines)


def _latest_work_unit_lines(work_unit: dict[str, Any]) -> list[str]:
    identity = work_unit.get("identity") if isinstance(work_unit.get("identity"), dict) else {}
    work_unit_id = work_unit.get("work_unit_id") or identity.get("work_unit_id")
    return [
        f"- id: `{work_unit_id}`",
        f"- phase_status: `{identity.get('phase') or work_unit.get('phase')}`",
        f"- work_unit_status: `{work_unit.get('status')}`",
        f"- kind: `{identity.get('kind') or work_unit.get('kind')}`",
        f"- lane: `{identity.get('lane_id') or work_unit.get('lane_id')}`",
        f"- attempt: `{identity.get('attempt') or work_unit.get('attempt')}`",
        f"- status: `{work_unit.get('status')}`",
    ]


def handoff_metadata(repo: Path, path: Path) -> dict[str, object]:
    return {
        "tui_handoff_path": str(path),
        "tui_handoff_exists": path.exists(),
    }


def _latest_artifacts(repo: Path, results: Iterable[Any]) -> dict[str, str]:
    for result in reversed(list(results)):
        log_path = getattr(result, "log_path", None)
        if log_path:
            log = Path(str(log_path))
            return {
                "root": str(log.parent),
                "metadata": str(log.parent / "launch.json"),
                "log": str(log),
                "heartbeat": str(log.parent / "heartbeat.json"),
                "terminal": str(log.parent / "terminal-summary.json"),
            }

    for event in reversed(read_events(repo)):
        metadata = event.get("metadata") or {}
        artifacts = metadata.get("artifacts") or {}
        if isinstance(artifacts, dict) and artifacts:
            return {key: str(value) for key, value in artifacts.items() if key in {"root", "metadata", "log", "heartbeat", "terminal"}}
        launch = metadata.get("launch") or {}
        if isinstance(launch, dict) and launch.get("log_path"):
            log = Path(str(launch["log_path"]))
            heartbeat = launch.get("heartbeat_path") or str(log.parent / "heartbeat.json")
            terminal = launch.get("terminal_path") or str(log.parent / "terminal-summary.json")
            return {"root": str(log.parent), "metadata": str(log.parent / "launch.json"), "log": str(log), "heartbeat": str(heartbeat), "terminal": str(terminal)}
    latest_run = _latest_run_dir(repo)
    if latest_run:
        return {
            "root": str(latest_run),
            "metadata": str(latest_run / "launch.json"),
            "log": str(latest_run / "output.log"),
            "heartbeat": str(latest_run / "heartbeat.json"),
            "terminal": str(latest_run / "terminal-summary.json"),
        }
    return {}


def _read_launch_metadata(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _latest_run_dir(repo: Path) -> Path | None:
    candidates: list[Path] = []
    for runs in phase_loop_runs_dirs(repo):
        if runs.exists():
            candidates.extend(path for path in runs.iterdir() if path.is_dir())
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _what_happened(snapshot: StateSnapshot, action: str, current_status: str) -> str:
    phase = snapshot.current_phase or "the roadmap"
    terminal = _current_terminal_summary(snapshot) or {}
    if current_status == "complete" and snapshot.current_phase is None:
        return "All phases in the roadmap are complete."
    if snapshot.human_required:
        if snapshot.blocker_class:
            return f"The phase loop stopped at `{phase}` because `{snapshot.blocker_class}` requires operator action."
        return f"The phase loop stopped at `{phase}` because operator action is required."
    if current_status == "blocked":
        return f"The phase loop stopped at `{phase}` because a non-human repair is required before it can continue."
    if current_status == "awaiting_phase_closeout":
        return f"`{phase}` finished with verified dirty phase output. `manual` remains the default closeout policy until the operator opts into `commit` or `push`."
    if current_status == "unknown":
        return f"The phase loop could not safely classify `{phase}` after `{action}`. Inspect the linked logs and ledger before resuming."
    if terminal.get("phase") and terminal.get("terminal_status"):
        return (
            f"The latest observed child exit for `{terminal['phase']}` ended with terminal status "
            f"`{terminal['terminal_status']}`."
        )
    if current_status == "complete":
        return f"`{phase}` is complete. The next run will continue from the next non-complete phase."
    if current_status == "planned":
        return f"`{phase}` is the nearest downstream phase with a current plan artifact. The next run should execute that phase."
    if current_status == "unplanned":
        return f"`{phase}` is the nearest downstream phase without a current plan artifact. The next run should plan that phase."
    if current_status == "executed":
        return f"`{phase}` has executed but still needs repair or completion evidence before the loop can advance."
    return f"The latest loop action was `{action}` and the current phase is `{phase}`."


def _required_action(snapshot: StateSnapshot) -> str:
    pr_action = _pr_required_action(snapshot.git_topology or {})
    if pr_action:
        return pr_action
    if snapshot.blocker_class == "repeated_verification_failure":
        terminal = snapshot.terminal_summary or {}
        missing_plan = terminal.get("terminal_blocker") if isinstance(terminal.get("terminal_blocker"), dict) else {}
        if snapshot.blocker_summary:
            return (
                f"{snapshot.blocker_summary} Inspect the linked terminal summary and event ledger, then rerun the "
                "planning command only after confirming the required plan artifact path."
            )
        if missing_plan:
            return "A child turn exited successfully but did not produce the machine-required artifact. Inspect state and rerun the bounded planning command."
    if snapshot.current_phase and snapshot.phases.get(snapshot.current_phase) == "blocked" and not snapshot.human_required:
        return (
            "Start with `.phase-loop/tui-handoff.md` or `phase-loop handoff`, then confirm the current "
            "machine state with `phase-loop status --json` before making a bounded non-human repair. "
            "If this repo has not migrated yet, `.codex/phase-loop/` remains a legacy read fallback."
        )
    if snapshot.blocker_class == "dirty_worktree_conflict":
        return (
            "Review the target repo worktree and the dirty path classification, then preserve phase-owned output "
            "or isolate unowned/pre-existing paths before rerunning the loop."
        )
    if snapshot.current_phase and snapshot.phases.get(snapshot.current_phase) == "awaiting_phase_closeout":
        return (
            "Preserve the verified phase-owned output in the target repo before rerunning the loop. "
            "`manual` is the default closeout policy; `commit` and `push` are explicit operator opt-ins. "
            "This state is not a human-required blocker, but the loop will not relaunch the phase until closeout is resolved."
        )
    if snapshot.blocker_class == "branch_sync_conflict":
        return (
            "Align the target branch with its upstream before rerunning release "
            "dispatch. Do not cut a release from a local-only commit."
        )
    if snapshot.blocker_summary:
        return snapshot.blocker_summary
    if snapshot.human_required:
        return "Resolve the human-required blocker recorded in the machine state before rerunning the loop."
    return (
        "Start with `.phase-loop/tui-handoff.md` or `phase-loop handoff`, then confirm the current "
        "machine state with `phase-loop status --json` before making a bounded non-human repair. "
        "If this repo has not migrated yet, `.codex/phase-loop/` remains a legacy read fallback."
    )


def _pr_required_action(topology: dict[str, Any]) -> str | None:
    pr_url = topology.get("pr_url")
    if not pr_url:
        return None
    review_decision = topology.get("pr_review_decision")
    mergeable = topology.get("pr_mergeable")
    head = topology.get("pr_head_ref") or topology.get("matching_remote_ref") or "the PR branch"
    base = topology.get("pr_base_ref") or topology.get("base_ref") or "the base branch"
    if review_decision == "REVIEW_REQUIRED":
        return (
            f"Approve and merge PR {pr_url} from `{head}` into `{base}`, then fetch the target repo "
            "so local branch state matches the merged upstream before rerunning the loop."
        )
    if mergeable == "MERGEABLE":
        return (
            f"Merge PR {pr_url} from `{head}` into `{base}`, then fetch the target repo so local branch "
            "state matches the merged upstream before rerunning the loop."
        )
    return (
        f"Resolve PR {pr_url} from `{head}` into `{base}`, then fetch the target repo so local branch "
        "state matches the merged upstream before rerunning the loop."
    )


def _verify_commands(snapshot: StateSnapshot, current_status: str) -> list[str]:
    commands = ["```bash"]
    if current_status == "blocked" and not snapshot.human_required:
        commands.extend(
            [
                "phase-loop handoff",
                "phase-loop status --json",
                "phase-loop monitor --once --json",
            ]
        )
    elif snapshot.blocker_class == "dirty_worktree_conflict":
        commands.extend(
            [
                "git status --short --branch",
                "git fetch origin main --tags --prune",
                "git rev-parse HEAD origin/main",
            ]
        )
    elif current_status == "awaiting_phase_closeout":
        commands.extend(
            [
                "git status --short --branch",
                "phase-loop status --json",
            ]
        )
    elif snapshot.blocker_class == "branch_sync_conflict":
        commands.extend(
            [
                "git status --short --branch",
                "git fetch origin main --tags --prune",
                "git rev-parse HEAD origin/main",
            ]
        )
    elif current_status == "blocked" or snapshot.human_required:
        if snapshot.human_required:
            commands.append("phase-loop status --json")
        else:
            commands.extend(
                [
                    "phase-loop handoff",
                    "phase-loop status --json",
                ]
            )
    else:
        commands.append("phase-loop status")
    commands.append("```")
    return commands


def _git_topology_lines(topology: dict[str, Any]) -> list[str]:
    if topology.get("available") is False:
        return [f"- unavailable: {topology.get('reason', 'unknown')}"]

    lines = [
        f"- branch: `{topology.get('branch', 'unknown')}`",
        f"- head: `{topology.get('head', 'unknown')}`",
    ]
    if topology.get("base_ref"):
        base = topology["base_ref"]
        ahead = topology.get("ahead_of_base")
        behind = topology.get("behind_base")
        divergence = []
        if ahead is not None:
            divergence.append(f"ahead {ahead}")
        if behind is not None:
            divergence.append(f"behind {behind}")
        suffix = f" ({', '.join(divergence)})" if divergence else ""
        lines.append(f"- base: `{base}`{suffix}")
    if topology.get("upstream_ref"):
        lines.append(f"- upstream: `{topology['upstream_ref']}`")
    if topology.get("target_push_ref"):
        lines.append(f"- target push ref: `{topology['target_push_ref']}`")
    if topology.get("pr_url"):
        lines.append(f"- pull request: {topology['pr_url']}")
    if topology.get("pr_review_decision"):
        lines.append(f"- PR review decision: `{topology['pr_review_decision']}`")
    if topology.get("pr_mergeable"):
        lines.append(f"- PR mergeable: `{topology['pr_mergeable']}`")
    if topology.get("matching_remote_ref"):
        lines.append(f"- matching remote ref: `{topology['matching_remote_ref']}`")
    if topology.get("pr_head_ref") or topology.get("pr_base_ref"):
        lines.append(
            f"- PR refs: head `{topology.get('pr_head_ref', 'unknown')}`, base `{topology.get('pr_base_ref', 'unknown')}`"
        )
    if topology.get("status_short_branch"):
        lines.extend(["", "```text", str(topology["status_short_branch"]), "```"])
    return lines


def _heartbeat_lines(heartbeat: dict[str, Any]) -> list[str]:
    lines = [
        f"- process alive: `{heartbeat.get('process_alive', False)}`",
        f"- quiet level: `{heartbeat.get('quiet_level', 'unknown')}`",
    ]
    if heartbeat.get("elapsed_seconds") is not None:
        lines.append(f"- elapsed: `{heartbeat['elapsed_seconds']}s`")
    if heartbeat.get("seconds_since_log_update") is not None:
        lines.append(f"- seconds since log update: `{heartbeat['seconds_since_log_update']}s`")
    if heartbeat.get("recommended_action"):
        lines.append(f"- recommended action: {heartbeat['recommended_action']}")
    if heartbeat.get("nudge_prompt"):
        lines.extend(["", "Paste-ready nudge if operator judgment says the child may be stalled:", "", "```text", str(heartbeat["nudge_prompt"]), "```"])
    return lines


def _display_path(repo: Path, path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        return str(resolved.relative_to(repo.resolve()))
    except ValueError:
        return str(resolved)


def _current_status(snapshot: StateSnapshot) -> str:
    if snapshot.current_phase:
        return snapshot.phases.get(snapshot.current_phase, "unknown")
    if snapshot.phases and all(status == "complete" for status in snapshot.phases.values()):
        return "complete"
    return "unknown"


def _should_render_heartbeat(snapshot: StateSnapshot, heartbeat: dict[str, Any]) -> bool:
    terminal = _current_terminal_summary(snapshot) or {}
    if terminal.get("terminal_status") and not heartbeat.get("process_alive", False):
        return False
    return True


def _closeout_summary_lines(summary: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if summary.get("closeout_mode"):
        lines.append(f"- mode: `{summary['closeout_mode']}`")
    if summary.get("closeout_action"):
        lines.append(f"- action: `{summary['closeout_action']}`")
    if summary.get("closeout_commit"):
        lines.append(f"- commit: `{summary['closeout_commit']}`")
    if summary.get("closeout_push_ref"):
        lines.append(f"- push target: `{summary['closeout_push_ref']}`")
    if summary.get("closeout_refusal_reason"):
        lines.append(f"- refusal reason: `{summary['closeout_refusal_reason']}`")
    if summary.get("verification_status"):
        lines.append(f"- verification: `{summary['verification_status']}`")
    return lines


def _terminal_summary_lines(summary: dict[str, Any]) -> list[str]:
    lines = [
        f"- terminal status: `{summary.get('terminal_status', 'unknown')}`",
        f"- verification: `{summary.get('verification_status', 'unknown')}`",
        f"- phase-owned dirty: `{str(summary.get('phase_owned_dirty', False)).lower()}`",
    ]
    if summary.get("metric_id"):
        lines.append(f"- metric_id: `{summary['metric_id']}`")
    closeout = summary.get("phase_loop_closeout")
    if isinstance(closeout, dict):
        outcome = closeout.get("terminal_status") or closeout.get("outcome") or "unknown"
        sb = closeout.get("source_bundle") if isinstance(closeout.get("source_bundle"), dict) else {}
        phase_id = sb.get("phase_id") or closeout.get("pipeline_phase_id") or "unknown"
        bundle_sha = sb.get("sha256") or closeout.get("source_bundle_sha256")
        lines.append(
            "- Pipeline closeout: "
            f"`{outcome}` "
            f"for `{phase_id}`"
        )
        if bundle_sha:
            lines.append(f"- source bundle: `{str(bundle_sha)[:12]}`")
    latest_verify = summary.get("latest_verification_unit")
    if isinstance(latest_verify, dict) and latest_verify.get("work_unit_id"):
        lines.append(f"- latest verification unit: `{latest_verify['work_unit_id']}`")
    verification_commands = summary.get("verification_commands") or ()
    if verification_commands:
        failed = [
            str(command.get("command"))
            for command in verification_commands
            if isinstance(command, dict) and command.get("status") not in {"passed", "complete", "ok"}
        ]
        lines.append(f"- verification commands: `{len(verification_commands)}`")
        if failed:
            lines.append(f"- failed verification commands: `{', '.join(failed)}`")
    if summary.get("next_action"):
        lines.append(f"- next action: {summary['next_action']}")
    blocker = summary.get("terminal_blocker")
    if isinstance(blocker, dict) and blocker.get("blocker_class"):
        lines.append(f"- terminal blocker: `{blocker['blocker_class']}`")
        if blocker.get("blocker_summary"):
            lines.append(f"- blocker summary: {blocker['blocker_summary']}")
    dirty_paths = summary.get("dirty_paths") or ()
    if dirty_paths:
        lines.append(f"- dirty paths: `{', '.join(dirty_paths)}`")
    return lines


def _metrics_summary_lines(summary: dict[str, object]) -> list[str]:
    if not summary.get("total"):
        return []
    lines = [f"- recent_total: `{summary.get('total')}`"]
    for label, key in (
        ("by_executor", "by_executor"),
        ("by_model", "by_model"),
        ("by_effort", "by_effort"),
        ("by_terminal_status", "by_terminal_status"),
        ("by_verification_status", "by_verification_status"),
        ("by_blocker_class", "by_blocker_class"),
    ):
        bucket = summary.get(key)
        if isinstance(bucket, dict) and bucket:
            lines.append(f"- {label}: `{_format_counts(bucket)}`")
    return lines


def _format_counts(bucket: dict[str, object]) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(bucket.items()))


def _injection_lines(metadata: dict[str, Any]) -> list[str]:
    bundle_id = metadata.get("skill_bundle_id")
    bundle_sha = metadata.get("skill_bundle_sha256")
    harness_target = metadata.get("harness_target")
    injection_mode = metadata.get("injection_mode")
    fallback_mode = metadata.get("fallback_mode")
    recommended_roots = metadata.get("recommended_installed_roots") or []
    installed_roots = metadata.get("installed_skill_roots") or []
    warnings = metadata.get("installed_skill_warnings") or []
    bridge_inventory = metadata.get("bridge_skill_inventory") or []
    plugin_artifacts = metadata.get("plugin_bundle_artifacts") if isinstance(metadata.get("plugin_bundle_artifacts"), dict) else {}
    expected_skill_pack = metadata.get("expected_skill_pack") or []
    dispatch = metadata.get("dispatch_decision") if isinstance(metadata.get("dispatch_decision"), dict) else {}
    dispatch_summary = metadata.get("dispatch_summary")
    claude_mode = metadata.get("claude_execution_mode")
    team_policy = metadata.get("claude_team_policy") if isinstance(metadata.get("claude_team_policy"), dict) else {}
    eligibility = metadata.get("phase_team_eligibility") if isinstance(metadata.get("phase_team_eligibility"), dict) else {}
    if not any(
        (
            bundle_id,
            bundle_sha,
            harness_target,
            injection_mode,
            recommended_roots,
            warnings,
            bridge_inventory,
            expected_skill_pack,
            plugin_artifacts,
            dispatch,
            dispatch_summary,
            claude_mode,
            team_policy,
            eligibility,
        )
    ):
        return []
    lines = []
    if harness_target:
        lines.append(f"- harness target: `{harness_target}`")
    if dispatch.get("selected_executor"):
        lines.append(f"- selected executor: `{dispatch['selected_executor']}`")
    if dispatch.get("source"):
        lines.append(f"- dispatch source: `{dispatch['source']}`")
    if dispatch.get("selected_via"):
        lines.append(f"- dispatch path: `{dispatch['selected_via']}`")
    if dispatch.get("fallback_applied"):
        lines.append("- dispatch fallback applied: `true`")
    if dispatch.get("considered_executors"):
        lines.append(f"- considered executors: `{', '.join(dispatch['considered_executors'])}`")
    if dispatch.get("blocked_reason"):
        lines.append(f"- dispatch blocked reason: `{dispatch['blocked_reason']}`")
    if dispatch.get("required_capabilities"):
        lines.append(f"- required capabilities: `{', '.join(dispatch['required_capabilities'])}`")
    if dispatch_summary:
        lines.append(f"- dispatch summary: {dispatch_summary}")
    if bundle_id:
        lines.append(f"- injected bundle: `{bundle_id}`")
    if bundle_sha:
        lines.append(f"- bundle sha256: `{bundle_sha}`")
    if injection_mode:
        lines.append(f"- delivery mode: `{injection_mode}`")
    if fallback_mode:
        lines.append(f"- fallback mode: `{fallback_mode}`")
    if expected_skill_pack:
        lines.append(f"- expected skill pack: `{', '.join(expected_skill_pack)}`")
    if plugin_artifacts.get("plugin_dir"):
        lines.append(f"- repo-owned plugin dir: `{plugin_artifacts['plugin_dir']}`")
    if plugin_artifacts.get("settings_path"):
        lines.append(f"- generated settings artifact: `{plugin_artifacts['settings_path']}`")
    if plugin_artifacts.get("agents_path"):
        lines.append(f"- generated agents artifact: `{plugin_artifacts['agents_path']}`")
    if plugin_artifacts.get("mcp_config_path"):
        lines.append(f"- generated mcp artifact: `{plugin_artifacts['mcp_config_path']}`")
    if plugin_artifacts.get("artifact_names"):
        lines.append(f"- plugin artifact inventory: `{', '.join(plugin_artifacts['artifact_names'])}`")
    if claude_mode:
        lines.append(f"- Claude execution mode: `{claude_mode}`")
    if team_policy.get("maturity_label"):
        lines.append(f"- TEAMGOV maturity: `{team_policy['maturity_label']}`")
    if team_policy.get("max_teammates") is not None:
        lines.append(f"- TEAMGOV max teammates: `{team_policy['max_teammates']}`")
    if team_policy.get("max_native_tasks") is not None:
        lines.append(f"- TEAMGOV max native tasks: `{team_policy['max_native_tasks']}`")
    if team_policy.get("max_fanout") is not None:
        lines.append(f"- TEAMGOV max fanout: `{team_policy['max_fanout']}`")
    if team_policy.get("worktree_posture"):
        lines.append(f"- TEAMGOV worktree posture: `{team_policy['worktree_posture']}`")
    if team_policy.get("disallowed_tools"):
        lines.append(f"- TEAMGOV denied tools: `{', '.join(team_policy['disallowed_tools'])}`")
    if eligibility.get("allowed_execution_modes"):
        lines.append(f"- phase team-safe modes: `{', '.join(eligibility['allowed_execution_modes'])}`")
    if eligibility.get("reason"):
        lines.append(f"- phase team eligibility: `{eligibility['reason']}`")
    if eligibility.get("invalid_reasons"):
        lines.append(f"- TEAMGOV denial reasons: `{', '.join(eligibility['invalid_reasons'])}`")
    if recommended_roots:
        lines.append(f"- recommended installed roots: `{', '.join(recommended_roots)}`")
    if installed_roots:
        lines.append(f"- discovered installed roots: `{', '.join(installed_roots)}`")
    for record in bridge_inventory:
        if not isinstance(record, dict):
            continue
        skill_name = record.get("skill_name") or "unknown"
        parity_status = record.get("parity_status") or "unknown"
        lines.append(f"- bridge skill `{skill_name}` parity: `{parity_status}`")
        if record.get("repair_target"):
            lines.append(f"  - repair target: `{record['repair_target']}`")
    if warnings:
        lines.append("- installed-skill parity warnings:")
        for warning in warnings:
            lines.append(f"  - {warning}")
    return lines


def _reentry_lines(repo: Path, roadmap: Path) -> list[str]:
    lines: list[str] = []
    trusted = _trusted_workflow_handoff(repo, roadmap)
    if trusted:
        if trusted.get("workflow_skill"):
            lines.append(f"- trusted workflow handoff: `{trusted['workflow_skill']}`")
        if trusted.get("originating_harness"):
            lines.append(f"- trusted originating harness: `{trusted['originating_harness']}`")
        if trusted.get("phase"):
            lines.append(f"- trusted handoff phase: `{trusted['phase']}`")
        if trusted.get("status"):
            lines.append(f"- trusted handoff status: `{trusted['status']}`")
        if trusted.get("artifact"):
            lines.append(f"- trusted handoff artifact: `{_display_path(repo, Path(str(trusted['artifact'])))}`")
    manual = _latest_manual_import(repo, roadmap)
    if manual:
        lines.append(f"- latest manual import phase: `{manual.get('phase') or 'unknown'}`")
        lines.append(f"- latest manual import status: `{manual.get('status') or 'unknown'}`")
        if manual.get("originating_harness"):
            lines.append(f"- latest manual import harness: `{manual['originating_harness']}`")
        if manual.get("workflow_skill"):
            lines.append(f"- latest manual import workflow skill: `{manual['workflow_skill']}`")
        if manual.get("artifact"):
            lines.append(f"- latest manual import artifact: `{_display_path(repo, Path(str(manual['artifact'])))}`")
        for record in manual.get("bridge_skill_inventory") or []:
            if not isinstance(record, dict):
                continue
            skill_name = record.get("skill_name") or "unknown"
            parity_status = record.get("parity_status") or "unknown"
            lines.append(f"- latest manual import bridge skill `{skill_name}` parity: `{parity_status}`")
        warnings = manual.get("installed_skill_warnings") or []
        if warnings:
            lines.append("- latest manual import parity warnings:")
            for warning in warnings:
                lines.append(f"  - {warning}")
    return lines


def _taskledger_lines(repo: Path, metadata: dict[str, Any]) -> list[str]:
    task_ledger = metadata.get("task_ledger_artifacts") if isinstance(metadata.get("task_ledger_artifacts"), dict) else {}
    if not task_ledger:
        return []
    snapshot_path = task_ledger.get("snapshot_path")
    hook_manifest_path = task_ledger.get("hook_manifest_path")
    snapshot = read_task_snapshot(snapshot_path)
    lines: list[str] = []
    if snapshot_path:
        lines.append(f"- task snapshot: `{_display_path(repo, Path(str(snapshot_path)))}`")
    if hook_manifest_path:
        lines.append(f"- hook manifest: `{_display_path(repo, Path(str(hook_manifest_path)))}`")
    hook_inventory = task_ledger.get("hook_policy_inventory") or []
    if hook_inventory:
        event_names = [record.get("event_name") for record in hook_inventory if isinstance(record, dict) and record.get("event_name")]
        if event_names:
            lines.append(f"- hook inventory: `{', '.join(event_names)}`")
    freshness = _taskledger_freshness(snapshot, metadata)
    lines.append(f"- task snapshot freshness: `{freshness}`")
    if snapshot and isinstance(snapshot.get("latest_activity"), dict):
        latest_activity = snapshot["latest_activity"]
        if latest_activity.get("classification"):
            lines.append(f"- wait classification: `{latest_activity['classification']}`")
        if latest_activity.get("summary"):
            lines.append(f"- latest team activity: {latest_activity['summary']}")
    execution_mode = metadata.get("claude_execution_mode")
    if execution_mode:
        lines.append(f"- native execution mode: `{execution_mode}`")
    return lines


def _taskledger_freshness(snapshot: dict[str, Any] | None, metadata: dict[str, Any]) -> str:
    if not snapshot:
        return "missing"
    terminal = metadata.get("terminal_summary") if isinstance(metadata.get("terminal_summary"), dict) else {}
    if terminal.get("terminal_status"):
        return "superseded"
    if snapshot.get("freshness_timestamp"):
        return "fresh"
    return "missing"


def _trusted_workflow_handoff(repo: Path, roadmap: Path) -> dict[str, str] | None:
    identity = repo_identity(repo)
    handoff = latest_workflow_handoff(
        identity,
        repo,
        roadmap,
        (*WORKFLOW_EXECUTE_SKILLS, *WORKFLOW_PLAN_SKILLS),
    )
    if not handoff:
        return None
    artifact = handoff.get("artifact")
    phase = None
    if artifact:
        match = PLAN_RE.search(Path(str(artifact)).name)
        if match:
            phase = match.group(1).upper()
    return {
        "workflow_skill": str(handoff.get("workflow_skill") or ""),
        "originating_harness": str(handoff.get("originating_harness") or ""),
        "phase": str(phase or ""),
        "status": str(handoff.get("automation_status") or ""),
        "artifact": str(artifact or ""),
    }


def _latest_manual_import(repo: Path, roadmap: Path) -> dict[str, Any] | None:
    roadmap_value = str(roadmap.resolve())
    for event in reversed(read_events(repo)):
        event_roadmap = event.get("roadmap")
        if isinstance(event_roadmap, str) and event_roadmap:
            if str(Path(event_roadmap).expanduser().resolve()) != roadmap_value:
                continue
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        manual = metadata.get("manual_import") if isinstance(metadata.get("manual_import"), dict) else {}
        if not manual:
            continue
        return {
            "phase": event.get("phase"),
            "status": event.get("status"),
            "originating_harness": manual.get("originating_harness"),
            "workflow_skill": manual.get("workflow_skill"),
            "artifact": manual.get("artifact"),
            "installed_skill_warnings": manual.get("installed_skill_warnings", []),
            "bridge_skill_inventory": manual.get("bridge_skill_inventory", []),
        }
    return None


def _delegation_lines(metadata: dict[str, Any]) -> list[str]:
    request = metadata.get("delegation_request") if isinstance(metadata.get("delegation_request"), dict) else {}
    decision = metadata.get("delegation_decision") if isinstance(metadata.get("delegation_decision"), dict) else {}
    parent_child = metadata.get("parent_child") if isinstance(metadata.get("parent_child"), dict) else {}
    if not any((request, decision, parent_child)):
        return []
    lines: list[str] = []
    if request.get("request_id"):
        lines.append(f"- request id: `{request['request_id']}`")
    if request.get("product_action"):
        lines.append(f"- requested action: `{request['product_action']}`")
    if request.get("target_executor"):
        lines.append(f"- requested executor: `{request['target_executor']}`")
    if request.get("priority"):
        lines.append(f"- priority: `{request['priority']}`")
    if request.get("owned_files"):
        lines.append(f"- owned files: `{', '.join(request['owned_files'])}`")
    if request.get("expected_output"):
        lines.append(f"- expected output: {request['expected_output']}")
    if request.get("reason"):
        lines.append(f"- request reason: {request['reason']}")
    if decision.get("status"):
        lines.append(f"- decision: `{decision['status']}`")
    if decision.get("reason_code"):
        lines.append(f"- decision code: `{decision['reason_code']}`")
    if decision.get("summary"):
        lines.append(f"- decision summary: {decision['summary']}")
    if decision.get("selected_executor"):
        lines.append(f"- approved executor: `{decision['selected_executor']}`")
    if parent_child.get("parent_phase"):
        lines.append(f"- parent phase: `{parent_child['parent_phase']}`")
    if parent_child.get("parent_executor"):
        lines.append(f"- parent executor: `{parent_child['parent_executor']}`")
    if parent_child.get("parent_run_id"):
        lines.append(f"- parent run id: `{parent_child['parent_run_id']}`")
    if parent_child.get("child_action"):
        lines.append(f"- child action: `{parent_child['child_action']}`")
    if parent_child.get("child_executor"):
        lines.append(f"- child executor: `{parent_child['child_executor']}`")
    if parent_child.get("child_artifact_root"):
        lines.append(f"- child artifacts: `{parent_child['child_artifact_root']}`")
    if parent_child.get("child_worktree_root"):
        lines.append(f"- child worktree: `{parent_child['child_worktree_root']}`")
    if parent_child.get("child_closeout_result"):
        lines.append(f"- child closeout: `{json.dumps(parent_child['child_closeout_result'], sort_keys=True)}`")
    if parent_child.get("observed_launch_path"):
        lines.append(f"- launch metadata: `{parent_child['observed_launch_path']}`")
    return lines


def _current_terminal_summary(snapshot: StateSnapshot) -> dict[str, Any] | None:
    if not snapshot.terminal_summary:
        return None
    if snapshot.current_phase is None and snapshot.phases and all(status == "complete" for status in snapshot.phases.values()):
        if snapshot.terminal_summary.get("terminal_status") != "complete":
            return None
    phase = snapshot.terminal_summary.get("phase")
    closeout = snapshot.closeout_summary or {}
    if (
        phase
        and phase != snapshot.current_phase
        and closeout.get("phase") == phase
        and closeout.get("closeout_commit")
    ):
        return None
    return snapshot.terminal_summary
