"""Agent-runtime provider seam (CS-0.8).

The deterministic Python runner spawns subscription CLIs (advisor-panel legs
in :mod:`panel_invoker`, harness phase executors elsewhere) as one-shot child
processes. That is a *pluggable backend* wearing a hard-coded shape: callers
reach straight for ``subprocess``/``spawn`` instead of a stable interface, so
swapping in a session-oriented runtime later means touching every call site.

This module names that seam. ``AgentRuntimeProvider`` mirrors the method set
of omniagent-plus's ``core-contracts`` TypeScript interface
(``packages/core-contracts/src/provider.ts``): ``create_session`` /
``send_turn`` / ``read_history`` / ``stream_events`` / ``cancel_turn`` /
``close_session`` / ``get_session_info`` / ``health``. It is deliberately a
*method-set* port, not a schema-for-schema port — there is no zod-equivalent
validation layer here; Python callers get dataclasses and a ``Protocol``.

``HomebrewAgentRuntimeProvider`` is the concrete "degraded profile"
implementation (template: omniagent-plus's ``fake-provider.ts``): it wraps a
single one-shot CLI spawn as a SINGLE-TURN session with BUFFERED event replay.
There is no live event stream (the wrapped CLI call is synchronous and
already complete by the time ``send_turn`` returns), so ``stream_events``
replays the same buffer ``read_history`` would return. ``cancel_turn`` kills a
process handle *if the spawn function registered one*; a spawn that runs to
completion synchronously (the common case today — see
``panel_invoker._default_spawn``) has nothing live to kill, so cancelling an
already-terminal turn is a no-op success, mirroring fake-provider's cancelled
-state short-circuit. These gaps versus a live/streaming/omnigent-backed
runtime are declared, not hidden: ``health()`` reports them in
``unsupported_capabilities``.

Session/turn bookkeeping here is in-memory and per-process only. The
loop/train ledger (:mod:`train_ledger`) stays the enforcement layer's
responsibility — it may reference a provider session by ``session_id``, but
this module never reads or writes ledger state.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Iterator, Mapping, Protocol, runtime_checkable

if TYPE_CHECKING:  # avoid a runtime import cycle (backing_omnigent imports THIS module)
    from .advisor_board.backing_omnigent import OmnigentHttpClient

# ---------------------------------------------------------------------------
# Identifiers (open strings, not closed enums: `runtime.ts` reserves the closed
# `["omnigent"]` set for the eventual Omnigent-backed provider; a Python-only
# closed set here would break the moment that provider drops in).

RUNTIME_HOMEBREW = "homebrew"
RUNTIME_OMNIGENT = "omnigent"

# Reserved ``CreateSessionRequest.metadata`` key carrying the per-seat vendor-key
# HTTP headers (the never-silent-key auth material) through to the omnigent create
# call — metadata is the seam's documented pass-through for transport-specific
# fields, so no new request field is needed.
OMNIGENT_VENDOR_KEY_HEADERS_META = "_omnigent_vendor_key_headers"

AGENT_SESSION_STATES: tuple[str, ...] = (
    "created", "starting", "idle", "turn_active",
    "blocked_on_approval", "cancelling", "closed", "failed",
)
TURN_STATES: tuple[str, ...] = (
    "accepted", "queued", "running", "blocked_on_tool_approval",
    "cancelling", "cancelled", "timed_out", "completed", "failed",
)
CANCELLATION_REASONS: tuple[str, ...] = (
    "user_request", "approval_denied", "timeout", "provider_interrupt", "session_close",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Request/response shapes (mirrors provider.ts's types.ts, simplified: no
# worktree-lease / handoff-packet / retry-policy sub-schemas — callers that
# need those pass them through ``metadata``).


@dataclass(frozen=True)
class CreateSessionRequest:
    target_harness: str
    idempotency_key: str
    title: str
    runtime: str = RUNTIME_HOMEBREW
    correlation_id: str | None = None
    repo_root: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SendTurnRequest:
    session_id: str
    idempotency_key: str
    message: str
    turn_id: str | None = None
    timeout_seconds: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeEvent:
    """Single buffered runtime event. ``type`` mirrors the ``runtime.*`` event
    names in events.ts (e.g. ``runtime.session.created``, ``runtime.turn.started``,
    ``runtime.text.delta``, ``runtime.turn.completed``, ``runtime.turn.failed``,
    ``runtime.turn.cancelled``); ``payload`` carries that event's fields."""

    sequence: int
    session_id: str
    type: str
    occurred_at: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    turn_id: str | None = None
    terminal: bool = False


