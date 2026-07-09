"""Deterministic vector runner for outside-agent conformance manifests."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Mapping

from .outside_agent_core import (
    OutsideAgentBlocker,
    OutsideAgentVerdictStatus,
    validate_outside_agent_submission,
)
from .outside_agent_pin import (
    EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN,
    OutsideAgentContractPin,
)

_VECTOR_MANIFEST_SCHEMA_VERSION = "outside_agent_vector_manifest.v0.1"


@dataclass(frozen=True)
class OutsideAgentVectorResult:
    vector_name: str
    status: OutsideAgentVerdictStatus
    expected_status: OutsideAgentVerdictStatus | None
    matched: bool
    blockers: tuple[OutsideAgentBlocker, ...]
    evidence_refs: tuple[str, ...]


def run_outside_agent_vectors(
    manifest: Mapping[str, Any] | str | Path | None = None,
    *,
    contract_pin: OutsideAgentContractPin = EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN,
) -> tuple[OutsideAgentVectorResult, ...]:
    manifest_data, manifest_digest = _load_manifest(manifest, contract_pin)
    manifest_blockers = _validate_manifest(manifest_data, manifest_digest)
    if manifest_blockers:
        return (
            OutsideAgentVectorResult(
                vector_name="__manifest__",
                status=OutsideAgentVerdictStatus.BLOCKED,
                expected_status=None,
                matched=False,
                blockers=manifest_blockers,
                evidence_refs=(contract_pin.vector_manifest_name,),
            ),
        )

    results: list[OutsideAgentVectorResult] = []
    for vector in manifest_data.get("vectors", []):
        expected_status = OutsideAgentVerdictStatus(vector["expected_status"])
        verdict = validate_outside_agent_submission(
            vector["submission"], contract_pin=contract_pin
        )
        expected_blockers = tuple(vector.get("expected_blocker_codes", ()))
        actual_codes = tuple(blocker.code for blocker in verdict.blockers)
        blocker_match = all(code in actual_codes for code in expected_blockers)
        matched = verdict.status == expected_status and blocker_match
        results.append(
            OutsideAgentVectorResult(
                vector_name=str(vector["name"]),
                status=verdict.status,
                expected_status=expected_status,
                matched=matched,
                blockers=verdict.blockers,
                evidence_refs=tuple(ref.ref for ref in verdict.evidence_refs),
            )
        )
    return tuple(results)


def _load_manifest(
    manifest: Mapping[str, Any] | str | Path | None,
    contract_pin: OutsideAgentContractPin,
) -> tuple[Mapping[str, Any], str]:
    if manifest is None:
        import consiliency_spec

        manifest_bytes = (
            resources.files(consiliency_spec)
            / f"_data/{contract_pin.vector_manifest_name}"
        ).read_bytes()
        return json.loads(manifest_bytes), hashlib.sha256(manifest_bytes).hexdigest()
    if isinstance(manifest, Mapping):
        manifest_bytes = json.dumps(
            manifest, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return manifest, hashlib.sha256(manifest_bytes).hexdigest()

    manifest_bytes = Path(manifest).read_bytes()
    return json.loads(manifest_bytes), hashlib.sha256(manifest_bytes).hexdigest()


def _validate_manifest(
    manifest: Mapping[str, Any],
    manifest_digest: str,
) -> tuple[OutsideAgentBlocker, ...]:
    blockers: list[OutsideAgentBlocker] = []
    if manifest.get("manifest_schema_version") != _VECTOR_MANIFEST_SCHEMA_VERSION:
        blockers.append(
            OutsideAgentBlocker(
                "unsupported_schema_version",
                "outside-agent vector manifest schema is not supported",
                ref="manifest_schema_version",
            )
        )
    expected_digest = manifest.get("manifest_digest")
    if expected_digest and str(expected_digest).removeprefix("sha256:").lower() != manifest_digest:
        blockers.append(
            OutsideAgentBlocker(
                "digest_mismatch",
                "outside-agent vector manifest digest drifted",
                ref="manifest_digest",
            )
        )

    vectors = manifest.get("vectors")
    if not isinstance(vectors, list):
        blockers.append(
            OutsideAgentBlocker(
                "schema_validation_failed",
                "outside-agent vector manifest must contain vectors",
                ref="vectors",
            )
        )
        return tuple(blockers)

    for index, vector in enumerate(vectors):
        if not isinstance(vector, Mapping):
            blockers.append(
                OutsideAgentBlocker(
                    "schema_validation_failed",
                    "outside-agent vector must be metadata",
                    ref=f"vectors.{index}",
                )
            )
            continue
        if "expected_status" not in vector:
            blockers.append(
                OutsideAgentBlocker(
                    "schema_validation_failed",
                    "outside-agent vector is missing expected outcome",
                    ref=f"vectors.{index}.expected_status",
                )
            )
        elif vector["expected_status"] not in {
            OutsideAgentVerdictStatus.PASS.value,
            OutsideAgentVerdictStatus.BLOCKED.value,
        }:
            blockers.append(
                OutsideAgentBlocker(
                    "schema_validation_failed",
                    "outside-agent vector expected outcome is unsupported",
                    ref=f"vectors.{index}.expected_status",
                )
            )
        for field in ("name", "submission"):
            if field not in vector:
                blockers.append(
                    OutsideAgentBlocker(
                        "schema_validation_failed",
                        "outside-agent vector is incomplete",
                        ref=f"vectors.{index}.{field}",
                    )
                )
    return tuple(blockers)


__all__ = ["OutsideAgentVectorResult", "run_outside_agent_vectors"]
