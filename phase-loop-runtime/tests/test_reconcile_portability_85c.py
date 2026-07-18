import shutil
import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.reconcile import reconcile
from phase_loop_runtime.runtime_paths import roadmap_paths_match
from phase_loop_runtime.state import write_state
from phase_loop_test_utils import make_repo, provenanced_state, write_phase_plan


class RoadmapPathsMatchTest(unittest.TestCase):
    # ah#85(C): portable roadmap identity across a relocated repo root.
    def test_identical_absolute_paths_match_not_relocated(self):
        repo = Path("/x/repo")
        roadmap = repo / "specs" / "phase-plans-v1.md"
        self.assertEqual(roadmap_paths_match(str(repo), str(roadmap), repo, roadmap), (True, False))

    def test_relocated_same_relative_path_matches_relocated(self):
        stored_repo = Path("/home/user/code/avatar-client")
        stored_roadmap = stored_repo / "specs" / "phase-plans-v3.md"
        repo = Path("/mnt/workspace/worktrees/avatar-client-x")
        roadmap = repo / "specs" / "phase-plans-v3.md"
        self.assertEqual(roadmap_paths_match(str(stored_repo), str(stored_roadmap), repo, roadmap), (True, True))

    def test_different_relative_roadmap_does_not_match(self):
        stored_repo = Path("/a/repo")
        stored_roadmap = stored_repo / "specs" / "other-roadmap.md"
        repo = Path("/b/repo")
        roadmap = repo / "specs" / "phase-plans-v1.md"
        self.assertEqual(roadmap_paths_match(str(stored_repo), str(stored_roadmap), repo, roadmap), (False, False))

    def test_roadmap_outside_stored_repo_falls_back_to_non_match(self):
        stored_repo = Path("/a/repo")
        stored_roadmap = Path("/elsewhere/phase-plans-v1.md")  # not under stored_repo
        repo = Path("/b/repo")
        roadmap = repo / "specs" / "phase-plans-v1.md"
        self.assertEqual(roadmap_paths_match(str(stored_repo), str(stored_roadmap), repo, roadmap), (False, False))

    def test_empty_or_missing_stored_paths_do_not_match(self):
        repo = Path("/b/repo")
        roadmap = repo / "specs" / "phase-plans-v1.md"
        self.assertEqual(roadmap_paths_match(None, None, repo, roadmap), (False, False))
        self.assertEqual(roadmap_paths_match(str(repo), "", repo, roadmap), (False, False))


class ReconcileRepoRelocationTest(unittest.TestCase):
    def test_reconcile_preserves_status_after_repo_relocation(self):
        # ah#85(C) symptom #5: state written under repo root A, then `.phase-loop/` replayed
        # from a DIFFERENT root B (moved/renamed/copied worktree). The persisted "complete"
        # status must survive (only the snapshot-application path can produce it) and exactly one
        # `repo_relocated` portability warning must be emitted — instead of all-unplanned.
        # Hermetic (reconcile is read-side; no skill bundle needed) and UNMARKED so CI runs it.
        with tempfile.TemporaryDirectory() as tda, tempfile.TemporaryDirectory() as tdb:
            repo_a = make_repo(Path(tda))
            roadmap_a = repo_a / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo_a, "RUNNER", roadmap_a)
            # Persist a completed RUNNER with correct content provenance, absolute A paths.
            write_state(repo_a, provenanced_state(repo_a, roadmap_a, {"RUNNER": "complete"}))

            # Repo B: byte-identical roadmap/plan content (matching SHAs), different absolute root.
            repo_b = make_repo(Path(tdb))
            roadmap_b = repo_b / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo_b, "RUNNER", roadmap_b)
            # Relocate: copy A's `.phase-loop/` (state.json still carries A's absolute paths) into B.
            shutil.copytree(repo_a / ".phase-loop", repo_b / ".phase-loop")

            snapshot = reconcile(repo_b, roadmap_b, read_only=True)

            # Fails on pre-fix main (absolute-equality gate skips the snapshot block → not complete).
            self.assertEqual(snapshot.phases.get("RUNNER"), "complete")
            reasons = [w.get("reason") for w in snapshot.ledger_warnings]
            self.assertIn("repo_relocated", reasons)
            self.assertEqual(reasons.count("repo_relocated"), 1)

    def test_same_root_reconcile_emits_no_relocation_warning(self):
        # Guard against a false-positive relocation warning on the normal same-root path.
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            write_state(repo, provenanced_state(repo, roadmap, {"RUNNER": "complete"}))

            snapshot = reconcile(repo, roadmap, read_only=True)

            self.assertEqual(snapshot.phases.get("RUNNER"), "complete")
            self.assertNotIn("repo_relocated", [w.get("reason") for w in snapshot.ledger_warnings])


if __name__ == "__main__":
    unittest.main()
