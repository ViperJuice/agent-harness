import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.events import read_events
from phase_loop_runtime.launcher import LaunchResult
from phase_loop_runtime.runner import run_loop
from phase_loop_test_utils import build_fake_automation_output, commit_fixture_paths, make_repo, write_phase_plan


class PhaseLoopDryRunThenRunTest(unittest.TestCase):
    def test_dry_run_then_run_does_not_replay_dry_run_terminal_status_as_closeout(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            commit_fixture_paths(repo, "add runner plan", plan)

            dry_snapshot, dry_results = run_loop(repo, roadmap, phase="RUNNER", dry_run=True)

            self.assertEqual(dry_snapshot.phases["RUNNER"], "planned")
            self.assertEqual(len(dry_results), 1)
            self.assertTrue(dry_results[0].dry_run)
            dry_event = read_events(repo)[-1]
            self.assertTrue(dry_event["metadata"]["dry_run_only"])
            self.assertEqual(dry_event["metadata"]["terminal_summary"]["terminal_status"], "dry_run")

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                return LaunchResult(
                    command=spec.command,
                    returncode=0,
                    output=build_fake_automation_output(
                        status="executed",
                        verification_status="passed",
                        artifact=str(plan),
                    ),
                    executor=spec.executor,
                    log_path=str(log_path) if log_path else None,
                )

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER")

            self.assertEqual(len(results), 1)
            self.assertNotEqual(snapshot.blocker_class, "contract_bug")
            self.assertFalse(snapshot.human_required)
            self.assertTrue(
                any(
                    warning.get("reason") == "event_only_status" and warning.get("value") == "dry_run"
                    for warning in snapshot.ledger_warnings
                )
            )
            serialized = json.dumps(
                {
                    "blocker_class": snapshot.blocker_class,
                    "blocker_summary": snapshot.blocker_summary,
                    "events": read_events(repo),
                    "warnings": snapshot.ledger_warnings,
                },
                sort_keys=True,
            )
            self.assertNotIn("invalid terminal_status: dry_run", serialized)


if __name__ == "__main__":
    unittest.main()
