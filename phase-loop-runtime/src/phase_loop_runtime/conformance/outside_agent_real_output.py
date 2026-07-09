"""JSON serialization for governed-pipeline outside-agent validation verdicts."""
from __future__ import annotations

import hashlib
from typing import Any

from .outside_agent_real import OutsideAgentValidationVerdict


def serialize_outside_agent_validation_verdict(
    validation: OutsideAgentValidationVerdict,
) -> dict[str, Any]:
    verdict = validation.verdict
    return {
        "gate_id": "real_conformance_gate.v0.1",
        "authority": validation.authority,
        "validator_version": validation.validator_version,
        "command": "outside-agent-validate",
        "verdict_schema_version": verdict.verdict_schema_version,
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
        "vector_manifest_hash": verdict.contract_pin.vector_manifest_hash,
        "input_digest": verdict.input_digest,
        "submitted_refs": [ref.ref for ref in validation.submitted_refs],
        "submission_kind": verdict.submission_kind.value if verdict.submission_kind else None,
        "status": verdict.status.value,
        "blockers": [
            {"code": blocker.code, "message": blocker.message, "ref": blocker.ref}
            for blocker in verdict.blockers
        ],
        "evidence_refs": [
            {"ref": ref.ref, "digest": ref.digest, "kind": ref.kind}
            for ref in verdict.evidence_refs
        ],
        "redaction_posture": verdict.redaction_posture,
        "vectors_executed": validation.vectors_executed,
        "exit_code": int(validation.exit_code),
    }


def digest_outside_agent_validation_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


__all__ = [
    "digest_outside_agent_validation_bytes",
    "serialize_outside_agent_validation_verdict",
]
