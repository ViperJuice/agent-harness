from __future__ import annotations

import json
from pathlib import Path

from .discovery import execution_policy_for_action, find_plan_artifact, parse_execution_policy
from .git_topology import attach_git_topology
from .models import StateSnapshot

EXECUTION_POLICY_ACTIONS = ("plan", "execute", "repair", "review")


def render_status(snapshot: StateSnapshot, as_json: bool = False) -> str:
    snapshot = attach_git_topology(Path(snapshot.repo), snapshot)
    if as_json:
        payload = snapshot.to_json()
        policy_block = _resolve_execution_policy_block(snapshot)
        if policy_block:
            payload["execution_policy"] = policy_block
        return json.dumps(payload, indent=2, sort_keys=True)
    lines = [f"Roadmap: {snapshot.roadmap}", "Phase statuses:"]
    for phase, status in snapshot.phases.items():
        marker = "*" if phase == snapshot.current_phase else " "
        lines.append(f"{marker} {phase}: {status}")
    if snapshot.current_phase is None and snapshot.phases and all(status == "complete" for status in snapshot.phases.values()):
        lines.append("Roadmap complete: all phases are complete")
    terminal_summary = _current_terminal_summary(snapshot)
    if terminal_summary:
        lines.extend(_terminal_summary_lines(terminal_summary))
    if snapshot.metrics_summary:
        lines.extend(_metrics_summary_lines(snapshot.metrics_summary))
    if snapshot.human_required:
        lines.append(f"Human required: {snapshot.blocker_class or 'unknown'}")
        if snapshot.blocker_summary:
            lines.append(f"Blocker: {snapshot.blocker_summary}")
    elif snapshot.current_phase and snapshot.phases.get(snapshot.current_phase) == "awaiting_phase_closeout":
        lines.append("Closeout required: verified dirty phase output is awaiting preservation")
    lines.extend(_live_git_topology_lines(snapshot.git_topology))
    if snapshot.dirty_paths:
        lines.append(f"Dirty paths: {', '.join(snapshot.dirty_paths)}")
        lines.append(f"Phase-owned dirty: {'yes' if snapshot.phase_owned_dirty else 'no'}")
        if snapshot.phase_owned_dirty_paths:
            lines.append(f"Phase-owned paths: {', '.join(snapshot.phase_owned_dirty_paths)}")
        if snapshot.unowned_dirty_paths:
            lines.append(f"Unowned paths: {', '.join(snapshot.unowned_dirty_paths)}")
        if snapshot.pre_existing_dirty_paths:
            lines.append(f"Pre-existing paths: {', '.join(snapshot.pre_existing_dirty_paths)}")
    if snapshot.closeout_summary:
        lines.extend(_closeout_summary_lines(snapshot.closeout_summary))
    if snapshot.blocker_class and not snapshot.human_required:
        lines.append(f"Blocker: {snapshot.blocker_class}")
        if snapshot.blocker_summary:
            lines.append(f"Blocker summary: {snapshot.blocker_summary}")
        lines.append(
            "Repair guidance: start with .phase-loop/tui-handoff.md or `phase-loop handoff`, "
            "then verify `phase-loop status --json` before relaunching repair. "
            "Legacy .codex/phase-loop state remains readable during migration."
        )
    if snapshot.ledger_warnings:
        lines.append(f"Ledger warnings: {len(snapshot.ledger_warnings)}")
    return "\n".join(lines)