@dataclass
class AgentSessionInfo:
    id: str
    runtime: str
    target_harness: str
    title: str
    state: str
    created_at: str
    updated_at: str
    active_turn_id: str | None = None
    event_cursor: int = 0
    correlation_id: str | None = None
    repo_root: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class TurnHandle:
    session_id: str
    turn_id: str
    idempotency_key: str
    state: str
    created_at: str
    updated_at: str
    event_cursor: int = 0


@dataclass(frozen=True)
class SessionHistory:
    session_id: str
    events: tuple[RuntimeEvent, ...]
    next_cursor: int | None = None


@dataclass(frozen=True)
class ProviderHealth:
    runtime: str
    backend: str
    available: bool
    active_sessions: int
    unsupported_capabilities: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


class RuntimeProviderError(RuntimeError):
    """Raised for caller-facing provider failures (unknown session/turn, etc.).

    Mirrors errors.ts's ``RuntimeFailure`` shape loosely via ``category`` /
    ``scope`` attributes rather than a parallel zod-validated schema.
    """

    def __init__(self, message: str, *, category: str, scope: str) -> None:
        super().__init__(message)
        self.category = category
        self.scope = scope


# ---------------------------------------------------------------------------
# The interface. `Protocol` (structural) rather than `abc.ABC`: callers that
# already have a spawn-shaped object should not be forced into inheritance to
# satisfy this seam.


@runtime_checkable
class AgentRuntimeProvider(Protocol):
    def create_session(self, request: CreateSessionRequest) -> AgentSessionInfo: ...

    def send_turn(self, request: SendTurnRequest) -> TurnHandle: ...

    def read_history(
        self, session_id: str, *, after_sequence: int = 0, limit: int | None = None
    ) -> SessionHistory: ...

    def stream_events(
        self, session_id: str, *, after_sequence: int = 0
    ) -> Iterator[RuntimeEvent]: ...

    def cancel_turn(self, handle: TurnHandle, reason: str = "user_request") -> TurnHandle: ...

    def close_session(self, session_id: str) -> None: ...

    def get_session_info(self, session_id: str) -> AgentSessionInfo: ...

    def health(self) -> ProviderHealth: ...


# ---------------------------------------------------------------------------
# Homebrew degraded-profile implementation.

# A spawn function performs the actual one-shot CLI call. It receives the
# `SendTurnRequest` and an optional `register_process` callback; if the spawn
# launches a real child process it MAY call `register_process(pid)` so
# `cancel_turn` has something to kill while the turn is still running. Most
# existing spawns (panel_invoker._default_spawn and friends) run a blocking
# `subprocess.run`/PTY session to completion and return before `send_turn`
# gets a chance to hand back a handle to cancel against — that is the
# documented degraded capability, not a bug in this seam.
SpawnResult = tuple[str, str]  # (status, text) — matches panel_invoker.SpawnFn
SpawnFn = Callable[..., SpawnResult]


@dataclass
class _SessionRecord:
    info: AgentSessionInfo
    events: list[RuntimeEvent] = field(default_factory=list)
    turns: dict[str, TurnHandle] = field(default_factory=dict)
    turns_by_key: dict[str, TurnHandle] = field(default_factory=dict)
    live_pids: dict[str, int] = field(default_factory=dict)


