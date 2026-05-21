from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path

from .models import (
    LaneIRDiagnostic,
    LaneWave,
    LaneWaveDecision,
    LaneWorktreeAssignment,
    PhasePlanIR,
    PhasePlanLane,
    WorkUnitState,
)
from .runtime_paths import lane_worktree_path


COMPLETE_WORK_UNIT_STATUSES = {"complete", "skipped"}


def build_lane_wave_plan(
    ir: PhasePlanIR,
    work_units: dict[str, WorkUnitState] | None = None,
    *,
    mode: str = "serialized",
    assignments: tuple[LaneWorktreeAssignment, ...] = (),
) -> tuple[LaneWaveDecision, ...]:
    decisions: list[LaneWaveDecision] = []
    remaining = tuple(ir.lanes)
    completed = set(_completed_lane_ids(ir, work_units or {}))
    while True:
        decision = select_ready_lane_wave(
            ir,
            work_units or {},
            mode=mode,
            completed_lane_ids=tuple(completed),
            assignments=assignments,
        )
        decisions.append(decision)
        if decision.status != "ready" or decision.ready_wave is None:
            break
        completed.update(decision.ready_wave.lane_ids)
        if not [lane for lane in remaining if lane.lane_id not in completed]:
            break
    return tuple(decisions)


def select_ready_lane_wave(
    ir: PhasePlanIR,
    work_units: dict[str, WorkUnitState] | None = None,
    *,
    mode: str = "serialized",
    completed_lane_ids: tuple[str, ...] = (),
    assignments: tuple[LaneWorktreeAssignment, ...] = (),
    expected_base_sha: str | None = None,
) -> LaneWaveDecision:
    if mode not in {"serialized", "concurrent"}:
        raise ValueError(f"invalid lane scheduler mode: {mode}")
    if ir.diagnostics:
        return LaneWaveDecision(status="blocked", mode=mode, diagnostics=ir.diagnostics)
    work_units = work_units or {}
    completed = set(completed_lane_ids) | set(_completed_lane_ids(ir, work_units))
    blocked = _blocked_lane_ids(ir, work_units)
    active = _active_lane_ids(ir, work_units)
    ready = [
        lane
        for lane in ir.lanes
        if lane.lane_id not in completed
        and lane.lane_id not in blocked
        and _dependencies_complete(lane, completed)
        and lane.lane_id not in active
    ]
    pending = tuple(lane.lane_id for lane in ir.lanes if lane.lane_id not in completed)
    if not ready:
        status = "empty" if not pending else "blocked"
        return LaneWaveDecision(
            status=status,
            mode=mode,
            pending_lane_ids=pending,
            completed_lane_ids=tuple(_stable_lane_order(ir, completed)),
            blocked_lane_ids=tuple(_stable_lane_order(ir, blocked)),
            diagnostics=_work_unit_blocking_diagnostics(ir, active=active, blocked=blocked),
        )
    selected = ready[:1] if mode == "serialized" else _concurrent_ready_lanes(ready)
    diagnostics = (
        validate_concurrent_lane_ownership(selected, assignments=assignments, expected_base_sha=expected_base_sha)
        if mode == "concurrent"
        else ()
    )
    if diagnostics:
        return LaneWaveDecision(
            status="blocked",
            mode=mode,
            pending_lane_ids=pending,
            completed_lane_ids=tuple(_stable_lane_order(ir, completed)),
            blocked_lane_ids=tuple(_stable_lane_order(ir, blocked)),
            diagnostics=diagnostics,
        )
    wave_id = f"wave-{len(completed) + 1:03d}"
    wave_assignments = _assignments_for_lanes(selected, assignments)
    return LaneWaveDecision(
        status="ready",
        mode=mode,
        ready_wave=LaneWave(
            wave_id=wave_id,
            lane_ids=tuple(lane.lane_id for lane in selected),
            mode=mode,
            assignments=wave_assignments,
        ),
        pending_lane_ids=pending,
        completed_lane_ids=tuple(_stable_lane_order(ir, completed)),
        blocked_lane_ids=tuple(_stable_lane_order(ir, blocked)),
    )


def validate_concurrent_lane_ownership(
    lanes: tuple[PhasePlanLane, ...] | list[PhasePlanLane],
    *,
    assignments: tuple[LaneWorktreeAssignment, ...] = (),
    expected_base_sha: str | None = None,
) -> tuple[LaneIRDiagnostic, ...]:
    diagnostics: list[LaneIRDiagnostic] = []
    writers = [lane for lane in lanes if not lane.read_only]
    assignment_by_lane = {assignment.lane_id: assignment for assignment in assignments}
    if len(writers) > 1:
        for lane in writers:
            assignment = assignment_by_lane.get(lane.lane_id)
            if assignment is None or assignment.isolation_mode != "git_worktree":
                diagnostics.append(
                    LaneIRDiagnostic(
                        kind="unsafe_concurrent_lane",
                        lane_id=lane.lane_id,
                        message=f"{lane.lane_id} cannot launch concurrently without an isolated git worktree assignment",
                        details={"lane_id": lane.lane_id},
                    )
                )
            elif expected_base_sha and assignment.base_sha and assignment.base_sha != expected_base_sha:
                diagnostics.append(
                    LaneIRDiagnostic(
                        kind="stale_worktree_assignment",
                        lane_id=lane.lane_id,
                        message=f"{lane.lane_id} cannot launch concurrently from stale base SHA {assignment.base_sha}",
                        details={"lane_id": lane.lane_id, "base_sha": assignment.base_sha, "expected_base_sha": expected_base_sha},
                    )
                )
    for index, left in enumerate(writers):
        for right in writers[index + 1 :]:
            if _patterns_overlap_any(left.owned_files, right.owned_files):
                diagnostics.append(
                    LaneIRDiagnostic(
                        kind="overlapping_write_ownership",
                        lane_id=right.lane_id,
                        message=f"{left.lane_id} and {right.lane_id} cannot launch concurrently with overlapping owned files",
                        details={"left": left.lane_id, "right": right.lane_id},
                    )
                )
    return tuple(diagnostics)


