from __future__ import annotations

from dataclasses import asdict, dataclass
from fnmatch import fnmatchcase
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .discovery import PlanOwnership
    from .models import PhaseSourceBundle


def snapshot_git_dirty_paths(repo: Path) -> tuple[str, ...]:
    try:
        output = subprocess.check_output(
            ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return ()
    paths: list[str] = []
    for line in output.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path and path not in paths:
            paths.append(path)
    return tuple(paths)


@dataclass(frozen=True)
class PipelineWriteBoundaryDiagnostic:
    kind: str
    message: str
    path: str
    blocker_class: str = "contract_bug"
    human_required: bool = False

    def to_json(self) -> dict[str, object]:
        return {key: value for key, value in asdict(self).items() if value not in (None, "")}


def pipeline_authorized_write_patterns(bundle: "PhaseSourceBundle") -> tuple[str, ...]:
    policy = bundle.delegated_write_policy if isinstance(bundle.delegated_write_policy, dict) else {}
    patterns: list[str] = []
    for value in policy.get("owned_files") or ():
        if isinstance(value, str) and value.strip():
            patterns.append(value.strip())
    if bundle.artifact_target_root:
        root = bundle.artifact_target_root.strip().rstrip("/")
        if root:
            patterns.append(root)
            patterns.append(f"{root}/**")
    return tuple(dict.fromkeys(patterns))


def pipeline_write_boundary_diagnostic(
    repo: Path,
    dirty_paths: tuple[str, ...] | list[str],
    *,
    plan_ownership: "PlanOwnership",
    bundle: "PhaseSourceBundle | None",
) -> PipelineWriteBoundaryDiagnostic | None:
    if bundle is None:
        return None
    protected_paths = {source.path for source in bundle.protected_sources}
    authorized = pipeline_authorized_write_patterns(bundle)
    for path in dirty_paths:
        rel_path = _repo_relative_path(repo, path)
        if rel_path in protected_paths:
            if not (plan_ownership.matches(rel_path) and _matches_any(rel_path, authorized)):
                return PipelineWriteBoundaryDiagnostic(
                    kind="protected_pipeline_source_write",
                    message="Protected Pipeline source write is not authorized by both the phase plan and source bundle",
                    path=rel_path,
                )
            continue
        if _is_pipeline_owned(rel_path, bundle):
            if not (plan_ownership.matches(rel_path) and _matches_any(rel_path, authorized)):
                return PipelineWriteBoundaryDiagnostic(
                    kind="unauthorized_pipeline_write",
                    message="Pipeline-owned write is outside the combined phase-plan and source-bundle boundary",
                    path=rel_path,
                )
    return None


def _repo_relative_path(repo: Path, path: str) -> str:
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            return candidate.resolve().relative_to(repo.resolve()).as_posix()
        except ValueError:
            return candidate.as_posix()
    return candidate.as_posix()


def _is_pipeline_owned(path: str, bundle: "PhaseSourceBundle") -> bool:
    if path.startswith(".pipeline/") or path == ".pipeline":
        return True
    if path == "pipeline.definition.json":
        return True
    if path in {source.path for source in bundle.protected_sources}:
        return True
    portal_contracts = {
        source.path for source in bundle.protected_sources if source.category == "portal_contracts"
    }
    return path in portal_contracts


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(path == pattern or fnmatchcase(path, pattern) or _globstar_match(path, pattern) for pattern in patterns)


def _globstar_match(path: str, pattern: str) -> bool:
    if not pattern.endswith("/**"):
        return False
    return path.startswith(pattern[:-3].rstrip("/") + "/")
