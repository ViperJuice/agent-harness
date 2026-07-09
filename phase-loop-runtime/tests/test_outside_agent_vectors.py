import copy

from phase_loop_runtime.conformance.outside_agent_core import OutsideAgentVerdictStatus
from phase_loop_runtime.conformance.outside_agent_vectors import run_outside_agent_vectors


def _submission(**extra):
    value = {
        "submission_schema_version": "outside_agent_submission.v0.1",
        "submission_kind": "work_request",
        "metadata": {"submission_id": "oa-1", "content_digest": "a" * 64},
        "provenance_refs": [{"ref": "requests/oa-1.json", "digest": "b" * 64}],
    }
    value.update(extra)
    return value


def _manifest():
    return {
        "manifest_schema_version": "outside_agent_vector_manifest.v0.1",
        "vectors": [
            {
                "name": "valid-work-request",
                "submission": _submission(),
                "expected_status": "pass",
            },
            {
                "name": "unknown-field",
                "submission": _submission(extra_field=True),
                "expected_status": "blocked",
                "expected_blocker_codes": ["unknown_field"],
            },
        ],
    }


def test_vector_runner_matches_positive_and_negative_expected_outcomes():
    results = run_outside_agent_vectors(_manifest())

    assert [result.vector_name for result in results] == [
        "valid-work-request",
        "unknown-field",
    ]
    assert all(result.matched for result in results)
    assert results[0].status == OutsideAgentVerdictStatus.PASS
    assert results[1].status == OutsideAgentVerdictStatus.BLOCKED


def test_unknown_vector_schema_version_fails_closed():
    manifest = _manifest()
    manifest["manifest_schema_version"] = "outside_agent_vector_manifest.v9"

    result = run_outside_agent_vectors(manifest)[0]

    assert result.vector_name == "__manifest__"
    assert result.status == OutsideAgentVerdictStatus.BLOCKED
    assert any(blocker.code == "unsupported_schema_version" for blocker in result.blockers)


def test_missing_expected_outcome_fails_closed():
    manifest = _manifest()
    del manifest["vectors"][0]["expected_status"]

    result = run_outside_agent_vectors(manifest)[0]

    assert result.vector_name == "__manifest__"
    assert any(blocker.code == "schema_validation_failed" for blocker in result.blockers)


def test_manifest_digest_drift_fails_closed():
    manifest = copy.deepcopy(_manifest())
    manifest["manifest_digest"] = "0" * 64

    result = run_outside_agent_vectors(manifest)[0]

    assert result.vector_name == "__manifest__"
    assert any(blocker.code == "digest_mismatch" for blocker in result.blockers)
