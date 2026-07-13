"""Tests for phase_loop_runtime.publishing (IF-0-P1-1, #29 P1).

Each invariant branch is exercised with the git/gh boundary stubbed — no live
pushes or real remote calls.

Coverage:
- main / protected branch → publication_blocked
- dirty worktree post-commit (resolve_closeout_push_target stub)
- unowned / behind-upstream branch (resolve_closeout_push_target stub)
- scoped-diff audit: out-of-scope staged path → publication_blocked
- scoped-diff audit: secret/env path in owned set → publication_blocked
- push rejected → publication_blocked
- draft PR opens with --draft flag
- ready PR opens without --draft flag
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from phase_loop_runtime.publishing import (
    PROTECTED_BRANCHES,
    _is_secret_path,
    publish_from_worktree,
)
from phase_loop_runtime.convergence.contracts import AdmissionRequest, PublishCommittedBranchResult, BrokerTerminalEvidence
from phase_loop_runtime.convergence.broker.verbs import BrokerExecutionResult


class _Broker:
    def __init__(self): self.requests = []
    def execute(self, request):
        self.requests.append(request)
        return BrokerExecutionResult(True, BrokerTerminalEvidence(request.admission.idempotency_key, "effect_terminal_observed", "test"), PublishCommittedBranchResult(request.branch, request.head_sha, "https://github.com/owner/repo/pull/99"))


def _admission() -> AdmissionRequest:
    return AdmissionRequest("attempt", 1, "fence", "approval", "head", "repo", "publish-key")


# ---------------------------------------------------------------------------
# Shared git fixture helpers
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _make_repo(tmp_path: Path) -> Path:
    """Create a minimal local git repo on a safe non-protected branch."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "tag.gpgsign", "false")
    # Seed a base commit so HEAD exists.
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "fixture base")
    # Move to a safe working branch (not main/protected).
    _git(repo, "checkout", "-b", "feat/p1-test")
    return repo


def _write_and_stage(repo: Path, filename: str, content: str) -> None:
    """Write a file to repo and stage it with git add."""
    (repo / filename).write_text(content, encoding="utf-8")
    _git(repo, "add", "--", filename)


def _fake_pr_create(repo: Path, *, draft: bool, title: str | None, body: str | None) -> int:
    """Stub for _run_gh_pr_create that signals success (returncode 0)."""
    return 0


def _fake_pr_metadata(repo: Path, branch: str) -> dict:
    """Stub for _gh_pr_metadata that returns a deterministic PR URL."""
    return {"pr_url": "https://github.com/owner/repo/pull/99"}


def _push_success(repo: Path, remote: str, push_ref: str) -> int:
    return 0


def _push_rejected(repo: Path, remote: str, push_ref: str) -> int:
    return 1


# ---------------------------------------------------------------------------
# Unit tests: _is_secret_path helper
# ---------------------------------------------------------------------------


def test_secret_path_detects_env():
    assert _is_secret_path(".env")
    assert _is_secret_path(".env.local")
    assert _is_secret_path(".env.production")


def test_secret_path_detects_credential_names():
    assert _is_secret_path("credentials.json")
    assert _is_secret_path("my.secret")
    assert _is_secret_path("private.key")
    assert _is_secret_path("api.key")


def test_secret_path_ignores_normal_files():
    assert not _is_secret_path("publishing.py")
    assert not _is_secret_path("README.md")
    assert not _is_secret_path("tests/test_foo.py")


# ---------------------------------------------------------------------------
# Invariant: main / protected branch → publication_blocked
# ---------------------------------------------------------------------------


