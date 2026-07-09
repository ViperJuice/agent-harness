from phase_loop_runtime.conformance.outside_agent_pin import (
    EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN,
    OutsideAgentContractPin,
)


def test_expected_outside_agent_contract_pin_records_spec_identity():
    pin = EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN

    assert isinstance(pin, OutsideAgentContractPin)
    assert pin.schema_version == "outside_agent_submission.v0.1"
    assert pin.verdict_schema_version == "outside_agent_route_verdict.v0.1"
    assert pin.contract_package == "consiliency-spec"
    assert pin.contract_version == "0.1.0"
    assert len(pin.contract_git_sha) == 40
    assert len(pin.vector_manifest_hash) == 64
    assert pin.vector_manifest_name == "test-vectors/outside-agent/manifest.json"
    assert pin.source_owner == "Consiliency/spec"
    assert pin.redaction_posture == "metadata_only"
