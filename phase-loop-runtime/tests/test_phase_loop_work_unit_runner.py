import os
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.models import CommandAdapterConfig, HarnessLaneAssignment, WorkUnitCloseout, WorkUnitIdentity, WorkUnitState
from phase_loop_runtime.runner import (
    launch_harness_lane_work_unit,
    launch_work_unit_attempt,
    record_work_unit_closeout,
    reduce_harness_lane_closeout,
    resume_work_units,
    run_loop,
    select_next_work_unit,
)
from phase_loop_runtime.state import load_work_unit_state, write_work_unit_state
from phase_loop_test_utils import commit_fixture_paths, make_repo, write_phase_plan
from phase_loop_test_utils import assert_metadata_only_evidence_refs


def _lane_plan_body() -> str:
    return (
        "# RUNNER\n\n"
        "## Lane Index & Dependencies\n\n"
        "- SL-0 - Producer; Depends on: (none); Blocks: SL-1; Parallel-safe: no\n"
        "- SL-1 - Reducer; Depends on: SL-0; Blocks: (none); Parallel-safe: no\n\n"
        "## Lanes\n\n"
        "### SL-0 - Producer\n"
        "- **Owned files**: `producer.py`\n"
        "- **Interfaces provided**: `producer.out`\n"
        "- **Interfaces consumed**: none\n"
        "- **Parallel-safe**: no\n\n"
        "### SL-1 - Reducer\n"
        "- **Owned files**: none\n"
        "- **Interfaces provided**: `reducer.out`\n"
        "- **Interfaces consumed**: `producer.out`\n"
        "- **Parallel-safe**: no\n"
    )


