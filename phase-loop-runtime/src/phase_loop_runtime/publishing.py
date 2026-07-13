"""Runtime publishing primitive — IF-0-P1-1 (#29 P1).

Factors the #28 worktree→branch→verify→commit→push→PR flow out of the
execute-skill prose into a runtime module the coordinator (P3+) and the
execute skills both call, so the safety controls are code, not interpretation.

Contract (IF-0-P1-1): ``publish_from_worktree(repo, owned_paths, ...)``
returns ``{branch, head_sha, pr_url, status}`` on success, or
``{status: "publication_blocked", reason: str, detail: str}`` on any invariant
violation.  The ``branch`` and ``head_sha`` fields are load-bearing: the
coordinator (P3) injects them into downstream nodes via IF-0-P2-2.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Sequence

from .git_topology import collect_git_topology
from .convergence.contracts import AdmissionRequest, BrokerRequest, BrokerVerb
from .convergence.broker.verbs import BrokerClient

# Branches that are never valid publication targets.
PROTECTED_BRANCHES: frozenset[str] = frozenset({"main", "master", "develop", "release"})


def _is_secret_path(path: str) -> bool:
    """Return True if the path looks like a secret, credential, or .env file.

    Used during the staged-diff audit to reject any file whose name matches
    common credential/env patterns, even if the caller listed it in owned_paths.
    """
    name = Path(path).name.lower()
    # .env, .env.local, .env.production, .env.test, etc.
    if name.startswith(".env"):
        return True
    # Common credential / secret file names.
    for fragment in ("credential", "secret", ".key", "private"):
        if fragment in name:
            return True
    return False


def _blocked(reason: str, detail: str = "") -> dict[str, Any]:
    """Return a structured publication_blocked result."""
    result: dict[str, Any] = {"status": "publication_blocked", "reason": reason}
    if detail:
        result["detail"] = detail
    return result


def _git_run(repo: Path, *args: str) -> int:
    """Run a git command; return returncode."""
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed.returncode


def _git_output(repo: Path, *args: str) -> str | None:
    """Run a git command; return stripped stdout or None on failure."""
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


# ---------------------------------------------------------------------------
# Public API — IF-0-P1-1
# ---------------------------------------------------------------------------


def publish_from_worktree(
    repo: Path,
    owned_paths: Sequence[str],
    *,
    draft: bool = True,
    pr_title: str | None = None,
    pr_body: str | None = None,
    commit_message: str | None = None,
    topology: dict[str, Any] | None = None,
    protected_branches: frozenset[str] = PROTECTED_BRANCHES,
    prebuilt: bool = False,
    broker_client: BrokerClient | None = None,
    admission: AdmissionRequest | None = None,
) -> dict[str, Any]:
    """Perform the #28 publish flow for one repo/worktree (IF-0-P1-1).

    Parameters
    ----------
    repo:
        Repository (or worktree) root.  All git operations run here.
    owned_paths:
        File paths (relative to *repo*) this run owns and intends to publish.
        Only these paths are staged; no ``git add -A`` is ever used.
    draft:
        Open the PR as a draft when True (dependencies remain or verification
        partial).  Open as ready (``--fill``) when False.
    pr_title:
        Optional PR title override (passed to ``gh pr create --title``).
    pr_body:
        Optional PR body override (passed to ``gh pr create --body``).
    commit_message:
        Commit message override.  Defaults to ``"chore: publish plan changes"``.
    topology:
        Pre-collected git topology dict.  When None, topology is collected via
        ``collect_git_topology(repo)``.  Useful in tests or when the caller
        already has topology.
    protected_branches:
        Branch names that are never valid publication targets.  Defaults to
        ``PROTECTED_BRANCHES``.

    Returns
    -------
    dict
        On success: ``{status: "published", branch, head_sha, pr_url}``.
        On any invariant violation: ``{status: "publication_blocked",
        reason: <reason-slug>, detail: <human-readable explanation>}``.

    Safety invariants enforced (fail-closed):
    - Never publish from ``main`` or a protected branch.
    - Never ``git add -A``; stage only *owned_paths* by explicit path.
    - Staged-diff audit: staged set must be a subset of *owned_paths*; no
      secret/credential/``.env`` paths; ``git diff --cached --check`` clean.
    - Commit, then ``resolve_closeout_push_target`` re-checks the post-commit
      state (dirty worktree → stop; behind upstream → stop / unowned branch).
    - Push without ``--force``; a rejected push returns publication_blocked.
    - ``gh pr create`` opens a draft or ready PR per intent.
    """
    topo = dict(topology or collect_git_topology(repo))

    if not topo.get("available"):
        return _blocked("not_a_git_worktree", "Repo is not a git worktree")

    branch = topo.get("branch", "")

    # Invariant: never publish from main or a protected branch.
    if not branch or branch.startswith("detached@"):
        return _blocked("detached_head", "Cannot publish from detached HEAD state")
    if branch in protected_branches:
        return _blocked(
            "branch_protected",
            f"Cannot publish from protected branch {branch!r}; "
            "establish a worktree/branch first (Workflow step 6)",
        )

    if prebuilt:
        head_sha = _git_output(repo, "rev-parse", "HEAD")
        if not head_sha:
            return _blocked("head_sha_missing", "Could not resolve prebuilt HEAD")
    elif not owned_paths:
        return _blocked("no_owned_paths", "No owned paths to stage; nothing to publish")

    # Stage owned paths by explicit name — never git add -A.
    stage_rc = 0 if prebuilt else _git_run(repo, "add", "--", *owned_paths)
    if stage_rc != 0:
        return _blocked(
            "stage_failed",
            f"git add -- <owned_paths> failed (rc={stage_rc}); "
            "check that owned paths exist in the worktree",
        )

    # Staged-diff audit ---------------------------------------------------
    audit_result = None if prebuilt else _audit_staged_diff(repo, owned_paths)
    if audit_result is not None:
        return audit_result

    # Commit --------------------------------------------------------------
    msg = commit_message or "chore: publish plan changes"
    commit_rc = 0 if prebuilt else _git_run(repo, "commit", "-m", msg)
    if commit_rc != 0:
        return _blocked(
            "commit_failed",
            f"git commit failed (rc={commit_rc}); "
            "possibly nothing was staged or a pre-commit hook rejected the commit",
        )

    # Capture head_sha immediately after commit (load-bearing for IF-0-P1-1).
    head_sha = _git_output(repo, "rev-parse", "HEAD")
    if not head_sha:
        return _blocked("head_sha_missing", "Could not resolve HEAD after commit")

    if pr_title is not None:
        return _blocked("custom_title_unsupported", "BrokerRequest has no pr_title field")
    if broker_client is None or admission is None:
        return _blocked("broker_required", "publish mutation requires an admitted broker client")
    execution = broker_client.execute(BrokerRequest(BrokerVerb.PUBLISH_COMMITTED_BRANCH, admission, str(repo), branch, head_sha, tuple(owned_paths), draft=draft, pr_body=pr_body or ""))
    if not execution.accepted or execution.publish_result is None:
        return _blocked(execution.reason or execution.evidence.terminal_state, execution.evidence.evidence_reference)
    return {"status": "published", "branch": execution.publish_result.branch, "head_sha": execution.publish_result.head_sha, "pr_url": execution.publish_result.pr_url}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _audit_staged_diff(repo: Path, owned_paths: Sequence[str]) -> dict[str, Any] | None:
    """Audit the staged diff against the safety invariants.

    Returns a ``publication_blocked`` dict on the first violation, or ``None``
    when the staged set is clean.  Checks (in order):

    1. Something must be staged (nothing staged → blocked).
    2. Every staged path must be in *owned_paths* (no out-of-scope files).
    3. No staged path may look like a secret/credential/``.env`` file.
    4. ``git diff --cached --check`` must pass (no trailing whitespace etc.).
    """
    owned_set = {Path(p).as_posix() for p in owned_paths}

    staged_raw = _git_output(repo, "diff", "--cached", "--name-only")
    staged_paths = [p.strip() for p in (staged_raw or "").splitlines() if p.strip()]

    if not staged_paths:
        return _blocked(
            "nothing_staged",
            "No changes were staged after git add; "
            "owned paths may already be clean or not modified",
        )

    for path in staged_paths:
        norm = Path(path).as_posix()
        if norm not in owned_set:
            return _blocked(
                "out_of_scope_staged_path",
                f"Staged path {path!r} is not in the owned-paths set; "
                "unstage it or add it to owned_paths explicitly",
            )
        if _is_secret_path(path):
            return _blocked(
                "secret_staged_path",
                f"Staged path {path!r} matches a secret/credential/.env pattern; "
                "remove it from owned_paths and unstage it",
            )

    # Trailing-whitespace / mixed-indent check.
    check_rc = _git_run(repo, "diff", "--cached", "--check")
    if check_rc != 0:
        return _blocked(
            "staged_check_failed",
            "git diff --cached --check found trailing whitespace or mixed indent; "
            "fix and restage before publishing",
        )

    return None
