from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .capability_registry import describe_dispatch_decision
from .injection import claude_hook_guardrail_inventory, materialize_claude_plugin_bundle
from .models import (
    TERMINAL_SUMMARY_FIELDS,
    WORK_UNIT_METRIC_SCHEMA_VERSION,
    WorkUnitMetric,
    metadata_command,
    utc_now,
)
from .runtime_paths import (
    ensure_phase_loop_excluded,
    phase_loop_run_context_file,
    phase_loop_runs_dir,
    phase_loop_stop_file,
    phase_loop_stop_files,
)


NOTIFICATION_PAYLOAD_FIELDS = (
    "timestamp",
    "repo",
    "roadmap",
    "event_kind",
    "monitor_status",
    "current_phase",
    "current_status",
    "human_required",
    "blocker_class",
    "blocker_summary",
    "required_human_inputs",
    "latest_heartbeat",
    "terminal_summary",
    "state_path",
    "event_path",
    "tui_handoff_path",
    "run_log_path",
    "recommended_action",
    "not_run_ratio",
    "not_run_count",
    "sample_size",
    "threshold",
)

NOT_RUN_ALERT_THRESHOLD = 0.2
NOT_RUN_ALERT_SAMPLE_SIZE = 50
CPU_ACTIVE_THRESHOLD_PERCENT = 1.0


def stop_file(repo: Path) -> Path:
    return phase_loop_stop_file(repo)


def stop_requested(repo: Path) -> bool:
    return any(path.exists() for path in phase_loop_stop_files(repo))


def phase_loop_metrics_file(repo: Path) -> Path:
    return repo / ".phase-loop" / "metrics.jsonl"


def phase_loop_metrics_path(repo: Path) -> Path:
    return phase_loop_metrics_file(repo)


def run_artifacts(repo: Path, phase: str, action: str, index: int, command_or_spec) -> dict[str, Path]:
    ensure_phase_loop_excluded(repo)
    run_id = f"{utc_now().replace(':', '').replace('-', '').replace('Z', 'Z')}-{index:02d}-{_slug(phase)}-{action}"
    root = phase_loop_runs_dir(repo) / run_id
    root.mkdir(parents=True, exist_ok=True)
    context_path: Path | None = None
    if isinstance(command_or_spec, list):
        metadata = {
            "timestamp": utc_now(),
            "phase": phase,
            "action": action,
            "cwd": str(repo),
            "run_root": str(root),
            "command": command_or_spec,
            "log_path": str(root / "output.log"),
            "heartbeat_path": str(root / "heartbeat.json"),
            "terminal_path": str(root / "terminal-summary.json"),
            "stop_file": str(stop_file(repo)),
        }
    else:
        context_path, context_sha256 = _write_context_artifact(root, command_or_spec)
        injection = command_or_spec.injection_metadata.to_json()
        dispatch = command_or_spec.dispatch_decision.to_json() if command_or_spec.dispatch_decision else None
        plugin_bundle_artifacts = _materialize_launch_bundle(repo, root, command_or_spec)
        task_ledger_artifacts = _materialize_task_ledger(root, phase, command_or_spec)
        metadata = {
            "timestamp": utc_now(),
            "phase": phase,
            "action": action,
            "cwd": command_or_spec.wrapped_cwd or str(repo),
            "run_root": str(root),
            "executor": command_or_spec.executor,
            "command": metadata_command(command_or_spec.command, command_or_spec.prompt_bundle.render_prompt()),
            "available": command_or_spec.available,
            "dry_run_only": command_or_spec.dry_run_only,
            "unavailable_reason": command_or_spec.reason,
            "live_proof_gate": command_or_spec.live_proof_gate,
            "promotion_status": command_or_spec.promotion_status,
            "promotion_requirements": list(command_or_spec.promotion_requirements),
            "auth_preflight_mode": command_or_spec.auth_preflight_mode,
            "auth_preflight_probes": list(command_or_spec.auth_preflight_probes),
            "timeout_posture": command_or_spec.timeout_posture,
            "output_capture_format": command_or_spec.output_capture_format,
            "terminal_summary_artifact": command_or_spec.terminal_summary_artifact,
            "permission_posture": command_or_spec.permission_posture,
            "selected_agent": command_or_spec.selected_agent,
            "selected_model": command_or_spec.selected_model,
            "selected_effort": command_or_spec.selected_effort,
            "profile_source": command_or_spec.profile_source,
            "override_reason": command_or_spec.override_reason,
            "selected_variant": command_or_spec.selected_variant,
            "command_adapter_name": command_or_spec.command_adapter_name,
            "command_template": command_or_spec.command_template,
            "wrapped_cwd": command_or_spec.wrapped_cwd,
            "launch_timeout_seconds": command_or_spec.launch_timeout_seconds,
            "claude_execution_mode": command_or_spec.claude_execution_mode,
            "claude_team_policy": (
                command_or_spec.claude_team_policy.to_json() if command_or_spec.claude_team_policy else None
            ),
            "phase_team_eligibility": (
                command_or_spec.phase_team_eligibility.to_json() if command_or_spec.phase_team_eligibility else None
            ),
            "harness_target": injection.get("harness_target"),
            "injection_mode": injection.get("injection_mode"),
            "context_sha256": context_sha256 or injection.get("context_sha256"),
            "context_line_count": injection.get("context_line_count"),
            "context_char_count": injection.get("context_char_count"),
            "expected_skill_pack": injection.get("expected_skill_pack", []),
            "skill_bundle_id": injection.get("skill_bundle_id"),
            "skill_bundle_sha256": injection.get("skill_bundle_sha256"),
            "fallback_mode": injection.get("fallback_mode"),
            "context_path": str(context_path) if context_path is not None else injection.get("context_path"),
            "recommended_installed_roots": injection.get("recommended_installed_roots", []),
            "installed_skill_roots": injection.get("installed_skill_roots", []),
            "installed_skill_warnings": injection.get("installed_skill_warnings", []),
            "bridge_skill_inventory": injection.get("bridge_skill_inventory", []),
            "plugin_bundle_artifacts": plugin_bundle_artifacts,
            "task_ledger_artifacts": task_ledger_artifacts,
            "dispatch_decision": dispatch,
            "dispatch_summary": describe_dispatch_decision(command_or_spec.dispatch_decision) if command_or_spec.dispatch_decision else None,
            "harness_lane_assignment": (
                command_or_spec.harness_lane_assignment.to_json()
                if command_or_spec.harness_lane_assignment
                else None
            ),
            "lane_id": (
                command_or_spec.harness_lane_assignment.lane_id
                if command_or_spec.harness_lane_assignment
                else None
            ),
            "work_unit_kind": (
                command_or_spec.harness_lane_assignment.work_unit_kind
                if command_or_spec.harness_lane_assignment
                else None
            ),
            "log_path": str(root / "output.log"),
            "heartbeat_path": str(root / "heartbeat.json"),
            "terminal_path": str(root / "terminal-summary.json"),
            "stop_file": str(stop_file(repo)),
        }
    (root / "launch.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "root": root,
        "metadata": root / "launch.json",
        "log": root / "output.log",
        "heartbeat": root / "heartbeat.json",
        "terminal": root / "terminal-summary.json",
        **(
            {
                "task_snapshot": root / "task-snapshot.json",
                "hook_manifest": root / "hook-guardrails.json",
            }
            if (root / "task-snapshot.json").exists()
            else {}
        ),
        **({"context": context_path} if context_path is not None else {}),
    }


