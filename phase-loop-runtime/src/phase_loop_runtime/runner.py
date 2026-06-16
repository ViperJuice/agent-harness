from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import replace
from pathlib import Path, PurePosixPath

from .broker import validate_delegation_request
from .baml_modular import BamlValidationError, parse_baml_response
from .capability_registry import default_executor_for_work_unit, describe_dispatch_decision, resolve_dispatch_decision
from .classifier import classify_all
from .closeout_evidence_audit import audit_closeout_evidence
from .closeout import build_phase_loop_closeout, phase_loop_closeout_diagnostic
from .closeout_validation import validate_produced_gates
from .discovery import (
    PLAN_RE,
    dispatch_hints_for_action,
    execution_policy_dispatch_hints,
    execution_policy_for_action,
    find_plan_artifact,
    load_execution_phase_source_bundle,
    load_phase_source_bundle,
    parse_automation_status,
    parse_closeout_payload_doc,
    parse_dispatch_hints,
    parse_dispatch_hints_doc,
    parse_execution_policy,
    parse_pipeline_plan_metadata,
    plan_metadata,
    parse_plan_ownership,
    parse_roadmap_phases,
    pipeline_execution_blocker,
    pipeline_execution_plan_diagnostic,
    phase_source_bundle_diagnostic,
    plan_artifact_diagnostic,
    previous_phase_owned_dirty_paths,
    resolve_suite_command_doc,
    roadmap_closeout_evidence_audit_enabled,
    validate_plan_verification_commands_for_intake,
    verification_commands_from_plan,
)
from .dispatch_lock import DispatchLock, DispatchLockContention
from .events import append_event, event_path, read_events
from .evidence_audit import run_tier3_runner_audit
from .evidence_audit_config import EvidenceAuditConfigError, load_evidence_audit_config
from .events import append_work_unit_event
from .git_ops import pipeline_write_boundary_diagnostic
from .git_topology import collect_git_topology, resolve_closeout_push_target
from .handoff import tui_handoff_path, write_tui_handoff
from .launcher import (
    LaunchResult,
    build_launch_request,
    build_launch_spec,
    extract_executor_output_text,
    launch_with_spec,
    run_auth_preflight,
)
from .lane_scheduler import select_ready_lane_wave, worktree_assignments_for_wave
from .maintenance import MaintenanceOptions, active_loop, active_loop_blocker, run_maintenance
from .models import (
    CLOSEOUT_MODES,
    CommandAdapterConfig,
    DelegationBudget,
    DelegationDecision,
    DelegationRequest,
    DispatchDecision,
    DispatchHints,
    EXECUTORS,
    HarnessLaneAssignment,
    LoopEvent,
    PHASE_STATUSES,
    ParentChildRunMetadata,
    PRODUCT_LOOP_ACTIONS,
    StateSnapshot,
    WorkUnitCloseout,
    WorkUnitEventMetadata,
    WorkUnitIdentity,
    WorkUnitState,
    require_literal,
    metadata_command,
    utc_now,
)
from .observability import (
    apply_child_terminal_summary_overlay,
    append_work_unit_metric,
    build_terminal_summary,
    build_work_unit_metric,
    merge_launch_metadata,
    operator_halt_metadata,
    phase_loop_metrics_path,
    read_work_unit_metrics,
    run_artifacts,
    stop_requested,
    summarize_work_unit_metrics,
    write_terminal_summary,
)
from .pipeline_adapter.flag import trust_executor_evidence_enabled
from .plan_ir import iter_waves
from .pipeline_adapter.sibling_matcher import validate_phase_owned_evidence
from .profiles import resolve_execution_policy, resolve_model_selection_from_policy, resolve_profile, resolve_profile_for_executor
from .prompts import build_prompt
from .provenance import event_provenance, snapshot_provenance
from .reconcile import reconcile
from .release_guard import release_dispatch_blocker
from .state import load_work_unit_state, state_path, write_state, write_work_unit_state
from .state_degradation import record_degradation
from .verification_evidence import (
    ARTIFACT_NAME as VERIFICATION_ARTIFACT_NAME,
    LOG_NAME as VERIFICATION_LOG_NAME,
    detect_changed_dependency_manifests,
    resolve_install_command,
    run_verification,
    validate_verification_artifact,
)
from .worker_pool import read_worker_summary, worker_summary_path, write_worker_summary

try:  # Optional in the adapter runtime; tests and normal installs provide it.
    import yaml
except Exception:  # pragma: no cover - exercised only in stripped runtimes
    yaml = None


class RotationState:
    def __init__(self, executors: tuple[str, ...], *, mode: str = "phase", on_policy_pin: str = "skip") -> None:
        if mode not in {"phase", "work_unit"}:
            raise ValueError(f"invalid rotation mode: {mode}")
        if on_policy_pin not in {"skip", "fallback-next"}:
            raise ValueError(f"invalid rotation policy-pin behavior: {on_policy_pin}")
        if not executors:
            raise ValueError("rotation executor list must not be empty")
        self.executors = executors
        self.mode = mode
        self.on_policy_pin = on_policy_pin
        self.cursor = 0

    @classmethod
    def from_csv(cls, value: str | None, *, mode: str = "phase", on_policy_pin: str = "skip") -> "RotationState | None":
        if value is None:
            return None
        seen: list[str] = []
        for item in value.split(","):
            executor = item.strip()
            if not executor:
                continue
            require_literal(executor, EXECUTORS, "rotation executor")
            if executor not in seen:
                seen.append(executor)
        return cls(tuple(seen), mode=mode, on_policy_pin=on_policy_pin)

    def current(self) -> str:
        return self.executors[self.cursor % len(self.executors)]

    def consume_policy_pin(self) -> None:
        if self.on_policy_pin == "skip":
            self.advance()

    def advance(self, selected_executor: str | None = None) -> None:
        if selected_executor in self.executors:
            self.cursor = (self.executors.index(str(selected_executor)) + 1) % len(self.executors)
        else:
            self.cursor = (self.cursor + 1) % len(self.executors)


def _delegated_child_closeout_result(
    *,
    decision: DelegationDecision,
    terminal_summary: dict[str, object] | None = None,
    child_automation: dict[str, object] | None = None,
    dry_run: bool = False,
    launch_failed: bool = False,
) -> dict[str, object]:
    result: dict[str, object] = {
        "delegation_status": decision.status,
        "selected_executor": decision.selected_executor,
        "dry_run": dry_run,
    }
    if not decision.approved:
        result["status"] = "denied"
        result["reason_code"] = decision.reason_code
        result["summary"] = decision.summary
        return result
    if isinstance(child_automation, dict) and child_automation:
        result["status"] = child_automation.get("automation_status") or ("planned" if dry_run else "unknown")
        result["verification_status"] = child_automation.get("automation_verification_status")
        result["human_required"] = child_automation.get("automation_human_required")
        result["blocker_class"] = child_automation.get("automation_blocker_class")
        result["blocker_summary"] = child_automation.get("automation_blocker_summary")
        result["next_command"] = child_automation.get("automation_next_command")
        return {key: value for key, value in result.items() if value is not None}
    if terminal_summary:
        result["status"] = terminal_summary.get("terminal_status")
        result["verification_status"] = terminal_summary.get("verification_status")
    if launch_failed and not result.get("status"):
        result["status"] = "failed"
    return {key: value for key, value in result.items() if value is not None}


def _delegated_child_status_and_blocker(closeout: dict[str, object]) -> tuple[str, dict[str, object] | None]:
    raw_status = closeout.get("status")
    status = _phase_status_literal(raw_status)
    if status in {"planned", "executed", "complete", "awaiting_phase_closeout"}:
        return status, None
    blocker = {
        "human_required": str(closeout.get("human_required", "")).lower() == "true",
        "blocker_class": _optional_automation_literal(closeout.get("blocker_class")) or "repeated_verification_failure",
        "blocker_summary": _optional_automation_literal(closeout.get("blocker_summary"))
        or f"Delegated child returned non-terminal status: {raw_status!r}.",
        "required_human_inputs": (),
        "access_attempts": (),
    }
    return "blocked", blocker