class HomebrewAgentRuntimeProvider:
    """Presents a one-shot CLI spawn as a single-turn, buffered-replay session.

    ``spawn(leg_or_target, artifact, **kwargs) -> (status, text)`` is the same
    shape as :data:`panel_invoker.SpawnFn` — this class is the adaptation
    layer, not a rewrite of the CLI legs themselves. Each session supports
    exactly one turn (the "single-turn session" the CS-0.8 spec calls for);
    a second ``send_turn`` on the same session is rejected the same way
    fake-provider.ts rejects a concurrent turn.
    """

    def __init__(self, spawn: SpawnFn, *, backend: str = "cli-spawn") -> None:
        self._spawn = spawn
        self._backend = backend
        self._sessions: dict[str, _SessionRecord] = {}

    # -- session lifecycle ------------------------------------------------

    def create_session(self, request: CreateSessionRequest) -> AgentSessionInfo:
        session_id = _new_id("session")
        now = _now()
        info = AgentSessionInfo(
            id=session_id,
            runtime=request.runtime,
            target_harness=request.target_harness,
            title=request.title,
            state="idle",
            created_at=now,
            updated_at=now,
            correlation_id=request.correlation_id,
            repo_root=request.repo_root,
            metadata=dict(request.metadata),
        )
        record = _SessionRecord(info=info)
        record.events.append(
            RuntimeEvent(
                sequence=1,
                session_id=session_id,
                type="runtime.session.created",
                occurred_at=now,
                payload={"state": "idle", "title": request.title},
            )
        )
        info.event_cursor = 1
        self._sessions[session_id] = record
        return info

    def _record(self, session_id: str) -> _SessionRecord:
        record = self._sessions.get(session_id)
        if record is None:
            raise RuntimeProviderError(
                f"unknown session {session_id}", category="validation", scope="session"
            )
        return record

    def _append(self, record: _SessionRecord, *, type: str, payload: Mapping[str, Any],
                turn_id: str | None = None, terminal: bool = False) -> RuntimeEvent:
        event = RuntimeEvent(
            sequence=len(record.events) + 1,
            session_id=record.info.id,
            type=type,
            occurred_at=_now(),
            payload=dict(payload),
            turn_id=turn_id,
            terminal=terminal,
        )
        record.events.append(event)
        record.info.event_cursor = event.sequence
        return event

    # -- single turn --------------------------------------------------------

    def send_turn(self, request: SendTurnRequest) -> TurnHandle:
        record = self._record(request.session_id)
        existing = record.turns_by_key.get(request.idempotency_key)
        if existing is not None:
            return existing
        if record.info.active_turn_id is not None:
            raise RuntimeProviderError(
                "only one turn is allowed per homebrew (single-turn) session",
                category="concurrency_limit",
                scope="session",
            )

        turn_id = request.turn_id or _new_id("turn")
        now = _now()
        handle = TurnHandle(
            session_id=request.session_id,
            turn_id=turn_id,
            idempotency_key=request.idempotency_key,
            state="running",
            created_at=now,
            updated_at=now,
        )
        record.turns[turn_id] = handle
        record.turns_by_key[request.idempotency_key] = handle
        record.info.active_turn_id = turn_id
        record.info.state = "turn_active"
        record.info.updated_at = now
        self._append(
            record, type="runtime.turn.started",
            payload={"message": request.message, "state": "running"}, turn_id=turn_id,
        )

        def register_process(pid: int) -> None:
            record.live_pids[turn_id] = pid

        try:
            status, text = self._spawn(request, register_process=register_process)
        except Exception as exc:  # fail-closed: a broken spawn fails the turn, never raises
            return self._finish_turn(record, handle, ok=False, text=str(exc), status="ERROR")
        finally:
            record.live_pids.pop(turn_id, None)

        return self._finish_turn(record, handle, ok=(status == "OK"), text=text, status=status)

    def _finish_turn(
        self, record: _SessionRecord, handle: TurnHandle, *, ok: bool, text: str, status: str
    ) -> TurnHandle:
        if handle.state == "cancelled":
            return handle
        now = _now()
        self._append(
            record, type="runtime.text.delta", payload={"delta": text}, turn_id=handle.turn_id,
        )
        if ok:
            self._append(
                record, type="runtime.turn.completed",
                payload={"outcome": "completed", "status": status}, turn_id=handle.turn_id,
                terminal=True,
            )
            handle.state = "completed"
        else:
            self._append(
                record, type="runtime.turn.failed",
                payload={"outcome": "failed", "status": status, "message": text},
                turn_id=handle.turn_id, terminal=True,
            )
            handle.state = "failed"
        handle.updated_at = now
        handle.event_cursor = record.info.event_cursor
        record.info.active_turn_id = None
        record.info.state = "idle"
        record.info.updated_at = now
        return handle

    # -- history / replay ----------------------------------------------------

    def read_history(
        self, session_id: str, *, after_sequence: int = 0, limit: int | None = None
    ) -> SessionHistory:
        record = self._record(session_id)
        events = [e for e in record.events if e.sequence > after_sequence]
        if limit is not None:
            events = events[:limit]
        next_cursor = events[-1].sequence if events else (after_sequence or None)
        return SessionHistory(session_id=session_id, events=tuple(events), next_cursor=next_cursor)

    def stream_events(self, session_id: str, *, after_sequence: int = 0) -> Iterator[RuntimeEvent]:
        # No live stream: the wrapped spawn is already synchronous/complete by
        # the time any turn exists, so "streaming" is a replay of the buffer.
        record = self._record(session_id)
        for event in record.events:
            if event.sequence > after_sequence:
                yield event

    # -- cancellation ---------------------------------------------------------

    def cancel_turn(self, handle: TurnHandle, reason: str = "user_request") -> TurnHandle:
        record = self._record(handle.session_id)
        current = record.turns.get(handle.turn_id)
        if current is None:
            raise RuntimeProviderError(
                f"unknown turn {handle.turn_id}", category="validation", scope="turn",
            )
        if current.state in {"cancelled", "completed", "failed", "timed_out"}:
            return current  # idempotent: nothing live to cancel

        pid = record.live_pids.pop(current.turn_id, None)
        if pid is not None:
            _terminate_pid(pid)

        now = _now()
        self._append(
            record, type="runtime.turn.cancelled",
            payload={"outcome": "cancelled", "reason": reason}, turn_id=current.turn_id,
            terminal=True,
        )
        current.state = "cancelled"
        current.updated_at = now
        current.event_cursor = record.info.event_cursor
        record.info.active_turn_id = None
        record.info.state = "idle"
        record.info.updated_at = now
        return current

    # -- session close / introspection -----------------------------------------

    def close_session(self, session_id: str) -> None:
        record = self._record(session_id)
        if record.info.active_turn_id is not None:
            active = record.turns[record.info.active_turn_id]
            self.cancel_turn(active, reason="session_close")
        now = _now()
        self._append(record, type="runtime.session.closed", payload={"reason": "logical_close"}, terminal=True)
        record.info.state = "closed"
        record.info.updated_at = now

    def get_session_info(self, session_id: str) -> AgentSessionInfo:
        return self._record(session_id).info

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            runtime=RUNTIME_HOMEBREW,
            backend=self._backend,
            available=True,
            active_sessions=sum(1 for r in self._sessions.values() if r.info.state != "closed"),
            unsupported_capabilities=(
                "live_event_streaming",
                "mid_turn_cancellation_of_synchronous_spawns",
                "multi_turn_sessions",
            ),
            notes=("homebrew degraded profile: one-shot CLI spawn presented as a single-turn, buffered-replay session",),
        )


