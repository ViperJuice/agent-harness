"""Governed-pipeline outside-agent validator runtime surface."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Iterable, Mapping

from .. import __version__
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
from .outside_agent_provenance import normalize_outside_agent_ref

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
_CONTRACT_PIN_BLOCKER_CODES = frozenset(
    {
        "contract_pin_failure",
        "unsupported_schema_version",
        "vector_manifest_hash_mismatch",
    }
)


class OutsideAgentValidationExitCode(IntEnum):
    PASS = 0
    INTERNAL_ERROR = 1
    MALFORMED_INPUT = 2
    REDACTION_VIOLATION = 3
    PROVENANCE_FAILURE = 4
    CONTRACT_VECTOR_PIN_FAILURE = 5
    CONFORMANCE_BLOCKED = 6


@dataclass(frozen=True)
class OutsideAgentSubmittedRef:
    ref: str


@dataclass(frozen=True)
class OutsideAgentValidationVerdict:
    authority: str
    validator_version: str
    exit_code: OutsideAgentValidationExitCode
    verdict: OutsideAgentConformanceVerdict
    submitted_refs: tuple[OutsideAgentSubmittedRef, ...]
    vectors_executed: bool = False
    metadata: Mapping[str, str] = field(default_factory=dict)


def build_outside_agent_validation_verdict(
    submission: Any,
    *,
    submitted_refs: Iterable[str] = (),
    contract_pin: OutsideAgentContractPin = EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN,
    validator_version: str = __version__,
    core_validator: Callable[..., OutsideAgentConformanceVerdict] = validate_outside_agent_submission,
) -> OutsideAgentValidationVerdict:
    """Build deterministic governed-pipeline validation evidence without external I/O."""
    normalized_refs, ref_blockers = _normalize_submitted_refs(submitted_refs)
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
    else:
        verdict = core_validator(submission, contract_pin=contract_pin)

    if ref_blockers:
        verdict = _verdict_with_extra_blockers(verdict, ref_blockers)

    return OutsideAgentValidationVerdict(
        authority="governed_pipeline_validator",
        validator_version=validator_version,
        exit_code=_exit_code_for_blockers(verdict.blockers),
        verdict=verdict,
        submitted_refs=tuple(OutsideAgentSubmittedRef(ref=ref) for ref in normalized_refs),
        vectors_executed=False,
        metadata={"source": "outside_agent_governed_pipeline_validator"},
    )


def build_malformed_outside_agent_validation_verdict(
    *,
    input_digest: str,
    message: str = "outside-agent submission JSON could not be parsed",
    contract_pin: OutsideAgentContractPin = EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN,
    validator_version: str = __version__,
) -> OutsideAgentValidationVerdict:
    verdict = _malformed_verdict(
        input_digest=input_digest,
        contract_pin=contract_pin,
        blocker=OutsideAgentBlocker("malformed_input", message, ref="$"),
    )
    return OutsideAgentValidationVerdict(
        authority="governed_pipeline_validator",
        validator_version=validator_version,
        exit_code=OutsideAgentValidationExitCode.MALFORMED_INPUT,
        verdict=verdict,
        submitted_refs=(),
        vectors_executed=False,
        metadata={"source": "outside_agent_governed_pipeline_validator"},
    )


def _exit_code_for_blockers(
    blockers: tuple[OutsideAgentBlocker, ...],
) -> OutsideAgentValidationExitCode:
    if not blockers:
        return OutsideAgentValidationExitCode.PASS
    codes = {blocker.code for blocker in blockers}
    if codes & _REDACTION_BLOCKER_CODES:
        return OutsideAgentValidationExitCode.REDACTION_VIOLATION
    if codes & _PROVENANCE_BLOCKER_CODES:
        return OutsideAgentValidationExitCode.PROVENANCE_FAILURE
    if "malformed_input" in codes or "schema_validation_failed" in codes:
        return OutsideAgentValidationExitCode.MALFORMED_INPUT
    if codes & _CONTRACT_PIN_BLOCKER_CODES:
        return OutsideAgentValidationExitCode.CONTRACT_VECTOR_PIN_FAILURE
    return OutsideAgentValidationExitCode.CONFORMANCE_BLOCKED


def _normalize_submitted_refs(
    submitted_refs: Iterable[str],
) -> tuple[tuple[str, ...], tuple[OutsideAgentBlocker, ...]]:
    refs: list[str] = []
    blockers: list[OutsideAgentBlocker] = []
    for index, ref in enumerate(submitted_refs):
        try:
            refs.append(normalize_outside_agent_ref(ref))
        except ValueError as exc:
            blockers.append(
                OutsideAgentBlocker(
                    str(exc),
                    "outside-agent submitted ref is not a safe repo-relative path",
                    ref=f"submitted_refs.{index}",
                )
            )
    return tuple(refs), tuple(blockers)


def _verdict_with_extra_blockers(
    verdict: OutsideAgentConformanceVerdict,
    blockers: tuple[OutsideAgentBlocker, ...],
) -> OutsideAgentConformanceVerdict:
    from dataclasses import replace

    return replace(
        verdict,
        status=OutsideAgentVerdictStatus.BLOCKED,
        blockers=verdict.blockers + blockers,
    )


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
    "OutsideAgentSubmittedRef",
    "OutsideAgentValidationExitCode",
    "OutsideAgentValidationVerdict",
    "build_malformed_outside_agent_validation_verdict",
    "build_outside_agent_validation_verdict",
]
