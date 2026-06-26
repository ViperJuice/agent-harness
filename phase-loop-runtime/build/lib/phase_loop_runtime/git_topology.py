from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

from .models import LoopEvent, StateSnapshot


def attach_git_topology(repo: Path, value: LoopEvent | StateSnapshot) -> LoopEvent | StateSnapshot:
    if value.git_topology:
        return value
    return replace(value, git_topology=collect_git_topology(repo))


def collect_git_topology(repo: Path) -> dict[str, Any]:
    if not _git_available(repo):
        return {"available": False, "reason": "not a git worktree"}

    topology: dict[str, Any] = {
        "available": True,
        "branch": _branch(repo),
        "head": _git(repo, "rev-parse", "HEAD"),
        "status_short_branch": _git(repo, "status", "--short", "--branch"),
    }
    topology["clean"] = not bool(_git(repo, "status", "--short"))

    upstream = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if upstream:
        topology["upstream_ref"] = upstream
        topology["upstream_head"] = _git(repo, "rev-parse", upstream)
        behind, ahead = _ahead_behind(repo, upstream)
        if ahead is not None:
            topology["ahead_of_upstream"] = ahead
        if behind is not None:
            topology["behind_upstream"] = behind

    default_remote = _git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if default_remote:
        topology["default_remote_ref"] = default_remote

    base_ref = os.environ.get("PHASE_LOOP_BASE_REF") or upstream or default_remote or _existing_ref(repo, "origin/main")
    if base_ref:
        topology["base_ref"] = base_ref
        topology["base_head"] = _git(repo, "rev-parse", base_ref)
        behind, ahead = _ahead_behind(repo, base_ref)
        if ahead is not None:
            topology["ahead_of_base"] = ahead
        if behind is not None:
            topology["behind_base"] = behind

    target_push_ref = os.environ.get("PHASE_LOOP_TARGET_PUSH_REF")
    if target_push_ref:
        topology["target_push_ref"] = target_push_ref
    pr_head_ref = os.environ.get("PHASE_LOOP_PR_HEAD_REF") or os.environ.get("GITHUB_HEAD_REF")
    if pr_head_ref:
        topology["pr_head_ref"] = pr_head_ref
    pr_base_ref = os.environ.get("PHASE_LOOP_PR_BASE_REF") or os.environ.get("GITHUB_BASE_REF")
    if pr_base_ref:
        topology["pr_base_ref"] = pr_base_ref
    pr_url = os.environ.get("PHASE_LOOP_PR_URL")
    if pr_url:
        topology["pr_url"] = pr_url
    elif "pr_url" not in topology:
        topology.update(_matching_pr_metadata(repo, topology.get("head"), default_remote))

    return {key: value for key, value in topology.items() if value not in (None, "")}


def resolve_closeout_push_target(repo: Path, topology: dict[str, Any] | None = None) -> dict[str, Any]:
    topology = dict(topology or collect_git_topology(repo))
    if not topology.get("available"):
        return {"allowed": False, "refusal_reason": "not_a_git_worktree"}

    explicit_target = topology.get("target_push_ref")
    upstream = topology.get("upstream_ref")
    remote = _remote_from_upstream(upstream)
    if explicit_target:
        push_ref = str(explicit_target)
        if remote is None:
            remote = _default_push_remote(repo, topology)
        if remote is None:
            return {"allowed": False, "refusal_reason": "missing_push_remote"}
    elif upstream:
        remote, branch_ref = _split_upstream_ref(str(upstream))
        if remote is None or branch_ref is None:
            return {"allowed": False, "refusal_reason": "ambiguous_upstream_ref"}
        push_ref = branch_ref
    else:
        return {"allowed": False, "refusal_reason": "missing_push_target"}

    if not topology.get("clean", False):
        return {"allowed": False, "remote": remote, "push_ref": push_ref, "refusal_reason": "post_commit_dirty_worktree"}
    if int(topology.get("behind_upstream", 0) or 0) > 0:
        return {"allowed": False, "remote": remote, "push_ref": push_ref, "refusal_reason": "behind_upstream"}

    return {"allowed": True, "remote": remote, "push_ref": push_ref}


