import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from phase_loop_runtime.dispatch_lock import DispatchLock
from phase_loop_runtime.events import read_events
from phase_loop_runtime.runner import run_loop
from phase_loop_test_utils import make_repo


class DispatchLockSameRoadmapTest(unittest.TestCase):
    def test_same_roadmap_contention_returns_concurrent_dispatch_blocker(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            with DispatchLock(repo, roadmap):
                snapshot, results = run_loop(repo=repo, roadmap=roadmap, phase="RUNNER", max_phases=1)

            self.assertEqual(results, [])
            self.assertFalse(snapshot.human_required)
            self.assertEqual(snapshot.blocker_class, "concurrent_dispatch")
            self.assertIn("PID", snapshot.blocker_summary or "")
            self.assertIn(str(roadmap), snapshot.blocker_summary or "")
            self.assertEqual(snapshot.terminal_summary["terminal_blocker"]["blocker_class"], "concurrent_dispatch")

            event = read_events(repo)[-1]
            self.assertEqual(event["status"], "blocked")
            self.assertEqual(event["blocker"]["blocker_class"], "concurrent_dispatch")
            self.assertEqual(event["metadata"]["dispatch_lock"]["status"], "blocked")

    def test_no_dispatch_lock_preserves_unlocked_path(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            with DispatchLock(repo, roadmap):
                snapshot, results = run_loop(
                    repo=repo,
                    roadmap=roadmap,
                    phase="RUNNER",
                    max_phases=0,
                    dispatch_lock_enabled=False,
                )

            self.assertNotEqual(snapshot.blocker_class, "concurrent_dispatch")
            self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
