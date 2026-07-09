"""Advisory outside-agent preflight evidence."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Mapping

from .outside_agent_core import (
    OutsideAgentBlocker,
    OutsideAgentConformanceVerdict,
    OutsideAgentVerdictStatus,
    validate_outside_agent_submission,
)
from .outside_agent_pin import (
    EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN,
    OutsideAgentContractPin,
)


_REDACTION_BLOCKER_CODES = frozenset(
    {
        "local_env_value_present",
        "raw_log_present",
        "raw_payload_present",
        "secret_like_value_present",
    }
)
_PROVENANCE_BLOCKER_CODES = frozenset(
    {
        "absolute_path_ref",
        "digest_mismatch",
        "missing_digest",
        "path_traversal_ref",
        "unsafe_source_ref",
    }
)


class OutsideAgentAdvisoryExitCode(IntEnum):
    PASS = 0
    INTERNAL_ERROR = 1
    MALFORMED_INPUT = 2
    REDACTION_VIOLATION = 3
    PROVENANCE_FAILURE = 4


@dataclass(frozen=True)
class OutsideAgentAdvisoryEvidence:
    authority: str
    classification: str
    exit_code: OutsideAgentAdvisoryExitCode
    verdict: OutsideAgentConformanceVerdict
    metadata: Mapping[str, str] = field(default_factory=dict)


def build_outside_agent_advisory_evidence(
    submission: Any,
    *,
    contract_pin: OutsideAgentContractPin = EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN,
) -> OutsideAgentAdvisoryEvidence:
    """Build deterministic advisory evidence without external I/O."""
    if not isinstance(submission, Mapping):
        verdict = _malformed_verdict(
            input_digest=_digest_value(submission),
            contract_pin=contract_pin,
            blocker=OutsideAgentBlocker(
                "malformed_input",
                "outside-agent submission JSON must be an object",
                ref="$",
            ),
        )
        return _evidence_for_verdict(verdict)

    verdict = validate_outside_agent_submission(submission, contract_pin=contract_pin)
    return _evidence_for_verdict(verdict)


def build_malformed_outside_agent_advisory_evidence(
    *,
    input_digest: str,
    message: str = "outside-agent submission JSON could not be parsed",
    contract_pin: OutsideAgentContractPin = EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN,
) -> OutsideAgentAdvisoryEvidence:
    verdict = _malformed_verdict(
        input_digest=input_digest,
        contract_pin=contract_pin,
        blocker=OutsideAgentBlocker("malformed_input", message, ref="$"),
    )
    return _evidence_for_verdict(verdict)


def serialize_outside_agent_advisory_evidence(
    evidence: OutsideAgentAdvisoryEvidence,
) -> dict[str, Any]:
    verdict = evidence.verdict
    return {
        "authority": evidence.authority,
        "classification": evidence.classification,
        "exit_code": int(evidence.exit_code),
        "verdict_schema_version": verdict.verdict_schema_version,
        "submission_kind": verdict.submission_kind.value if verdict.submission_kind else None,
        "status": verdict.status.value,
        "blockers": [
            {"code": blocker.code, "message": blocker.message, "ref": blocker.ref}
            for blocker in verdict.blockers
        ],
        "contract_pin": {
            "schema_version": verdict.contract_pin.schema_version,
            "verdict_schema_version": verdict.contract_pin.verdict_schema_version,
            "contract_package": verdict.contract_pin.contract_package,
            "contract_version": verdict.contract_pin.contract_version,
            "contract_git_sha": verdict.contract_pin.contract_git_sha,
            "vector_manifest_name": verdict.contract_pin.vector_manifest_name,
            "vector_manifest_hash": verdict.contract_pin.vector_manifest_hash,
            "source_owner": verdict.contract_pin.source_owner,
            "redaction_posture": verdict.contract_pin.redaction_posture,
        },
        "input_digest": verdict.input_digest,
        "provenance_refs": list(verdict.provenance_refs),
        "evidence_refs": [
            {"ref": ref.ref, "digest": ref.digest, "kind": ref.kind}
            for ref in verdict.evidence_refs
        ],
        "redaction_posture": verdict.redaction_posture,
        "metadata": dict(evidence.metadata),
    }


def digest_outside_agent_submission_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _evidence_for_verdict(
    verdict: OutsideAgentConformanceVerdict,
) -> OutsideAgentAdvisoryEvidence:
    exit_code = _exit_code_for_blockers(verdict.blockers)
    return OutsideAgentAdvisoryEvidence(
        authority="advisory",
        classification=_classification_for_exit_code(exit_code),
        exit_code=exit_code,
        verdict=verdict,
        metadata={"source": "outside_agent_advisory_preflight"},
    )


def _exit_code_for_blockers(
    blockers: tuple[OutsideAgentBlocker, ...],
) -> OutsideAgentAdvisoryExitCode:
    if not blockers:
        return OutsideAgentAdvisoryExitCode.PASS
    codes = {blocker.code for blocker in blockers}
    if codes & _REDACTION_BLOCKER_CODES:
        return OutsideAgentAdvisoryExitCode.REDACTION_VIOLATION
    if codes & _PROVENANCE_BLOCKER_CODES:
        return OutsideAgentAdvisoryExitCode.PROVENANCE_FAILURE
    return OutsideAgentAdvisoryExitCode.MALFORMED_INPUT


def _classification_for_exit_code(exit_code: OutsideAgentAdvisoryExitCode) -> str:
    return {
        OutsideAgentAdvisoryExitCode.PASS: "clean_advisory_pass",
        OutsideAgentAdvisoryExitCode.INTERNAL_ERROR: "internal_error",
        OutsideAgentAdvisoryExitCode.MALFORMED_INPUT: "malformed_input",
        OutsideAgentAdvisoryExitCode.REDACTION_VIOLATION: "redaction_violation",
        OutsideAgentAdvisoryExitCode.PROVENANCE_FAILURE: "provenance_failure",
    }[exit_code]


def _malformed_verdict(
    *,
    input_digest: str,
    contract_pin: OutsideAgentContractPin,
    blocker: OutsideAgentBlocker,
) -> OutsideAgentConformanceVerdict:
    return OutsideAgentConformanceVerdict(
        verdict_schema_version=contract_pin.verdict_schema_version,
        submission_kind=None,
        status=OutsideAgentVerdictStatus.BLOCKED,
        blockers=(blocker,),
        contract_pin=contract_pin,
        input_digest=input_digest,
        provenance_refs=(),
        evidence_refs=(),
        redaction_posture=contract_pin.redaction_posture,
        metadata={"source_owner": contract_pin.source_owner},
    )


def _digest_value(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "OutsideAgentAdvisoryEvidence",
    "OutsideAgentAdvisoryExitCode",
    "build_malformed_outside_agent_advisory_evidence",
    "build_outside_agent_advisory_evidence",
    "digest_outside_agent_submission_bytes",
    "serialize_outside_agent_advisory_evidence",
]
