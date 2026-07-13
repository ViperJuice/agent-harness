"""Transcript-free recovery status projection."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .event_log import RecoveredTrainState


@dataclass(frozen=True)
class TrainStatusSnapshot:
    train_id: str
    event_log_path: str
    last_event_offset: int
    pending_attempt_ids: tuple[str, ...]
    node_states: tuple[tuple[str, str], ...]
    verification_valid: bool
    approval_valid: bool
    ambiguities: tuple[str, ...]
    next_action: str


def build_train_status(state: RecoveredTrainState, event_log_path: Path | str = "") -> TrainStatusSnapshot:
    pending = tuple(event.attempt_id or event.node_id for event in state.pending_attempts)
    next_action = "reconcile exact authority" if not state.ambiguities and not pending else "resolve ambiguous or pending convergence state"
    return TrainStatusSnapshot(state.train_id, str(event_log_path), state.last_event_offset, pending, tuple(sorted((key, event.kind.value) for key, event in state.node_states.items())), state.verification_valid, state.approval_valid, state.ambiguities, next_action)


def render_train_status(snapshot: TrainStatusSnapshot, *, as_json: bool = False) -> str:
    if as_json:
        return json.dumps(asdict(snapshot), sort_keys=True)
    return "\n".join((f"train-status: {snapshot.train_id}", f"event-log: {snapshot.event_log_path}", f"last-offset: {snapshot.last_event_offset}", f"pending-attempts: {', '.join(snapshot.pending_attempt_ids) or 'none'}", f"next-action: {snapshot.next_action}"))