# ---------------------------------------------------------------------------
# Omnigent-backed implementation (ABDOMNI). The `["omnigent"]` runtime this
# module's header reserves, now filled: the sibling of HomebrewAgentRuntimeProvider
# on the SAME AgentRuntimeProvider seam. Template: omniagent-plus's
# `omnigent-transport/http-provider.ts` (`OmnigentHttpProvider implements
# AgentRuntimeProvider`), ported method-for-method over the frozen v0.4.0 HTTP
# surface. The transport itself lives in `advisor_board.backing_omnigent`
# (`OmnigentHttpClient`); this class is the Protocol adaptation, exactly as
# HomebrewAgentRuntimeProvider adapts a one-shot CLI spawn.


def _map_omnigent_state(status: Any) -> str:
    return {
        "launching": "starting",
        "running": "turn_active",
        "waiting": "turn_active",
        "failed": "failed",
        "idle": "idle",
    }.get(str(status), "idle")


class OmnigentAgentRuntimeProvider:
    """Presents Omnigent v0.4.0 (over ``OmnigentHttpClient``) as an
    ``AgentRuntimeProvider``. Single-turn/buffered-replay degraded profile, like
    the homebrew provider: the public transport surface is driven per-turn and the
    SSE stream is replayed from history rather than held open, and child-session /
    public harness-override capabilities are declared unsupported in ``health()``
    (mirroring http-provider.ts's notes)."""

    def __init__(self, client: "OmnigentHttpClient", *, backend: str = "omnigent-http") -> None:
        self._client = client
        self._backend = backend
        self._sessions: dict[str, AgentSessionInfo] = {}
        self._turns: dict[str, TurnHandle] = {}

    # -- session lifecycle -----------------------------------------------------

    def create_session(self, request: CreateSessionRequest) -> AgentSessionInfo:
        headers = dict(request.metadata).get(OMNIGENT_VENDOR_KEY_HEADERS_META) or None
        snapshot = self._client.create_session(
            target_harness=request.target_harness,
            idempotency_key=request.idempotency_key,
            title=request.title,
            vendor_key_headers=headers,
        )
        info = self._to_info(request, snapshot)
        self._sessions[info.id] = info
        return info

    def _to_info(self, request: CreateSessionRequest, snapshot: Mapping[str, Any]) -> AgentSessionInfo:
        now = _now()
        return AgentSessionInfo(
            id=str(snapshot.get("id")),
            runtime=RUNTIME_OMNIGENT,
            target_harness=request.target_harness,
            title=str(snapshot.get("title", request.title)),
            state=_map_omnigent_state(snapshot.get("status")),
            created_at=str(snapshot.get("createdAt", now)),
            updated_at=str(snapshot.get("updatedAt", now)),
            correlation_id=request.correlation_id,
            repo_root=request.repo_root,
            metadata=dict(snapshot.get("metadata") or {}),
        )

    def send_turn(self, request: SendTurnRequest) -> TurnHandle:
        key = f"{request.session_id}:{request.idempotency_key}"
        ack = self._client.send_turn(request.session_id, request.message)
        now = _now()
        handle = TurnHandle(
            session_id=request.session_id,
            turn_id=str(ack.get("turnId")),
            idempotency_key=request.idempotency_key,
            state="queued" if ack.get("queued") else "running",
            created_at=now,
            updated_at=now,
        )
        self._turns[key] = handle
        info = self._sessions.get(request.session_id)
        if info is not None:
            info.active_turn_id = handle.turn_id
            info.state = "turn_active"
            info.updated_at = now
        return handle

    # -- history / replay ------------------------------------------------------

    def _map_history(self, session_id: str, items: list[Any]) -> list[RuntimeEvent]:
        events: list[RuntimeEvent] = []
        for index, item in enumerate(items):
            raw = item.get("event", item) if isinstance(item, Mapping) else {}
            events.append(
                RuntimeEvent(
                    sequence=index + 1,
                    session_id=session_id,
                    type=str(raw.get("type", "")),
                    occurred_at=str(raw.get("occurredAt", _now())),
                    payload={
                        k: raw.get(k)
                        for k in ("delta", "outputText", "reason", "status", "message")
                        if raw.get(k) is not None
                    },
                    turn_id=raw.get("turnId"),
                    terminal=bool(raw.get("terminal")),
                )
            )
        return events

    def read_history(
        self, session_id: str, *, after_sequence: int = 0, limit: int | None = None
    ) -> SessionHistory:
        events = [
            e for e in self._map_history(session_id, self._client.get_history(session_id))
            if e.sequence > after_sequence
        ]
        if limit is not None:
            events = events[:limit]
        next_cursor = events[-1].sequence if events else (after_sequence or None)
        return SessionHistory(session_id=session_id, events=tuple(events), next_cursor=next_cursor)

    def stream_events(self, session_id: str, *, after_sequence: int = 0) -> Iterator[RuntimeEvent]:
        # No held-open stream in the degraded profile — replay the buffer (same as
        # the homebrew provider; the per-turn call is already complete).
        for event in self.read_history(session_id).events:
            if event.sequence > after_sequence:
                yield event

    # -- cancellation / close --------------------------------------------------

    def cancel_turn(self, handle: TurnHandle, reason: str = "user_request") -> TurnHandle:
        self._client.interrupt(handle.session_id, reason)
        handle.state = "cancelled"
        handle.updated_at = _now()
        info = self._sessions.get(handle.session_id)
        if info is not None:
            info.active_turn_id = None
            info.state = "idle"
            info.updated_at = handle.updated_at
        return handle

    def close_session(self, session_id: str) -> None:
        self._client.delete_session(session_id)
        info = self._sessions.get(session_id)
        if info is not None:
            info.state = "closed"
            info.active_turn_id = None
            info.updated_at = _now()

    def get_session_info(self, session_id: str) -> AgentSessionInfo:
        snapshot = self._client.get_session(session_id)
        existing = self._sessions.get(session_id)
        info = AgentSessionInfo(
            id=str(snapshot.get("id", session_id)),
            runtime=RUNTIME_OMNIGENT,
            target_harness=existing.target_harness if existing else "",
            title=str(snapshot.get("title", existing.title if existing else "")),
            state="closed" if (existing and existing.state == "closed") else _map_omnigent_state(snapshot.get("status")),
            created_at=str(snapshot.get("createdAt", existing.created_at if existing else _now())),
            updated_at=str(snapshot.get("updatedAt", _now())),
            correlation_id=existing.correlation_id if existing else None,
            repo_root=existing.repo_root if existing else None,
            metadata=dict(snapshot.get("metadata") or {}),
        )
        self._sessions[session_id] = info
        return info

    def health(self) -> ProviderHealth:
        try:
            self._client.list_harnesses()
            available = True
        except Exception:  # any transport failure → not available
            available = False
        return ProviderHealth(
            runtime=RUNTIME_OMNIGENT,
            backend=self._backend,
            available=available,
            active_sessions=sum(1 for i in self._sessions.values() if i.state != "closed"),
            unsupported_capabilities=(
                "child_session_creation",
                "public_harness_override",
                "held_open_event_streaming",
            ),
            notes=(
                "omnigent v0.4.0 public transport: single-turn board leg presented as a "
                "buffered-replay session; logical close via DELETE",
            ),
        )


def _terminate_pid(pid: int) -> None:
    """Best-effort kill of a registered live process group (mirrors panel_invoker's
    `_terminate_process_group`, generalized to a bare pid since spawn functions
    register only a pid, not a `subprocess.Popen`)."""
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError, OSError):
            return
        time.sleep(0.05)
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
