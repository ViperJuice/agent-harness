import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.lane_scheduler import select_ready_lane_wave, validate_concurrent_lane_ownership, worktree_assignments_for_wave
from phase_loop_runtime.models import LaneWorktreeAssignment, WorkUnitIdentity, WorkUnitState
from phase_loop_runtime.plan_ir import parse_phase_plan_ir
from phase_loop_test_utils import make_repo, write_phase_plan


def _fanout_body() -> str:
    return (
        "# WAVESCHED\n\n"
        "## Lane Index & Dependencies\n\n"
        "- SL-0 - A; Depends on: (none); Blocks: SL-2; Parallel-safe: yes\n"
        "- SL-1 - B; Depends on: (none); Blocks: SL-2; Parallel-safe: yes\n"
        "- SL-2 - Reducer; Depends on: SL-0, SL-1; Blocks: (none); Parallel-safe: no\n\n"
        "## Lanes\n\n"
        "### SL-0 - A\n"
        "- **Owned files**: `a.py`\n"
        "- **Interfaces provided**: `a.out`\n"
        "- **Interfaces consumed**: none\n"
        "- **Parallel-safe**: yes\n\n"
        "### SL-1 - B\n"
        "- **Owned files**: `b.py`\n"
        "- **Interfaces provided**: `b.out`\n"
        "- **Interfaces consumed**: none\n"
        "- **Parallel-safe**: yes\n\n"
        "### SL-2 - Reducer\n"
        "- **Owned files**: none\n"
        "- **Interfaces provided**: `done.out`\n"
        "- **Interfaces consumed**: `a.out`, `b.out`\n"
        "- **Parallel-safe**: no\n"
    )


def _three_lane_fanout_body() -> str:
    return (
        "# WAVESCHED\n\n"
        "## Lane Index & Dependencies\n\n"
        "- SL-0 - A; Depends on: (none); Blocks: SL-3; Parallel-safe: yes\n"
        "- SL-1 - B; Depends on: (none); Blocks: SL-3; Parallel-safe: yes\n"
        "- SL-2 - C; Depends on: (none); Blocks: SL-3; Parallel-safe: yes\n"
        "- SL-3 - Reducer; Depends on: SL-0, SL-1, SL-2; Blocks: (none); Parallel-safe: no\n\n"
        "## Lanes\n\n"
        "### SL-0 - A\n"
        "- **Owned files**: `a.py`\n"
        "- **Interfaces provided**: `a.out`\n"
        "- **Interfaces consumed**: none\n"
        "- **Parallel-safe**: yes\n\n"
        "### SL-1 - B\n"
        "- **Owned files**: `b.py`\n"
        "- **Interfaces provided**: `b.out`\n"
        "- **Interfaces consumed**: none\n"
        "- **Parallel-safe**: yes\n\n"
        "### SL-2 - C\n"
        "- **Owned files**: `c.py`\n"
        "- **Interfaces provided**: `c.out`\n"
        "- **Interfaces consumed**: none\n"
        "- **Parallel-safe**: yes\n\n"
        "### SL-3 - Reducer\n"
        "- **Owned files**: none\n"
        "- **Interfaces provided**: `done.out`\n"
        "- **Interfaces consumed**: `a.out`, `b.out`, `c.out`\n"
        "- **Parallel-safe**: no\n"
    )


