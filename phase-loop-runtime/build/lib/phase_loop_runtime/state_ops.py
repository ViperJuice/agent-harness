from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from .discovery import (
    WORKFLOW_EXECUTE_SKILLS,
    WORKFLOW_PLAN_SKILLS,
    PLAN_RE,
    latest_workflow_handoff,
    repo_identity,
)
from .events import event_path, event_read_paths, read_events
from .git_topology import collect_git_topology
from .handoff import tui_handoff_path
from .models import utc_now
from .observability import (
    phase_loop_metrics_path,
    read_launch_metadata,
    read_run_heartbeat,
    read_task_snapshot,
    read_terminal_summary,
    read_work_unit_metrics,
    summarize_work_unit_metrics,
)
from .provenance import roadmap_sha256
from .reconcile import reconcile
from .runtime_paths import (
    ensure_phase_loop_excluded,
    phase_loop_active_loop_file,
    phase_loop_active_loop_read_files,
    phase_loop_dir,
    legacy_phase_loop_dir,
    phase_loop_read_dir,
    phase_loop_runs_dir,
    phase_loop_runs_dirs,
    phase_loop_stop_file,
    phase_loop_stop_files,
    phase_loop_tui_handoff_read_file,
)
from .state import load_state, state_path, state_read_path


def active_loop_path(repo: Path) -> Path:
    return phase_loop_active_loop_file(repo)


def _active_loop_exists(repo: Path) -> bool:
    return any(_active_loop_file_is_live(path) for path in phase_loop_active_loop_read_files(repo))


