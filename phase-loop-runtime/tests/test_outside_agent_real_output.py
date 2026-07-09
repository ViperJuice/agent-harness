import json

from phase_loop_runtime.conformance import (
    OutsideAgentValidationExitCode,
    build_outside_agent_validation_verdict,
    serialize_outside_agent_validation_verdict,
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


def test_serializes_clean_governed_pipeline_verdict_shape():
    payload = serialize_outside_agent_validation_verdict(
        build_outside_agent_validation_verdict(
            _submission(),
            submitted_refs=("src/agent.py", "docs/result.md"),
        )
    )

    assert payload["gate_id"] == "real_conformance_gate.v0.1"
    assert payload["authority"] == "governed_pipeline_validator"
    assert payload["validator_version"]
    assert payload["command"] == "outside-agent-validate"
    assert payload["verdict_schema_version"] == "outside_agent_route_verdict.v0.1"
    assert payload["contract_pin"]["contract_package"] == "consiliency-spec"
    assert payload["vector_manifest_hash"] == payload["contract_pin"]["vector_manifest_hash"]
    assert len(payload["input_digest"]) == 64
    assert payload["submitted_refs"] == ["src/agent.py", "docs/result.md"]
    assert payload["status"] == "pass"
    assert payload["blockers"] == []
    assert payload["evidence_refs"] == [
        {"ref": "requests/oa-1.json", "digest": "b" * 64, "kind": "metadata"},
        {"ref": "evidence/oa-1.json", "digest": "c" * 64, "kind": "metadata"},
    ]
    assert payload["redaction_posture"] == "metadata_only"
    assert payload["vectors_executed"] is False
    assert payload["exit_code"] == 0


def test_serialized_real_verdict_is_deterministic_json():
    first = serialize_outside_agent_validation_verdict(
        build_outside_agent_validation_verdict(_submission())
    )
    second = serialize_outside_agent_validation_verdict(
        build_outside_agent_validation_verdict(_submission())
    )

    assert json.dumps(first, sort_keys=True, separators=(",", ":")) == json.dumps(
        second,
        sort_keys=True,
        separators=(",", ":"),
    )


def test_serialized_blocked_verdict_has_typed_blockers_and_no_advisory_fields():
    payload = serialize_outside_agent_validation_verdict(
        build_outside_agent_validation_verdict(_submission(raw_payload={"digest": "d" * 64}))
    )

    assert payload["status"] == "blocked"
    assert payload["exit_code"] == int(OutsideAgentValidationExitCode.REDACTION_VIOLATION)
    assert {"code": "raw_payload_present", "message": "outside-agent metadata contains raw payload content", "ref": "$.raw_payload"} in payload["blockers"]
    assert "classification" not in payload
    assert "accepted_for_merge" not in payload
    assert "merge_verdict" not in payload
    assert "portal_projection" not in payload
