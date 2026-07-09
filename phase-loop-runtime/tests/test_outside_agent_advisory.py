import os

from phase_loop_runtime.conformance import (
    EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN,
    OutsideAgentAdvisoryEvidence,
    OutsideAgentAdvisoryExitCode,
    build_outside_agent_advisory_evidence,
    serialize_outside_agent_advisory_evidence,
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


def test_builds_clean_advisory_evidence_without_external_access(monkeypatch):
    monkeypatch.setenv("API_KEY", "sk-test-value")
    monkeypatch.setenv("OUTSIDE_AGENT_SPEC_ROOT", "/not/read/by/advisory")
    calls = []
    monkeypatch.setattr(os, "system", lambda command: calls.append(command) or 1)

    evidence = build_outside_agent_advisory_evidence(_submission())
    payload = serialize_outside_agent_advisory_evidence(evidence)

    assert isinstance(evidence, OutsideAgentAdvisoryEvidence)
    assert evidence.exit_code == OutsideAgentAdvisoryExitCode.PASS
    assert payload["authority"] == "advisory"
    assert payload["classification"] == "clean_advisory_pass"
    assert payload["status"] == "pass"
    assert payload["redaction_posture"] == "metadata_only"
    assert payload["contract_pin"]["schema_version"] == "outside_agent_submission.v0.1"
    assert payload["contract_pin"]["source_owner"] == EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN.source_owner
    assert len(payload["input_digest"]) == 64
    assert payload["provenance_refs"] == ["requests/oa-1.json"]
    assert payload["evidence_refs"] == [
        {"ref": "requests/oa-1.json", "digest": "b" * 64, "kind": "metadata"},
        {"ref": "evidence/oa-1.json", "digest": "c" * 64, "kind": "metadata"},
    ]
    assert "accepted_for_merge" not in payload
    assert "merge_verdict" not in payload
    assert calls == []


def test_serialized_advisory_evidence_is_deterministic_and_metadata_only():
    first = serialize_outside_agent_advisory_evidence(build_outside_agent_advisory_evidence(_submission()))
    second = serialize_outside_agent_advisory_evidence(build_outside_agent_advisory_evidence(_submission()))

    assert first == second
    assert first["metadata"] == {"source": "outside_agent_advisory_preflight"}


def test_malformed_submission_maps_to_exit_code_2():
    evidence = build_outside_agent_advisory_evidence(["not", "an", "object"])
    payload = serialize_outside_agent_advisory_evidence(evidence)

    assert evidence.exit_code == OutsideAgentAdvisoryExitCode.MALFORMED_INPUT
    assert payload["classification"] == "malformed_input"
    assert payload["exit_code"] == 2
    assert payload["blockers"][0]["code"] == "malformed_input"


def test_redaction_blocker_maps_to_exit_code_3():
    submission = _submission(raw_payload={"body_digest": "d" * 64})

    evidence = build_outside_agent_advisory_evidence(submission)
    payload = serialize_outside_agent_advisory_evidence(evidence)

    assert evidence.exit_code == OutsideAgentAdvisoryExitCode.REDACTION_VIOLATION
    assert payload["classification"] == "redaction_violation"
    assert payload["exit_code"] == 3
    assert {blocker["code"] for blocker in payload["blockers"]} >= {
        "raw_payload_present",
        "unknown_field",
    }


def test_provenance_blocker_maps_to_exit_code_4():
    submission = _submission(provenance_refs=[{"ref": "../unsafe.json", "digest": "b" * 64}])

    evidence = build_outside_agent_advisory_evidence(submission)
    payload = serialize_outside_agent_advisory_evidence(evidence)

    assert evidence.exit_code == OutsideAgentAdvisoryExitCode.PROVENANCE_FAILURE
    assert payload["classification"] == "provenance_failure"
    assert payload["exit_code"] == 4
    assert payload["blockers"][0]["code"] == "path_traversal_ref"
