from dataclasses import replace

from phase_loop_runtime.conformance.outside_agent_core import (
    OutsideAgentConformanceVerdict,
    OutsideAgentEvidenceRef,
    OutsideAgentSubmissionKind,
    OutsideAgentVerdictStatus,
    validate_outside_agent_submission,
)
from phase_loop_runtime.conformance.outside_agent_pin import (
    EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN,
)
from phase_loop_runtime.conformance.outside_agent_redaction import (
    assert_outside_agent_metadata_only,
    sanitize_outside_agent_verdict,
)


def _submission(**extra):
    value = {
        "submission_schema_version": "outside_agent_submission.v0.1",
        "submission_kind": "work_request",
        "metadata": {"submission_id": "oa-1", "content_digest": "a" * 64},
        "provenance_refs": [{"ref": "requests/oa-1.json", "digest": "b" * 64}],
    }
    value.update(extra)
    return value


def _codes(blockers):
    return {blocker.code for blocker in blockers}


def test_clean_metadata_only_submission_passes():
    assert assert_outside_agent_metadata_only(_submission()) == ()


def test_raw_payload_provider_body_raw_logs_and_vector_bodies_fail_closed():
    value = _submission(
        raw_payload={"body": "raw"},
        metadata={"submission_id": "oa-1", "content_digest": "a" * 64, "raw_log": "DEBUG a"},
        evidence_refs=[{"ref": "evidence/a.json", "digest": "c" * 64, "copied_vector_body": "body"}],
    )

    codes = _codes(assert_outside_agent_metadata_only(value))

    assert "raw_payload_present" in codes
    assert "raw_log_present" in codes


def test_secret_like_values_and_local_env_values_fail_closed():
    value = _submission(
        metadata={
            "submission_id": "oa-1",
            "content_digest": "a" * 64,
            "local_env": {"HOME": "/home/user"},
            "api_key": "sk-test-secret",
        }
    )

    codes = _codes(assert_outside_agent_metadata_only(value))

    assert "secret_like_value_present" in codes
    assert "local_env_value_present" in codes


def test_core_verdict_contains_only_metadata_refs_and_digests():
    verdict = validate_outside_agent_submission(_submission())

    assert verdict.status == OutsideAgentVerdictStatus.PASS
    assert verdict.input_digest
    assert verdict.provenance_refs == ("requests/oa-1.json",)
    assert verdict.redaction_posture == "metadata_only"
    assert verdict.contract_pin == EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN


def test_sanitize_outside_agent_verdict_blocks_non_metadata_output():
    verdict = OutsideAgentConformanceVerdict(
        verdict_schema_version="outside_agent_route_verdict.v0.1",
        submission_kind=OutsideAgentSubmissionKind.WORK_REQUEST,
        status=OutsideAgentVerdictStatus.PASS,
        blockers=(),
        contract_pin=EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN,
        input_digest="a" * 64,
        provenance_refs=("requests/oa-1.json",),
        evidence_refs=(OutsideAgentEvidenceRef("evidence/a.json", "b" * 64),),
        metadata={"raw_payload": "provider body"},
    )

    sanitized = sanitize_outside_agent_verdict(replace(verdict))

    assert sanitized.status == OutsideAgentVerdictStatus.BLOCKED
    assert "raw_payload_present" in _codes(sanitized.blockers)
