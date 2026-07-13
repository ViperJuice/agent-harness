import json

from phase_loop_runtime.panel_invoker import SeatOutcomeRecord, persist_seat_outcome


def test_seat_outcome_is_metadata_only_and_persisted():
    values = []
    record = SeatOutcomeRecord("codex:1", "codex", True, "OK", "a", 1, "artifact", "now", "evidence")
    persist_seat_outcome(record, values.append)
    assert json.loads(values[0])["seat_key"] == "codex:1"
    assert "text" not in values[0]
