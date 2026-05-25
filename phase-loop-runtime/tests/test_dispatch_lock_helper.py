import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from phase_loop_runtime.dispatch_lock import DispatchLock, DispatchLockContention, dispatch_lock_path
from phase_loop_test_utils import make_repo


class DispatchLockHelperTest(unittest.TestCase):
    def test_lock_path_is_per_roadmap(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            first = repo / "specs" / "phase-plans-v1.md"
            second = repo / "specs" / "phase-plans-v2.md"
            second.write_text("# Roadmap\n", encoding="utf-8")

            self.assertNotEqual(dispatch_lock_path(repo, first), dispatch_lock_path(repo, second))
            self.assertEqual(dispatch_lock_path(repo, first).name, "dispatch.lock")
            self.assertIn(".phase-loop", dispatch_lock_path(repo, first).parts)

    def test_same_roadmap_contention_reports_holder_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            with DispatchLock(repo, roadmap) as held:
                with self.assertRaises(DispatchLockContention) as raised:
                    DispatchLock(repo, roadmap).acquire()

                self.assertEqual(raised.exception.lock_path, held.path)
                self.assertIsNotNone(raised.exception.holder_pid)
                self.assertIsNotNone(raised.exception.elapsed_seconds)
                self.assertIn(str(roadmap), raised.exception.blocker_summary(roadmap))

    def test_release_allows_reacquire(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            lock = DispatchLock(repo, roadmap).acquire()
            lock.release()

            reacquired = DispatchLock(repo, roadmap).acquire()
            reacquired.release()

    def test_holder_metadata_is_written(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            with DispatchLock(repo, roadmap) as held:
                payload = json.loads(held.path.read_text(encoding="utf-8"))

            self.assertIn("pid", payload)
            self.assertIn("started_at", payload)
            self.assertEqual(payload["repo"], str(repo))
            self.assertEqual(payload["roadmap"], str(roadmap))


if __name__ == "__main__":
    unittest.main()
