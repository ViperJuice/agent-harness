import json
import subprocess
import sys
from pathlib import Path

from phase_loop_runtime.conformance.outside_agent_vectors import run_outside_agent_vectors


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "outside_agent_real"


def _run_validate(name, tmp_path):
    submission_path = FIXTURE_ROOT / f"{name}.json"
    output_path = tmp_path / f"{name}-verdict.json"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "phase_loop_runtime.cli",
            "outside-agent-validate",
            submission_path,
            "--output",
            str(output_path),
            "--submitted-ref",
            "src/agent.py",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_fixture_invocations_do_not_run_vectors_for_live_validation(tmp_path):
    expected = {
        "clean_submission": 0,
        "malformed_submission": 2,
        "provenance_submission": 4,
        "redaction_submission": 3,
    }

    for name, exit_code in expected.items():
        result = _run_validate(name, tmp_path)
        payload = json.loads(result.stdout)

        assert result.returncode == exit_code
        assert payload["vectors_executed"] is False
        assert payload["authority"] == "governed_pipeline_validator"


def test_ci_release_vector_evidence_runs_pinned_vector_runner():
    manifest = {
        "manifest_schema_version": "outside_agent_vector_manifest.v0.1",
        "vectors": [
            {
                "name": "valid-work-request",
                "submission": json.loads(
                    Path(f"{FIXTURE_ROOT}/clean_submission.json").read_text(encoding="utf-8")
                ),
                "expected_status": "pass",
            },
            {
                "name": "unknown-field",
                "submission": json.loads(
                    Path(f"{FIXTURE_ROOT}/redaction_submission.json").read_text(encoding="utf-8")
                ),
                "expected_status": "blocked",
                "expected_blocker_codes": ["raw_payload_present"],
            },
        ],
    }

    results = run_outside_agent_vectors(manifest)

    assert [result.vector_name for result in results] == [
        "valid-work-request",
        "unknown-field",
    ]
    assert all(result.matched for result in results)
