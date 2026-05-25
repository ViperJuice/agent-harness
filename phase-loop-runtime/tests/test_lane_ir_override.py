import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from phase_loop_runtime.cli import build_parser, main
from phase_loop_runtime.events import read_events
from phase_loop_runtime.reconcile import _plan_blocker
from phase_loop_test_utils import make_repo, provenanced_state, write_phase_plan


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
