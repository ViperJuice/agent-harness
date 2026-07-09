"""Import checks for the pinned Consiliency/spec outside-agent contract."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from importlib import metadata, resources
from pathlib import Path
from typing import Any

from .outside_agent_pin import (
    EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN,
    OutsideAgentContractPin,
)


class OutsideAgentContractError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def load_outside_agent_contract_pin(
    spec_root: str | os.PathLike[str] | None = None,
    *,
    expected_pin: OutsideAgentContractPin = EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN,
) -> OutsideAgentContractPin:
    """Return the frozen pin only after the source contract matches it.

    ``spec_root`` (or ``OUTSIDE_AGENT_SPEC_ROOT``) is the pre-release train path:
    it validates an immutable Consiliency/spec checkout by git SHA plus manifest
    hash. Without a source root, the helper validates an installed
    ``consiliency_spec`` package. Both paths fail closed on missing or drifted
    contract identity.
    """
    source_root = spec_root or os.environ.get("OUTSIDE_AGENT_SPEC_ROOT")
    if source_root:
        _validate_spec_root(Path(source_root), expected_pin)
        return expected_pin

    _validate_installed_package(expected_pin)
    return expected_pin


def _validate_spec_root(root: Path, expected_pin: OutsideAgentContractPin) -> None:
    if not root.exists():
        raise OutsideAgentContractError("missing_contract", f"spec root not found: {root}")

    git_sha = _git_head(root)
    if git_sha and git_sha != expected_pin.contract_git_sha:
        raise OutsideAgentContractError(
            "unknown_contract_version",
            f"expected {expected_pin.contract_git_sha}, found {git_sha}",
        )

    _validate_contract_files(
        _read_json(root / "schemas" / "outside-agent-submission.schema.json"),
        _read_json(root / "schemas" / "outside-agent-route-verdict.schema.json"),
        _read_bytes(root / expected_pin.vector_manifest_name),
        expected_pin,
    )


def _validate_installed_package(expected_pin: OutsideAgentContractPin) -> None:
    try:
        import consiliency_spec
    except ModuleNotFoundError as exc:
        raise OutsideAgentContractError(
            "missing_contract", "consiliency_spec package is not installed"
        ) from exc

    try:
        version = metadata.version(expected_pin.contract_package)
    except metadata.PackageNotFoundError as exc:
        raise OutsideAgentContractError(
            "missing_contract", f"{expected_pin.contract_package} distribution is not installed"
        ) from exc

    if version != expected_pin.contract_version:
        raise OutsideAgentContractError(
            "unknown_contract_version",
            f"expected {expected_pin.contract_version}, found {version}",
        )

    package_root = resources.files(consiliency_spec)
    _validate_contract_files(
        json.loads((package_root / "_data/schemas/outside-agent-submission.schema.json").read_text()),
        json.loads((package_root / "_data/schemas/outside-agent-route-verdict.schema.json").read_text()),
        (package_root / f"_data/{expected_pin.vector_manifest_name}").read_bytes(),
        expected_pin,
    )


def _validate_contract_files(
    submission_schema: dict[str, Any],
    verdict_schema: dict[str, Any],
    vector_manifest_bytes: bytes,
    expected_pin: OutsideAgentContractPin,
) -> None:
    schema_version = (
        submission_schema.get("properties", {})
        .get("submission_schema_version", {})
        .get("const")
    )
    if schema_version != expected_pin.schema_version:
        raise OutsideAgentContractError(
            "unknown_contract_version",
            f"expected schema {expected_pin.schema_version}, found {schema_version!r}",
        )

    verdict_schema_version = (
        verdict_schema.get("properties", {})
        .get("verdict_schema_version", {})
        .get("const")
    )
    if verdict_schema_version != expected_pin.verdict_schema_version:
        raise OutsideAgentContractError(
            "unknown_contract_version",
            f"expected verdict schema {expected_pin.verdict_schema_version}, found {verdict_schema_version!r}",
        )

    manifest_hash = hashlib.sha256(vector_manifest_bytes).hexdigest()
    if manifest_hash != expected_pin.vector_manifest_hash:
        raise OutsideAgentContractError(
            "vector_manifest_hash_mismatch",
            f"expected {expected_pin.vector_manifest_hash}, found {manifest_hash}",
        )


def _git_head(root: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise OutsideAgentContractError("missing_contract", f"missing {path}") from exc


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise OutsideAgentContractError("missing_contract", f"missing {path}") from exc


__all__ = ["OutsideAgentContractError", "load_outside_agent_contract_pin"]
