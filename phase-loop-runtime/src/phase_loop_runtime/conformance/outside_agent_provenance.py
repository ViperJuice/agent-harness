"""Provenance and digest checks for outside-agent submissions."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping

from .outside_agent_core import OutsideAgentBlocker


@dataclass(frozen=True)
class OutsideAgentProvenanceRef:
    ref: str
    digest: str
    kind: str = "metadata"


@dataclass(frozen=True)
class OutsideAgentProvenanceValidation:
    provenance_refs: tuple[str, ...]
    evidence_refs: tuple[OutsideAgentProvenanceRef, ...]
    blockers: tuple[OutsideAgentBlocker, ...]


def validate_outside_agent_provenance(
    submission: Mapping[str, Any],
) -> OutsideAgentProvenanceValidation:
    blockers: list[OutsideAgentBlocker] = []
    provenance_refs: list[str] = []
    evidence_refs: list[OutsideAgentProvenanceRef] = []

    for index, record in enumerate(submission.get("provenance_refs") or []):
        normalized, ref_blockers = _validate_ref_record(record, f"provenance_refs.{index}")
        blockers.extend(ref_blockers)
        if normalized is not None:
            provenance_refs.append(normalized.ref)
            evidence_refs.append(normalized)

    for index, record in enumerate(submission.get("evidence_refs") or []):
        normalized, ref_blockers = _validate_ref_record(record, f"evidence_refs.{index}")
        blockers.extend(ref_blockers)
        if normalized is not None:
            evidence_refs.append(normalized)

    return OutsideAgentProvenanceValidation(
        provenance_refs=tuple(provenance_refs),
        evidence_refs=tuple(evidence_refs),
        blockers=tuple(blockers),
    )


def normalize_outside_agent_ref(ref: str) -> str:
    if not isinstance(ref, str) or not ref.strip():
        raise ValueError("unsafe_source_ref")
    normalized = ref.replace("\\", "/").strip()
    if normalized.startswith("/") or "://" in normalized:
        raise ValueError("absolute_path_ref")

    path = PurePosixPath(normalized)
    if any(part == ".." for part in path.parts):
        raise ValueError("path_traversal_ref")
    if any(part in ("", ".") for part in path.parts):
        raise ValueError("unsafe_source_ref")
    return path.as_posix()


def _validate_ref_record(
    record: Any,
    ref_prefix: str,
) -> tuple[OutsideAgentProvenanceRef | None, tuple[OutsideAgentBlocker, ...]]:
    blockers: list[OutsideAgentBlocker] = []
    if not isinstance(record, Mapping):
        return None, (
            OutsideAgentBlocker(
                "unsafe_source_ref",
                "outside-agent provenance record must be metadata",
                ref=ref_prefix,
            ),
        )

    raw_ref = record.get("ref", record.get("path"))
    try:
        normalized_ref = normalize_outside_agent_ref(raw_ref)
    except ValueError as exc:
        blockers.append(
            OutsideAgentBlocker(
                str(exc),
                "outside-agent ref is not a safe repo-relative path",
                ref=ref_prefix,
            )
        )
        normalized_ref = None

    digest = record.get("digest")
    if not _is_digest(digest):
        blockers.append(
            OutsideAgentBlocker(
                "missing_digest",
                "outside-agent ref must include a sha256 digest",
                ref=f"{ref_prefix}.digest",
            )
        )
    elif "content" in record and _digest_bytes(str(record["content"]).encode("utf-8")) != _strip_digest_prefix(digest):
        blockers.append(
            OutsideAgentBlocker(
                "digest_mismatch",
                "outside-agent ref digest does not match supplied content metadata",
                ref=f"{ref_prefix}.digest",
            )
        )

    if normalized_ref is None or not _is_digest(digest):
        return None, tuple(blockers)
    return (
        OutsideAgentProvenanceRef(
            ref=normalized_ref,
            digest=_strip_digest_prefix(digest),
            kind=str(record.get("kind", "metadata")),
        ),
        tuple(blockers),
    )


def _is_digest(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return len(_strip_digest_prefix(value)) == 64 and all(
        char in "0123456789abcdef" for char in _strip_digest_prefix(value).lower()
    )


def _strip_digest_prefix(value: str) -> str:
    return value.removeprefix("sha256:").lower()


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


__all__ = [
    "OutsideAgentProvenanceRef",
    "OutsideAgentProvenanceValidation",
    "normalize_outside_agent_ref",
    "validate_outside_agent_provenance",
]
