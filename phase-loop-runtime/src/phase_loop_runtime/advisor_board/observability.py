"""ABDOBS — observability forwarding: envelope → state-ledger sink + async
best-effort dispatch (Phase 6, specs/phase-plans-v5.md).

This wires the MAPPING that ABDFREEZE-5 deferred: the frozen internal
``AdvisorBoardEvent`` envelope (:mod:`events`) → omniagent-plus's own
``state-ledger`` / ``ui-read-model`` — the **confirmed** sink, because v0.4.0's
HTTP surface is launcher-centric and exposes **no ingestion endpoint** for an
externally-launched (native) session. We control that ledger, so the mapping is
ours to own here.

Three load-bearing rules carry over from ABDFREEZE-5 and are enforced here:

* **launcher ≠ observability-plane** — a natively-launched leg stays natively
  launched and only *emits*; nothing in this module can create a session or send
  a turn (the sinks are structurally emit-only), so observability can never
  relaunch the native host leg through the gateway.
* **async / best-effort, never delays or fails the native leg** —
  :class:`AsyncForwardingSink` enqueues with a NON-BLOCKING put on the leg's
  thread and does the (possibly slow / failing) sink write on a background
  daemon thread; :class:`BoardObserver` wraps construct+map+enqueue in a
  swallow-all so even a bad event kind or a full queue never touches the leg.
* **our envelope, not a guessed upstream schema** — the wire target is
  omniagent-plus's *own* frozen shapes (``runtime_event.v0.1`` inside
  ``state_ledger_record.v0.1``), so a real binding can feed
  ``AuditLedger.appendRuntimeEvent`` verbatim.

**Cross-language seam.** The ledger is TypeScript (omniagent-plus
``packages/state-ledger``); the emit is Python (here). This module therefore
ships (a) the pure mapping functions, (b) a :class:`LedgerWriter` adapter
Protocol — the documented integration boundary a real omniagent-plus binding
implements over IPC/HTTP/a shared file — and (c) :class:`JsonlLedgerWriter`, a
reference transport that appends the exact ``state_ledger_record.v0.1`` records
the TS ``AppendOnlyStore`` ingests. It does NOT reimplement ledger internals
(retention / replay / compaction stay TS-side).
"""
from __future__ import annotations

import json
import queue
import threading
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .events import (
    AdvisorBoardEvent,
    EventSink,
    best_effort_forward,
)

# --- per-workload boundary (documented + structurally enforced) -------------
# Board = native launch + OPTIONAL forward (this module); phase-execution =
# Omnigent-as-launcher (CS-2.2, out of scope). The native host leg is NEVER
# relaunched through the gateway for observability's sake — the sinks below are
# emit-only, so this path *cannot* launch anything.
WORKLOAD_BOARD = "advisor_board"
WORKLOAD_PHASE_EXECUTION = "phase_execution"

# omniagent-plus wire schemas we target (owned by us; frozen upstream).
LEDGER_RECORD_SCHEMA = "state_ledger_record.v0.1"  # packages/core-contracts state-ledger.ts
RUNTIME_EVENT_SCHEMA = "runtime_event.v0.1"        # packages/core-contracts events.ts
LEDGER_RECORD_KIND = "runtime_event"               # AuditLedger.appendRuntimeEvent kind

# Our envelope kind -> the omniagent runtime.* event type it maps to. A board run
# projects to a session; each seat projects to a turn within that session.
_KIND_TO_RUNTIME_TYPE: dict[str, str] = {
    "board.started": "runtime.session.created",
    "board.completed": "runtime.session.closed",
    "seat.started": "runtime.turn.started",
    "seat.text.delta": "runtime.text.delta",
    "seat.completed": "runtime.turn.completed",
    "seat.failed": "runtime.turn.failed",
    "seat.skipped": "runtime.turn.cancelled",
}

# Which of our kinds are terminal for their scope (turn/session end).
_TERMINAL_KINDS = frozenset(
    {"board.completed", "seat.completed", "seat.failed", "seat.skipped"}
)


def _now_iso() -> str:
    """ISO-8601 UTC with an explicit offset (matches the upstream zod
    ``datetime({offset: true})`` contract)."""
    return datetime.now(timezone.utc).isoformat()


def new_session_id() -> str:
    """Mint a per-board-run session id (never reuse the board NAME as the session
    id — that collides across runs and is semantically wrong)."""
    return f"session-{uuid.uuid4().hex[:12]}"


