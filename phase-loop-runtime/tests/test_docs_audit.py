"""Tests for the pipeline-independent docs-audit backstop (#18 follow-up, v0.1.7).

The audit is diff-driven (no `.phase-loop/` state) and FAIL-CLOSED: an un-evaluable
input is `blocked`, never a silent pass. These tests cover the relevance-bound decision
contract, the silent-absence case (a release surface changed with no required-doc change
— the gap the v0.1.6 closeout gate structurally cannot catch), the four fail-open fixes
(diff-error fail-closed, batched-push before-SHA, push:tags / first-tag base), and that
the shipped `release_guard` controls are UNCHANGED (the audit's taxonomy is standalone).
"""
from __future__ import annotations

import os
import subprocess
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from phase_loop_runtime import docs_audit, docs_surfaces as ds


@contextmanager
def _env(**kv):
    saved = {k: os.environ.get(k) for k in kv}
    try:
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _init_repo(repo: Path) -> None:
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")


def _commit(repo: Path, msg: str, files: dict[str, str]) -> str:
    for rel, body in files.items():
        fp = repo / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(body, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


class TaxonomyTest(unittest.TestCase):
    def test_release_class_and_relevance(self):
        self.assertEqual(ds.classify_surface("pyproject.toml"), "release")
        self.assertEqual(ds.classify_surface("src/pkg/cli.py"), "general")
        self.assertIsNone(ds.classify_surface("src/pkg/internal.py"))
        self.assertIn("CHANGELOG*", ds.required_docs_for("pyproject.toml"))

    def test_package_json_is_not_release(self):
        # Consensus: do NOT widen the release set with **/package.json.
        self.assertNotIn("**/package.json", ds.RELEASE_AFFECTING_PATTERNS)
        self.assertIsNone(ds.classify_surface("frontend/package.json"))

    def test_shipped_release_guard_unchanged(self):
        # The audit's taxonomy is standalone — it must NOT re-export through / mutate the
        # shipped release_guard control (which gates release dispatch with human_required).
        from phase_loop_runtime import release_guard
        self.assertNotIn("**/package.json", release_guard.RELEASE_AFFECTING_PATTERNS)
        self.assertFalse(release_guard._is_release_affecting_path("frontend/package.json"))
        self.assertIsNot(release_guard.RELEASE_AFFECTING_PATTERNS, ds.RELEASE_AFFECTING_PATTERNS)


class DecisionContractTest(unittest.TestCase):
    """evaluate(): the per-surface, relevance-bound contract."""

    def test_release_surface_no_doc_blocks(self):
        r = docs_audit.evaluate(["pyproject.toml"], {})
        self.assertEqual(r.docs_freshness, "blocked")
        self.assertEqual(r.exit_code, 1)

    def test_release_surface_with_relevant_doc_passes(self):
        r = docs_audit.evaluate(["pyproject.toml", "CHANGELOG.md"], {})
        self.assertEqual(r.docs_freshness, "passed")

    def test_release_surface_irrelevant_doc_still_blocks(self):
        # docs/guide.md is not pyproject's required doc (CHANGELOG) → relevance binding holds.
        r = docs_audit.evaluate(["pyproject.toml", "docs/guide.md"], {})
        self.assertEqual(r.docs_freshness, "blocked")

    def test_release_token_does_not_satisfy(self):
        # A recorded no_doc_delta token can satisfy a general surface but NOT a release one.
        decisions = {"release": {"decision": "no_doc_delta", "reason": "x", "evidence": ()}}
        r = docs_audit.evaluate(["pyproject.toml"], decisions)
        self.assertEqual(r.docs_freshness, "blocked")

    def test_general_no_decision_blocks(self):
        r = docs_audit.evaluate(["src/pkg/cli.py"], {})
        self.assertEqual(r.docs_freshness, "blocked")

    def test_general_recorded_decision_passes(self):
        decisions = {"general": {"decision": "no_doc_delta", "reason": "internal-only", "evidence": ()}}
        r = docs_audit.evaluate(["src/pkg/cli.py"], decisions)
        self.assertEqual(r.docs_freshness, "passed")

    def test_general_with_doc_change_passes(self):
        r = docs_audit.evaluate(["src/pkg/cli.py", "README.md"], {})
        self.assertEqual(r.docs_freshness, "passed")

    def test_no_public_surface_skipped(self):
        r = docs_audit.evaluate(["src/pkg/internal.py"], {})
        self.assertEqual(r.docs_freshness, "skipped")
        self.assertEqual(r.exit_code, 0)


class GitLevelAuditTest(unittest.TestCase):
    """run_audit() end-to-end on real tmp git repos — no .phase-loop state."""

    def test_silent_absence_version_bump_no_changelog_blocks(self):
        # THE point: a release surface changed, CHANGELOG simply not updated, no token.
        with TemporaryDirectory() as d:
            repo = Path(d)
            _init_repo(repo)
            base = _commit(repo, "base", {"pyproject.toml": 'version = "0.1.0"\n', "CHANGELOG.md": "# c\n"})
            _commit(repo, "bump, no changelog", {"pyproject.toml": 'version = "0.2.0"\n'})
            r = docs_audit.run_audit(repo, base=base)
            self.assertEqual(r.docs_freshness, "blocked")
            self.assertTrue(any(f["klass"] == "release" for f in r.findings))

    def test_version_bump_with_changelog_passes(self):
        with TemporaryDirectory() as d:
            repo = Path(d)
            _init_repo(repo)
            base = _commit(repo, "base", {"pyproject.toml": 'version = "0.1.0"\n', "CHANGELOG.md": "# c\n"})
            _commit(repo, "bump + changelog", {"pyproject.toml": 'version = "0.2.0"\n', "CHANGELOG.md": "# c\n## 0.2.0\n"})
            r = docs_audit.run_audit(repo, base=base)
            self.assertEqual(r.docs_freshness, "passed")

    def test_unresolvable_base_fails_closed(self):
        with TemporaryDirectory() as d:
            repo = Path(d)
            _init_repo(repo)
            _commit(repo, "base", {"a.txt": "1\n"})
            r = docs_audit.run_audit(repo, base="does-not-exist")
            self.assertEqual(r.docs_freshness, "blocked")
            self.assertEqual(r.findings[0]["code"], "base_unresolved")

    def test_diff_error_fails_closed(self):
        # A base that resolves (rev_ok) but whose three-dot diff vs HEAD errors (unrelated
        # histories, no merge-base) must be `blocked` — NOT a silent skip (#1).
        with TemporaryDirectory() as d:
            repo = Path(d)
            _init_repo(repo)
            mainline = _commit(repo, "main line", {"a.txt": "1\n"})
            _git(repo, "checkout", "-q", "--orphan", "other")
            _git(repo, "rm", "-rfq", "--cached", ".")
            orphan = _commit(repo, "orphan root", {"b.txt": "2\n"})
            _git(repo, "checkout", "-q", mainline)  # detached on the main-line commit
            # explicit base → three-dot → unrelated history → git diff exits 128 → fail-closed
            r = docs_audit.run_audit(repo, base=orphan)
            self.assertEqual(r.docs_freshness, "blocked")
            self.assertEqual(r.findings[0]["code"], "diff_unavailable")

    def test_decisions_file_satisfies_general(self):
        with TemporaryDirectory() as d:
            repo = Path(d)
            _init_repo(repo)
            base = _commit(repo, "base", {"src/cli.py": "x = 1\n", ".doc-decisions.json": "{}\n"})
            _commit(repo, "edit cli + record decision", {
                "src/cli.py": "x = 2\n",
                ".doc-decisions.json": '{"decisions":[{"surface":"general","decision":"no_doc_delta","reason":"internal"}]}\n',
            })
            r = docs_audit.run_audit(repo, base=base)
            self.assertEqual(r.docs_freshness, "passed")



class BaseResolutionTest(unittest.TestCase):
    def setUp(self):
        self.d = TemporaryDirectory()
        self.repo = Path(self.d.name)
        _init_repo(self.repo)

    def tearDown(self):
        self.d.cleanup()

    def test_explicit_base_wins(self):
        with _env(GITHUB_BASE_REF="main", GITHUB_REF="refs/tags/v9"):
            base, ctx = docs_audit.resolve_base(self.repo, "abc")
            self.assertEqual((base, ctx), ("abc", "explicit"))

    def test_pr_context(self):
        with _env(GITHUB_BASE_REF="main", GITHUB_REF=None, DOCS_AUDIT_PUSH_BEFORE=None):
            self.assertEqual(docs_audit.resolve_base(self.repo, None), ("origin/main", "pull_request"))

    def test_push_before_sha_batched(self):
        # The whole pushed batch is diffed via the before SHA + two-dot — NOT just the tip.
        c0 = _commit(self.repo, "c0", {"a.txt": "1\n"})
        _commit(self.repo, "c1 release bump no changelog", {"pyproject.toml": 'version = "0.2.0"\n'})
        _commit(self.repo, "c2 unrelated", {"a.txt": "2\n"})  # tip; HEAD~1 would only see this
        with _env(GITHUB_BASE_REF=None, GITHUB_REF="refs/heads/main", DOCS_AUDIT_PUSH_BEFORE=c0):
            base, ctx = docs_audit.resolve_base(self.repo, None)
            self.assertEqual((base, ctx), (c0, "push"))
            r = docs_audit.run_audit(self.repo, base=None)
            # the release bump is in c1 (below the tip) → caught only because we diff c0..HEAD
            self.assertEqual(r.docs_freshness, "blocked")

    def test_first_push_all_zeros_falls_back(self):
        _commit(self.repo, "root", {"a.txt": "1\n"})
        with _env(GITHUB_BASE_REF=None, GITHUB_REF="refs/heads/main", DOCS_AUDIT_PUSH_BEFORE="0" * 40):
            base, ctx = docs_audit.resolve_base(self.repo, None)
            self.assertEqual(ctx, "push_first")
            self.assertEqual(base, docs_audit._EMPTY_TREE)  # no HEAD~1 on a single-commit repo

    def test_tag_context_prior_tag(self):
        _commit(self.repo, "c0", {"a.txt": "1\n"})
        _git(self.repo, "tag", "-m", "v0.1.0", "v0.1.0")
        _commit(self.repo, "c1", {"a.txt": "2\n"})
        _git(self.repo, "tag", "-m", "v0.2.0", "v0.2.0")
        with _env(GITHUB_BASE_REF=None, GITHUB_REF="refs/tags/v0.2.0", DOCS_AUDIT_PUSH_BEFORE=None):
            self.assertEqual(docs_audit.resolve_base(self.repo, None), ("v0.1.0", "push_tag"))

    def test_first_tag_falls_back(self):
        # First-ever tag: no prior tag from HEAD^ → fall back, never base_unresolved (#8).
        _commit(self.repo, "c0", {"a.txt": "1\n"})
        _commit(self.repo, "c1", {"a.txt": "2\n"})
        _git(self.repo, "tag", "-m", "v0.1.0", "v0.1.0")
        with _env(GITHUB_BASE_REF=None, GITHUB_REF="refs/tags/v0.1.0", DOCS_AUDIT_PUSH_BEFORE=None):
            base, ctx = docs_audit.resolve_base(self.repo, None)
            self.assertEqual(ctx, "push_tag_first")
            self.assertEqual(base, "HEAD~1")  # exists → preferred over the empty tree


if __name__ == "__main__":
    unittest.main()
