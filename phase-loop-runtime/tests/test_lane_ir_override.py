import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from phase_loop_runtime.cli import build_parser, main
from phase_loop_runtime.events import read_events
from phase_loop_runtime.models import StateSnapshot, utc_now
from phase_loop_runtime.profiles import resolve_profile
from phase_loop_runtime.provenance import snapshot_provenance
from phase_loop_runtime.reconcile import _plan_blocker
from phase_loop_runtime.runner import _perform_phase_closeout
from phase_loop_test_utils import commit_fixture_paths, make_repo, provenanced_state, write_phase_plan


OVERLAPPING_PLAN = """# RUNNER

## Lanes

### SL-0 - First
- **Owned files**: `shared.txt`
- **Interfaces provided**: first
- **Interfaces consumed**: none

### SL-1 - Second
- **Owned files**: `shared.txt`
- **Interfaces provided**: second
- **Interfaces consumed**: none
"""


class LaneIrOverrideTest(unittest.TestCase):
    def test_override_flag_is_hidden_when_exact_false(self):
        with patch.dict(os.environ, {"PHASE_LOOP_ALLOW_LANE_IR_OVERRIDE": "false"}, clear=True):
            with self.assertRaises(SystemExit):
                build_parser().parse_args(["run", "--allow-lane-ir-override", "overlapping_write_ownership"])

    def test_override_flag_is_visible_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            parsed = build_parser().parse_args(["run", "--allow-lane-ir-override", "overlapping_write_ownership"])

        self.assertEqual(parsed.allow_lane_ir_override, "overlapping_write_ownership")

    def test_override_requires_reason_when_enabled(self):
        with patch.dict(os.environ, {"PHASE_LOOP_ALLOW_LANE_IR_OVERRIDE": "true"}, clear=True), tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap, body=OVERLAPPING_PLAN)
            with self.assertRaises(SystemExit):
                main(
                    [
                        "run",
                        "--repo",
                        str(repo),
                        "--roadmap",
                        str(roadmap),
                        "--phase",
                        "RUNNER",
                        "--allow-lane-ir-override",
                        "overlapping_write_ownership",
                    ]
                )

    def test_override_rejects_unknown_diagnostic_kind(self):
        with patch.dict(os.environ, {"PHASE_LOOP_ALLOW_LANE_IR_OVERRIDE": "true"}, clear=True), tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap, body=OVERLAPPING_PLAN)
            with self.assertRaises(SystemExit):
                main(
                    [
                        "run",
                        "--repo",
                        str(repo),
                        "--roadmap",
                        str(roadmap),
                        "--phase",
                        "RUNNER",
                        "--allow-lane-ir-override",
                        "not_a_diagnostic",
                        "--reason",
                        "operator inspected fixture",
                    ]
                )

    def test_non_overridden_lane_ir_diagnostic_still_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap, body=OVERLAPPING_PLAN)

            blocker = _plan_blocker(repo, roadmap, "RUNNER")

            self.assertEqual(blocker["blocker_class"], "contract_bug")
            self.assertEqual(blocker["lane_ir_diagnostics"][0]["kind"], "overlapping_write_ownership")

    def test_lane_ir_blocker_summary_names_diagnostic_and_plan_location(self):
        """#52: the failed-closed summary must name the concrete diagnostic
        (kind@lane + message) and the plan file, so the operator can repair the
        plan without guessing — not the old opaque generic string."""
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap, body=OVERLAPPING_PLAN)

            blocker = _plan_blocker(repo, roadmap, "RUNNER")
            summary = blocker["blocker_summary"]

            self.assertIn("overlapping_write_ownership", summary)  # the concrete diagnostic kind
            self.assertIn("SL-", summary)                          # the failing lane id
            self.assertIn("RUNNER", summary)                       # the phase
            self.assertIn(".md", summary)                          # the plan file location
            self.assertNotEqual(
                summary,
                "Lane IR diagnostics failed closed for the current phase plan.",
            )

    def test_closeout_on_lane_ir_invalid_plan_reports_contract_bug_not_missing_owned(self):
        # OWNFIX #17: when the plan's Lane IR is invalid (here: overlapping write
        # ownership), the CLOSEOUT must surface the contract_bug naming the failing
        # lane/diagnostic, not the misleading missing_phase_owned_dirty_paths.
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, body=OVERLAPPING_PLAN)
            commit_fixture_paths(repo, "add invalid RUNNER plan", plan)
            (repo / "shared.txt").write_text("phase output\n", encoding="utf-8")

            snapshot = StateSnapshot(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phases={"RUNNER": "awaiting_phase_closeout"},
                current_phase="RUNNER",
                phase_owned_dirty=False,
                phase_owned_dirty_paths=(),
                dirty_paths=("shared.txt",),
                closeout_terminal_status="complete",
                **snapshot_provenance(roadmap),
            )

            status, event = _perform_phase_closeout(
                repo,
                roadmap,
                "RUNNER",
                snapshot,
                resolve_profile("execute"),
                action="execute",
                closeout_mode="commit",
            )

            self.assertEqual(status, "blocked")
            self.assertIsNotNone(event.blocker)
            self.assertEqual(event.blocker["blocker_class"], "contract_bug")
            self.assertEqual(
                event.blocker["lane_ir_diagnostics"][0]["kind"], "overlapping_write_ownership"
            )
            # The operator-facing message names the specific failing diagnostic.
            self.assertIn("overlapping_write_ownership", event.blocker["blocker_summary"])
            # And it must NOT be the misleading dirty-path-classification refusal.
            self.assertNotEqual(
                event.metadata["closeout"].get("closeout_refusal_reason"),
                "missing_phase_owned_dirty_paths",
            )

    def test_override_event_filters_matching_lane_ir_diagnostic(self):
        with patch.dict(os.environ, {"PHASE_LOOP_ALLOW_LANE_IR_OVERRIDE": "true"}, clear=True), tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, body=OVERLAPPING_PLAN)

            def fake_run_loop(**kwargs):
                return provenanced_state(repo, roadmap, {"RUNNER": "planned"}), []

            with patch("phase_loop_runtime.cli.run_loop", side_effect=fake_run_loop), patch("phase_loop_runtime.cli.render_status", return_value="status"):
                self.assertEqual(
                    main(
                        [
                            "run",
                            "--repo",
                            str(repo),
                            "--roadmap",
                            str(roadmap),
                            "--phase",
                            "RUNNER",
                            "--allow-lane-ir-override",
                            "overlapping_write_ownership",
                            "--reason",
                            "operator inspected fixture",
                        ]
                    ),
                    0,
                )

            self.assertEqual(_plan_blocker(repo, roadmap, "RUNNER"), {})
            event = read_events(repo)[-1]
            payload = event["metadata"]["runner.lane_ir_override_invoked"]
            self.assertEqual(payload["diagnostic_kinds_overridden"], ["overlapping_write_ownership"])
            self.assertEqual(payload["plan_path"], str(plan))
            self.assertEqual(payload["operator_reason"], "operator inspected fixture")


if __name__ == "__main__":
    unittest.main()
