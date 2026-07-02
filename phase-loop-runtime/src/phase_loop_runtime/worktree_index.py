"""Git-derived worktree freshness index (CS-0.10a).

Answers "where is the freshest working copy of a path, and who's touching it" by
reading `git worktree list` and diffing each worktree's branch against the base
ref (default: `origin/<default-branch>`, falling back to `origin/main`). Purely
git-derived and READ-ONLY: it never writes repo state, never mutates the working
tree, and holds no persistent index of its own — every call re-derives the answer
from `git` at invocation time, so it can't drift from reality the way a cached
index could.

If a repo has no worktrees beyond the primary one, or none of them touch the
queried path, the answer collapses to the `origin/main` baseline entry alone —
there is nothing else to report.
"""
from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WorktreeRef:
    """One entry from `git worktree list --porcelain`."""

    path: str
    branch: str | None
    head_sha: str | None
    bare: bool = False
    detached: bool = False

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Holder:
    """A working copy (a worktree, or the `origin/main` baseline) that carries a
    commit touching the queried path."""

    worktree: str
    branch: str
    last_commit_sha: str
    last_commit_time: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PathFreshness:
    path: str
    holders: tuple[Holder, ...]
    main_behind: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "holders": [holder.to_json() for holder in self.holders],
            "main_behind": self.main_behind,
        }


@dataclass(frozen=True)
class WorktreeIndexReport:
    base_ref: str
    worktrees: tuple[WorktreeRef, ...]
    paths: tuple[PathFreshness, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "base_ref": self.base_ref,
            "worktrees": [wt.to_json() for wt in self.worktrees],
            "paths": [pf.to_json() for pf in self.paths],
        }


