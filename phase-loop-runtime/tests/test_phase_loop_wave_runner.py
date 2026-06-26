import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.cli import build_parser
from phase_loop_runtime.events import read_events
from phase_loop_runtime.runtime_paths import phase_loop_stop_file
import phase_loop_runtime.runner as runner_module
from phase_loop_runtime.runner import run_loop
from phase_loop_runtime.state import load_work_unit_state, write_work_unit_state
from phase_loop_runtime.models import WorkUnitIdentity, WorkUnitState
from phase_loop_test_utils import commit_fixture_paths, make_repo, write_phase_plan

import pytest

# TESTDECOUPLE SL-1 (overlay-dependent): builds a skill/adoption bundle or runs the
# runtime execute path, which resolves the dotfiles skill-source / profile overlay
# (claude-config/*, codex-config/* …) absent standalone. Run-time integration: the
# conftest hook skips it when no dotfiles tree is reachable.
pytestmark = pytest.mark.dotfiles_integration


def _wave_body() -> str:
    return (
        "# WAVESCHED\n\n"
        "## Lane Index & Dependencies\n\n"
        "- SL-0 - Producer; Depends on: (none); Blocks: SL-1; Parallel-safe: yes\n"
        "- SL-1 - Reducer; Depends on: SL-0; Blocks: (none); Parallel-safe: no\n\n"
        "## Lanes\n\n"
        "### SL-0 - Producer\n"
        "- **Owned files**: `producer.py`\n"
        "- **Interfaces provided**: `producer.out`\n"
        "- **Interfaces consumed**: none\n"
        "- **Parallel-safe**: yes\n\n"
        "### SL-1 - Reducer\n"
        "- **Owned files**: none\n"
        "- **Interfaces provided**: `done.out`\n"
        "- **Interfaces consumed**: `producer.out`\n"
        "- **Parallel-safe**: no\n"
    )


def _concurrent_wave_body() -> str:
    return (
        "# WAVESCHED\n\n"
        "## Lane Index & Dependencies\n\n"
        "- SL-0 - Producer A; Depends on: (none); Blocks: SL-2; Parallel-safe: yes\n"
        "- SL-1 - Producer B; Depends on: (none); Blocks: SL-2; Parallel-safe: yes\n"
        "- SL-2 - Reducer; Depends on: SL-0, SL-1; Blocks: (none); Parallel-safe: no\n\n"
        "## Lanes\n\n"
        "### SL-0 - Producer A\n"
        "- **Owned files**: `producer-a.py`\n"
        "- **Interfaces provided**: `producer-a.out`\n"
        "- **Interfaces consumed**: none\n"
        "- **Parallel-safe**: yes\n\n"
        "### SL-1 - Producer B\n"
        "- **Owned files**: `producer-b.py`\n"
        "- **Interfaces provided**: `producer-b.out`\n"
        "- **Interfaces consumed**: none\n"
        "- **Parallel-safe**: yes\n\n"
        "### SL-2 - Reducer\n"
        "- **Owned files**: none\n"
        "- **Interfaces provided**: `done.out`\n"
        "- **Interfaces consumed**: `producer-a.out`, `producer-b.out`\n"
        "- **Parallel-safe**: no\n"
    )


def _three_lane_concurrent_wave_body() -> str:
    return (
        "# WAVESCHED\n\n"
        "## Lane Index & Dependencies\n\n"
        "- SL-0 - Producer A; Depends on: (none); Blocks: SL-3; Parallel-safe: yes\n"
        "- SL-1 - Producer B; Depends on: (none); Blocks: SL-3; Parallel-safe: yes\n"
        "- SL-2 - Producer C; Depends on: (none); Blocks: SL-3; Parallel-safe: yes\n"
        "- SL-3 - Reducer; Depends on: SL-0, SL-1, SL-2; Blocks: (none); Parallel-safe: no\n\n"
        "## Lanes\n\n"
        "### SL-0 - Producer A\n"
        "- **Owned files**: `producer-a.py`\n"
        "- **Interfaces provided**: `producer-a.out`\n"
        "- **Interfaces consumed**: none\n"
        "- **Parallel-safe**: yes\n\n"
        "### SL-1 - Producer B\n"
        "- **Owned files**: `producer-b.py`\n"
        "- **Interfaces provided**: `producer-b.out`\n"
        "- **Interfaces consumed**: none\n"
        "- **Parallel-safe**: yes\n\n"
        "### SL-2 - Producer C\n"
        "- **Owned files**: `producer-c.py`\n"
        "- **Interfaces provided**: `producer-c.out`\n"
        "- **Interfaces consumed**: none\n"
        "- **Parallel-safe**: yes\n\n"
        "### SL-3 - Reducer\n"
        "- **Owned files**: none\n"
        "- **Interfaces provided**: `done.out`\n"
        "- **Interfaces consumed**: `producer-a.out`, `producer-b.out`, `producer-c.out`\n"
        "- **Parallel-safe**: no\n"
    )


