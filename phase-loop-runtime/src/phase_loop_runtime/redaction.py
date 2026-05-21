from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Any, Mapping

from .models import CHANGED_PATH_CATEGORIES, SourceTruthImpact


_SOURCE_TRUTH_REASON_BY_CATEGORY = {
    "docs": "docs_source_truth_touched",
    "specs": "specs_source_truth_touched",
    "active_canonical_spec": "active_specs_touched",
    "managed_root_mirror_spec": "managed_mirror_specs_touched",
    "mirror_manifest": "mirror_manifests_touched",
    "archive_manifest": "archive_manifests_touched",
    "archived_spec": "archived_specs_touched",
    "unmanaged_spec": "unmanaged_specs_touched",
    "pipeline_sources": "pipeline_sources_touched",
    "portal_contract_refs": "portal_contract_refs_touched",
    "greenfield_authority_refs": "greenfield_authority_refs_touched",
}

_CATEGORY_BY_PROTECTED_SOURCE_ROLE = {
    "active_canonical_spec": "active_canonical_spec",
    "managed_mirror_file": "managed_root_mirror_spec",
    "mirror_manifest": "mirror_manifest",
    "archive_manifest": "archive_manifest",
    "archived_spec": "archived_spec",
    "unmanaged_spec_input": "unmanaged_spec",
    "root_specs_intake": "unmanaged_spec",
}

_FORBIDDEN_METADATA_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("raw_diff", re.compile(r"diff --git|@@\s+-\d+,\d+\s+\+\d+,\d+\s+@@")),
    ("raw_spec_body", re.compile(r"raw spec bod(?:y|ies)|spec body bytes|verbatim spec", re.I)),
    ("raw_transcript", re.compile(r"raw transcript|transcript bytes|verbatim transcript", re.I)),
    ("secret_like_value", re.compile(r"(?:api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{12,}", re.I)),
    ("absolute_private_path", re.compile(r"/(?:home|users|mnt/(?:private|evidence|secure|raw|HC_Volume_[^/\s]+))/(?:[^\"'\s]+)", re.I)),
    ("provider_payload", re.compile(r"raw provider payload|provider payload|anthropic[_-]?payload|openai[_-]?payload", re.I)),
    ("credential_payload", re.compile(r"credential payload|private key|-----begin [a-z ]*private key-----", re.I)),
    ("local_env_value", re.compile(r"local env value|\.env(?:\.local)? value|process\.env\[[^\]]+\]\s*=", re.I)),
    ("private_evidence", re.compile(r"private evidence|evidence bytes|raw evidence", re.I)),
)


def classify_changed_path(path: str, protected_source_roles: Mapping[str, str] | None = None) -> str:
    normalized = _normalize_path(path)
    parts = PurePosixPath(normalized).parts
    lower = normalized.lower()
    role_category = _category_from_protected_source_role(normalized, protected_source_roles)
    if role_category is not None:
        return role_category

    if normalized.startswith("tests/") or "/tests/" in normalized or "/fixtures/" in normalized:
        return "tests"
    if lower == "readme.md" or normalized.startswith("docs/") or normalized.endswith(".md") and "/docs/" in normalized:
        return "docs"
    if _looks_like_mirror_manifest(normalized, lower):
        return "mirror_manifest"
    if _looks_like_archive_manifest(normalized, lower):
        return "archive_manifest"
    if _looks_like_active_canonical_spec(normalized, lower):
        return "active_canonical_spec"
    if _looks_like_archived_spec(normalized, lower):
        return "archived_spec"
    if normalized.startswith("specs/") or normalized.startswith("spec/"):
        return "unmanaged_spec"
    if (
        normalized.startswith(".pipeline/")
        or "pipeline.definition.json" in lower
        or normalized.startswith("packages/pipeline-schema/")
        or normalized.startswith("pipeline-sources/")
    ):
        return "pipeline_sources"
    if (
        "portal-contract" in lower
        or normalized.startswith("portal/contracts/")
        or normalized.startswith("contracts/portal/")
        or normalized.startswith("consiliency-portal/contracts/")
    ):
        return "portal_contract_refs"
    if (
        "greenfield-authority" in lower
        or normalized.startswith("greenfield/authority/")
        or normalized.startswith("greenfield/contracts/")
        or normalized.startswith("authority/greenfield/")
    ):
        return "greenfield_authority_refs"
    if _looks_like_code_path(normalized, parts):
        return "code"
    return "unknown"


