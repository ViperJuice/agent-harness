import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.lane_scheduler import worktree_assignments_for_phase_wave
from phase_loop_test_utils import make_repo


class WorkerPoolWorktreeAllocTest(unittest.TestCase):
    def test_concurrent_phase_wave_gets_distinct_git_worktrees(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))

            assignments = worktree_assignments_for_phase_wave(
                repo,
                ("A", "B", "C"),
                branch="feature/wp",
                mode="concurrent",
                base_sha="base",
            )

            self.assertEqual({item.lane_id for item in assignments}, {"A", "B", "C"})
            self.assertEqual({item.isolation_mode for item in assignments}, {"git_worktree"})
            self.assertEqual({item.base_sha for item in assignments}, {"base"})
            self.assertEqual(len({item.worktree_path for item in assignments}), 3)

    def test_serialized_phase_wave_uses_main_worktree(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))

            assignments = worktree_assignments_for_phase_wave(repo, ("A", "B"), branch="main", mode="serialized")

            self.assertEqual(tuple(item.worktree_path for item in assignments), (str(repo), str(repo)))
            self.assertEqual({item.isolation_mode for item in assignments}, {"main_worktree"})


if __name__ == "__main__":
    unittest.main()
