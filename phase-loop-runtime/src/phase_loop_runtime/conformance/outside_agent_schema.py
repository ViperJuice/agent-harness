"""Metadata-only schema checks for outside-agent submissions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .outside_agent_core import OutsideAgentBlocker, OutsideAgentSubmissionKind
from .outside_agent_pin import (
    EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN,
    OutsideAgentContractPin,
)

_ALLOWED_TOP_LEVEL_FIELDS = frozenset(
    {
        "submission_schema_version",
        "submission_kind",
        "metadata",
        "provenance_refs",
        "evidence_refs",
    }
)
_REQUIRED_METADATA_FIELDS = frozenset({"submission_id", "content_digest"})


@dataclass(frozen=True)
class OutsideAgentSchemaValidation:
    submission_kind: OutsideAgentSubmissionKind | None
    blockers: tuple[OutsideAgentBlocker, ...]


def validate_outside_agent_submission_schema(
    submission: Mapping[str, Any],
    *,
    contract_pin: OutsideAgentContractPin = EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN,
) -> OutsideAgentSchemaValidation:
    blockers: list[OutsideAgentBlocker] = []

    for field in sorted(set(submission) - _ALLOWED_TOP_LEVEL_FIELDS):
        blockers.append(
            OutsideAgentBlocker(
                "unknown_field",
                "outside-agent submission contains an unsupported top-level field",
                ref=field,
            )
        )

    schema_version = submission.get("submission_schema_version")
    if schema_version != contract_pin.schema_version:
        blockers.append(
            OutsideAgentBlocker(
                "unsupported_schema_version",
                "outside-agent submission schema version is not pinned",
                ref="submission_schema_version",
            )
        )

    submission_kind = _parse_submission_kind(submission.get("submission_kind"))
    if submission_kind is None:
        blockers.append(
            OutsideAgentBlocker(
                "unsupported_submission_kind",
                "outside-agent submission kind is not supported",
                ref="submission_kind",
            )
        )

    metadata = submission.get("metadata")
    if not isinstance(metadata, Mapping):
        blockers.append(
            OutsideAgentBlocker(
                "schema_validation_failed",
                "outside-agent submission metadata is required",
                ref="metadata",
            )
        )
    else:
        missing = sorted(_REQUIRED_METADATA_FIELDS - set(metadata))
        for field in missing:
            blockers.append(
                OutsideAgentBlocker(
                    "schema_validation_failed",
                    "outside-agent submission metadata is incomplete",
                    ref=f"metadata.{field}",
                )
            )

    provenance_refs = submission.get("provenance_refs")
    if not isinstance(provenance_refs, list) or not provenance_refs:
        blockers.append(
            OutsideAgentBlocker(
                "schema_validation_failed",
                "outside-agent submission must include provenance refs",
                ref="provenance_refs",
            )
        )

    evidence_refs = submission.get("evidence_refs", [])
    if evidence_refs is not None and not isinstance(evidence_refs, list):
        blockers.append(
            OutsideAgentBlocker(
                "schema_validation_failed",
                "outside-agent evidence refs must be a list",
                ref="evidence_refs",
            )
        )

    return OutsideAgentSchemaValidation(
        submission_kind=submission_kind,
        blockers=tuple(blockers),
    )


def _parse_submission_kind(value: Any) -> OutsideAgentSubmissionKind | None:
    try:
        return OutsideAgentSubmissionKind(value)
    except ValueError:
        return None


__all__ = [
    "OutsideAgentSchemaValidation",
    "validate_outside_agent_submission_schema",
]
