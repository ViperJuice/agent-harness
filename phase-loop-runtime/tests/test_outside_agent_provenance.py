import hashlib

import pytest

from phase_loop_runtime.conformance.outside_agent_provenance import (
    normalize_outside_agent_ref,
    validate_outside_agent_provenance,
)


def _submission(ref="requests/oa-1.json", digest="a" * 64):
    return {
        "provenance_refs": [{"ref": ref, "digest": digest}],
        "evidence_refs": [{"ref": "evidence/oa-1.json", "digest": "b" * 64}],
    }


def _codes(result):
    return {blocker.code for blocker in result.blockers}


def test_accepts_repo_relative_refs_and_digests():
    result = validate_outside_agent_provenance(_submission())

    assert result.blockers == ()
    assert result.provenance_refs == ("requests/oa-1.json",)
    assert [ref.ref for ref in result.evidence_refs] == [
        "requests/oa-1.json",
        "evidence/oa-1.json",
    ]


def test_normalize_outside_agent_ref_rejects_unsafe_refs():
    assert normalize_outside_agent_ref("a/b.json") == "a/b.json"
    for ref in ("/tmp/a.json", "../a.json", "", "https://example.test/a.json"):
        with pytest.raises(ValueError):
            normalize_outside_agent_ref(ref)


def test_absolute_path_traversal_empty_and_missing_digest_fail_closed():
    assert "absolute_path_ref" in _codes(
        validate_outside_agent_provenance(_submission("/tmp/a.json"))
    )
    assert "path_traversal_ref" in _codes(
        validate_outside_agent_provenance(_submission("../a.json"))
    )
    assert "unsafe_source_ref" in _codes(
        validate_outside_agent_provenance(_submission(""))
    )
    assert "missing_digest" in _codes(
        validate_outside_agent_provenance(_submission(digest=None))
    )


def test_missing_digest_fails_closed():
    assert "missing_digest" in _codes(validate_outside_agent_provenance(_submission(digest=None)))


def test_digest_mismatch_fails_closed_without_reading_local_files():
    result = validate_outside_agent_provenance(
        {
            "provenance_refs": [
                {
                    "ref": "requests/oa-1.json",
                    "digest": "0" * 64,
                    "content": "metadata fixture",
                }
            ]
        }
    )

    assert "digest_mismatch" in _codes(result)
    assert hashlib.sha256(b"metadata fixture").hexdigest() != "0" * 64