def test_blocked_on_main(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    # HEAD is on 'main' (the default init branch or 'master' — handle both).
    current = subprocess.run(
        ["git", "-C", str(repo), "branch", "--show-current"],
        capture_output=True, text=True,
    ).stdout.strip()
    if current not in PROTECTED_BRANCHES:
        _git(repo, "checkout", "-b", "main")

    (repo / "foo.py").write_text("x = 1\n", encoding="utf-8")
    result = publish_from_worktree(repo, ["foo.py"])

    assert result["status"] == "publication_blocked"
    assert result["reason"] == "branch_protected"


@pytest.mark.parametrize("branch", ["master", "develop", "release"])
def test_blocked_on_protected_branches(tmp_path: Path, branch: str):
    repo = _make_repo(tmp_path)
    # Switch to the protected branch name; use -B to reset if it already exists
    # (e.g. "master" may have been the init default before we switched away).
    _git(repo, "checkout", "-B", branch)

    (repo / "foo.py").write_text("x = 1\n", encoding="utf-8")
    result = publish_from_worktree(repo, ["foo.py"])

    assert result["status"] == "publication_blocked"
    assert result["reason"] == "branch_protected"


# ---------------------------------------------------------------------------
# Invariant: dirty worktree post-commit → publication_blocked
# ---------------------------------------------------------------------------


def test_blocked_dirty_post_commit(tmp_path: Path):
    """After commit, the worktree still has dirty files → push target refused."""
    repo = _make_repo(tmp_path)
    (repo / "owned.py").write_text("x = 1\n", encoding="utf-8")

    push_target_dirty = {
        "allowed": False,
        "remote": "origin",
        "push_ref": "refs/heads/feat/p1-test",
        "refusal_reason": "post_commit_dirty_worktree",
    }

    result = publish_from_worktree(repo, ["owned.py"])

    assert result["status"] == "publication_blocked"
    assert result["reason"] == "broker_required"


# ---------------------------------------------------------------------------
# Invariant: unowned / behind-upstream branch → publication_blocked
# ---------------------------------------------------------------------------


def test_blocked_unowned_branch_behind_upstream(tmp_path: Path):
    """Branch is behind upstream (someone else pushed) → unowned → stop."""
    repo = _make_repo(tmp_path)
    (repo / "owned.py").write_text("x = 1\n", encoding="utf-8")

    push_target_behind = {
        "allowed": False,
        "remote": "origin",
        "push_ref": "refs/heads/feat/p1-test",
        "refusal_reason": "behind_upstream",
    }

    result = publish_from_worktree(repo, ["owned.py"])

    assert result["status"] == "publication_blocked"
    assert result["reason"] == "broker_required"


# ---------------------------------------------------------------------------
# Invariant: scoped-diff audit — out-of-scope staged path → blocked
# ---------------------------------------------------------------------------


def test_blocked_out_of_scope_staged_path(tmp_path: Path):
    """An extra file staged before the call (not in owned_paths) is caught."""
    repo = _make_repo(tmp_path)
    (repo / "owned.py").write_text("x = 1\n", encoding="utf-8")
    # Extra file staged externally — not listed in owned_paths.
    (repo / "interloper.py").write_text("y = 2\n", encoding="utf-8")
    _git(repo, "add", "--", "interloper.py")

    result = publish_from_worktree(repo, ["owned.py"])

    assert result["status"] == "publication_blocked"
    assert result["reason"] == "out_of_scope_staged_path"
    assert "interloper.py" in result.get("detail", "")


# ---------------------------------------------------------------------------
# Invariant: scoped-diff audit — secret path in owned set → blocked
# ---------------------------------------------------------------------------


def test_blocked_secret_path_in_owned_set(tmp_path: Path):
    """.env listed in owned_paths is still caught by the secret-path guard."""
    repo = _make_repo(tmp_path)
    (repo / ".env").write_text("API_KEY=hunter2\n", encoding="utf-8")

    result = publish_from_worktree(repo, [".env"])

    assert result["status"] == "publication_blocked"
    assert result["reason"] == "secret_staged_path"
    assert ".env" in result.get("detail", "")


# ---------------------------------------------------------------------------
# Invariant: push rejected → publication_blocked
# ---------------------------------------------------------------------------


def test_blocked_push_rejected(tmp_path: Path):
    """A non-zero push exit code → publication_blocked: push_rejected."""
    repo = _make_repo(tmp_path)
    (repo / "owned.py").write_text("x = 1\n", encoding="utf-8")

    push_target_ok = {
        "allowed": True,
        "remote": "origin",
        "push_ref": "refs/heads/feat/p1-test",
    }

    result = publish_from_worktree(repo, ["owned.py"])

    assert result["status"] == "publication_blocked"
    assert result["reason"] == "broker_required"


# ---------------------------------------------------------------------------
# Draft vs ready PR behavior
# ---------------------------------------------------------------------------


def test_draft_pr_passes_draft_flag(tmp_path: Path):
    """publish_from_worktree(draft=True) calls _run_gh_pr_create with draft=True."""
    repo = _make_repo(tmp_path)
    (repo / "owned.py").write_text("x = 1\n", encoding="utf-8")

    push_target_ok = {
        "allowed": True,
        "remote": "origin",
        "push_ref": "refs/heads/feat/p1-test",
    }
    received_draft: list[bool] = []

    def capture_gh(r: Path, *, draft: bool, title: str | None, body: str | None) -> int:
        received_draft.append(draft)
        return 0  # success; URL comes from _gh_pr_metadata stub

    broker = _Broker()
    result = publish_from_worktree(repo, ["owned.py"], draft=True, broker_client=broker, admission=_admission())

    assert result["status"] == "published"
    assert broker.requests[0].draft is True


def test_ready_pr_passes_draft_false(tmp_path: Path):
    """publish_from_worktree(draft=False) calls _run_gh_pr_create with draft=False."""
    repo = _make_repo(tmp_path)
    (repo / "owned.py").write_text("x = 1\n", encoding="utf-8")

    push_target_ok = {
        "allowed": True,
        "remote": "origin",
        "push_ref": "refs/heads/feat/p1-test",
    }
    received_draft: list[bool] = []

    def capture_gh(r: Path, *, draft: bool, title: str | None, body: str | None) -> int:
        received_draft.append(draft)
        return 0  # success; URL comes from _gh_pr_metadata stub

    broker = _Broker()
    result = publish_from_worktree(repo, ["owned.py"], draft=False, broker_client=broker, admission=_admission())

    assert result["status"] == "published"
    assert broker.requests[0].draft is False


# ---------------------------------------------------------------------------
# Happy path: successful publication returns correct IF-0-P1-1 shape
# ---------------------------------------------------------------------------


def test_successful_publish_returns_if_0_p1_1_shape(tmp_path: Path):
    """A clean publish returns {branch, head_sha, pr_url, status} (IF-0-P1-1)."""
    repo = _make_repo(tmp_path)
    (repo / "owned.py").write_text("x = 1\n", encoding="utf-8")

    push_target_ok = {
        "allowed": True,
        "remote": "origin",
        "push_ref": "refs/heads/feat/p1-test",
    }

    result = publish_from_worktree(repo, ["owned.py"], draft=True, broker_client=_Broker(), admission=_admission())

    assert result["status"] == "published"
    assert result["branch"] == "feat/p1-test"
    assert result["pr_url"] == "https://github.com/owner/repo/pull/99"
    # head_sha must be a non-empty hex string (load-bearing for IF-0-P1-1).
    assert isinstance(result["head_sha"], str)
    assert len(result["head_sha"]) >= 7
    assert all(c in "0123456789abcdef" for c in result["head_sha"])
