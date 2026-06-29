"""docs-freshness v4 P4 — post-dispatch release evidence-repair reducer + invariants."""
import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime import release_guard
from phase_loop_runtime.docs_audit import run_audit


class ReleaseEvidenceReducerTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.repo = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _write(self, rel, body):
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")

    def test_residual_placeholder_detected_post_dispatch(self):
        self._write("CHANGELOG.md", "## v9\n- Message Board SHA: recovery commit pending\n")
        residuals = release_guard.release_evidence_residuals(self.repo, ["CHANGELOG.md"])
        self.assertIn("CHANGELOG.md", residuals)
        blocker = release_guard.release_dispatch_evidence_blocker(self.repo, ["CHANGELOG.md"])
        self.assertIsNotNone(blocker)
        self.assertEqual(blocker.blocker_class, "release_evidence_residual")

    def test_clean_evidence_no_blocker(self):
        self._write("CHANGELOG.md", "## v9\n- Message Board SHA: abc1234 (released)\n")
        self.assertEqual(release_guard.release_evidence_residuals(self.repo, ["CHANGELOG.md"]), {})
        self.assertIsNone(release_guard.release_dispatch_evidence_blocker(self.repo, ["CHANGELOG.md"]))

    def test_reducer_is_operator_path_human_required(self):
        # The release-dispatch reducer is the operator-driven flow (NOT the autonomous
        # run-loop) and like the existing release_dispatch_blocker uses human_required.
        self._write("CHANGELOG.md", "- Message Board SHA: recovery commit pending\n")
        blocker = release_guard.release_dispatch_evidence_blocker(self.repo, ["CHANGELOG.md"])
        self.assertTrue(blocker.to_blocker()["human_required"])


class DocsAuditInvariantTest(unittest.TestCase):
    """CI invariant: a release surface with no doc decision fails the check (exit 1);
    satisfied → exit 0. Diff-driven, no `.phase-loop/` state."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.repo = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _git(self, *args):
        import subprocess
        subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                       capture_output=True)

    def _commit(self, msg):
        self._git("add", "-A")
        self._git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", msg)

    def _init(self):
        self._git("init", "-q")
        (self.repo / "README.md").write_text("# base\n", encoding="utf-8")
        self._commit("base")

    def test_release_surface_no_doc_blocks_with_exit_1(self):
        self._init()
        (self.repo / "pyproject.toml").write_text("[project]\nversion='2'\n", encoding="utf-8")
        self._commit("bump version, no changelog")
        report = run_audit(self.repo, "HEAD~1")
        self.assertEqual(report.docs_freshness, "blocked")
        self.assertEqual(report.exit_code, 1)

    def test_release_surface_with_changelog_passes_exit_0(self):
        self._init()
        (self.repo / "pyproject.toml").write_text("[project]\nversion='2'\n", encoding="utf-8")
        (self.repo / "CHANGELOG.md").write_text("## v2\n- bumped\n", encoding="utf-8")
        self._commit("bump version + changelog")
        report = run_audit(self.repo, "HEAD~1")
        self.assertEqual(report.docs_freshness, "passed")
        self.assertEqual(report.exit_code, 0)


if __name__ == "__main__":
    unittest.main()