def _turn_id_for(event: AdvisorBoardEvent) -> str | None:
    """A seat projects to a turn; derive a stable turn id from the seat's frozen
    ``seat_key`` label. ``board.*`` events have no turn. Two byte-identical seats
    share a ``seat_key`` (a label, not a unique id, per ABDFREEZE-1) and so map to
    the same turn — an accepted, documented consequence of the label semantics."""
    if not event.seat_key:
        return None
    slug = "".join(c if c.isalnum() else "-" for c in event.seat_key).strip("-")
    return f"turn-{slug or 'seat'}"


def _runtime_payload(event: AdvisorBoardEvent) -> dict[str, Any]:
    """Project our envelope payload onto the runtime.* event's payload shape.

    Kept faithful to the upstream per-type payloads (events.ts) so a real binding
    validates against ``runtimeEventSchema`` without a second translation."""
    p = dict(event.payload)
    kind = event.kind
    if kind == "board.started":
        return {"state": "idle", "title": str(p.get("title", event.board))}
    if kind == "board.completed":
        return {"reason": "logical_close"}
    if kind == "seat.started":
        return {"message": str(p.get("message", "")), "state": "running"}
    if kind == "seat.text.delta":
        return {"delta": str(p.get("delta", ""))}
    if kind == "seat.completed":
        out = {"outcome": "completed"}
        if p.get("outputSummary"):
            out["outputSummary"] = str(p["outputSummary"])
        return out
    if kind == "seat.failed":
        return {"outcome": "failed", "failure": p.get("failure", {"reason": p.get("status", "failed")})}
    if kind == "seat.skipped":
        return {"outcome": "cancelled", "reason": str(p.get("reason", "skipped"))}
    return p  # pragma: no cover - kinds are exhaustive (EVENT_KINDS)


def map_event_to_runtime_event(
    event: AdvisorBoardEvent, *, session_id: str
) -> dict[str, Any]:
    """Map our envelope → an omniagent ``runtime_event.v0.1`` envelope (dict).

    ``redaction`` is ``metadata_only`` except for the text delta (the advisor's
    own model output, allowed content); we never carry a raw API key (frozen in
    the envelope contract), so nothing here is ``content_redacted``."""
    runtime_type = _KIND_TO_RUNTIME_TYPE.get(event.kind)
    if runtime_type is None:  # pragma: no cover - EVENT_KINDS is closed
        raise ValueError(f"no runtime.* mapping for kind {event.kind!r}")
    redaction = "content_allowed" if event.kind == "seat.text.delta" else "metadata_only"
    envelope: dict[str, Any] = {
        "schema": RUNTIME_EVENT_SCHEMA,
        "eventId": f"event-{uuid.uuid4().hex[:12]}",
        "sequence": int(event.sequence),
        "sessionId": session_id,
        "type": runtime_type,
        "occurredAt": event.occurred_at,
        "payload": _runtime_payload(event),
        "redaction": redaction,
        "terminal": event.kind in _TERMINAL_KINDS,
    }
    turn_id = _turn_id_for(event)
    if turn_id is not None:
        envelope["turnId"] = turn_id
    # The board name is our cross-run correlation handle in the plane.
    if event.board:
        envelope["correlationId"] = event.board
    return envelope


def map_event_to_ledger_record(
    event: AdvisorBoardEvent, *, session_id: str, sequence: int | None = None
) -> dict[str, Any]:
    """Map our envelope → a ``state_ledger_record.v0.1`` record (dict), kind
    ``runtime_event`` — exactly what ``AuditLedger.appendRuntimeEvent`` writes.

    ``sequence`` is the ledger's own append cursor (positive, per store); when a
    writer assigns it, pass it through. Defaults to the event's own sequence."""
    runtime_event = map_event_to_runtime_event(event, session_id=session_id)
    record: dict[str, Any] = {
        "schema": LEDGER_RECORD_SCHEMA,
        "recordId": f"record-{uuid.uuid4().hex[:12]}",
        "sequence": int(sequence if sequence is not None else event.sequence),
        "kind": LEDGER_RECORD_KIND,
        "schemaVersion": 1,
        "recordedAt": event.occurred_at,
        "sessionId": session_id,
        "payload": runtime_event,
    }
    if "turnId" in runtime_event:
        record["turnId"] = runtime_event["turnId"]
    return record


