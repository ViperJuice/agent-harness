from phase_loop_runtime.conformance.outside_agent_core import OutsideAgentSubmissionKind
from phase_loop_runtime.conformance.outside_agent_schema import (
    validate_outside_agent_submission_schema,
)


def _submission(kind="work_request"):
    return {
        "submission_schema_version": "outside_agent_submission.v0.1",
        "submission_kind": kind,
        "metadata": {"submission_id": "oa-1", "content_digest": "a" * 64},
        "provenance_refs": [{"ref": "requests/oa-1.json", "digest": "b" * 64}],
    }


def _codes(result):
    return {blocker.code for blocker in result.blockers}


def test_accepts_supported_submission_kinds():
    expected = {
        "work_request": OutsideAgentSubmissionKind.WORK_REQUEST,
        "implementation_submission": OutsideAgentSubmissionKind.IMPLEMENTATION_SUBMISSION,
        "ambiguity_report": OutsideAgentSubmissionKind.AMBIGUITY_REPORT,
    }

    for kind, parsed in expected.items():
        result = validate_outside_agent_submission_schema(_submission(kind))
        assert result.submission_kind == parsed
        assert result.blockers == ()


def test_unsupported_schema_version_fails_closed():
    submission = _submission()
    submission["submission_schema_version"] = "outside_agent_submission.v9"

    assert "unsupported_schema_version" in _codes(
        validate_outside_agent_submission_schema(submission)
    )


def test_unsupported_submission_kind_fails_closed():
    submission = _submission("freeform_patch")

    assert "unsupported_submission_kind" in _codes(
        validate_outside_agent_submission_schema(submission)
    )


def test_unknown_top_level_field_fails_closed():
    submission = _submission()
    submission["raw_result"] = {"anything": True}

    assert "unknown_field" in _codes(
        validate_outside_agent_submission_schema(submission)
    )


def test_missing_required_metadata_fails_closed():
    submission = _submission()
    submission["metadata"] = {"submission_id": "oa-1"}

    assert "schema_validation_failed" in _codes(
        validate_outside_agent_submission_schema(submission)
    )
