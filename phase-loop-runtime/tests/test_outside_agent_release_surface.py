import importlib.metadata
import re
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib

import phase_loop_runtime
from phase_loop_runtime.conformance import (
    EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN,
    build_outside_agent_advisory_evidence,
    build_outside_agent_validation_verdict,
    serialize_outside_agent_advisory_evidence,
    serialize_outside_agent_validation_verdict,
)


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = RUNTIME_ROOT.parent


def _require_repo_files(*paths: Path):
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        pytest.skip(
            "repo-root release docs/workflows are absent in standalone clean-room: "
            + ", ".join(missing)
        )


def _submission():
    return {
        "submission_schema_version": "outside_agent_submission.v0.1",
        "submission_kind": "work_request",
        "metadata": {
            "submission_id": "oa-release-1",
            "content_digest": "a" * 64,
        },
        "provenance_refs": [
            {"ref": "requests/oa-release-1.json", "digest": "b" * 64},
        ],
        "evidence_refs": [
            {"ref": "evidence/oa-release-1.json", "digest": "c" * 64},
        ],
    }


def test_package_version_matches_runtime_version():
    pyproject_path = RUNTIME_ROOT / "pyproject.toml"
    if not pyproject_path.exists():
        assert importlib.metadata.version("phase-loop-runtime") == phase_loop_runtime.__version__
        return

    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

    assert pyproject["project"]["name"] == "phase-loop-runtime"
    assert pyproject["project"]["version"] == phase_loop_runtime.__version__


def test_outside_agent_public_release_surface_exports_validator_and_advisory_entrypoints():
    from phase_loop_runtime import conformance

    expected_exports = {
        "EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN",
        "OutsideAgentContractPin",
        "OutsideAgentAdvisoryEvidence",
        "OutsideAgentAdvisoryExitCode",
        "build_outside_agent_advisory_evidence",
        "serialize_outside_agent_advisory_evidence",
        "OutsideAgentValidationExitCode",
        "OutsideAgentValidationVerdict",
        "build_outside_agent_validation_verdict",
        "serialize_outside_agent_validation_verdict",
    }

    assert expected_exports <= set(conformance.__all__)
    for name in expected_exports:
        assert hasattr(conformance, name)


def test_real_validator_and_advisory_outputs_share_pinned_metadata_only_contract_evidence():
    validation_payload = serialize_outside_agent_validation_verdict(
        build_outside_agent_validation_verdict(_submission())
    )
    advisory_payload = serialize_outside_agent_advisory_evidence(
        build_outside_agent_advisory_evidence(_submission())
    )

    assert validation_payload["validator_version"] == phase_loop_runtime.__version__
    assert validation_payload["authority"] == "governed_pipeline_validator"
    assert advisory_payload["authority"] == "advisory"
    assert advisory_payload["redaction_posture"] == "metadata_only"
    assert advisory_payload["contract_pin"] == validation_payload["contract_pin"]
    assert validation_payload["vector_manifest_hash"] == EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN.vector_manifest_hash
    assert "accepted_for_merge" not in validation_payload
    assert "merge_verdict" not in validation_payload
    assert "accepted_for_merge" not in advisory_payload
    assert "merge_verdict" not in advisory_payload


def test_expected_outside_agent_contract_pin_release_fields_are_complete():
    pin = EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN

    assert pin.contract_package == "consiliency-spec"
    assert pin.contract_version
    assert re.fullmatch(r"[0-9a-f]{40}", pin.contract_git_sha)
    assert pin.schema_version == "outside_agent_submission.v0.1"
    assert pin.verdict_schema_version == "outside_agent_route_verdict.v0.1"
    assert pin.vector_manifest_name == "test-vectors/outside-agent/manifest.json"
    assert re.fullmatch(r"[0-9a-f]{64}", pin.vector_manifest_hash)
    assert pin.source_owner == "Consiliency/spec"
    assert pin.redaction_posture == "metadata_only"


def test_release_workflows_keep_version_build_and_publish_boundaries_explicit():
    consistency_path = REPO_ROOT / ".github/workflows/release-consistency.yml"
    publish_path = REPO_ROOT / ".github/workflows/publish-pypi.yml"
    _require_repo_files(consistency_path, publish_path)

    consistency = consistency_path.read_text(encoding="utf-8")
    publish = publish_path.read_text(encoding="utf-8")

    assert "push:" in consistency
    assert "tags: ['v*']" in consistency
    assert "pyproject version == __init__ __version__" in consistency
    assert "github.ref_type == 'tag'" in consistency
    assert "Trusted" in publish
    assert "Publishing (OIDC)" in publish
    assert "workflow_dispatch" in publish
    assert "Verify tag matches phase-loop-runtime version" in publish
    assert "python -m build --sdist --wheel --outdir dist phase-loop-runtime" in publish
    assert "pypa/gh-action-pypi-publish" in publish
    assert "id-token: write" in publish
    assert "PYPI_API_TOKEN" not in publish
    assert "secrets." not in publish


def test_release_handoff_records_metadata_only_package_contract_and_dispatch_boundary():
    handoff_path = REPO_ROOT / "docs/releases/outside-agent-release-handoff.md"
    _require_repo_files(handoff_path)

    handoff = handoff_path.read_text(encoding="utf-8")
    pin = EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN

    required_terms = {
        "phase-loop-runtime",
        phase_loop_runtime.__version__,
        "validator version",
        "governed_pipeline_validator",
        "outside-agent-preflight",
        "outside-agent-validate",
        pin.contract_package,
        pin.contract_version,
        pin.contract_git_sha,
        pin.schema_version,
        pin.verdict_schema_version,
        pin.vector_manifest_name,
        pin.vector_manifest_hash,
        pin.source_owner,
        pin.redaction_posture,
        f"phase_loop_runtime-{phase_loop_runtime.__version__}-py3-none-any.whl",
        f"phase_loop_runtime-{phase_loop_runtime.__version__}.tar.gz",
        "maintainer",
        "not published",
        "not dispatched",
    }

    lowered = handoff.lower()
    for term in required_terms:
        assert term.lower() in lowered

    forbidden_terms = {
        "accepted_for_merge",
        "merge_verdict",
        "provider payload",
        "local env",
        "tbd",
        "todo",
        "/home/",
        "/mnt/",
    }
    for term in forbidden_terms:
        assert term not in lowered


def test_public_docs_point_to_handoff_without_claiming_release_dispatch():
    readme_path = REPO_ROOT / "README.md"
    changelog_path = REPO_ROOT / "CHANGELOG.md"
    _require_repo_files(readme_path, changelog_path)

    readme = readme_path.read_text(encoding="utf-8").lower()
    changelog = changelog_path.read_text(encoding="utf-8").lower()

    assert "docs/releases/outside-agent-release-handoff.md" in readme
    assert "docs/outside-agent-conformance.md" in readme
    assert "outside-agent-preflight" in readme
    assert "outside-agent-validate" in readme
    assert "governed-pipeline" in readme
    assert "outside-agent conformance runtime (oarelease)" in changelog
    assert "release handoff" in changelog
    assert "governed-pipeline pinning instructions" in changelog
    assert "maintainer-owned publish/tag/workflow-dispatch" in changelog
    assert "0.5.0" in changelog