def render_state_inspection(summary: dict[str, object], as_json: bool = False) -> str:
    if as_json:
        return json.dumps(summary, indent=2, sort_keys=True)
    lines = [
        f"State path: {summary.get('state_path')}",
        f"Events: {summary.get('event_count', 0)}",
        f"Current phase: {summary.get('current_phase') or 'unknown'}",
        f"Runs path: {summary.get('runs_path')}",
        f"Stop file: {summary.get('stop_file')}",
        f"TUI handoff: {summary.get('tui_handoff_path')}",
        f"Stop requested: {summary.get('stop_requested', False)}",
        f"Legacy entries: {summary.get('legacy_count', 0)}",
        f"Ledger warnings: {summary.get('mismatch_count', 0)}",
    ]
    if summary.get("last_event"):
        last = summary["last_event"]
        lines.append(f"Last event: {last.get('phase')} {last.get('status')} {last.get('timestamp')}")
    if summary.get("latest_heartbeat"):
        heartbeat = summary["latest_heartbeat"]
        lines.append(
            "Heartbeat: "
            f"alive={heartbeat.get('process_alive')} "
            f"level={heartbeat.get('quiet_level')} "
            f"elapsed={heartbeat.get('elapsed_seconds', 'unknown')}s "
            f"quiet={heartbeat.get('seconds_since_log_update', 'unknown')}s"
        )
    if summary.get("latest_terminal_summary"):
        lines.extend(_terminal_summary_lines(summary["latest_terminal_summary"]))
    if summary.get("latest_metric"):
        latest = summary["latest_metric"]
        lines.append(f"Latest metric: {latest.get('metric_id')} {latest.get('terminal_status', 'unknown')}")
    if summary.get("metrics_summary"):
        lines.extend(_metrics_summary_lines(summary["metrics_summary"]))
    if summary.get("dirty_paths"):
        lines.append(f"Dirty paths: {', '.join(summary['dirty_paths'])}")
        lines.append(f"Phase-owned dirty: {summary.get('phase_owned_dirty', False)}")
        if summary.get("phase_owned_dirty_paths"):
            lines.append(f"Phase-owned paths: {', '.join(summary['phase_owned_dirty_paths'])}")
        if summary.get("unowned_dirty_paths"):
            lines.append(f"Unowned paths: {', '.join(summary['unowned_dirty_paths'])}")
        if summary.get("pre_existing_dirty_paths"):
            lines.append(f"Pre-existing paths: {', '.join(summary['pre_existing_dirty_paths'])}")
    if summary.get("closeout_summary"):
        lines.extend(_closeout_summary_lines(summary["closeout_summary"]))
    return "\n".join(lines)


def render_archive_result(summary: dict[str, object], as_json: bool = False) -> str:
    if as_json:
        return json.dumps(summary, indent=2, sort_keys=True)
    if not summary.get("archived"):
        return "No phase-loop state files to archive."
    return f"Archived {len(summary.get('moved', ())) } phase-loop state file(s) to {summary.get('archive_path')}"


def render_skill_sync_result(summary: dict[str, object], as_json: bool = False) -> str:
    if as_json:
        return json.dumps(summary, indent=2, sort_keys=True)
    lines = [
        f"Mode: {summary.get('mode', 'check')}",
        f"Repo: {summary.get('repo')}",
        f"Harnesses: {', '.join(summary.get('harnesses', []))}",
    ]
    blocker = summary.get("blocker")
    if isinstance(blocker, dict) and blocker.get("blocker_class"):
        lines.append(f"Blocked: {blocker['blocker_class']}")
        if blocker.get("blocker_summary"):
            lines.append(f"Blocker summary: {blocker['blocker_summary']}")
    for record in summary.get("bridge_skills", []):
        if not isinstance(record, dict):
            continue
        skill_name = record.get("skill_name") or "unknown"
        lines.append(f"Bridge parity: {record.get('harness_target')}: {skill_name} -> {record.get('parity_status', 'unknown')}")
        if record.get("repair_target"):
            lines.append(f"  repair target: {record['repair_target']}")
    for record in summary.get("workflow_sources", []):
        if not isinstance(record, dict):
            continue
        skill_name = record.get("skill_name") or "unknown"
        lines.append(f"Workflow source: {record.get('harness_target')}: {skill_name} -> {record.get('parity_status', 'unknown')}")
    for record in summary.get("vestigial_workflow_candidates", []):
        if not isinstance(record, dict):
            continue
        lines.append(f"Vestigial workflow: {record.get('path')} -> {record.get('status', 'unknown')}")
    for record in summary.get("skill_classifications", []):
        if not isinstance(record, dict):
            continue
        skill_name = record.get("skill_name") or "unknown"
        replacement = record.get("canonical_replacement")
        suffix = f" replacement={replacement}" if replacement else ""
        lines.append(
            f"Skill classification: {skill_name} -> {record.get('classification', 'unknown')}{suffix}"
        )
    changed = summary.get("changed", [])
    if changed:
        lines.append(f"Repaired bridge skills: {len(changed)}")
    return "\n".join(lines)


def _closeout_summary_lines(summary: dict[str, object]) -> list[str]:
    lines: list[str] = []
    mode = summary.get("closeout_mode")
    action = summary.get("closeout_action")
    if mode:
        lines.append(f"Closeout mode: {mode}")
    if action:
        lines.append(f"Closeout action: {action}")
    if summary.get("closeout_commit"):
        lines.append(f"Closeout commit: {summary['closeout_commit']}")
    if summary.get("closeout_push_ref"):
        lines.append(f"Closeout push ref: {summary['closeout_push_ref']}")
    if summary.get("closeout_refusal_reason"):
        lines.append(f"Closeout refusal: {summary['closeout_refusal_reason']}")
    if summary.get("verification_status"):
        lines.append(f"Closeout verification: {summary['verification_status']}")
    return lines


