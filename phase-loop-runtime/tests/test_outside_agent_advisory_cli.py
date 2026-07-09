import json
import subprocess
import sys


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


def _run_preflight(path, *args):
    return subprocess.run(
        [sys.executable, "-m", "phase_loop_runtime.cli", "outside-agent-preflight", str(path), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_clean_pass_outputs_advisory_json(tmp_path):
    submission_path = tmp_path / "submission.json"
    submission_path.write_text(json.dumps(_submission()), encoding="utf-8")

    result = _run_preflight(submission_path)
    payload = json.loads(result.stdout)

    assert result.returncode == 0
    assert payload["authority"] == "advisory"
    assert payload["classification"] == "clean_advisory_pass"
    assert payload["status"] == "pass"
    assert payload["redaction_posture"] == "metadata_only"
    assert "accepted_for_merge" not in payload
    assert "merge_verdict" not in payload
    assert result.stderr == ""


def test_cli_malformed_json_returns_exit_2(tmp_path):
    submission_path = tmp_path / "submission.json"
    submission_path.write_text("{not json", encoding="utf-8")

    result = _run_preflight(submission_path)
    payload = json.loads(result.stdout)

    assert result.returncode == 2
    assert payload["classification"] == "malformed_input"
    assert payload["blockers"][0]["code"] == "malformed_input"


def test_cli_redaction_violation_returns_exit_3(tmp_path):
    submission_path = tmp_path / "submission.json"
    submission_path.write_text(json.dumps(_submission(provider_response_body="digest-only-marker")), encoding="utf-8")

    result = _run_preflight(submission_path)
    payload = json.loads(result.stdout)

    assert result.returncode == 3
    assert payload["classification"] == "redaction_violation"
    assert "raw_payload_present" in {blocker["code"] for blocker in payload["blockers"]}


def test_cli_provenance_failure_returns_exit_4(tmp_path):
    submission_path = tmp_path / "submission.json"
    submission_path.write_text(
        json.dumps(_submission(provenance_refs=[{"ref": "/tmp/unsafe.json", "digest": "b" * 64}])),
        encoding="utf-8",
    )

    result = _run_preflight(submission_path)
    payload = json.loads(result.stdout)

    assert result.returncode == 4
    assert payload["classification"] == "provenance_failure"
    assert "absolute_path_ref" in {blocker["code"] for blocker in payload["blockers"]}


def test_cli_writes_output_file_with_stdout_payload(tmp_path):
    submission_path = tmp_path / "submission.json"
    output_path = tmp_path / "advisory.json"
    submission_path.write_text(json.dumps(_submission()), encoding="utf-8")

    result = _run_preflight(submission_path, "--output", str(output_path))

    assert result.returncode == 0
    assert json.loads(output_path.read_text(encoding="utf-8")) == json.loads(result.stdout)
