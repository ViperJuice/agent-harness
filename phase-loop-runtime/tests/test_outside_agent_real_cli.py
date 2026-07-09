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


def _run_validate(path, *args):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "phase_loop_runtime.cli",
            "outside-agent-validate",
            str(path),
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_clean_pass_writes_file_and_stdout(tmp_path):
    submission_path = tmp_path / "submission.json"
    output_path = tmp_path / "verdict.json"
    submission_path.write_text(json.dumps(_submission()), encoding="utf-8")

    result = _run_validate(
        submission_path,
        "--output",
        str(output_path),
        "--submitted-ref",
        "src/agent.py",
    )
    payload = json.loads(result.stdout)

    assert result.returncode == 0
    assert json.loads(output_path.read_text(encoding="utf-8")) == payload
    assert payload["gate_id"] == "real_conformance_gate.v0.1"
    assert payload["authority"] == "governed_pipeline_validator"
    assert payload["command"] == "outside-agent-validate"
    assert payload["submitted_refs"] == ["src/agent.py"]
    assert payload["vectors_executed"] is False
    assert "accepted_for_merge" not in payload
    assert "merge_verdict" not in payload
    assert result.stderr == ""


def test_cli_malformed_json_returns_exit_2_and_writes_output(tmp_path):
    submission_path = tmp_path / "submission.json"
    output_path = tmp_path / "verdict.json"
    submission_path.write_text("{not json", encoding="utf-8")

    result = _run_validate(submission_path, "--output", str(output_path))
    payload = json.loads(result.stdout)

    assert result.returncode == 2
    assert payload["blockers"][0]["code"] == "malformed_input"
    assert json.loads(output_path.read_text(encoding="utf-8")) == payload


def test_cli_redaction_violation_returns_exit_3(tmp_path):
    submission_path = tmp_path / "submission.json"
    output_path = tmp_path / "verdict.json"
    submission_path.write_text(
        json.dumps(_submission(provider_response_body="digest-only-marker")),
        encoding="utf-8",
    )

    result = _run_validate(submission_path, "--output", str(output_path))
    payload = json.loads(result.stdout)

    assert result.returncode == 3
    assert "raw_payload_present" in {blocker["code"] for blocker in payload["blockers"]}


def test_cli_provenance_failure_returns_exit_4(tmp_path):
    submission_path = tmp_path / "submission.json"
    output_path = tmp_path / "verdict.json"
    submission_path.write_text(
        json.dumps(_submission(provenance_refs=[{"ref": "/tmp/unsafe.json", "digest": "b" * 64}])),
        encoding="utf-8",
    )

    result = _run_validate(submission_path, "--output", str(output_path))
    payload = json.loads(result.stdout)

    assert result.returncode == 4
    assert "absolute_path_ref" in {blocker["code"] for blocker in payload["blockers"]}


def test_cli_requires_output(tmp_path):
    submission_path = tmp_path / "submission.json"
    submission_path.write_text(json.dumps(_submission()), encoding="utf-8")

    result = _run_validate(submission_path)

    assert result.returncode == 2
    assert "required" in result.stderr
    assert result.stdout == ""


def test_cli_contract_pin_failure_returns_exit_5(tmp_path):
    submission_path = tmp_path / "submission.json"
    output_path = tmp_path / "verdict.json"
    submission_path.write_text(
        json.dumps(_submission(submission_schema_version="outside_agent_submission.v9")),
        encoding="utf-8",
    )

    result = _run_validate(submission_path, "--output", str(output_path))

    assert result.returncode == 5


def test_cli_other_conformance_blockers_return_exit_6(tmp_path):
    submission_path = tmp_path / "submission.json"
    output_path = tmp_path / "verdict.json"
    submission_path.write_text(
        json.dumps(_submission(submission_kind="not_supported")),
        encoding="utf-8",
    )

    result = _run_validate(submission_path, "--output", str(output_path))

    assert result.returncode == 6
