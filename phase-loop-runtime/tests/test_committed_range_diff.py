"""FAB (Consiliency/agent-harness#191) piece 3b-consumer — the committed-range
review primitive `governed_bundle.committed_range_diff`. Deliberately UNMARKED so
CI runs it (the goal-id-inc2 lesson). Uses a real git repo.

Security focus (automated commit-review hardening): both SHA args are validated
fail-closed BEFORE the subprocess, so a `-`/`--flag`-leading value or a ref/branch
name can never be smuggled to `git diff` as an option and alter what the delta
review is shown vs what gets authenticated.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from phase_loop_runtime.governed_bundle import committed_range_diff


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _one_commit_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "work"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "c0")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "a.py").write_text("x = 2\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "c1")
    head = _git(repo, "rev-parse", "HEAD")
    return repo, base, head


def test_shows_the_committed_range_delta(tmp_path: Path):
    repo, base, head = _one_commit_repo(tmp_path)
    diff = committed_range_diff(repo, base, head)
    assert "-x = 1" in diff and "+x = 2" in diff, diff


def test_empty_range_between_a_sha_and_itself(tmp_path: Path):
    repo, _base, head = _one_commit_repo(tmp_path)
    assert committed_range_diff(repo, head, head) == "(empty committed range diff)"


@pytest.mark.parametrize(
    "bad",
    [
        "--upload-pack=/bin/false",  # flag-leading (the argv flag-smuggling case)
        "-Rbase",                    # short-flag-leading
        "main",                      # a ref/branch name, not a resolved SHA
        "HEAD",                      # a symbolic ref
        "abc123",                    # < 7 hex (too short to be a resolved OID)
        "zzzzzzz",                   # non-hex
        "",                          # empty
    ],
)
def test_non_resolved_sha_fails_closed(tmp_path: Path, bad: str):
    """A base/head that is not a RESOLVED SHA raises ValueError BEFORE any git
    call — never a sentinel string (a validation failure is a caller bug / attack,
    not a transient render failure) and never reaching `git diff`."""
    repo, base, head = _one_commit_repo(tmp_path)
    with pytest.raises(ValueError):
        committed_range_diff(repo, bad, head)
    with pytest.raises(ValueError):
        committed_range_diff(repo, base, bad)
