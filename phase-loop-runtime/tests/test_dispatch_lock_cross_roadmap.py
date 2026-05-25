import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from phase_loop_runtime.dispatch_lock import DispatchLock, dispatch_lock_path
from phase_loop_test_utils import make_repo, write_named_roadmap


class DispatchLockCrossRoadmapTest(unittest.TestCase):
    def test_cross_roadmap_locks_are_independent(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            first = repo / "specs" / "phase-plans-v1.md"
            second = write_named_roadmap(repo, (("ALPHA", "Alpha"),), version="v2")

            self.assertNotEqual(dispatch_lock_path(repo, first), dispatch_lock_path(repo, second))
            with DispatchLock(repo, first), DispatchLock(repo, second):
                self.assertTrue(dispatch_lock_path(repo, first).exists())
                self.assertTrue(dispatch_lock_path(repo, second).exists())


if __name__ == "__main__":
    unittest.main()
