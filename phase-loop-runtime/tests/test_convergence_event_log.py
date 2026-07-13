from pathlib import Path

import pytest

from phase_loop_runtime.convergence.event_log import (
    default_convergence_event_log_path, read_convergence_events, record_intent,
    record_outcome, recover_train_state,
)
from phase_loop_runtime.train_ledger import CoordinatorEvent, CoordinatorEventKind


def _event(kind, **overrides):
    value = dict(kind=kind, train_id="train", node_id="node", roadmap_path="plan.md", roadmap_digest="d", workspace_id="w", branch="b", base_ref="main", base_sha="base", head_sha="head", phase="RUNTIME", action="execute", attempt_id="a", epoch=1)
    value.update(overrides)
    return CoordinatorEvent(**value)


def test_durable_intent_then_outcome_and_recovery(tmp_path: Path):
    path = default_convergence_event_log_path(tmp_path, "train")
    intent = _event(CoordinatorEventKind.INTENT)
    outcome = _event(CoordinatorEventKind.OUTCOME, verification_digest="digest", seat_outcomes=("seat",))
    record_intent(path, intent)
    record_outcome(path, outcome)
    record_outcome(path, outcome)
    assert read_convergence_events(path) == (intent, outcome)
    assert not recover_train_state(read_convergence_events(path)).pending_attempts


def test_rejects_phase_loop_and_outcome_without_intent(tmp_path: Path):
    with pytest.raises(ValueError):
        default_convergence_event_log_path(tmp_path / ".phase-loop", "train")
    with pytest.raises(ValueError):
        record_outcome(tmp_path / "events.jsonl", _event(CoordinatorEventKind.OUTCOME))


def test_tolerates_only_malformed_final_record(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    record_intent(path, _event(CoordinatorEventKind.INTENT))
    path.write_text(path.read_text() + "{", encoding="utf-8")
    assert len(read_convergence_events(path)) == 1
    path.write_text("{\n" + path.read_text(), encoding="utf-8")
    with pytest.raises(ValueError):
        read_convergence_events(path)
