from __future__ import annotations

import json

from phase_loop_runtime.events import read_events
from phase_loop_runtime.pipeline_adapter.merge_policy import MergePolicy
from phase_loop_runtime.pipeline_adapter.ratification import emit_ratification_passed
from phase_loop_test_utils import make_repo


def test_ratification_event_and_trigger_payload_match(tmp_path):
    repo = make_repo(tmp_path)
    (repo / ".pipeline").mkdir()
    policy = MergePolicy(on_pass="required", approvers=("ops",))
    audit = {"terminal_status": "complete", "verification_status": "passed", "produced_if_gates": ["IF-0-EVENT-1"]}

    emit_ratification_passed(repo, "v32", "RE", "complete", policy, audit)

    events = [event for event in read_events(repo) if event.get("event_type") == "ratification.passed"]
    assert len(events) == 1
    trigger_payload = json.loads((repo / ".pipeline" / "ratification-trigger.json").read_text(encoding="utf-8"))
    event_payload = events[0]["payload"]

    assert event_payload == trigger_payload
    assert event_payload["roadmap_version"] == "v32"
    assert event_payload["phase_alias"] == "RE"
    assert event_payload["ratification_gate"] == "complete"
    assert event_payload["merge_policy"] == {"on_pass": "required", "approvers": ["ops"]}
    assert event_payload["audit"] == audit
    assert event_payload["pipeline_branch"]
    assert event_payload["default_branch"] == "main"
    assert event_payload["head_sha"]
    assert event_payload["merge_pr_title"] == "v32 phase RE ratification: complete"
