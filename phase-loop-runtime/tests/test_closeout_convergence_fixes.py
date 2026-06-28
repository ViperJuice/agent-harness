"""Tests for the closeout convergence fixes (issues #5 and #6).

#5: build-regenerated gitignored artifacts must not enter the closeout dirty set —
    previously they were classified ``unowned`` -> ``dirty_worktree_conflict`` and the
    runner re-dispatched a repair turn that re-ran the build and reproduced the same
    ignored output, looping forever.
#6: a phase whose verified work is already on the base branch (nothing to commit) must
    finalize as a no-op (``noop_already_committed``), not be mistaken for a commit
    failure and re-dispatched.
"""
from __future__ import annotations

import subprocess

from phase_loop_runtime.runner import (
    _closeout_nothing_staged,
    _dirty_paths,
    _gitignored_paths,
)
from phase_loop_test_utils import make_repo


def _git(repo, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


# --- #5: gitignored paths excluded from the dirty set ----------------------

def test_gitignored_paths_helper(tmp_path):
    repo = make_repo(tmp_path)
    (repo / ".gitignore").write_text("dist/\n*.log\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "ignores")
    ignored = _gitignored_paths(repo, ["dist/a.js", "app.log", "src/main.py"])
    assert ignored == {"dist/a.js", "app.log"}


def test_dirty_paths_excludes_tracked_then_gitignored(tmp_path):
    # The real #5 case: a path that is TRACKED but matches a gitignore pattern still
    # shows in `git status` (as modified) when a build regenerates it; check-ignore
    # must drop it from the dirty set so it never becomes spillover.
    repo = make_repo(tmp_path)
    (repo / "gen.py").write_text("v1\n", encoding="utf-8")
    _git(repo, "add", "gen.py")
    _git(repo, "commit", "-m", "track gen.py")
    (repo / ".gitignore").write_text("gen.py\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "now ignore gen.py")
    (repo / "gen.py").write_text("v2 regenerated\n", encoding="utf-8")  # build regenerates
    # a genuine, non-ignored change must still be reported:
    (repo / "real.txt").write_text("real change\n", encoding="utf-8")

    dirty = _dirty_paths(repo)
    assert "gen.py" not in dirty, dirty
    assert "real.txt" in dirty, dirty


def test_dirty_paths_keeps_non_ignored_untracked(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "new.py").write_text("x\n", encoding="utf-8")
    assert "src/new.py" in _dirty_paths(repo)


# --- #6: nothing-staged detection (no-op finalize) -------------------------

def test_closeout_nothing_staged_true_when_index_matches_head(tmp_path):
    repo = make_repo(tmp_path)
    # fresh repo from make_repo has a committed initial tree, nothing staged:
    assert _closeout_nothing_staged(repo) is True


def test_closeout_nothing_staged_false_when_changes_staged(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "x.txt").write_text("change\n", encoding="utf-8")
    _git(repo, "add", "x.txt")
    assert _closeout_nothing_staged(repo) is False
