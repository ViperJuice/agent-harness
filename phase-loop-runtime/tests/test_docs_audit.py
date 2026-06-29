"""docs-freshness v4 P1 — pipeline-independent docs-audit gate.

Diff-driven only: every test runs WITHOUT `.phase-loop/` state. Covers the unified
taxonomy re-exports, the per-surface relevance-bound decision contract, and the
git-level --base resolution / fail-closed-on-unevaluable behavior.
"""
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from phase_loop_runtime import docs_audit, docs_surfaces as ds
from phase_loop_runtime.models import PUBLIC_SURFACE_GLOBS, public_surface_touched
from phase_loop_runtime.release_guard import RELEASE_AFFECTING_PATTERNS, _is_release_affecting_path


class UnifiedTaxonomyTest(unittest.TestCase):
    """IF-0-P1-1: one canonical taxonomy; models + release_guard re-export from it (no 3rd copy)."""

    def test_public_surface_globs_re_export(self):
        self.assertIs(PUBLIC_SURFACE_GLOBS, ds.GENERAL_PUBLIC_GLOBS)
        self.assertTrue(public_surface_touched(["pkg/cli.py"]))
        self.assertFalse(public_surface_touched(["pkg/internal.py"]))

    def test_release_patterns_re_export(self):
        self.assertIs(RELEASE_AFFECTING_PATTERNS, ds.RELEASE_AFFECTING_PATTERNS)
        self.assertTrue(_is_release_affecting_path("pyproject.toml"))

    def test_classify_and_relevance(self):
        self.assertEqual(ds.classify_surface("pyproject.toml"), "release")
        self.assertEqual(ds.classify_surface("pkg/cli.py"), "general")
        self.assertIsNone(ds.classify_surface("pkg/internal.py"))
        self.assertIn("CHANGELOG*", ds.required_docs_for("VERSION"))


class DecisionContractTest(unittest.TestCase):
    """The per-surface, relevance-bound contract (Assumption 4) via the pure evaluator."""

    def test_release_surface_no_doc_blocks(self):
        r = docs_audit.evaluate(["pyproject.toml"], {})
        self.assertEqual(r.docs_freshness, "blocked")
        self.assertEqual(r.exit_code, 1)
        self.assertEqual(r.findings[0]["code"], "release_docs_unsatisfied")

    def test_release_surface_with_relevant_doc_passes(self):
        r = docs_audit.evaluate(["pyproject.toml", "CHANGELOG.md"], {})
        self.assertEqual(r.docs_freshness, "passed")
        self.assertEqual(r.exit_code, 0)

    def test_release_surface_with_irrelevant_doc_still_blocks(self):
        # README touched but the REQUIRED doc (CHANGELOG) was not — relevance binding.
        r = docs_audit.evaluate(["pyproject.toml", "README.md"], {})
        self.assertEqual(r.docs_freshness, "blocked")
        self.assertEqual(r.findings[0]["code"], "release_docs_unsatisfied")

    def test_release_token_does_not_satisfy(self):
        # A recorded token without the relevant doc change must NOT satisfy a release surface.
        decisions = {"pyproject.toml": {"decision": "no_doc_delta", "reason": "internal", "evidence": ()}}
        r = docs_audit.evaluate(["pyproject.toml"], decisions)
        self.assertEqual(r.docs_freshness, "blocked")

    def test_general_surface_no_decision_blocks(self):
        r = docs_audit.evaluate(["pkg/cli.py"], {})
        self.assertEqual(r.docs_freshness, "blocked")
        self.assertEqual(r.findings[0]["code"], "general_decision_missing")

    def test_general_surface_recorded_decision_passes(self):
        decisions = {"general": {"decision": "no_doc_delta", "reason": "refactor only", "evidence": ()}}
        r = docs_audit.evaluate(["pkg/cli.py"], decisions)
        self.assertEqual(r.docs_freshness, "passed")

    def test_general_surface_with_doc_change_passes(self):
        r = docs_audit.evaluate(["pkg/cli.py", "README.md"], {})
        self.assertEqual(r.docs_freshness, "passed")

    def test_no_public_surface_is_skipped(self):
        r = docs_audit.evaluate(["pkg/internal.py", "tests/test_x.py"], {})
        self.assertEqual(r.docs_freshness, "skipped")
        self.assertEqual(r.exit_code, 0)