def hotfix_run_artifacts(repo: Path, reason: str, plan_stub: Path) -> dict[str, Path]:
    ensure_phase_loop_excluded(repo)
    plan_path = plan_stub if plan_stub.is_absolute() else repo / plan_stub
    run_id = f"{utc_now().replace(':', '').replace('-', '').replace('Z', 'Z')}-hotfix-{_slug(plan_path.stem)}"
    root = phase_loop_runs_dir(repo) / run_id
    root.mkdir(parents=True, exist_ok=True)
    metadata = {
        "timestamp": utc_now(),
        "phase": "HOTFIX",
        "action": "hotfix",
        "work_unit": "hotfix",
        "cwd": str(repo),
        "run_root": str(root),
        "reason": _redact_hotfix_reason(reason),
        "plan_stub": str(plan_path),
        "log_path": str(root / "output.log"),
        "heartbeat_path": str(root / "heartbeat.json"),
        "terminal_path": str(root / "terminal-summary.json"),
        "verification_artifact_path": str(root / "verification.json"),
        "verification_log_path": str(root / "verification.log"),
        "stop_file": str(stop_file(repo)),
    }
    (root / "launch.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "root": root,
        "metadata": root / "launch.json",
        "log": root / "output.log",
        "heartbeat": root / "heartbeat.json",
        "terminal": root / "terminal-summary.json",
        "verification_artifact": root / "verification.json",
        "verification_log": root / "verification.log",
    }


def build_work_unit_metric(
    *,
    repo: Path,
    phase: str,
    action: str,
    launch_metadata: dict[str, Any] | None = None,
    launch_result: Any | None = None,
    terminal_summary: dict[str, Any] | None = None,
    artifact_paths: dict[str, str] | None = None,
    lane_id: str | None = None,
) -> WorkUnitMetric:
    launch_metadata = launch_metadata or {}
    terminal_summary = terminal_summary or {}
    launch = launch_metadata.get("launch") if isinstance(launch_metadata.get("launch"), dict) else {}
    harness_lane_assignment = (
        launch_metadata.get("harness_lane_assignment")
        if isinstance(launch_metadata.get("harness_lane_assignment"), dict)
        else {}
    )
    execution_policy = (
        launch_metadata.get("execution_policy") if isinstance(launch_metadata.get("execution_policy"), dict) else {}
    )
    selected_policy = _selected_execution_policy(execution_policy, launch_metadata)
    dispatch_decision = (
        launch_metadata.get("dispatch_decision") if isinstance(launch_metadata.get("dispatch_decision"), dict) else {}
    )
    executor = str(
        launch_metadata.get("executor")
        or launch.get("executor")
        or selected_policy.get("executor")
        or dispatch_decision.get("selected_executor")
        or "manual"
    )
    model = str(
        launch_metadata.get("selected_model")
        or launch.get("selected_model")
        or selected_policy.get("model")
        or launch_metadata.get("model")
        or "unknown"
    )
    effort = launch_metadata.get("selected_variant") or selected_policy.get("effort") or launch_metadata.get("selected_effort")
    if effort is not None:
        effort = str(effort)
    started_at = getattr(launch_result, "started_at", None) or launch.get("started_at") or launch_metadata.get("started_at")
    finished_at = getattr(launch_result, "finished_at", None) or launch.get("finished_at") or launch_metadata.get("finished_at")
    returncode = getattr(launch_result, "returncode", None)
    if returncode is None:
        returncode = launch.get("returncode")
    terminal_blocker = terminal_summary.get("terminal_blocker")
    blocker_class = terminal_blocker.get("blocker_class") if isinstance(terminal_blocker, dict) else None
    if blocker_class == "blocked_by_external_setup":
        blocker_class = "admin_approval"
    if blocker_class == "blocked_by_implementation":
        blocker_class = "repeated_verification_failure"
    metric_id = str(launch_metadata.get("metric_id") or f"wum-{uuid.uuid4().hex}")
    metric = WorkUnitMetric(
        metric_id=metric_id,
        schema_version=WORK_UNIT_METRIC_SCHEMA_VERSION,
        timestamp=utc_now(),
        work_unit_id=_work_unit_id(terminal_summary, launch_metadata),
        work_unit_kind=str(selected_policy.get("work_unit_kind") or _work_unit_kind_for_action(action)),
        phase=phase,
        lane_id=lane_id or harness_lane_assignment.get("lane_id"),
        wave_id=_wave_id(terminal_summary, launch_metadata),
        action=action,
        executor=executor,
        provider=str(_provider_for_executor(executor)),
        model=model,
        effort=effort,
        thinking_level=selected_policy.get("thinking_level"),
        policy_source=selected_policy.get("execution_policy_source") or selected_policy.get("source"),
        policy_override_reason=selected_policy.get("execution_policy_override_reason")
        or selected_policy.get("override_reason"),
        profile_source=selected_policy.get("model_source") or selected_policy.get("source") or launch_metadata.get("profile_source"),
        fallback_applied=bool(
            selected_policy.get("fallback_applied")
            or launch_metadata.get("fallback_applied")
            or dispatch_decision.get("fallback_applied")
        ),
        fallback=selected_policy.get("fallback") or launch_metadata.get("fallback"),
        fallback_reason=(
            selected_policy.get("fallback_reason")
            or launch_metadata.get("fallback_reason")
            or dispatch_decision.get("fallback_reason")
            or dispatch_decision.get("blocked_reason")
        ),
        duration_seconds=_duration_seconds(started_at, finished_at),
        returncode=returncode,
        terminal_status=terminal_summary.get("terminal_status"),
        verification_status=terminal_summary.get("verification_status"),
        blocker_class=str(blocker_class) if blocker_class else None,
        artifact_paths=dict(artifact_paths or terminal_summary.get("artifact_paths") or {}),
    )
    ensure_phase_loop_excluded(repo)
    return metric


