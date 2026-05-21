from __future__ import annotations

import os
import re
import subprocess
from dataclasses import replace
from pathlib import Path

from .broker import validate_delegation_request
from .capability_registry import default_executor_for_work_unit, describe_dispatch_decision, resolve_dispatch_decision
from .classifier import classify_all
from .closeout import build_phase_loop_closeout, phase_loop_closeout_diagnostic
from .discovery import (
    PLAN_RE,
    dispatch_hints_for_action,
    execution_policy_dispatch_hints,
    execution_policy_for_action,
    find_plan_artifact,
    load_execution_phase_source_bundle,
    load_phase_source_bundle,
    parse_automation_status,
    parse_dispatch_hints,
    parse_execution_policy,
    parse_pipeline_plan_metadata,
    parse_plan_ownership,
    parse_roadmap_phases,
    pipeline_execution_blocker,
    pipeline_execution_plan_diagnostic,
    phase_source_bundle_diagnostic,
    plan_artifact_diagnostic,
)
from .events import append_event, event_path, read_events
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
from .profiles import resolve_execution_policy, resolve_model_selection_from_policy, resolve_profile, resolve_profile_for_executor
from .prompts import build_prompt
from .provenance import event_provenance, snapshot_provenance
from .reconcile import reconcile
from .release_guard import release_dispatch_blocker
from .state import load_work_unit_state, state_path, write_state, write_work_unit_state
from .state_degradation import record_degradation

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
        if not match or match.group(1).lower() != phase.lower():
            continue
        diagnostic = pipeline_execution_plan_diagnostic(repo, candidate, phase=phase, roadmap=roadmap)
        if diagnostic is not None:
            return candidate, diagnostic
    return None


def status_snapshot(repo: Path, roadmap: Path) -> StateSnapshot:
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
        unowned_dirty_paths=snapshot.unowned_dirty_paths,
        pre_existing_dirty_paths=snapshot.pre_existing_dirty_paths,
        phase_owned_dirty=snapshot.phase_owned_dirty,
        terminal_summary=snapshot.terminal_summary,
        latest_metric=recent_metrics[-1] if recent_metrics else None,
        metrics_summary=summarize_work_unit_metrics(recent_metrics),
        closeout_terminal_status=snapshot.closeout_terminal_status,
        closeout_summary=snapshot.closeout_summary,
        ledger_warnings=snapshot.ledger_warnings,
        **snapshot_provenance(roadmap),
    )


