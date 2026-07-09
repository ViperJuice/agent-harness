from phase_loop_runtime.conformance import (
    EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN,
    OutsideAgentSubmittedRef,
    OutsideAgentValidationExitCode,
    OutsideAgentValidationVerdict,
    build_outside_agent_validation_verdict,
)
from phase_loop_runtime.conformance.outside_agent_core import (
    OutsideAgentBlocker,
    OutsideAgentConformanceVerdict,
    validate_outside_agent_submission,
)


def _submission(**overrides):
    submission = {
        "submission_schema_version": "outside_agent_submission.v0.1",
        "submission_kind": "work_request",
        "metadata": {
            "submission_id": "oa-1",
            "content_digest": "a" * 64,
        },
        "provenance_refs": [
            {"ref": "requests/oa-1.json", "digest": "b" * 64},
        ],
        "evidence_refs": [
            {"ref": "evidence/oa-1.json", "digest": "c" * 64},
        ],
    }
    submission.update(overrides)
    return submission


def test_real_validator_wraps_core_once_with_metadata_only_evidence():
    calls = []

    def core(submission, *, contract_pin):
        calls.append((submission, contract_pin))
        return validate_outside_agent_submission(submission, contract_pin=contract_pin)

    verdict = build_outside_agent_validation_verdict(
        _submission(),
        submitted_refs=("src/agent.py",),
        core_validator=core,
    )

    assert isinstance(verdict, OutsideAgentValidationVerdict)
    assert verdict.authority == "governed_pipeline_validator"
    assert verdict.validator_version
    assert verdict.exit_code == OutsideAgentValidationExitCode.PASS
    assert verdict.verdict.contract_pin == EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN
    assert len(verdict.verdict.input_digest) == 64
    assert verdict.submitted_refs == (OutsideAgentSubmittedRef(ref="src/agent.py"),)
    assert verdict.vectors_executed is False
    assert calls == [(_submission(), EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN)]


def test_real_validator_malformed_object_maps_to_exit_2_and_calls_core_once():
    calls = []

    def core(submission, *, contract_pin):
        calls.append(submission)
        return validate_outside_agent_submission(submission, contract_pin=contract_pin)

    verdict = build_outside_agent_validation_verdict({"metadata": {}}, core_validator=core)

    assert verdict.exit_code == OutsideAgentValidationExitCode.MALFORMED_INPUT
    assert calls == [{"metadata": {}}]
    assert "schema_validation_failed" in {blocker.code for blocker in verdict.verdict.blockers}


def test_real_validator_redaction_violation_maps_to_exit_3():
    verdict = build_outside_agent_validation_verdict(
        _submission(provider_response_body="digest-only-marker")
    )

    assert verdict.exit_code == OutsideAgentValidationExitCode.REDACTION_VIOLATION
    assert "raw_payload_present" in {blocker.code for blocker in verdict.verdict.blockers}


def test_real_validator_provenance_failure_maps_to_exit_4():
    verdict = build_outside_agent_validation_verdict(
        _submission(provenance_refs=[{"ref": "/tmp/unsafe.json", "digest": "b" * 64}])
    )

    assert verdict.exit_code == OutsideAgentValidationExitCode.PROVENANCE_FAILURE
    assert "absolute_path_ref" in {blocker.code for blocker in verdict.verdict.blockers}


def test_real_validator_contract_pin_failure_maps_to_exit_5():
    verdict = build_outside_agent_validation_verdict(
        _submission(submission_schema_version="outside_agent_submission.v9")
    )

    assert verdict.exit_code == OutsideAgentValidationExitCode.CONTRACT_VECTOR_PIN_FAILURE
    assert "unsupported_schema_version" in {blocker.code for blocker in verdict.verdict.blockers}


def test_real_validator_other_conformance_blocker_maps_to_exit_6():
    base = validate_outside_agent_submission(_submission())

    def core(submission, *, contract_pin):
        return OutsideAgentConformanceVerdict(
            verdict_schema_version=base.verdict_schema_version,
            submission_kind=base.submission_kind,
            status=base.status,
            blockers=(OutsideAgentBlocker("policy_blocked", "blocked by policy"),),
            contract_pin=base.contract_pin,
            input_digest=base.input_digest,
            provenance_refs=base.provenance_refs,
            evidence_refs=base.evidence_refs,
            redaction_posture=base.redaction_posture,
            metadata=base.metadata,
        )

    verdict = build_outside_agent_validation_verdict(_submission(), core_validator=core)

    assert verdict.exit_code == OutsideAgentValidationExitCode.CONFORMANCE_BLOCKED


def test_real_validator_rejects_absolute_submitted_refs_without_raw_paths():
    verdict = build_outside_agent_validation_verdict(
        _submission(),
        submitted_refs=("/tmp/agent.py",),
    )

    assert verdict.exit_code == OutsideAgentValidationExitCode.PROVENANCE_FAILURE
    assert verdict.submitted_refs == ()
    assert verdict.verdict.blockers[-1].ref == "submitted_refs.0"