def append_work_unit_metric(repo: Path, metric: WorkUnitMetric) -> Path:
    path = phase_loop_metrics_path(repo)
    ensure_phase_loop_excluded(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(metric.to_json(), sort_keys=True) + "\n")
    return path


def read_work_unit_metrics(repo: Path, limit: int | None = None) -> list[dict[str, Any]]:
    path = phase_loop_metrics_path(repo)
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            records.append(data)
    return records[-limit:] if limit is not None else records


def summarize_work_unit_metrics(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    trailing = metrics[-NOT_RUN_ALERT_SAMPLE_SIZE:]
    not_run_count = sum(1 for metric in trailing if metric.get("verification_status") == "not_run")
    sample_size = len(trailing)
    not_run_ratio = round(not_run_count / sample_size, 3) if sample_size else 0.0
    summary: dict[str, Any] = {
        "total": len(metrics),
        "by_executor": {},
        "by_model": {},
        "by_effort": {},
        "by_terminal_status": {},
        "by_verification_status": {},
        "by_blocker_class": {},
        "not_run_ratio": not_run_ratio,
        "not_run_count": not_run_count,
        "sample_size": sample_size,
        "threshold": NOT_RUN_ALERT_THRESHOLD,
        "not_run_alert": bool(sample_size and not_run_ratio > NOT_RUN_ALERT_THRESHOLD),
    }
    for metric in metrics:
        _bump(summary["by_executor"], metric.get("executor"))
        _bump(summary["by_model"], metric.get("model"))
        _bump(summary["by_effort"], metric.get("effort"))
        _bump(summary["by_terminal_status"], metric.get("terminal_status"))
        _bump(summary["by_verification_status"], metric.get("verification_status"))
        _bump(summary["by_blocker_class"], metric.get("blocker_class"))
    return summary


def _write_context_artifact(root: Path, spec) -> tuple[Path | None, str | None]:
    context_sha256 = spec.prompt_bundle.context_sha256()
    if spec.delivery_mode != "context_file":
        return None, context_sha256
    context_path = phase_loop_run_context_file(root)
    context_path.write_text(spec.prompt_bundle.render_context().rstrip() + "\n", encoding="utf-8")
    return context_path, context_sha256


def _selected_execution_policy(execution_policy: dict[str, Any], launch_metadata: dict[str, Any]) -> dict[str, Any]:
    if isinstance(execution_policy.get("selected"), dict):
        return execution_policy["selected"]
    if isinstance(execution_policy.get("resolved"), dict):
        return execution_policy["resolved"]
    if execution_policy.get("work_unit_kind") or execution_policy.get("model") or execution_policy.get("effort"):
        return execution_policy
    if launch_metadata.get("selected_model") or launch_metadata.get("selected_effort"):
        return {
            "executor": launch_metadata.get("executor"),
            "model": launch_metadata.get("selected_model"),
            "effort": launch_metadata.get("selected_effort"),
            "source": launch_metadata.get("profile_source"),
            "override_reason": launch_metadata.get("override_reason"),
        }
    request = launch_metadata.get("launch_request")
    if isinstance(request, dict):
        model_selection = request.get("model_selection")
        if isinstance(model_selection, dict):
            return {
                "executor": request.get("executor"),
                "model": model_selection.get("model"),
                "effort": model_selection.get("effort"),
                "source": model_selection.get("source"),
                "override_reason": model_selection.get("override_reason"),
            }
    return {}


def _work_unit_kind_for_action(action: str) -> str:
    return {
        "roadmap": "roadmap_build",
        "plan": "phase_plan",
        "execute": "lane_execute",
        "repair": "repair",
        "review": "lane_review",
        "maintain-skills": "closeout",
    }.get(action, "lane_execute")


def _work_unit_id(terminal_summary: dict[str, Any], launch_metadata: dict[str, Any]) -> str | None:
    work_unit = terminal_summary.get("work_unit")
    if isinstance(work_unit, dict) and work_unit.get("work_unit_id"):
        return str(work_unit["work_unit_id"])
    work_unit = launch_metadata.get("work_unit")
    if isinstance(work_unit, dict) and work_unit.get("work_unit_id"):
        return str(work_unit["work_unit_id"])
    return None


def _wave_id(terminal_summary: dict[str, Any], launch_metadata: dict[str, Any]) -> str | None:
    if terminal_summary.get("wave_id"):
        return str(terminal_summary["wave_id"])
    if launch_metadata.get("wave_id"):
        return str(launch_metadata["wave_id"])
    work_unit = terminal_summary.get("work_unit")
    if isinstance(work_unit, dict) and work_unit.get("wave_id"):
        return str(work_unit["wave_id"])
    work_unit = launch_metadata.get("work_unit")
    if isinstance(work_unit, dict) and work_unit.get("wave_id"):
        return str(work_unit["wave_id"])
    return None


def _provider_for_executor(executor: str) -> str:
    return {
        "codex": "openai",
        "claude": "anthropic",
        "gemini": "google",
        "opencode": "opencode",
        "pi": "pi-agent",
        "command": "command",
        "manual": "manual",
    }.get(executor, executor)


def _duration_seconds(started_at: str | None, finished_at: str | None) -> float | None:
    if not started_at or not finished_at:
        return None
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        finished = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, round((finished - started).total_seconds(), 3))