def _current_branch(repo: Path) -> str:
    try:
        branch = subprocess.check_output(
            ["git", "-C", str(repo), "branch", "--show-current"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        branch = ""
    return branch or "detached"


def _current_head(repo: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def _repo_relative(repo: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo.resolve()))
    except ValueError:
        return str(path)


def _pipeline_branchgov_active(repo: Path) -> bool:
    from .pipeline_adapter.flag import branchgov_enabled
    from .pipeline_adapter.markers import detect_pipeline_mode

    return detect_pipeline_mode(repo) and branchgov_enabled()


def _roadmap_version(roadmap: Path) -> str:
    match = re.fullmatch(r"phase-plans-(v[\w.-]+)\.md", roadmap.name)
    return match.group(1) if match else roadmap.stem


def _default_branch(repo: Path) -> str:
    remote_head = _git_output_or_empty(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if remote_head.startswith("origin/"):
        return remote_head.removeprefix("origin/")
    # Authoritative fallback: ask the remote for its HEAD when origin/HEAD is unset.
    # The previous fallback used @{upstream}, which returns the CURRENT branch's
    # tracking ref — wrong (e.g. on a pipeline branch, returned that branch as
    # "default") and would cause BranchGov to refuse commits there.
    ls_remote = _git_output_or_empty(repo, "ls-remote", "--symref", "origin", "HEAD")
    for line in ls_remote.splitlines():
        if line.startswith("ref: refs/heads/"):
            ref = line.split("\t", 1)[0].removeprefix("ref: refs/heads/").strip()
            if ref:
                return ref
    return "main"


def _git_output_or_empty(repo: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), *args],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return ""


def _pipeline_branch_blocker_from_error(exc: Exception) -> dict[str, object]:
    blocker_class = getattr(exc, "blocker_class", None) or "contract_bug"
    blocker_summary = getattr(exc, "blocker_summary", None) or str(exc)
    return {
        "human_required": False,
        "blocker_class": blocker_class,
        "blocker_summary": str(blocker_summary),
        "required_human_inputs": (),
        "access_attempts": (),
    }


def _ensure_pipeline_branch_before_dispatch(repo: Path, roadmap: Path) -> dict[str, object] | None:
    if not _pipeline_branchgov_active(repo):
        return None
    from .pipeline_adapter.branch_ops import ensure_pipeline_branch

    try:
        ensure_pipeline_branch(repo, _roadmap_version(roadmap), _default_branch(repo))
    except Exception as exc:
        return _pipeline_branch_blocker_from_error(exc)
    return None


def _refuse_pipeline_default_branch_commit(repo: Path) -> dict[str, object] | None:
    if not _pipeline_branchgov_active(repo):
        return None
    from .pipeline_adapter.branch_ops import refuse_default_branch_commit

    try:
        refuse_default_branch_commit(repo, _default_branch(repo))
    except Exception as exc:
        return _pipeline_branch_blocker_from_error(exc)
    return None


def _emit_ratification_if_reached(
    *,
    repo: Path,
    roadmap: Path,
    phase: str,
    plan: Path | None,
    child_automation: dict[str, object],
) -> dict[str, object] | None:
    if plan is None or not _pipeline_branchgov_active(repo):
        return None
    terminal_status = _child_terminal_status(child_automation)
    if terminal_status is None:
        return None
    metadata = plan_metadata(plan)
    ratification_gate = metadata.get("ratification_gate") or "complete"
    if terminal_status != ratification_gate:
        return None
    try:
        from .pipeline_adapter.merge_policy import parse as parse_merge_policy
        from .pipeline_adapter.ratification import emit_ratification_passed

        merge_policy = parse_merge_policy(metadata)
        emit_ratification_passed(
            repo,
            _roadmap_version(roadmap),
            phase,
            ratification_gate,
            merge_policy,
            _ratification_audit_payload(child_automation),
        )
    except Exception as exc:
        return {
            "human_required": False,
            "blocker_class": getattr(exc, "blocker_class", None) or "contract_bug",
            "blocker_summary": f"Ratification event emission failed: {exc}",
            "required_human_inputs": (),
            "access_attempts": (),
        }
    return None


def _child_terminal_status(child_automation: dict[str, object]) -> str | None:
    payload = child_automation.get("native_closeout_payload")
    if isinstance(payload, dict):
        terminal_status = payload.get("terminal_status")
        if isinstance(terminal_status, str):
            return terminal_status
    status = child_automation.get("automation_status")
    return status if isinstance(status, str) else None


def _ratification_audit_payload(child_automation: dict[str, object]) -> dict[str, object]:
    payload = child_automation.get("native_closeout_payload")
    if isinstance(payload, dict):
        return {
            "terminal_status": payload.get("terminal_status"),
            "verification_status": payload.get("verification_status"),
            "dirty_paths": payload.get("dirty_paths") if isinstance(payload.get("dirty_paths"), list) else [],
            "produced_if_gates": (
                payload.get("produced_if_gates") if isinstance(payload.get("produced_if_gates"), list) else []
            ),
            "source": child_automation.get("native_closeout_source"),
        }
    return {
        "terminal_status": child_automation.get("automation_status"),
        "verification_status": child_automation.get("automation_verification_status"),
        "dirty_paths": child_automation.get("dirty_paths") if isinstance(child_automation.get("dirty_paths"), list) else [],
        "produced_if_gates": (
            child_automation.get("produced_if_gates")
            if isinstance(child_automation.get("produced_if_gates"), list)
            else []
        ),
        "source": "automation",
    }


def is_plan_doc_current(repo: Path, phase: str, plan: Path, roadmap: Path, *, recent_commit_window: int = 50) -> bool:
    current_plan = find_plan_artifact(repo, phase, roadmap=roadmap)
    if current_plan is None or current_plan.resolve() != plan.resolve():
        return False
    metadata = plan_metadata(plan)
    if metadata.get("last_generated", "").strip():
        return True
    # When the plan's frontmatter `phase:` matches the queried phase,
    # the plan IS the active artifact even without last_generated metadata
    # or recent git activity. Fixes regenesis DEF-2: the planner used to
    # re-dispatch when both heuristics failed even though the plan was
    # demonstrably the right one for this phase.
    if str(metadata.get("phase", "")).strip().upper() == phase.upper():
        return True
    rel_plan = _repo_relative(repo, plan)
    try:
        output = subprocess.check_output(
            ["git", "-C", str(repo), "log", "--name-only", "-n", str(recent_commit_window), "--", rel_plan],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return False
    return rel_plan in {line.strip() for line in output.splitlines()}


def is_sibling_phase_plan_doc(path: str, roadmap: Path, current_phase: str) -> bool:
    rel = PurePosixPath(path)
    if rel.is_absolute() or ".." in rel.parts or len(rel.parts) != 2 or rel.parts[0] != "plans":
        return False

    # Use the roadmap's known version-segment as ground truth, then derive
    # the alias as whatever follows. Avoids the regex-greedy ambiguity
    # (e.g. phase-plan-v32-VISUALPARITY-SL-1.md naively parses as
    # v32 + VISUALPARITY-SL-1 vs v32-VISUALPARITY + SL-1; only the
    # roadmap's known version disambiguates).
    roadmap_match = re.fullmatch(r"phase-plans-(v[\w.-]+)\.md", roadmap.name)
    if not roadmap_match:
        return False
    expected_prefix = f"phase-plan-{roadmap_match.group(1)}-"
    if not rel.name.startswith(expected_prefix) or not rel.name.endswith(".md"):
        return False
    alias = rel.name[len(expected_prefix):-len(".md")].upper()
    if not alias or alias == current_phase.upper():
        return False
    return alias in {phase.upper() for phase in parse_roadmap_phases(roadmap)}


def _latest_phase_event_status(repo: Path, phase: str) -> str | None:
    for event in reversed(read_events(repo)):
        if str(event.get("phase", "")).upper() == phase.upper():
            status = event.get("status")
            return str(status) if status is not None else None
    return None


def set_phase_status(
    repo: Path,
    roadmap: Path,
    phase: str,
    classifications: dict[str, str],
    next_status: str,
    *,
    reason: str,
    trigger: str,
    selection,
    action: str,
    metadata: dict[str, object] | None = None,
) -> str:
    if next_status not in PHASE_STATUSES:
        raise ValueError(f"invalid phase status: {next_status}")
    reason = reason.strip()
    trigger = trigger.strip()
    if not reason:
        raise ValueError("phase status transition reason is required")
    if not trigger:
        raise ValueError("phase status transition trigger is required")
    previous = classifications.get(phase, "unplanned")
    classifications[phase] = next_status
    if previous == next_status:
        return next_status
    transition = {
        "from": previous,
        "to": next_status,
        "reason": reason,
        "trigger": trigger,
    }
    event_metadata = {"state_transition": transition}
    if metadata:
        event_metadata.update(metadata)
    append_event(
        repo,
        LoopEvent(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phase=phase,
            action="state_transition",
            status=next_status,
            model=selection.model,
            reasoning_effort=selection.effort,
            source=selection.source,
            override_reason=selection.override_reason,
            metadata={**event_metadata, "trigger_action": action},
            **event_provenance(roadmap, phase),
        ),
    )
    return next_status


def _pipeline_boundary_blocker(
    repo: Path,
    roadmap: Path,
    plan: Path | None,
    bundle,
    dirty_paths: list[str] | tuple[str, ...],
) -> dict[str, object] | None:
    if plan is None or bundle is None:
        return None
    diagnostic = pipeline_write_boundary_diagnostic(
        repo,
        tuple(dirty_paths),
        plan_ownership=parse_plan_ownership(repo, roadmap, plan),
        bundle=bundle,
    )
    if diagnostic is None:
        return None
    return {
        "human_required": diagnostic.human_required,
        "blocker_class": diagnostic.blocker_class,
        "blocker_summary": f"Pipeline write-boundary validation failed: {diagnostic.kind} ({diagnostic.path})",
        "required_human_inputs": (),
        "access_attempts": (),
        "diagnostic": diagnostic.to_json(),
    }


def _stale_pipeline_plan_candidate(repo: Path, roadmap: Path, phase: str):
    for candidate in sorted((repo / "plans").glob("phase-plan-v*-*.md")):
        match = PLAN_RE.search(candidate.name)
        if not match or match.group(2).lower() != phase.lower():
            continue
        diagnostic = pipeline_execution_plan_diagnostic(repo, candidate, phase=phase, roadmap=roadmap)
        if diagnostic is not None:
            return candidate, diagnostic
    return None


def status_snapshot(repo: Path, roadmap: Path, pipeline_mode: str = "standalone") -> StateSnapshot:
    require_literal(pipeline_mode, ("standalone", "pipeline_optional", "pipeline_required"), "pipeline mode")
    snapshot = reconcile(repo, roadmap)
    recent_metrics = read_work_unit_metrics(repo, limit=50)
    return StateSnapshot(
        timestamp=utc_now(),
        repo=str(repo),
        roadmap=str(roadmap),
        phases=snapshot.phases,
        current_phase=snapshot.current_phase,
        last_action="status",
        human_required=snapshot.human_required,
        blocker_class=snapshot.blocker_class,
        blocker_summary=snapshot.blocker_summary,
        required_human_inputs=snapshot.required_human_inputs,
        access_attempts=snapshot.access_attempts,
        dirty_paths=snapshot.dirty_paths,
        phase_owned_dirty_paths=snapshot.phase_owned_dirty_paths,
        previous_phase_owned_paths=snapshot.previous_phase_owned_paths,
        unowned_dirty_paths=snapshot.unowned_dirty_paths,
        pre_existing_dirty_paths=snapshot.pre_existing_dirty_paths,
        phase_owned_dirty=snapshot.phase_owned_dirty,
        terminal_summary=snapshot.terminal_summary,
        latest_metric=recent_metrics[-1] if recent_metrics else None,
        metrics_summary=summarize_work_unit_metrics(recent_metrics),
        closeout_terminal_status=snapshot.closeout_terminal_status,
        closeout_summary=snapshot.closeout_summary,
        pipeline_mode=pipeline_mode,
        ledger_warnings=snapshot.ledger_warnings,
        **snapshot_provenance(roadmap),
    )


def _dirty_evidence_from_metadata(metadata: object) -> dict[str, object] | None:
    if not isinstance(metadata, dict):
        return None
    for key in ("terminal_summary", "completion_dirty_worktree", "plan_dirty_worktree", "incomplete_execute_dirty_worktree"):
        value = metadata.get(key)
        if not isinstance(value, dict):
            continue
        if not any(field in value for field in ("phase_owned_dirty_paths", "previous_phase_owned_paths")):
            continue
        return {
            "source": key,
            "dirty_paths": list(value.get("dirty_paths", ())),
            "phase_owned_dirty_paths": list(value.get("phase_owned_dirty_paths", ())),
            "previous_phase_owned_paths": list(value.get("previous_phase_owned_paths", ())),
            "phase_owned_dirty": bool(value.get("phase_owned_dirty", False)),
        }
    return None


def _cross_phase_dirty_start_gate(repo: Path, current_phase: str) -> dict[str, object] | None:
    current_phase = current_phase.upper()
    current_dirty = set(_dirty_paths(repo))
    if not current_dirty:
        return None
    scanned_events = 0
    phases_seen: set[str] = set()
    for event in list(reversed(read_events(repo)))[:50]:
        scanned_events += 1
        if not isinstance(event, dict):
            continue
        prior_phase = str(event.get("phase", "")).upper()
        if not prior_phase or prior_phase == current_phase or prior_phase in phases_seen:
            continue
        evidence = _dirty_evidence_from_metadata(event.get("metadata"))
        if evidence is None:
            continue
        phases_seen.add(prior_phase)
        candidate_paths = [
            str(path)
            for path in (
                list(evidence.get("phase_owned_dirty_paths", ()))
                + list(evidence.get("previous_phase_owned_paths", ()))
            )
            if str(path)
        ]
        overlapping = sorted(path for path in dict.fromkeys(candidate_paths) if path in current_dirty)
        if not overlapping:
            continue
        return {
            "status": "refused",
            "current_phase": current_phase,
            "offending_phase": prior_phase,
            "last_event_timestamp": event.get("timestamp"),
            "overlapping_dirty_paths": overlapping,
            "scanned_events": scanned_events,
            "dirty_evidence_source": evidence.get("source"),
            "current_dirty_paths": sorted(current_dirty),
            "next_actions": [
                "Commit or stash the overlapping dirty paths before rerunning phase-loop.",
                f"phase-loop reconcile --phase {prior_phase} --to-status planned --reason <text>",
            ],
        }
    return None


def _start_gate_refused_event(
    *,
    repo: Path,
    roadmap: Path,
    phase: str,
    action: str,
    selection,
    blocker: dict[str, object],
    start_gate: dict[str, object],
) -> LoopEvent:
    terminal_summary = build_terminal_summary(
        terminal_status="blocked",
        terminal_blocker=blocker,
        verification_status="blocked",
        next_action=(
            f"Resolve dirty output from {start_gate['offending_phase']} before dispatching {phase}: "
            + " or ".join(str(item) for item in start_gate["next_actions"])
        ),
        dirty_paths=tuple(start_gate["overlapping_dirty_paths"]),
        previous_phase_owned_paths=tuple(start_gate["overlapping_dirty_paths"]),
    )
    return LoopEvent(
        timestamp=utc_now(),
        repo=str(repo),
        roadmap=str(roadmap),
        phase=phase,
        action="start_gate_refused",
        status="blocked",
        model=selection.model,
        reasoning_effort=selection.effort,
        source=selection.source,
        override_reason=selection.override_reason,
        blocker=blocker,
        metadata={
            "trigger_action": action,
            "start_gate": start_gate,
            "terminal_summary": terminal_summary,
        },
        **event_provenance(roadmap, phase),
    )


def _start_gate_bypassed_event(
    *,
    repo: Path,
    roadmap: Path,
    phase: str,
    action: str,
    selection,
    reason: str | None,
    start_gate: dict[str, object],
) -> LoopEvent:
    bypassed = dict(start_gate)
    bypassed["status"] = "bypassed"
    bypassed["reason"] = str(reason or "").strip()
    return LoopEvent(
        timestamp=utc_now(),
        repo=str(repo),
        roadmap=str(roadmap),
        phase=phase,
        action="start_gate_bypassed",
        status="planned",
        model=selection.model,
        reasoning_effort=selection.effort,
        source=selection.source,
        override_reason=selection.override_reason,
        metadata={
            "trigger_action": action,
            "start_gate": bypassed,
        },
        **event_provenance(roadmap, phase),
    )


def run_loop(
    repo: Path,
    roadmap: Path,
    phase: str | None = None,
    max_phases: int = 1,
    max_phases_explicit: bool = False,
    full_phase: bool = False,
    no_deprecation_hints: bool = False,
    model_profile: str | None = None,
    executor: str | None = None,
    allowed_executors: tuple[str, ...] = (),
    fallback_executors: tuple[str, ...] = (),
    disabled_executors: tuple[str, ...] = (),
    required_capabilities: tuple[str, ...] = (),
    model: str | None = None,
    effort: str | None = None,
    dry_run: bool = False,
    json_output: bool = False,
    action: str = "run",
    maintenance_options: MaintenanceOptions | None = None,
    observe: bool = True,
    stream_output: bool = False,
    bypass_approvals: bool = False,
    heartbeat_interval_seconds: int = 30,
    quiet_warning_seconds: int = 600,
    quiet_blocker_seconds: int = 1800,
    heartbeat_enabled: bool = True,
    closeout_mode: str = "manual",
    enable_tier_3: bool = False,
    tier_3_budget: int = 3,
    command_adapter_name: str | None = None,
    command_template: str | None = None,
    product_action_override: str | None = None,
    claude_execution_mode: str | None = None,
    work_unit_mode: bool = False,
    rotate_executors: str | None = None,
    rotation_mode: str = "phase",
    rotation_on_policy_pin: str = "skip",
    lane_scheduler_mode: str = "off",
    source_bundle_path: str | Path | None = None,
    pipeline_mode: str | None = None,
    output_path: str | Path | None = None,
    stuck_loop_iterations: int = 5,
    stuck_loop_minutes: int = 30,
    force_replan: bool = False,
    dispatch_lock_enabled: bool = True,
    parallel_dispatch: bool = False,
    allow_cross_phase_dirty_reason: str | None = None,
) -> tuple[StateSnapshot, list[LaunchResult]]:
    if closeout_mode not in CLOSEOUT_MODES:
        raise ValueError(f"invalid closeout mode: {closeout_mode}")
    if allow_cross_phase_dirty_reason is not None and not allow_cross_phase_dirty_reason.strip():
        raise ValueError("allow_cross_phase_dirty_reason must not be empty")
    allow_cross_phase_dirty_reason = allow_cross_phase_dirty_reason.strip() if allow_cross_phase_dirty_reason else None
    require_literal(lane_scheduler_mode, ("off", "serialized", "concurrent"), "lane scheduler mode")
    try:
        rotation_state = RotationState.from_csv(
            rotate_executors,
            mode=rotation_mode,
            on_policy_pin=rotation_on_policy_pin,
        )
    except ValueError as exc:
        selection = resolve_profile(model_profile or product_action_override or "execute", model=model, effort=effort)
        blocker = {
            "human_required": False,
            "blocker_class": "contract_bug",
            "blocker_summary": f"Invalid executor rotation options: {exc}",
            "required_human_inputs": (),
        }
        snapshot = reconcile(repo, roadmap)
        selected = _select_ready_phase(repo, roadmap, snapshot.phases, phase)
        snapshot = StateSnapshot(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phases=snapshot.phases,
            current_phase=selected,
            last_action=action,
            model=selection.model,
            reasoning_effort=selection.effort,
            source=selection.source,
            override_reason=selection.override_reason,
            human_required=False,
            blocker_class="contract_bug",
            blocker_summary=str(blocker["blocker_summary"]),
            required_human_inputs=(),
            **snapshot_provenance(roadmap),
        )
        append_event(
            repo,
            LoopEvent(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phase=selected or "UNKNOWN",
                action=action,
                status="blocked",
                model=selection.model,
                reasoning_effort=selection.effort,
                source=selection.source,
                override_reason=selection.override_reason,
                blocker=blocker,
                metadata={
                    "rotation": {
                        "status": "blocked",
                        "reason": "invalid_rotation_options",
                        "rotate_executors": rotate_executors,
                        "rotation_mode": rotation_mode,
                        "rotation_on_policy_pin": rotation_on_policy_pin,
                    },
                    "terminal_summary": build_terminal_summary(
                        terminal_status="blocked",
                        terminal_blocker=blocker,
                        verification_status="blocked",
                        next_action="Fix --rotate-executors, --rotation-mode, or --rotation-on-policy-pin before relaunching.",
                    ),
                },
                **event_provenance(roadmap, selected or "UNKNOWN"),
            ),
        )
        _write_state_and_handoff(
            repo,
            roadmap,
            snapshot,
            action=action,
            results=[],
            output_path=output_path,
            override_phase=selected,
            source_bundle_path=source_bundle_path or os.environ.get("PHASE_LOOP_SOURCE_BUNDLE"),
            pipeline_mode=pipeline_mode or os.environ.get("PHASE_LOOP_PIPELINE_MODE"),
        )
        return snapshot, []
    explicit_product_action = (
        product_action_override.lower()
        if isinstance(product_action_override, str) and product_action_override.lower() in PRODUCT_LOOP_ACTIONS
        else None
    )
    effective_source_bundle_path = source_bundle_path or os.environ.get("PHASE_LOOP_SOURCE_BUNDLE")
    effective_pipeline_mode = pipeline_mode or os.environ.get("PHASE_LOOP_PIPELINE_MODE") or "standalone"
    if action == "maintain-skills":
        return run_maintenance(
            repo=repo,
            roadmap=roadmap,
            options=maintenance_options or MaintenanceOptions(),
            model_profile=model_profile,
            model=model,
            effort=effort,
            dry_run=dry_run,
            json_output=json_output,
            observe=observe,
            stream_output=stream_output,
            bypass_approvals=bypass_approvals,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            quiet_warning_seconds=quiet_warning_seconds,
            quiet_blocker_seconds=quiet_blocker_seconds,
            heartbeat_enabled=heartbeat_enabled,
        )

    snapshot = reconcile(repo, roadmap)
    classifications = snapshot.phases
    selected = _select_ready_phase(repo, roadmap, classifications, phase)
    results: list[LaunchResult] = []
    selection = resolve_profile(model_profile or explicit_product_action or "execute", model=model, effort=effort)
    dispatch_lock_context = _null_context()
    if dispatch_lock_enabled and not dry_run:
        try:
            dispatch_lock_context = DispatchLock(repo, roadmap).acquire()
        except DispatchLockContention as exc:
            blocker = {
                "human_required": False,
                "blocker_class": "concurrent_dispatch",
                "blocker_summary": exc.blocker_summary(roadmap),
                "required_human_inputs": (),
            }
            snapshot = StateSnapshot(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phases=classifications,
                current_phase=selected,
                last_action=action,
                model=selection.model,
                reasoning_effort=selection.effort,
                source=selection.source,
                override_reason=selection.override_reason,
                human_required=False,
                blocker_class="concurrent_dispatch",
                blocker_summary=str(blocker["blocker_summary"]),
                required_human_inputs=(),
                terminal_summary=build_terminal_summary(
                    terminal_status="blocked",
                    terminal_blocker=blocker,
                    verification_status="blocked",
                    next_action=str(blocker["blocker_summary"]),
                ),
                **snapshot_provenance(roadmap),
            )
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase=selected or "UNKNOWN",
                    action=action,
                    status="blocked",
                    model=selection.model,
                    reasoning_effort=selection.effort,
                    source=selection.source,
                    override_reason=selection.override_reason,
                    blocker=blocker,
                    metadata={
                        "dispatch_lock": {
                            "status": "blocked",
                            "lock_path": str(exc.lock_path),
                            "holder_pid": exc.holder_pid,
                            "elapsed_seconds": exc.elapsed_seconds,
                            "roadmap": exc.roadmap or str(roadmap),
                        },
                        "terminal_summary": snapshot.terminal_summary,
                    },
                    **event_provenance(roadmap, selected or "UNKNOWN"),
                ),
            )
            _write_state_and_handoff(
                repo,
                roadmap,
                snapshot,
                action=action,
                results=[],
                output_path=output_path,
                override_phase=selected,
                source_bundle_path=effective_source_bundle_path,
                pipeline_mode=effective_pipeline_mode,
            )
            return snapshot, []
    blocker = active_loop_blocker(repo, desired_mode="product")
    if blocker:
        snapshot = StateSnapshot(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phases=classifications,
            current_phase=selected,
            last_action=action,
            model=selection.model,
            reasoning_effort=selection.effort,
            source=selection.source,
            override_reason=selection.override_reason,
            human_required=blocker.human_required,
            blocker_class=blocker.blocker_class,
            blocker_summary=blocker.blocker_summary,
            required_human_inputs=blocker.required_human_inputs,
            **snapshot_provenance(roadmap),
        )
        _write_state_and_handoff(
            repo,
            roadmap,
            snapshot,
            action=action,
            results=[],
            output_path=output_path,
            override_phase=selected,
            source_bundle_path=effective_source_bundle_path,
            pipeline_mode=effective_pipeline_mode,
        )
        append_event(
            repo,
            LoopEvent(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phase=selected or "UNKNOWN",
                action=action,
                status="blocked",
                model=selection.model,
                reasoning_effort=selection.effort,
                source=selection.source,
                override_reason=selection.override_reason,
                blocker=blocker.to_json(),
                **event_provenance(roadmap, selected or "UNKNOWN"),
            ),
        )
        return snapshot, []

    loop_context = active_loop(repo, "product") if not dry_run else _null_context()
    current = selected
    phase_cycles_completed = 0
    coordinator_waves = tuple(iter_waves(roadmap)) if parallel_dispatch and phase is None else ()
    coordinator_started_waves: set[int] = set()
    if coordinator_waves and not max_phases_explicit:
        max_phases = sum(len(wave) for wave in coordinator_waves)
    iterations_remaining = max_phases if not full_phase else max(max_phases * 4, max_phases)
    if max_phases_explicit and not full_phase and not no_deprecation_hints and selected is not None:
        append_event(
            repo,
            LoopEvent(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phase=selected,
                action=action,
                status=classifications.get(selected, "unknown"),
                model=selection.model,
                reasoning_effort=selection.effort,
                source=selection.source,
                override_reason=selection.override_reason,
                metadata={
                    "max_phases_hint": {
                        "status": "emitted",
                        "legacy_unit": "dispatched_actions",
                        "full_phase_unit": "complete_phase_cycles",
                        "message": "--max-phases counts dispatched actions unless --full-phase is set.",
                    }
                },
                **event_provenance(roadmap, selected),
            ),
        )
    with dispatch_lock_context, loop_context:
        while iterations_remaining > 0 and (not full_phase or phase_cycles_completed < max_phases):
            iterations_remaining -= 1
            snapshot = reconcile(repo, roadmap)
            classifications = snapshot.phases
            alias = (
                _select_parallel_dispatch_phase(coordinator_waves, classifications)
                if coordinator_waves
                else _select_ready_phase(repo, roadmap, classifications, phase)
            )
            if alias is None:
                current = None
                break
            coordinator_wave = _coordinator_wave_for_alias(coordinator_waves, alias)
            if coordinator_wave is not None:
                wave_index, phase_aliases = coordinator_wave
                if wave_index not in coordinator_started_waves:
                    coordinator_started_waves.add(wave_index)
                    _append_coordinator_event(
                        repo=repo,
                        roadmap=roadmap,
                        phase=alias,
                        action="coordinator.wave_started",
                        status=classifications.get(alias, "unknown"),
                        selection=selection,
                        metadata={
                            "wave_index": wave_index,
                            "phase_aliases": list(phase_aliases),
                        },
                    )
            if stop_requested(repo):
                current = alias
                append_event(
                    repo,
                    LoopEvent(
                        timestamp=utc_now(),
                        repo=str(repo),
                        roadmap=str(roadmap),
                        phase=alias,
                        action="operator_halt",
                        status=classifications.get(alias, "unknown"),
                        model=selection.model,
                        reasoning_effort=selection.effort,
                        source=selection.source,
                        override_reason=selection.override_reason,
                        metadata=operator_halt_metadata(repo),
                        **event_provenance(roadmap, alias),
                    ),
                )
                break
            # Stuck-loop detection: refuse to dispatch another iteration if
            # this phase has been ping-ponging in `(action=run, status=executing)`
            # past the iteration cap or time ceiling.
            stuck = detect_stuck_loop(
                repo,
                roadmap,
                alias,
                max_iterations=stuck_loop_iterations,
                max_minutes=stuck_loop_minutes,
            )
            if stuck is not None:
                classifications[alias] = "blocked"
                append_event(
                    repo,
                    LoopEvent(
                        timestamp=utc_now(),
                        repo=str(repo),
                        roadmap=str(roadmap),
                        phase=alias,
                        action="run",
                        status="blocked",
                        model=selection.model,
                        reasoning_effort=selection.effort,
                        source=selection.source,
                        override_reason=selection.override_reason,
                        blocker={
                            "human_required": True,
                            "blocker_class": "stuck_loop",
                            "blocker_summary": (
                                f"Phase {alias} has been in run/executing for "
                                f"{stuck.get('iteration_count')} iterations over "
                                f"{stuck.get('elapsed_minutes')} minutes without "
                                f"converging to complete or blocked "
                                f"(trigger: {stuck.get('trigger')}). Investigate "
                                f"the executor's terminal_status emission; consider "
                                f"`phase-loop reopen --phase {alias}` to reset and "
                                f"re-plan, or `phase-loop reconcile` if the work is "
                                f"actually complete."
                            ),
                            "required_human_inputs": (),
                            "access_attempts": (),
                        },
                        metadata={"stuck_loop": stuck},
                        **event_provenance(roadmap, alias),
                    ),
                )
                current = alias
                break
            if dry_run and results and alias == current:
                break
            current = alias
            if coordinator_wave is not None:
                wave_index, phase_aliases = coordinator_wave
                _append_coordinator_event(
                    repo=repo,
                    roadmap=roadmap,
                    phase=alias,
                    action="coordinator.phase_dispatched",
                    status=classifications.get(alias, "unknown"),
                    selection=selection,
                    metadata={
                        "wave_index": wave_index,
                        "phase_alias": alias,
                        "phase_aliases": list(phase_aliases),
                    },
                )
            start_gate = _cross_phase_dirty_start_gate(repo, alias)
            if start_gate is not None and allow_cross_phase_dirty_reason is None:
                classifications[alias] = "blocked"
                blocker = {
                    "human_required": False,
                    "blocker_class": "dirty_worktree_conflict",
                    "blocker_summary": (
                        f"Start gate refused {alias}: prior phase {start_gate['offending_phase']} "
                        f"still owns {len(start_gate['overlapping_dirty_paths'])} dirty path(s). "
                        "Commit or stash the paths, or recover the prior phase with "
                        f"`phase-loop reconcile --phase {start_gate['offending_phase']} --to-status planned --reason <text>`."
                    ),
                    "required_human_inputs": (),
                    "access_attempts": (),
                }
                append_event(
                    repo,
                    _start_gate_refused_event(
                        repo=repo,
                        roadmap=roadmap,
                        phase=alias,
                        action=action,
                        selection=selection,
                        blocker=blocker,
                        start_gate=start_gate,
                    ),
                )
                break
            if start_gate is not None:
                append_event(
                    repo,
                    _start_gate_bypassed_event(
                        repo=repo,
                        roadmap=roadmap,
                        phase=alias,
                        action=action,
                        selection=selection,
                        reason=allow_cross_phase_dirty_reason,
                        start_gate=start_gate,
                    ),
                )
            status = classifications.get(alias, "unknown")
            plan = find_plan_artifact(repo, alias, roadmap=roadmap)
            stale_pipeline_plan = _stale_pipeline_plan_candidate(repo, roadmap, alias) if plan is None else None
            if not dry_run and status in {"planned", "executed"}:
                branch_blocker = _ensure_pipeline_branch_before_dispatch(repo, roadmap)
                if branch_blocker is not None:
                    classifications[alias] = "blocked"
                    append_event(
                        repo,
                        LoopEvent(
                            timestamp=utc_now(),
                            repo=str(repo),
                            roadmap=str(roadmap),
                            phase=alias,
                            action=action,
                            status="blocked",
                            model=selection.model,
                            reasoning_effort=selection.effort,
                            source=selection.source,
                            override_reason=selection.override_reason,
                            blocker=branch_blocker,
                            metadata={
                                "pipeline_branch_governance": {
                                    "status": "blocked",
                                    "roadmap_version": _roadmap_version(roadmap),
                                    "default_branch": _default_branch(repo),
                                },
                                "terminal_summary": build_terminal_summary(
                                    terminal_status="blocked",
                                    terminal_blocker=branch_blocker,
                                    verification_status="blocked",
                                    next_action="Resolve the pipeline branch governance blocker before dispatch.",
                                ),
                            },
                            **event_provenance(roadmap, alias),
                        ),
                    )
                    break
            if (status in {"planned", "executed"} or explicit_product_action in {"execute", "review"}) and stale_pipeline_plan is not None:
                stale_plan, execution_diagnostic = stale_pipeline_plan
                classifications[alias] = "blocked"
                execution_blocker = pipeline_execution_blocker(execution_diagnostic)
                append_event(
                    repo,
                    LoopEvent(
                        timestamp=utc_now(),
                        repo=str(repo),
                        roadmap=str(roadmap),
                        phase=alias,
                        action=action,
                        status="blocked",
                        model=selection.model,
                        reasoning_effort=selection.effort,
                        source=selection.source,
                        override_reason=selection.override_reason,
                        blocker=execution_blocker,
                        metadata={
                            "pipeline_execution_preflight": {
                                "status": "blocked",
                                "diagnostic": execution_diagnostic.to_json(),
                                "plan": str(stale_plan),
                                "next_action": "Repair or replan the Pipeline-aware phase before launching child execution.",
                            },
                            "terminal_summary": _pipeline_blocked_terminal_summary(
                                repo=repo,
                                roadmap=roadmap,
                                plan=stale_plan,
                                phase=alias,
                                blocker=execution_blocker,
                                diagnostic=execution_diagnostic,
                                next_action="Repair or replan the Pipeline-aware phase before launching child execution.",
                            ),
                        },
                        **event_provenance(roadmap, alias),
                    ),
                )
                break
            if lane_scheduler_mode != "off" and status in {"planned", "executed"} and plan is not None:
                decision = _launch_ready_lane_wave(
                    repo=repo,
                    roadmap=roadmap,
                    plan=plan,
                    phase=alias,
                    mode=lane_scheduler_mode,
                    action=action,
                    selection=selection,
                    dry_run=dry_run,
                )
                classifications[alias] = decision["phase_status"]
                append_event(repo, decision["event"])
                break
            if work_unit_mode and status in {"planned", "executed"} and plan is not None:
                execution_diagnostic = pipeline_execution_plan_diagnostic(repo, plan, phase=alias, roadmap=roadmap)
                if execution_diagnostic is not None:
                    classifications[alias] = "blocked"
                    execution_blocker = pipeline_execution_blocker(execution_diagnostic)
                    append_event(
                        repo,
                        LoopEvent(
                            timestamp=utc_now(),
                            repo=str(repo),
                            roadmap=str(roadmap),
                            phase=alias,
                            action=action,
                            status="blocked",
                            model=selection.model,
                            reasoning_effort=selection.effort,
                            source=selection.source,
                            override_reason=selection.override_reason,
                            blocker=execution_blocker,
                            metadata={
                                "pipeline_execution_preflight": {
                                    "status": "blocked",
                                    "diagnostic": execution_diagnostic.to_json(),
                                    "plan": str(plan),
                                    "work_unit_mode": True,
                                },
                                "terminal_summary": _pipeline_blocked_terminal_summary(
                                    repo=repo,
                                    roadmap=roadmap,
                                    plan=plan,
                                    phase=alias,
                                    blocker=execution_blocker,
                                    diagnostic=execution_diagnostic,
                                    next_action="Repair or replan the Pipeline-aware phase before launching child work units.",
                                ),
                            },
                            **event_provenance(roadmap, alias),
                        ),
                    )
                    break
                state = select_next_work_unit(repo, plan, alias)
                if state is None:
                    classifications[alias] = "awaiting_phase_closeout"
                    append_event(
                        repo,
                        LoopEvent(
                            timestamp=utc_now(),
                            repo=str(repo),
                            roadmap=str(roadmap),
                            phase=alias,
                            action=action,
                            status="awaiting_phase_closeout",
                            model=selection.model,
                            reasoning_effort=selection.effort,
                            source=selection.source,
                            override_reason=selection.override_reason,
                            metadata={
                                "work_unit_mode": True,
                                "terminal_summary": build_terminal_summary(
                                    terminal_status="complete",
                                    terminal_blocker=None,
                                    verification_status="passed",
                                    next_action="All work units are complete; run phase closeout or reducer verification.",
                                ),
                            },
                            **event_provenance(roadmap, alias),
                        ),
                    )
                    break
                work_unit_selected_executor = None
                work_unit_policy = {"source": "work_unit_mode", "dry_run": dry_run}
                if rotation_state is not None and rotation_state.mode == "work_unit":
                    pinned_executor = state.policy.get("executor") if isinstance(state.policy, dict) else None
                    if pinned_executor:
                        work_unit_selected_executor = str(pinned_executor)
                        rotation_state.consume_policy_pin()
                    else:
                        work_unit_selected_executor = rotation_state.current()
                        rotation_state.advance(work_unit_selected_executor)
                    work_unit_policy.update(
                        {
                            "executor": work_unit_selected_executor,
                            "rotation": {
                                "mode": rotation_state.mode,
                                "on_policy_pin": rotation_state.on_policy_pin,
                            },
                        }
                    )
                launched = launch_work_unit_attempt(
                    repo,
                    roadmap,
                    plan,
                    state.identity,
                    policy=work_unit_policy,
                    artifacts={},
                )
                append_event(
                    repo,
                    LoopEvent(
                        timestamp=utc_now(),
                        repo=str(repo),
                        roadmap=str(roadmap),
                        phase=alias,
                        action=action,
                        status="executing",
                        model=selection.model,
                        reasoning_effort=selection.effort,
                        source=selection.source,
                        override_reason=selection.override_reason,
                        metadata={
                            "work_unit_mode": True,
                            "latest_work_unit": launched.to_json(),
                            **(
                                {
                                    "rotation": {
                                        "mode": rotation_state.mode,
                                        "selected_executor": work_unit_selected_executor,
                                        "on_policy_pin": rotation_state.on_policy_pin,
                                    }
                                }
                                if rotation_state is not None and rotation_state.mode == "work_unit"
                                else {}
                            ),
                            "terminal_summary": build_terminal_summary(
                                terminal_status="executing",
                                terminal_blocker=None,
                                verification_status="not_run",
                                next_action=f"Execute work unit {launched.work_unit_id}.",
                            ),
                        },
                        selected_executor=work_unit_selected_executor,
                        **event_provenance(roadmap, alias),
                    ),
                )
                classifications[alias] = "executing"
                break
            if status == "awaiting_phase_closeout":
                if closeout_mode != "manual":
                    classifications[alias], closeout_event = _perform_phase_closeout(
                        repo,
                        roadmap,
                        alias,
                        snapshot,
                        selection,
                        action=action,
                        closeout_mode=closeout_mode,
                    )
                    append_event(repo, closeout_event)
                    if phase:
                        break
                    continue
                break
            launch_action = None
            if explicit_product_action == "repair" or status == "blocked":
                if snapshot.human_required:
                    break
                recovered = _recover_verified_dirty_closeout(
                    repo,
                    roadmap,
                    alias,
                    plan,
                    snapshot,
                    selection,
                    action=action,
                )
                if recovered is not None:
                    recovered_status, recovered_event = recovered
                    append_event(repo, recovered_event)
                    classifications[alias] = recovered_status
                    if recovered_status == "awaiting_phase_closeout" and closeout_mode != "manual":
                        closeout_snapshot = reconcile(repo, roadmap)
                        classifications[alias], closeout_event = _perform_phase_closeout(
                            repo,
                            roadmap,
                            alias,
                            closeout_snapshot,
                            selection,
                            action=action,
                            closeout_mode=closeout_mode,
                        )
                        append_event(repo, closeout_event)
                        if phase:
                            break
                        continue
                    break
                repair_precondition = repair_precondition_for_snapshot(repo, roadmap, alias, plan, snapshot)
                if repair_precondition["status"] == "cleared":
                    status = "planned" if plan is not None else "unplanned"
                    set_phase_status(
                        repo,
                        roadmap,
                        alias,
                        classifications,
                        status,
                        reason="repair_precondition_cleared",
                        trigger="live_dirty_worktree_check",
                        selection=selection,
                        action=action,
                        metadata={"repair_precondition": repair_precondition},
                    )
                elif repair_precondition["status"] == "sticky":
                    break
                if repair_precondition["status"] != "cleared":
                    repair_context, repair_missing = _build_repair_context(repo, alias, plan, snapshot)
                    if repair_missing:
                        append_event(
                            repo,
                            LoopEvent(
                                timestamp=utc_now(),
                                repo=str(repo),
                                roadmap=str(roadmap),
                                phase=alias,
                                action=action,
                                status="blocked",
                                model=selection.model,
                                reasoning_effort=selection.effort,
                                source=selection.source,
                                override_reason=selection.override_reason,
                                blocker={
                                    "human_required": False,
                                    "blocker_class": snapshot.blocker_class,
                                    "blocker_summary": (
                                        "Repair launch skipped because trusted repair context is incomplete. "
                                        "Inspect `.phase-loop/tui-handoff.md`, run `phase-loop handoff`, "
                                        "then verify `phase-loop status --json` before retrying repair."
                                    ),
                                    "required_human_inputs": (),
                                    "access_attempts": (),
                                },
                                metadata={
                                    "repair_launch": {
                                        "status": "blocked",
                                        "reason": "missing_trusted_repair_context",
                                        "missing": repair_missing,
                                        "state_path": str(state_path(repo)),
                                        "events_path": str(event_path(repo)),
                                        "handoff_path": str(tui_handoff_path(repo)),
                                        "recovery_commands": [
                                            "phase-loop handoff",
                                            "phase-loop status --json",
                                        ],
                                    }
                                },
                                **event_provenance(roadmap, alias),
                            ),
                        )
                        break
                    launch_action = "repair"
            if launch_action is None:
                repair_context = None
                if explicit_product_action in {"roadmap", "plan", "execute", "review"}:
                    launch_action = explicit_product_action
                elif status == "planned" and plan is not None:
                    latest_phase_status = _latest_phase_event_status(repo, alias)
                    if latest_phase_status != "planned":
                        launch_action = "execute"
                    elif not force_replan and is_plan_doc_current(repo, alias, plan, roadmap):
                        append_event(
                            repo,
                            LoopEvent(
                                timestamp=utc_now(),
                                repo=str(repo),
                                roadmap=str(roadmap),
                                phase=alias,
                                action=action,
                                status="plan_skipped",
                                model=selection.model,
                                reasoning_effort=selection.effort,
                                source=selection.source,
                                override_reason=selection.override_reason,
                                metadata={
                                    "plan_doc_skip": {
                                        "reason": "plan_doc_current",
                                        "plan_artifact": _repo_relative(repo, plan),
                                        "forced_replan": False,
                                    }
                                },
                                **event_provenance(roadmap, alias),
                            ),
                        )
                        launch_action = "execute"
                    else:
                        launch_action = "plan"
                else:
                    if status in {"planned", "executed"} and plan is not None:
                        launch_action = "execute"
                    elif plan is not None and not force_replan and is_plan_doc_current(repo, alias, plan, roadmap):
                        # Fix for issue #4: when phase status is "executing", "unplanned",
                        # or any non-{planned,executed} state but a current plan-doc exists,
                        # prefer execute over re-planning. Stale "executing" status from a
                        # prior abandoned run was forcing planner re-dispatch.
                        append_event(
                            repo,
                            LoopEvent(
                                timestamp=utc_now(),
                                repo=str(repo),
                                roadmap=str(roadmap),
                                phase=alias,
                                action=action,
                                status="plan_skipped",
                                model=selection.model,
                                reasoning_effort=selection.effort,
                                source=selection.source,
                                override_reason=selection.override_reason,
                                metadata={
                                    "plan_doc_skip": {
                                        "reason": "plan_doc_current",
                                        "plan_artifact": _repo_relative(repo, plan),
                                        "forced_replan": False,
                                        "trigger_status": status,
                                    }
                                },
                                **event_provenance(roadmap, alias),
                            ),
                        )
                        launch_action = "execute"
                    else:
                        launch_action = "plan"
            planner_source_bundle_context = None
            execution_source_bundle_context = None
            if launch_action == "plan":
                bundle_diagnostic = phase_source_bundle_diagnostic(
                    repo,
                    effective_source_bundle_path,
                    phase=alias,
                    roadmap=roadmap,
                    pipeline_mode=effective_pipeline_mode,
                )
                if bundle_diagnostic is not None:
                    classifications[alias] = "blocked"
                    bundle_blocker = {
                        "human_required": bundle_diagnostic.human_required,
                        "blocker_class": bundle_diagnostic.blocker_class,
                        "blocker_summary": f"Pipeline source bundle validation failed: {bundle_diagnostic.kind}",
                        "required_human_inputs": (),
                    }
                    append_event(
                        repo,
                        LoopEvent(
                            timestamp=utc_now(),
                            repo=str(repo),
                            roadmap=str(roadmap),
                            phase=alias,
                            action=action,
                            status="blocked",
                            model=selection.model,
                            reasoning_effort=selection.effort,
                            source=selection.source,
                            override_reason=selection.override_reason,
                            blocker=bundle_blocker,
                            metadata={
                                "planner_source_bundle_context": {
                                    "status": "blocked",
                                    "diagnostic": bundle_diagnostic.to_json(),
                                    "source_bundle_path": str(effective_source_bundle_path or ""),
                                    "pipeline_mode": effective_pipeline_mode or "standalone",
                                },
                                "terminal_summary": build_terminal_summary(
                                    terminal_status="blocked",
                                    terminal_blocker=bundle_blocker,
                                    verification_status="blocked",
                                    next_action=(
                                        "Repair the Pipeline source bundle or rerun planning without pipeline_required mode "
                                        "before launching child planning."
                                    ),
                                ),
                            },
                            **event_provenance(roadmap, alias),
                        ),
                    )
                    break
                planner_source_bundle_context = load_phase_source_bundle(
                    repo,
                    effective_source_bundle_path,
                    phase=alias,
                    roadmap=roadmap,
                    pipeline_mode=effective_pipeline_mode,
                )
            elif launch_action in {"execute", "review"}:
                supplied_bundle_diagnostic = (
                    phase_source_bundle_diagnostic(
                        repo,
                        effective_source_bundle_path,
                        phase=alias,
                        roadmap=roadmap,
                        pipeline_mode=effective_pipeline_mode,
                    )
                    if effective_source_bundle_path or effective_pipeline_mode
                    else None
                )
                if supplied_bundle_diagnostic is not None:
                    classifications[alias] = "blocked"
                    execution_blocker = pipeline_execution_blocker(supplied_bundle_diagnostic)
                    append_event(
                        repo,
                        LoopEvent(
                            timestamp=utc_now(),
                            repo=str(repo),
                            roadmap=str(roadmap),
                            phase=alias,
                            action=action,
                            status="blocked",
                            model=selection.model,
                            reasoning_effort=selection.effort,
                            source=selection.source,
                            override_reason=selection.override_reason,
                            blocker=execution_blocker,
                            metadata={
                                "pipeline_execution_preflight": {
                                    "status": "blocked",
                                    "diagnostic": supplied_bundle_diagnostic.to_json(),
                                    "source_bundle_path": str(effective_source_bundle_path or ""),
                                    "pipeline_mode": effective_pipeline_mode or "standalone",
                                    "next_action": "Repair the Pipeline source bundle before launching child execution.",
                                },
                                "terminal_summary": _pipeline_blocked_terminal_summary(
                                    repo=repo,
                                    roadmap=roadmap,
                                    plan=plan,
                                    phase=alias,
                                    blocker=execution_blocker,
                                    diagnostic=supplied_bundle_diagnostic,
                                    next_action="Repair the Pipeline source bundle before launching child execution.",
                                ),
                            },
                            **event_provenance(roadmap, alias),
                        ),
                    )
                    break
                execution_diagnostic = pipeline_execution_plan_diagnostic(repo, plan, phase=alias, roadmap=roadmap) if plan is not None else None
                if execution_diagnostic is not None:
                    classifications[alias] = "blocked"
                    execution_blocker = pipeline_execution_blocker(execution_diagnostic)
                    append_event(
                        repo,
                        LoopEvent(
                            timestamp=utc_now(),
                            repo=str(repo),
                            roadmap=str(roadmap),
                            phase=alias,
                            action=action,
                            status="blocked",
                            model=selection.model,
                            reasoning_effort=selection.effort,
                            source=selection.source,
                            override_reason=selection.override_reason,
                            blocker=execution_blocker,
                            metadata={
                                "pipeline_execution_preflight": {
                                    "status": "blocked",
                                    "diagnostic": execution_diagnostic.to_json(),
                                    "plan": str(plan),
                                    "next_action": "Repair or replan the Pipeline-aware phase before launching child execution.",
                                },
                                "terminal_summary": _pipeline_blocked_terminal_summary(
                                    repo=repo,
                                    roadmap=roadmap,
                                    plan=plan,
                                    phase=alias,
                                    blocker=execution_blocker,
                                    diagnostic=execution_diagnostic,
                                    next_action="Repair or replan the Pipeline-aware phase before launching child execution.",
                                ),
                            },
                            **event_provenance(roadmap, alias),
                        ),
                    )
                    break
                if effective_source_bundle_path or effective_pipeline_mode:
                    execution_source_bundle_context = load_phase_source_bundle(
                        repo,
                        effective_source_bundle_path,
                        phase=alias,
                        roadmap=roadmap,
                        pipeline_mode=effective_pipeline_mode,
                    )
                elif plan is not None:
                    execution_source_bundle_context = load_execution_phase_source_bundle(
                        repo,
                        plan,
                        phase=alias,
                        roadmap=roadmap,
                    )
            if launch_action == "execute":
                prompt_profile = "execute"
            elif launch_action == "repair":
                prompt_profile = "repair"
            else:
                prompt_profile = "plan"
            plan_hints_doc = parse_dispatch_hints_doc(plan, kind="plan") if plan is not None else ({}, ())
            roadmap_hints_doc = parse_dispatch_hints_doc(roadmap, kind="roadmap")
            plan_dispatch_hints = (
                dispatch_hints_for_action(plan_hints_doc[0], launch_action) if plan is not None else None
            )
            roadmap_dispatch_hints = dispatch_hints_for_action(roadmap_hints_doc[0], launch_action)
            # Dispatch-hints parse errors (e.g. planner emitted a literal not in
            # DISPATCH_CAPABILITIES allowlist) surface as contract_bug blockers
            # rather than crashing the runner. Same pattern as F3 Execution Policy.
            dispatch_hints_parse_error = (plan_hints_doc[1] + roadmap_hints_doc[1])
            plan_execution_policy_doc = parse_execution_policy(plan, kind="plan") if plan is not None else None
            roadmap_execution_policy_doc = parse_execution_policy(roadmap, kind="roadmap")
            policy_parse_error = (
                (plan_execution_policy_doc.parse_error if plan_execution_policy_doc else None)
                or roadmap_execution_policy_doc.parse_error
            )
            if dispatch_hints_parse_error:
                first = dispatch_hints_parse_error[0]
                allowed_msg = (
                    "DispatchHints literal allowlist — see DISPATCH_CAPABILITIES / "
                    "EXECUTORS in phase_loop_runtime/models.py. "
                    "Either patch the plan to remove the invalid literal "
                    "OR add the literal to the runner allowlist and pip install."
                )
                classifications[alias] = "blocked"
                dispatch_blocker = {
                    "human_required": True,
                    "blocker_class": "contract_bug",
                    "blocker_summary": (
                        f"{first.path}: ## Dispatch Hints bucket {first.bucket!r} "
                        f"contains an invalid literal "
                        f"({first.invalid_literal or 'unknown'}). "
                        f"Runner error: {first.raw_message}. {allowed_msg}"
                    ),
                    "required_human_inputs": (),
                }
                append_event(
                    repo,
                    LoopEvent(
                        timestamp=utc_now(),
                        repo=str(repo),
                        roadmap=str(roadmap),
                        phase=alias,
                        action=action,
                        status="blocked",
                        model=selection.model,
                        reasoning_effort=selection.effort,
                        source=selection.source,
                        override_reason=selection.override_reason,
                        blocker=dispatch_blocker,
                        metadata={
                            "dispatch_hints_parse_error": {
                                "path": first.path,
                                "bucket": first.bucket,
                                "invalid_literal": first.invalid_literal,
                                "raw_message": first.raw_message,
                                "error_count": len(dispatch_hints_parse_error),
                            },
                        },
                        **event_provenance(roadmap, alias),
                    ),
                )
                current = alias
                break
            if policy_parse_error is not None:
                classifications[alias] = "blocked"
                policy_blocker = {
                    "human_required": False,
                    "blocker_class": "contract_bug",
                    "blocker_summary": (
                        f"{policy_parse_error.path}:{policy_parse_error.line_number} — "
                        f"malformed Execution Policy line: {policy_parse_error.raw_line!r}"
                    ),
                    "required_human_inputs": (),
                }
                append_event(
                    repo,
                    LoopEvent(
                        timestamp=utc_now(),
                        repo=str(repo),
                        roadmap=str(roadmap),
                        phase=alias,
                        action=action,
                        status="blocked",
                        model=selection.model,
                        reasoning_effort=selection.effort,
                        source=selection.source,
                        override_reason=selection.override_reason,
                        blocker=policy_blocker,
                        metadata={
                            "execution_policy_parse_error": policy_parse_error.to_json(),
                            "terminal_summary": build_terminal_summary(
                                terminal_status="blocked",
                                terminal_blocker=policy_blocker,
                                verification_status="blocked",
                                next_action=(
                                    "Edit the Execution Policy block to match the documented grammar, "
                                    "or remove the block to fall back to defaults, before relaunching."
                                ),
                            ),
                        },
                        **event_provenance(roadmap, alias),
                    ),
                )
                break
            plan_execution_policy = (
                execution_policy_for_action(plan_execution_policy_doc, launch_action) if plan_execution_policy_doc is not None else None
            )
            roadmap_execution_policy = execution_policy_for_action(roadmap_execution_policy_doc, launch_action)
            plan_policy_dispatch_hints = execution_policy_dispatch_hints(plan_execution_policy)
            roadmap_policy_dispatch_hints = execution_policy_dispatch_hints(roadmap_execution_policy)
            effective_plan_dispatch_hints = plan_policy_dispatch_hints or plan_dispatch_hints
            effective_roadmap_dispatch_hints = roadmap_policy_dispatch_hints or roadmap_dispatch_hints
            rotation_preferred_executor = None
            rotation_policy_pin = bool(
                (plan_policy_dispatch_hints and plan_policy_dispatch_hints.preferred_executors)
                or (roadmap_policy_dispatch_hints and roadmap_policy_dispatch_hints.preferred_executors)
            )
            if rotation_state is not None and rotation_state.mode == "phase" and executor is None:
                if rotation_policy_pin:
                    rotation_state.consume_policy_pin()
                else:
                    rotation_preferred_executor = rotation_state.current()
            operator_dispatch_hints = _operator_dispatch_hints(
                action=launch_action,
                executor=executor or rotation_preferred_executor,
                allowed_executors=allowed_executors,
                fallback_executors=fallback_executors,
                disabled_executors=disabled_executors,
                required_capabilities=required_capabilities,
            )
            dispatch_decision = resolve_dispatch_decision(
                action=launch_action,
                dry_run=dry_run,
                repo=repo,
                operator=operator_dispatch_hints,
                plan=effective_plan_dispatch_hints,
                roadmap=effective_roadmap_dispatch_hints,
            )
            if rotation_state is not None and rotation_state.mode == "phase" and rotation_preferred_executor is not None:
                rotation_state.advance(dispatch_decision.selected_executor)
            repair_loop_pivot: dict[str, object] | None = None
            if (
                launch_action == "repair"
                and not dispatch_decision.blocked
                and dispatch_decision.selected_executor
                and _recent_repeated_repair_failures(repo, alias, dispatch_decision.selected_executor, snapshot) >= 2
            ):
                pivot_executor = _repair_fallback_candidate(
                    dispatch_decision,
                    operator_fallback_executors=fallback_executors,
                    disabled_executors=disabled_executors,
                )
                repair_loop_pivot = {
                    "status": "pivoted" if pivot_executor else "blocked",
                    "phase": alias,
                    "from_executor": dispatch_decision.selected_executor,
                    "to_executor": pivot_executor,
                    "reason": "repeated_repair_failure_fingerprint",
                    "blocker_class": snapshot.blocker_class,
                    "blocker_summary": snapshot.blocker_summary,
                }
                if pivot_executor:
                    operator_dispatch_hints = DispatchHints(
                        preferred_executors=(pivot_executor,),
                        allowed_executors=dispatch_decision.allowed_executors,
                        fallback_executors=tuple(
                            executor for executor in dispatch_decision.fallback_executors if executor != pivot_executor
                        ),
                        disabled_executors=tuple(disabled_executors),
                        required_capabilities=tuple(required_capabilities),
                        source="repair-loop-pivot",
                        action=launch_action,
                    )
                    dispatch_decision = resolve_dispatch_decision(
                        action=launch_action,
                        dry_run=dry_run,
                        repo=repo,
                        operator=operator_dispatch_hints,
                        plan=None,
                        roadmap=None,
                    )
                else:
                    classifications[alias] = "blocked"
                    loop_blocker = {
                        "human_required": False,
                        "blocker_class": "repeated_verification_failure",
                        "blocker_summary": (
                            f"Repair launch for {alias} repeated the same {dispatch_decision.selected_executor} "
                            "failure fingerprint twice and no fallback executor was configured."
                        ),
                        "required_human_inputs": (),
                        "access_attempts": (),
                    }
                    append_event(
                        repo,
                        LoopEvent(
                            timestamp=utc_now(),
                            repo=str(repo),
                            roadmap=str(roadmap),
                            phase=alias,
                            action=action,
                            status="blocked",
                            model=selection.model,
                            reasoning_effort=selection.effort,
                            source=selection.source,
                            override_reason=selection.override_reason,
                            blocker=loop_blocker,
                            metadata={
                                "repair_loop_guard": repair_loop_pivot,
                                "dispatch_decision": dispatch_decision.to_json(),
                            },
                            **event_provenance(roadmap, alias),
                        ),
                    )
                    break
            if dispatch_decision.blocked:
                classifications[alias] = "blocked"
                dispatch_blocker = {
                    "human_required": False,
                    "blocker_summary": dispatch_decision.blocked_summary,
                    "required_human_inputs": (),
                }
                append_event(
                    repo,
                    LoopEvent(
                        timestamp=utc_now(),
                        repo=str(repo),
                        roadmap=str(roadmap),
                        phase=alias,
                        action=action,
                        status="blocked",
                        model=selection.model,
                        reasoning_effort=selection.effort,
                        source=selection.source,
                        override_reason=selection.override_reason,
                        blocker=dispatch_blocker,
                        metadata={
                            "dispatch_decision": dispatch_decision.to_json(),
                            **(
                                {
                                    "rotation": {
                                        "mode": rotation_state.mode,
                                        "preferred_executor": rotation_preferred_executor,
                                        "policy_pin": rotation_policy_pin,
                                        "on_policy_pin": rotation_state.on_policy_pin,
                                    }
                                }
                                if rotation_state is not None
                                else {}
                            ),
                            "dispatch_hints": {
                                "operator": operator_dispatch_hints.to_json() if operator_dispatch_hints else None,
                                "plan": effective_plan_dispatch_hints.to_json() if effective_plan_dispatch_hints else None,
                                "roadmap": effective_roadmap_dispatch_hints.to_json() if effective_roadmap_dispatch_hints else None,
                            },
                            **({"repair_loop_guard": repair_loop_pivot} if repair_loop_pivot else {}),
                            "execution_policy": {
                                "plan": plan_execution_policy.to_json() if plan_execution_policy else None,
                                "roadmap": roadmap_execution_policy.to_json() if roadmap_execution_policy else None,
                            },
                            "terminal_summary": build_terminal_summary(
                                terminal_status="blocked",
                                terminal_blocker=dispatch_blocker,
                                verification_status="blocked",
                                next_action=(
                                    f"{dispatch_decision.blocked_summary} Inspect the dispatch metadata in the latest event "
                                    "before retrying with different hints or a different executor."
                                ),
                            ),
                        },
                        selected_executor=dispatch_decision.selected_executor,
                        **event_provenance(roadmap, alias),
                    ),
                )
                break
            resolved_executor = dispatch_decision.selected_executor or "codex"
            selection = resolve_profile_for_executor(
                action=launch_action,
                executor=resolved_executor,
                profile=model_profile,
                model=model,
                effort=effort,
            )
            try:
                execution_policy = resolve_execution_policy(
                    action=launch_action,
                    executor=resolved_executor,
                    model_selection=selection,
                    operator_model=model,
                    operator_effort=effort,
                    plan_policy=plan_execution_policy,
                    roadmap_policy=roadmap_execution_policy,
                )
                selection = resolve_model_selection_from_policy(
                    profile=selection.profile,
                    resolved_policy=execution_policy,
                )
            except ValueError as exc:
                classifications[alias] = "blocked"
                policy_blocker = {
                    "human_required": False,
                    "blocker_class": "contract_bug",
                    "blocker_summary": f"Execution policy failed closed: {exc}",
                    "required_human_inputs": (),
                }
                append_event(
                    repo,
                    LoopEvent(
                        timestamp=utc_now(),
                        repo=str(repo),
                        roadmap=str(roadmap),
                        phase=alias,
                        action=action,
                        status="blocked",
                        model=selection.model,
                        reasoning_effort=selection.effort,
                        source=selection.source,
                        override_reason=selection.override_reason,
                        blocker=policy_blocker,
                        metadata={
                            "dispatch_decision": dispatch_decision.to_json(),
                            **(
                                {
                                    "rotation": {
                                        "mode": rotation_state.mode,
                                        "preferred_executor": rotation_preferred_executor,
                                        "policy_pin": rotation_policy_pin,
                                        "on_policy_pin": rotation_state.on_policy_pin,
                                    }
                                }
                                if rotation_state is not None
                                else {}
                            ),
                            "execution_policy": {
                                "plan": plan_execution_policy.to_json() if plan_execution_policy else None,
                                "roadmap": roadmap_execution_policy.to_json() if roadmap_execution_policy else None,
                                "blocked_reason": str(exc),
                            },
                            "terminal_summary": build_terminal_summary(
                                terminal_status="blocked",
                                terminal_blocker=policy_blocker,
                                verification_status="blocked",
                                next_action="Fix the execution policy or add an explicit supported fallback before retrying.",
                            ),
                        },
                        selected_executor=dispatch_decision.selected_executor,
                        **event_provenance(roadmap, alias),
                    ),
                )
                break
            if not dry_run and launch_action == "execute":
                release_blocker = release_dispatch_blocker(repo, plan)
                if release_blocker:
                    classifications[alias] = "blocked"
                    append_event(
                        repo,
                        LoopEvent(
                            timestamp=utc_now(),
                            repo=str(repo),
                            roadmap=str(roadmap),
                            phase=alias,
                            action=action,
                            status="blocked",
                            model=selection.model,
                            reasoning_effort=selection.effort,
                            source=selection.source,
                            override_reason=selection.override_reason,
                            blocker=release_blocker.to_blocker(),
                        metadata=release_blocker.metadata,
                        selected_executor=dispatch_decision.selected_executor,
                        **event_provenance(roadmap, alias),
                    ),
                    )
                    snapshot = StateSnapshot(
                        timestamp=utc_now(),
                        repo=str(repo),
                        roadmap=str(roadmap),
                        phases=classifications,
                        current_phase=alias,
                        last_action=action,
                        model=selection.model,
                        reasoning_effort=selection.effort,
                        source=selection.source,
                        override_reason=selection.override_reason,
                        human_required=True,
                        blocker_class=release_blocker.blocker_class,
                        blocker_summary=release_blocker.blocker_summary,
                        required_human_inputs=release_blocker.required_human_inputs,
                        **snapshot_provenance(roadmap),
                    )
                    _write_state_and_handoff(
                        repo,
                        roadmap,
                        snapshot,
                        action=action,
                        results=results,
                        output_path=output_path,
                        override_phase=selected,
                        source_bundle_path=effective_source_bundle_path,
                        pipeline_mode=effective_pipeline_mode,
                    )
                    break
            if not dry_run and launch_action == "execute" and plan is not None:
                verification_preflight_blocker = _execute_verification_preflight_blocker(repo, roadmap, plan)
                if verification_preflight_blocker is not None:
                    classifications[alias] = "blocked"
                    terminal_summary = build_terminal_summary(
                        terminal_status="blocked",
                        terminal_blocker=verification_preflight_blocker,
                        verification_status="blocked",
                        next_action=str(verification_preflight_blocker["blocker_summary"]),
                    )
                    append_event(
                        repo,
                        LoopEvent(
                            timestamp=utc_now(),
                            repo=str(repo),
                            roadmap=str(roadmap),
                            phase=alias,
                            action=action,
                            status="blocked",
                            model=selection.model,
                            reasoning_effort=selection.effort,
                            source=selection.source,
                            override_reason=selection.override_reason,
                            blocker=verification_preflight_blocker,
                            metadata={
                                "verification_preflight": {
                                    "status": "blocked",
                                    "enforcement": _verification_enforcement_mode(),
                                },
                                "terminal_summary": terminal_summary,
                            },
                            selected_executor=dispatch_decision.selected_executor,
                            **event_provenance(roadmap, alias),
                        ),
                    )
                    snapshot = StateSnapshot(
                        timestamp=utc_now(),
                        repo=str(repo),
                        roadmap=str(roadmap),
                        phases=classifications,
                        current_phase=alias,
                        last_action=action,
                        model=selection.model,
                        reasoning_effort=selection.effort,
                        source=selection.source,
                        override_reason=selection.override_reason,
                        human_required=False,
                        blocker_class=str(verification_preflight_blocker["blocker_class"]),
                        blocker_summary=str(verification_preflight_blocker["blocker_summary"]),
                        required_human_inputs=(),
                        terminal_summary={"phase": alias, **terminal_summary},
                        **snapshot_provenance(roadmap),
                    )
                    _write_state_and_handoff(
                        repo,
                        roadmap,
                        snapshot,
                        action=action,
                        results=results,
                        output_path=output_path,
                        override_phase=selected,
                        source_bundle_path=effective_source_bundle_path,
                        pipeline_mode=effective_pipeline_mode,
                    )
                    break
            prompt_bundle = build_prompt(
                launch_action,
                roadmap=roadmap,
                phase=alias,
                plan=plan,
                blocker_summary=snapshot.blocker_summary,
                repair_context=repair_context,
                harness_target=resolved_executor,
                injection_mode_override="context_file" if resolved_executor == "command" else None,
                planner_source_bundle_context=planner_source_bundle_context,
            )
            command_adapter = (
                CommandAdapterConfig(
                    name=command_adapter_name,
                    template=command_template,
                    delivery_mode="context_file",
                )
                if resolved_executor == "command" and command_adapter_name and command_template
                else None
            )
            request = build_launch_request(
                executor=resolved_executor,
                action=launch_action,
                repo=repo,
                roadmap=roadmap,
                phase=alias,
                plan=plan,
                model_selection=selection,
                prompt_bundle=prompt_bundle,
                command_adapter=command_adapter,
                dispatch_decision=dispatch_decision,
                claude_execution_mode=claude_execution_mode,
                json_output=json_output,
                bypass_approvals=bypass_approvals,
            )
            spec = build_launch_spec(request)
            if not spec.available and (not dry_run or spec.executor == "command"):
                classifications[alias] = "blocked"
                event_blocker = {
                    "human_required": False,
                    "blocker_summary": spec.reason,
                    "required_human_inputs": (),
                }
                artifacts = run_artifacts(repo, alias, launch_action, len(results) + 1, spec) if observe else {}
                terminal_summary = _persist_terminal_summary(
                    artifacts,
                    build_terminal_summary(
                        terminal_status="blocked",
                        terminal_blocker=event_blocker,
                        verification_status="blocked",
                        next_action=spec.reason or "Provide a valid explicit adapter configuration before retrying.",
                        artifact_paths={key: str(value) for key, value in artifacts.items()} if artifacts else {},
                    ),
                )
                terminal_summary = _attach_work_unit_metric(
                    repo=repo,
                    phase=alias,
                    action=launch_action,
                    artifacts=artifacts,
                    request=request,
                    result=None,
                    terminal_summary=terminal_summary,
                )
                append_event(
                    repo,
                    LoopEvent(
                        timestamp=utc_now(),
                        repo=str(repo),
                        roadmap=str(roadmap),
                        phase=alias,
                        action=action,
                        status="blocked",
                        model=selection.model,
                        reasoning_effort=selection.effort,
                        source=selection.source,
                        override_reason=selection.override_reason,
                        command=metadata_command(spec.command, spec.prompt_bundle.render_prompt()),
                        blocker=event_blocker,
                        metadata={
                            "launch_request": request.to_json(),
                            "launch_spec": spec.to_json(),
                            "terminal_summary": terminal_summary,
                            "artifacts": {key: str(value) for key, value in artifacts.items()} if artifacts else {},
                        },
                        selected_executor=dispatch_decision.selected_executor,
                        **event_provenance(roadmap, alias),
                    ),
                )
                break
            artifacts = run_artifacts(repo, alias, launch_action, len(results) + 1, spec) if observe else {}
            if artifacts:
                merge_launch_metadata(artifacts.get("metadata"), {"execution_policy": execution_policy.to_json()})
                if execution_source_bundle_context is not None:
                    merge_launch_metadata(
                        artifacts.get("metadata"),
                        {"pipeline_source_bundle": execution_source_bundle_context.to_json()},
                    )
            preflight = run_auth_preflight(spec) if not dry_run else None
            if preflight and preflight.metadata and artifacts:
                merge_launch_metadata(artifacts.get("metadata"), {"auth_preflight_result": preflight.metadata})
            if preflight and not preflight.ok:
                classifications[alias] = "blocked"
                event_blocker = {
                    "human_required": False,
                    "blocker_class": preflight.blocker_class,
                    "blocker_summary": preflight.blocker_summary,
                    "required_human_inputs": (),
                    "access_attempts": (),
                }
                suggested_ttl_seconds = getattr(preflight, "suggested_ttl_seconds", None)
                demoted_to = getattr(preflight, "demoted_to", None)
                if suggested_ttl_seconds is not None:
                    event_blocker["suggested_ttl_seconds"] = suggested_ttl_seconds
                if demoted_to:
                    event_blocker["demoted_to"] = demoted_to
                _record_preflight_degradation(repo, spec.executor, alias, preflight)
                terminal_summary = _persist_terminal_summary(
                    artifacts,
                    build_terminal_summary(
                        terminal_status="blocked",
                        terminal_blocker=event_blocker,
                        verification_status="blocked",
                        next_action=(
                            f"Restore {_executor_display_name(spec.executor)} CLI auth or subscription readiness before retrying live execution."
                        ),
                        artifact_paths={key: str(value) for key, value in artifacts.items()} if artifacts else {},
                    ),
                )
                terminal_summary = _attach_work_unit_metric(
                    repo=repo,
                    phase=alias,
                    action=launch_action,
                    artifacts=artifacts,
                    request=request,
                    result=None,
                    terminal_summary=terminal_summary,
                )
                append_event(
                    repo,
                    LoopEvent(
                        timestamp=utc_now(),
                        repo=str(repo),
                        roadmap=str(roadmap),
                        phase=alias,
                        action=action,
                        status="blocked",
                        model=selection.model,
                        reasoning_effort=selection.effort,
                        source=selection.source,
                        override_reason=selection.override_reason,
                        command=metadata_command(spec.command, spec.prompt_bundle.render_prompt()),
                        blocker=event_blocker,
                        metadata={
                            "launch_request": request.to_json(),
                            "launch_spec": spec.to_json(),
                            "auth_preflight_result": preflight.metadata or {},
                            "terminal_summary": terminal_summary,
                            "artifacts": {key: str(value) for key, value in artifacts.items()} if artifacts else {},
                        },
                        selected_executor=dispatch_decision.selected_executor,
                        **event_provenance(roadmap, alias),
                    ),
                )
                break
            pre_launch_dirty_paths = _dirty_paths(repo) if not dry_run else []
            failed_launch_closeout_override: dict[str, object] | None = None
            if coordinator_wave is not None:
                wave_index, phase_aliases = coordinator_wave
                _append_coordinator_event(
                    repo=repo,
                    roadmap=roadmap,
                    phase=alias,
                    action="coordinator.worker_dispatched",
                    status=classifications.get(alias, "unknown"),
                    selection=selection,
                    metadata={
                        "wave_index": wave_index,
                        "phase_alias": alias,
                        "phase_aliases": list(phase_aliases),
                        "summary_path": str(worker_summary_path(repo, roadmap, alias)),
                    },
                )
            result = launch_with_spec(
                spec,
                dry_run=dry_run,
                log_path=artifacts.get("log"),
                heartbeat_path=artifacts.get("heartbeat") if heartbeat_enabled else None,
                stream_output=stream_output,
                heartbeat_interval_seconds=heartbeat_interval_seconds,
                quiet_warning_seconds=quiet_warning_seconds,
                quiet_blocker_seconds=quiet_blocker_seconds,
            )
            results.append(result)
            if artifacts:
                merge_launch_metadata(
                    artifacts.get("metadata"),
                    {
                        "process_pid": result.process_pid,
                        "process_group_id": result.process_group_id,
                        "started_at": result.started_at,
                        "finished_at": result.finished_at,
                        "timed_out": result.timed_out,
                        "interrupted": result.interrupted,
                        "stalled": result.stalled,
                        "cleanup_evidence": result.cleanup_evidence,
                    },
                )
            failed_launch_closeout = _trusted_failed_launch_closeout(result, spec) if result.failed else None
            if failed_launch_closeout is not None:
                failed_launch_closeout_override = {
                    "reason": "failed wrapper returned a trusted shared automation closeout",
                    "automation_status": failed_launch_closeout.get("automation_status"),
                    "automation_verification_status": failed_launch_closeout.get("automation_verification_status"),
                    "original_returncode": failed_launch_closeout.get("original_returncode"),
                }
                result = replace(result, returncode=0)
                if artifacts:
                    merge_launch_metadata(
                        artifacts.get("metadata"),
                        {
                            "failed_launch_closeout_override": failed_launch_closeout_override,
                        },
                    )
            launch_contract_blocker = _launch_contract_blocker(result, artifacts, spec.executor, alias)
            if result.failed:
                launch_blocker = launch_contract_blocker or _executor_launch_failure_blocker(spec.executor, alias, result.output)
                classifications[alias] = "blocked" if launch_blocker else "unknown"
                failure_metadata = _launch_failure_metadata(result, artifacts, request=request, spec=spec)
                if launch_blocker:
                    failure_metadata["terminal_summary"] = _persist_terminal_summary(
                        artifacts,
                        build_terminal_summary(
                            terminal_status="blocked",
                            terminal_blocker=launch_blocker,
                            verification_status="blocked",
                            next_action=launch_blocker["blocker_summary"],
                            artifact_paths={key: str(value) for key, value in artifacts.items()} if artifacts else {},
                        ),
                    )
                append_event(
                    repo,
                    LoopEvent(
                        timestamp=utc_now(),
                        repo=str(repo),
                        roadmap=str(roadmap),
                        phase=alias,
                        action=action,
                        status="blocked" if launch_blocker else "unknown",
                        model=selection.model,
                        reasoning_effort=selection.effort,
                        source=selection.source,
                        override_reason=selection.override_reason,
                        command=metadata_command(spec.command, spec.prompt_bundle.render_prompt()),
                        blocker=launch_blocker,
                        metadata=failure_metadata,
                        selected_executor=dispatch_decision.selected_executor,
                        **event_provenance(roadmap, alias),
                    ),
                )
                snapshot = StateSnapshot(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phases=classifications,
                    current_phase=alias,
                    last_action=action,
                    model=selection.model,
                    reasoning_effort=selection.effort,
                    source=selection.source,
                    override_reason=selection.override_reason,
                    terminal_summary={"phase": alias, **failure_metadata["terminal_summary"]},
                    **snapshot_provenance(roadmap),
                )
                _write_state_and_handoff(
                    repo,
                    roadmap,
                    snapshot,
                    action=action,
                    results=results,
                    output_path=output_path,
                    override_phase=selected,
                    source_bundle_path=effective_source_bundle_path,
                    pipeline_mode=effective_pipeline_mode,
                )
                current = alias
                break
            if launch_contract_blocker:
                classifications[alias] = "blocked"
                failure_metadata = _launch_failure_metadata(result, artifacts, request=request, spec=spec)
                failure_metadata["terminal_summary"] = _persist_terminal_summary(
                    artifacts,
                    build_terminal_summary(
                        terminal_status="blocked",
                        terminal_blocker=launch_contract_blocker,
                        verification_status="blocked",
                        next_action=str(launch_contract_blocker["blocker_summary"]),
                        artifact_paths={key: str(value) for key, value in artifacts.items()} if artifacts else {},
                    ),
                )
                append_event(
                    repo,
                    LoopEvent(
                        timestamp=utc_now(),
                        repo=str(repo),
                        roadmap=str(roadmap),
                        phase=alias,
                        action=action,
                        status="blocked",
                        model=selection.model,
                        reasoning_effort=selection.effort,
                        source=selection.source,
                        override_reason=selection.override_reason,
                        command=metadata_command(spec.command, spec.prompt_bundle.render_prompt()),
                        blocker=launch_contract_blocker,
                        metadata=failure_metadata,
                        selected_executor=dispatch_decision.selected_executor,
                        **event_provenance(roadmap, alias),
                    ),
                )
                snapshot = StateSnapshot(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phases=classifications,
                    current_phase=alias,
                    last_action=action,
                    model=selection.model,
                    reasoning_effort=selection.effort,
                    source=selection.source,
                    override_reason=selection.override_reason,
                    terminal_summary={"phase": alias, **failure_metadata["terminal_summary"]},
                    **snapshot_provenance(roadmap),
                )
                _write_state_and_handoff(
                    repo,
                    roadmap,
                    snapshot,
                    action=action,
                    results=results,
                    output_path=output_path,
                    override_phase=selected,
                    source_bundle_path=effective_source_bundle_path,
                    pipeline_mode=effective_pipeline_mode,
                )
                current = alias
                break
            if dry_run:
                terminal_summary = _persist_terminal_summary(
                    artifacts,
                    build_terminal_summary(
                        terminal_status="dry_run",
                        terminal_blocker=None,
                        verification_status="not_run",
                        next_action="Dry run only; no child command was executed and phase state was not advanced.",
                        artifact_paths={key: str(value) for key, value in artifacts.items()} if artifacts else {},
                    ),
                )
                terminal_summary = _attach_work_unit_metric(
                    repo=repo,
                    phase=alias,
                    action=launch_action,
                    artifacts=artifacts,
                    request=request,
                    result=result,
                    terminal_summary=terminal_summary,
                )
                launch_event_metadata = result.event_metadata()
                if terminal_summary.get("metric_id"):
                    launch_event_metadata["metric_id"] = terminal_summary["metric_id"]
                append_event(
                    repo,
                    LoopEvent(
                        timestamp=utc_now(),
                        repo=str(repo),
                        roadmap=str(roadmap),
                        phase=alias,
                        action=action,
                        status=classifications.get(alias, "unplanned"),
                        model=selection.model,
                        reasoning_effort=selection.effort,
                        source=selection.source,
                        override_reason=selection.override_reason,
                        command=metadata_command(spec.command, spec.prompt_bundle.render_prompt()),
                        metadata={
                            "launch": launch_event_metadata,
                            "launch_request": request.to_json(),
                            "launch_spec": spec.to_json(),
                            **(
                                {
                                    "dispatch_decision": request.dispatch_decision.to_json(),
                                    "dispatch_summary": describe_dispatch_decision(request.dispatch_decision),
                                }
                                if request.dispatch_decision is not None
                                else {}
                            ),
                            "artifacts": {key: str(value) for key, value in artifacts.items()} if artifacts else {},
                            "terminal_summary": terminal_summary,
                            "dry_run_only": True,
                            "pipeline_mode": effective_pipeline_mode,
                            **(
                                {
                                    "rotation": {
                                        "mode": rotation_state.mode,
                                        "preferred_executor": rotation_preferred_executor,
                                        "policy_pin": rotation_policy_pin,
                                        "on_policy_pin": rotation_state.on_policy_pin,
                                    }
                                }
                                if rotation_state is not None
                                else {}
                            ),
                        },
                        selected_executor=dispatch_decision.selected_executor,
                        **event_provenance(roadmap, alias),
                    ),
                )
                if phase:
                    break
                break
            post_snapshot = reconcile(repo, roadmap)
            post_launch = post_snapshot.phases.get(alias)
            status_after_launch = (
                post_launch
                if post_launch in {"planned", "complete", "blocked", "unknown", "executed", "awaiting_phase_closeout"}
                else ("planned" if dry_run else "executed")
            )
            set_phase_status(
                repo,
                roadmap,
                alias,
                classifications,
                status_after_launch,
                reason="launch_result_reduction",
                trigger=launch_action,
                selection=selection,
                action=action,
            )
            event_blocker = None
            child_automation: dict[str, object] = {}
            post_launch_plan = find_plan_artifact(repo, alias, roadmap=roadmap)
            runner_verification: dict[str, object] | None = None
            if not dry_run:
                verification_plan = post_launch_plan or plan
                if launch_action == "execute" and verification_plan is not None:
                    runner_verification = _run_execute_verification(
                        repo=repo,
                        roadmap=roadmap,
                        plan=verification_plan,
                        artifacts=artifacts,
                    )
                    if runner_verification and artifacts:
                        merge_launch_metadata(artifacts.get("metadata"), {"runner_verification": runner_verification})
                    if (
                        runner_verification
                        and not runner_verification.get("ok", False)
                        and _verification_enforcement_mode() == "hard"
                    ):
                        status_after_launch = "blocked"
                        set_phase_status(
                            repo,
                            roadmap,
                            alias,
                            classifications,
                            status_after_launch,
                            reason="runner_verification_failed",
                            trigger=launch_action,
                            selection=selection,
                            action=action,
                        )
                        event_blocker = {
                            "human_required": False,
                            "blocker_class": "repeated_verification_failure",
                            "blocker_summary": str(
                                runner_verification.get("blocker_summary")
                                or "Runner-owned verification failed before closeout reduction."
                            ),
                            "required_human_inputs": (),
                            "access_attempts": (),
                        }
                child_automation = _parsed_child_automation(result, spec)
                if runner_verification:
                    child_automation["runner_verification"] = runner_verification
                if failed_launch_closeout_override and child_automation:
                    child_automation["failed_launch_closeout_override"] = failed_launch_closeout_override
                    child_automation["original_returncode"] = failed_launch_closeout_override.get("original_returncode")
                automation_status = child_automation.get("automation_status")
                validation_plan = post_launch_plan or plan
                if validation_plan is not None and child_automation:
                    gate_validation = validate_produced_gates(validation_plan, child_automation)
                    if gate_validation.warning:
                        child_automation["produced_gates_warning"] = gate_validation.warning
                        child_automation["produced_gates_validation"] = gate_validation.to_json()
                    if not gate_validation.ok:
                        status_after_launch = "blocked"
                        set_phase_status(
                            repo,
                            roadmap,
                            alias,
                            classifications,
                            status_after_launch,
                            reason="gate_validation_failed",
                            trigger=launch_action,
                            selection=selection,
                            action=action,
                        )
                        child_automation["produced_gates_validation"] = gate_validation.to_json()
                        event_blocker = {
                            "human_required": False,
                            "blocker_class": gate_validation.blocker_class or "contract_bug",
                            "blocker_summary": gate_validation.blocker_summary
                            or "completed closeout produced_if_gates failed validation",
                            "required_human_inputs": (),
                            "access_attempts": (),
                        }
                        automation_status = status_after_launch
                if (
                    event_blocker is None
                    and child_automation
                    and _phase_status_literal(automation_status) == "complete"
                ):
                    tier3_audit = _runner_tier3_closeout_audit(
                        repo=repo,
                        roadmap=roadmap,
                        phase=alias,
                        cli_enable_tier3=enable_tier_3,
                        tier3_budget=tier_3_budget,
                        model=selection.model,
                        reasoning_effort=selection.effort,
                        source=selection.source,
                    )
                    if tier3_audit is not None:
                        child_automation["tier3_audit"] = tier3_audit["summary"]
                        if tier3_audit.get("blocker"):
                            status_after_launch = "blocked"
                            set_phase_status(
                                repo,
                                roadmap,
                                alias,
                                classifications,
                                status_after_launch,
                                reason="tier3_audit_blocked",
                                trigger=launch_action,
                                selection=selection,
                                action=action,
                            )
                            event_blocker = tier3_audit["blocker"]
                            automation_status = status_after_launch
                if not automation_status and launch_action == "plan" and post_launch_plan is not None:
                    artifact_automation = _parsed_artifact_automation(post_launch_plan, spec)
                    artifact_status = artifact_automation.get("automation_status")
                    if artifact_status == "planned":
                        child_automation = artifact_automation
                        automation_status = artifact_status
                if _requires_shared_automation_closeout(result, spec) and not _repair_launch_cleared_phase(
                    launch_action,
                    post_launch,
                    post_snapshot,
                    alias,
                ):
                    if not automation_status:
                        status_after_launch = "blocked"
                        set_phase_status(
                            repo,
                            roadmap,
                            alias,
                            classifications,
                            status_after_launch,
                            reason="missing_shared_automation_closeout",
                            trigger=launch_action,
                            selection=selection,
                            action=action,
                        )
                        event_blocker = {
                            "human_required": False,
                            "blocker_class": "repeated_verification_failure",
                            "blocker_summary": (
                                f"{_executor_display_name(spec.executor)} live launch for {alias} exited successfully but did not emit a valid shared automation closeout."
                            ),
                            "required_human_inputs": (),
                            "access_attempts": (),
                        }
                    elif child_automation.get("automation_parse_error"):
                        status_after_launch = "blocked"
                        set_phase_status(
                            repo,
                            roadmap,
                            alias,
                            classifications,
                            status_after_launch,
                            reason="automation_parse_error",
                            trigger=launch_action,
                            selection=selection,
                            action=action,
                        )
                        event_blocker = {
                            "human_required": False,
                            "blocker_class": child_automation.get("automation_parse_error_blocker_class")
                            or "repeated_verification_failure",
                            "blocker_summary": str(child_automation["automation_parse_error"]),
                            "required_human_inputs": (),
                            "access_attempts": (),
                        }
                    elif isinstance(automation_status, str):
                        if automation_status == "delegated":
                            delegation_request = child_automation.get("delegation_request")
                            if isinstance(delegation_request, DelegationRequest):
                                delegated_outcome = launch_delegated_child(
                                    repo=repo,
                                    roadmap=roadmap,
                                    parent_phase=alias,
                                    parent_action=launch_action,
                                    parent_executor=spec.executor,
                                    parent_run_id=artifacts.get("root").name if artifacts.get("root") else None,
                                    plan=plan,
                                    request=delegation_request,
                                    dry_run=dry_run,
                                    json_output=json_output,
                                    stream_output=stream_output,
                                    bypass_approvals=bypass_approvals,
                                    heartbeat_interval_seconds=heartbeat_interval_seconds,
                                    quiet_warning_seconds=quiet_warning_seconds,
                                    quiet_blocker_seconds=quiet_blocker_seconds,
                                )
                                child_automation["delegated_child"] = delegated_outcome
                                closeout = (
                                    delegated_outcome.get("launch_metadata", {})
                                    .get("parent_child", {})
                                    .get("child_closeout_result", {})
                                )
                                if isinstance(closeout, dict):
                                    status_after_launch, event_blocker = _delegated_child_status_and_blocker(closeout)
                                else:
                                    status_after_launch = "blocked"
                                    event_blocker = {
                                        "human_required": False,
                                        "blocker_class": "repeated_verification_failure",
                                        "blocker_summary": "Delegated child did not return closeout metadata.",
                                        "required_human_inputs": (),
                                        "access_attempts": (),
                                    }
                                set_phase_status(
                                    repo,
                                    roadmap,
                                    alias,
                                    classifications,
                                    status_after_launch,
                                    reason="delegated_child_reduction",
                                    trigger=launch_action,
                                    selection=selection,
                                    action=action,
                                )
                                automation_status = status_after_launch
                            else:
                                status_after_launch = "blocked"
                                set_phase_status(
                                    repo,
                                    roadmap,
                                    alias,
                                    classifications,
                                    status_after_launch,
                                    reason="delegation_request_invalid",
                                    trigger=launch_action,
                                    selection=selection,
                                    action=action,
                                )
                                child_automation["automation_parse_error"] = (
                                    f"{_executor_display_name(spec.executor)} live launch for {alias} requested delegation "
                                    "without a typed delegation_request block."
                                )
                                event_blocker = {
                                    "human_required": False,
                                    "blocker_class": "repeated_verification_failure",
                                    "blocker_summary": str(child_automation["automation_parse_error"]),
                                    "required_human_inputs": (),
                                    "access_attempts": (),
                                }
                                automation_status = status_after_launch
                        else:
                            automation_status_literal = _phase_status_literal(automation_status)
                            if automation_status_literal is None:
                                status_after_launch = "blocked"
                                child_automation["automation_parse_error"] = (
                                    f"{_executor_display_name(spec.executor)} live launch for {alias} emitted an invalid "
                                    f"shared automation status: {automation_status!r}."
                                )
                                event_blocker = {
                                    "human_required": False,
                                    "blocker_class": "repeated_verification_failure",
                                    "blocker_summary": str(child_automation["automation_parse_error"]),
                                    "required_human_inputs": (),
                                    "access_attempts": (),
                                }
                                automation_status = status_after_launch
                            else:
                                status_after_launch = automation_status_literal
                        set_phase_status(
                            repo,
                            roadmap,
                            alias,
                            classifications,
                            status_after_launch,
                            reason="launch_result_reduction",
                            trigger=launch_action,
                            selection=selection,
                            action=action,
                        )
                        automation_human_required = str(child_automation.get("automation_human_required", "")).lower() == "true"
                        automation_blocker_class = _optional_automation_literal(child_automation.get("automation_blocker_class"))
                        automation_blocker_summary = _optional_automation_literal(child_automation.get("automation_blocker_summary"))
                        automation_required_human_inputs = tuple(child_automation.get("automation_required_human_inputs", ()))
                        if automation_human_required or automation_blocker_class or automation_blocker_summary:
                            event_blocker = {
                                "human_required": automation_human_required,
                                "blocker_class": automation_blocker_class,
                                "blocker_summary": automation_blocker_summary,
                                "required_human_inputs": automation_required_human_inputs,
                                "access_attempts": (),
                            }
                        if automation_status == "blocked":
                            default_summary = (
                                f"{_executor_display_name(spec.executor)} live launch for {alias} reported blocked without "
                                "a frozen blocker class or actionable blocker summary."
                            )
                            if event_blocker is None:
                                event_blocker = {
                                    "human_required": False,
                                    "blocker_class": "repeated_verification_failure",
                                    "blocker_summary": default_summary,
                                    "required_human_inputs": (),
                                    "access_attempts": (),
                                }
                            else:
                                if not event_blocker.get("blocker_class"):
                                    event_blocker["blocker_class"] = "repeated_verification_failure"
                                if not event_blocker.get("blocker_summary"):
                                    event_blocker["blocker_summary"] = default_summary
            if (
                launch_action == "plan"
                and not dry_run
                and post_launch_plan is not None
                and status_after_launch in {"complete", "executed"}
                and event_blocker is None
            ):
                status_after_launch = "planned"
                set_phase_status(
                    repo,
                    roadmap,
                    alias,
                    classifications,
                    status_after_launch,
                    reason="planning_launch_produced_plan",
                    trigger=launch_action,
                    selection=selection,
                    action=action,
                )
            if post_launch_plan is not None:
                plan = post_launch_plan

            executor_closeout_event = _executor_closeout_event(
                repo=repo,
                roadmap=roadmap,
                phase=alias,
                selection=selection,
                spec=spec,
                dispatch_decision=dispatch_decision,
                child_automation=child_automation,
            )
            if executor_closeout_event is not None:
                append_event(repo, executor_closeout_event)
            if event_blocker is None and child_automation:
                ratification_blocker = _emit_ratification_if_reached(
                    repo=repo,
                    roadmap=roadmap,
                    phase=alias,
                    plan=plan,
                    child_automation=child_automation,
                )
                if ratification_blocker is not None:
                    status_after_launch = "blocked"
                    event_blocker = ratification_blocker
                    set_phase_status(
                        repo,
                        roadmap,
                        alias,
                        classifications,
                        status_after_launch,
                        reason="ratification_event_blocked",
                        trigger=launch_action,
                        selection=selection,
                        action=action,
                    )

            missing_plan_after_planning: dict[str, object] = {}
            if (
                launch_action == "plan"
                and not dry_run
                and status_after_launch in {"planned", "executed", "complete"}
                and post_launch_plan is None
                and post_launch not in {"complete", "awaiting_phase_closeout", "blocked"}
            ):
                invalid_plan_artifacts: list[dict[str, str]] = []
                for candidate in sorted((repo / "plans").glob("phase-plan-v*-*.md")):
                    match = PLAN_RE.search(candidate.name)
                    if not match or match.group(2).lower() != alias.lower():
                        continue
                    diagnostic = plan_artifact_diagnostic(repo, candidate, roadmap, alias)
                    if diagnostic:
                        invalid_plan_artifacts.append(
                            {
                                "artifact": str(candidate),
                                "diagnostic": diagnostic,
                            }
                        )
                status_after_launch = "blocked"
                set_phase_status(
                    repo,
                    roadmap,
                    alias,
                    classifications,
                    status_after_launch,
                    reason="planning_launch_missing_current_plan_artifact",
                    trigger=launch_action,
                    selection=selection,
                    action=action,
                )
                missing_plan_after_planning = {
                    "reason": "planning_launch_missing_current_plan_artifact",
                    "expected_phase": alias,
                    "roadmap": str(roadmap),
                    "invalid_plan_artifacts": invalid_plan_artifacts,
                    "recovery_commands": [
                        f"codex-plan-phase {roadmap} {alias}",
                        f"{_phase_loop_cli()} status --repo {repo} --json",
                    ],
                }
                if invalid_plan_artifacts:
                    blocker_summary = (
                        f"Planning turn for {alias} wrote a phase plan artifact, but it does not match "
                        f"the current roadmap: {invalid_plan_artifacts[0]['diagnostic']} "
                        f"({invalid_plan_artifacts[0]['artifact']})."
                    )
                else:
                    blocker_summary = (
                        f"Planning turn for {alias} exited successfully but did not create a current phase plan artifact."
                    )
                event_blocker = {
                    "human_required": False,
                    "blocker_class": "repeated_verification_failure",
                    "blocker_summary": blocker_summary,
                    "required_human_inputs": (),
                    "access_attempts": (),
                }
            dirty_summary: dict[str, object] = {}
            repair_completion_success = launch_action == "repair" and _repair_completion_success(child_automation)
            executor_terminal_summary = _executor_terminal_summary(child_automation)
            completion_dirty_paths = _dirty_paths(repo) if status_after_launch == "complete" else []
            plan_dirty_paths = (
                _dirty_paths(repo)
                if closeout_mode != "manual" and launch_action in {"plan", "repair"} and status_after_launch == "planned"
                else []
            )
            blocked_plan_dirty_paths = (
                _dirty_paths(repo)
                if closeout_mode != "manual"
                and launch_action in {"plan", "repair"}
                and status_after_launch == "blocked"
                and post_launch_plan is not None
                and not bool((event_blocker or {}).get("human_required"))
                and (
                    post_launch == "planned"
                    or _optional_automation_literal(child_automation.get("automation_blocker_class"))
                    == "dirty_worktree_conflict"
                )
                else []
            )
            incomplete_execute_dirty_paths = (
                _dirty_paths(repo)
                if launch_action == "execute"
                and (
                    status_after_launch in {"planned", "executed"}
                    or _successful_missing_closeout_blocker(result, event_blocker)
                )
                else []
            )
            if completion_dirty_paths:
                dirty_summary = _classify_dirty_paths(
                    repo,
                    roadmap,
                    plan,
                    pre_launch_dirty_paths,
                    completion_dirty_paths,
                    allow_pre_existing_phase_owned=repair_completion_success,
                    current_phase=alias,
                    terminal_summary=executor_terminal_summary,
                    emit_runtime_relaxation_event=True,
                )
                boundary_blocker = _pipeline_boundary_blocker(
                    repo,
                    roadmap,
                    plan,
                    execution_source_bundle_context,
                    completion_dirty_paths,
                )
                if boundary_blocker is not None:
                    status_after_launch, event_blocker = "blocked", boundary_blocker
                    dirty_summary["pipeline_write_boundary"] = boundary_blocker
                elif event_blocker is not None:
                    status_after_launch = "blocked"
                else:
                    status_after_launch, event_blocker = _dirty_outcome(
                        dirty_summary,
                        blocked_summary="Phase reported complete but left dirty paths that are not closeout-safe.",
                    )
                set_phase_status(
                    repo,
                    roadmap,
                    alias,
                    classifications,
                    status_after_launch,
                    reason="completion_dirty_worktree_outcome",
                    trigger=launch_action,
                    selection=selection,
                    action=action,
                )
            elif plan_dirty_paths:
                dirty_summary = _classify_dirty_paths(
                    repo,
                    roadmap,
                    plan,
                    pre_launch_dirty_paths,
                    plan_dirty_paths,
                    current_phase=alias,
                    terminal_summary=executor_terminal_summary,
                    emit_runtime_relaxation_event=True,
                )
                status_after_launch, event_blocker = _dirty_outcome(
                    dirty_summary,
                    blocked_summary="Phase planning turn produced dirty paths that are not closeout-safe.",
                )
                set_phase_status(
                    repo,
                    roadmap,
                    alias,
                    classifications,
                    status_after_launch,
                    reason="planning_dirty_worktree_outcome",
                    trigger=launch_action,
                    selection=selection,
                    action=action,
                )
            elif blocked_plan_dirty_paths:
                plan_dirty_paths = blocked_plan_dirty_paths
                dirty_summary = _classify_dirty_paths(
                    repo,
                    roadmap,
                    plan,
                    pre_launch_dirty_paths,
                    plan_dirty_paths,
                    current_phase=alias,
                    terminal_summary=executor_terminal_summary,
                    emit_runtime_relaxation_event=True,
                )
                status_after_launch, event_blocker = _dirty_outcome(
                    dirty_summary,
                    blocked_summary="Phase planning turn reported a stale or non-human blocker and produced dirty paths that are not closeout-safe.",
                )
                set_phase_status(
                    repo,
                    roadmap,
                    alias,
                    classifications,
                    status_after_launch,
                    reason="blocked_planning_dirty_worktree_outcome",
                    trigger=launch_action,
                    selection=selection,
                    action=action,
                )
            elif incomplete_execute_dirty_paths:
                dirty_summary = _classify_dirty_paths(
                    repo,
                    roadmap,
                    plan,
                    pre_launch_dirty_paths,
                    incomplete_execute_dirty_paths,
                    current_phase=alias,
                    terminal_summary=executor_terminal_summary,
                    emit_runtime_relaxation_event=True,
                )
                boundary_blocker = _pipeline_boundary_blocker(
                    repo,
                    roadmap,
                    plan,
                    execution_source_bundle_context,
                    incomplete_execute_dirty_paths,
                )
                if boundary_blocker is not None:
                    status_after_launch, event_blocker = "blocked", boundary_blocker
                    dirty_summary["pipeline_write_boundary"] = boundary_blocker
                else:
                    status_after_launch, event_blocker = _dirty_outcome(
                        dirty_summary,
                        blocked_summary="Phase execute turn ended without completion evidence and left dirty paths that are not closeout-safe.",
                    )
                set_phase_status(
                    repo,
                    roadmap,
                    alias,
                    classifications,
                    status_after_launch,
                    reason="incomplete_execute_dirty_worktree_outcome",
                    trigger=launch_action,
                    selection=selection,
                    action=action,
                )
            elif (
                launch_action == "execute"
                and status_after_launch == "blocked"
                and _optional_automation_literal(child_automation.get("automation_blocker_class")) == "dirty_worktree_conflict"
                and child_automation.get("automation_verification_status") == "passed"
            ):
                verified_dirty_paths = _dirty_paths(repo)
                if verified_dirty_paths:
                    dirty_summary = _classify_dirty_paths(
                        repo,
                        roadmap,
                        plan,
                        pre_launch_dirty_paths,
                        verified_dirty_paths,
                        current_phase=alias,
                        terminal_summary=executor_terminal_summary,
                        emit_runtime_relaxation_event=True,
                    )
                    boundary_blocker = _pipeline_boundary_blocker(
                        repo,
                        roadmap,
                        plan,
                        execution_source_bundle_context,
                        verified_dirty_paths,
                    )
                    if boundary_blocker is not None:
                        status_after_launch, event_blocker = "blocked", boundary_blocker
                        dirty_summary["pipeline_write_boundary"] = boundary_blocker
                    else:
                        status_after_launch, event_blocker = _dirty_outcome(
                            dirty_summary,
                            blocked_summary="Phase reported verified dirty closeout but left dirty paths that are not closeout-safe.",
                        )
                    if status_after_launch == "awaiting_phase_closeout":
                        completion_dirty_paths = verified_dirty_paths
                    set_phase_status(
                        repo,
                        roadmap,
                        alias,
                        classifications,
                        status_after_launch,
                        reason="verified_dirty_closeout_outcome",
                        trigger=launch_action,
                        selection=selection,
                        action=action,
                    )
            if (
                status_after_launch == "blocked"
                and post_snapshot.current_phase == alias
                and event_blocker is None
                and (post_snapshot.human_required or post_snapshot.blocker_class or post_launch == "blocked")
            ):
                event_blocker = {
                    "human_required": post_snapshot.human_required,
                    "blocker_class": post_snapshot.blocker_class,
                    "blocker_summary": post_snapshot.blocker_summary,
                    "required_human_inputs": post_snapshot.required_human_inputs,
                    "access_attempts": post_snapshot.access_attempts,
                }
            launch_metadata = _launch_event_metadata(
                result,
                artifacts,
                request=request,
                spec=spec,
                status_after_launch=status_after_launch,
                event_blocker=event_blocker,
                child_automation=child_automation,
                completion_dirty_paths=completion_dirty_paths,
                plan_dirty_paths=plan_dirty_paths,
                incomplete_execute_dirty_paths=incomplete_execute_dirty_paths,
                dirty_summary=dirty_summary,
                missing_plan_after_planning=missing_plan_after_planning,
                execution_policy=execution_policy.to_json(),
            )
            if repair_loop_pivot:
                launch_metadata["repair_loop_guard"] = repair_loop_pivot
            if rotation_state is not None:
                launch_metadata["rotation"] = {
                    "mode": rotation_state.mode,
                    "preferred_executor": rotation_preferred_executor,
                    "policy_pin": rotation_policy_pin,
                    "on_policy_pin": rotation_state.on_policy_pin,
                }
            event = LoopEvent(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phase=alias,
                action=action,
                status=status_after_launch,
                model=selection.model,
                reasoning_effort=selection.effort,
                source=selection.source,
                override_reason=selection.override_reason,
                command=metadata_command(spec.command, spec.prompt_bundle.render_prompt()),
                blocker=event_blocker,
                metadata=launch_metadata,
                selected_executor=dispatch_decision.selected_executor,
                **event_provenance(roadmap, alias),
            )
            append_event(repo, event)
            status_after_closeout = status_after_launch
            if status_after_launch == "awaiting_phase_closeout" and closeout_mode != "manual":
                closeout_snapshot = reconcile(repo, roadmap)
                classifications[alias], closeout_event = _perform_phase_closeout(
                    repo,
                    roadmap,
                    alias,
                    closeout_snapshot,
                    selection,
                    action=action,
                    closeout_mode=closeout_mode,
                )
                append_event(repo, closeout_event)
                status_after_closeout = classifications[alias]
            if coordinator_wave is not None:
                wave_index, phase_aliases = coordinator_wave
                latest_snapshot = reconcile(repo, roadmap)
                latest_statuses = latest_snapshot.phases
                worker_terminal = launch_metadata.get("terminal_summary") if isinstance(launch_metadata, dict) else None
                if isinstance(worker_terminal, dict):
                    summary_path = write_worker_summary(repo, roadmap, alias, worker_terminal)
                else:
                    summary_path = worker_summary_path(repo, roadmap, alias)
                worker_summary_read = read_worker_summary(repo, roadmap, alias)
                if worker_summary_read["status"] != "ok":
                    set_phase_status(
                        repo,
                        roadmap,
                        alias,
                        classifications,
                        "blocked",
                        reason="worker_summary_ingest_failed",
                        trigger=launch_action,
                        selection=selection,
                        action=action,
                    )
                    latest_snapshot = reconcile(repo, roadmap)
                    latest_statuses = latest_snapshot.phases
                _append_coordinator_event(
                    repo=repo,
                    roadmap=roadmap,
                    phase=alias,
                    action="coordinator.worker_completed",
                    status=latest_statuses.get(alias, status_after_closeout),
                    selection=selection,
                    metadata={
                        "wave_index": wave_index,
                        "phase_alias": alias,
                        "phase_aliases": list(phase_aliases),
                        "phase_status": latest_statuses.get(alias, status_after_closeout),
                        "summary_path": str(summary_path),
                        "summary_read": worker_summary_read,
                    },
                )
                _append_coordinator_event(
                    repo=repo,
                    roadmap=roadmap,
                    phase=alias,
                    action="coordinator.phase_completed",
                    status=latest_statuses.get(alias, status_after_closeout),
                    selection=selection,
                    metadata={
                        "wave_index": wave_index,
                        "phase_alias": alias,
                        "phase_aliases": list(phase_aliases),
                        "phase_status": latest_statuses.get(alias, status_after_closeout),
                    },
                )
                if _parallel_wave_terminal(phase_aliases, latest_statuses):
                    failed_phases = [
                        phase_alias
                        for phase_alias in phase_aliases
                        if latest_statuses.get(phase_alias) in {"blocked", "unknown"}
                    ]
                    succeeded_phases = [
                        phase_alias
                        for phase_alias in phase_aliases
                        if latest_statuses.get(phase_alias) == "complete"
                    ]
                    _append_coordinator_event(
                        repo=repo,
                        roadmap=roadmap,
                        phase=alias,
                        action="coordinator.wave_completed",
                        status=latest_statuses.get(alias, status_after_closeout),
                        selection=selection,
                        metadata={
                            "wave_index": wave_index,
                            "phase_aliases": list(phase_aliases),
                            "succeeded_phases": succeeded_phases,
                            "failed_phases": failed_phases,
                        },
                    )
            if full_phase:
                if status_after_closeout in {"complete", "blocked", "awaiting_phase_closeout", "unknown"}:
                    phase_cycles_completed += 1
                    if phase_cycles_completed >= max_phases or phase:
                        break
                    continue
                if phase and status_after_closeout == "planned":
                    continue
            if phase:
                break

    snapshot = reconcile(repo, roadmap)
    classifications = snapshot.phases
    current = snapshot.current_phase
    snapshot = StateSnapshot(
        timestamp=utc_now(),
        repo=str(repo),
        roadmap=str(roadmap),
        phases=classifications,
        current_phase=current,
        last_action=action,
        model=selection.model,
        reasoning_effort=selection.effort,
        source=selection.source,
        override_reason=selection.override_reason,
        human_required=snapshot.human_required,
        blocker_class=snapshot.blocker_class,
        blocker_summary=snapshot.blocker_summary,
        required_human_inputs=snapshot.required_human_inputs,
        access_attempts=snapshot.access_attempts,
        dirty_paths=snapshot.dirty_paths,
        phase_owned_dirty_paths=snapshot.phase_owned_dirty_paths,
        previous_phase_owned_paths=snapshot.previous_phase_owned_paths,
        unowned_dirty_paths=snapshot.unowned_dirty_paths,
        pre_existing_dirty_paths=snapshot.pre_existing_dirty_paths,
        phase_owned_dirty=snapshot.phase_owned_dirty,
        terminal_summary=snapshot.terminal_summary,
        closeout_terminal_status=snapshot.closeout_terminal_status,
        closeout_summary=snapshot.closeout_summary,
        ledger_warnings=snapshot.ledger_warnings,
        **snapshot_provenance(roadmap),
    )
    _write_state_and_handoff(
        repo,
        roadmap,
        snapshot,
        action=action,
        results=results,
        output_path=output_path,
        override_phase=selected,
        source_bundle_path=effective_source_bundle_path,
        pipeline_mode=effective_pipeline_mode,
    )
    return snapshot, results


def launch_delegated_child(
    *,
    repo: Path,
    roadmap: Path,
    parent_phase: str,
    parent_action: str,
    plan: Path | None,
    request: DelegationRequest,
    parent_executor: str | None = None,
    parent_run_id: str | None = None,
    current_depth: int = 0,
    current_fanout: int = 0,
    max_depth: int = 2,
    max_fanout: int = 2,
    dry_run: bool = False,
    json_output: bool = False,
    stream_output: bool = False,
    bypass_approvals: bool = False,
    heartbeat_interval_seconds: int = 30,
    quiet_warning_seconds: int = 600,
    quiet_blocker_seconds: int = 1800,
) -> dict[str, object]:
    decision = validate_delegation_request(
        repo,
        roadmap,
        plan,
        request,
        active_loop_mode="product",
        current_depth=current_depth,
        current_fanout=current_fanout,
        max_depth=max_depth,
        max_fanout=max_fanout,
        dry_run=dry_run,
    )
    parent_child = ParentChildRunMetadata(
        parent_phase=parent_phase.upper(),
        parent_action=parent_action,
        parent_executor=parent_executor,
        parent_run_id=parent_run_id,
        child_phase=parent_phase.upper(),
        child_action=request.product_action,
        request_id=request.request_id,
        child_executor=decision.selected_executor or request.target_executor,
        child_worktree_root=str(repo.resolve()),
    )
    selection = resolve_profile_for_executor(
        action=request.product_action,
        executor=decision.selected_executor or request.target_executor,
    )

    if not decision.approved:
        artifacts = run_artifacts(repo, parent_phase.upper(), request.product_action, 1, ["delegation", "denied"])
        terminal_summary = _persist_terminal_summary(
            artifacts,
            build_terminal_summary(
                terminal_status="blocked",
                terminal_blocker={
                    "human_required": decision.human_required,
                    "blocker_class": decision.blocker_class,
                    "blocker_summary": decision.summary,
                    "required_human_inputs": (),
                },
                verification_status="blocked",
                next_action=decision.summary,
                artifact_paths={key: str(value) for key, value in artifacts.items()},
            ),
        )
        terminal_summary = _attach_delegation_metric(
            repo=repo,
            phase=parent_phase.upper(),
            action=request.product_action,
            target_executor=request.target_executor,
            artifacts=artifacts,
            result=None,
            terminal_summary=terminal_summary,
            launch_metadata={"executor": request.target_executor},
        )
        parent_child = ParentChildRunMetadata(
            parent_phase=parent_child.parent_phase,
            parent_action=parent_child.parent_action,
            parent_executor=parent_child.parent_executor,
            parent_run_id=parent_child.parent_run_id,
            child_phase=parent_child.child_phase,
            child_action=parent_child.child_action,
            request_id=parent_child.request_id,
            child_executor=parent_child.child_executor,
            observed_launch_path=str(artifacts["metadata"]),
            child_artifact_root=str(artifacts["root"]),
            child_worktree_root=parent_child.child_worktree_root,
            child_closeout_result=_delegated_child_closeout_result(
                decision=decision,
                terminal_summary=terminal_summary,
                dry_run=dry_run,
            ),
        )
        launch_metadata = merge_launch_metadata(
            artifacts["metadata"],
            {
                "dispatch_decision": decision.dispatch_decision,
                "dispatch_summary": (
                    describe_dispatch_decision(DispatchDecision(selected_executor=None, **decision.dispatch_decision))
                    if isinstance(decision.dispatch_decision, dict)
                    else None
                ),
                "delegation_request": request.to_json(),
                "delegation_decision": decision.to_json(),
                "parent_child": parent_child.to_json(),
                "terminal_summary": terminal_summary,
            },
        )
        return {
            "decision": decision.to_json(),
            "result": None,
            "artifacts": {key: str(value) for key, value in artifacts.items()},
            "launch_metadata": launch_metadata,
            "terminal_summary": terminal_summary,
        }

    prompt_bundle = build_prompt(
        request.product_action,
        roadmap=roadmap,
        phase=parent_phase.upper(),
        plan=plan,
        blocker_summary=request.review_context,
        repair_context={"delegation_reason": request.reason} if request.repair_context else None,
        harness_target=decision.selected_executor or request.target_executor,
        delegation_request=request,
        parent_child_metadata=parent_child,
    )
    launch_request = build_launch_request(
        executor=decision.selected_executor or request.target_executor,
        action=request.product_action,
        repo=repo,
        roadmap=roadmap,
        phase=parent_phase.upper(),
        plan=plan,
        model_selection=selection,
        prompt_bundle=prompt_bundle,
        dispatch_decision=DispatchDecision(**decision.dispatch_decision) if isinstance(decision.dispatch_decision, dict) else None,
        delegation_request=request,
        parent_child_metadata=parent_child,
        json_output=json_output,
        bypass_approvals=bypass_approvals,
    )
    spec = build_launch_spec(launch_request)
    artifacts = run_artifacts(repo, parent_phase.upper(), request.product_action, 1, spec)
    parent_child = ParentChildRunMetadata(
        parent_phase=parent_child.parent_phase,
        parent_action=parent_child.parent_action,
        parent_executor=parent_child.parent_executor,
        parent_run_id=parent_child.parent_run_id,
        child_phase=parent_child.child_phase,
        child_action=parent_child.child_action,
        request_id=parent_child.request_id,
        child_executor=parent_child.child_executor,
        observed_launch_path=str(artifacts["metadata"]),
        child_artifact_root=str(artifacts["root"]),
        child_worktree_root=parent_child.child_worktree_root,
    )
    launch_metadata = merge_launch_metadata(
        artifacts["metadata"],
        {
            "delegation_request": request.to_json(),
            "delegation_decision": decision.to_json(),
            "parent_child": parent_child.to_json(),
        },
    )
    preflight = None if dry_run else run_auth_preflight(spec)
    if preflight and preflight.metadata:
        merge_launch_metadata(artifacts["metadata"], {"auth_preflight_result": preflight.metadata})
    if preflight and not preflight.ok:
        child_blocker = {
            "human_required": False,
            "blocker_class": preflight.blocker_class,
            "blocker_summary": preflight.blocker_summary,
            "required_human_inputs": (),
        }
        suggested_ttl_seconds = getattr(preflight, "suggested_ttl_seconds", None)
        demoted_to = getattr(preflight, "demoted_to", None)
        if suggested_ttl_seconds is not None:
            child_blocker["suggested_ttl_seconds"] = suggested_ttl_seconds
        if demoted_to:
            child_blocker["demoted_to"] = demoted_to
        _record_preflight_degradation(repo, spec.executor, parent_phase.upper(), preflight)
        terminal_summary = _persist_terminal_summary(
            artifacts,
            build_terminal_summary(
                terminal_status="blocked",
                terminal_blocker=child_blocker,
                verification_status="blocked",
                next_action=preflight.blocker_summary or "Restore delegated child auth readiness before retrying.",
                artifact_paths={key: str(value) for key, value in artifacts.items()},
            ),
        )
        terminal_summary = _attach_delegation_metric(
            repo=repo,
            phase=parent_phase.upper(),
            action=request.product_action,
            target_executor=decision.selected_executor or request.target_executor,
            artifacts=artifacts,
            result=None,
            terminal_summary=terminal_summary,
        )
        parent_child = ParentChildRunMetadata(
            parent_phase=parent_child.parent_phase,
            parent_action=parent_child.parent_action,
            parent_executor=parent_child.parent_executor,
            parent_run_id=parent_child.parent_run_id,
            child_phase=parent_child.child_phase,
            child_action=parent_child.child_action,
            request_id=parent_child.request_id,
            child_executor=parent_child.child_executor,
            observed_launch_path=parent_child.observed_launch_path,
            child_artifact_root=parent_child.child_artifact_root,
            child_worktree_root=parent_child.child_worktree_root,
            child_closeout_result=_delegated_child_closeout_result(
                decision=decision,
                terminal_summary=terminal_summary,
                dry_run=dry_run,
            ),
        )
        launch_metadata = merge_launch_metadata(
            artifacts["metadata"],
            {
                "parent_child": parent_child.to_json(),
                "terminal_summary": terminal_summary,
            },
        )
        return {
            "decision": decision.to_json(),
            "result": None,
            "artifacts": {key: str(value) for key, value in artifacts.items()},
            "launch_metadata": launch_metadata,
            "terminal_summary": terminal_summary,
        }
    result = launch_with_spec(
        spec,
        dry_run=dry_run,
        log_path=artifacts["log"],
        heartbeat_path=artifacts["heartbeat"],
        stream_output=stream_output,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        quiet_warning_seconds=quiet_warning_seconds,
        quiet_blocker_seconds=quiet_blocker_seconds,
    )
    terminal_status = "planned" if dry_run else ("unknown" if result.failed else "executed")
    terminal_summary = _persist_terminal_summary(
        artifacts,
        build_terminal_summary(
            terminal_status=terminal_status,
            terminal_blocker=None if not result.failed else {"human_required": False, "blocker_summary": "Delegated child launch failed."},
            verification_status="failed" if result.failed else ("not_run" if not dry_run else "passed"),
            next_action=(
                "Inspect the delegated child log and lineage metadata before retrying."
                if result.failed
                else "Inspect the delegated child artifacts through the runner metadata."
            ),
            artifact_paths={key: str(value) for key, value in artifacts.items()},
        ),
    )
    terminal_summary = _attach_delegation_metric(
        repo=repo,
        phase=parent_phase.upper(),
        action=request.product_action,
        target_executor=decision.selected_executor or request.target_executor,
        artifacts=artifacts,
        result=result,
        terminal_summary=terminal_summary,
    )
    child_automation = _parsed_child_automation(result, spec)
    parent_child = ParentChildRunMetadata(
        parent_phase=parent_child.parent_phase,
        parent_action=parent_child.parent_action,
        parent_executor=parent_child.parent_executor,
        parent_run_id=parent_child.parent_run_id,
        child_phase=parent_child.child_phase,
        child_action=parent_child.child_action,
        request_id=parent_child.request_id,
        child_executor=parent_child.child_executor,
        observed_launch_path=parent_child.observed_launch_path,
        child_artifact_root=parent_child.child_artifact_root,
        child_worktree_root=parent_child.child_worktree_root,
        child_closeout_result=_delegated_child_closeout_result(
            decision=decision,
            terminal_summary=terminal_summary,
            child_automation=child_automation,
            dry_run=dry_run,
            launch_failed=result.failed,
        ),
    )
    launch_metadata = merge_launch_metadata(
        artifacts["metadata"],
        {
            "parent_child": parent_child.to_json(),
            "terminal_summary": terminal_summary,
        },
    )
    return {
        "decision": decision.to_json(),
        "result": result.event_metadata(),
        "artifacts": {key: str(value) for key, value in artifacts.items()},
        "launch_metadata": launch_metadata,
        "terminal_summary": terminal_summary,
    }


def _launch_ready_lane_wave(
    *,
    repo: Path,
    roadmap: Path,
    plan: Path,
    phase: str,
    mode: str,
    action: str,
    selection,
    dry_run: bool,
) -> dict[str, object]:
    from .plan_ir import parse_phase_plan_ir

    metadata = {
        "lane_scheduler": {
            "mode": mode,
            "dry_run": dry_run,
        }
    }
    execution_diagnostic = pipeline_execution_plan_diagnostic(repo, plan, phase=phase, roadmap=roadmap)
    if execution_diagnostic is not None:
        blocker = pipeline_execution_blocker(execution_diagnostic)
        metadata["pipeline_execution_preflight"] = {
            "status": "blocked",
            "diagnostic": execution_diagnostic.to_json(),
            "plan": str(plan),
            "lane_scheduler_mode": mode,
        }
        metadata["terminal_summary"] = _pipeline_blocked_terminal_summary(
            repo=repo,
            roadmap=roadmap,
            plan=plan,
            phase=phase,
            blocker=blocker,
            diagnostic=execution_diagnostic,
            next_action="Repair or replan the Pipeline-aware phase before launching scheduler lane work.",
        )
        return {
            "phase_status": "blocked",
            "event": LoopEvent(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phase=phase,
                action=action,
                status="blocked",
                model=selection.model,
                reasoning_effort=selection.effort,
                source=selection.source,
                override_reason=selection.override_reason,
                blocker=blocker,
                metadata=metadata,
                **event_provenance(roadmap, phase),
            ),
        }
    ir = parse_phase_plan_ir(plan)
    bundle = load_execution_phase_source_bundle(repo, plan, phase=phase, roadmap=roadmap)
    work_units = load_work_unit_state(repo)
    base_sha = _current_head(repo)
    assignments = worktree_assignments_for_wave(repo, ir.lanes, branch=_current_branch(repo), mode=mode, base_sha=base_sha)
    decision = select_ready_lane_wave(ir, work_units, mode=mode, assignments=assignments, expected_base_sha=base_sha)
    metadata["lane_scheduler"]["decision"] = decision.to_json()
    if decision.status == "ready" and decision.ready_wave is not None:
        launched: list[dict[str, object]] = []
        lane_by_id = {lane.lane_id: lane for lane in ir.lanes}
        assignment_by_lane = {assignment.lane_id: assignment for assignment in decision.ready_wave.assignments}
        stopped = False
        for lane_id in decision.ready_wave.lane_ids:
            if stop_requested(repo):
                stopped = True
                break
            lane = lane_by_id[lane_id]
            kind = "phase_reducer" if lane.reducer_kind != "none" or lane.read_only else "lane_execute"
            lane_executor = default_executor_for_work_unit(kind, scheduler_assigned=True)
            assignment = assignment_by_lane.get(lane_id)
            identity = WorkUnitIdentity(
                phase=phase.upper(),
                kind=kind,
                lane_id=lane_id,
                attempt=_next_work_unit_attempt(work_units, phase.upper(), kind, lane_id),
            )
            state = launch_work_unit_attempt(
                repo,
                roadmap,
                plan,
                identity,
                policy={
                    "source": "lane_scheduler",
                    "mode": mode,
                    "wave_id": decision.ready_wave.wave_id,
                    "dry_run": dry_run,
                    "executor": lane_executor,
                    "model": selection.model,
                    "effort": selection.effort,
                    "work_unit_kind": kind,
                    **({"worktree_assignment": assignment.to_json()} if assignment else {}),
                    **({"pipeline_source_bundle": bundle.to_json()} if bundle is not None else {}),
                },
                artifacts={},
            )
            work_units[state.work_unit_id] = state
            launched.append(state.to_json())
        metadata["lane_scheduler"]["launched_work_units"] = launched
        if stopped:
            metadata["lane_scheduler"]["stop_requested"] = True
        metadata["terminal_summary"] = build_terminal_summary(
            terminal_status="blocked" if stopped else "executing",
            terminal_blocker=None,
            verification_status="blocked" if stopped else "not_run",
            next_action=(
                f"Stop file interrupted lane scheduler wave {decision.ready_wave.wave_id}; inspect launched work units before retrying."
                if stopped
                else f"Execute lane scheduler wave {decision.ready_wave.wave_id}."
            ),
        )
        return {
            "phase_status": "blocked" if stopped else "executing",
            "event": LoopEvent(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phase=phase,
                action=action,
                status="blocked" if stopped else "executing",
                model=selection.model,
                reasoning_effort=selection.effort,
                source=selection.source,
                override_reason=selection.override_reason,
                metadata=metadata,
                **event_provenance(roadmap, phase),
            ),
        }
    if decision.status == "empty":
        metadata["terminal_summary"] = build_terminal_summary(
            terminal_status="complete",
            terminal_blocker=None,
            verification_status="passed",
            next_action="All scheduler lanes are complete; run phase closeout or reducer verification.",
        )
        return {
            "phase_status": "awaiting_phase_closeout",
            "event": LoopEvent(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phase=phase,
                action=action,
                status="awaiting_phase_closeout",
                model=selection.model,
                reasoning_effort=selection.effort,
                source=selection.source,
                override_reason=selection.override_reason,
                metadata=metadata,
                **event_provenance(roadmap, phase),
            ),
        }
    blocker = {
        "human_required": False,
        "blocker_class": "contract_bug",
        "blocker_summary": "Lane scheduler could not select a safe ready wave.",
        "required_human_inputs": (),
    }
    metadata["terminal_summary"] = build_terminal_summary(
        terminal_status="blocked",
        terminal_blocker=blocker,
        verification_status="blocked",
        next_action="Inspect lane_scheduler decision diagnostics before retrying.",
    )
    return {
        "phase_status": "blocked",
        "event": LoopEvent(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phase=phase,
            action=action,
            status="blocked",
            model=selection.model,
            reasoning_effort=selection.effort,
            source=selection.source,
            override_reason=selection.override_reason,
            blocker=blocker,
            metadata=metadata,
            **event_provenance(roadmap, phase),
        ),
    }


def select_next_work_unit(repo: Path, plan: Path, phase: str) -> WorkUnitState | None:
    from .plan_ir import parse_phase_plan_ir

    existing = load_work_unit_state(repo)
    ir = parse_phase_plan_ir(plan)
    if ir.diagnostics:
        return None
    by_lane = {
        state.identity.lane_id: state
        for state in existing.values()
        if state.identity.phase == phase.upper() and state.identity.kind in {"lane_execute", "phase_reducer"}
    }
    for lane in ir.lanes:
        state = by_lane.get(lane.lane_id)
        if state is not None and state.status in {"complete", "skipped"}:
            continue
        if state is not None and state.status == "blocked" and state.human_required:
            return None
        if any(by_lane.get(dep) is None or by_lane[dep].status != "complete" for dep in lane.depends_on):
            continue
        kind = "phase_reducer" if lane.reducer_kind != "none" or not lane.owned_files else "lane_execute"
        attempt = _next_work_unit_attempt(existing, phase.upper(), kind, lane.lane_id)
        return WorkUnitState(
            identity=WorkUnitIdentity(phase=phase.upper(), kind=kind, lane_id=lane.lane_id, attempt=attempt),
            status="pending",
            policy=lane.execution_policy.to_json() if lane.execution_policy else {},
        )
    return None


def select_review_work_units(repo: Path, plan: Path, phase: str) -> tuple[WorkUnitState, ...]:
    from .discovery import execution_policy_for_action
    from .plan_ir import parse_phase_plan_ir

    existing = load_work_unit_state(repo)
    ir = parse_phase_plan_ir(plan)
    if ir.diagnostics or ir.execution_policy is None:
        return ()
    review_policy = execution_policy_for_action(ir.execution_policy, "review")
    if review_policy is None or review_policy.work_unit_kind != "lane_review":
        return ()

    implementation_states = {
        state.identity.lane_id: state
        for state in existing.values()
        if state.identity.phase == phase.upper() and state.identity.kind == "lane_execute"
    }
    review_states = {
        state.identity.lane_id: state
        for state in existing.values()
        if state.identity.phase == phase.upper() and state.identity.kind == "lane_review"
    }
    selected: list[WorkUnitState] = []
    for lane in ir.lanes:
        if not lane.owned_files:
            continue
        implementation = implementation_states.get(lane.lane_id)
        if implementation is None or implementation.status != "complete":
            continue
        review = review_states.get(lane.lane_id)
        if review is not None and review.status in {"complete", "skipped", "running"}:
            continue
        attempt = _next_work_unit_attempt(existing, phase.upper(), "lane_review", lane.lane_id)
        selected.append(
            WorkUnitState(
                identity=WorkUnitIdentity(phase=phase.upper(), kind="lane_review", lane_id=lane.lane_id, attempt=attempt),
                status="pending",
                policy=review_policy.to_json(),
            )
        )
    return tuple(selected)


def select_phase_reducer_work_unit(repo: Path, plan: Path, phase: str) -> WorkUnitState | None:
    from .plan_ir import parse_phase_plan_ir

    existing = load_work_unit_state(repo)
    ir = parse_phase_plan_ir(plan)
    if ir.diagnostics:
        return None
    states_by_lane_kind = {
        (state.identity.lane_id, state.identity.kind): state
        for state in existing.values()
        if state.identity.phase == phase.upper()
    }
    for lane in ir.lanes:
        if lane.reducer_kind == "none" and lane.owned_files:
            continue
        reducer = states_by_lane_kind.get((lane.lane_id, "phase_reducer"))
        if reducer is not None and reducer.status in {"complete", "skipped", "running"}:
            continue
        blocked_review = any(
            state.identity.kind == "lane_review" and state.status == "blocked"
            for state in existing.values()
            if state.identity.phase == phase.upper()
        )
        if blocked_review:
            return None
        producers_ready = all(
            (states_by_lane_kind.get((dependency, "lane_execute")) is not None)
            and states_by_lane_kind[(dependency, "lane_execute")].status == "complete"
            for dependency in lane.depends_on
        )
        if not producers_ready:
            continue
        reviews_ready = all(
            states_by_lane_kind.get((producer.lane_id, "lane_review")) is None
            or states_by_lane_kind[(producer.lane_id, "lane_review")].status in {"complete", "skipped"}
            for producer in ir.lanes
            if producer.owned_files and producer.lane_id in lane.depends_on
        )
        if not reviews_ready:
            continue
        attempt = _next_work_unit_attempt(existing, phase.upper(), "phase_reducer", lane.lane_id)
        return WorkUnitState(
            identity=WorkUnitIdentity(phase=phase.upper(), kind="phase_reducer", lane_id=lane.lane_id, attempt=attempt),
            status="pending",
            policy=lane.execution_policy.to_json() if lane.execution_policy else {},
        )
    return None


def launch_work_unit_attempt(
    repo: Path,
    roadmap: Path,
    plan: Path,
    identity: WorkUnitIdentity,
    *,
    policy: dict[str, object] | None = None,
    artifacts: dict[str, str] | None = None,
    retry_of: str | None = None,
) -> WorkUnitState:
    artifact_paths = dict(artifacts or {})
    state = WorkUnitState(
        identity=identity,
        status="running",
        created_at=utc_now(),
        updated_at=utc_now(),
        policy=dict(policy or {}),
        artifacts=artifact_paths,
        heartbeat_path=artifact_paths.get("heartbeat"),
        terminal_summary_path=artifact_paths.get("terminal"),
        retry_of=retry_of,
    )
    write_work_unit_state(repo, state, roadmap=roadmap)
    append_work_unit_event(
        repo,
        WorkUnitEventMetadata(
            identity=identity,
            status="running",
            event_type="launch",
            launch_metadata={"plan": str(plan), "policy": dict(policy or {})},
            heartbeat_path=state.heartbeat_path,
            terminal_summary_path=state.terminal_summary_path,
            retry_of=retry_of,
        ),
        roadmap=roadmap,
    )
    return state


def launch_harness_lane_work_unit(
    *,
    repo: Path,
    roadmap: Path,
    plan: Path,
    assignment: HarnessLaneAssignment,
    executor: str = "codex",
    action: str = "execute",
    dry_run: bool = True,
    json_output: bool = False,
    bypass_approvals: bool = False,
    command_adapter: CommandAdapterConfig | None = None,
) -> dict[str, object]:
    diagnostic = pipeline_execution_plan_diagnostic(repo, plan, phase=assignment.phase, roadmap=roadmap)
    if diagnostic is not None:
        blocker = pipeline_execution_blocker(diagnostic)
        terminal_summary = build_terminal_summary(
            terminal_status="blocked",
            terminal_blocker=blocker,
            verification_status="blocked",
            next_action="Repair or replan the Pipeline-aware phase before launching harness lane work.",
        )
        terminal_summary = _attach_phase_loop_closeout(
            repo=repo,
            roadmap=roadmap,
            plan=plan,
            phase=assignment.phase,
            terminal_summary=terminal_summary,
            blocker=blocker,
            pipeline_diagnostic=diagnostic,
        )
        return {
            "request": {},
            "spec": {},
            "result": None,
            "state": None,
            "terminal_summary": terminal_summary,
            "artifacts": {},
            "pipeline_execution_preflight": {
                "status": "blocked",
                "diagnostic": diagnostic.to_json(),
                "plan": str(plan),
            },
        }
    bundle = load_execution_phase_source_bundle(repo, plan, phase=assignment.phase, roadmap=roadmap)
    if bundle is not None:
        assignment = replace(
            assignment,
            metadata={**assignment.metadata, "pipeline_source_bundle": bundle.to_json()},
        )
    selection = resolve_profile_for_executor(action=action, executor=executor)
    prompt_bundle = build_prompt(
        action,
        roadmap=roadmap,
        phase=assignment.phase,
        plan=plan,
        harness_target=executor,
        injection_mode_override="context_file" if executor == "command" else None,
        harness_lane_assignment=assignment,
    )
    request = build_launch_request(
        executor=executor,
        action=action,
        repo=repo,
        roadmap=roadmap,
        phase=assignment.phase,
        plan=plan,
        model_selection=selection,
        prompt_bundle=prompt_bundle,
        command_adapter=command_adapter,
        harness_lane_assignment=assignment,
        json_output=json_output,
        bypass_approvals=bypass_approvals,
    )
    spec = build_launch_spec(request)
    artifacts = run_artifacts(repo, assignment.phase, action, 1, spec)
    identity = WorkUnitIdentity(
        phase=assignment.phase.upper(),
        kind=assignment.work_unit_kind,
        lane_id=assignment.lane_id,
        attempt=_next_work_unit_attempt(load_work_unit_state(repo), assignment.phase.upper(), assignment.work_unit_kind, assignment.lane_id),
    )
    state = launch_work_unit_attempt(
        repo,
        roadmap,
        plan,
        identity,
        policy={"executor": executor, "work_unit_kind": assignment.work_unit_kind, "harness_lane_assignment": assignment.to_json()},
        artifacts={key: str(value) for key, value in artifacts.items()},
    )
    result = launch_with_spec(spec, dry_run=dry_run, log_path=artifacts.get("log"))
    terminal_summary = build_terminal_summary(
        terminal_status="complete" if dry_run else "executing",
        terminal_blocker=None,
        verification_status="passed" if dry_run else "not_run",
        next_action="Dry-run fake harness lane launch recorded." if dry_run else "Await harness lane closeout.",
        artifact_paths={key: str(value) for key, value in artifacts.items()},
        work_unit=state.to_json(),
    )
    terminal_summary = _attach_phase_loop_closeout(
        repo=repo,
        roadmap=roadmap,
        plan=plan,
        phase=assignment.phase,
        terminal_summary=terminal_summary,
        changed_paths=assignment.owned_files,
    )
    write_terminal_summary(artifacts.get("terminal"), terminal_summary)
    metric = build_work_unit_metric(
        repo=repo,
        phase=assignment.phase,
        action=action,
        launch_metadata=merge_launch_metadata(artifacts.get("metadata"), {"harness_lane_assignment": assignment.to_json()}),
        launch_result=result,
        terminal_summary=terminal_summary,
        artifact_paths={key: str(value) for key, value in artifacts.items()},
        lane_id=assignment.lane_id,
    )
    append_work_unit_metric(repo, metric)
    terminal_summary = {**terminal_summary, "metric_id": metric.metric_id}
    write_terminal_summary(artifacts.get("terminal"), terminal_summary)
    merge_launch_metadata(
        artifacts.get("metadata"),
        {
            "metric_id": metric.metric_id,
            "metrics_artifact": str(phase_loop_metrics_path(repo)),
            "terminal_summary": terminal_summary,
        },
    )
    return {
        "request": request.to_json(),
        "spec": spec.to_json(),
        "result": result.event_metadata(),
        "state": state.to_json(),
        "terminal_summary": terminal_summary,
        "artifacts": {key: str(value) for key, value in artifacts.items()},
    }


def reduce_harness_lane_closeout(
    repo: Path,
    roadmap: Path,
    assignment: HarnessLaneAssignment,
    *,
    status: str = "complete",
    verification_status: str = "passed",
) -> WorkUnitState:
    identity = WorkUnitIdentity(phase=assignment.phase.upper(), kind=assignment.work_unit_kind, lane_id=assignment.lane_id, attempt=1)
    return record_work_unit_closeout(
        repo,
        roadmap,
        WorkUnitCloseout(
            identity=identity,
            status=status,
            closeout_summary={
                "verification_status": verification_status,
                "harness_lane_assignment": assignment.to_json(),
            },
        ),
    )


def record_work_unit_closeout(repo: Path, roadmap: Path, closeout: WorkUnitCloseout) -> WorkUnitState:
    closeout_summary = closeout.closeout_summary or closeout.automation
    plan_path = _plan_from_work_unit_state(repo, closeout, roadmap)
    if plan_path is not None:
        closeout_summary = dict(closeout_summary)
        terminal_summary = closeout.terminal_summary or closeout_summary.get("terminal_summary") or {}
        attached = _attach_phase_loop_closeout(
            repo=repo,
            roadmap=roadmap,
            plan=plan_path,
            phase=closeout.identity.phase,
            terminal_summary=dict(terminal_summary) if isinstance(terminal_summary, dict) else {},
            automation=closeout.automation,
            blocker=closeout_summary.get("blocker") if isinstance(closeout_summary.get("blocker"), dict) else None,
            work_unit_closeout=closeout,
        )
        if attached.get("phase_loop_closeout"):
            closeout_summary["phase_loop_closeout"] = attached["phase_loop_closeout"]
    existing = load_work_unit_state(repo).get(closeout.identity.work_unit_id)
    state = WorkUnitState(
        identity=closeout.identity,
        status=closeout.status,
        created_at=existing.created_at if existing else utc_now(),
        updated_at=utc_now(),
        parent_phase_event_id=existing.parent_phase_event_id if existing else None,
        policy=existing.policy if existing else {},
        artifacts=existing.artifacts if existing else {},
        heartbeat_path=existing.heartbeat_path if existing else None,
        terminal_summary_path=existing.terminal_summary_path if existing else None,
        closeout_summary=closeout_summary,
        retry_of=existing.retry_of if existing else None,
        superseded_by=existing.superseded_by if existing else None,
        blocker=(
            {
                "human_required": closeout.human_required,
                "blocker_class": closeout.blocker_class,
                "blocker_summary": closeout.blocker_summary,
                "required_human_inputs": closeout.required_human_inputs,
            }
            if closeout.human_required or closeout.blocker_class or closeout.blocker_summary
            else None
        ),
        human_required=closeout.human_required,
    )
    write_work_unit_state(repo, state, roadmap=roadmap)
    append_work_unit_event(
        repo,
        WorkUnitEventMetadata(
            identity=closeout.identity,
            status=closeout.status,
            event_type="closeout",
            closeout_summary=closeout_summary,
            blocker=state.blocker,
        ),
        roadmap=roadmap,
    )
    return state


def supersede_work_unit_attempt(
    repo: Path,
    roadmap: Path,
    state: WorkUnitState,
    next_identity: WorkUnitIdentity,
) -> WorkUnitState:
    superseded = state.with_status("superseded", superseded_by=next_identity.work_unit_id)
    write_work_unit_state(repo, superseded, roadmap=roadmap)
    append_work_unit_event(
        repo,
        WorkUnitEventMetadata(
            identity=state.identity,
            status="superseded",
            event_type="supersede",
            superseded_by=next_identity.work_unit_id,
        ),
        roadmap=roadmap,
    )
    return superseded


def resume_work_units(
    repo: Path,
    roadmap: Path,
    plan: Path,
    phase: str,
    *,
    stale_heartbeat_seconds: int = 1800,
) -> WorkUnitState | None:
    existing = load_work_unit_state(repo)
    active = [
        state
        for state in existing.values()
        if state.identity.phase == phase.upper() and state.status in {"running", "blocked", "awaiting-closeout"}
    ]
    human_blocker = next((state for state in active if state.status == "blocked" and state.human_required), None)
    if human_blocker is not None:
        return human_blocker
    for state in sorted(active, key=lambda item: item.updated_at, reverse=True):
        if state.status == "running" and not _work_unit_heartbeat_is_stale(state, stale_heartbeat_seconds):
            return state
        if state.status in {"running", "blocked"}:
            next_identity = WorkUnitIdentity(
                phase=state.identity.phase,
                kind=state.identity.kind,
                lane_id=state.identity.lane_id,
                attempt=_next_work_unit_attempt(existing, state.identity.phase, state.identity.kind, state.identity.lane_id),
            )
            supersede_work_unit_attempt(repo, roadmap, state, next_identity)
            return launch_work_unit_attempt(
                repo,
                roadmap,
                plan,
                next_identity,
                policy=state.policy,
                artifacts=state.artifacts,
                retry_of=state.work_unit_id,
            )
    return select_next_work_unit(repo, plan, phase)


def _next_work_unit_attempt(existing: dict[str, WorkUnitState], phase: str, kind: str, lane_id: str) -> int:
    attempts = [
        state.identity.attempt
        for state in existing.values()
        if state.identity.phase == phase and state.identity.kind == kind and state.identity.lane_id == lane_id
    ]
    return (max(attempts) + 1) if attempts else 1


def _work_unit_heartbeat_is_stale(state: WorkUnitState, stale_heartbeat_seconds: int) -> bool:
    if not state.heartbeat_path:
        return True
    heartbeat = Path(state.heartbeat_path)
    if not heartbeat.exists():
        return True
    try:
        mtime = heartbeat.stat().st_mtime
    except OSError:
        return True
    import time

    return (time.time() - mtime) >= stale_heartbeat_seconds


def detect_stuck_loop(
    repo: Path,
    roadmap: Path,
    alias: str,
    max_iterations: int = 5,
    max_minutes: int = 30,
) -> dict[str, object] | None:
    """Detect a stuck plan↔execute ping-pong loop for the named phase.

    Reads the events.jsonl ledger for the named alias and looks for the
    pattern: repeated `(action=run, status=executing)` events without any
    intervening `complete` or `blocked` terminal status, AND either:

    - iteration count exceeds `max_iterations`, OR
    - elapsed time between the first such event and now exceeds
      `max_minutes`.

    Returns a metadata dict (suitable for embedding in a blocker payload)
    when stuck-loop detected, or None when the phase is progressing normally.
    """
    from datetime import datetime, timezone, timedelta

    # Read recent events for this alias
    executing_events: list[dict[str, object]] = []
    for event in read_events(repo):
        if event.get("phase") != alias:
            continue
        if Path(str(event.get("roadmap", ""))).resolve() != roadmap.resolve():
            continue
        action = event.get("action")
        status = event.get("status")
        # Any complete/blocked terminal resets the streak
        if status in {"complete", "blocked"}:
            executing_events.clear()
            continue
        # Only count run/executing events as part of the stuck streak
        if action == "run" and status == "executing":
            executing_events.append(event)

    if len(executing_events) < max_iterations:
        # Also check time-based ceiling
        if not executing_events:
            return None
        try:
            first_ts = datetime.fromisoformat(str(executing_events[0].get("timestamp", "")).rstrip("Z").replace("Z", "+00:00"))
            if first_ts.tzinfo is None:
                first_ts = first_ts.replace(tzinfo=timezone.utc)
            elapsed = datetime.now(timezone.utc) - first_ts
            if elapsed < timedelta(minutes=max_minutes):
                return None
            trigger = "time_ceiling"
            elapsed_minutes = int(elapsed.total_seconds() // 60)
        except (ValueError, TypeError):
            return None
    else:
        trigger = "iteration_cap"
        try:
            first_ts = datetime.fromisoformat(str(executing_events[0].get("timestamp", "")).rstrip("Z").replace("Z", "+00:00"))
            if first_ts.tzinfo is None:
                first_ts = first_ts.replace(tzinfo=timezone.utc)
            elapsed_minutes = int((datetime.now(timezone.utc) - first_ts).total_seconds() // 60)
        except (ValueError, TypeError):
            elapsed_minutes = -1

    return {
        "trigger": trigger,
        "iteration_count": len(executing_events),
        "iteration_cap": max_iterations,
        "elapsed_minutes": elapsed_minutes,
        "minutes_ceiling": max_minutes,
        "first_executing_timestamp": str(executing_events[0].get("timestamp", "")),
        "latest_executing_timestamp": str(executing_events[-1].get("timestamp", "")),
        "phase": alias,
    }


def _select_ready_phase(repo: Path, roadmap: Path, classifications: dict[str, str], phase: str | None = None) -> str | None:
    phases = [p.upper() for p in parse_roadmap_phases(roadmap)]
    if phase:
        return phase.upper()
    blocked = next((p for p in phases if classifications.get(p) == "blocked"), None)
    if blocked:
        return blocked
    awaiting_closeout = next((p for p in phases if classifications.get(p) == "awaiting_phase_closeout"), None)
    if awaiting_closeout:
        return awaiting_closeout
    return next((p for p in phases if classifications.get(p) != "complete"), None)


def _select_parallel_dispatch_phase(waves: tuple[tuple[str, ...], ...], classifications: dict[str, str]) -> str | None:
    for wave in waves:
        if any(classifications.get(alias) not in {"complete", "blocked"} for alias in wave):
            return next(
                (
                    alias
                    for alias in wave
                    if classifications.get(alias) != "complete" and classifications.get(alias) != "blocked"
                ),
                None,
            )
        if any(classifications.get(alias) == "blocked" for alias in wave):
            return None
    return None


def _coordinator_wave_for_alias(waves: tuple[tuple[str, ...], ...], alias: str) -> tuple[int, tuple[str, ...]] | None:
    for index, wave in enumerate(waves):
        if alias in wave:
            return index, wave
    return None


def _parallel_wave_terminal(wave: tuple[str, ...], classifications: dict[str, str]) -> bool:
    return all(classifications.get(alias) in {"complete", "blocked"} for alias in wave)


def _append_coordinator_event(
    *,
    repo: Path,
    roadmap: Path,
    phase: str,
    action: str,
    status: str,
    selection,
    metadata: dict[str, object],
) -> None:
    append_event(
        repo,
        LoopEvent(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phase=phase,
            action=action,
            status=status if status in PHASE_STATUSES else "unknown",
            model=selection.model,
            reasoning_effort=selection.effort,
            source=selection.source,
            override_reason=selection.override_reason,
            metadata={"coordinator": metadata},
            **event_provenance(roadmap, phase),
        ),
    )


def _launch_event_metadata(
    result: LaunchResult,
    artifacts: dict[str, Path],
    *,
    request,
    spec,
    status_after_launch: str,
    event_blocker: dict | None,
    child_automation: dict[str, object],
    completion_dirty_paths: list[str],
    plan_dirty_paths: list[str],
    incomplete_execute_dirty_paths: list[str],
    dirty_summary: dict[str, object],
    missing_plan_after_planning: dict[str, object] | None = None,
    execution_policy: dict[str, object] | None = None,
) -> dict:
    artifact_paths = {key: str(value) for key, value in artifacts.items()} if artifacts else {}
    metadata = {
        "launch": result.event_metadata(),
        "launch_request": request.to_json(),
        "launch_spec": spec.to_json(),
    }
    if child_automation:
        metadata["child_automation"] = child_automation
    if request.dispatch_decision is not None:
        metadata["dispatch_decision"] = request.dispatch_decision.to_json()
        metadata["dispatch_summary"] = describe_dispatch_decision(request.dispatch_decision)
    if execution_policy is not None:
        metadata["execution_policy"] = execution_policy
    if artifact_paths:
        metadata["artifacts"] = artifact_paths
        task_ledger = _task_ledger_event_metadata(
            artifacts,
            status_after_launch=status_after_launch,
            event_blocker=event_blocker,
        )
        if task_ledger:
            metadata["task_ledger"] = task_ledger
    if missing_plan_after_planning:
        metadata["missing_plan_after_planning"] = missing_plan_after_planning
    if completion_dirty_paths:
        metadata["completion_dirty_worktree"] = {
            "reason": "complete_status_with_dirty_worktree",
            "terminal_status": "complete",
            "dirty_paths": completion_dirty_paths,
            **dirty_summary,
        }
    if plan_dirty_paths:
        metadata["plan_dirty_worktree"] = {
            "reason": "plan_status_with_dirty_worktree",
            "terminal_status": "planned",
            "dirty_paths": plan_dirty_paths,
            **dirty_summary,
        }
    if incomplete_execute_dirty_paths:
        metadata["incomplete_execute_dirty_worktree"] = {
            "reason": "execute_status_without_completion_with_dirty_worktree",
            "terminal_status": "executed",
            "dirty_paths": incomplete_execute_dirty_paths,
            **dirty_summary,
        }
    terminal_status = "complete" if completion_dirty_paths else status_after_launch
    if plan_dirty_paths:
        terminal_status = "planned"
    if incomplete_execute_dirty_paths:
        terminal_status = "executed"
    verification_status = _terminal_verification_status(terminal_status, event_blocker)
    terminal_blocker = event_blocker
    next_action = _terminal_next_action(terminal_status, event_blocker, dirty_summary)
    child_baml_closeout = child_automation.get("native_closeout_payload") if isinstance(child_automation, dict) else None
    if not isinstance(child_baml_closeout, dict):
        child_baml_closeout = None
    extraction_failure = (
        child_automation.get("native_closeout_extraction_failure") if isinstance(child_automation, dict) else None
    )
    if not isinstance(extraction_failure, dict) and child_baml_closeout is None:
        extraction_failure = _native_closeout_extraction_failure(result, spec)
    terminal_summary = build_terminal_summary(
            terminal_status=terminal_status,
            terminal_blocker=terminal_blocker,
            verification_status=verification_status,
            next_action=next_action,
            dirty_paths=dirty_summary.get("dirty_paths", completion_dirty_paths or plan_dirty_paths or incomplete_execute_dirty_paths),
            phase_owned_dirty=bool(dirty_summary.get("phase_owned_dirty", False)),
            phase_owned_dirty_paths=dirty_summary.get("phase_owned_dirty_paths", ()),
            previous_phase_owned_paths=dirty_summary.get("previous_phase_owned_paths", ()),
            unowned_dirty_paths=dirty_summary.get("unowned_dirty_paths", ()),
            pre_existing_dirty_paths=dirty_summary.get("pre_existing_dirty_paths", ()),
            artifact_paths=artifact_paths,
            child_baml_closeout=child_baml_closeout,
            extraction_failure=extraction_failure,
        )
    terminal_summary = _attach_phase_loop_closeout(
        repo=Path(str(request.repo)),
        roadmap=Path(str(request.roadmap)),
        plan=Path(str(request.plan)) if request.plan is not None else None,
        phase=str(request.phase or ""),
        terminal_summary=terminal_summary,
        automation=child_automation,
        blocker=terminal_blocker,
        access_attempts=tuple(terminal_blocker.get("access_attempts", ())) if isinstance(terminal_blocker, dict) else (),
    )
    metadata["terminal_summary"] = _persist_terminal_summary(
        artifacts,
        terminal_summary,
        child_baml_closeout=child_baml_closeout,
        extraction_failure=extraction_failure,
    )
    metadata["terminal_summary"] = _attach_work_unit_metric(
        repo=Path(str(request.repo)),
        phase=str(request.phase or ""),
        action=str(request.action),
        artifacts=artifacts,
        request=request,
        result=result,
        terminal_summary=metadata["terminal_summary"],
    )
    metadata["launch"] = result.event_metadata()
    if metadata["terminal_summary"].get("metric_id"):
        metadata["launch"]["metric_id"] = metadata["terminal_summary"]["metric_id"]
    return metadata


def _verification_enforcement_mode() -> str:
    value = os.environ.get("PHASE_LOOP_VERIFY_ENFORCE", "warn").strip().lower()
    return "hard" if value == "hard" else "warn"


def _execute_verification_preflight_blocker(repo: Path, roadmap: Path, plan: Path) -> dict[str, object] | None:
    if _verification_enforcement_mode() != "hard":
        return None
    findings = validate_plan_verification_commands_for_intake(repo, plan)
    if findings:
        first = findings[0]
        summary = getattr(first, "message", None) or str(first)
        return {
            "human_required": False,
            "blocker_class": "contract_bug",
            "blocker_summary": f"Plan verification command intake failed: {summary}",
            "required_human_inputs": (),
            "access_attempts": (),
        }
    suite_command, suite_findings = resolve_suite_command_doc(repo, roadmap, plan)
    if suite_findings:
        return {
            "human_required": False,
            "blocker_class": "contract_bug",
            "blocker_summary": f"Suite command declaration is invalid: {suite_findings[0].message}",
            "required_human_inputs": (),
            "access_attempts": (),
        }
    if suite_command is None:
        return {
            "human_required": False,
            "blocker_class": "verification_evidence_missing",
            "blocker_summary": "Execute launch requires automation.suite_command when PHASE_LOOP_VERIFY_ENFORCE=hard.",
            "required_human_inputs": (),
            "access_attempts": (),
        }
    return None


def _run_execute_verification(
    *,
    repo: Path,
    roadmap: Path,
    plan: Path,
    artifacts: dict[str, Path],
) -> dict[str, object]:
    run_dir = artifacts.get("root")
    if run_dir is None:
        return {
            "ok": False,
            "code": "missing_run_dir",
            "blocker_summary": "Runner-owned verification requires an observed run directory.",
        }
    commands, operational_exemptions = verification_commands_from_plan(plan)
    suite_command, suite_findings = resolve_suite_command_doc(repo, roadmap, plan)
    if suite_findings:
        return {
            "ok": False,
            "code": suite_findings[0].code,
            "blocker_summary": suite_findings[0].message,
            "suite_command": None,
            "operational_exemptions": operational_exemptions,
        }
    manifests = detect_changed_dependency_manifests(repo, "HEAD")
    install_argv = resolve_install_command(repo, manifests) if manifests else None
    env_refresh = (
        {"triggered": True, "manifests": manifests, "install_argv": install_argv or [], "exit_code": 127}
        if manifests and install_argv is None
        else ({"triggered": True, "manifests": manifests, "install_argv": install_argv} if manifests else None)
    )
    result = run_verification(
        repo,
        run_dir,
        commands,
        suite_command,
        env_refresh,
        float(os.environ.get("PHASE_LOOP_VERIFY_TIMEOUT_SECONDS", "1200")),
        operational_exemptions=operational_exemptions,
    )
    artifact_path = run_dir / VERIFICATION_ARTIFACT_NAME
    validation = validate_verification_artifact(artifact_path)
    validation_json = validation.to_json()
    summary = {
        "ok": validation.ok,
        "code": validation.code,
        "verification_artifact_path": str(artifact_path),
        "verification_log_path": str(run_dir / VERIFICATION_LOG_NAME),
        "suite_command": suite_command,
        "env_refresh": env_refresh,
        "verification_exit_summary": validation_json.get("exit_summary", {}),
        "operational_exemptions": operational_exemptions,
        "validation": validation_json,
        "run_id": result.run_id,
    }
    if not validation.ok:
        summary["blocker_summary"] = f"Runner-owned verification failed: {validation.code}"
    return summary


def _executor_closeout_event(
    *,
    repo: Path,
    roadmap: Path,
    phase: str,
    selection,
    spec,
    dispatch_decision: DispatchDecision,
    child_automation: dict[str, object],
) -> LoopEvent | None:
    payload = child_automation.get("native_closeout_payload")
    if not isinstance(payload, dict):
        return None
    if child_automation.get("automation_parse_error"):
        return None
    validation = child_automation.get("produced_gates_validation")
    if isinstance(validation, dict) and validation.get("ok") is False:
        return None
    source_status = _phase_status_literal(payload.get("terminal_status"))
    if source_status is None:
        return None
    metadata = {
        "executor_closeout_event": {
            "source_status": source_status,
            "verification_status": payload.get("verification_status"),
            "produced_if_gates": list(payload.get("produced_if_gates") or ()),
            "dirty_paths": list(payload.get("dirty_paths") or ()),
        },
        "child_automation": child_automation,
    }
    return LoopEvent(
        timestamp=utc_now(),
        repo=str(repo),
        roadmap=str(roadmap),
        phase=phase,
        # DEF-4: keep executor-terminal closeouts distinct from runner-classified run events.
        action="executor.closeout",
        status=source_status,
        model=selection.model,
        reasoning_effort=selection.effort,
        source=selection.source,
        override_reason=selection.override_reason,
        command=metadata_command(spec.command, spec.prompt_bundle.render_prompt()),
        metadata=metadata,
        selected_executor=dispatch_decision.selected_executor,
        **event_provenance(roadmap, phase),
    )


def _runner_tier3_closeout_audit(
    *,
    repo: Path,
    roadmap: Path,
    phase: str,
    cli_enable_tier3: bool,
    tier3_budget: int,
    model: str,
    reasoning_effort: str,
    source: str,
) -> dict[str, object] | None:
    try:
        config = load_evidence_audit_config(repo)
    except EvidenceAuditConfigError as exc:
        return {
            "summary": {
                "tier3_enabled": False,
                "config_error": str(exc),
            },
            "blocker": {
                "human_required": False,
                "blocker_class": "contract_bug",
                "blocker_summary": f"Malformed evidence-audit config: {exc}",
                "required_human_inputs": (),
                "access_attempts": (),
            },
        }

    phase_config = config.phase_config(phase)
    excluded = config.tier3_excluded(phase)
    tier3_enabled = bool(phase_config.tier3_enabled or cli_enable_tier3)
    summary: dict[str, object] = {
        "tier2_enabled": phase_config.tier2_enabled,
        "tier3_enabled": tier3_enabled and not excluded,
        "tier3_excluded": excluded,
        "tier3_budget": max(0, int(tier3_budget)),
        "tier3_calls_made": 0,
    }
    if not phase_config.tier2_enabled or not tier3_enabled or excluded:
        return {"summary": summary}

    audit = run_tier3_runner_audit(
        repo,
        tier3_budget=max(0, int(tier3_budget)),
        confidence_threshold=phase_config.tier3_confidence_threshold,
    )
    summary.update(audit.to_json())
    for record in audit.invocations:
        _append_tier3_audit_event(
            repo=repo,
            roadmap=roadmap,
            phase=phase,
            metadata={
                **record.metadata,
                "tier3_budget": audit.tier3_budget,
                "tier3_calls_made": audit.tier3_calls_made,
            },
            model=model,
            reasoning_effort=reasoning_effort,
            source=source,
        )
    return {"summary": summary, **({"blocker": audit.blocker} if audit.blocker else {})}


def _append_tier3_audit_event(
    *,
    repo: Path,
    roadmap: Path,
    phase: str,
    metadata: dict[str, object],
    model: str,
    reasoning_effort: str,
    source: str,
) -> None:
    path = event_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": utc_now(),
        "repo": str(repo),
        "roadmap": str(roadmap),
        "phase": phase,
        "action": "evidence_audit_tier3",
        "status": "executed",
        "model": model,
        "reasoning_effort": reasoning_effort,
        "source": source,
        "metadata": metadata,
        "git_topology": collect_git_topology(repo),
        "schema_version": 2,
        **event_provenance(roadmap, phase),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _attach_work_unit_metric(
    *,
    repo: Path,
    phase: str,
    action: str,
    artifacts: dict[str, Path],
    request,
    result: LaunchResult | None,
    terminal_summary: dict[str, object],
) -> dict[str, object]:
    if not artifacts:
        return terminal_summary
    artifact_paths = {key: str(value) for key, value in artifacts.items()}
    launch_metadata = merge_launch_metadata(artifacts.get("metadata"), {})
    metric = build_work_unit_metric(
        repo=repo,
        phase=phase,
        action=action,
        launch_metadata=launch_metadata,
        launch_result=result,
        terminal_summary=terminal_summary,
        artifact_paths=artifact_paths,
    )
    append_work_unit_metric(repo, metric)
    terminal_summary = {**terminal_summary, "metric_id": metric.metric_id}
    if artifacts.get("terminal") is not None:
        write_terminal_summary(artifacts.get("terminal"), terminal_summary)
    merge_launch_metadata(
        artifacts.get("metadata"),
        {
            "metric_id": metric.metric_id,
            "metrics_artifact": str(phase_loop_metrics_path(repo)),
            "terminal_summary": terminal_summary,
        },
    )
    return terminal_summary


def _attach_delegation_metric(
    *,
    repo: Path,
    phase: str,
    action: str,
    target_executor: str,
    artifacts: dict[str, Path],
    result: LaunchResult | None,
    terminal_summary: dict[str, object],
    launch_metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    if not artifacts:
        return terminal_summary
    launch_metadata = {
        "executor": target_executor,
        **(launch_metadata or merge_launch_metadata(artifacts.get("metadata"), {})),
    }
    metric = build_work_unit_metric(
        repo=repo,
        phase=phase,
        action=action,
        launch_metadata=launch_metadata,
        launch_result=result,
        terminal_summary=terminal_summary,
        artifact_paths={key: str(value) for key, value in artifacts.items()},
    )
    append_work_unit_metric(repo, metric)
    terminal_summary = {**terminal_summary, "metric_id": metric.metric_id}
    write_terminal_summary(artifacts.get("terminal"), terminal_summary)
    merge_launch_metadata(
        artifacts.get("metadata"),
        {
            "metric_id": metric.metric_id,
            "metrics_artifact": str(phase_loop_metrics_path(repo)),
            "terminal_summary": terminal_summary,
        },
    )
    return terminal_summary


def _operator_dispatch_hints(
    *,
    action: str,
    executor: str | None,
    allowed_executors: tuple[str, ...],
    fallback_executors: tuple[str, ...],
    disabled_executors: tuple[str, ...],
    required_capabilities: tuple[str, ...],
) -> DispatchHints | None:
    hints = DispatchHints(
        preferred_executors=(executor,) if executor else (),
        allowed_executors=tuple(allowed_executors),
        fallback_executors=tuple(fallback_executors),
        disabled_executors=tuple(disabled_executors),
        required_capabilities=tuple(required_capabilities),
        source="operator",
        action=action,
    )
    return None if hints.is_empty() else hints


def _build_repair_context(
    repo: Path,
    phase: str,
    plan: Path | None,
    snapshot: StateSnapshot,
) -> tuple[dict[str, object] | None, list[str]]:
    missing: list[str] = []
    terminal_summary = snapshot.terminal_summary
    if not terminal_summary or terminal_summary.get("phase") != phase:
        missing.append("terminal_summary")
    dirty_paths = list(snapshot.dirty_paths) or list((terminal_summary or {}).get("dirty_paths", ()))
    phase_owned_dirty_paths = list(snapshot.phase_owned_dirty_paths) or list(
        (terminal_summary or {}).get("phase_owned_dirty_paths", ())
    )
    previous_phase_owned_paths = list(snapshot.previous_phase_owned_paths) or list(
        (terminal_summary or {}).get("previous_phase_owned_paths", ())
    )
    unowned_dirty_paths = list(snapshot.unowned_dirty_paths) or list((terminal_summary or {}).get("unowned_dirty_paths", ()))
    pre_existing_dirty_paths = list(snapshot.pre_existing_dirty_paths) or list(
        (terminal_summary or {}).get("pre_existing_dirty_paths", ())
    )
    phase_owned_dirty = snapshot.phase_owned_dirty or bool((terminal_summary or {}).get("phase_owned_dirty", False))
    if plan is None:
        missing.append("phase_plan")
    context = {
        "state_path": str(state_path(repo)),
        "events_path": str(event_path(repo)),
        "handoff_path": str(tui_handoff_path(repo)),
        "handoff_command": f"{_phase_loop_cli()} handoff --repo {repo}",
        "status_command": f"{_phase_loop_cli()} status --repo {repo} --json",
        "plan_path": str(plan) if plan is not None else "none",
        "terminal_summary": terminal_summary or {},
        "dirty_paths": dirty_paths,
        "phase_owned_dirty_paths": phase_owned_dirty_paths,
        "previous_phase_owned_paths": previous_phase_owned_paths,
        "unowned_dirty_paths": unowned_dirty_paths,
        "pre_existing_dirty_paths": pre_existing_dirty_paths,
        "phase_owned_dirty": phase_owned_dirty,
        "closeout_summary": snapshot.closeout_summary or {},
        "artifact_paths": _latest_phase_artifacts(repo, phase),
    }
    return (context if not missing else None), missing


def repair_precondition_for_snapshot(
    repo: Path,
    roadmap: Path,
    phase: str,
    plan: Path | None,
    snapshot: StateSnapshot,
) -> dict[str, object]:
    blocker_class = snapshot.blocker_class
    sticky_blockers = {
        "missing_secret",
        "account_or_billing_setup",
        "admin_approval",
        "product_decision_missing",
        "destructive_operation",
    }
    if snapshot.human_required or blocker_class in sticky_blockers:
        return {
            "status": "sticky",
            "reason": "sticky_blocker",
            "dirty_summary": {},
        }
    if blocker_class != "dirty_worktree_conflict":
        return {
            "status": "repair_required",
            "reason": "unsupported_live_repair_precondition",
            "dirty_summary": {},
        }

    dirty_paths = _dirty_paths(repo)
    dirty_summary = _classify_dirty_paths(repo, roadmap, plan, dirty_paths, dirty_paths, current_phase=phase) if dirty_paths else {
        "dirty_paths": [],
        "phase_owned_dirty_paths": [],
        "previous_phase_owned_paths": [],
        "expected_sibling_dirty_paths": [],
        "expected_sibling_dirty": False,
        "unowned_dirty_paths": [],
        "pre_existing_dirty_paths": [],
        "phase_owned_dirty": False,
        "ownership_errors": [],
        "rename_sources_promoted": [],
    }
    if (
        not dirty_summary.get("unowned_dirty_paths")
        and not dirty_summary.get("pre_existing_dirty_paths")
        and not dirty_summary.get("ownership_errors")
    ):
        return {
            "status": "cleared",
            "reason": "live_dirty_worktree_precondition_cleared",
            "dirty_summary": dirty_summary,
        }
    return {
        "status": "repair_required",
        "reason": "live_dirty_worktree_still_blocked",
        "dirty_summary": dirty_summary,
    }


def _recover_verified_dirty_closeout(
    repo: Path,
    roadmap: Path,
    phase: str,
    plan: Path | None,
    snapshot: StateSnapshot,
    selection,
    *,
    action: str,
) -> tuple[str, LoopEvent] | None:
    automation = _latest_verified_dirty_child_automation(repo, phase)
    if not automation:
        return None
    dirty_paths = _dirty_paths(repo)
    if not dirty_paths:
        return None
    plan_for_ownership = plan or _latest_launch_plan_path(repo, phase)
    dirty_summary = _classify_dirty_paths(repo, roadmap, plan_for_ownership, [], dirty_paths, current_phase=phase)
    status, blocker = _dirty_outcome(
        dirty_summary,
        blocked_summary="Phase reported verified dirty closeout but left dirty paths that are not closeout-safe.",
    )
    terminal_status = "complete" if status == "awaiting_phase_closeout" else "blocked"
    terminal_blocker = blocker
    metadata = {
        "verified_dirty_closeout_recovery": {
            "source": "child_automation",
            "child_automation": automation,
            "plan_path": str(plan_for_ownership) if plan_for_ownership is not None else None,
            "plan_source": "current" if plan is not None else "latest_launch",
        },
        "completion_dirty_worktree": {
            "reason": "verified_dirty_closeout_recovery",
            "terminal_status": "complete",
            "dirty_paths": dirty_paths,
            **dirty_summary,
        },
        "terminal_summary": build_terminal_summary(
            terminal_status=terminal_status,
            terminal_blocker=terminal_blocker,
            verification_status="passed" if status == "awaiting_phase_closeout" else "blocked",
            next_action=_terminal_next_action(terminal_status, terminal_blocker, dirty_summary),
            dirty_paths=dirty_summary.get("dirty_paths", dirty_paths),
            phase_owned_dirty=bool(dirty_summary.get("phase_owned_dirty", False)),
            phase_owned_dirty_paths=dirty_summary.get("phase_owned_dirty_paths", ()),
            previous_phase_owned_paths=dirty_summary.get("previous_phase_owned_paths", ()),
            unowned_dirty_paths=dirty_summary.get("unowned_dirty_paths", ()),
            pre_existing_dirty_paths=dirty_summary.get("pre_existing_dirty_paths", ()),
            artifact_paths=_latest_phase_artifacts(repo, phase),
        ),
    }
    return (
        status,
        LoopEvent(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phase=phase,
            action=action,
            status=status,
            model=selection.model,
            reasoning_effort=selection.effort,
            source=selection.source,
            override_reason=selection.override_reason,
            blocker=blocker,
            metadata=metadata,
            **event_provenance(roadmap, phase),
        ),
    )


def _latest_verified_dirty_child_automation(repo: Path, phase: str) -> dict[str, object] | None:
    for event in reversed(read_events(repo)):
        if str(event.get("phase", "")).upper() != phase.upper():
            continue
        metadata = event.get("metadata")
        if not isinstance(metadata, dict):
            continue
        automation = metadata.get("child_automation")
        if not isinstance(automation, dict):
            continue
        if (
            automation.get("automation_status") == "blocked"
            and _optional_automation_literal(automation.get("automation_blocker_class")) == "dirty_worktree_conflict"
            and automation.get("automation_verification_status") == "passed"
            and str(automation.get("automation_human_required", "")).lower() != "true"
        ):
            return dict(automation)
    return None


def _repair_completion_success(automation: dict[str, object]) -> bool:
    if _phase_status_literal(automation.get("automation_status")) != "complete":
        return False
    if automation.get("automation_verification_status") != "passed":
        return False
    if str(automation.get("automation_human_required", "")).lower() == "true":
        return False
    if _optional_automation_literal(automation.get("automation_blocker_class")):
        return False
    if _optional_automation_literal(automation.get("automation_blocker_summary")):
        return False
    return True


def _recent_repeated_repair_failures(
    repo: Path,
    phase: str,
    executor: str,
    snapshot: StateSnapshot,
    *,
    threshold: int = 2,
) -> int:
    fingerprint = _repair_failure_fingerprint(snapshot.blocker_class, snapshot.blocker_summary)
    if not fingerprint:
        return 0
    count = 0
    for event in reversed(read_events(repo)):
        if str(event.get("phase", "")).upper() != phase.upper():
            continue
        metadata = event.get("metadata")
        launch_request = metadata.get("launch_request") if isinstance(metadata, dict) else None
        if not isinstance(launch_request, dict) or launch_request.get("action") != "repair":
            continue
        if launch_request.get("executor") != executor:
            continue
        if event.get("status") != "blocked":
            break
        blocker = event.get("blocker")
        if not isinstance(blocker, dict):
            break
        if _repair_failure_fingerprint(blocker.get("blocker_class"), blocker.get("blocker_summary")) != fingerprint:
            break
        count += 1
        if count >= threshold:
            return count
    return count


def _repair_failure_fingerprint(blocker_class: object, blocker_summary: object) -> str | None:
    if not blocker_class and not blocker_summary:
        return None
    summary = re.sub(r"\s+", " ", str(blocker_summary or "")).strip().lower()
    return f"{blocker_class or 'none'}:{summary}"


def _repair_fallback_candidate(
    dispatch_decision: DispatchDecision,
    *,
    operator_fallback_executors: tuple[str, ...],
    disabled_executors: tuple[str, ...],
) -> str | None:
    selected = dispatch_decision.selected_executor
    disabled = set(disabled_executors)
    allowed = set(dispatch_decision.allowed_executors)
    for executor in (*operator_fallback_executors, *dispatch_decision.fallback_executors):
        if executor == selected or executor in disabled:
            continue
        if allowed and executor not in allowed:
            continue
        return executor
    return None


def _latest_launch_plan_path(repo: Path, phase: str) -> Path | None:
    for event in reversed(read_events(repo)):
        if str(event.get("phase", "")).upper() != phase.upper():
            continue
        metadata = event.get("metadata")
        if not isinstance(metadata, dict):
            continue
        launch_request = metadata.get("launch_request")
        if not isinstance(launch_request, dict):
            continue
        plan_value = launch_request.get("plan")
        if not isinstance(plan_value, str) or not plan_value:
            continue
        plan = Path(plan_value).expanduser()
        if not plan.is_absolute():
            plan = repo / plan
        if plan.exists():
            return plan
    return None


def _phase_loop_cli() -> Path:
    return Path("phase-loop")


def _latest_phase_artifacts(repo: Path, phase: str) -> dict[str, str]:
    for event in reversed(read_events(repo)):
        if str(event.get("phase", "")).upper() != phase.upper():
            continue
        metadata = event.get("metadata") or {}
        artifacts = metadata.get("artifacts")
        if isinstance(artifacts, dict) and artifacts:
            return {key: str(value) for key, value in artifacts.items()}
        launch = metadata.get("launch") or {}
        if isinstance(launch, dict) and launch.get("log_path"):
            log = Path(str(launch["log_path"]))
            return {
                "root": str(log.parent),
                "metadata": str(log.parent / "launch.json"),
                "log": str(log),
                "heartbeat": str(launch.get("heartbeat_path") or (log.parent / "heartbeat.json")),
                "terminal": str(launch.get("terminal_path") or (log.parent / "terminal-summary.json")),
            }
    return {}


def _launch_failure_metadata(result: LaunchResult, artifacts: dict[str, Path], *, request, spec) -> dict[str, object]:
    artifact_paths = {key: str(value) for key, value in artifacts.items()} if artifacts else {}
    payload = {
        "launch": result.event_metadata(),
        "launch_request": request.to_json(),
        "launch_spec": spec.to_json(),
        "artifacts": artifact_paths,
        "terminal_summary": _persist_terminal_summary(
            artifacts,
            build_terminal_summary(
                terminal_status="unknown",
                terminal_blocker=None,
                verification_status="failed",
                next_action="Inspect the final log and event ledger before resuming.",
                artifact_paths=artifact_paths,
            ),
        ),
    }
    payload["terminal_summary"] = _attach_work_unit_metric(
        repo=Path(str(request.repo)),
        phase=str(request.phase or ""),
        action=str(request.action),
        artifacts=artifacts,
        request=request,
        result=result,
        terminal_summary=payload["terminal_summary"],
    )
    if payload["terminal_summary"].get("metric_id"):
        payload["launch"]["metric_id"] = payload["terminal_summary"]["metric_id"]
    task_ledger = _task_ledger_event_metadata(artifacts, status_after_launch="unknown", event_blocker=None)
    if task_ledger:
        payload["task_ledger"] = task_ledger
    return payload


def _persist_terminal_summary(
    artifacts: dict[str, Path],
    summary: dict[str, object],
    child_baml_closeout: dict[str, object] | None = None,
    extraction_failure: dict[str, object] | None = None,
) -> dict[str, object]:
    summary = apply_child_terminal_summary_overlay(
        summary,
        child_baml_closeout=child_baml_closeout,
        extraction_failure=extraction_failure,
    )
    terminal_path = artifacts.get("terminal")
    if terminal_path is not None:
        write_terminal_summary(terminal_path, summary)
    metadata_path = artifacts.get("metadata")
    if metadata_path is not None:
        runtime = {
            "updated_at": utc_now(),
            "terminal_status": summary.get("terminal_status"),
            "verification_status": summary.get("verification_status"),
            "superseded": bool(summary.get("terminal_status")),
        }
        merge_launch_metadata(
            metadata_path,
            {
                "terminal_summary": summary,
                "task_ledger_runtime": runtime,
            },
        )
    return summary


def _pipeline_blocked_terminal_summary(
    *,
    repo: Path,
    roadmap: Path,
    plan: Path,
    phase: str,
    blocker: dict,
    diagnostic,
    next_action: str,
) -> dict[str, object]:
    return _attach_phase_loop_closeout(
        repo=repo,
        roadmap=roadmap,
        plan=plan,
        phase=phase,
        terminal_summary=build_terminal_summary(
            terminal_status="blocked",
            terminal_blocker=blocker,
            verification_status="blocked",
            next_action=next_action,
        ),
        blocker=blocker,
        pipeline_diagnostic=diagnostic,
    )


def _attach_phase_loop_closeout(
    *,
    repo: Path,
    roadmap: Path,
    plan: Path | None,
    phase: str,
    terminal_summary: dict[str, object],
    automation: dict[str, object] | None = None,
    blocker: dict | None = None,
    access_attempts: tuple[dict, ...] | list[dict] = (),
    changed_paths: tuple[str, ...] | list[str] = (),
    work_unit_closeout: WorkUnitCloseout | None = None,
    pipeline_diagnostic=None,
    force_closeout: bool = False,
) -> dict[str, object]:
    if plan is None:
        return terminal_summary
    try:
        metadata = parse_pipeline_plan_metadata(plan)
    except ValueError:
        return terminal_summary
    if metadata.empty and pipeline_diagnostic is None and not force_closeout and not changed_paths:
        return terminal_summary
    bundle = None if pipeline_diagnostic is not None else load_execution_phase_source_bundle(repo, plan, phase=phase, roadmap=roadmap)
    closeout = build_phase_loop_closeout(
        phase_alias=phase,
        plan_path=plan,
        source_bundle=bundle,
        plan_metadata=metadata,
        pipeline_diagnostic=pipeline_diagnostic,
        terminal_summary=terminal_summary,
        automation=automation or {},
        blocker=blocker or {},
        access_attempts=access_attempts,
        changed_paths=changed_paths,
        artifact_paths=terminal_summary.get("artifact_paths") if isinstance(terminal_summary.get("artifact_paths"), dict) else {},
        evidence_refs=terminal_summary.get("evidence_refs") if isinstance(terminal_summary.get("evidence_refs"), list) else (),
        work_unit_closeout=work_unit_closeout,
    )
    if phase_loop_closeout_diagnostic(closeout) is not None:
        return terminal_summary
    return {**terminal_summary, "phase_loop_closeout": closeout}


def _plan_from_work_unit_state(repo: Path, closeout: WorkUnitCloseout, roadmap: Path) -> Path | None:
    if isinstance(closeout.closeout_summary, dict):
        plan = closeout.closeout_summary.get("plan")
        if isinstance(plan, str) and plan:
            path = Path(plan)
            return path if path.is_absolute() else repo / path
    existing = load_work_unit_state(repo).get(closeout.identity.work_unit_id)
    if existing and isinstance(existing.policy, dict):
        plan = existing.policy.get("plan")
        if isinstance(plan, str) and plan:
            path = Path(plan)
            return path if path.is_absolute() else repo / path
    return find_plan_artifact(repo, closeout.identity.phase, roadmap=roadmap)


def _task_ledger_event_metadata(
    artifacts: dict[str, Path],
    *,
    status_after_launch: str,
    event_blocker: dict[str, object] | None,
) -> dict[str, object] | None:
    task_snapshot = artifacts.get("task_snapshot")
    hook_manifest = artifacts.get("hook_manifest")
    if task_snapshot is None and hook_manifest is None:
        return None
    return {
        "task_snapshot_path": str(task_snapshot) if task_snapshot is not None else None,
        "hook_manifest_path": str(hook_manifest) if hook_manifest is not None else None,
        "status_after_launch": status_after_launch,
        "blocked": bool(event_blocker),
    }


def _parsed_child_automation(result: LaunchResult, spec) -> dict[str, object]:
    text = extract_executor_output_text(result, spec)
    parsed = {}
    native_failure: dict[str, object] | None = None
    for source, candidate in _native_closeout_text_candidates(result, spec, text):
        native = _parse_native_closeout_status(candidate)
        if native and not native.get("automation_parse_error"):
            native["native_closeout_source"] = source
            parsed = native
            text = candidate
            native_failure = None
            break
        if native and native_failure is None:
            native_failure = {
                "reason": "malformed_native_closeout",
                "source": source,
                "classification": "native_closeout_extraction",
            }
            native["native_closeout_extraction_failure"] = native_failure
            parsed = native
            text = candidate
    if not parsed:
        parsed = parse_automation_status(text)
        native_failure = _native_closeout_extraction_failure(result, spec, text=text)
        if parsed and native_failure:
            parsed["native_closeout_extraction_failure"] = native_failure
    if text and parsed:
        parsed["raw_output_excerpt"] = text[:1000]
        delegation_request = _parse_delegation_request(text)
        if delegation_request is not None:
            parsed["delegation_request"] = delegation_request
    _annotate_automation_parse_error(parsed, _executor_display_name(spec.executor), spec.prompt_bundle.workflow_command)
    return parsed


def _parse_native_closeout_status(text: str) -> dict[str, object]:
    # Extract the closeout dict from the raw executor output (which may be
    # JSONL with many event lines before the final closeout JSON), then
    # serialize it back to JSON before handing to BAML. Passing raw multi-
    # line text to BAML causes it to fail "Failed to find any
    # PhaseLoopCloseoutV1 @stream.not_null" on intermediate event lines.
    extracted = _find_json_closeout_payload(text)
    if not extracted:
        return {}
    try:
        payload, parse_errors = parse_closeout_payload_doc(json.dumps(extracted), kind="native_closeout")
    except BamlValidationError as exc:
        return {
            "automation_status": "blocked",
            "automation_next_skill": "codex-plan-phase",
            "automation_next_command": "none",
            "automation_human_required": "false",
            "automation_blocker_class": "contract_bug",
            "automation_blocker_summary": f"BAML closeout validation failed: {exc}",
            "automation_required_human_inputs": [],
            "automation_verification_status": "blocked",
            "automation_parse_error": f"BAML closeout validation failed: {exc}",
            "automation_parse_error_blocker_class": "contract_bug",
        }
    if parse_errors:
        first_error = parse_errors[0]
        invalid_literal = first_error.invalid_literal or "unknown"
        summary = (
            f"Closeout payload contains invalid literal {invalid_literal} "
            f"for field {first_error.field}; either patch the executor prompt "
            f"or add {invalid_literal} to the runner allowlist."
        )
        return {
            "automation_status": "blocked",
            "automation_next_skill": "codex-plan-phase",
            "automation_next_command": "none",
            "automation_human_required": "false",
            "automation_blocker_class": "contract_bug",
            "automation_blocker_summary": summary,
            "automation_required_human_inputs": [],
            "automation_verification_status": "blocked",
            "automation_parse_error": first_error.raw_message,
            "automation_parse_error_blocker_class": "contract_bug",
            "automation_parse_errors": [
                {
                    "source": error.source,
                    "field": error.field,
                    "raw_message": error.raw_message,
                    "invalid_literal": error.invalid_literal,
                }
                for error in parse_errors
            ],
        }
    if payload is None:
        return {}
    terminal_status = str(payload.get("terminal_status") or "")
    verification_status = str(payload.get("verification_status") or "not_run")
    blocker_class = str(payload.get("blocker_class") or "none")
    blocker_summary = str(payload.get("blocker_summary") or "none")
    human_required = bool(payload.get("human_required", False))
    required_inputs = payload.get("required_human_inputs")
    if not isinstance(required_inputs, list):
        required_inputs = []
    return {
        "automation_status": terminal_status,
        "automation_next_skill": "none",
        "automation_next_command": str(payload.get("next_action") or "none"),
        "automation_human_required": "true" if human_required else "false",
        "automation_blocker_class": blocker_class,
        "automation_blocker_summary": blocker_summary,
        "automation_required_human_inputs": [str(item) for item in required_inputs],
        "automation_verification_status": verification_status,
        "produced_if_gates": payload.get("produced_if_gates"),
        "dirty_paths": payload.get("dirty_paths"),
        "native_closeout_payload": payload,
    }


def _executor_terminal_summary(child_automation: dict[str, object]) -> dict[str, object]:
    payload = child_automation.get("native_closeout_payload")
    if isinstance(payload, dict):
        return payload
    return child_automation


def _find_json_closeout_payload(text: str) -> dict[str, object] | None:
    decoder = json.JSONDecoder()
    closeout: dict[str, object] | None = None
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            data, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and {"terminal_status", "verification_status", "dirty_paths"}.issubset(data):
            closeout = data
    return closeout


def _native_closeout_text_candidates(result: LaunchResult, spec, output_text: str) -> tuple[tuple[str, str], ...]:
    candidates: list[tuple[str, str]] = [("output", output_text)]
    log_path = Path(result.log_path) if result.log_path else None
    if log_path is not None:
        try:
            log_text = log_path.read_text(encoding="utf-8")
        except OSError:
            log_text = ""
        if log_text and log_text != output_text:
            candidates.append(("output_log", log_text))
    return tuple(candidates)


def _native_closeout_extraction_failure(
    result: LaunchResult,
    spec,
    *,
    source: str = "output",
    text: str | None = None,
) -> dict[str, object] | None:
    if text is None:
        text = extract_executor_output_text(result, spec)
        for candidate_source, candidate in _native_closeout_text_candidates(result, spec, text):
            if _find_json_closeout_payload(candidate):
                return None
            source = candidate_source
            text = candidate
    if _find_json_closeout_payload(text):
        return None
    stripped = text.strip()
    if result.timed_out or result.interrupted or result.stalled or stripped.count("{") > stripped.count("}"):
        reason = "truncated_output"
    elif "terminal_status" in stripped or "verification_status" in stripped:
        reason = "malformed_native_closeout"
    else:
        reason = "missing_native_closeout"
    return {
        "reason": reason,
        "source": source,
        "classification": "native_closeout_extraction",
    }


def _parsed_artifact_automation(plan: Path, spec) -> dict[str, object]:
    try:
        text = plan.read_text(encoding="utf-8")
    except OSError:
        return {}
    parsed = parse_automation_status(text)
    if text and parsed:
        parsed["raw_output_excerpt"] = text[:1000]
        parsed["automation_source"] = "plan_artifact"
        parsed["automation_artifact"] = str(plan)
    _annotate_automation_parse_error(parsed, _executor_display_name(spec.executor), spec.prompt_bundle.workflow_command)
    return parsed


def _trusted_failed_launch_closeout(result: LaunchResult, spec) -> dict[str, object] | None:
    parsed = _parsed_child_automation(result, spec)
    if not parsed or parsed.get("automation_parse_error"):
        return None
    status = parsed.get("automation_status")
    if not isinstance(status, str):
        return None
    status_literal = _phase_status_literal(status)
    if status_literal not in {"planned", "executed", "awaiting_phase_closeout", "complete"}:
        return None
    if str(parsed.get("automation_human_required", "")).lower() == "true":
        return None
    if _optional_automation_literal(parsed.get("automation_blocker_class")):
        return None
    if _optional_automation_literal(parsed.get("automation_blocker_summary")):
        return None
    if parsed.get("automation_verification_status") not in {"not_run", "passed"}:
        return None
    parsed = dict(parsed)
    parsed["original_returncode"] = result.returncode
    return parsed


def _annotate_automation_parse_error(parsed: dict[str, object], executor_label: str, workflow_command: str) -> None:
    if parsed:
        missing = [
            key
            for key in (
                "automation_status",
                "automation_next_skill",
                "automation_next_command",
                "automation_human_required",
                "automation_blocker_class",
                "automation_blocker_summary",
                "automation_verification_status",
                "automation_required_human_inputs",
            )
            if key not in parsed
        ]
        if missing:
            parsed["automation_parse_error"] = (
                f"{executor_label} live launch for {workflow_command} emitted a malformed shared automation closeout. "
                f"Missing fields: {', '.join(missing)}."
            )


def _parse_delegation_request(text: str) -> DelegationRequest | None:
    if "delegation_request:" not in text:
        return None
    match = re.search(r"(?ms)^delegation_request:\s*\n(?P<body>.*?)(?=^automation:\s*$|\Z)", text)
    if not match:
        return None
    request: dict[str, object] | None = None
    if yaml is not None:
        try:
            payload = yaml.safe_load("delegation_request:\n" + match.group("body"))
        except Exception:
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("delegation_request"), dict):
            request = payload["delegation_request"]
    if request is None:
        request = _parse_plain_delegation_request(match.group("body"))
    if not request:
        return None
    budget = request.get("budget")
    delegation_budget = None
    if isinstance(budget, dict):
        delegation_budget = DelegationBudget(
            max_tokens=budget.get("max_tokens") if isinstance(budget.get("max_tokens"), int) else None,
            max_seconds=budget.get("max_seconds") if isinstance(budget.get("max_seconds"), int) else None,
            max_cost_usd=budget.get("max_cost_usd") if isinstance(budget.get("max_cost_usd"), (int, float)) else None,
            notes=str(budget.get("notes") or budget.get("rationale")) if budget.get("notes") or budget.get("rationale") else None,
        )
    owned_files = request.get("owned_files") or ()
    if not isinstance(owned_files, (list, tuple)):
        owned_files = (str(owned_files),)
    try:
        return DelegationRequest(
            request_id=str(request["request_id"]),
            product_action=str(request["product_action"]),
            target_executor=str(request["target_executor"]),
            reason=str(request["reason"]),
            owned_files=tuple(str(item) for item in owned_files),
            expected_output=str(request["expected_output"]),
            priority=str(request.get("priority") or "normal"),
            review_context=str(request["review_context"]) if request.get("review_context") is not None else None,
            repair_context=str(request["repair_context"]) if request.get("repair_context") is not None else None,
            budget=delegation_budget,
            metadata={"source": "child_automation_delegation_request"},
        )
    except Exception:
        return None


def _parse_plain_delegation_request(body: str) -> dict[str, object]:
    request: dict[str, object] = {}
    current_list: str | None = None
    current_map: str | None = None
    for raw_line in body.splitlines():
        if not raw_line.strip():
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        if line.startswith("- ") and current_list:
            items = request.setdefault(current_list, [])
            if isinstance(items, list):
                items.append(_plain_yaml_value(line[2:]))
            continue
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if current_map and indent > 2:
            nested = request.setdefault(current_map, {})
            if isinstance(nested, dict):
                nested[key] = _plain_yaml_value(value)
            continue
        current_list = None
        current_map = None
        if not value:
            if key == "owned_files":
                request[key] = []
                current_list = key
            elif key == "budget":
                request[key] = {}
                current_map = key
            else:
                request[key] = None
            continue
        request[key] = _plain_yaml_value(value)
    return request


def _plain_yaml_value(value: str) -> object:
    text = value.strip().strip("'\"")
    if text == "[]":
        return []
    if re.fullmatch(r"-?\d+", text):
        try:
            return int(text)
        except ValueError:
            return text
    if re.fullmatch(r"-?\d+\.\d+", text):
        try:
            return float(text)
        except ValueError:
            return text
    return text


def _requires_shared_automation_closeout(result: LaunchResult, spec) -> bool:
    if spec.executor not in {"codex", "claude", "gemini", "opencode", "command"}:
        return False
    return result.executor == spec.executor


def _repair_launch_cleared_phase(
    launch_action: str,
    post_launch: str | None,
    post_snapshot: StateSnapshot,
    alias: str,
) -> bool:
    if launch_action != "repair":
        return False
    if post_launch not in {"planned", "complete", "awaiting_phase_closeout"}:
        return False
    if post_snapshot.current_phase == alias and (post_snapshot.human_required or post_snapshot.blocker_class):
        return False
    return True


def _successful_missing_closeout_blocker(result: LaunchResult, blocker: dict | None) -> bool:
    if result.returncode != 0 or result.timed_out or result.interrupted or result.stalled:
        return False
    if not blocker or blocker.get("human_required"):
        return False
    if blocker.get("blocker_class") != "repeated_verification_failure":
        return False
    summary = str(blocker.get("blocker_summary") or "")
    return "did not emit a valid shared automation closeout" in summary


def _launch_contract_blocker(
    result: LaunchResult,
    artifacts: dict[str, Path],
    executor: str,
    phase: str,
) -> dict[str, object] | None:
    if result.stalled:
        return {
            "human_required": False,
            "blocker_class": "stalled_child_observation",
            "blocker_summary": (
                f"{_executor_display_name(executor)} live launch for {phase} went silent past the quiet-blocker threshold "
                "(no log output, child still running) and required process-group cleanup before it could emit a terminal summary."
            ),
            "required_human_inputs": (),
            "access_attempts": (),
        }
    if result.timed_out:
        return {
            "human_required": False,
            "blocker_class": "repeated_verification_failure",
            "blocker_summary": (
                f"{_executor_display_name(executor)} live launch for {phase} exceeded the runner timeout and required process-group cleanup."
            ),
            "required_human_inputs": (),
            "access_attempts": (),
        }
    if result.interrupted:
        return {
            "human_required": False,
            "blocker_class": "repeated_verification_failure",
            "blocker_summary": (
                f"{_executor_display_name(executor)} live launch for {phase} was interrupted and required process-group cleanup."
            ),
            "required_human_inputs": (),
            "access_attempts": (),
        }
    cleanup = result.cleanup_evidence if isinstance(result.cleanup_evidence, dict) else {}
    if cleanup.get("process_alive_after_cleanup"):
        return {
            "human_required": False,
            "blocker_class": "repeated_verification_failure",
            "blocker_summary": (
                f"{_executor_display_name(executor)} live launch for {phase} failed to fully clean up the child process boundary."
            ),
            "required_human_inputs": (),
            "access_attempts": (),
        }
    if result.log_path and not result.dry_run and (
        result.process_pid is not None
        or result.started_at is not None
        or result.finished_at is not None
        or result.heartbeat_summary is not None
    ):
        log_path = Path(result.log_path)
        if not log_path.exists():
            return {
                "human_required": False,
                "blocker_class": "repeated_verification_failure",
                "blocker_summary": (
                    f"{_executor_display_name(executor)} live launch for {phase} exited without the required durable output log."
                ),
                "required_human_inputs": (),
                "access_attempts": (),
            }
        if log_path.stat().st_size == 0 and not result.output.strip():
            return {
                "human_required": False,
                "blocker_class": "repeated_verification_failure",
                "blocker_summary": (
                    f"{_executor_display_name(executor)} live launch for {phase} produced a zero-byte durable output log and no reducible child output."
                ),
                "required_human_inputs": (),
                "access_attempts": (),
            }
    return None


def _executor_launch_failure_blocker(executor: str, phase: str, output: str) -> dict[str, object] | None:
    if executor not in {"codex", "claude", "gemini", "opencode"}:
        return None
    lowered = output.lower()
    auth_markers = (
        "not logged in",
        "log in",
        "login",
        "subscription",
        "billing",
        "quota",
        "rate limit",
        "extra usage",
        "usage",
        "overage",
        "account",
        "auth",
    )
    if not any(marker in lowered for marker in auth_markers):
        return None
    label = {
        "codex": "Codex",
        "claude": "Claude",
        "gemini": "Gemini",
        "opencode": "OpenCode",
    }[executor]
    return {
        "human_required": False,
        "blocker_class": "account_or_billing_setup",
        "blocker_summary": (
            f"{label} live launch for {phase} failed after metadata-only preflight and appears to require CLI auth, quota, or subscription attention."
        ),
        "required_human_inputs": (),
        "access_attempts": (),
    }


def _record_preflight_degradation(repo: Path, executor: str, phase: str, preflight: object) -> None:
    ttl_seconds = getattr(preflight, "suggested_ttl_seconds", None)
    blocker_class = getattr(preflight, "blocker_class", None)
    if ttl_seconds is None or not blocker_class:
        return
    record_degradation(
        repo,
        executor,
        str(blocker_class),
        phase,
        str(getattr(preflight, "blocker_summary", "") or ""),
        int(ttl_seconds),
        demoted_to=str(getattr(preflight, "demoted_to", None) or "proof_gated"),
    )


def _executor_display_name(executor: str) -> str:
    if executor == "opencode":
        return "Opencode"
    if executor == "command":
        return "Command adapter"
    return executor.capitalize()


def _terminal_verification_status(terminal_status: str, blocker: dict | None) -> str:
    if terminal_status == "unknown":
        return "failed"
    if blocker:
        return "blocked"
    if terminal_status == "complete":
        return "passed"
    return "not_run"


def _terminal_next_action(terminal_status: str, blocker: dict | None, dirty_summary: dict[str, object]) -> str:
    if blocker and blocker.get("blocker_summary"):
        return str(blocker["blocker_summary"])
    if dirty_summary.get("phase_owned_dirty"):
        return "Preserve the verified phase-owned output before rerunning the loop."
    if dirty_summary.get("dirty_paths"):
        return "Inspect the dirty path classification before rerunning the loop."
    if terminal_status == "planned":
        return "Execute the current phase when ready."
    if terminal_status == "complete":
        return "Continue to the next non-complete phase."
    if terminal_status == "blocked":
        return "Repair the recorded blocker before rerunning the loop."
    return "Inspect the final log and event ledger before resuming."


def _optional_automation_literal(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null"}:
        return None
    if text.startswith("<") and text.endswith(">") and "none" in text.lower():
        return None
    if text == "blocked_by_external_setup":
        return "admin_approval"
    if text == "blocked_by_implementation":
        return "repeated_verification_failure"
    return text


def _phase_status_literal(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text in PHASE_STATUSES else None


def _dirty_paths(repo: Path) -> list[str]:
    try:
        status = subprocess.check_output(
            ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
            text=True,
        )
    except Exception:
        return []
    paths: list[str] = []
    for line in status.splitlines():
        path = line[3:] if len(line) > 3 else ""
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip().strip('"')
        if path:
            paths.append(path)
    return sorted(dict.fromkeys(paths))


def _detect_dirty_renames(repo: Path) -> dict[str, str]:
    """Detect rename pairs in the dirty tree.

    Returns `{source: destination}` for both git-reported renames
    (`R src -> dst` in `git status --porcelain`) and filesystem-only moves
    that git did not pair, identified by blob-hash equality between the
    deleted HEAD blob and an untracked working-tree file.

    Pairing is exact (blob-equal), not similarity-based — a move that
    rewrites content will not be detected, by design. Callers should add
    an explicit ownership declaration for that case.
    """
    try:
        status = subprocess.check_output(
            ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
            text=True,
        )
    except Exception:
        return {}

    renames: dict[str, str] = {}
    deletions: list[str] = []
    untracked: list[str] = []
    for line in status.splitlines():
        if len(line) < 4:
            continue
        xy = line[:2]
        path = line[3:]
        if " -> " in path:
            src, dst = path.split(" -> ", 1)
            renames[src.strip().strip('"')] = dst.strip().strip('"')
            continue
        path = path.strip().strip('"')
        if not path:
            continue
        if xy == "??":
            untracked.append(path)
        elif "D" in xy:
            deletions.append(path)

    if not deletions or not untracked:
        return renames

    untracked_pool = list(untracked)
    for deletion in deletions:
        try:
            head_blob = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", f"HEAD:{deletion}"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except Exception:
            continue
        if not head_blob:
            continue
        matched: str | None = None
        for candidate in untracked_pool:
            try:
                if not (repo / candidate).is_file():
                    continue
                candidate_blob = subprocess.check_output(
                    ["git", "-C", str(repo), "hash-object", candidate],
                    text=True,
                    stderr=subprocess.DEVNULL,
                ).strip()
            except Exception:
                continue
            if candidate_blob == head_blob:
                matched = candidate
                break
        if matched is not None:
            renames[deletion] = matched
            untracked_pool.remove(matched)

    return renames


def _classify_dirty_paths(
    repo: Path,
    roadmap: Path,
    plan: Path | None,
    pre_launch_dirty_paths: list[str],
    post_launch_dirty_paths: list[str],
    *,
    allow_pre_existing_phase_owned: bool = False,
    current_phase: str | None = None,
    terminal_summary: dict[str, object] | None = None,
    emit_runtime_relaxation_event: bool = False,
) -> dict[str, object]:
    ownership = parse_plan_ownership(repo, roadmap, plan)
    pre_launch = set(pre_launch_dirty_paths)
    expected_sibling_dirty = [
        path for path in post_launch_dirty_paths if current_phase and is_sibling_phase_plan_doc(path, roadmap, current_phase)
    ]
    expected_sibling_set = set(expected_sibling_dirty)
    previous_evidence = set(previous_phase_owned_dirty_paths(repo, current_phase)) if current_phase else set()
    previous_phase_owned = [
        path for path in post_launch_dirty_paths if path in previous_evidence and (not pre_launch or path in pre_launch)
    ]
    previous_phase_owned_set = set(previous_phase_owned)
    phase_owned = [
        path
        for path in post_launch_dirty_paths
        if path not in previous_phase_owned_set and ownership.matches_dirty_output(path)
    ]
    phase_owned_set = set(phase_owned)

    rename_map = _detect_dirty_renames(repo)
    rename_sources_promoted: list[str] = []
    for src, dst in rename_map.items():
        if src in phase_owned_set:
            continue
        if src not in post_launch_dirty_paths:
            continue
        if dst in phase_owned_set or ownership.matches_dirty_output(dst):
            phase_owned.append(src)
            phase_owned_set.add(src)
            rename_sources_promoted.append(src)

    pre_existing = [
        path
        for path in post_launch_dirty_paths
        if path in pre_launch
        and path not in ownership.control_paths
        and path not in expected_sibling_set
        and path not in previous_phase_owned_set
        and not (allow_pre_existing_phase_owned and path in phase_owned_set)
    ]
    unowned = [
        path
        for path in post_launch_dirty_paths
        if path not in phase_owned_set and path not in previous_phase_owned_set and path not in expected_sibling_set
    ]
    runtime_relaxation = _runtime_relaxation_evidence(
        ownership.owned_patterns,
        post_launch_dirty_paths,
        unowned,
        terminal_summary,
    )
    if runtime_relaxation:
        for item in runtime_relaxation:
            path = item["path"]
            if path not in phase_owned_set:
                phase_owned.append(path)
                phase_owned_set.add(path)
        relaxed_paths = {item["path"] for item in runtime_relaxation}
        unowned = [path for path in unowned if path not in relaxed_paths]
        if emit_runtime_relaxation_event and current_phase:
            _append_runtime_relaxation_event(
                repo,
                roadmap,
                current_phase,
                declared_paths=ownership.owned_patterns,
                actual_paths=post_launch_dirty_paths,
                evidence=runtime_relaxation,
            )
    control_only_dirty = bool(post_launch_dirty_paths) and all(path in ownership.control_paths for path in post_launch_dirty_paths)
    closeout_safe_dirty = bool(post_launch_dirty_paths) and not pre_existing and not unowned
    return {
        "dirty_paths": post_launch_dirty_paths,
        "phase_owned_dirty_paths": phase_owned,
        "previous_phase_owned_paths": previous_phase_owned,
        "expected_sibling_dirty_paths": expected_sibling_dirty,
        "expected_sibling_dirty": bool(expected_sibling_dirty),
        "unowned_dirty_paths": unowned,
        "pre_existing_dirty_paths": pre_existing,
        "phase_owned_dirty": (ownership.valid or control_only_dirty) and closeout_safe_dirty,
        "ownership_errors": [] if control_only_dirty else list(ownership.errors),
        "rename_sources_promoted": rename_sources_promoted,
        "runtime_relaxation_evidence": runtime_relaxation,
    }


def _runtime_relaxation_evidence(
    declared_paths: tuple[str, ...],
    actual_paths: list[str],
    unowned_paths: list[str],
    terminal_summary: dict[str, object] | None,
) -> tuple[dict[str, str], ...]:
    if not unowned_paths or not trust_executor_evidence_enabled() or not terminal_summary:
        return ()
    if terminal_summary.get("phase_owned_dirty") is not True:
        return ()
    accepted = validate_phase_owned_evidence(
        declared_paths,
        tuple(actual_paths),
        terminal_summary.get("phase_owned_evidence"),
    )
    accepted_by_path = {item["path"]: item for item in accepted}
    return tuple(accepted_by_path[path] for path in unowned_paths if path in accepted_by_path)


def _append_runtime_relaxation_event(
    repo: Path,
    roadmap: Path,
    phase: str,
    *,
    declared_paths: tuple[str, ...],
    actual_paths: list[str],
    evidence: tuple[dict[str, str], ...],
) -> None:
    append_event(
        repo,
        LoopEvent(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phase=phase,
            action="runner.runtime_relaxation_invoked",
            status="executed",
            model="phase-loop-runtime",
            reasoning_effort="none",
            source="runner",
            metadata={
                "declared_paths": list(declared_paths),
                "actual_paths": list(actual_paths),
                "evidence": list(evidence),
            },
            **event_provenance(roadmap, phase),
        ),
    )


def _dirty_outcome(dirty_summary: dict[str, object], *, blocked_summary: str) -> tuple[str, dict | None]:
    if dirty_summary.get("phase_owned_dirty"):
        return "awaiting_phase_closeout", None

    details: list[str] = []
    if dirty_summary.get("pre_existing_dirty_paths"):
        details.append("pre-existing dirty paths: " + ", ".join(dirty_summary["pre_existing_dirty_paths"]))
    if dirty_summary.get("unowned_dirty_paths"):
        details.append("unowned dirty paths: " + ", ".join(dirty_summary["unowned_dirty_paths"]))
    if dirty_summary.get("ownership_errors"):
        details.append("ownership evidence failed closed: " + ", ".join(dirty_summary["ownership_errors"]))
    summary = blocked_summary if not details else f"{blocked_summary} ({'; '.join(details)})"
    required_inputs: list[str] = []
    if dirty_summary.get("pre_existing_dirty_paths"):
        required_inputs.append(
            "Review or isolate pre-existing dirty paths before rerunning the loop: "
            + ", ".join(dirty_summary["pre_existing_dirty_paths"])
        )
    if dirty_summary.get("unowned_dirty_paths"):
        required_inputs.append(
            "Review or isolate unowned dirty paths before rerunning the loop: "
            + ", ".join(dirty_summary["unowned_dirty_paths"])
        )
    if dirty_summary.get("ownership_errors"):
        required_inputs.append(
            "Repair the plan ownership contract before rerunning the loop: "
            + ", ".join(dirty_summary["ownership_errors"])
        )
    human_required = bool(dirty_summary.get("pre_existing_dirty_paths")) or (
        bool(dirty_summary.get("unowned_dirty_paths")) and not bool(dirty_summary.get("ownership_errors"))
    )
    return (
        "blocked",
        {
            "human_required": human_required,
            "blocker_class": "dirty_worktree_conflict",
            "blocker_summary": summary,
            "required_human_inputs": tuple(required_inputs)
            or ("Review the dirty path classification before rerunning the loop.",),
            "access_attempts": (),
        },
    )


def _closeout_lane_ir_blocker(repo: Path, roadmap: Path, phase: str) -> dict[str, object] | None:
    """OWNFIX #17: surface unresolved Lane IR diagnostics at closeout as a
    contract_bug naming the failing lane/diagnostic, instead of the misleading
    missing_phase_owned_dirty_paths refusal. Reuses the same parse + override path
    and the same lane_ir_diagnostics shape as the pre-launch reconcile._plan_blocker.
    """
    plan = find_plan_artifact(repo, phase, roadmap=roadmap)
    if plan is None:
        return None
    from .plan_ir import parse_phase_plan_ir
    from .reconcile import _lane_ir_override

    lane_ir = parse_phase_plan_ir(plan)
    if not (lane_ir.lanes and lane_ir.diagnostics):
        return None
    override = _lane_ir_override(repo, roadmap, phase, plan)
    remaining = tuple(diagnostic for diagnostic in lane_ir.diagnostics if diagnostic.kind not in override)
    if not remaining:
        return None
    detail = ", ".join(f"{d.kind}@{d.lane_id or 'plan'}" for d in remaining)
    return {
        "human_required": False,
        "blocker_class": "contract_bug",
        "blocker_summary": (
            f"Lane IR diagnostics failed closed for {phase} closeout: {detail}. "
            "Fix the plan's lane ownership before rerunning closeout."
        ),
        "required_human_inputs": (),
        "access_attempts": (),
        "lane_ir_diagnostics": tuple(diagnostic.to_json() for diagnostic in remaining),
    }


def _perform_phase_closeout(
    repo: Path,
    roadmap: Path,
    phase: str,
    snapshot: StateSnapshot,
    selection,
    *,
    action: str,
    closeout_mode: str,
) -> tuple[str, LoopEvent]:
    terminal_status = snapshot.closeout_terminal_status or "executed"
    verification_status = "passed" if terminal_status == "complete" else "not_run"
    metadata = {
        "closeout": {
            "closeout_mode": closeout_mode,
            "verification_status": verification_status,
        }
    }
    blocker = None
    status = terminal_status
    closeout_dirty_paths = tuple(
        dict.fromkeys((*snapshot.phase_owned_dirty_paths, *snapshot.previous_phase_owned_paths))
    )
    # Fallback (regenesis v37 fix): when codex's classification left
    # phase_owned_dirty_paths empty but every dirty path matches the
    # active plan's owned-files glob, auto-classify as phase-owned and
    # proceed. Works around codex emitting empty phase_owned_dirty_paths
    # despite valid dirty_paths. Does NOT bypass the blocker if any dirty
    # path is NOT owned by the plan.
    unowned_remainder: tuple[str, ...] = ()
    if (not snapshot.phase_owned_dirty or not closeout_dirty_paths) and snapshot.dirty_paths:
        plan_for_fallback = find_plan_artifact(repo, phase, roadmap=roadmap)
        if plan_for_fallback is not None:
            ownership_for_fallback = parse_plan_ownership(repo, roadmap, plan_for_fallback)
            if ownership_for_fallback.valid:
                # OWNFIX #36-item1: partial-classify. The de6ce6f fallback was
                # all-or-nothing — a SINGLE unowned dirty path (e.g. a test the plan
                # under-enumerated, as reproduced from the real ai-stack-v2 INVENTORY
                # run) defeated `all(...)` and blocked every verified-owned path.
                # Instead, auto-classify the matching subset (so verified owned work
                # commits without manual intervention) and carry the genuinely-unowned
                # remainder forward to a loud, human-required scope blocker below.
                matched = tuple(
                    p for p in snapshot.dirty_paths if ownership_for_fallback.matches_dirty_output(p)
                )
                if matched:
                    matched_set = set(matched)
                    unowned_remainder = tuple(p for p in snapshot.dirty_paths if p not in matched_set)
                    reclassified_paths = tuple(
                        dict.fromkeys((*matched, *snapshot.previous_phase_owned_paths))
                    )
                    closeout_dirty_paths = reclassified_paths
                    snapshot = replace(
                        snapshot,
                        phase_owned_dirty=True,
                        phase_owned_dirty_paths=matched,
                    )
                    metadata["closeout"]["closeout_dirty_paths_autoclassified"] = list(matched)
                    if unowned_remainder:
                        metadata["closeout"]["closeout_unowned_remainder"] = list(unowned_remainder)
    if not snapshot.phase_owned_dirty or not closeout_dirty_paths:
        status = "blocked"
        # OWNFIX #17: an invalid Lane IR is the real reason classification failed and
        # the fallback could not fire — surface that contract_bug (naming the lane /
        # diagnostic) rather than the misleading missing_phase_owned_dirty_paths.
        # Ordering invariant: an invalid plan never autoclassifies (the fallback above
        # requires ownership.valid), so this short-circuits before a clean refusal.
        lane_ir_blocker = _closeout_lane_ir_blocker(repo, roadmap, phase)
        if lane_ir_blocker is not None:
            blocker = lane_ir_blocker
            metadata["closeout"].update(
                {
                    "closeout_action": "refused",
                    "closeout_refusal_reason": "lane_ir_contract_bug",
                    "lane_ir_diagnostics": list(lane_ir_blocker["lane_ir_diagnostics"]),
                }
            )
        else:
            blocker = {
                "human_required": False,
                "blocker_class": "dirty_worktree_conflict",
                "blocker_summary": "Trusted phase-owned dirty paths are missing for closeout.",
                "required_human_inputs": ("Inspect dirty path classification before rerunning closeout.",),
                "access_attempts": (),
            }
            metadata["closeout"].update(
                {
                    "closeout_action": "refused",
                    "closeout_refusal_reason": "missing_phase_owned_dirty_paths",
                }
            )
    else:
        commit_action = _closeout_commit_action(action, terminal_status)
        plan = find_plan_artifact(repo, phase, roadmap=roadmap)
        commit_message = _closeout_commit_message(
            repo,
            phase,
            action=commit_action,
            terminal_status=terminal_status,
            plan=plan,
            coauthor_trailers=_closeout_coauthor_trailers(repo, roadmap, phase),
            continuation=bool(snapshot.previous_phase_owned_paths),
        )
        add_result = _run_git_closeout(repo, "add", "--", *closeout_dirty_paths)
        if add_result.returncode != 0:
            status, blocker = _commit_failure_closeout(
                metadata,
                stage="add",
                returncode=add_result.returncode,
                stderr=add_result.stderr or add_result.stdout,
            )
        else:
            guard_blocker = _refuse_pipeline_default_branch_commit(repo)
            if guard_blocker is not None:
                status = "blocked"
                blocker = guard_blocker
                metadata["closeout"].update(
                    {
                        "closeout_action": "commit_refused",
                        "verification_status": "blocked",
                        "closeout_refusal_reason": "pipeline_default_branch_commit",
                    }
                )
            else:
                commit_result = _run_git_closeout(repo, "commit", "-F", "-", input_text=commit_message)
                if commit_result.returncode != 0:
                    status, blocker = _commit_failure_closeout(
                        metadata,
                        stage="commit",
                        returncode=commit_result.returncode,
                        stderr=commit_result.stderr or commit_result.stdout,
                    )
                else:
                    commit = _git_output(repo, "rev-parse", "HEAD")
                    status = "planned" if terminal_status == "planned" else "complete"
                    metadata["closeout"]["verification_status"] = "not_run" if status == "planned" else "passed"
                    metadata["closeout"].update(
                        {
                            "closeout_action": "commit",
                            "closeout_commit": commit,
                        }
                    )
                    if roadmap_closeout_evidence_audit_enabled(roadmap):
                        try:
                            audit = audit_closeout_evidence(commit, phase, repo)
                        except Exception as exc:
                            status, blocker = _commit_failure_closeout(
                                metadata,
                                stage="audit",
                                returncode=1,
                                stderr=str(exc),
                                blocker_class="repeated_verification_failure",
                            )
                        else:
                            total_claims = len(audit.matched_claims) + len(audit.unmatched_claims)
                            metadata["closeout"]["closeout_evidence_audit"] = {
                                "audit_status": audit.audit_status,
                                "matched_claim_count": len(audit.matched_claims),
                                "unmatched_claim_count": len(audit.unmatched_claims),
                                "total_claim_count": total_claims,
                            }
                            if audit.audit_status == "drift_detected":
                                status = "blocked"
                                metadata["closeout"]["verification_status"] = "blocked"
                                blocker = {
                                    "human_required": False,
                                    "blocker_class": "closeout_evidence_drift",
                                    "blocker_summary": (
                                        f"{len(audit.unmatched_claims)} of {total_claims} "
                                        "closeout claims have no matching files in the closeout diff"
                                    ),
                                    "required_human_inputs": (),
                                    "access_attempts": (),
                                }
        if closeout_mode == "push":
            decision = resolve_closeout_push_target(repo, collect_git_topology(repo))
            if decision.get("allowed"):
                _git(repo, "push", str(decision["remote"]), f"HEAD:{decision['push_ref']}")
                metadata["closeout"].update(
                    {
                        "closeout_action": "push",
                        "closeout_push_ref": f"{decision['remote']} {decision['push_ref']}",
                    }
                )
            else:
                metadata["closeout"].update(
                    {
                        "closeout_action": "push_refused",
                        "closeout_push_ref": decision.get("push_ref"),
                        "closeout_refusal_reason": decision.get("refusal_reason"),
                    }
                )
    # OWNFIX #36-item1: if the owned subset committed cleanly but a genuinely-unowned
    # remainder exists, surface it loudly AFTER preserving the owned work. Only fires on
    # commit success (status complete/planned) so it never overrides an earlier block
    # (commit failure, audit drift, lane-IR). The remainder cannot be auto-resolved
    # until the plan declares it or GATE's classifier lands, so it is human_required —
    # the autonomous loop then stops cleanly (runner.py ~1438) instead of spinning.
    # Verification genuinely passed; this block is about scope, not verification.
    if unowned_remainder and status in ("complete", "planned"):
        status = "blocked"
        blocker = {
            "human_required": True,
            "blocker_class": "closeout_scope_violation",
            "blocker_summary": (
                f"Committed {len(closeout_dirty_paths)} phase-owned path(s); "
                f"{len(unowned_remainder)} verified dirty path(s) are outside the plan's "
                f"owned files and need an ownership declaration or break-glass: "
                f"{', '.join(unowned_remainder)}"
            ),
            "required_human_inputs": (
                "Declare the path(s) in the phase plan's owned files, or rerun closeout with break-glass.",
            ),
            "access_attempts": (),
        }
        metadata["closeout"].update(
            {
                "closeout_refusal_reason": "unowned_dirty_remainder",
                "unowned_dirty_paths": list(unowned_remainder),
            }
        )
    event = LoopEvent(
        timestamp=utc_now(),
        repo=str(repo),
        roadmap=str(roadmap),
        phase=phase,
        action=action,
        status=status,
        model=selection.model,
        reasoning_effort=selection.effort,
        source=selection.source,
        override_reason=selection.override_reason,
        blocker=blocker,
        metadata=metadata,
        **event_provenance(roadmap, phase),
    )
    return status, event


def _closeout_commit_action(action: str, terminal_status: str) -> str:
    if action in {"plan", "execute", "repair", "review", "roadmap", "maintain-skills"}:
        return action
    return "plan" if terminal_status == "planned" else "execute"


def _closeout_commit_message(
    repo: Path,
    phase: str,
    *,
    action: str,
    terminal_status: str,
    plan: Path | None,
    coauthor_trailers: tuple[str, ...] = (),
    continuation: bool = False,
) -> str:
    prefix = "phase-loop continuation" if continuation else f"phase-loop {action}"
    lines = [f"{prefix}: {phase}", ""]
    if plan is not None and plan.exists():
        lines.append(f"Plan: {_repo_relative(repo, plan)}")
    lines.extend(
        [
            f"Terminal-Status: {terminal_status}",
            "Closeout-Commit: pending",
        ]
    )
    if phase.upper() == "CAC":
        lines.append("Fixes #8 Fixes #10")
    if continuation:
        lines.append("Refs #10")
    if coauthor_trailers:
        lines.append("")
        lines.extend(coauthor_trailers)
    return "\n".join(lines).rstrip() + "\n"


def _closeout_coauthor_trailers(repo: Path, roadmap: Path, phase: str) -> tuple[str, ...]:
    trailers: list[str] = []
    provenance = event_provenance(roadmap, phase)
    for event in reversed(read_events(repo)):
        if str(event.get("phase", "")).upper() != phase.upper():
            continue
        if str(event.get("roadmap_sha256") or "") != provenance.get("roadmap_sha256"):
            continue
        metadata = event.get("metadata")
        if not isinstance(metadata, dict):
            continue
        child = metadata.get("child_automation")
        if isinstance(child, dict):
            _collect_coauthor_trailers(child, trailers)
        terminal = metadata.get("terminal_summary")
        if isinstance(terminal, dict):
            _collect_coauthor_trailers(terminal, trailers)
        if trailers:
            break
    return tuple(dict.fromkeys(trailers))


def _collect_coauthor_trailers(payload: dict[str, object], trailers: list[str]) -> None:
    for key in ("co_authored_by", "coauthored_by", "coauthor_trailers", "co_authored_by_trailers"):
        _append_valid_coauthor_values(payload.get(key), trailers)
    native = payload.get("native_closeout_payload")
    if isinstance(native, dict):
        _collect_coauthor_trailers(native, trailers)
    raw = payload.get("raw_output_excerpt")
    if isinstance(raw, str):
        for line in raw.splitlines():
            _append_valid_coauthor_values(line.strip(), trailers)


def _append_valid_coauthor_values(value: object, trailers: list[str]) -> None:
    values = value if isinstance(value, (list, tuple)) else (value,)
    for item in values:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if re.fullmatch(r"Co-Authored-By: [^<>\n]+ <[^<>\s]+@[^<>\s]+>", text):
            trailers.append(text)


def _run_git_closeout(repo: Path, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _commit_failure_closeout(
    metadata: dict[str, object],
    *,
    stage: str,
    returncode: int,
    stderr: str,
    blocker_class: str = "dirty_worktree_conflict",
) -> tuple[str, dict[str, object]]:
    closeout = metadata.setdefault("closeout", {})
    if isinstance(closeout, dict):
        closeout["closeout_action"] = "commit_failed"
        closeout["verification_status"] = "blocked"
        closeout["commit_failure"] = {
            "stage": stage,
            "returncode": returncode,
            "stderr_excerpt": _redacted_stderr_excerpt(stderr),
        }
    return (
        "awaiting_phase_closeout",
        {
            "human_required": False,
            "blocker_class": blocker_class,
            "blocker_summary": f"Commit closeout failed during {stage}; inspect commit_failure metadata before rerunning closeout.",
            "required_human_inputs": (),
            "access_attempts": (),
        },
    )


def _redacted_stderr_excerpt(text: str, max_chars: int = 500) -> str:
    redacted = re.sub(r"(?i)(api[_-]?key|authorization|token|secret|password)(\s*[:=]\s*)\S+", r"\1\2<redacted>", text or "")
    redacted = " ".join(redacted.split())
    if len(redacted) > max_chars:
        return redacted[: max_chars - 3] + "..."
    return redacted


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _git_output(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


class _null_context:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return False


def _write_state_and_handoff(
    repo: Path,
    roadmap: Path,
    snapshot: StateSnapshot,
    *,
    action: str,
    results: list[LaunchResult],
    output_path: str | Path | None = None,
    override_phase: str | None = None,
    source_bundle_path: str | Path | None = None,
    pipeline_mode: str | None = None,
) -> None:
    recent_metrics = read_work_unit_metrics(repo, limit=50)
    work_units = {key: value.to_json() for key, value in load_work_unit_state(repo).items()}
    latest_work_unit = None
    if work_units:
        latest_work_unit = max(work_units.values(), key=lambda item: str(item.get("updated_at") or ""))
    snapshot = replace(
        snapshot,
        latest_metric=recent_metrics[-1] if recent_metrics else snapshot.latest_metric,
        metrics_summary=summarize_work_unit_metrics(recent_metrics) if recent_metrics else snapshot.metrics_summary,
        work_units=work_units or snapshot.work_units,
        latest_work_unit=latest_work_unit or snapshot.latest_work_unit,
    )
    write_state(repo, snapshot)
    write_tui_handoff(repo, roadmap, snapshot, action=action, results=results, mode="product")
    if output_path:
        _write_deterministic_closeout(
            repo,
            roadmap,
            snapshot,
            Path(output_path),
            override_phase=override_phase,
            source_bundle_path=source_bundle_path,
            pipeline_mode=pipeline_mode,
        )


def _write_deterministic_closeout(
    repo: Path,
    roadmap: Path,
    snapshot: StateSnapshot,
    output_path: Path,
    override_phase: str | None = None,
    source_bundle_path: str | Path | None = None,
    pipeline_mode: str | None = None,
) -> None:
    import json

    phase = override_phase or snapshot.current_phase
    plan = find_plan_artifact(repo, phase, roadmap=roadmap)
    stale_plan = _stale_pipeline_plan_candidate(repo, roadmap, phase) if phase and plan is None else None
    if stale_plan is not None:
        plan = stale_plan[0]
    bundle_path = source_bundle_path or os.environ.get("PHASE_LOOP_SOURCE_BUNDLE")
    effective_pipeline_mode = pipeline_mode or os.environ.get("PHASE_LOOP_PIPELINE_MODE")

    source_bundle = None
    diagnostic = None
    blocker = None
    if phase:
        if stale_plan is not None:
            diagnostic = stale_plan[1]
            blocker = pipeline_execution_blocker(diagnostic)
        elif plan is not None:
            diagnostic = pipeline_execution_plan_diagnostic(repo, plan, phase=phase, roadmap=roadmap)
            if diagnostic is not None:
                blocker = pipeline_execution_blocker(diagnostic)
        if diagnostic is None:
            try:
                if bundle_path or effective_pipeline_mode:
                    source_bundle = load_phase_source_bundle(
                        repo,
                        bundle_path,
                        phase=phase,
                        roadmap=roadmap,
                        pipeline_mode=effective_pipeline_mode,
                    )
                elif plan is not None:
                    source_bundle = load_execution_phase_source_bundle(repo, plan, phase=phase, roadmap=roadmap)
            except Exception:
                source_bundle = None

    terminal_summary = snapshot.terminal_summary if not override_phase or override_phase == snapshot.current_phase else None
    existing_closeout = (
        terminal_summary.get("phase_loop_closeout")
        if isinstance(terminal_summary, dict) and isinstance(terminal_summary.get("phase_loop_closeout"), dict)
        else None
    )
    if existing_closeout is not None and phase_loop_closeout_diagnostic(existing_closeout) is None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(existing_closeout, indent=2, sort_keys=True), encoding="utf-8")
        return
    if blocker is not None:
        terminal_summary = build_terminal_summary(
            terminal_status="blocked",
            terminal_blocker=blocker,
            verification_status="blocked",
            next_action="Repair or replan the Pipeline-aware phase before accepting deterministic closeout.",
        )

    changed_paths = (
        tuple(dict.fromkeys((*snapshot.phase_owned_dirty_paths, *snapshot.previous_phase_owned_paths)))
        if not override_phase or override_phase == snapshot.current_phase
        else ()
    )
    if not changed_paths and plan is not None:
        dirty_summary = _classify_dirty_paths(repo, roadmap, plan, [], _dirty_paths(repo), current_phase=phase)
        changed_paths = tuple(
            dict.fromkeys(
                (
                    *dirty_summary.get("phase_owned_dirty_paths", ()),
                    *dirty_summary.get("previous_phase_owned_paths", ()),
                )
            )
        )

    closeout = build_phase_loop_closeout(
        phase_alias=phase or "UNKNOWN",
        plan_path=plan or "",
        source_bundle=source_bundle,
        plan_metadata=parse_pipeline_plan_metadata(plan) if plan is not None else None,
        pipeline_diagnostic=diagnostic,
        terminal_summary=terminal_summary,
        blocker=blocker or {},
        changed_paths=changed_paths,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(closeout, indent=2, sort_keys=True), encoding="utf-8")
