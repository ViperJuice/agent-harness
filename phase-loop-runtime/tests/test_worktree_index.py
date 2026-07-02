"""Tests for the CS-0.10a worktree-index freshness pointer (`phase_loop_runtime.worktree_index`).

Purely git-derived, read-only: builds a real bare `origin` + clone + a second
`git worktree add` off it to match the shape the tool is meant to answer for —
"where is the freshest working copy of a path, and who's touching it".
"""
from __future__ import annotations

import io
import json
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from phase_loop_runtime import worktree_index as wi
from phase_loop_runtime.cli import build_parser, main as cli_main


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _configure(repo: Path) -> None:
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


class WorktreeIndexTest(unittest.TestCase):
    """Sets up: origin.git (bare) <- repo (clone, branch main, pushed) with a
    second `git worktree add` branch (`feat/touch`) that edits one file and
    never pushes — the shape a real CS-0.10a query answers over."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        root = Path(self.tmp.name)
        self.origin = root / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.origin)], check=True)

        self.repo = root / "repo"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.repo)], check=True)
        _configure(self.repo)
        _git(self.repo, "checkout", "-q", "-B", "main")
        _commit(self.repo, "init", {"README.md": "hi\n", "keep/untouched.txt": "static\n"})
        subprocess.run(["git", "-C", str(self.repo), "push", "-q", "-u", "origin", "main"], check=True)

        self.worktree_path = root / "repo-feat-touch"
        subprocess.run(
            ["git", "-C", str(self.repo), "worktree", "add", "-q", "-b", "feat/touch", str(self.worktree_path), "origin/main"],
            check=True,
        )
        _configure(self.worktree_path)
        self.touch_sha = _commit(self.worktree_path, "touch a file", {"docs/touched.txt": "new\n"})
        self.edit_sha = _commit(self.worktree_path, "edit an existing file", {"README.md": "hi\nedited\n"})

    def tearDown(self):
        self.tmp.cleanup()

    def test_untouched_path_returns_origin_main_only(self):
        report = wi.build_index(self.repo, path="keep/untouched.txt")
        self.assertEqual(len(report.paths), 1)
        pf = report.paths[0]
        self.assertEqual(pf.path, "keep/untouched.txt")
        self.assertFalse(pf.main_behind)
        self.assertEqual(len(pf.holders), 1)
        self.assertEqual(pf.holders[0].worktree, "origin/main")

    def test_edited_existing_path_returns_both_baseline_and_branch_holder(self):
        # Acceptance case: branch B EDITS a file that already exists on origin/main
        # (not a brand-new file) — holders must include both the origin/main
        # baseline copy and B, with main flagged behind.
        report = wi.build_index(self.repo, path="README.md")
        self.assertEqual(len(report.paths), 1)
        pf = report.paths[0]
        self.assertTrue(pf.main_behind)
        holder_by_branch = {h.branch: h for h in pf.holders}
        self.assertIn("origin/main", holder_by_branch)
        self.assertIn("feat/touch", holder_by_branch)
        branch_holder = holder_by_branch["feat/touch"]
        self.assertEqual(branch_holder.worktree, str(self.worktree_path))
        self.assertEqual(branch_holder.last_commit_sha, self.edit_sha)

    def test_touched_path_returns_worktree_holder_and_flags_main_behind(self):
        report = wi.build_index(self.repo, path="docs/touched.txt")
        self.assertEqual(len(report.paths), 1)
        pf = report.paths[0]
        self.assertTrue(pf.main_behind)
        holder_by_branch = {h.branch: h for h in pf.holders}
        self.assertIn("feat/touch", holder_by_branch)
        holder = holder_by_branch["feat/touch"]
        self.assertEqual(holder.worktree, str(self.worktree_path))
        self.assertEqual(holder.last_commit_sha, self.touch_sha)
        # no baseline entry: the file doesn't exist on origin/main at all
        self.assertNotIn("origin/main", holder_by_branch)

    def test_all_worktrees_enumerated(self):
        report = wi.build_index(self.repo, path="docs/touched.txt")
        paths = {wt.path for wt in report.worktrees}
        self.assertIn(str(self.repo.resolve()), {str(Path(p).resolve()) for p in paths})
        self.assertIn(str(self.worktree_path.resolve()), {str(Path(p).resolve()) for p in paths})

    def test_no_query_reports_every_touched_path(self):
        report = wi.build_index(self.repo)
        touched = {pf.path for pf in report.paths}
        self.assertEqual(touched, {"docs/touched.txt", "README.md"})

    def test_repo_with_no_extra_worktrees_answers_origin_main(self):
        with TemporaryDirectory() as td2:
            root2 = Path(td2)
            origin2 = root2 / "origin.git"
            subprocess.run(["git", "init", "-q", "--bare", str(origin2)], check=True)
            repo2 = root2 / "repo"
            subprocess.run(["git", "clone", "-q", str(origin2), str(repo2)], check=True)
            _configure(repo2)
            _git(repo2, "checkout", "-q", "-B", "main")
            _commit(repo2, "init", {"a.txt": "1\n"})
            subprocess.run(["git", "-C", str(repo2), "push", "-q", "-u", "origin", "main"], check=True)

            report = wi.build_index(repo2, path="a.txt")
            self.assertEqual(len(report.worktrees), 1)
            self.assertEqual(len(report.paths), 1)
            self.assertEqual(report.paths[0].holders[0].worktree, "origin/main")
            self.assertFalse(report.paths[0].main_behind)

    def test_never_writes_repo_state(self):
        before = _git(self.repo, "status", "--porcelain", "--untracked-files=all")
        wi.build_index(self.repo, path="docs/touched.txt")
        wi.build_index(self.repo)
        after = _git(self.repo, "status", "--porcelain", "--untracked-files=all")
        self.assertEqual(before, after)

    def test_json_and_human_output(self):
        report = wi.build_index(self.repo, path="docs/touched.txt")
        payload = report.to_json()
        self.assertEqual(payload["paths"][0]["path"], "docs/touched.txt")
        self.assertTrue(payload["paths"][0]["main_behind"])
        rendered = wi.render_human(report)
        self.assertIn("docs/touched.txt", rendered)
        self.assertIn("main behind", rendered)

    def test_cli_dispatch_json_and_human(self):
        # --repo is a per-subcommand argument (like every other subcommand here);
        # it belongs after the subcommand token, matching this suite's convention.
        parser = build_parser()
        args = parser.parse_args(["worktree-index", "--repo", str(self.repo), "--path", "docs/touched.txt", "--json"])
        self.assertEqual(args.command, "worktree-index")

        json_out = io.StringIO()
        with redirect_stdout(json_out):
            rc = cli_main(["worktree-index", "--repo", str(self.repo), "--path", "docs/touched.txt", "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(json_out.getvalue())
        self.assertTrue(payload["paths"][0]["main_behind"])

        human_out = io.StringIO()
        with redirect_stdout(human_out):
            rc = cli_main(["worktree-index", "--repo", str(self.repo), "--path", "docs/touched.txt"])
        self.assertEqual(rc, 0)
        self.assertIn("docs/touched.txt", human_out.getvalue())


if __name__ == "__main__":
    unittest.main()