class PhaseLoopWaveRunnerTest(unittest.TestCase):
    def test_dfparsoak_fixture_launches_governed_pipeline_style_three_lane_wave(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.write_text("# Roadmap\n\n### Phase 0 - Parallel Soak (DFPARSOAK)\n", encoding="utf-8")
            fixture = Path(__file__).resolve().parent / "fixtures" / "phase_loop_soak" / "dfparsoak_wave_plan.md"
            plan = write_phase_plan(repo, "DFPARSOAK", roadmap, body=fixture.read_text(encoding="utf-8"))
            commit_fixture_paths(repo, "dfparsoak wave fixture", roadmap, plan)

            snapshot, results = run_loop(
                repo,
                roadmap,
                phase="DFPARSOAK",
                dry_run=True,
                lane_scheduler_mode="concurrent",
            )
            work_units = load_work_unit_state(repo)
            events = read_events(repo)

            self.assertEqual(results, [])
            self.assertEqual(snapshot.phases["DFPARSOAK"], "executing")
            self.assertEqual(
                tuple(work_units),
                (
                    "DFPARSOAK.lane_execute.SL-0.1",
                    "DFPARSOAK.lane_execute.SL-1.1",
                    "DFPARSOAK.lane_execute.SL-2.1",
                ),
            )
            event = events[-1]
            self.assertEqual(event["metadata"]["lane_scheduler"]["decision"]["ready_wave"]["wave_id"], "wave-001")
            self.assertEqual(len(event["metadata"]["lane_scheduler"]["launched_work_units"]), 3)
            self.assertEqual(event["metadata"]["lane_scheduler"]["decision"]["ready_wave"]["lane_ids"], ["SL-0", "SL-1", "SL-2"])

            seen_worktrees = set()
            seen_owned = set()
            for lane_id in ("SL-0", "SL-1", "SL-2"):
                state = work_units[f"DFPARSOAK.lane_execute.{lane_id}.1"]
                policy = state.policy
                assignment = policy["worktree_assignment"]
                self.assertEqual(policy["wave_id"], "wave-001")
                self.assertEqual(policy["executor"], "pi")
                self.assertTrue(policy["model"])
                self.assertTrue(policy["effort"])
                self.assertTrue(policy["dry_run"])
                self.assertEqual(assignment["lane_id"], lane_id)
                self.assertEqual(assignment["isolation_mode"], "git_worktree")
                self.assertTrue(assignment["base_sha"])
                self.assertNotIn(assignment["worktree_path"], seen_worktrees)
                seen_worktrees.add(assignment["worktree_path"])
                lane_owned = tuple(item for item in policy["worktree_assignment"].get("owned_files", ()))
                for path in state.artifacts:
                    self.assertNotIn("raw", path.lower())
                for owned in lane_owned:
                    self.assertNotIn(owned, seen_owned)
                    seen_owned.add(owned)

            serialized_event = json.dumps(event, sort_keys=True).lower()
            self.assertNotIn("provider-payload", serialized_event)
            self.assertNotIn("raw-transcript", serialized_event)

    def test_lane_scheduler_cli_mode_is_explicit(self):
        args = build_parser().parse_args(["--lane-scheduler", "serialized", "run"])
        default_args = build_parser().parse_args(["run"])

        self.assertEqual(args.lane_scheduler_mode, "serialized")
        self.assertIsNone(default_args.lane_scheduler_mode)

    def test_coarse_execution_remains_default_without_lane_scheduler(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, body=_wave_body())
            commit_fixture_paths(repo, "wave fixture", plan)

            _snapshot, results = run_loop(repo, roadmap, phase="RUNNER", dry_run=True)

            self.assertEqual(len(results), 1)
            self.assertFalse(load_work_unit_state(repo))

    def test_serialized_lane_scheduler_launches_one_ready_work_unit(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, body=_wave_body())
            commit_fixture_paths(repo, "wave fixture", plan)

            snapshot, results = run_loop(
                repo,
                roadmap,
                phase="RUNNER",
                dry_run=True,
                lane_scheduler_mode="serialized",
            )
            work_units = load_work_unit_state(repo)
            events = read_events(repo)

            self.assertEqual(results, [])
            self.assertEqual(snapshot.phases["RUNNER"], "executing")
            self.assertEqual(tuple(work_units), ("RUNNER.lane_execute.SL-0.1",))
            self.assertEqual(events[-1]["metadata"]["lane_scheduler"]["decision"]["ready_wave"]["lane_ids"], ["SL-0"])

    def test_concurrent_lane_scheduler_launches_ready_wave_work_units(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, body=_concurrent_wave_body())
            commit_fixture_paths(repo, "wave fixture", plan)

            snapshot, results = run_loop(
                repo,
                roadmap,
                phase="RUNNER",
                dry_run=True,
                lane_scheduler_mode="concurrent",
            )
            work_units = load_work_unit_state(repo)
            events = read_events(repo)

            self.assertEqual(results, [])
            self.assertEqual(snapshot.phases["RUNNER"], "executing")
            self.assertEqual(tuple(work_units), ("RUNNER.lane_execute.SL-0.1", "RUNNER.lane_execute.SL-1.1"))
            self.assertEqual(events[-1]["metadata"]["lane_scheduler"]["decision"]["ready_wave"]["lane_ids"], ["SL-0", "SL-1"])
            for state in work_units.values():
                self.assertEqual(state.policy["mode"], "concurrent")
                self.assertEqual(state.policy["wave_id"], "wave-001")
                self.assertEqual(state.policy["executor"], "pi")
                self.assertEqual(state.policy["worktree_assignment"]["isolation_mode"], "git_worktree")
                self.assertTrue(state.policy["worktree_assignment"]["base_sha"])

    def test_concurrent_lane_scheduler_launches_three_ready_wave_work_units(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, body=_three_lane_concurrent_wave_body())
            commit_fixture_paths(repo, "three-lane wave fixture", plan)

            snapshot, results = run_loop(
                repo,
                roadmap,
                phase="RUNNER",
                dry_run=True,
                lane_scheduler_mode="concurrent",
            )
            work_units = load_work_unit_state(repo)
            events = read_events(repo)

            self.assertEqual(results, [])
            self.assertEqual(snapshot.phases["RUNNER"], "executing")
            self.assertEqual(
                tuple(work_units),
                ("RUNNER.lane_execute.SL-0.1", "RUNNER.lane_execute.SL-1.1", "RUNNER.lane_execute.SL-2.1"),
            )
            self.assertEqual(events[-1]["metadata"]["lane_scheduler"]["decision"]["ready_wave"]["lane_ids"], ["SL-0", "SL-1", "SL-2"])
            for state in work_units.values():
                self.assertEqual(state.policy["executor"], "pi")
                self.assertEqual(state.policy["mode"], "concurrent")
                self.assertEqual(state.policy["wave_id"], "wave-001")
                self.assertEqual(state.policy["worktree_assignment"]["isolation_mode"], "git_worktree")
                self.assertTrue(state.policy["worktree_assignment"]["base_sha"])

    def test_concurrent_lane_scheduler_stop_file_preserves_prior_launches(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, body=_concurrent_wave_body())
            commit_fixture_paths(repo, "wave fixture", plan)

            original_launch = runner_module.launch_work_unit_attempt

            def launch_then_stop(*args, **kwargs):
                state = original_launch(*args, **kwargs)
                phase_loop_stop_file(repo).parent.mkdir(parents=True, exist_ok=True)
                phase_loop_stop_file(repo).write_text("stop\n", encoding="utf-8")
                return state

            with patch("phase_loop_runtime.runner.launch_work_unit_attempt", side_effect=launch_then_stop):
                snapshot, results = run_loop(
                    repo,
                    roadmap,
                    phase="RUNNER",
                    dry_run=True,
                    lane_scheduler_mode="concurrent",
                )
            work_units = load_work_unit_state(repo)
            events = read_events(repo)

            self.assertEqual(results, [])
            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            self.assertEqual(tuple(work_units), ("RUNNER.lane_execute.SL-0.1",))
            self.assertTrue(events[-1]["metadata"]["lane_scheduler"]["stop_requested"])

    def test_completed_scheduler_lanes_route_to_phase_closeout(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, body=_wave_body())
            commit_fixture_paths(repo, "wave fixture", plan)
            write_work_unit_state(
                repo,
                WorkUnitState(
                    identity=WorkUnitIdentity(phase="RUNNER", kind="lane_execute", lane_id="SL-0", attempt=1),
                    status="complete",
                ),
                roadmap=roadmap,
            )
            write_work_unit_state(
                repo,
                WorkUnitState(
                    identity=WorkUnitIdentity(phase="RUNNER", kind="phase_reducer", lane_id="SL-1", attempt=1),
                    status="complete",
                ),
                roadmap=roadmap,
            )

            snapshot, _results = run_loop(repo, roadmap, phase="RUNNER", dry_run=True, lane_scheduler_mode="serialized")

            self.assertEqual(snapshot.phases["RUNNER"], "awaiting_phase_closeout")


if __name__ == "__main__":
    unittest.main()
