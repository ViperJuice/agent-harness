import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from phase_loop_runtime.cli import _validate_tracked_closeout_artifact, main
from phase_loop_runtime.events import read_events
from phase_loop_runtime.reconcile import reconcile
from phase_loop_test_utils import commit_fixture_paths, make_repo, write_phase_plan


def _run(argv):
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _fixture(td: Path):
    """Repo with a committed roadmap, RUNNER plan, and RUNNER closeout markdown. Returns
    (repo, roadmap, closeout_relpath, closeout_commit)."""
    repo = make_repo(Path(td))
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "RUNNER", roadmap)
    closeout = repo / "planning" / "phase-artifacts" / "RUNNER-closeout.md"
    closeout.parent.mkdir(parents=True, exist_ok=True)
    closeout.write_text("# RUNNER closeout\n\nPhase completed; IF gates satisfied.\n", encoding="utf-8")
    commit_fixture_paths(repo, "add runner plan + closeout", plan, closeout)
    return repo, roadmap, closeout.relative_to(repo).as_posix(), _git(repo, "rev-parse", "HEAD")


class ValidateTrackedCloseoutArtifactTest(unittest.TestCase):
    def test_tracked_committed_markdown_is_recovery_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            repo, _roadmap, rel, commit = _fixture(Path(td))
            result = _validate_tracked_closeout_artifact(repo, rel, commit, "RUNNER")
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["code"], "recovered_from_tracked_closeout")
            self.assertEqual(result["provenance"], "tracked_closeout_artifact")
            self.assertEqual(result["closeout_commit"], commit)  # canonical resolved SHA

    def test_untracked_markdown_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            repo, _roadmap, _rel, commit = _fixture(Path(td))
            untracked = repo / "planning" / "phase-artifacts" / "RUNNER-ghost.md"
            untracked.write_text("# RUNNER not committed\n", encoding="utf-8")  # on disk, NOT committed
            result = _validate_tracked_closeout_artifact(repo, untracked.relative_to(repo).as_posix(), commit, "RUNNER")
            self.assertFalse(result["ok"])
            self.assertEqual(result["code"], "closeout_artifact_not_committed")

    def test_index_ref_zero_is_rejected(self):
        # codex: `:0` is the git INDEX, not a commit — staged-but-uncommitted prose must not pass.
        with tempfile.TemporaryDirectory() as td:
            repo, _roadmap, rel, _commit = _fixture(Path(td))
            result = _validate_tracked_closeout_artifact(repo, rel, ":0", "RUNNER")
            self.assertFalse(result["ok"])
            self.assertEqual(result["code"], "closeout_commit_not_a_commit")

    def test_nonexistent_commit_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            repo, _roadmap, rel, _commit = _fixture(Path(td))
            result = _validate_tracked_closeout_artifact(repo, rel, "0" * 40, "RUNNER")
            self.assertFalse(result["ok"])
            self.assertEqual(result["code"], "closeout_commit_not_a_commit")

    def test_commit_not_reachable_from_head_is_rejected(self):
        # An orphan/side-branch commit is not part of HEAD's history.
        with tempfile.TemporaryDirectory() as td:
            repo, _roadmap, rel, _commit = _fixture(Path(td))
            _git(repo, "checkout", "-q", "-b", "side")
            (repo / "planning" / "phase-artifacts" / "RUNNER-extra.md").write_text("# RUNNER extra\n", encoding="utf-8")
            commit_fixture_paths(repo, "side closeout", repo / "planning" / "phase-artifacts" / "RUNNER-extra.md")
            side_commit = _git(repo, "rev-parse", "HEAD")
            _git(repo, "checkout", "-q", "-")  # back to the original branch; side_commit not reachable
            result = _validate_tracked_closeout_artifact(repo, rel, side_commit, "RUNNER")
            self.assertFalse(result["ok"])
            self.assertEqual(result["code"], "closeout_commit_not_in_history")

    def test_tracked_directory_is_rejected(self):
        # codex/Fable: a tree object (directory) must not qualify as a file blob.
        with tempfile.TemporaryDirectory() as td:
            repo, _roadmap, _rel, commit = _fixture(Path(td))
            result = _validate_tracked_closeout_artifact(repo, "planning/phase-artifacts", commit, "RUNNER")
            self.assertFalse(result["ok"])
            self.assertEqual(result["code"], "closeout_artifact_not_a_file")

    def test_tracked_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            repo, _roadmap, _rel, _commit = _fixture(Path(td))
            link = repo / "RUNNER-link.md"
            os.symlink("planning/phase-artifacts/RUNNER-closeout.md", link)
            commit_fixture_paths(repo, "add symlink", link)
            result = _validate_tracked_closeout_artifact(repo, "RUNNER-link.md", _git(repo, "rev-parse", "HEAD"), "RUNNER")
            self.assertFalse(result["ok"])
            self.assertEqual(result["code"], "closeout_artifact_not_a_file")

    def test_empty_tracked_markdown_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            empty = repo / "planning" / "RUNNER-empty.md"
            empty.parent.mkdir(parents=True, exist_ok=True)
            empty.write_text("", encoding="utf-8")
            commit_fixture_paths(repo, "add empty closeout", empty)
            result = _validate_tracked_closeout_artifact(repo, "planning/RUNNER-empty.md", _git(repo, "rev-parse", "HEAD"), "RUNNER")
            self.assertFalse(result["ok"])
            self.assertEqual(result["code"], "empty_closeout_artifact")

    def test_unrelated_tracked_file_is_rejected_by_phase_binding(self):
        with tempfile.TemporaryDirectory() as td:
            repo, _roadmap, _rel, commit = _fixture(Path(td))
            # README.md is tracked by make_repo but neither its name nor content mentions RUNNER.
            result = _validate_tracked_closeout_artifact(repo, "README.md", commit, "RUNNER")
            self.assertFalse(result["ok"])
            self.assertEqual(result["code"], "closeout_artifact_phase_mismatch")

    def test_binary_without_phase_token_is_rejected_without_crash(self):
        # gemini: a binary blob must not crash the decode; here it lacks the phase token in name
        # and content, so it is cleanly rejected (content read uses errors="replace").
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            blob = repo / "asset.bin"
            blob.write_bytes(bytes(range(256)) * 8)
            commit_fixture_paths(repo, "add binary", blob)
            result = _validate_tracked_closeout_artifact(repo, "asset.bin", _git(repo, "rev-parse", "HEAD"), "RUNNER")
            self.assertFalse(result["ok"])
            self.assertEqual(result["code"], "closeout_artifact_phase_mismatch")

    def test_path_outside_repo_is_rejected(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as outside:
            repo, _roadmap, _rel, commit = _fixture(Path(td))
            stray = Path(outside) / "RUNNER-closeout.md"
            stray.write_text("# outside\n", encoding="utf-8")
            result = _validate_tracked_closeout_artifact(repo, str(stray), commit, "RUNNER")
            self.assertFalse(result["ok"])
            self.assertEqual(result["code"], "artifact_outside_repo")


class ReconcileTrackedCloseoutRecoveryTest(unittest.TestCase):
    def _args(self, repo, roadmap, phase, *extra):
        return ["reconcile", "--repo", str(repo), "--roadmap", str(roadmap), "--phase", phase, *extra]

    def test_reconcile_recovers_completed_phase_from_tracked_closeout(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap, rel, commit = _fixture(Path(td))
            code, out, stderr = _run(
                self._args(
                    repo, roadmap, "RUNNER",
                    "--closeout-artifact", rel,
                    "--closeout-commit", commit,
                    "--repair-summary", "Recovered RUNNER from tracked closeout after interrupted session.",
                    "--verification-status", "passed",
                )
            )
            self.assertEqual(code, 0, stderr)
            repair = read_events(repo)[-1]["metadata"]["manual_repair"]
            self.assertEqual(repair["evidence_provenance"], "tracked_closeout_artifact")
            self.assertEqual(repair["verification_evidence"]["code"], "recovered_from_tracked_closeout")
            # End-to-end: a fresh reconcile now sees RUNNER complete, and the provenance is surfaced
            # at the status boundary (so a consumer can tell it apart from a runner pass).
            snapshot = reconcile(repo, roadmap)
            self.assertEqual(snapshot.phases.get("RUNNER"), "complete")
            self.assertEqual(snapshot.closeout_summary.get("evidence_provenance"), "tracked_closeout_artifact")
            self.assertIn("recovery evidence: tracked_closeout_artifact", out)

    def test_reconcile_rejects_untracked_closeout_markdown(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap, _rel, commit = _fixture(Path(td))
            untracked = repo / "planning" / "phase-artifacts" / "RUNNER-ghost.md"
            untracked.write_text("# RUNNER not committed\n", encoding="utf-8")
            code, _out, stderr = _run(
                self._args(
                    repo, roadmap, "RUNNER",
                    "--closeout-artifact", untracked.relative_to(repo).as_posix(),
                    "--closeout-commit", commit,
                    "--repair-summary", "attempt",
                    "--verification-status", "passed",
                    "--allow-dirty",
                )
            )
            self.assertEqual(code, 2)
            self.assertIn("closeout_artifact_not_committed", stderr)

    def test_closeout_artifact_forbidden_for_rg_phase(self):
        # RG hard-requires runner verification; a prose closeout must never satisfy it.
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap, rel, commit = _fixture(Path(td))
            code, _out, stderr = _run(
                self._args(
                    repo, roadmap, "RG",
                    "--closeout-artifact", rel,
                    "--closeout-commit", commit,
                    "--repair-summary", "x",
                    "--verification-status", "passed",
                )
            )
            self.assertEqual(code, 2)
            self.assertIn("requires runner verification", stderr)

    def test_closeout_artifact_requires_audit_fields(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap, rel, _commit = _fixture(Path(td))
            code, _out, stderr = _run(
                self._args(repo, roadmap, "RUNNER", "--closeout-artifact", rel, "--verification-status", "passed")
            )
            self.assertEqual(code, 2)
            self.assertIn("--closeout-commit", stderr)

    def test_closeout_artifact_and_verification_log_mutually_exclusive(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap, rel, commit = _fixture(Path(td))
            code, _out, stderr = _run(
                self._args(
                    repo, roadmap, "RUNNER",
                    "--closeout-artifact", rel,
                    "--verification-log", rel,
                    "--closeout-commit", commit,
                    "--repair-summary", "x",
                    "--verification-status", "passed",
                )
            )
            self.assertEqual(code, 2)
            self.assertIn("mutually exclusive", stderr)

    def test_verification_log_path_still_rejects_markdown(self):
        # Regression guard: the runner-verification path is unchanged and still rejects a markdown.
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap, rel, _commit = _fixture(Path(td))
            code, _out, stderr = _run(
                self._args(
                    repo, roadmap, "RUNNER",
                    "--verification-status", "passed",
                    "--verification-log", rel,
                    "--allow-dirty",
                )
            )
            self.assertEqual(code, 2)
            self.assertIn("malformed_artifact", stderr)


if __name__ == "__main__":
    unittest.main()
