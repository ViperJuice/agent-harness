import json

from phase_loop_runtime.convergence.event_log import RecoveredTrainState
from phase_loop_runtime.convergence.status import build_train_status, render_train_status


def test_status_projection_is_stable_and_transcript_free():
    snapshot = build_train_status(RecoveredTrainState("t", last_event_offset=2), "events.jsonl")
    assert json.loads(render_train_status(snapshot, as_json=True))["event_log_path"] == "events.jsonl"
    assert "transcript" not in render_train_status(snapshot).lower()