class PhaseLoopLaneSchedulerTest(unittest.TestCase):
    def test_serialized_mode_selects_first_ready_lane_stably(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            plan = write_phase_plan(repo, "WAVESCHED", repo / "specs" / "phase-plans-v1.md", body=_fanout_body())
            ir = parse_phase_plan_ir(plan)

            decision = select_ready_lane_wave(ir, mode="serialized")

            self.assertEqual(decision.status, "ready")
            self.assertEqual(decision.ready_wave.lane_ids, ("SL-0",))
            self.assertEqual(decision.ready_wave.mode, "serialized")

    def test_concurrent_mode_requires_isolated_worktrees_for_multiple_writers(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            plan = write_phase_plan(repo, "WAVESCHED", repo / "specs" / "phase-plans-v1.md", body=_fanout_body())
            ir = parse_phase_plan_ir(plan)

            blocked = select_ready_lane_wave(ir, mode="concurrent")
            assignments = worktree_assignments_for_wave(repo, ir.lanes[:2], branch="main", mode="concurrent")
            ready = select_ready_lane_wave(ir, mode="concurrent", assignments=assignments)

            self.assertEqual(blocked.status, "blocked")
            self.assertEqual(blocked.diagnostics[0].kind, "unsafe_concurrent_lane")
            self.assertEqual(ready.status, "ready")
            self.assertEqual(ready.ready_wave.lane_ids, ("SL-0", "SL-1"))
            self.assertEqual(ready.ready_wave.assignments[0].isolation_mode, "git_worktree")
            self.assertEqual({item.lane_id for item in ready.ready_wave.assignments}, {"SL-0", "SL-1"})

    def test_concurrent_mode_selects_three_lane_wave_with_scheduler_owned_assignments(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            plan = write_phase_plan(repo, "WAVESCHED", repo / "specs" / "phase-plans-v1.md", body=_three_lane_fanout_body())
            ir = parse_phase_plan_ir(plan)
            assignments = worktree_assignments_for_wave(repo, ir.lanes[:3], branch="main", mode="concurrent", base_sha="base")

            decision = select_ready_lane_wave(ir, mode="concurrent", assignments=assignments, expected_base_sha="base")

            self.assertEqual(decision.status, "ready")
            self.assertEqual(decision.ready_wave.lane_ids, ("SL-0", "SL-1", "SL-2"))
            self.assertEqual(decision.ready_wave.mode, "concurrent")
            self.assertEqual({item.isolation_mode for item in decision.ready_wave.assignments}, {"git_worktree"})
            self.assertEqual({item.base_sha for item in decision.ready_wave.assignments}, {"base"})

    def test_serialized_mode_remains_compatible_without_scheduler_assignment(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            plan = write_phase_plan(repo, "WAVESCHED", repo / "specs" / "phase-plans-v1.md", body=_fanout_body())
            ir = parse_phase_plan_ir(plan)

            decision = select_ready_lane_wave(ir, mode="serialized", assignments=())

            self.assertEqual(decision.status, "ready")
            self.assertEqual(decision.ready_wave.lane_ids, ("SL-0",))
            self.assertEqual(decision.ready_wave.assignments, ())

    def test_dependency_completion_skips_done_lanes_and_releases_reducer(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            plan = write_phase_plan(repo, "WAVESCHED", repo / "specs" / "phase-plans-v1.md", body=_fanout_body())
            ir = parse_phase_plan_ir(plan)
            work_units = {
                "WAVESCHED.lane_execute.SL-0.1": WorkUnitState(
                    identity=WorkUnitIdentity(phase="WAVESCHED", kind="lane_execute", lane_id="SL-0", attempt=1),
                    status="complete",
                ),
                "WAVESCHED.lane_execute.SL-1.1": WorkUnitState(
                    identity=WorkUnitIdentity(phase="WAVESCHED", kind="lane_execute", lane_id="SL-1", attempt=1),
                    status="complete",
                ),
            }

            decision = select_ready_lane_wave(ir, work_units, mode="serialized")

            self.assertEqual(decision.ready_wave.lane_ids, ("SL-2",))

    def test_overlap_validation_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            body = _fanout_body().replace("`b.py`", "`a.py`")
            plan = write_phase_plan(repo, "WAVESCHED", repo / "specs" / "phase-plans-v1.md", body=body)
            ir = parse_phase_plan_ir(plan)

            diagnostics = validate_concurrent_lane_ownership(ir.lanes[:2])

            self.assertTrue(any(item.kind == "overlapping_write_ownership" for item in diagnostics))

    def test_concurrent_mode_blocks_stale_worktree_assignment_base(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            plan = write_phase_plan(repo, "WAVESCHED", repo / "specs" / "phase-plans-v1.md", body=_fanout_body())
            ir = parse_phase_plan_ir(plan)
            assignments = (
                LaneWorktreeAssignment("SL-0", str(repo.parent / "wt-0"), isolation_mode="git_worktree", base_sha="old"),
                LaneWorktreeAssignment("SL-1", str(repo.parent / "wt-1"), isolation_mode="git_worktree", base_sha="old"),
            )

            decision = select_ready_lane_wave(ir, mode="concurrent", assignments=assignments, expected_base_sha="new")

            self.assertEqual(decision.status, "blocked")
            self.assertTrue(any(item.kind == "stale_worktree_assignment" for item in decision.diagnostics))

    def test_active_and_human_blocked_units_emit_typed_diagnostics(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            plan = write_phase_plan(repo, "WAVESCHED", repo / "specs" / "phase-plans-v1.md", body=_fanout_body())
            ir = parse_phase_plan_ir(plan)
            work_units = {
                "WAVESCHED.lane_execute.SL-0.1": WorkUnitState(
                    identity=WorkUnitIdentity(phase="WAVESCHED", kind="lane_execute", lane_id="SL-0", attempt=1),
                    status="running",
                ),
                "WAVESCHED.lane_execute.SL-1.1": WorkUnitState(
                    identity=WorkUnitIdentity(phase="WAVESCHED", kind="lane_execute", lane_id="SL-1", attempt=1),
                    status="blocked",
                    human_required=True,
                ),
            }

            decision = select_ready_lane_wave(ir, work_units, mode="concurrent")

            self.assertEqual(decision.status, "blocked")
            self.assertEqual({item.kind for item in decision.diagnostics}, {"active_work_unit", "human_required_blocked_work_unit"})


if __name__ == "__main__":
    unittest.main()