def worktree_assignments_for_wave(
    repo: Path,
    lanes: tuple[PhasePlanLane, ...] | list[PhasePlanLane],
    *,
    branch: str,
    mode: str,
    base_sha: str | None = None,
) -> tuple[LaneWorktreeAssignment, ...]:
    if mode == "serialized":
        return tuple(
            LaneWorktreeAssignment(lane_id=lane.lane_id, worktree_path=str(repo), isolation_mode="main_worktree")
            for lane in lanes
        )
    return tuple(
        LaneWorktreeAssignment(
            lane_id=lane.lane_id,
            worktree_path=str(lane_worktree_path(repo, branch=branch, lane_id=lane.lane_id)),
            isolation_mode="git_worktree",
            branch=branch,
            base_sha=base_sha,
        )
        for lane in lanes
    )


def _completed_lane_ids(ir: PhasePlanIR, work_units: dict[str, WorkUnitState]) -> tuple[str, ...]:
    completed = {
        state.identity.lane_id
        for state in work_units.values()
        if state.identity.phase == str(ir.metadata.get("phase", "")).upper() and state.status in COMPLETE_WORK_UNIT_STATUSES
    }
    return tuple(_stable_lane_order(ir, completed))


def _blocked_lane_ids(ir: PhasePlanIR, work_units: dict[str, WorkUnitState]) -> set[str]:
    phase = str(ir.metadata.get("phase", "")).upper()
    return {
        state.identity.lane_id
        for state in work_units.values()
        if state.identity.phase == phase and state.status == "blocked" and state.human_required
    }


def _dependencies_complete(lane: PhasePlanLane, completed: set[str]) -> bool:
    return all(dependency in completed for dependency in lane.depends_on)


def _active_lane_ids(ir: PhasePlanIR, work_units: dict[str, WorkUnitState]) -> set[str]:
    phase = str(ir.metadata.get("phase", "")).upper()
    return {
        state.identity.lane_id
        for state in work_units.values()
        if state.identity.phase == phase and state.status in {"running", "awaiting-closeout"}
    }


def _work_unit_blocking_diagnostics(
    ir: PhasePlanIR,
    *,
    active: set[str],
    blocked: set[str],
) -> tuple[LaneIRDiagnostic, ...]:
    diagnostics: list[LaneIRDiagnostic] = []
    for lane_id in _stable_lane_order(ir, active):
        diagnostics.append(
            LaneIRDiagnostic(
                kind="active_work_unit",
                lane_id=lane_id,
                message=f"{lane_id} already has an active work unit",
                details={"lane_id": lane_id},
            )
        )
    for lane_id in _stable_lane_order(ir, blocked):
        diagnostics.append(
            LaneIRDiagnostic(
                kind="human_required_blocked_work_unit",
                lane_id=lane_id,
                message=f"{lane_id} has a human-required blocked work unit",
                details={"lane_id": lane_id},
            )
        )
    return tuple(diagnostics)


def _stable_lane_order(ir: PhasePlanIR, lane_ids: set[str]) -> list[str]:
    return [lane.lane_id for lane in ir.lanes if lane.lane_id in lane_ids]


def _concurrent_ready_lanes(ready: list[PhasePlanLane]) -> tuple[PhasePlanLane, ...]:
    if len(ready) <= 1:
        return tuple(ready)
    prefix = ready[0].lane_id.split("-", 1)[0]
    selected = [lane for lane in ready if lane.parallel_safe and lane.reducer_kind == "none" and lane.lane_id.startswith(prefix)]
    return tuple(selected or ready[:1])


def _assignments_for_lanes(
    lanes: tuple[PhasePlanLane, ...] | list[PhasePlanLane],
    assignments: tuple[LaneWorktreeAssignment, ...],
) -> tuple[LaneWorktreeAssignment, ...]:
    by_lane = {assignment.lane_id: assignment for assignment in assignments}
    return tuple(by_lane[lane.lane_id] for lane in lanes if lane.lane_id in by_lane)


def _patterns_overlap_any(left_patterns: tuple[str, ...], right_patterns: tuple[str, ...]) -> bool:
    return any(_patterns_overlap(left, right) for left in left_patterns for right in right_patterns)


def _patterns_overlap(left: str, right: str) -> bool:
    if left == right:
        return True
    left_has_glob = _has_glob(left)
    right_has_glob = _has_glob(right)
    if left_has_glob and fnmatchcase(right, left):
        return True
    if right_has_glob and fnmatchcase(left, right):
        return True
    if left_has_glob and right_has_glob:
        left_prefix = _pattern_prefix(left)
        right_prefix = _pattern_prefix(right)
        return bool(left_prefix and right_prefix and (left_prefix.startswith(right_prefix) or right_prefix.startswith(left_prefix)))
    return left.startswith(right.rstrip("/") + "/") or right.startswith(left.rstrip("/") + "/")


def _has_glob(pattern: str) -> bool:
    return any(token in pattern for token in ("*", "?", "["))


def _pattern_prefix(pattern: str) -> str:
    for index, char in enumerate(pattern):
        if char in "*?[":
            return pattern[:index].rstrip("/")
    return pattern.rstrip("/")