# --- cross-language transport seam ------------------------------------------


@runtime_checkable
class LedgerWriter(Protocol):
    """The documented integration boundary to omniagent-plus's TS state-ledger.

    A real binding implements ``append_record`` by forwarding the
    ``state_ledger_record.v0.1`` dict to ``AuditLedger.appendRuntimeEvent`` across
    the process/language boundary (IPC / HTTP / a shared append file). This
    module ships :class:`JsonlLedgerWriter` as the reference transport; it is
    deliberately NOT a live write to the real ledger (retention / replay /
    compaction are TS-side)."""

    def append_record(self, record: Mapping[str, Any]) -> None: ...


class JsonlLedgerWriter:
    """Reference :class:`LedgerWriter`: appends one ``state_ledger_record.v0.1``
    JSON object per line to a file the TS ``AppendOnlyStore`` can ingest/replay.
    A file boundary is the cleanest documented cross-language seam."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def append_record(self, record: Mapping[str, Any]) -> None:
        line = json.dumps(dict(record), separators=(",", ":"), sort_keys=True)
        with self._lock:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")


# --- sinks (structurally emit-only: this path can never launch) --------------


class StateLedgerSink:
    """:class:`~events.EventSink` that maps each envelope → a state-ledger record
    and hands it to a :class:`LedgerWriter`. Emit-only by construction — it has
    no ``create_session`` / ``send_turn``, so the observability plane can never
    relaunch a leg (the launcher ≠ observability-plane boundary, in code)."""

    def __init__(self, writer: LedgerWriter, *, session_id: str | None = None) -> None:
        self._writer = writer
        self._session_id = session_id or new_session_id()

    @property
    def session_id(self) -> str:
        return self._session_id

    def emit(self, event: AdvisorBoardEvent) -> None:
        record = map_event_to_ledger_record(event, session_id=self._session_id)
        self._writer.append_record(record)


class CollectingSink:
    """In-memory :class:`~events.EventSink` (tests / a local ui-read-model tail).
    Emit-only, thread-safe."""

    def __init__(self) -> None:
        self._events: list[AdvisorBoardEvent] = []
        self._lock = threading.Lock()

    def emit(self, event: AdvisorBoardEvent) -> None:
        with self._lock:
            self._events.append(event)

    @property
    def events(self) -> tuple[AdvisorBoardEvent, ...]:
        with self._lock:
            return tuple(self._events)


class AsyncForwardingSink:
    """Wrap any :class:`~events.EventSink` and dispatch OFF the leg's critical
    path: ``emit`` does a NON-BLOCKING put and returns immediately; a background
    daemon thread performs the real (possibly slow / failing) downstream write
    via :func:`best_effort_forward`, which swallows every sink error.

    This is the async half ABDFREEZE-5 deferred: the never-raise guarantee is
    frozen in ``events.best_effort_forward``; the never-DELAY guarantee is here
    (unbounded queue by default; a bounded queue drops on full rather than
    blocking the leg)."""

    def __init__(self, downstream: EventSink, *, maxsize: int = 0) -> None:
        self._downstream = downstream
        self._queue: queue.Queue[Any] = queue.Queue(maxsize)
        self._thread = threading.Thread(
            target=self._run, name="advisor-board-observability", daemon=True
        )
        self._sentinel = object()
        self._started = False
        self._closed = False
        self._lock = threading.Lock()
        self.dropped = 0  # events shed under back-pressure (bounded queue only)

    def _ensure_started(self) -> None:
        with self._lock:
            if not self._started and not self._closed:
                self._thread.start()
                self._started = True

    def emit(self, event: AdvisorBoardEvent) -> None:
        # Non-blocking: a full bounded queue drops the event rather than delaying
        # the leg. Enqueue errors are swallowed — observability never fails a leg.
        self._ensure_started()
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            self.dropped += 1
        except Exception:
            return

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is self._sentinel:
                    return
                best_effort_forward(self._downstream, item)
            finally:
                self._queue.task_done()

    def flush(self, timeout: float | None = None) -> bool:
        """Block until the CURRENT backlog has been dispatched. Returns True if it
        drained (best effort; only for a deterministic test / graceful shutdown —
        never called on the leg's critical path)."""
        if not self._started:
            return True
        deadline_join = self._queue.join  # join has no timeout; used via close for hard drain
        if timeout is None:
            deadline_join()
            return True
        # Poll unfinished tasks so a hung downstream can't wedge the caller.
        import time

        end = time.monotonic() + timeout
        while self._queue.unfinished_tasks and time.monotonic() < end:
            time.sleep(0.005)
        return self._queue.unfinished_tasks == 0

    def close(self, timeout: float | None = 5.0) -> None:
        """Drain deterministically and stop the worker. FIFO guarantees every
        event enqueued before ``close`` is dispatched before the sentinel, so a
        test can assert the plane's contents right after ``close``."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if not self._started:
                return
        self._queue.put(self._sentinel)
        self._thread.join(timeout)


# --- the board observer (swallow-all emit around a board run) ----------------


def _status_to_seat_kind(status: str) -> str:
    """Map a ``PanelLegResult.status`` to the terminal seat event kind."""
    s = (status or "").upper()
    if s in {"OK", "EMPTY"}:
        return "seat.completed"
    if s == "UNAVAILABLE":
        return "seat.skipped"
    return "seat.failed"  # DEGRADED / ERROR / TIMEOUT


class BoardObserver:
    """Emit the ``board.*`` / ``seat.*`` envelope sequence for one board run.

    EVERY emit is swallow-all: the AdvisorBoardEvent constructor can raise on a
    bad kind, the mapping can raise, and the enqueue can raise — none of that may
    delay or fail the native leg, so the whole construct+map+forward path is
    wrapped. A ``None`` sink is a cheap no-op (keeps the default board
    byte-neutral: no envelope is even built)."""

    def __init__(self, sink: EventSink | None, *, board_name: str) -> None:
        self._sink = sink
        self._board = board_name
        self._seq = 0
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._sink is not None

    def _emit(
        self,
        kind: str,
        *,
        seat_key: str = "",
        vendor_family: str = "",
        harness: str = "",
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        if self._sink is None:
            return
        try:
            with self._lock:
                self._seq += 1
                seq = self._seq
            event = AdvisorBoardEvent(
                kind=kind,
                board=self._board,
                sequence=seq,
                occurred_at=_now_iso(),
                seat_key=seat_key,
                vendor_family=vendor_family,
                harness=harness,
                payload=dict(payload or {}),
            )
            best_effort_forward(self._sink, event)
        except Exception:
            # Observability NEVER touches the leg — swallow construction/map/enqueue.
            return

    def board_started(self) -> None:
        self._emit("board.started", payload={"title": self._board})

    def board_completed(self, results: Any) -> None:
        try:
            legs = list(results)
            ok = sum(1 for r in legs if getattr(r, "status", "") in {"OK", "EMPTY"})
            payload = {"seats": len(legs), "ok": ok}
        except Exception:
            payload = {}
        self._emit("board.completed", payload=payload)

    def _seat_fields(self, seat: Any) -> dict[str, str]:
        try:
            return {
                "seat_key": str(getattr(seat, "seat_key", "") or ""),
                "vendor_family": str(getattr(seat, "vendor_family", "") or ""),
                "harness": str(getattr(seat, "harness", "") or ""),
            }
        except Exception:
            return {"seat_key": "", "vendor_family": "", "harness": ""}

    def seat_started(self, seat: Any) -> None:
        self._emit("seat.started", **self._seat_fields(seat))

    def seat_result(self, seat: Any, result: Any) -> None:
        fields = self._seat_fields(seat)
        status = str(getattr(result, "status", "") or "")
        kind = _status_to_seat_kind(status)
        payload: dict[str, Any] = {"status": status}
        detail = getattr(result, "detail", None)
        if kind == "seat.skipped" and detail:
            payload["reason"] = str(detail)
        if kind == "seat.failed":
            payload["failure"] = {"reason": str(detail or status)}
        self._emit(kind, payload=payload, **fields)


__all__ = [
    "WORKLOAD_BOARD",
    "WORKLOAD_PHASE_EXECUTION",
    "LEDGER_RECORD_SCHEMA",
    "RUNTIME_EVENT_SCHEMA",
    "LEDGER_RECORD_KIND",
    "new_session_id",
    "map_event_to_runtime_event",
    "map_event_to_ledger_record",
    "LedgerWriter",
    "JsonlLedgerWriter",
    "StateLedgerSink",
    "CollectingSink",
    "AsyncForwardingSink",
    "BoardObserver",
]
