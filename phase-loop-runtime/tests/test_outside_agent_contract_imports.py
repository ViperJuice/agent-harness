import hashlib
import json
from pathlib import Path

import pytest

from phase_loop_runtime.conformance.outside_agent_imports import (
    OutsideAgentContractError,
    load_outside_agent_contract_pin,
)
from phase_loop_runtime.conformance.outside_agent_pin import OutsideAgentContractPin


def _pin_for(root: Path, *, schema_version="outside_agent_submission.v0.1") -> OutsideAgentContractPin:
    manifest = root / "test-vectors" / "outside-agent" / "manifest.json"
    return OutsideAgentContractPin(
        schema_version=schema_version,
        verdict_schema_version="outside_agent_route_verdict.v0.1",
        contract_package="consiliency-spec",
        contract_version="0.1.0",
        contract_git_sha="0" * 40,
        vector_manifest_hash=hashlib.sha256(manifest.read_bytes()).hexdigest(),
        vector_manifest_name="test-vectors/outside-agent/manifest.json",
        source_owner="Consiliency/spec",
        redaction_posture="metadata_only",
    )


def _write_spec_root(root: Path, *, schema_version="outside_agent_submission.v0.1", verdict_version="outside_agent_route_verdict.v0.1", manifest=None) -> None:
    (root / "schemas").mkdir(parents=True)
    (root / "test-vectors" / "outside-agent").mkdir(parents=True)
    (root / "schemas" / "outside-agent-submission.schema.json").write_text(
        json.dumps({"properties": {"submission_schema_version": {"const": schema_version}}}),
        encoding="utf-8",
    )
    (root / "schemas" / "outside-agent-route-verdict.schema.json").write_text(
        json.dumps({"properties": {"verdict_schema_version": {"const": verdict_version}}}),
        encoding="utf-8",
    )
    (root / "test-vectors" / "outside-agent" / "manifest.json").write_text(
        json.dumps(manifest if manifest is not None else {"manifest_schema_version": "outside_agent_vector_manifest.v0.1"}),
        encoding="utf-8",
    )


def test_loads_pin_from_matching_spec_root(tmp_path):
    _write_spec_root(tmp_path)
    expected = _pin_for(tmp_path)

    assert load_outside_agent_contract_pin(tmp_path, expected_pin=expected) == expected


def test_missing_contract_fails_closed(tmp_path):
    expected = OutsideAgentContractPin(
        schema_version="outside_agent_submission.v0.1",
        verdict_schema_version="outside_agent_route_verdict.v0.1",
        contract_package="consiliency-spec",
        contract_version="0.1.0",
        contract_git_sha="0" * 40,
        vector_manifest_hash="0" * 64,
        vector_manifest_name="test-vectors/outside-agent/manifest.json",
        source_owner="Consiliency/spec",
        redaction_posture="metadata_only",
    )

    with pytest.raises(OutsideAgentContractError) as exc_info:
        load_outside_agent_contract_pin(tmp_path / "missing", expected_pin=expected)

    assert exc_info.value.code == "missing_contract"


def test_unknown_schema_version_fails_closed(tmp_path):
    _write_spec_root(tmp_path, schema_version="outside_agent_submission.v9")
    expected = _pin_for(tmp_path)

    with pytest.raises(OutsideAgentContractError) as exc_info:
        load_outside_agent_contract_pin(tmp_path, expected_pin=expected)

    assert exc_info.value.code == "unknown_contract_version"


def test_vector_manifest_hash_mismatch_fails_closed(tmp_path):
    _write_spec_root(tmp_path)
    expected = _pin_for(tmp_path)
    (tmp_path / "test-vectors" / "outside-agent" / "manifest.json").write_text(
        json.dumps({"manifest_schema_version": "outside_agent_vector_manifest.v0.1", "changed": True}),
        encoding="utf-8",
    )

    with pytest.raises(OutsideAgentContractError) as exc_info:
        load_outside_agent_contract_pin(tmp_path, expected_pin=expected)

    assert exc_info.value.code == "vector_manifest_hash_mismatch"