def _git_available(repo: Path) -> bool:
    return _git(repo, "rev-parse", "--is-inside-work-tree") == "true"


def _branch(repo: Path) -> str:
    branch = _git(repo, "branch", "--show-current")
    if branch:
        return branch
    head = _git(repo, "rev-parse", "--short", "HEAD")
    return f"detached@{head}" if head else "unknown"


def _existing_ref(repo: Path, ref: str) -> str | None:
    return ref if _git(repo, "rev-parse", "--verify", "--quiet", ref) else None


def _ahead_behind(repo: Path, base_ref: str) -> tuple[int | None, int | None]:
    counts = _git(repo, "rev-list", "--left-right", "--count", f"{base_ref}...HEAD")
    if not counts:
        return None, None
    parts = counts.split()
    if len(parts) != 2:
        return None, None
    try:
        behind = int(parts[0])
        ahead = int(parts[1])
    except ValueError:
        return None, None
    return behind, ahead


def _remote_from_upstream(upstream_ref: Any) -> str | None:
    if not isinstance(upstream_ref, str) or "/" not in upstream_ref:
        return None
    return upstream_ref.split("/", 1)[0] or None


def _split_upstream_ref(upstream_ref: str) -> tuple[str | None, str | None]:
    if "/" not in upstream_ref:
        return None, None
    remote, branch = upstream_ref.split("/", 1)
    if not remote or not branch:
        return None, None
    return remote, f"refs/heads/{branch}"


def _default_push_remote(repo: Path, topology: dict[str, Any]) -> str | None:
    upstream_remote = _remote_from_upstream(topology.get("upstream_ref"))
    if upstream_remote:
        return upstream_remote
    default_remote = topology.get("default_remote_ref")
    if isinstance(default_remote, str) and "/" in default_remote:
        return default_remote.split("/", 1)[0]
    remotes = _git(repo, "remote")
    if not remotes:
        return None
    names = [line.strip() for line in remotes.splitlines() if line.strip()]
    if len(names) == 1:
        return names[0]
    if "origin" in names:
        return "origin"
    return None


def _matching_pr_metadata(repo: Path, head: Any, default_remote: str | None) -> dict[str, Any]:
    if not isinstance(head, str) or not head:
        return {}
    default_branch = default_remote.removeprefix("origin/") if default_remote else "main"
    for ref in _remote_refs_at_head(repo, head):
        if ref == f"origin/{default_branch}":
            continue
        if not ref.startswith("origin/"):
            continue
        metadata = _gh_pr_metadata(repo, ref.removeprefix("origin/"))
        if metadata:
            metadata["matching_remote_ref"] = ref
            return metadata
    return {}


def _remote_refs_at_head(repo: Path, head: str) -> list[str]:
    refs = _git(repo, "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/remotes/origin")
    if not refs:
        return []
    matches: list[str] = []
    for line in refs.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == head:
            matches.append(parts[0])
    return matches


def _gh_pr_metadata(repo: Path, branch: str) -> dict[str, Any]:
    payload = _gh(repo, "pr", "list", "--head", branch, "--state", "open", "--limit", "1", "--json", "number,url,headRefName,baseRefName,reviewDecision,mergeable,isDraft")
    if not payload:
        return {}
    try:
        import json

        prs = json.loads(payload)
    except Exception:
        return {}
    if not isinstance(prs, list) or not prs:
        return {}
    pr = prs[0]
    if not isinstance(pr, dict):
        return {}
    return {
        "pr_number": pr.get("number"),
        "pr_url": pr.get("url"),
        "pr_head_ref": pr.get("headRefName"),
        "pr_base_ref": pr.get("baseRefName"),
        "pr_review_decision": pr.get("reviewDecision"),
        "pr_mergeable": pr.get("mergeable"),
        "pr_is_draft": pr.get("isDraft"),
    }


def _git(repo: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _gh(repo: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["gh", *args],
            cwd=repo,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None