def _run(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _commit_repo(repo: Path) -> None:
    _run(repo, "init", "-q")
    _run(repo, "config", "user.email", "t@t.t")
    _run(repo, "config", "user.name", "t")
    (repo / "base.txt").write_text("base\n")
    _run(repo, "add", "-A")
    _run(repo, "commit", "-q", "-m", "base")


class GitLevelAuditTest(unittest.TestCase):
    """run_audit over a real temp git repo — no .phase-loop state anywhere."""

    def test_unresolvable_base_fails_closed(self):
        with TemporaryDirectory() as d:
            repo = Path(d)
            _commit_repo(repo)
            r = docs_audit.run_audit(repo, base="does-not-exist")
            self.assertEqual(r.docs_freshness, "blocked")
            self.assertEqual(r.exit_code, 1)
            self.assertEqual(r.findings[0]["code"], "base_unresolved")

    def test_release_change_no_doc_blocks_at_git_level(self):
        with TemporaryDirectory() as d:
            repo = Path(d)
            _commit_repo(repo)
            (repo / "pyproject.toml").write_text("[project]\nversion='9.9'\n")
            _run(repo, "add", "-A")
            _run(repo, "commit", "-q", "-m", "bump")
            r = docs_audit.run_audit(repo, base="HEAD~1")
            self.assertEqual(r.docs_freshness, "blocked")
            self.assertEqual(r.exit_code, 1)

    def test_release_change_with_changelog_passes_at_git_level(self):
        with TemporaryDirectory() as d:
            repo = Path(d)
            _commit_repo(repo)
            (repo / "pyproject.toml").write_text("[project]\nversion='9.9'\n")
            (repo / "CHANGELOG.md").write_text("## 9.9\n- thing\n")
            _run(repo, "add", "-A")
            _run(repo, "commit", "-q", "-m", "bump + changelog")
            r = docs_audit.run_audit(repo, base="HEAD~1")
            self.assertEqual(r.docs_freshness, "passed")
            self.assertEqual(r.exit_code, 0)

    def test_decisions_file_satisfies_general(self):
        with TemporaryDirectory() as d:
            repo = Path(d)
            _commit_repo(repo)
            (repo / "cli.py").write_text("# a public surface\n")
            (repo / ".doc-decisions.json").write_text(json.dumps({
                "version": 1,
                "decisions": [{"surface": "general", "decision": "no_doc_delta", "reason": "internal"}],
            }))
            _run(repo, "add", "-A")
            _run(repo, "commit", "-q", "-m", "cli change + decision")
            r = docs_audit.run_audit(repo, base="HEAD~1")
            self.assertEqual(r.docs_freshness, "passed")


class BaseResolutionTest(unittest.TestCase):
    def test_explicit_base_wins(self):
        with TemporaryDirectory() as d:
            self.assertEqual(docs_audit.resolve_base(Path(d), "abc123"), ("abc123", "explicit"))

    def test_pr_context(self):
        import os
        with TemporaryDirectory() as d:
            old = os.environ.get("GITHUB_BASE_REF")
            os.environ["GITHUB_BASE_REF"] = "main"
            try:
                base, ctx = docs_audit.resolve_base(Path(d), None)
                self.assertEqual((base, ctx), ("origin/main", "pull_request"))
            finally:
                os.environ.pop("GITHUB_BASE_REF", None)
                if old is not None:
                    os.environ["GITHUB_BASE_REF"] = old

    def test_push_context_defaults_head_parent(self):
        import os
        with TemporaryDirectory() as d:
            for k in ("GITHUB_BASE_REF", "GITHUB_REF"):
                os.environ.pop(k, None)
            base, ctx = docs_audit.resolve_base(Path(d), None)
            self.assertEqual((base, ctx), ("HEAD~1", "push"))


if __name__ == "__main__":
    unittest.main()
