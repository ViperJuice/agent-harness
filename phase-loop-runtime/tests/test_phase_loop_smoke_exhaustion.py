import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.reconcile import reconcile
from phase_loop_runtime.runner import run_loop
from phase_loop_smoke_utils import BIN, append_phase_event, make_completed_roadmap_smoke_fixture, make_two_phase_repo


class PhaseLoopSmokeExhaustionTest(unittest.TestCase):
    def test_clean_fixture_status_and_dry_run_create_local_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            status = subprocess.run([str(BIN), "status", "--repo", str(repo), "--roadmap", str(roadmap), "--json"], text=True, capture_output=True, check=True)
            data = json.loads(status.stdout)
            self.assertEqual(data["phases"], {"ALPHA": "unplanned", "BETA": "unplanned"})
            self.assertTrue((repo / ".phase-loop" / "state.json").exists())
            self.assertTrue((repo / ".phase-loop" / "tui-handoff.md").exists())
            self.assertFalse((repo / ".phase-loop" / "events.jsonl").exists())

            dry_run = subprocess.run([str(BIN), "dry-run", "--repo", str(repo), "--roadmap", str(roadmap), "--max-phases", "1"], text=True, capture_output=True, check=True)
            self.assertIn("codex exec", dry_run.stdout)
            self.assertTrue((repo / ".phase-loop" / "state.json").exists())
            self.assertTrue((repo / ".phase-loop" / "events.jsonl").exists())
            state = subprocess.run([str(BIN), "state", "--repo", str(repo), "--roadmap", str(roadmap), "--json"], text=True, capture_output=True, check=True)
            self.assertEqual(json.loads(state.stdout)["legacy_count"], 0)

    def test_two_terminal_handoffs_exhaust_roadmap_without_extra_selection(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_phase_event(repo, roadmap, "ALPHA", "complete")
            append_phase_event(repo, roadmap, "BETA", "complete")
            snapshot = reconcile(repo, roadmap)
            self.assertIsNone(snapshot.current_phase)
            self.assertEqual(snapshot.phases["ALPHA"], "complete")
            self.assertEqual(snapshot.phases["BETA"], "complete")
            exhausted, results = run_loop(repo, roadmap, max_phases=2, dry_run=True)
            self.assertEqual(results, [])
            self.assertIsNone(exhausted.current_phase)

    def test_completed_roadmap_fixture_exhausts_without_resume_noise(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = make_completed_roadmap_smoke_fixture(Path(td))
            append_phase_event(fixture.repo, fixture.roadmap, "OBSERVE", "complete")
            append_phase_event(fixture.repo, fixture.roadmap, "RUNNER", "complete")

            exhausted, results = run_loop(fixture.repo, fixture.roadmap, max_phases=2, dry_run=True)

            self.assertEqual(results, [])
            self.assertIsNone(exhausted.current_phase)
            self.assertEqual(exhausted.phases["OBSERVE"], "complete")
            self.assertEqual(exhausted.phases["RUNNER"], "complete")


if __name__ == "__main__":
    unittest.main()