class PhaseLoopWorkUnitRunnerTest(unittest.TestCase):
    def test_dfparsoak_harness_lane_launch_records_assignment_and_redacted_refs(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "DFPARSOAK", roadmap, body=_lane_plan_body())
            assignment = HarnessLaneAssignment(
                phase="DFPARSOAK",
                lane_id="SL-0",
                work_unit_kind="lane_execute",
                prompt_kind="implementation",
                owned_files=("producer.py",),
                consumed_interfaces=("DFPARSOAK-soak-input-contract",),
                execution_policy={"executor": "codex", "effort": "medium", "fallback_reason": "explicit-route-proof"},
                metadata={
                    "wave_id": "wave-001",
                    "redacted_evidence_refs": (
                        "phase-loop-run:dfparsoak-wave-001",
                        "log:redacted:sha256:" + "4" * 64,
                    ),
                },
            )

            launched = launch_harness_lane_work_unit(
                repo=repo,
                roadmap=roadmap,
                plan=plan,
                assignment=assignment,
                executor="codex",
                dry_run=True,
            )
            reduced = reduce_harness_lane_closeout(repo, roadmap, assignment)

            self.assertEqual(launched["terminal_summary"]["terminal_status"], "complete")
            self.assertEqual(launched["state"]["identity"]["lane_id"], "SL-0")
            self.assertEqual(launched["spec"]["harness_lane_assignment"]["metadata"]["wave_id"], "wave-001")
            self.assertEqual(reduced.status, "complete")
            assert_metadata_only_evidence_refs(self, assignment.metadata["redacted_evidence_refs"])

    def test_select_next_work_unit_skips_completed_dependencies(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, body=_lane_plan_body())

            first = select_next_work_unit(repo, plan, "RUNNER")
            self.assertEqual(first.work_unit_id, "RUNNER.lane_execute.SL-0.1")

            write_work_unit_state(repo, first.with_status("complete"), roadmap=roadmap)
            second = select_next_work_unit(repo, plan, "RUNNER")

            self.assertEqual(second.work_unit_id, "RUNNER.phase_reducer.SL-1.1")

    def test_resume_retries_nonhuman_blocked_and_preserves_human_blocker(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, body=_lane_plan_body())
            identity = WorkUnitIdentity(phase="RUNNER", kind="lane_execute", lane_id="SL-0", attempt=1)
            write_work_unit_state(
                repo,
                WorkUnitState(
                    identity=identity,
                    status="blocked",
                    blocker={"blocker_class": "repeated_verification_failure"},
                    human_required=False,
                ),
                roadmap=roadmap,
            )

            retried = resume_work_units(repo, roadmap, plan, "RUNNER")
            loaded = load_work_unit_state(repo)

            self.assertEqual(retried.work_unit_id, "RUNNER.lane_execute.SL-0.2")
            self.assertEqual(loaded["RUNNER.lane_execute.SL-0.1"].status, "superseded")
            self.assertEqual(loaded["RUNNER.lane_execute.SL-0.2"].retry_of, "RUNNER.lane_execute.SL-0.1")

            human = WorkUnitState(
                identity=WorkUnitIdentity(phase="RUNNER", kind="lane_execute", lane_id="SL-2", attempt=1),
                status="blocked",
                blocker={"blocker_class": "admin_approval"},
                human_required=True,
            )
            write_work_unit_state(repo, human, roadmap=roadmap)

            self.assertEqual(resume_work_units(repo, roadmap, plan, "RUNNER").work_unit_id, human.work_unit_id)

    def test_resume_supersedes_stale_running_attempt(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, body=_lane_plan_body())
            heartbeat = repo / ".phase-loop" / "runs" / "stale" / "heartbeat.json"
            heartbeat.parent.mkdir(parents=True)
            heartbeat.write_text("{}\n", encoding="utf-8")
            old = time.time() - 7200
            os.utime(heartbeat, (old, old))
            identity = WorkUnitIdentity(phase="RUNNER", kind="lane_execute", lane_id="SL-0", attempt=1)
            write_work_unit_state(
                repo,
                WorkUnitState(identity=identity, status="running", heartbeat_path=str(heartbeat)),
                roadmap=roadmap,
            )

            retried = resume_work_units(repo, roadmap, plan, "RUNNER", stale_heartbeat_seconds=60)

            self.assertEqual(retried.status, "running")
            self.assertEqual(retried.retry_of, "RUNNER.lane_execute.SL-0.1")

    def test_record_closeout_and_explicit_mode_do_not_use_coarse_phase_launch(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, body=_lane_plan_body())
            commit_fixture_paths(repo, "runner work-unit fixture", plan)
            launched = launch_work_unit_attempt(
                repo,
                roadmap,
                plan,
                WorkUnitIdentity(phase="RUNNER", kind="lane_execute", lane_id="SL-0", attempt=1),
            )
            closed = record_work_unit_closeout(
                repo,
                roadmap,
                WorkUnitCloseout(identity=launched.identity, status="complete", closeout_summary={"verification_status": "passed"}),
            )
            self.assertEqual(closed.status, "complete")

            snapshot, results = run_loop(repo, roadmap, phase="RUNNER", dry_run=True, work_unit_mode=True)

            self.assertEqual(results, [])
            self.assertEqual(snapshot.phases["RUNNER"], "executing")
            self.assertTrue(load_work_unit_state(repo))

    def test_fake_harness_lane_work_unit_launch_and_reducer_matrix(self):
        for executor in ("codex", "claude", "gemini", "opencode", "command"):
            with self.subTest(executor=executor):
                with tempfile.TemporaryDirectory() as td:
                    repo = make_repo(Path(td))
                    roadmap = repo / "specs" / "phase-plans-v1.md"
                    plan = write_phase_plan(repo, "HARNESSLANE", roadmap, body=_lane_plan_body())
                    assignment = HarnessLaneAssignment(
                        phase="HARNESSLANE",
                        lane_id="SL-0",
                        work_unit_kind="lane_execute",
                        prompt_kind="implementation",
                        owned_files=("producer.py",),
                        consumed_interfaces=("PhasePlanLane",),
                        execution_policy={"executor": executor, "effort": "medium"},
                    )

                    launched = launch_harness_lane_work_unit(
                        repo=repo,
                        roadmap=roadmap,
                        plan=plan,
                        assignment=assignment,
                        executor=executor,
                        dry_run=True,
                        command_adapter=(
                            CommandAdapterConfig(name="fake", template="fake --context {context_file}", delivery_mode="context_file")
                            if executor == "command"
                            else None
                        ),
                    )
                    reduced = reduce_harness_lane_closeout(repo, roadmap, assignment)

                    self.assertEqual(launched["spec"]["harness_lane_assignment"]["lane_id"], "SL-0")
                    self.assertEqual(launched["terminal_summary"]["work_unit"]["identity"]["lane_id"], "SL-0")
                    self.assertEqual(reduced.status, "complete")


if __name__ == "__main__":
    unittest.main()