def build_source_truth_impact(
    changed_paths: tuple[str, ...] | list[str] | Any,
    protected_source_roles: Mapping[str, str] | None = None,
) -> SourceTruthImpact:
    paths = _stable_paths(changed_paths)
    boundaries = tuple(
        {"path": path, "category": classify_changed_path(path, protected_source_roles)}
        for path in paths
    )
    reasons: list[str] = []
    for boundary in boundaries:
        category = boundary["category"]
        reason = _SOURCE_TRUTH_REASON_BY_CATEGORY.get(category)
        if reason is not None:
            reasons.append(reason)
        if "adoption" in boundary["path"].lower() and "contract" in boundary["path"].lower():
            reasons.append("adoption_contracts_touched")
        if "contract" in boundary["path"].lower() and category in CHANGED_PATH_CATEGORIES:
            reasons.append("contract_refs_touched")
    return SourceTruthImpact(
        changed_path_boundaries=boundaries,
        canonical_refresh_recommended=bool(reasons),
        canonical_refresh_reason_codes=tuple(sorted(dict.fromkeys(reasons))),
        redaction_posture="metadata_only",
    )


def metadata_redaction_diagnostic(payload: Mapping[str, Any] | None) -> dict[str, str] | None:
    if payload is None:
        return None
    serialized = json.dumps(payload, sort_keys=True)
    for kind, pattern in _FORBIDDEN_METADATA_PATTERNS:
        if pattern.search(serialized):
            return {"kind": "malformed_closeout", "message": f"closeout contains forbidden metadata token: {kind}"}
    return None


def _normalize_path(path: str) -> str:
    normalized = str(path).replace("\\", "/").strip()
    return normalized[2:] if normalized.startswith("./") else normalized


def _stable_paths(paths: tuple[str, ...] | list[str] | Any) -> list[str]:
    if not isinstance(paths, (tuple, list)):
        return []
    return sorted(dict.fromkeys(_normalize_path(str(path)) for path in paths if str(path).strip()))


def _looks_like_code_path(path: str, parts: tuple[str, ...]) -> bool:
    if path.startswith(("codex-config/", "shared/skills/", "scripts/")):
        return True
    if parts and parts[0] in {"bin", "lib", "src"}:
        return True
    return PurePosixPath(path).suffix in {".py", ".sh", ".bash", ".zsh", ".toml", ".yaml", ".yml", ".json"}


def _category_from_protected_source_role(
    path: str,
    protected_source_roles: Mapping[str, str] | None,
) -> str | None:
    if not protected_source_roles:
        return None
    role = protected_source_roles.get(path) or protected_source_roles.get(path.lower())
    if role is None:
        return None
    return _CATEGORY_BY_PROTECTED_SOURCE_ROLE.get(role)


def _looks_like_mirror_manifest(path: str, lower: str) -> bool:
    name = PurePosixPath(path).name.lower()
    return name == "mirror-manifest.json" or "mirror_manifest" in lower or "/mirror-manifest" in lower


def _looks_like_archive_manifest(path: str, lower: str) -> bool:
    name = PurePosixPath(path).name.lower()
    return name == "archive-manifest.json" or "archive_manifest" in lower or "/archive-manifest" in lower


def _looks_like_active_canonical_spec(path: str, lower: str) -> bool:
    return (
        path.startswith(".pipeline/specs/active/")
        or path.startswith(".pipeline/specs/canonical/")
        or "/active-canonical/" in lower
        or "/canonical-specs/" in lower
    )


def _looks_like_archived_spec(path: str, lower: str) -> bool:
    return path.startswith(".pipeline/specs/archive/") or "/archived-specs/" in lower
