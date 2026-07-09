"""Deterministic outside-agent conformance verdict core."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from .outside_agent_pin import (
    EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN,
    OutsideAgentContractPin,
)


class OutsideAgentSubmissionKind(str, Enum):
    WORK_REQUEST = "work_request"
    IMPLEMENTATION_SUBMISSION = "implementation_submission"
    AMBIGUITY_REPORT = "ambiguity_report"


class OutsideAgentVerdictStatus(str, Enum):
    PASS = "pass"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class OutsideAgentBlocker:
    code: str
    message: str
    ref: str | None = None


@dataclass(frozen=True)
class OutsideAgentEvidenceRef:
    ref: str
    digest: str
    kind: str = "metadata"


@dataclass(frozen=True)
class OutsideAgentConformanceVerdict:
    verdict_schema_version: str
    submission_kind: OutsideAgentSubmissionKind | None
    status: OutsideAgentVerdictStatus
    blockers: tuple[OutsideAgentBlocker, ...]
    contract_pin: OutsideAgentContractPin
    input_digest: str
    provenance_refs: tuple[str, ...]
    evidence_refs: tuple[OutsideAgentEvidenceRef, ...]
    redaction_posture: str = "metadata_only"
    metadata: Mapping[str, str] = field(default_factory=dict)


def validate_outside_agent_submission(
    submission: Mapping[str, Any],
    *,
    contract_pin: OutsideAgentContractPin = EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN,
) -> OutsideAgentConformanceVerdict:
    """Validate a metadata-only outside-agent submission without external I/O."""
    from .outside_agent_provenance import validate_outside_agent_provenance
    from .outside_agent_redaction import assert_outside_agent_metadata_only
    from .outside_agent_schema import validate_outside_agent_submission_schema

    input_digest = _digest_mapping(submission)
    schema_result = validate_outside_agent_submission_schema(
        submission, contract_pin=contract_pin
    )
    provenance_result = validate_outside_agent_provenance(submission)
    redaction_blockers = assert_outside_agent_metadata_only(submission)

    blockers = (
        schema_result.blockers
        + provenance_result.blockers
        + tuple(redaction_blockers)
    )
    status = (
        OutsideAgentVerdictStatus.BLOCKED
        if blockers
        else OutsideAgentVerdictStatus.PASS
    )

    evidence_refs = tuple(
        OutsideAgentEvidenceRef(ref=ref.ref, digest=ref.digest, kind=ref.kind)
        for ref in provenance_result.evidence_refs
    )
    return OutsideAgentConformanceVerdict(
        verdict_schema_version=contract_pin.verdict_schema_version,
        submission_kind=schema_result.submission_kind,
        status=status,
        blockers=blockers,
        contract_pin=contract_pin,
        input_digest=input_digest,
        provenance_refs=provenance_result.provenance_refs,
        evidence_refs=evidence_refs,
        redaction_posture=contract_pin.redaction_posture,
        metadata={"source_owner": contract_pin.source_owner},
    )


def _digest_mapping(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "OutsideAgentBlocker",
    "OutsideAgentConformanceVerdict",
    "OutsideAgentEvidenceRef",
    "OutsideAgentSubmissionKind",
    "OutsideAgentVerdictStatus",
    "validate_outside_agent_submission",
]
