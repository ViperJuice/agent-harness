"""Coordinator-owned, metadata-only convergence event log."""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from phase_loop_runtime.train_ledger import CoordinatorEvent, CoordinatorEventKind

_MAX_RECORD_BYTES = 64 * 1024
_LOCK = threading.Lock()


@dataclass(frozen=True)
class RecoveredTrainState:
    train_id: str
    node_states: dict[str, CoordinatorEvent] = field(default_factory=dict)
    pending_attempts: tuple[CoordinatorEvent, ...] = ()
    latest_epoch: int | None = None
    verification_valid: bool = False
    approval_valid: bool = False
    ambiguities: tuple[str, ...] = ()
    last_event_offset: int = -1


def default_convergence_event_log_path(coordinator_root: Path, train_id: str) -> Path:
    path = coordinator_root / "convergence" / f"train-{train_id}.events.jsonl"
    if ".phase-loop" in path.parts:
        raise ValueError("convergence event logs cannot be stored under .phase-loop")
    return path


def _key(event: CoordinatorEvent) -> tuple[str, str, str | None, int | None]:
    return (event.train_id, event.node_id, event.attempt_id, event.epoch)


def _payload(event: CoordinatorEvent) -> bytes:
    value = asdict(event)
    value["kind"] = event.kind.value
    for name in ("owned_paths", "upstream_dep_shas", "seat_outcomes"):
        value[name] = list(value[name])
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if len(raw) > _MAX_RECORD_BYTES:
        raise ValueError("convergence event exceeds metadata-only size limit")
    return raw


def _append(path: Path, event: CoordinatorEvent) -> None:
    if ".phase-loop" in path.parts:
        raise ValueError("convergence event logs cannot be stored under .phase-loop")
    raw = _payload(event)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, raw)
            os.fsync(fd)
        finally:
            os.close(fd)


def record_intent(path: Path, event: CoordinatorEvent) -> None:
    if event.kind is not CoordinatorEventKind.INTENT:
        raise ValueError("record_intent requires an intent event")
    if event in read_convergence_events(path):
        return
    _append(path, event)


def record_outcome(path: Path, event: CoordinatorEvent) -> None:
    if event.kind is not CoordinatorEventKind.OUTCOME:
        raise ValueError("record_outcome requires an outcome event")
    events = read_convergence_events(path)
    matches = [item for item in events if item.kind is CoordinatorEventKind.INTENT and _key(item) == _key(event)]
    if not matches:
        raise ValueError("outcome has no matching intent")
    if event in events:
        return
    _append(path, event)


def _event(value: dict) -> CoordinatorEvent:
    value = dict(value)
    value["kind"] = CoordinatorEventKind(value["kind"])
    for name in ("owned_paths", "upstream_dep_shas", "seat_outcomes"):
        value[name] = tuple(value.get(name, ()))
    return CoordinatorEvent(**value)


def read_convergence_events(path: Path) -> tuple[CoordinatorEvent, ...]:
    if not path.exists():
        return ()
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    events: list[CoordinatorEvent] = []
    for index, line in enumerate(lines):
        try:
            events.append(_event(json.loads(line)))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            if index == len(lines) - 1:
                break
            raise ValueError(f"malformed convergence event at line {index + 1}") from exc
    return tuple(events)


def recover_train_state(events: Iterable[CoordinatorEvent]) -> RecoveredTrainState:
    values = tuple(events)
    if not values:
        return RecoveredTrainState(train_id="")
    train_id = values[0].train_id
    ambiguities: list[str] = []
    intents: dict[tuple[str, str, str | None, int | None], CoordinatorEvent] = {}
    outcomes: dict[tuple[str, str, str | None, int | None], CoordinatorEvent] = {}
    node_states: dict[str, CoordinatorEvent] = {}
    versions = {(event.event_schema_version, event.transition_model_version, event.invalidation_model_version) for event in values}
    if len(versions) != 1 or any(event.train_id != train_id for event in values):
        ambiguities.append("mixed event versions or train identities")
    latest_epoch: int | None = None
    for event in values:
        if event.epoch is not None:
            if latest_epoch is not None and event.epoch < latest_epoch:
                ambiguities.append("epoch regression")
            latest_epoch = max(latest_epoch or event.epoch, event.epoch)
        target = intents if event.kind is CoordinatorEventKind.INTENT else outcomes
        key = _key(event)
        if key in target and target[key] != event:
            ambiguities.append("conflicting duplicate event")
        target[key] = event
        node_states[event.node_id] = event
    for key, outcome in outcomes.items():
        if key not in intents:
            ambiguities.append("outcome without intent")
        if outcome.blocker_reason and "ambiguous" in outcome.blocker_reason.lower():
            ambiguities.append("ambiguous provider outcome")
    pending = tuple(event for key, event in intents.items() if key not in outcomes)
    verification_valid = bool(outcomes) and not ambiguities and all(event.verification_digest for event in outcomes.values())
    approval_valid = bool(outcomes) and not ambiguities and all(event.seat_outcomes for event in outcomes.values())
    return RecoveredTrainState(train_id, node_states, pending, latest_epoch, verification_valid, approval_valid, tuple(dict.fromkeys(ambiguities)), len(values) - 1)