def _active_loop_file_is_live(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    pid = data.get("pid") if isinstance(data, dict) else None
    if isinstance(pid, int) and not _pid_is_live(pid):
        path.unlink(missing_ok=True)
        return False
    return True


def _pid_is_live(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def archive_root(repo: Path) -> Path:
    return phase_loop_dir(repo) / "archive"


def state_files(repo: Path) -> tuple[Path, ...]:
    files: list[Path] = [
        state_path(repo),
        event_path(repo),
        active_loop_path(repo),
        phase_loop_stop_file(repo),
        tui_handoff_path(repo),
    ]
    files.extend(path for path in event_read_paths(repo) if path not in files)
    files.extend(path for path in phase_loop_active_loop_read_files(repo) if path not in files)
    files.extend(path for path in phase_loop_stop_files(repo) if path not in files)
    legacy_handoff = phase_loop_tui_handoff_read_file(repo)
    if legacy_handoff not in files:
        files.append(legacy_handoff)
    legacy_state = state_read_path(repo)
    if legacy_state not in files:
        files.append(legacy_state)
    legacy_root = legacy_phase_loop_dir(repo)
    for name in ("state.json", "events.jsonl", "active-loop.json", "stop", "tui-handoff.md"):
        legacy_file = legacy_root / name
        if legacy_file not in files:
            files.append(legacy_file)
    return tuple(files)


def inspect_state(repo: Path, roadmap: Path | None = None) -> dict[str, object]:
    state = load_state(repo)
    events = read_events(repo)
    last_event = _last_event_summary(events[-1]) if events else None
    legacy_count = sum(1 for event in events if int(event.get("schema_version", 1)) < 2)
    latest_launch = _latest_launch_metadata(repo, events)
    latest_task_ledger = _latest_task_ledger(latest_launch)
    recent_metrics = read_work_unit_metrics(repo, limit=50)
    latest_metric = recent_metrics[-1] if recent_metrics else None
    summary: dict[str, object] = {
        "repo": str(repo),
        "state_path": str(state_path(repo)),
        "state_read_path": str(state_read_path(repo)),
        "event_path": str(event_path(repo)),
        "event_read_paths": [str(path) for path in event_read_paths(repo) if path.exists()],
        "active_loop_path": str(active_loop_path(repo)),
        "runs_path": str(phase_loop_runs_dir(repo)),
        "stop_file": str(phase_loop_stop_file(repo)),
        "tui_handoff_path": str(tui_handoff_path(repo)),
        "metrics_path": str(phase_loop_metrics_path(repo)),
        "runtime_root": str(phase_loop_dir(repo)),
        "runtime_read_root": str(phase_loop_read_dir(repo)),
        "archive_root": str(archive_root(repo)),
        "state_exists": state_read_path(repo).exists(),
        "events_exists": any(path.exists() for path in event_read_paths(repo)),
        "active_loop_exists": _active_loop_exists(repo),
        "stop_requested": any(path.exists() for path in phase_loop_stop_files(repo)),
        "tui_handoff_exists": phase_loop_tui_handoff_read_file(repo).exists(),
        "event_count": len(events),
        "last_event": last_event,
        "latest_launch_metadata": latest_launch,
        "latest_heartbeat": _latest_heartbeat(repo, events),
        "latest_terminal_summary": _latest_terminal_summary(repo, events),
        "latest_task_snapshot": latest_task_ledger.get("snapshot"),
        "latest_hook_summary": latest_task_ledger.get("hook_summary"),
        "latest_metric": latest_metric,
        "metrics_summary": summarize_work_unit_metrics(recent_metrics),
        "latest_work_unit": state.latest_work_unit if state else None,
        "work_unit_status": _work_unit_status(state.latest_work_unit if state else None),
        "legacy_count": legacy_count + (1 if state and state.schema_version < 2 else 0),
        "stored_roadmap": state.roadmap if state else None,
        "stored_roadmap_sha256": state.roadmap_sha256 if state else None,
        "git_topology": collect_git_topology(repo),
    }
    if roadmap is not None and roadmap.exists():
        snapshot = reconcile(repo, roadmap)
        summary.update(
            {
                "roadmap": str(roadmap),
                "roadmap_sha256": roadmap_sha256(roadmap),
                "current_phase": snapshot.current_phase,
                "phase_status": snapshot.phases.get(snapshot.current_phase) if snapshot.current_phase else None,
                "phases": snapshot.phases,
                "human_required": snapshot.human_required,
                "blocker_class": snapshot.blocker_class,
                "blocker_summary": snapshot.blocker_summary,
                "required_human_inputs": snapshot.required_human_inputs,
                "access_attempts": snapshot.access_attempts,
                "dirty_paths": snapshot.dirty_paths,
                "phase_owned_dirty_paths": snapshot.phase_owned_dirty_paths,
                "previous_phase_owned_paths": snapshot.previous_phase_owned_paths,
                "unowned_dirty_paths": snapshot.unowned_dirty_paths,
                "pre_existing_dirty_paths": snapshot.pre_existing_dirty_paths,
                "phase_owned_dirty": snapshot.phase_owned_dirty,
                "terminal_summary": snapshot.terminal_summary,
                "closeout_terminal_status": snapshot.closeout_terminal_status,
                "closeout_summary": snapshot.closeout_summary,
                "ledger_warnings": list(snapshot.ledger_warnings),
                "mismatch_count": len(snapshot.ledger_warnings),
            }
        )
        if snapshot.terminal_summary:
            summary["latest_terminal_summary"] = snapshot.terminal_summary
        if snapshot.current_phase is None and snapshot.phases and all(status == "complete" for status in snapshot.phases.values()):
            summary["latest_terminal_summary"] = None
    else:
        summary["mismatch_count"] = 0
    summary["monitor_status"] = _monitor_status(summary)
    if isinstance(summary["monitor_status"], dict):
        summary["monitor_status"]["latest_metric"] = latest_metric
        summary["monitor_status"]["metrics_summary"] = summary["metrics_summary"]
        summary["monitor_status"]["work_unit"] = summary.get("latest_work_unit")
        summary["monitor_status"]["work_unit_status"] = summary.get("work_unit_status")
    return summary


def _work_unit_status(work_unit: dict[str, object] | None) -> str | None:
    if not isinstance(work_unit, dict):
        return None
    status = work_unit.get("status")
    return str(status) if status is not None else None


def archive_state(repo: Path, reason: str | None = None, dry_run: bool = False) -> dict[str, object]:
    # #39: --dry-run must be read-only. Compute the planned move set without
    # creating the archive dir, renaming any file, or writing the manifest.
    ensure_phase_loop_excluded(repo)
    files = [path for path in state_files(repo) if path.exists()]
    archive_dir = archive_root(repo) / utc_now().replace(":", "").replace("-", "")
    moved: list[dict[str, str]] = []
    if files:
        if not dry_run:
            archive_dir.mkdir(parents=True, exist_ok=True)
        for source in files:
            try:
                destination_name = source.resolve().relative_to(repo.resolve()).as_posix().replace("/", "__")
            except (OSError, ValueError):
                destination_name = source.name
            destination = archive_dir / destination_name
            if not dry_run:
                shutil.move(str(source), destination)
            moved.append({"source": str(source), "destination": str(destination)})
    manifest = {
        "timestamp": utc_now(),
        "repo": str(repo),
        "reason": reason or "",
        "moved": moved,
    }
    if files and not dry_run:
        (archive_dir / "archive.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return {
        "repo": str(repo),
        "archive_path": str(archive_dir) if files else None,
        "moved": moved,
        "archived": bool(files) and not dry_run,
        "dry_run": dry_run,
        "reason": reason or "",
    }


def _last_event_summary(event: dict) -> dict[str, object]:
    keys = ("timestamp", "phase", "action", "status", "source", "schema_version", "roadmap_sha256", "phase_sha256")
    return {key: event.get(key) for key in keys if key in event}


def _trusted_workflow_handoff(repo: Path, roadmap: Path) -> dict[str, object] | None:
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
            phase = match.group(2).upper()
    return {
        "workflow_skill": handoff.get("workflow_skill"),
        "originating_harness": handoff.get("originating_harness"),
        "phase": phase,
        "status": handoff.get("automation_status"),
        "artifact": artifact,
        "timestamp": handoff.get("timestamp"),
    }


def _latest_manual_import(events: list[dict], roadmap: Path) -> dict[str, object] | None:
    roadmap_value = str(roadmap.resolve())
    for event in reversed(events):
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
            "timestamp": event.get("timestamp"),
            "originating_harness": manual.get("originating_harness"),
            "workflow_skill": manual.get("workflow_skill"),
            "artifact": manual.get("artifact"),
            "installed_skill_warnings": manual.get("installed_skill_warnings", []),
            "bridge_skill_inventory": manual.get("bridge_skill_inventory", []),
        }
    return None


def _latest_heartbeat(repo: Path, events: list[dict]) -> dict[str, object] | None:
    active = _active_loop_heartbeat(repo)
    if active:
        return active
    for event in reversed(events):
        metadata = event.get("metadata") or {}
        artifacts = metadata.get("artifacts") or {}
        if isinstance(artifacts, dict):
            heartbeat = read_run_heartbeat(artifacts.get("heartbeat"))
            if heartbeat:
                return heartbeat
        launch = metadata.get("launch") or {}
        if isinstance(launch, dict):
            heartbeat = read_run_heartbeat(launch.get("heartbeat_path"))
            if heartbeat:
                return heartbeat
    return None


def _latest_launch_metadata(repo: Path, events: list[dict]) -> dict[str, object] | None:
    active = _active_loop_launch_metadata(repo)
    if active:
        return active
    for event in reversed(events):
        metadata = event.get("metadata") or {}
        artifacts = metadata.get("artifacts") or {}
        if isinstance(artifacts, dict):
            launch_path = artifacts.get("metadata")
            if launch_path:
                try:
                    data = json.loads(Path(str(launch_path)).read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    data = None
                if isinstance(data, dict):
                    return data
        launch_spec = metadata.get("launch_spec")
        if isinstance(launch_spec, dict):
            return launch_spec
    latest_run = _latest_run_dir(repo)
    if latest_run:
        data = read_launch_metadata(latest_run / "launch.json")
        if data:
            return data
    return None


def _active_loop_launch_metadata(repo: Path) -> dict[str, object] | None:
    if not _active_loop_exists(repo):
        return None
    heartbeat = _active_loop_heartbeat(repo)
    if not heartbeat:
        return None
    heartbeat_path = heartbeat.get("heartbeat_path")
    if not isinstance(heartbeat_path, str) or not heartbeat_path:
        return None
    return read_launch_metadata(Path(heartbeat_path).parent / "launch.json")


def _latest_run_dir(repo: Path) -> Path | None:
    candidates: list[Path] = []
    for runs in phase_loop_runs_dirs(repo):
        if runs.exists():
            candidates.extend(path for path in runs.iterdir() if path.is_dir())
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _latest_task_ledger(launch_metadata: dict[str, object] | None) -> dict[str, object]:
    if not isinstance(launch_metadata, dict):
        return {"snapshot": None, "hook_summary": None}
    task_ledger = launch_metadata.get("task_ledger_artifacts")
    if not isinstance(task_ledger, dict):
        return {"snapshot": None, "hook_summary": None}
    snapshot = read_task_snapshot(task_ledger.get("snapshot_path"))
    hook_summary = None
    hook_manifest = task_ledger.get("hook_manifest_path")
    if isinstance(hook_manifest, str) and hook_manifest:
        try:
            parsed = json.loads(Path(hook_manifest).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, dict):
            hook_summary = parsed
    return {"snapshot": snapshot, "hook_summary": hook_summary}


def _active_loop_heartbeat(repo: Path) -> dict[str, object] | None:
    if not _active_loop_exists(repo):
        return None
    candidates: list[Path] = []
    for runs in phase_loop_runs_dirs(repo):
        if runs.exists():
            candidates.extend(runs.glob("*/heartbeat.json"))
    candidates = sorted(candidates, key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)
    for candidate in candidates:
        heartbeat = read_run_heartbeat(candidate)
        if heartbeat:
            return heartbeat
    return None


def _latest_terminal_summary(repo: Path, events: list[dict]) -> dict[str, object] | None:
    state = load_state(repo)
    if state and state.terminal_summary:
        return state.terminal_summary
    superseded_phases: set[str] = set()
    for event in reversed(events):
        phase = str(event.get("phase", "")).upper()
        metadata = event.get("metadata") or {}
        if _terminal_summary_superseded(event):
            if phase:
                superseded_phases.add(phase)
            continue
        terminal = metadata.get("terminal_summary")
        if isinstance(terminal, dict) and phase not in superseded_phases:
            return terminal
        artifacts = metadata.get("artifacts") or {}
        if isinstance(artifacts, dict):
            summary = read_terminal_summary(artifacts.get("terminal"))
            if summary and phase not in superseded_phases:
                return summary
            if phase not in superseded_phases and _terminal_artifact_expected(event, artifacts.get("terminal")):
                return None
        launch = metadata.get("launch") or {}
        if isinstance(launch, dict):
            summary = read_terminal_summary(launch.get("terminal_path"))
            if summary and phase not in superseded_phases:
                return summary
            if phase not in superseded_phases and _terminal_artifact_expected(event, launch.get("terminal_path")):
                return None
    return None


def _terminal_summary_superseded(event: dict) -> bool:
    if event.get("status") not in {"complete", "planned"} or event.get("action") != "manual_repair":
        return False
    metadata = event.get("metadata")
    if not isinstance(metadata, dict):
        return False
    manual_repair = metadata.get("manual_repair")
    if isinstance(manual_repair, dict):
        return bool(manual_repair.get("clears_blocker"))
    return bool(metadata.get("clears_blocker"))


def _monitor_status(summary: dict[str, object]) -> dict[str, object]:
    repo_value = summary.get("repo")
    roadmap_value = summary.get("roadmap")
    repo_path = Path(str(repo_value)).expanduser().resolve() if isinstance(repo_value, str) and repo_value else None
    roadmap_path = Path(str(roadmap_value)).expanduser().resolve() if isinstance(roadmap_value, str) and roadmap_value else None
    phases = summary.get("phases") if isinstance(summary.get("phases"), dict) else {}
    current_phase = summary.get("current_phase")
    current_status = phases.get(current_phase) if current_phase else ("complete" if phases and all(status == "complete" for status in phases.values()) else "unknown")
    heartbeat = summary.get("latest_heartbeat") if isinstance(summary.get("latest_heartbeat"), dict) else {}
    terminal = summary.get("terminal_summary") if isinstance(summary.get("terminal_summary"), dict) else {}
    launch = summary.get("latest_launch_metadata") if isinstance(summary.get("latest_launch_metadata"), dict) else {}
    task_snapshot = summary.get("latest_task_snapshot") if isinstance(summary.get("latest_task_snapshot"), dict) else {}
    hook_summary = summary.get("latest_hook_summary") if isinstance(summary.get("latest_hook_summary"), dict) else {}
    heartbeat_process_alive = bool(heartbeat.get("process_alive"))
    launch_phase = str(launch.get("phase") or "").upper()
    active_heartbeat_for_current_phase = bool(
        heartbeat_process_alive
        and current_phase
        and launch_phase == str(current_phase).upper()
        and heartbeat.get("event_kind", "heartbeat") == "heartbeat"
    )
    reported_current_status = (
        "executing"
        if active_heartbeat_for_current_phase or (heartbeat_process_alive and current_status in {"planned", "executing"})
        else current_status
    )
    event_kind = "heartbeat"
    recommended_action = "Continue observing."
    terminal_status = terminal.get("terminal_status") if terminal else None
    if summary.get("stop_requested"):
        event_kind = "operator_halt"
        recommended_action = "Remove the stop file only after the operator is ready to resume."
    elif current_status == "complete" and current_phase is None:
        event_kind = "complete"
        recommended_action = "No resume command is required; this roadmap is complete."
    elif active_heartbeat_for_current_phase:
        event_kind = str(heartbeat.get("event_kind") or "heartbeat")
        recommended_action = heartbeat.get("recommended_action") or recommended_action
    elif terminal_status and not heartbeat_process_alive:
        event_kind = "terminal_exit"
        recommended_action = terminal.get("next_action") or "Inspect the terminal summary before deciding whether to resume."
    elif current_status == "blocked" or summary.get("human_required"):
        event_kind = "blocked"
        recommended_action = "Inspect the TUI handoff, state JSON, event ledger, and terminal summary before resuming."
    elif current_status == "awaiting_phase_closeout":
        event_kind = "awaiting_phase_closeout"
        recommended_action = "Preserve verified phase-owned output with an explicit closeout policy before relaunching."
    elif heartbeat.get("heartbeat_status") == "stale" or heartbeat.get("quiet_level") == "stale":
        event_kind = "stale"
        recommended_action = heartbeat.get("recommended_action") or "Inspect the latest run log and nudge the owning TUI if needed."
    elif heartbeat_process_alive and heartbeat.get("event_kind"):
        event_kind = str(heartbeat["event_kind"])
        recommended_action = heartbeat.get("recommended_action") or recommended_action
    elif terminal_status:
        event_kind = "terminal_exit"
        recommended_action = terminal.get("next_action") or "Inspect the terminal summary before deciding whether to resume."
    elif heartbeat.get("event_kind"):
        event_kind = str(heartbeat["event_kind"])
        recommended_action = heartbeat.get("recommended_action") or recommended_action
    monitor = {
        "event_kind": event_kind,
        "current_phase": current_phase,
        "current_status": reported_current_status,
        "phase_status": current_status,
        "active_loop_exists": summary.get("active_loop_exists", False),
        "stop_requested": summary.get("stop_requested", False),
        "human_required": summary.get("human_required", False),
        "blocker_class": None if active_heartbeat_for_current_phase else summary.get("blocker_class"),
        "heartbeat_status": heartbeat.get("heartbeat_status") or heartbeat.get("quiet_level"),
        "terminal_status": None if heartbeat_process_alive else terminal_status,
        "recommended_action": recommended_action,
        "state_path": summary.get("state_path"),
        "event_path": summary.get("event_path"),
        "tui_handoff_path": summary.get("tui_handoff_path"),
        "runs_path": summary.get("runs_path"),
    }
    if isinstance(summary.get("latest_work_unit"), dict):
        monitor["work_unit"] = summary["latest_work_unit"]
        monitor["work_unit_status"] = summary.get("work_unit_status")
    dispatch = launch.get("dispatch_decision")
    if isinstance(dispatch, dict):
        monitor["selected_executor"] = dispatch.get("selected_executor")
        monitor["dispatch_source"] = dispatch.get("source")
        monitor["selected_via"] = dispatch.get("selected_via")
        monitor["fallback_applied"] = dispatch.get("fallback_applied", False)
        monitor["considered_executors"] = dispatch.get("considered_executors")
        monitor["dispatch_blocked_reason"] = dispatch.get("blocked_reason")
    elif launch.get("executor"):
        monitor["selected_executor"] = launch.get("executor")
    team_policy = launch.get("claude_team_policy")
    if isinstance(team_policy, dict):
        monitor["claude_team_policy"] = {
            "execution_mode": launch.get("claude_execution_mode"),
            "maturity_label": team_policy.get("maturity_label"),
            "max_teammates": team_policy.get("max_teammates"),
            "max_native_tasks": team_policy.get("max_native_tasks"),
            "max_fanout": team_policy.get("max_fanout"),
            "worktree_posture": team_policy.get("worktree_posture"),
        }
    eligibility = launch.get("phase_team_eligibility")
    if isinstance(eligibility, dict):
        monitor["phase_team_eligibility"] = {
            "allowed_execution_modes": eligibility.get("allowed_execution_modes"),
            "eligible_for_native_team": eligibility.get("eligible_for_native_team"),
            "reason": eligibility.get("reason"),
            "invalid_reasons": eligibility.get("invalid_reasons"),
        }
    task_ledger = launch.get("task_ledger_artifacts")
    if isinstance(task_ledger, dict):
        monitor["task_snapshot_path"] = task_ledger.get("snapshot_path")
        monitor["hook_manifest_path"] = task_ledger.get("hook_manifest_path")
        if task_ledger.get("hook_policy_inventory"):
            monitor["hook_policy_inventory"] = task_ledger.get("hook_policy_inventory")
        freshness = _task_snapshot_freshness(
            task_snapshot=task_snapshot,
            heartbeat=heartbeat,
            terminal=terminal,
            current_status=str(current_status),
        )
        monitor["task_snapshot_freshness"] = freshness
        latest_activity = _task_latest_activity(task_snapshot, terminal, current_status, freshness)
        if latest_activity:
            monitor["latest_team_activity"] = latest_activity
        wait_classification = _claude_wait_classification(
            execution_mode=str(launch.get("claude_execution_mode") or "solo"),
            task_snapshot=task_snapshot,
            freshness=freshness,
        )
        monitor["wait_classification"] = wait_classification
        if hook_summary.get("activity_source"):
            monitor["hook_activity_source"] = hook_summary.get("activity_source")
    delegation = launch.get("delegation_decision")
    if isinstance(delegation, dict):
        monitor["delegation"] = {
            "request_id": delegation.get("request_id"),
            "status": delegation.get("status"),
            "reason_code": delegation.get("reason_code"),
            "summary": delegation.get("summary"),
            "selected_executor": delegation.get("selected_executor"),
        }
    parent_child = launch.get("parent_child")
    if isinstance(parent_child, dict):
        monitor["delegation_lineage"] = {
            "parent_phase": parent_child.get("parent_phase"),
            "parent_executor": parent_child.get("parent_executor"),
            "parent_run_id": parent_child.get("parent_run_id"),
            "child_action": parent_child.get("child_action"),
            "child_executor": parent_child.get("child_executor"),
            "child_artifact_root": parent_child.get("child_artifact_root"),
            "child_worktree_root": parent_child.get("child_worktree_root"),
            "child_closeout_result": parent_child.get("child_closeout_result"),
        }
    trusted_handoff = _trusted_workflow_handoff(repo_path, roadmap_path) if repo_path and roadmap_path and roadmap_path.exists() else None
    if isinstance(trusted_handoff, dict) and trusted_handoff and _trusted_workflow_handoff_matches_phases(trusted_handoff, phases):
        monitor["trusted_workflow_handoff"] = trusted_handoff
    manual_import = _latest_manual_import(read_events(repo_path), roadmap_path) if repo_path and roadmap_path and roadmap_path.exists() else None
    if isinstance(manual_import, dict) and manual_import:
        monitor["latest_manual_import"] = manual_import
    return monitor


def _task_snapshot_freshness(
    *,
    task_snapshot: dict[str, object],
    heartbeat: dict[str, object],
    terminal: dict[str, object],
    current_status: str,
) -> str:
    if not task_snapshot or not _task_snapshot_valid(task_snapshot):
        return "missing"
    if terminal.get("terminal_status") and not heartbeat.get("process_alive", False):
        return "superseded"
    if current_status in {"blocked", "complete", "awaiting_phase_closeout"} and terminal.get("terminal_status"):
        return "superseded"
    if heartbeat.get("heartbeat_status") == "stale" or heartbeat.get("quiet_level") == "stale":
        return "stale"
    if heartbeat and not heartbeat.get("process_alive", True):
        return "stale"
    return "fresh"


def _task_snapshot_valid(task_snapshot: dict[str, object]) -> bool:
    required = ("schema_version", "freshness_timestamp", "teammates", "tasks", "latest_activity")
    if not all(key in task_snapshot for key in required):
        return False
    return isinstance(task_snapshot.get("teammates"), list) and isinstance(task_snapshot.get("tasks"), list)


def _task_latest_activity(
    task_snapshot: dict[str, object],
    terminal: dict[str, object],
    current_status: object,
    freshness: str,
) -> dict[str, object] | None:
    if freshness == "superseded":
        return {
            "classification": "superseded_by_terminal",
            "summary": terminal.get("next_action") or f"Native-team evidence is superseded by newer {current_status} state.",
            "latest_update_timestamp": terminal.get("finished_at") or task_snapshot.get("freshness_timestamp"),
            "provenance_source": "terminal_summary",
        }
    latest_activity = task_snapshot.get("latest_activity")
    return latest_activity if isinstance(latest_activity, dict) else None


def _claude_wait_classification(
    *,
    execution_mode: str,
    task_snapshot: dict[str, object],
    freshness: str,
) -> str:
    if freshness == "superseded":
        return "superseded"
    if freshness == "missing" and execution_mode != "solo":
        return "team_state_unavailable"
    latest_activity = task_snapshot.get("latest_activity") if isinstance(task_snapshot, dict) else {}
    if isinstance(latest_activity, dict) and latest_activity.get("classification"):
        return str(latest_activity["classification"])
    if execution_mode == "agent_team":
        return "claude_agent_team_active"
    if execution_mode == "subagent":
        return "claude_subagent_wait"
    return "claude_solo"


def _trusted_workflow_handoff_matches_phases(handoff: dict[str, object], phases: dict[object, object]) -> bool:
    phase = str(handoff.get("phase") or "").upper()
    status = handoff.get("status")
    if not phase or status is None:
        return True
    return phases.get(phase) == status


def _terminal_artifact_expected(event: dict, terminal_path: object) -> bool:
    if not isinstance(terminal_path, str) or not terminal_path:
        return False
    return event.get("status") in {"blocked", "unknown", "executed", "awaiting_phase_closeout", "complete"}
