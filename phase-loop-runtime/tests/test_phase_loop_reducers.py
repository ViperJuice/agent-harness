import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.models import WorkUnitCloseout, WorkUnitIdentity, WorkUnitState
from phase_loop_runtime.closeout import build_phase_loop_closeout
from phase_loop_runtime.runner import (
    record_work_unit_closeout,
    select_phase_reducer_work_unit,
    select_review_work_units,
)
from phase_loop_runtime.state import write_work_unit_state
from phase_loop_test_utils import make_repo, write_phase_plan


def _plan_body(include_review_policy: bool = True) -> str:
    policy = (
        "## Execution Policy\n\n"
        "- review: executor=`codex`, effort=`high`, work-unit=`lane_review`, unsupported=`inherit_default`, inherit-default=`true`\n"
        "- SL-2: executor=`codex`, effort=`high`, work-unit=`phase_reducer`, unsupported=`inherit_default`, inherit-default=`true`\n\n"
        if include_review_policy
        else ""
    )
    return (
        "# REDUCEVERIFY\n\n"
        "## Lane Index & Dependencies\n\n"
        "- SL-0 - One; Depends on: (none); Blocks: SL-2; Parallel-safe: no\n"
        "- SL-1 - Two; Depends on: (none); Blocks: SL-2; Parallel-safe: no\n"
        "- SL-2 - Acceptance reducer; Depends on: SL-0, SL-1; Blocks: (none); Parallel-safe: no\n\n"
        "## Lanes\n\n"
        "### SL-0 - One\n"
        "- **Owned files**: `one.py`\n"
        "- **Interfaces provided**: `one.out`\n\n"
        "### SL-1 - Two\n"
        "- **Owned files**: `two.py`\n"
        "- **Interfaces provided**: `two.out`\n\n"
        "### SL-2 - Acceptance reducer\n"
        "- **Owned files**: none\n"
        "- **Interfaces consumed**: `one.out`, `two.out`\n\n"
        f"{policy}"
    )


class PhaseLoopReducersTest(unittest.TestCase):
    def test_selected_lane_reviews_run_after_each_implementation_lane(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "REDUCEVERIFY", roadmap, body=_plan_body())
            write_work_unit_state(
                repo,
                WorkUnitState(
                    identity=WorkUnitIdentity(phase="REDUCEVERIFY", kind="lane_execute", lane_id="SL-0", attempt=1),
                    status="complete",
                ),
                roadmap=roadmap,
            )

            reviews = select_review_work_units(repo, plan, "REDUCEVERIFY")

            self.assertEqual([review.work_unit_id for review in reviews], ["REDUCEVERIFY.lane_review.SL-0.1"])
            self.assertEqual(reviews[0].policy["work_unit_kind"], "lane_review")

    def test_policy_absent_reviews_are_skipped_explicitly(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "REDUCEVERIFY", roadmap, body=_plan_body(include_review_policy=False))
            write_work_unit_state(
                repo,
                WorkUnitState(
                    identity=WorkUnitIdentity(phase="REDUCEVERIFY", kind="lane_execute", lane_id="SL-0", attempt=1),
                    status="complete",
                ),
                roadmap=roadmap,
            )

            self.assertEqual(select_review_work_units(repo, plan, "REDUCEVERIFY"), ())

    def test_phase_reducer_waits_for_every_consumed_producer_and_reviews(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "REDUCEVERIFY", roadmap, body=_plan_body())
            write_work_unit_state(
                repo,
                WorkUnitState(
                    identity=WorkUnitIdentity(phase="REDUCEVERIFY", kind="lane_execute", lane_id="SL-0", attempt=1),
                    status="complete",
                ),
                roadmap=roadmap,
            )

            self.assertIsNone(select_phase_reducer_work_unit(repo, plan, "REDUCEVERIFY"))

            for lane_id in ("SL-1",):
                write_work_unit_state(
                    repo,
                    WorkUnitState(
                        identity=WorkUnitIdentity(phase="REDUCEVERIFY", kind="lane_execute", lane_id=lane_id, attempt=1),
                        status="complete",
                    ),
                    roadmap=roadmap,
                )
            write_work_unit_state(
                repo,
                WorkUnitState(
                    identity=WorkUnitIdentity(phase="REDUCEVERIFY", kind="lane_review", lane_id="SL-0", attempt=1),
                    status="blocked",
                    blocker={"blocker_class": "repeated_verification_failure"},
                ),
                roadmap=roadmap,
            )
            self.assertIsNone(select_phase_reducer_work_unit(repo, plan, "REDUCEVERIFY"))

            record_work_unit_closeout(
                repo,
                roadmap,
                WorkUnitCloseout(
                    identity=WorkUnitIdentity(phase="REDUCEVERIFY", kind="lane_review", lane_id="SL-0", attempt=1),
                    status="complete",
                ),
            )
            reducer = select_phase_reducer_work_unit(repo, plan, "REDUCEVERIFY")

            self.assertEqual(reducer.work_unit_id, "REDUCEVERIFY.phase_reducer.SL-2.1")

    def test_phase_closeout_includes_lane_identity_and_redacted_evidence_refs(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "REDUCEVERIFY", roadmap, body=_plan_body())
            closeout = build_phase_loop_closeout(
                phase_alias="REDUCEVERIFY",
                plan_path=plan,
                terminal_summary={"terminal_status": "complete", "verification_status": "passed"},
                automation={"status": "complete", "verification_status": "passed", "human_required": False},
                changed_paths=("one.py",),
                work_unit_closeout=WorkUnitCloseout(
                    identity=WorkUnitIdentity(phase="REDUCEVERIFY", kind="lane_execute", lane_id="SL-0", attempt=1),
                    status="complete",
                    wave_id="wave-001",
                    worktree_path=str(repo),
                    changed_paths=("one.py",),
                    verification_status="passed",
                    evidence_refs=({"path": ".phase-loop/runs/run-1/terminal-summary.json", "sha256": "a" * 64},),
                ),
            )

            self.assertEqual(closeout["lane"]["lane_id"], "SL-0")
            self.assertEqual(closeout["lane"]["wave_id"], "wave-001")
            self.assertEqual(closeout["lane"]["worktree_path"], str(repo))
            self.assertEqual(closeout["lane"]["changed_paths"], ["one.py"])
            self.assertEqual(closeout["lane"]["evidence_refs"][0]["path"], ".phase-loop/runs/run-1/terminal-summary.json")
            self.assertNotIn("transcript", closeout["lane"]["evidence_refs"][0])


if __name__ == "__main__":
    unittest.main()
