import json
import subprocess
import sys

from phase_loop_runtime.conformance.outside_agent_advisory import (
    build_outside_agent_advisory_evidence,
    serialize_outside_agent_advisory_evidence,
)


_FORBIDDEN_AUTHORITY_FIELDS = (
    "accepted_for_merge",
    "merge_verdict",
    "authoritative",
    "acceptance_status",
)


def _submission():
    return {
        "submission_schema_version": "outside_agent_submission.v0.1",
        "submission_kind": "work_request",
        "metadata": {
            "submission_id": "oa-boundary",
            "content_digest": "a" * 64,
        },
        "provenance_refs": [
            {"ref": "requests/oa-boundary.json", "digest": "b" * 64},
        ],
        "evidence_refs": [
            {"ref": "evidence/oa-boundary.json", "digest": "c" * 64},
        ],
    }


def test_sdk_serialization_never_claims_merge_authority():
    payload = serialize_outside_agent_advisory_evidence(
        build_outside_agent_advisory_evidence(_submission())
    )
    flat = json.dumps(payload, sort_keys=True)

    assert payload["authority"] == "advisory"
    for field in _FORBIDDEN_AUTHORITY_FIELDS:
        assert field not in payload
        assert field not in flat


def test_cli_stdout_never_claims_merge_authority(tmp_path):
    submission_path = tmp_path / "submission.json"
    submission_path.write_text(json.dumps(_submission()), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "phase_loop_runtime.cli", "outside-agent-preflight", str(submission_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout)["authority"] == "advisory"
    for field in _FORBIDDEN_AUTHORITY_FIELDS:
        assert field not in result.stdout


def test_cli_output_file_never_claims_merge_authority(tmp_path):
    submission_path = tmp_path / "submission.json"
    output_path = tmp_path / "advisory.json"
    submission_path.write_text(json.dumps(_submission()), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "phase_loop_runtime.cli",
            "outside-agent-preflight",
            str(submission_path),
            "--output",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    output = output_path.read_text(encoding="utf-8")

    assert result.returncode == 0
    assert json.loads(output)["authority"] == "advisory"
    for field in _FORBIDDEN_AUTHORITY_FIELDS:
        assert field not in output
