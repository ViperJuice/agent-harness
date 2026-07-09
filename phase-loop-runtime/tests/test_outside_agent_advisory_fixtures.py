import json
from pathlib import Path

from phase_loop_runtime.conformance.outside_agent_advisory import (
    build_outside_agent_advisory_evidence,
    serialize_outside_agent_advisory_evidence,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "outside_agent_advisory"


def test_advisory_fixture_outputs_match_stable_summaries():
    expected = json.loads((FIXTURE_DIR / "expected_summary.json").read_text(encoding="utf-8"))

    for path in sorted(FIXTURE_DIR.glob("*_submission.json")):
        submission = json.loads(path.read_text(encoding="utf-8"))
        payload = serialize_outside_agent_advisory_evidence(
            build_outside_agent_advisory_evidence(submission)
        )

        assert {
            "authority": payload["authority"],
            "classification": payload["classification"],
            "exit_code": payload["exit_code"],
            "status": payload["status"],
            "blocker_codes": sorted({blocker["code"] for blocker in payload["blockers"]}),
            "provenance_refs": payload["provenance_refs"],
        } == expected[path.name]
        assert payload["redaction_posture"] == "metadata_only"
        assert payload["contract_pin"]["schema_version"] == "outside_agent_submission.v0.1"
        assert payload["contract_pin"]["redaction_posture"] == "metadata_only"
        assert len(payload["input_digest"]) == 64
        assert "accepted_for_merge" not in payload
        assert "merge_verdict" not in payload