def _bump(bucket: dict[str, int], raw_value: Any) -> None:
    value = str(raw_value) if raw_value not in (None, "") else "unknown"
    bucket[value] = bucket.get(value, 0) + 1


def _materialize_launch_bundle(repo: Path, run_root: Path, spec) -> dict[str, Any] | None:
    if spec.executor != "claude":
        return None
    bundle = materialize_claude_plugin_bundle(repo=repo, run_root=run_root, prompt_bundle=spec.prompt_bundle)
    return bundle or None


def _materialize_task_ledger(run_root: Path, phase: str, spec) -> dict[str, Any] | None:
    if getattr(spec, "executor", None) != "claude":
        return None
    generated_at = utc_now()
    execution_mode = getattr(spec, "claude_execution_mode", None) or "solo"
    eligibility = getattr(spec, "phase_team_eligibility", None)
    team_policy = getattr(spec, "claude_team_policy", None)
    lane_summaries = list(getattr(eligibility, "lane_summaries", ()) or ())
    snapshot_path = run_root / "task-snapshot.json"
    snapshot = _build_task_snapshot(
        phase=phase,
        generated_at=generated_at,
        execution_mode=execution_mode,
        lane_summaries=lane_summaries,
    )
    snapshot_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    hook_dir = run_root / "hook-policies"
    hook_dir.mkdir(parents=True, exist_ok=True)
    inventory = []
    for event_name in claude_hook_guardrail_inventory():
        policy = _hook_policy_record(
            event_name=event_name,
            generated_at=generated_at,
            execution_mode=execution_mode,
            allowed_worktree_root=(
                str(Path(getattr(spec, "wrapped_cwd", ""))) if getattr(spec, "wrapped_cwd", None) else None
            ),
        )
        policy_path = hook_dir / f"{event_name}.json"
        policy_path.write_text(json.dumps(policy, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        inventory.append(
            {
                "event_name": event_name,
                "path": str(policy_path),
                "blocker_posture": policy["blocker_posture"],
                "guardrail": policy["guardrail"],
            }
        )
    hook_manifest_path = run_root / "hook-guardrails.json"
    hook_manifest = {
        "schema_version": "taskledger-hook-guardrails.v1",
        "generated_at": generated_at,
        "activity_source": "documented_hook_events",
        "execution_mode": execution_mode,
        "hook_policy_inventory": inventory,
        "team_policy_inventory": (
            {
                "maturity_label": getattr(team_policy, "maturity_label", None),
                "max_teammates": getattr(team_policy, "max_teammates", None),
                "max_native_tasks": getattr(team_policy, "max_native_tasks", None),
                "max_fanout": getattr(team_policy, "max_fanout", None),
            }
            if team_policy is not None
            else {}
        ),
    }
    hook_manifest_path.write_text(json.dumps(hook_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "snapshot_path": str(snapshot_path),
        "snapshot_generated_at": generated_at,
        "snapshot_freshness_timestamp": generated_at,
        "snapshot_schema_version": snapshot["schema_version"],
        "activity_source": snapshot["activity_source"],
        "latest_activity": snapshot["latest_activity"],
        "hook_manifest_path": str(hook_manifest_path),
        "hook_policy_dir": str(hook_dir),
        "hook_policy_inventory": inventory,
    }


def _build_task_snapshot(
    *,
    phase: str,
    generated_at: str,
    execution_mode: str,
    lane_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    if execution_mode == "agent_team":
        teammates = [
            {
                "teammate_id": f"teammate-{index + 1}",
                "teammate_label": str(lane.get("heading") or f"Lane {index + 1}").lstrip("# ").strip(),
                "current_assignee": f"teammate-{index + 1}",
                "latest_update_timestamp": generated_at,
                "terminal_outcome": None,
                "provenance_source": "launch_contract",
            }
            for index, lane in enumerate(lane_summaries or [{"heading": "Lead Claude"}])
        ]
        tasks = [
            {
                "task_id": f"{phase.lower()}-lane-{index + 1}",
                "task_status": "pending",
                "dependency_ids": [],
                "ownership_claims": list(lane.get("owned_patterns") or ()),
                "current_assignee": teammates[index]["teammate_id"],
                "latest_update_timestamp": generated_at,
                "terminal_outcome": None,
                "provenance_source": "launch_contract",
            }
            for index, lane in enumerate(lane_summaries or [{"owned_patterns": []}])
        ]
        classification = "claude_agent_team_active"
        summary = "Native Claude agent-team launch initialized with lane-scoped tasks."
    elif execution_mode == "subagent":
        teammates = [
            {
                "teammate_id": "subagent-1",
                "teammate_label": "Claude subagent",
                "current_assignee": "subagent-1",
                "latest_update_timestamp": generated_at,
                "terminal_outcome": None,
                "provenance_source": "launch_contract",
            }
        ]
        tasks = [
            {
                "task_id": f"{phase.lower()}-subagent-task",
                "task_status": "waiting",
                "dependency_ids": [],
                "ownership_claims": list((lane_summaries[0].get("owned_patterns") if lane_summaries else []) or ()),
                "current_assignee": "subagent-1",
                "latest_update_timestamp": generated_at,
                "terminal_outcome": None,
                "provenance_source": "launch_contract",
            }
        ]
        classification = "claude_subagent_wait"
        summary = "Claude subagent launch initialized and waiting on delegated child progress."
    else:
        teammates = [
            {
                "teammate_id": "lead",
                "teammate_label": "Lead Claude",
                "current_assignee": "lead",
                "latest_update_timestamp": generated_at,
                "terminal_outcome": None,
                "provenance_source": "launch_contract",
            }
        ]
        tasks = [
            {
                "task_id": f"{phase.lower()}-solo-task",
                "task_status": "running",
                "dependency_ids": [],
                "ownership_claims": [],
                "current_assignee": "lead",
                "latest_update_timestamp": generated_at,
                "terminal_outcome": None,
                "provenance_source": "launch_contract",
            }
        ]
        classification = "claude_solo"
        summary = "Solo Claude launch initialized without native teammates."
    return {
        "schema_version": "taskledger.v1",
        "phase": phase,
        "execution_mode": execution_mode,
        "generated_at": generated_at,
        "freshness_timestamp": generated_at,
        "activity_source": "launch_contract",
        "teammates": teammates,
        "tasks": tasks,
        "latest_activity": {
            "classification": classification,
            "summary": summary,
            "latest_update_timestamp": generated_at,
            "provenance_source": "launch_contract",
        },
    }


def _hook_policy_record(
    *,
    event_name: str,
    generated_at: str,
    execution_mode: str,
    allowed_worktree_root: str | None,
) -> dict[str, Any]:
    default_exit_code = 1 if event_name == "WorktreeCreate" else 2
    guardrail_map = {
        "TaskCreated": "block_out_of_bounds_task_creation",
        "TaskCompleted": "block_premature_task_completion",
        "TeammateIdle": "block_unresolved_teammate_idle",
        "SubagentStop": "block_missing_terminal_outcome",
        "PostToolBatch": "block_missing_verification_evidence",
        "WorktreeCreate": "block_forbidden_worktree_location",
    }
    return {
        "schema_version": "taskledger-hook-policy.v1",
        "event_name": event_name,
        "generated_at": generated_at,
        "execution_mode": execution_mode,
        "blocker_posture": "hard_fail",
        "blocking_exit_code": default_exit_code,
        "guardrail": guardrail_map[event_name],
        "allowed_worktree_root": allowed_worktree_root,
        "provenance_source": "documented_hook_events",
    }


def read_launch_metadata(path: Path | str | None) -> dict[str, Any] | None:
    if not path:
        return None
    launch = Path(path)
    if not launch.exists():
        return None
    try:
        data = json.loads(launch.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def merge_launch_metadata(path: Path | str | None, extra: dict[str, Any]) -> dict[str, Any]:
    if not path:
        return {}
    launch = Path(path)
    data = read_launch_metadata(launch) or {}
    data.update(extra)
    launch.parent.mkdir(parents=True, exist_ok=True)
    launch.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return data


def heartbeat_path_for_log(log_path: Path) -> Path:
    return log_path.parent / "heartbeat.json"


def read_run_heartbeat(path: Path | str | None) -> dict[str, Any] | None:
    if not path:
        return None
    heartbeat = Path(path)
    if not heartbeat.exists():
        return None
    try:
        data = json.loads(heartbeat.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def read_terminal_summary(path: Path | str | None) -> dict[str, Any] | None:
    if not path:
        return None
    terminal = Path(path)
    if not terminal.exists():
        return None
    try:
        data = json.loads(terminal.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def read_task_snapshot(path: Path | str | None) -> dict[str, Any] | None:
    if not path:
        return None
    snapshot = Path(path)
    if not snapshot.exists():
        return None
    try:
        data = json.loads(snapshot.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_terminal_summary(path: Path | str | None, summary: dict[str, Any]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_terminal_summary(
    *,
    terminal_status: str,
    terminal_blocker: dict[str, Any] | None,
    verification_status: str,
    next_action: str,
    metric_id: str | None = None,
    dirty_paths: list[str] | tuple[str, ...] = (),
    phase_owned_dirty: bool = False,
    phase_owned_dirty_paths: list[str] | tuple[str, ...] = (),
    previous_phase_owned_paths: list[str] | tuple[str, ...] = (),
    unowned_dirty_paths: list[str] | tuple[str, ...] = (),
    pre_existing_dirty_paths: list[str] | tuple[str, ...] = (),
    artifact_paths: dict[str, str] | None = None,
    evidence_refs: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    work_unit: dict[str, Any] | None = None,
    verification_commands: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    latest_verification_unit: dict[str, Any] | None = None,
    phase_loop_closeout: dict[str, Any] | None = None,
    child_baml_closeout: dict[str, Any] | None = None,
    extraction_failure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = {
        "metric_id": metric_id,
        "terminal_status": terminal_status,
        "terminal_blocker": terminal_blocker,
        "verification_status": verification_status,
        "next_action": next_action,
        "dirty_paths": list(dirty_paths),
        "phase_owned_dirty": phase_owned_dirty,
        "phase_owned_dirty_paths": list(phase_owned_dirty_paths),
        "previous_phase_owned_paths": list(previous_phase_owned_paths),
        "unowned_dirty_paths": list(unowned_dirty_paths),
        "pre_existing_dirty_paths": list(pre_existing_dirty_paths),
        "artifact_paths": artifact_paths or {},
    }
    summary = apply_child_terminal_summary_overlay(
        summary,
        child_baml_closeout=child_baml_closeout,
        extraction_failure=extraction_failure,
    )
    return {
        **({"metric_id": metric_id} if metric_id else {}),
        **{field: summary[field] for field in TERMINAL_SUMMARY_FIELDS},
        **({"produced_if_gates": summary["produced_if_gates"]} if "produced_if_gates" in summary else {}),
        **({"extraction_failure": summary["extraction_failure"]} if "extraction_failure" in summary else {}),
        **({"evidence_refs": list(evidence_refs)} if evidence_refs else {}),
        **({"work_unit": work_unit} if work_unit else {}),
        **({"verification_commands": list(verification_commands)} if verification_commands else {}),
        **({"latest_verification_unit": latest_verification_unit} if latest_verification_unit else {}),
        **({"phase_loop_closeout": phase_loop_closeout} if phase_loop_closeout else {}),
    }


def apply_child_terminal_summary_overlay(
    summary: dict[str, Any],
    *,
    child_baml_closeout: dict[str, Any] | None = None,
    extraction_failure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    updated = dict(summary)
    # #38: the runner's verdict is authoritative. When the runner has already rejected
    # the child's closeout (it set a blocking terminal_status / terminal_blocker — e.g.
    # a produced_if_gates contract_bug), the child's self-reported BAML closeout must
    # NOT be overlaid back onto terminal_status / verification_status / terminal_blocker.
    # Otherwise the persisted terminal-summary.json surfaces "complete"/"passed" to the
    # next execute run and the executor reconcile-skips instead of redoing the work. The
    # child's claim remains in the event ledger (child_automation) for forensics.
    # (Narrowly scoped to runner *rejections*; planned/executed downgrade siblings are
    # not yet covered — see issue #38.)
    runner_blocker = summary.get("terminal_blocker")
    runner_blocked = summary.get("terminal_status") == "blocked" or (
        isinstance(runner_blocker, dict) and runner_blocker.get("blocker_class")
    )
    if isinstance(child_baml_closeout, dict):
        if not runner_blocked:
            terminal_status = _nonempty_text(child_baml_closeout.get("terminal_status"))
            if terminal_status is not None:
                updated["terminal_status"] = terminal_status
            verification_status = _nonempty_text(child_baml_closeout.get("verification_status"))
            if verification_status is not None:
                updated["verification_status"] = verification_status
            next_action = _nonempty_text(child_baml_closeout.get("next_action"))
            if next_action is not None:
                updated["next_action"] = next_action
        produced = _string_list(child_baml_closeout.get("produced_if_gates"))
        if produced is not None:
            updated["produced_if_gates"] = produced
        dirty_paths = _string_list(child_baml_closeout.get("dirty_paths"))
        if dirty_paths is not None and not updated.get("dirty_paths"):
            updated["dirty_paths"] = dirty_paths

        if not runner_blocked:
            blocker_class = _optional_child_literal(child_baml_closeout.get("blocker_class"))
            blocker_summary = _optional_child_literal(child_baml_closeout.get("blocker_summary"))
            human_required = bool(child_baml_closeout.get("human_required", False))
            required_inputs = _string_list(child_baml_closeout.get("required_human_inputs")) or []
            if blocker_class or blocker_summary or human_required:
                updated["terminal_blocker"] = {
                    "human_required": human_required,
                    "blocker_class": blocker_class,
                    "blocker_summary": blocker_summary,
                    "required_human_inputs": required_inputs,
                    "access_attempts": (),
                }
            elif updated.get("terminal_blocker") is None:
                updated["terminal_blocker"] = None

    sanitized_failure = _sanitize_extraction_failure(extraction_failure)
    if sanitized_failure is not None:
        updated["extraction_failure"] = sanitized_failure
    return updated


def _nonempty_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_child_literal(value: Any) -> str | None:
    text = _nonempty_text(value)
    if text is None or text.lower() in {"none", "null"}:
        return None
    return text


def _string_list(value: Any) -> list[str] | None:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return None


def _sanitize_extraction_failure(value: dict[str, Any] | None) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    allowed = ("reason", "source", "classification", "detail")
    sanitized = {
        key: str(value[key])
        for key in allowed
        if value.get(key) is not None and "\n" not in str(value[key])
    }
    return sanitized or None


def run_heartbeat_summary(
    *,
    log_path: Path | str | None = None,
    heartbeat_path: Path | str | None = None,
    pid: int | None = None,
    started_monotonic: float | None = None,
    started_at: str | None = None,
    heartbeat_interval_seconds: int = 30,
    quiet_warning_seconds: int = 600,
    quiet_blocker_seconds: int = 1800,
    command: list[str] | None = None,
    returncode: int | None = None,
    process_group_id: int | None = None,
) -> dict[str, Any]:
    log = Path(log_path) if log_path else None
    heartbeat = Path(heartbeat_path) if heartbeat_path else (heartbeat_path_for_log(log) if log else None)
    now = time.time()
    log_exists = bool(log and log.exists())
    log_size = log.stat().st_size if log_exists and log else 0
    log_mtime = log.stat().st_mtime if log_exists and log else None
    seconds_since_log_update = int(max(0, now - log_mtime)) if log_mtime else None
    quiet_level = _quiet_level(seconds_since_log_update, quiet_warning_seconds, quiet_blocker_seconds)
    process_alive = _pid_is_live(pid) if pid else False
    cpu_percent = _process_tree_cpu_percent(pid, process_group_id) if process_alive else None
    liveness_class = _liveness_class(quiet_level, process_alive, returncode, pid, cpu_percent)
    quiet_unknown_grace_seconds = _quiet_unknown_stale_grace_seconds(
        quiet_level,
        cpu_percent,
        heartbeat_interval_seconds,
        quiet_blocker_seconds,
    )
    stalled_suspect = _stalled_suspect(
        liveness_class,
        quiet_level,
        seconds_since_log_update,
        quiet_blocker_seconds,
        quiet_unknown_grace_seconds,
    )
    elapsed_seconds = int(time.monotonic() - started_monotonic) if started_monotonic is not None else None
    last_log_excerpt = _last_log_excerpt(log)
    heartbeat_status = _heartbeat_status(quiet_level, process_alive, returncode, pid)
    summary: dict[str, Any] = {
        "timestamp": utc_now(),
        "pid": pid,
        "process_group_id": process_group_id,
        "process_alive": process_alive,
        "returncode": returncode,
        "heartbeat_status": heartbeat_status,
        "event_kind": "terminal_exit" if returncode is not None else ("stale_heartbeat" if heartbeat_status == "stale" else "heartbeat"),
        "started_at": started_at,
        "elapsed_seconds": elapsed_seconds,
        "log_path": str(log) if log else None,
        "log_exists": log_exists,
        "log_size": log_size,
        "log_mtime": _format_epoch(log_mtime),
        "seconds_since_log_update": seconds_since_log_update,
        "heartbeat_interval_seconds": heartbeat_interval_seconds,
        "quiet_warning_seconds": quiet_warning_seconds,
        "quiet_blocker_seconds": quiet_blocker_seconds,
        "quiet_level": quiet_level,
        "cpu_percent": cpu_percent,
        "liveness_class": liveness_class,
        "quiet_unknown_grace_seconds": quiet_unknown_grace_seconds,
        "stalled_suspect": stalled_suspect,
        "recommended_action": _recommended_action(quiet_level, process_alive),
        "nudge_prompt": _nudge_prompt(log, seconds_since_log_update, elapsed_seconds),
        "last_log_excerpt": last_log_excerpt,
        "last_log_excerpt_hash": _stable_excerpt_hash(last_log_excerpt),
    }
    if heartbeat:
        summary["heartbeat_path"] = str(heartbeat)
    if command:
        summary["command"] = command
    return {key: value for key, value in summary.items() if value is not None}


def write_run_heartbeat(path: Path | str | None, summary: dict[str, Any]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_notification_payload(
    *,
    repo: Path,
    roadmap: Path,
    monitor_status: dict[str, Any],
    state_summary: dict[str, Any],
) -> dict[str, Any]:
    terminal = state_summary.get("terminal_summary") or state_summary.get("latest_terminal_summary") or {}
    heartbeat = state_summary.get("latest_heartbeat") or {}
    metrics_summary = monitor_status.get("metrics_summary") if isinstance(monitor_status.get("metrics_summary"), dict) else {}
    payload = {
        "timestamp": utc_now(),
        "repo": str(repo),
        "roadmap": str(roadmap),
        "event_kind": monitor_status.get("event_kind"),
        "monitor_status": monitor_status,
        "current_phase": state_summary.get("current_phase"),
        "current_status": monitor_status.get("current_status"),
        "human_required": state_summary.get("human_required", False),
        "blocker_class": state_summary.get("blocker_class"),
        "blocker_summary": state_summary.get("blocker_summary"),
        "required_human_inputs": list(state_summary.get("required_human_inputs") or ()),
        "latest_heartbeat": _compact_heartbeat(heartbeat if isinstance(heartbeat, dict) else {}),
        "terminal_summary": terminal if isinstance(terminal, dict) else {},
        "state_path": state_summary.get("state_path"),
        "event_path": state_summary.get("event_path"),
        "tui_handoff_path": state_summary.get("tui_handoff_path"),
        "run_log_path": heartbeat.get("log_path") if isinstance(heartbeat, dict) else None,
        "recommended_action": monitor_status.get("recommended_action"),
        "not_run_ratio": metrics_summary.get("not_run_ratio"),
        "not_run_count": metrics_summary.get("not_run_count"),
        "sample_size": metrics_summary.get("sample_size"),
        "threshold": metrics_summary.get("threshold"),
    }
    return {field: payload[field] for field in NOTIFICATION_PAYLOAD_FIELDS}


def run_notification_command(command: str | None, payload: dict[str, Any]) -> dict[str, Any] | None:
    if not command:
        return None
    try:
        result = subprocess.run(
            command,
            input=json.dumps(payload, indent=2, sort_keys=True) + "\n",
            text=True,
            capture_output=True,
            shell=True,
            timeout=30,
        )
    except Exception as exc:
        return {"command": command, "status": "failed", "error": str(exc)}
    return {
        "command": command,
        "status": "ok" if result.returncode == 0 else "failed",
        "returncode": result.returncode,
    }


def operator_halt_metadata(repo: Path) -> dict[str, object]:
    path = stop_file(repo)
    reason = None
    try:
        text = path.read_text(encoding="utf-8").strip()
        reason = text or None
    except OSError:
        reason = None
    return {"operator_halt": True, "stop_file": str(path), "reason": reason}


def _quiet_level(seconds_since_log_update: int | None, warning: int, blocker: int) -> str:
    if seconds_since_log_update is None:
        return "quiet"
    if seconds_since_log_update >= blocker:
        return "stale"
    if seconds_since_log_update >= warning:
        return "quiet"
    return "active"


def _recommended_action(quiet_level: str, process_alive: bool) -> str:
    if not process_alive:
        return "Inspect the final log and event ledger before resuming."
    if quiet_level == "stale":
        return "Inspect the log and paste the nudge prompt into the owning TUI or child session if available."
    if quiet_level == "quiet":
        return "Continue observing; paste the nudge prompt only if operator judgment says the child may be wedged."
    return "No action needed."


def _heartbeat_status(quiet_level: str, process_alive: bool, returncode: int | None, pid: int | None) -> str:
    if returncode is not None:
        return "exited"
    if pid and not process_alive:
        return "exited"
    return quiet_level


def _liveness_class(
    quiet_level: str,
    process_alive: bool,
    returncode: int | None,
    pid: int | None,
    cpu_percent: float | None,
) -> str:
    if returncode is not None:
        return "exited"
    if pid and not process_alive:
        return "exited"
    if quiet_level == "active":
        return "active_output"
    if cpu_percent is not None and cpu_percent > CPU_ACTIVE_THRESHOLD_PERCENT:
        return "cpu_active_quiet"
    if quiet_level == "stale" and cpu_percent is not None:
        return "suspect_stalled"
    if quiet_level == "stale":
        return "quiet_unknown"
    return "quiet_unknown"


def _quiet_unknown_stale_grace_seconds(
    quiet_level: str,
    cpu_percent: float | None,
    heartbeat_interval_seconds: int,
    quiet_blocker_seconds: int,
) -> int | None:
    if quiet_level != "stale" or cpu_percent is not None:
        return None
    return max(heartbeat_interval_seconds, min(quiet_blocker_seconds, 300))


def _stalled_suspect(
    liveness_class: str,
    quiet_level: str,
    seconds_since_log_update: int | None,
    quiet_blocker_seconds: int,
    quiet_unknown_grace_seconds: int | None,
) -> bool:
    if liveness_class == "suspect_stalled":
        return True
    if quiet_level != "stale" or quiet_unknown_grace_seconds is None:
        return False
    if seconds_since_log_update is None:
        return False
    return seconds_since_log_update >= quiet_blocker_seconds + quiet_unknown_grace_seconds


def _nudge_prompt(log: Path | None, quiet_seconds: int | None, elapsed_seconds: int | None) -> str:
    prompt = (
        "Status check: the supervisor has not observed child log output"
        f" for {quiet_seconds if quiet_seconds is not None else 'unknown'} seconds"
        f" after {elapsed_seconds if elapsed_seconds is not None else 'unknown'} seconds elapsed."
        " Please report current status, last tool/action, whether you are waiting on a subagent/tool,"
        " and whether human action is required."
    )
    if log:
        prompt += f" Latest log: {log}"
    return prompt


def _last_log_excerpt(log: Path | None, max_chars: int = 1200) -> str | None:
    if not log or not log.exists():
        return None
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return text[-max_chars:] if text else None


def _stable_excerpt_hash(text: str | None) -> str | None:
    if not text:
        return None
    import hashlib

    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _compact_heartbeat(heartbeat: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "timestamp",
        "pid",
        "process_alive",
        "returncode",
        "heartbeat_status",
        "event_kind",
        "elapsed_seconds",
        "seconds_since_log_update",
        "quiet_level",
        "cpu_percent",
        "liveness_class",
        "stalled_suspect",
        "recommended_action",
        "log_path",
        "heartbeat_path",
        "last_log_excerpt_hash",
    )
    return {key: heartbeat[key] for key in keys if key in heartbeat}


def _format_epoch(value: float | None) -> str | None:
    if value is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(value))


def _pid_is_live(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _process_tree_cpu_percent(pid: int | None, process_group_id: int | None) -> float | None:
    values = [
        value
        for value in (
            _process_cpu_percent(pid),
            _process_group_cpu_percent(process_group_id),
        )
        if value is not None
    ]
    if not values:
        return None
    return max(values)


def _process_cpu_percent(pid: int | None) -> float | None:
    if not pid or pid <= 0:
        return None
    try:
        result = subprocess.run(
            ["ps", "-o", "%cpu=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    first = (result.stdout or "").strip().splitlines()
    if not first:
        return None
    try:
        return float(first[0].strip())
    except ValueError:
        return None


def _process_group_cpu_percent(process_group_id: int | None) -> float | None:
    if not process_group_id or process_group_id <= 0:
        return None
    try:
        result = subprocess.run(
            ["ps", "-o", "%cpu=", "-g", str(process_group_id)],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    total = 0.0
    seen = False
    for line in (result.stdout or "").strip().splitlines():
        if not line.strip():
            continue
        try:
            total += float(line.strip())
        except ValueError:
            continue
        seen = True
    return total if seen else None


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-").lower()
    return slug or "unknown"


def _redact_hotfix_reason(reason: str) -> str:
    return re.sub(r"\s+", " ", reason).strip()[:200]
