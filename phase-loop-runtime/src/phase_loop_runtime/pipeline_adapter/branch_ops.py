from __future__ import annotations

import subprocess
from pathlib import Path

from .flag import branchgov_enabled
from .markers import detect_pipeline_mode


# Stable prefix of the BranchGov default-branch refusal message. Shared so
# reconcile._blocker_precondition_cleared can identify this blocker variant
# without copying the literal string (the suffix interpolates the branch name).
REFUSE_DEFAULT_BRANCH_COMMIT_PREFIX = "Refusing git commit on default branch"


class PipelineBranchInvariantError(RuntimeError):
    def __init__(self, message: str, *, blocker_class: str = "contract_bug") -> None:
        super().__init__(message)
        self.blocker_class = blocker_class
        self.blocker_summary = message


class PipelineDefaultBranchRefusalError(PipelineBranchInvariantError):
    def __init__(self, message: str) -> None:
        super().__init__(message, blocker_class="branch_sync_conflict")


def ensure_pipeline_branch(repo_root: Path, roadmap_version: str, default_branch: str) -> str:
    repo = Path(repo_root)
    pipeline_branch = f"consiliency/pipeline/{roadmap_version}"
    if not _branchgov_active(repo):
        return _current_branch(repo)

    current = _current_branch(repo)
    if current == default_branch and _dirty_status(repo):
        raise PipelineDefaultBranchRefusalError(
            f"Refusing pipeline branch operation from dirty default branch {default_branch}."
        )

    base_ref = f"origin/{default_branch}"
    _git(repo, "fetch", "origin", default_branch)
    if not _ref_exists(repo, base_ref):
        raise PipelineBranchInvariantError(
            f"Pipeline branch base ref {base_ref} is unavailable.",
            blocker_class="branch_sync_conflict",
        )

    if _local_branch_exists(repo, pipeline_branch):
        checkout = _git(repo, "checkout", pipeline_branch)
        if checkout.returncode != 0:
            raise PipelineBranchInvariantError(
                f"Unable to check out pipeline branch {pipeline_branch}: {_stderr_excerpt(checkout)}",
                blocker_class="branch_sync_conflict",
            )
        rebase = _git(repo, "rebase", base_ref)
        if rebase.returncode != 0:
            raise PipelineBranchInvariantError(
                f"Pipeline branch {pipeline_branch} could not rebase onto {base_ref}.",
                blocker_class="merge_conflict",
            )
    else:
        created = _git(repo, "checkout", "-b", pipeline_branch, base_ref)
        if created.returncode != 0:
            raise PipelineBranchInvariantError(
                f"Unable to create pipeline branch {pipeline_branch} from {base_ref}: {_stderr_excerpt(created)}",
                blocker_class="branch_sync_conflict",
            )
    return pipeline_branch


def refuse_default_branch_commit(repo_root: Path, default_branch: str) -> None:
    repo = Path(repo_root)
    if not _branchgov_active(repo):
        return
    if _current_branch(repo) == default_branch:
        raise PipelineDefaultBranchRefusalError(
            f"{REFUSE_DEFAULT_BRANCH_COMMIT_PREFIX} {default_branch} while pipeline branch governance is enabled."
        )


def _branchgov_active(repo: Path) -> bool:
    return detect_pipeline_mode(repo) and branchgov_enabled()


def _current_branch(repo: Path) -> str:
    result = _git(repo, "branch", "--show-current")
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    head = _git(repo, "rev-parse", "--short", "HEAD")
    return f"detached@{head.stdout.strip()}" if head.returncode == 0 and head.stdout.strip() else "unknown"


def _dirty_status(repo: Path) -> str:
    result = _git(repo, "status", "--porcelain", "--untracked-files=all")
    return result.stdout.strip() if result.returncode == 0 else ""


def _ref_exists(repo: Path, ref: str) -> bool:
    return _git(repo, "rev-parse", "--verify", "--quiet", ref).returncode == 0


def _local_branch_exists(repo: Path, branch: str) -> bool:
    return _ref_exists(repo, f"refs/heads/{branch}")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _stderr_excerpt(result: subprocess.CompletedProcess[str]) -> str:
    text = (result.stderr or result.stdout or "").strip()
    return " ".join(text.split())[:300] or "git command failed"
