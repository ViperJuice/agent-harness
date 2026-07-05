"""Internal advisor-board event envelope + best-effort forwarding (IF-0-ABDFREEZE-5).

This is OUR event shape — deliberately NOT a guessed Omnigent schema. It freezes
the observability contract's two load-bearing rules:

* **launcher != observability-plane**: a natively-launched leg stays natively
  launched and *emits* an ``AdvisorBoardEvent`` into a forwarded stream; it is
  never relaunched through the gateway for observability's sake.
* **forwarding is async/best-effort and can NEVER delay or fail the native
  leg**: ``best_effort_forward`` swallows every sink error and (by contract)
  hands the event to the sink off the leg's critical path.

The MAPPING from this envelope to a concrete sink (an Omnigent v0.4.0 ingestion
endpoint, or omniagent-plus's own ui-read-model / state-ledger) is deferred to
ABDOBS — do NOT freeze against a guessed upstream schema.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# Bumped only on a breaking change to the envelope; sinks key off it.
EVENT_SCHEMA_VERSION = "advisor_board.event.v1"

# The frozen event kinds a board leg emits. Board-level bracket the run; seat-level
# mirror a leg's lifecycle (aligned with the runtime ``runtime.*`` names in
# agent_runtime_provider.RuntimeEvent, but named in OUR namespace).
EVENT_KINDS: tuple[str, ...] = (
    "board.started",
    "board.completed",
    "seat.started",
    "seat.text.delta",
    "seat.completed",
    "seat.failed",
    "seat.skipped",
)


@dataclass(frozen=True)
class AdvisorBoardEvent:
    """One observability event from a board run — our envelope, sink-agnostic.

    ``board``          the board name (e.g. ``"default"``).
    ``seat_key``       the emitting seat's stable identity (``Seat.seat_key``);
                       empty for ``board.*`` events.
    ``vendor_family``  the seat's projected vendor family (empty for board events).
    ``harness``        the seat's execution lane (empty for board events).
    ``kind``           one of ``EVENT_KINDS``.
    ``sequence``       monotonic per board run.
    ``occurred_at``    ISO-8601 UTC timestamp (caller-supplied; frozen field only).
    ``payload``        kind-specific fields (e.g. ``{"delta": ...}`` /
                       ``{"status": ...}``); never carries a raw API key.
    """

    kind: str
    board: str
    sequence: int
    occurred_at: str
    seat_key: str = ""
    vendor_family: str = ""
    harness: str = ""
    schema_version: str = EVENT_SCHEMA_VERSION
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in EVENT_KINDS:
            raise ValueError(f"event.kind {self.kind!r} not in {EVENT_KINDS}")

    def to_json(self) -> dict[str, Any]:
        """Stable serialization for a sink. Field set is the frozen wire contract."""
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "board": self.board,
            "seat_key": self.seat_key,
            "vendor_family": self.vendor_family,
            "harness": self.harness,
            "sequence": self.sequence,
            "occurred_at": self.occurred_at,
            "payload": dict(self.payload),
        }


@runtime_checkable
class EventSink(Protocol):
    """A destination for advisor-board events. The concrete mapping to Omnigent /
    ui-read-model / state-ledger is ABDOBS; this only fixes the emit surface."""

    def emit(self, event: AdvisorBoardEvent) -> None: ...


class NullSink:
    """Default sink: drops every event. Keeps the ``default`` board's observability
    a no-op (today's behavior: no forwarding) until ABDOBS wires a real sink."""

    def emit(self, event: AdvisorBoardEvent) -> None:  # noqa: D401 - trivial
        return None


def best_effort_forward(sink: EventSink | None, event: AdvisorBoardEvent) -> bool:
    """Forward ``event`` to ``sink``, swallowing EVERY error.

    Returns whether the sink accepted the event; a ``None`` sink or any raised
    exception returns ``False`` WITHOUT propagating — forwarding must never delay
    or fail the native leg (IF-0-ABDFREEZE-5). The async dispatch (off the leg's
    critical path) is ABDOBS's to wire; the never-raise guarantee is frozen here.
    """
    if sink is None:
        return False
    try:
        sink.emit(event)
        return True
    except Exception:  # best-effort: an observability failure NEVER touches the leg
        return False
