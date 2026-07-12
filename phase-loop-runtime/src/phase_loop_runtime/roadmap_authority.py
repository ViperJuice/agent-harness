from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path


SCHEMA = "phase_loop_roadmap_authority.v1"
REQUIRED_MARKER = "phase_loop_roadmap_authority_required.v1\n"
LATCH_MARKER = "phase_loop_roadmap_authority_latched.v1\n"


class RoadmapAuthorityError(RuntimeError):
    pass


def roadmap_authority_file(repo: Path) -> Path:
    return _git_common_path(repo, "phase-loop-roadmap-authority.json")


def roadmap_authority_required_file(repo: Path) -> Path:
    return _git_common_path(repo, "phase-loop-roadmap-authority-required")


def roadmap_authority_latch_file(repo: Path) -> Path:
    return _git_common_path(repo, "phase-loop-roadmap-authority-latch")


def roadmap_authority_worktree_latch_file(repo: Path) -> Path:
    return repo.resolve() / ".phase-loop" / "roadmap-authority-latch"


def _git_common_path(repo: Path, name: str) -> Path:
    git_entry = repo.resolve() / ".git"
    if git_entry.is_dir():
        common = git_entry
    elif git_entry.is_file():
        try:
            line = git_entry.read_text(encoding="utf-8").strip()
            if not line.startswith("gitdir: "):
                raise ValueError
            git_dir = Path(line.removeprefix("gitdir: "))
            git_dir = git_dir if git_dir.is_absolute() else (repo / git_dir).resolve()
            commondir = git_dir / "commondir"
            if commondir.exists():
                common_value = commondir.read_text(encoding="utf-8").strip()
                common_path = Path(common_value)
                common = common_path if common_path.is_absolute() else (git_dir / common_path).resolve()
            else:
                common = git_dir
        except (OSError, UnicodeDecodeError, ValueError) as error:
            raise RoadmapAuthorityError("roadmap authority marker path unavailable") from error
    else:
        common = git_entry
    return common / name


def active_authorized_roadmap(repo: Path) -> Path | None:
    return assert_roadmap_authorized(repo, None)


def assert_roadmap_authorized(
    repo: Path,
    roadmap: str | Path | None,
) -> Path | None:
    root = repo.resolve()
    authority_path = roadmap_authority_file(root)
    if not authority_path.exists():
        _require_authority_not_removed(root)
        return _resolve_roadmap(root, roadmap) if roadmap is not None and str(roadmap).strip() else None
    _require_authority_installed(root, authority_path)
    authority = _read_authority(authority_path)
    if authority["status"] != "active":
        raise RoadmapAuthorityError("roadmap authority is blocked")
    active = _bound_path(root, authority["active_roadmap"])
    retired = {_bound_path(root, item["path"]): item for item in authority["retired_roadmaps"]}
    for path, item in retired.items():
        blob = _bound_common_blob(authority_path.parent, item["common_blob"])
        _require_control_mode(blob, 0o400)
        if not blob.is_file() or _sha256(blob) != item["sha256"]:
            raise RoadmapAuthorityError(f"retired roadmap digest mismatch: {path}")
    target = active if roadmap is None or str(roadmap).strip() == "" else _resolve_roadmap(root, roadmap)
    if target in retired:
        raise RoadmapAuthorityError(f"roadmap retired: {target}")
    if target != active:
        raise RoadmapAuthorityError(f"roadmap is not active: {target}")
    if active.is_file():
        if _sha256(active) != authority["active_roadmap_sha256"]:
            raise RoadmapAuthorityError(f"roadmap authority digest mismatch: {active}")
    else:
        raise RoadmapAuthorityError(f"roadmap authority digest mismatch: {active}")
    return target


def _read_authority(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RoadmapAuthorityError(f"invalid roadmap authority: {path}") from error
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "status",
        "active_roadmap",
        "active_roadmap_sha256",
        "retired_roadmaps",
    }:
        raise RoadmapAuthorityError(f"invalid roadmap authority schema: {path}")
    if value["schema"] != SCHEMA or value["status"] not in {"blocked", "active"} or not _digest(value["active_roadmap_sha256"]):
        raise RoadmapAuthorityError(f"invalid roadmap authority binding: {path}")
    retired = value["retired_roadmaps"]
    if not isinstance(retired, list):
        raise RoadmapAuthorityError(f"invalid retired roadmap set: {path}")
    seen: set[str] = set()
    for item in retired:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "common_blob"}:
            raise RoadmapAuthorityError(f"invalid retired roadmap binding: {path}")
        if not isinstance(item["path"], str) or item["path"] in seen or not _digest(item["sha256"]) or not isinstance(item["common_blob"], str) or not item["common_blob"]:
            raise RoadmapAuthorityError(f"invalid retired roadmap binding: {path}")
        seen.add(item["path"])
    return value


def _require_authority_not_removed(repo: Path) -> None:
    marker = roadmap_authority_required_file(repo)
    latch = roadmap_authority_latch_file(repo)
    worktree_latch = roadmap_authority_worktree_latch_file(repo)
    if not marker.exists() and not latch.exists() and not worktree_latch.exists():
        return
    if worktree_latch.exists():
        _read_control_file(worktree_latch, LATCH_MARKER, 0o400)
    if latch.exists():
        _read_control_file(latch, LATCH_MARKER, 0o400)
    if marker.exists():
        _read_control_file(marker, REQUIRED_MARKER, 0o400)
    raise RoadmapAuthorityError("required roadmap authority is missing")


def _require_authority_installed(repo: Path, authority: Path) -> None:
    _require_control_mode(authority, 0o600)
    _read_control_file(roadmap_authority_worktree_latch_file(repo), LATCH_MARKER, 0o400)
    _read_control_file(roadmap_authority_required_file(repo), REQUIRED_MARKER, 0o400)
    _read_control_file(roadmap_authority_latch_file(repo), LATCH_MARKER, 0o400)


def _read_control_file(path: Path, expected: str, mode: int) -> None:
    try:
        value = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise RoadmapAuthorityError("invalid roadmap authority required marker") from error
    if value != expected:
        raise RoadmapAuthorityError("invalid roadmap authority required marker")
    _require_control_mode(path, mode)


def _require_control_mode(path: Path, mode: int) -> None:
    try:
        metadata = path.stat()
        common_owner = path.parent.stat().st_uid
    except OSError as error:
        raise RoadmapAuthorityError("invalid roadmap authority control file") from error
    if os.name == "nt":
        return
    if metadata.st_uid != common_owner or stat.S_IMODE(metadata.st_mode) != mode or metadata.st_uid != os.geteuid():
        raise RoadmapAuthorityError("invalid roadmap authority control file")


def _resolve_roadmap(repo: Path, roadmap: str | Path | None) -> Path:
    if roadmap is None or str(roadmap).strip() == "":
        raise RoadmapAuthorityError("roadmap authority requires a roadmap path")
    path = Path(roadmap).expanduser()
    return (path if path.is_absolute() else repo / path).resolve()


def _bound_common_blob(common: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        raise RoadmapAuthorityError("retired roadmap blob escapes git common directory")
    resolved = (common / path).resolve()
    try:
        resolved.relative_to(common.resolve())
    except ValueError as error:
        raise RoadmapAuthorityError("retired roadmap blob escapes git common directory") from error
    return resolved


def _bound_path(repo: Path, value: object) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise RoadmapAuthorityError("roadmap authority paths must be repository-relative")
    path = (repo / value).resolve()
    try:
        path.relative_to(repo)
    except ValueError as error:
        raise RoadmapAuthorityError("roadmap authority path escapes repository") from error
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)