def _git(repo: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _is_ancestor(repo: Path, sha: str, ref: str) -> bool:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", sha, ref],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except Exception:
        return False
    return completed.returncode == 0


def default_base_ref(repo: Path) -> str:
    """`origin/<default-branch>` from the remote's HEAD symref, or `origin/main`."""
    default_remote = _git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    return default_remote or "origin/main"


def list_worktrees(repo: Path) -> tuple[WorktreeRef, ...]:
    """Parse `git worktree list --porcelain` into `WorktreeRef` entries."""
    output = _git(repo, "worktree", "list", "--porcelain")
    if not output:
        return ()

    entries: list[WorktreeRef] = []
    path: str | None = None
    head: str | None = None
    branch: str | None = None
    bare = False
    detached = False

    def flush() -> None:
        nonlocal path, head, branch, bare, detached
        if path is not None:
            entries.append(WorktreeRef(path=path, branch=branch, head_sha=head, bare=bare, detached=detached))
        path, head, branch, bare, detached = None, None, None, False, False

    for line in output.splitlines():
        if not line.strip():
            flush()
        elif line.startswith("worktree "):
            flush()
            path = line[len("worktree "):].strip()
        elif line.startswith("HEAD "):
            head = line[len("HEAD "):].strip()
        elif line.startswith("branch "):
            branch = line[len("branch "):].strip().removeprefix("refs/heads/")
        elif line == "bare":
            bare = True
        elif line == "detached":
            detached = True
    flush()
    return tuple(entries)


def branch_touched_paths(repo: Path, branch: str, base_ref: str) -> tuple[str, ...]:
    """Repo-relative paths `branch` touches relative to `base_ref` (`git diff --name-only`)."""
    output = _git(repo, "diff", f"{base_ref}...{branch}", "--name-only")
    if not output:
        return ()
    return tuple(line.strip() for line in output.splitlines() if line.strip())


def _last_commit(repo: Path, rev: str, path: str) -> tuple[str, str] | None:
    output = _git(repo, "log", "-1", "--format=%H\x1f%cI", rev, "--", path)
    if not output:
        return None
    parts = output.split("\x1f")
    if len(parts) != 2:
        return None
    sha, when = parts
    return sha, when


def _path_freshness(
    repo: Path,
    path: str,
    base_ref: str,
    touched_by_worktree: dict[WorktreeRef, tuple[str, ...]],
) -> PathFreshness:
    holders: list[Holder] = []

    baseline = _last_commit(repo, base_ref, path)
    if baseline is not None:
        sha, when = baseline
        holders.append(Holder(worktree="origin/main", branch=base_ref, last_commit_sha=sha, last_commit_time=when))

    main_behind = False
    for worktree, touched in touched_by_worktree.items():
        if path not in touched:
            continue
        commit = _last_commit(repo, worktree.branch or worktree.head_sha or "HEAD", path)
        if commit is None:
            continue
        sha, when = commit
        holders.append(
            Holder(worktree=worktree.path, branch=worktree.branch or "detached", last_commit_sha=sha, last_commit_time=when)
        )
        if not _is_ancestor(repo, sha, base_ref):
            main_behind = True

    return PathFreshness(path=path, holders=tuple(holders), main_behind=main_behind)


def build_index(repo: Path, *, base_ref: str | None = None, path: str | None = None) -> WorktreeIndexReport:
    """Build the freshness index. `path=None` reports every path any active
    worktree touches relative to `base_ref`; a repo with no diverging worktrees
    yields an empty `paths` tuple — the answer is `origin/main`."""
    resolved_base = base_ref or default_base_ref(repo)
    worktrees = list_worktrees(repo)

    touched_by_worktree: dict[WorktreeRef, tuple[str, ...]] = {}
    for worktree in worktrees:
        if worktree.bare or not worktree.branch:
            continue
        touched_by_worktree[worktree] = branch_touched_paths(repo, worktree.branch, resolved_base)

    if path is not None:
        candidate_paths: tuple[str, ...] = (path,)
    else:
        seen: dict[str, None] = {}
        for touched in touched_by_worktree.values():
            for touched_path in touched:
                seen.setdefault(touched_path, None)
        candidate_paths = tuple(sorted(seen))

    paths = tuple(_path_freshness(repo, p, resolved_base, touched_by_worktree) for p in candidate_paths)
    return WorktreeIndexReport(base_ref=resolved_base, worktrees=worktrees, paths=paths)


def render_human(report: WorktreeIndexReport) -> str:
    lines = [f"base_ref: {report.base_ref}", f"worktrees ({len(report.worktrees)}):"]
    for worktree in report.worktrees:
        tag = " [detached]" if worktree.detached else ""
        lines.append(f"  {worktree.path}  branch={worktree.branch or '-'}{tag}  head={worktree.head_sha or '-'}")

    if not report.paths:
        lines.append("")
        lines.append("no divergent paths relative to base_ref — origin/main is the answer")
        return "\n".join(lines)

    lines.append("")
    for path_freshness in report.paths:
        behind = "  [main behind]" if path_freshness.main_behind else ""
        lines.append(f"{path_freshness.path}{behind}")
        if not path_freshness.holders:
            lines.append("    (no holders)")
            continue
        for holder in path_freshness.holders:
            lines.append(
                f"    {holder.branch:<30} {holder.worktree}  {holder.last_commit_sha[:12]}  {holder.last_commit_time}"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(prog="phase-loop worktree-index")
    parser.add_argument("--repo", default=".", help="Repository root (default: cwd).")
    parser.add_argument("--path", help="Report freshness holders for a single repo-relative path.")
    parser.add_argument("--base", help="Diff base ref (default: origin/<default-branch>, falling back to origin/main).")
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON.")
    # tolerate the leading 'worktree-index' token when dispatched from the main CLI
    args = parser.parse_args([a for a in (argv or []) if a != "worktree-index"])

    report = build_index(Path(args.repo), base_ref=args.base, path=args.path)
    if args.json:
        print(json.dumps(report.to_json(), indent=2))
    else:
        print(render_human(report))
    return 0