def _terminal_summary_lines(summary: dict[str, object]) -> list[str]:
    lines = [
        f"Terminal status: {summary.get('terminal_status', 'unknown')}",
        f"Terminal verification: {summary.get('verification_status', 'unknown')}",
        f"Phase-owned dirty: {'yes' if summary.get('phase_owned_dirty') else 'no'}",
    ]
    if summary.get("metric_id"):
        lines.append(f"Metric id: {summary['metric_id']}")
    closeout = summary.get("phase_loop_closeout")
    if isinstance(closeout, dict):
        lines.append(f"Pipeline closeout: {closeout.get('outcome', 'unknown')} {closeout.get('pipeline_phase_id', 'unknown')}")
    if summary.get("next_action"):
        lines.append(f"Next action: {summary['next_action']}")
    blocker = summary.get("terminal_blocker")
    if isinstance(blocker, dict) and blocker.get("blocker_class"):
        lines.append(f"Terminal blocker: {blocker['blocker_class']}")
        if blocker.get("blocker_summary"):
            lines.append(f"Terminal blocker summary: {blocker['blocker_summary']}")
    dirty_paths = summary.get("dirty_paths")
    if dirty_paths:
        lines.append(f"Terminal dirty paths: {', '.join(dirty_paths)}")
    return lines


def _metrics_summary_lines(summary: dict[str, object]) -> list[str]:
    if not summary.get("total"):
        return []
    lines = [f"Metrics: {summary.get('total')} recent work unit(s)"]
    for label, key in (
        ("Executors", "by_executor"),
        ("Models", "by_model"),
        ("Efforts", "by_effort"),
        ("Terminal statuses", "by_terminal_status"),
        ("Verification", "by_verification_status"),
        ("Blockers", "by_blocker_class"),
    ):
        bucket = summary.get(key)
        if isinstance(bucket, dict) and bucket:
            lines.append(f"{label}: {_format_counts(bucket)}")
    return lines


def _format_counts(bucket: dict[str, object]) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(bucket.items()))


def _resolve_execution_policy_block(snapshot: StateSnapshot) -> dict[str, dict[str, dict[str, object]]]:
    repo = Path(snapshot.repo)
    roadmap = Path(snapshot.roadmap) if snapshot.roadmap else None
    if roadmap is None or not roadmap.exists():
        return {}
    try:
        roadmap_doc = parse_execution_policy(roadmap, kind="roadmap")
    except Exception:
        roadmap_doc = None
    result: dict[str, dict[str, dict[str, object]]] = {}
    for phase in snapshot.phases:
        plan_path = find_plan_artifact(repo, phase, roadmap=roadmap)
        try:
            plan_doc = parse_execution_policy(plan_path, kind="plan") if plan_path is not None else None
        except Exception:
            plan_doc = None
        per_action: dict[str, dict[str, object]] = {}
        for action in EXECUTION_POLICY_ACTIONS:
            rule = None
            source = None
            if plan_doc is not None:
                rule = execution_policy_for_action(plan_doc, action)
                if rule is not None:
                    source = "phase-plan policy"
            if rule is None and roadmap_doc is not None:
                rule = execution_policy_for_action(roadmap_doc, action)
                if rule is not None:
                    source = "roadmap policy"
            if rule is None:
                continue
            per_action[action] = {
                "executor": rule.executor,
                "model": rule.model,
                "effort": rule.effort,
                "source": source,
            }
        if per_action:
            result[phase] = per_action
    return result


def _live_git_topology_lines(topology: dict[str, object] | None) -> list[str]:
    if not isinstance(topology, dict) or not topology.get("available"):
        return []
    branch = topology.get("branch")
    head = topology.get("head")
    clean = topology.get("clean")
    lines = ["Live git topology:"]
    if branch:
        lines.append(f"  Branch: {branch}")
    if isinstance(head, str) and head:
        lines.append(f"  HEAD: {head[:12]}")
    if clean is True:
        lines.append("  Working tree: clean")
    elif clean is False:
        dirty_count = _dirty_path_count(topology.get("status_short_branch"))
        if dirty_count is None:
            lines.append("  Working tree: dirty")
        else:
            suffix = "path" if dirty_count == 1 else "paths"
            lines.append(f"  Working tree: dirty ({dirty_count} {suffix})")
    return lines


def _dirty_path_count(status_short_branch: object) -> int | None:
    if not isinstance(status_short_branch, str) or not status_short_branch:
        return None
    return sum(1 for line in status_short_branch.splitlines() if line and not line.startswith("##"))


def _current_terminal_summary(snapshot: StateSnapshot) -> dict[str, object] | None:
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
