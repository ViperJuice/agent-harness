from __future__ import annotations

from pathlib import PurePosixPath


LOCKFILE_NAMES = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb"}
ENV_SOURCE_NAMES = {"env.ts", "env.js", "env.mjs", "env.cjs", "environment.ts", "environment.js"}


def validate_phase_owned_evidence(
    declared_paths: tuple[str, ...] | list[str],
    actual_paths: tuple[str, ...] | list[str],
    evidence: object,
) -> tuple[dict[str, str], ...]:
    declared = tuple(filter(None, (_clean_path(path) for path in declared_paths)))
    actual = set(filter(None, (_clean_path(path) for path in actual_paths)))
    accepted: list[dict[str, str]] = []
    seen: set[str] = set()

    for item in _evidence_items(evidence):
        evidence_path = _clean_path(str(item.get("path") or item.get("actual_path") or item.get("sibling") or ""))
        if not evidence_path or evidence_path not in actual or evidence_path in seen:
            continue
        match = _matching_declared_path(evidence_path, declared)
        if match is None:
            continue
        accepted.append(
            {
                "path": evidence_path,
                "declared_path": match[0],
                "kind": match[1],
            }
        )
        seen.add(evidence_path)

    return tuple(accepted)


def _evidence_items(evidence: object) -> tuple[dict[str, object], ...]:
    if not isinstance(evidence, list):
        return ()
    items: list[dict[str, object]] = []
    for item in evidence:
        if isinstance(item, str):
            items.append({"path": item})
        elif isinstance(item, dict):
            items.append(item)
    return tuple(items)


def _clean_path(path: str) -> str:
    path = path.replace("\\", "/").strip().strip("/")
    if not path or path.startswith("/") or path.startswith("../") or "/../" in path or path == "..":
        return ""
    return path


def _matching_declared_path(path: str, declared_paths: tuple[str, ...]) -> tuple[str, str] | None:
    for declared in declared_paths:
        if _is_test_for(path, declared):
            return declared, "test"
        if _is_snapshot_for(path, declared):
            return declared, "snapshot"
        if _is_migration_peer(path, declared):
            return declared, "migration_timestamp"
        if _is_lockfile_for_manifest(path, declared):
            return declared, "package_lock"
        if _is_env_example_for(path, declared):
            return declared, "env_example"
    return None


def _stem_without_test_suffix(name: str) -> str:
    for suffix in (".test", ".spec"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _is_test_for(path: str, declared: str) -> bool:
    candidate = PurePosixPath(path)
    source = PurePosixPath(declared)
    if "__tests__" not in candidate.parts and not candidate.name.startswith("test_"):
        return False
    candidate_stem = _stem_without_test_suffix(candidate.stem)
    source_stem = source.stem
    if candidate.name.startswith("test_"):
        candidate_stem = candidate_stem.removeprefix("test_")
    return candidate_stem == source_stem


def _is_snapshot_for(path: str, declared: str) -> bool:
    candidate = PurePosixPath(path)
    source = PurePosixPath(declared)
    return "__snapshots__" in candidate.parts and candidate.stem.startswith(source.stem)


def _migration_timestamp(path: str) -> str | None:
    candidate = PurePosixPath(path)
    if "migrations" not in candidate.parts:
        return None
    prefix = candidate.name.split("_", 1)[0]
    return prefix if prefix.isdigit() and len(prefix) >= 8 else None


def _is_migration_peer(path: str, declared: str) -> bool:
    path_ts = _migration_timestamp(path)
    declared_ts = _migration_timestamp(declared)
    return bool(path_ts and declared_ts and path_ts == declared_ts and path != declared)


def _is_lockfile_for_manifest(path: str, declared: str) -> bool:
    candidate = PurePosixPath(path)
    source = PurePosixPath(declared)
    return candidate.parent == source.parent and candidate.name in LOCKFILE_NAMES and source.name == "package.json"


def _is_env_example_for(path: str, declared: str) -> bool:
    candidate = PurePosixPath(path)
    source = PurePosixPath(declared)
    if candidate.name != ".env.example":
        return False
    return candidate.parent == source.parent and (source.name in ENV_SOURCE_NAMES or "env" in source.stem.lower())