def run_loop(
    repo: Path,
    roadmap: Path,
    phase: str | None = None,
    max_phases: int = 1,
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
) -> tuple[StateSnapshot, list[LaunchResult]]:
    if closeout_mode not in CLOSEOUT_MODES:
        raise ValueError(f"invalid closeout mode: {closeout_mode}")
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
    effective_pipeline_mode = pipeline_mode or os.environ.get("PHASE_LOOP_PIPELINE_MODE")
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
    with loop_context:
        for _ in range(max_phases):
            snapshot = reconcile(repo, roadmap)
            classifications = snapshot.phases
            alias = _select_ready_phase(repo, roadmap, classifications, phase)
            if alias is None:
                current = None
                break
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
            if dry_run and results and alias == current:
                break
            current = alias
            status = classifications.get(alias, "unknown")
            plan = find_plan_artifact(repo, alias, roadmap=roadmap)
            stale_pipeline_plan = _stale_pipeline_plan_candidate(repo, roadmap, alias) if plan is None else None
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
            else:
                repair_context = None
                if explicit_product_action in {"roadmap", "plan", "execute", "review"}:
                    launch_action = explicit_product_action
                else:
                    launch_action = "execute" if status in {"planned", "executed"} and plan is not None else "plan"
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
            plan_dispatch_hints = (
                dispatch_hints_for_action(parse_dispatch_hints(plan, kind="plan"), launch_action) if plan is not None else None
            )
            roadmap_dispatch_hints = dispatch_hints_for_action(parse_dispatch_hints(roadmap, kind="roadmap"), launch_action)
            plan_execution_policy_doc = parse_execution_policy(plan, kind="plan") if plan is not None else None
            roadmap_execution_policy_doc = parse_execution_policy(roadmap, kind="roadmap")
            policy_parse_error = (
                (plan_execution_policy_doc.parse_error if plan_execution_policy_doc else None)
                or roadmap_execution_policy_doc.parse_error
            )
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
            classifications[alias] = status_after_launch
            event_blocker = None
            child_automation: dict[str, object] = {}
            post_launch_plan = find_plan_artifact(repo, alias, roadmap=roadmap)
            if not dry_run:
                child_automation = _parsed_child_automation(result, spec)
                if failed_launch_closeout_override and child_automation:
                    child_automation["failed_launch_closeout_override"] = failed_launch_closeout_override
                    child_automation["original_returncode"] = failed_launch_closeout_override.get("original_returncode")
                automation_status = child_automation.get("automation_status")
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
                        classifications[alias] = status_after_launch
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
                        classifications[alias] = status_after_launch
                        event_blocker = {
                            "human_required": False,
                            "blocker_class": "repeated_verification_failure",
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
                                classifications[alias] = status_after_launch
                                automation_status = status_after_launch
                            else:
                                status_after_launch = "blocked"
                                classifications[alias] = status_after_launch
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
                        classifications[alias] = status_after_launch
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
                classifications[alias] = status_after_launch
            if post_launch_plan is not None:
                plan = post_launch_plan

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
                    if not match or match.group(1).lower() != alias.lower():
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
                classifications[alias] = status_after_launch
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
                else:
                    status_after_launch, event_blocker = _dirty_outcome(
                        dirty_summary,
                        blocked_summary="Phase reported complete but left dirty paths that are not closeout-safe.",
                    )
                classifications[alias] = status_after_launch
            elif plan_dirty_paths:
                dirty_summary = _classify_dirty_paths(repo, roadmap, plan, pre_launch_dirty_paths, plan_dirty_paths)
                status_after_launch, event_blocker = _dirty_outcome(
                    dirty_summary,
                    blocked_summary="Phase planning turn produced dirty paths that are not closeout-safe.",
                )
                classifications[alias] = status_after_launch
            elif blocked_plan_dirty_paths:
                plan_dirty_paths = blocked_plan_dirty_paths
                dirty_summary = _classify_dirty_paths(repo, roadmap, plan, pre_launch_dirty_paths, plan_dirty_paths)
                status_after_launch, event_blocker = _dirty_outcome(
                    dirty_summary,
                    blocked_summary="Phase planning turn reported a stale or non-human blocker and produced dirty paths that are not closeout-safe.",
                )
                classifications[alias] = status_after_launch
            elif incomplete_execute_dirty_paths:
                dirty_summary = _classify_dirty_paths(repo, roadmap, plan, pre_launch_dirty_paths, incomplete_execute_dirty_paths)
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
                classifications[alias] = status_after_launch
            elif (
                launch_action == "execute"
                and status_after_launch == "blocked"
                and _optional_automation_literal(child_automation.get("automation_blocker_class")) == "dirty_worktree_conflict"
                and child_automation.get("automation_verification_status") == "passed"
            ):
                verified_dirty_paths = _dirty_paths(repo)
                if verified_dirty_paths:
                    dirty_summary = _classify_dirty_paths(repo, roadmap, plan, pre_launch_dirty_paths, verified_dirty_paths)
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
                    classifications[alias] = status_after_launch
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
    terminal_summary = build_terminal_summary(
            terminal_status=terminal_status,
            terminal_blocker=terminal_blocker,
            verification_status=verification_status,
            next_action=next_action,
            dirty_paths=dirty_summary.get("dirty_paths", completion_dirty_paths or plan_dirty_paths or incomplete_execute_dirty_paths),
            phase_owned_dirty=bool(dirty_summary.get("phase_owned_dirty", False)),
            phase_owned_dirty_paths=dirty_summary.get("phase_owned_dirty_paths", ()),
            unowned_dirty_paths=dirty_summary.get("unowned_dirty_paths", ()),
            pre_existing_dirty_paths=dirty_summary.get("pre_existing_dirty_paths", ()),
            artifact_paths=artifact_paths,
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
        "unowned_dirty_paths": unowned_dirty_paths,
        "pre_existing_dirty_paths": pre_existing_dirty_paths,
        "phase_owned_dirty": phase_owned_dirty,
        "closeout_summary": snapshot.closeout_summary or {},
        "artifact_paths": _latest_phase_artifacts(repo, phase),
    }
    return (context if not missing else None), missing


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
    dirty_summary = _classify_dirty_paths(repo, roadmap, plan_for_ownership, [], dirty_paths)
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


def _persist_terminal_summary(artifacts: dict[str, Path], summary: dict[str, object]) -> dict[str, object]:
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
    parsed = parse_automation_status(text)
    if text and parsed:
        parsed["raw_output_excerpt"] = text[:1000]
        delegation_request = _parse_delegation_request(text)
        if delegation_request is not None:
            parsed["delegation_request"] = delegation_request
    _annotate_automation_parse_error(parsed, _executor_display_name(spec.executor), spec.prompt_bundle.workflow_command)
    return parsed


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
    if result.returncode != 0 or result.timed_out or result.interrupted:
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
) -> dict[str, object]:
    ownership = parse_plan_ownership(repo, roadmap, plan)
    pre_launch = set(pre_launch_dirty_paths)
    phase_owned = [path for path in post_launch_dirty_paths if ownership.matches(path)]
    phase_owned_set = set(phase_owned)

    rename_map = _detect_dirty_renames(repo)
    rename_sources_promoted: list[str] = []
    for src, dst in rename_map.items():
        if src in phase_owned_set:
            continue
        if src not in post_launch_dirty_paths:
            continue
        if dst in phase_owned_set or ownership.matches(dst):
            phase_owned.append(src)
            phase_owned_set.add(src)
            rename_sources_promoted.append(src)

    pre_existing = [
        path
        for path in post_launch_dirty_paths
        if path in pre_launch
        and path not in ownership.control_paths
        and not (allow_pre_existing_phase_owned and path in phase_owned_set)
    ]
    unowned = [path for path in post_launch_dirty_paths if path not in phase_owned_set]
    control_only_dirty = bool(post_launch_dirty_paths) and all(path in ownership.control_paths for path in post_launch_dirty_paths)
    return {
        "dirty_paths": post_launch_dirty_paths,
        "phase_owned_dirty_paths": phase_owned,
        "unowned_dirty_paths": unowned,
        "pre_existing_dirty_paths": pre_existing,
        "phase_owned_dirty": (ownership.valid or control_only_dirty) and not pre_existing and not unowned and bool(post_launch_dirty_paths),
        "ownership_errors": [] if control_only_dirty else list(ownership.errors),
        "rename_sources_promoted": rename_sources_promoted,
    }


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
    if not snapshot.phase_owned_dirty or not snapshot.phase_owned_dirty_paths:
        status = "blocked"
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
        _git(repo, "add", "--", *snapshot.phase_owned_dirty_paths)
        _git(repo, "commit", "-m", f"phase-loop closeout: {phase}")
        commit = _git_output(repo, "rev-parse", "HEAD")
        status = "planned" if terminal_status == "planned" else "complete"
        metadata["closeout"]["verification_status"] = "not_run" if status == "planned" else "passed"
        metadata["closeout"].update(
            {
                "closeout_action": "commit",
                "closeout_commit": commit,
            }
        )
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

    changed_paths = snapshot.phase_owned_dirty_paths if not override_phase or override_phase == snapshot.current_phase else ()
    if not changed_paths and plan is not None:
        dirty_summary = _classify_dirty_paths(repo, roadmap, plan, [], _dirty_paths(repo))
        changed_paths = tuple(dirty_summary.get("phase_owned_dirty_paths", ()))

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
