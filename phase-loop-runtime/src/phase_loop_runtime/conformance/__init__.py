"""``phase_loop_runtime.conformance`` -- the named conformance surface.

This package preserves the existing public ``.consiliency/`` conformance
imports while providing a namespace for outside-agent contract pin helpers.
"""
from __future__ import annotations

from ..consiliency_gates import (
    CONSILIENCY_GATES_ENV,
    CONSILIENCY_GATES_MODES,
    DEFAULT_CONSILIENCY_GATES_MODE,
    resolve_consiliency_gates_mode,
    scan_consiliency_gates,
)
from ..consiliency_ingest import evaluate_governance_scope
from ..git_discipline import evaluate_git_discipline, self_heal_partition
from .outside_agent_core import (
    OutsideAgentBlocker,
    OutsideAgentConformanceVerdict,
    OutsideAgentEvidenceRef,
    OutsideAgentSubmissionKind,
    OutsideAgentVerdictStatus,
    validate_outside_agent_submission,
)
from .outside_agent_advisory import (
    OutsideAgentAdvisoryEvidence,
    OutsideAgentAdvisoryExitCode,
    build_outside_agent_advisory_evidence,
    serialize_outside_agent_advisory_evidence,
)
from .outside_agent_imports import (
    OutsideAgentContractError,
    load_outside_agent_contract_pin,
)
from .outside_agent_pin import (
    EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN,
    OutsideAgentContractPin,
)
from .outside_agent_real import (
    OutsideAgentSubmittedRef,
    OutsideAgentValidationExitCode,
    OutsideAgentValidationVerdict,
    build_outside_agent_validation_verdict,
)
from .outside_agent_real_output import (
    digest_outside_agent_validation_bytes,
    serialize_outside_agent_validation_verdict,
)

__all__ = [
    "scan_consiliency_gates",
    "resolve_consiliency_gates_mode",
    "CONSILIENCY_GATES_ENV",
    "CONSILIENCY_GATES_MODES",
    "DEFAULT_CONSILIENCY_GATES_MODE",
    "evaluate_git_discipline",
    "self_heal_partition",
    "evaluate_governance_scope",
    "OutsideAgentBlocker",
    "OutsideAgentConformanceVerdict",
    "OutsideAgentEvidenceRef",
    "OutsideAgentSubmissionKind",
    "OutsideAgentVerdictStatus",
    "validate_outside_agent_submission",
    "OutsideAgentAdvisoryEvidence",
    "OutsideAgentAdvisoryExitCode",
    "build_outside_agent_advisory_evidence",
    "serialize_outside_agent_advisory_evidence",
    "OutsideAgentContractError",
    "load_outside_agent_contract_pin",
    "EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN",
    "OutsideAgentContractPin",
    "OutsideAgentSubmittedRef",
    "OutsideAgentValidationExitCode",
    "OutsideAgentValidationVerdict",
    "build_outside_agent_validation_verdict",
    "digest_outside_agent_validation_bytes",
    "serialize_outside_agent_validation_verdict",
]
