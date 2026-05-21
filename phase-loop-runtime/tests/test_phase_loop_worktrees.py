import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.closeout import reduce_lane_dirty_paths
from phase_loop_runtime.git_ops import snapshot_git_dirty_paths
from phase_loop_runtime.models import PhasePlanLane
from phase_loop_runtime.runtime_paths import lane_worktree_path, lane_worktree_root
from phase_loop_test_utils import make_repo


class PhaseLoopWorktreeTest(unittest.TestCase):
    def test_lane_worktree_paths_use_workspace_mount_when_available(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = make_repo(root)
            workspace = root / "mnt" / "workspace"
            workspace.mkdir(parents=True)

            self.assertEqual(lane_worktree_root(repo, workspace_mount=workspace), workspace / "worktrees")
            self.assertEqual(
                lane_worktree_path(repo, branch="feature/test", lane_id="SL-1", workspace_mount=workspace),
                workspace / "worktrees" / "repo-feature-test-SL-1",
            )

    def test_lane_worktree_paths_fall_back_to_repo_siblings_without_workspace_mount(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            missing_mount = Path(td) / "missing-workspace"

            self.assertEqual(lane_worktree_root(repo, workspace_mount=missing_mount), repo.parent)
            self.assertEqual(
                lane_worktree_path(repo, branch="main", lane_id="SL-2", workspace_mount=missing_mount),
                repo.parent / "repo-main-SL-2",
            )

    def test_dirty_path_snapshot_and_reduction_classify_lane_peer_reducer_and_unowned(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            (repo / "lane.txt").write_text("lane\n", encoding="utf-8")
            (repo / "peer.txt").write_text("peer\n", encoding="utf-8")
            (repo / "extra.txt").write_text("extra\n", encoding="utf-8")
            subprocess.run(["git", "add", "lane.txt", "peer.txt"], cwd=repo, check=True)

            dirty = snapshot_git_dirty_paths(repo)
            lanes = (
                PhasePlanLane(lane_id="SL-0", name="lane", heading="### SL-0 - lane", owned_files=("lane.txt",)),
                PhasePlanLane(lane_id="SL-1", name="peer", heading="### SL-1 - peer", owned_files=("peer.txt",)),
            )
            reduced = reduce_lane_dirty_paths(
                dirty + ("reducer.json",),
                lanes,
                active_lane_id="SL-0",
                pre_existing_paths=("peer.txt",),
                reducer_paths=("reducer.json",),
            )
            by_path = {item.path: item.classification for item in reduced}

            self.assertEqual(by_path["lane.txt"], "lane_owned")
            self.assertEqual(by_path["peer.txt"], "pre_existing")
            self.assertEqual(by_path["extra.txt"], "unowned")
            self.assertEqual(by_path["reducer.json"], "reducer_owned")


if __name__ == "__main__":
    unittest.main()
