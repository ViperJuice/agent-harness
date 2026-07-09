"""Pinned outside-agent contract metadata consumed from Consiliency/spec."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OutsideAgentContractPin:
    schema_version: str
    verdict_schema_version: str
    contract_package: str
    contract_version: str
    contract_git_sha: str
    vector_manifest_hash: str
    vector_manifest_name: str
    source_owner: str
    redaction_posture: str


EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN = OutsideAgentContractPin(
    schema_version="outside_agent_submission.v0.1",
    verdict_schema_version="outside_agent_route_verdict.v0.1",
    contract_package="consiliency-spec",
    contract_version="0.1.0",
    contract_git_sha="c1085483a015ae61aba5fa3064fbd3a96ccc9a33",
    vector_manifest_hash="33cdb767831ee8eaf45961cdb7ccb5b8b21ac69ec054b0da7304e08a2d06434e",
    vector_manifest_name="test-vectors/outside-agent/manifest.json",
    source_owner="Consiliency/spec",
    redaction_posture="metadata_only",
)


__all__ = ["OutsideAgentContractPin", "EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN"]
