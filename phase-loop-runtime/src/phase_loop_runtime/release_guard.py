from __future__ import annotations

import fnmatch
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .discovery import plan_metadata
# Canonical release-surface taxonomy lives in docs_surfaces (IF-0-P1-1); re-exported
# here so there is exactly one source of truth (no parallel copy).
from .docs_surfaces import RELEASE_AFFECTING_PATTERNS


RELEASE_DISPATCH_MUTATION = "release_dispatch"


@dataclass(frozen=True)
class ReleaseDispatchBlocker:
    blocker_class: str
    blocker_summary: str
    required_human_inputs: tuple[str, ...]
    metadata: dict[str, Any]

    def to_blocker(self) -> dict[str, Any]:
        return {
            "human_required": True,
            "blocker_class": self.blocker_class,
            "blocker_summary": self.blocker_summary,
            "required_human_inputs": self.required_human_inputs,
        }


def is_release_dispatch_plan(plan: Path | None) -> bool:
    if plan is None:
        return False
    metadata = plan_metadata(plan)
    if metadata.get("phase_loop_mutation") == RELEASE_DISPATCH_MUTATION:
        return True
    try:
        text = plan.read_text(encoding="utf-8").lower()
    except OSError:
        return False
    return "gh workflow run" in text and "release" in text


def release_dispatch_blocker(repo: Path, plan: Path | None) -> ReleaseDispatchBlocker | None:
    if plan is None or not is_release_dispatch_plan(plan):
        return None

    metadata = plan_metadata(plan)
    dirty_paths = _dirty_release_affecting_paths(repo)
    if dirty_paths:
        return ReleaseDispatchBlocker(
            blocker_class="dirty_worktree_conflict",
            blocker_summary=(
                "Release dispatch requires clean release-affecting files before the "
                "external workflow is started."
            ),
            required_human_inputs=(
                "Commit, merge, or isolate the release-affecting changes.",
                "Sync the release branch with the configured base ref, then rerun the dispatch phase.",
            ),
            metadata={
                "guard": "release_dispatch",
                "reason": "dirty_release_affecting_paths",
                "plan": str(plan),
                "dirty_paths": dirty_paths,
                "phase_loop_mutation": metadata.get("phase_loop_mutation"),
            },
        )

    base_ref, base_ref_explicit = _release_base_ref(metadata)
    sync = _branch_sync(repo, base_ref)
    if not sync["base_available"]:
        if not base_ref_explicit and not sync["base_remote_available"]:
            return None
        return ReleaseDispatchBlocker(
            blocker_class="branch_sync_conflict",
            blocker_summary=f"Release dispatch base ref `{base_ref}` is unavailable locally.",
            required_human_inputs=(
                f"Fetch or configure `{base_ref}` before rerunning release dispatch.",
                "Rerun the dispatch phase from a branch that can be compared to the release base.",
            ),
            metadata={
                "guard": "release_dispatch",
                "reason": "base_ref_unavailable",
                "plan": str(plan),
                **sync,
            },
        )
    if sync["head"] != sync["base_commit"]:
        return ReleaseDispatchBlocker(
            blocker_class="branch_sync_conflict",
            blocker_summary=(
                f"Release dispatch requires `HEAD` to match `{base_ref}` before the "
                "external workflow is started."
            ),
            required_human_inputs=(
                f"Merge or sync the release branch so `HEAD` matches `{base_ref}`.",
                "Rerun the dispatch phase from the clean synced branch.",
            ),
            metadata={
                "guard": "release_dispatch",
                "reason": "head_not_at_base_ref",
                "plan": str(plan),
                **sync,
            },
        )
    return None


def _release_base_ref(metadata: dict[str, str]) -> tuple[str, bool]:
    for key in ("release_base_ref", "phase_loop_release_base_ref"):
        value = metadata.get(key)
        if value:
            return value, True
    return "origin/main", False


def _dirty_release_affecting_paths(repo: Path) -> list[str]:
    try:
        status = subprocess.check_output(
            ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
            text=True,
        )
    except Exception:
        return []
    paths: list[str] = []
    for line in status.splitlines():
        if not line:
            continue
        path = _status_path(line)
        if path and _is_release_affecting_path(path):
            paths.append(path)
    return sorted(dict.fromkeys(paths))


def _status_path(line: str) -> str:
    path = line[3:] if len(line) > 3 else ""
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    return path.strip().strip('"')


def _is_release_affecting_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return any(fnmatch.fnmatchcase(normalized, pattern) for pattern in RELEASE_AFFECTING_PATTERNS)


def _branch_sync(repo: Path, base_ref: str) -> dict[str, Any]:
    head = _git(repo, "rev-parse", "HEAD")
    base_commit = _git(repo, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
    remote = base_ref.split("/", 1)[0] if "/" in base_ref else ""
    return {
        "base_ref": base_ref,
        "base_available": bool(base_commit),
        "base_remote_available": bool(remote and _git(repo, "remote", "get-url", remote)),
        "head": _short_sha(head),
        "base_commit": _short_sha(base_commit),
    }


def _git(repo: Path, *args: str) -> str:
    try:
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def _short_sha(value: str) -> str:
    return value[:12] if value else ""
