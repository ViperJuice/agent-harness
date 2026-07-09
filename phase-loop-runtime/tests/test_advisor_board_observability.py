"""ABDOBS — observability forwarding (Phase 6).

Proves the four exit criteria against the FROZEN ABDFREEZE-5 envelope + the real
omniagent-plus sink shapes (`runtime_event.v0.1` inside `state_ledger_record.v0.1`):

1. **envelope emit** — a natively-launched board emits its runtime events as the
   frozen `AdvisorBoardEvent`; async/best-effort, so a forwarding failure (raise)
   or a slow sink (block) can NEVER delay or fail a native leg.
2. **envelope → confirmed sink** — the envelope maps to omniagent-plus's OWN
   state-ledger record (kind `runtime_event`), fed through the `LedgerWriter`
   cross-language seam (reference `JsonlLedgerWriter`), NOT a guessed HTTP endpoint.
3. **native host leg in the plane, un-gatewayed** — inside Claude, the claude host
   leg spawns natively (never a gateway) AND shows up in the sink.
4. **per-workload boundary** — sinks are structurally emit-only (cannot launch);
   `sink=None` is byte-neutral; the gatewayed-host-leg invariant still hard-raises.
"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime import panel_invoker as pi
from phase_loop_runtime.advisor_board import (
    BACKING_OMNIGENT,
    Board,
    HostContext,
    Seat,
)
from phase_loop_runtime.advisor_board.events import AdvisorBoardEvent, EVENT_KINDS
from phase_loop_runtime.advisor_board.fixtures import DEFAULT_BOARD
from phase_loop_runtime.advisor_board import observability as obs


def _ok_spawn(leg, art):
    return ("OK", f"{leg}\nAGREE")


# ---------------------------------------------------------------------------
# 1. Envelope emit — a natively-launched board emits the frozen envelope.


class EnvelopeEmitTests(unittest.TestCase):
    def test_default_board_emits_board_and_seat_envelope_sequence(self) -> None:
        sink = obs.CollectingSink()
        res = pi.invoke_board(DEFAULT_BOARD, "artifact", spawn=_ok_spawn, sink=sink)
        self.assertTrue(all(l.status == "OK" for l in res.legs))
        kinds = [e.kind for e in sink.events]
        # board brackets the run; each of the 3 seats emits started + a terminal.
        self.assertEqual(kinds[0], "board.started")
        self.assertEqual(kinds[-1], "board.completed")
        self.assertEqual(kinds.count("seat.started"), 3)
        self.assertEqual(kinds.count("seat.completed"), 3)
        # every emitted kind is a frozen EVENT_KIND; sequence is monotonic.
        self.assertTrue(all(k in EVENT_KINDS for k in kinds))
        seqs = [e.sequence for e in sink.events]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(seqs, list(range(1, len(seqs) + 1)))

    def test_seat_events_carry_seat_key_vendor_family_and_harness(self) -> None:
        sink = obs.CollectingSink()
        pi.invoke_board(DEFAULT_BOARD, "artifact", spawn=_ok_spawn, sink=sink)
        seat_events = [e for e in sink.events if e.kind.startswith("seat.")]
        self.assertTrue(seat_events)
        for e in seat_events:
            self.assertTrue(e.seat_key)
            self.assertTrue(e.vendor_family)
            self.assertTrue(e.harness)
        # board events carry the board name, no seat identity.
        board_events = [e for e in sink.events if e.kind.startswith("board.")]
        for e in board_events:
            self.assertEqual(e.board, "default")
            self.assertEqual(e.seat_key, "")

    def test_skipped_and_failed_seats_map_to_their_terminal_kinds(self) -> None:
        sink = obs.CollectingSink()
        # opencode/omnigent seat with no gateway → UNAVAILABLE (skip); codex → OK.
        board = Board(name="mix", purpose="x", seats=(
            Seat(model="gpt-5.6-sol", effort="max", harness="codex"),
            Seat(model="gpt-5.6-sol", effort="high", harness="opencode", backing=BACKING_OMNIGENT),
        ))
        pi.invoke_board(board, "artifact", spawn=_ok_spawn, sink=sink)
        by_kind = [e.kind for e in sink.events]
        self.assertIn("seat.completed", by_kind)  # codex OK
        self.assertIn("seat.skipped", by_kind)    # opencode UNAVAILABLE


# ---------------------------------------------------------------------------
# 1b. Async / best-effort: NEVER delays or fails the native leg.


class NeverFailsTheLegTests(unittest.TestCase):
    def test_a_sink_that_raises_on_emit_does_not_fail_the_board(self) -> None:
        class RaisingSink:
            def emit(self, event: AdvisorBoardEvent) -> None:
                raise RuntimeError("sink boom")

        res = pi.invoke_board(DEFAULT_BOARD, "artifact", spawn=_ok_spawn, sink=RaisingSink())
        self.assertEqual(tuple(l.leg for l in res.legs), ("codex", "gemini", "claude"))
        self.assertTrue(all(l.status == "OK" for l in res.legs))  # leg unaffected

    def test_async_wrapping_a_raising_sink_drains_without_crashing(self) -> None:
        class RaisingSink:
            def emit(self, event: AdvisorBoardEvent) -> None:
                raise RuntimeError("sink boom")

        async_sink = obs.AsyncForwardingSink(RaisingSink())
        res = pi.invoke_board(DEFAULT_BOARD, "artifact", spawn=_ok_spawn, sink=async_sink)
        async_sink.close()  # deterministic drain; must not raise
        self.assertTrue(all(l.status == "OK" for l in res.legs))

    def test_observer_swallows_a_construction_failure(self) -> None:
        # Even if envelope CONSTRUCTION raises (bad kind / frozen-dataclass guard),
        # the leg path must not see it. Force AdvisorBoardEvent to raise.
        sink = obs.CollectingSink()
        observer = obs.BoardObserver(sink, board_name="b")
        with patch.object(obs, "AdvisorBoardEvent", side_effect=ValueError("boom")):
            observer.board_started()  # must not raise
            observer.seat_started(Seat(model="claude-sonnet-5", effort="max", harness="claude"))
        self.assertEqual(sink.events, ())  # nothing forwarded, nothing raised

    def test_a_blocking_sink_does_not_delay_the_leg(self) -> None:
        # Deterministic (no sleeps): the sink parks on a gate on the BACKGROUND
        # thread; invoke_board must return while the sink is still parked.
        release = threading.Event()

        class BlockingSink:
            def __init__(self) -> None:
                self.entered = threading.Event()
                self.count = 0

            def emit(self, event: AdvisorBoardEvent) -> None:
                self.count += 1
                self.entered.set()
                release.wait(5)  # park until released

        blocking = BlockingSink()
        async_sink = obs.AsyncForwardingSink(blocking)
        res = pi.invoke_board(DEFAULT_BOARD, "artifact", spawn=_ok_spawn, sink=async_sink)
        # We are HERE (board returned) while the bg thread is parked in emit:
        self.assertTrue(blocking.entered.wait(5), "async dispatch never ran")
        self.assertFalse(release.is_set())            # sink still blocked
        self.assertTrue(all(l.status == "OK" for l in res.legs))  # leg unaffected
        # Now release and drain deterministically; every event lands.
        release.set()
        async_sink.close()
        self.assertGreaterEqual(blocking.count, 1)

    def test_async_close_drains_every_enqueued_event(self) -> None:
        collecting = obs.CollectingSink()
        async_sink = obs.AsyncForwardingSink(collecting)
        pi.invoke_board(DEFAULT_BOARD, "artifact", spawn=_ok_spawn, sink=async_sink)
        async_sink.close()  # FIFO + sentinel → all prior events dispatched
        kinds = [e.kind for e in collecting.events]
        self.assertEqual(kinds[0], "board.started")
        self.assertEqual(kinds[-1], "board.completed")
        self.assertEqual(kinds.count("seat.completed"), 3)


# ---------------------------------------------------------------------------
# 2. Envelope → the CONFIRMED sink (omniagent-plus state-ledger record shape).


class EnvelopeToLedgerMappingTests(unittest.TestCase):
    def _seat_event(self, kind: str = "seat.completed") -> AdvisorBoardEvent:
        return AdvisorBoardEvent(
            kind=kind, board="default", sequence=4, occurred_at="2026-07-05T00:00:00+00:00",
            seat_key="claude|claude-sonnet-5|max", vendor_family="claude", harness="claude",
            payload={"status": "OK"},
        )

    def test_ledger_record_has_the_frozen_upstream_shape(self) -> None:
        rec = obs.map_event_to_ledger_record(self._seat_event(), session_id="session-abc")
        self.assertEqual(rec["schema"], "state_ledger_record.v0.1")
        self.assertEqual(rec["kind"], "runtime_event")
        self.assertEqual(rec["schemaVersion"], 1)
        self.assertEqual(rec["sessionId"], "session-abc")
        self.assertIsInstance(rec["sequence"], int)
        self.assertGreater(rec["sequence"], 0)               # zod: positive int
        self.assertTrue(rec["recordedAt"].endswith("+00:00"))  # zod: datetime w/ offset
        self.assertTrue(rec["recordId"])
        # payload IS the runtime_event.v0.1 envelope.
        self.assertEqual(rec["payload"]["schema"], "runtime_event.v0.1")

    def test_board_projects_to_session_and_seat_projects_to_turn(self) -> None:
        board_ev = AdvisorBoardEvent(
            kind="board.started", board="default", sequence=1,
            occurred_at="2026-07-05T00:00:00+00:00", payload={"title": "default"},
        )
        board_rt = obs.map_event_to_runtime_event(board_ev, session_id="session-abc")
        self.assertEqual(board_rt["type"], "runtime.session.created")
        self.assertNotIn("turnId", board_rt)  # a board event has no turn

        seat_rt = obs.map_event_to_runtime_event(self._seat_event(), session_id="session-abc")
        self.assertEqual(seat_rt["type"], "runtime.turn.completed")
        self.assertTrue(seat_rt["turnId"])   # a seat projects to a turn
        self.assertEqual(seat_rt["sessionId"], "session-abc")

    def test_every_kind_maps_to_a_runtime_type_and_marks_terminals(self) -> None:
        terminal = {"board.completed", "seat.completed", "seat.failed", "seat.skipped"}
        for kind in EVENT_KINDS:
            ev = AdvisorBoardEvent(
                kind=kind, board="b", sequence=1, occurred_at="2026-07-05T00:00:00+00:00",
                seat_key=("s" if kind.startswith("seat.") else ""),
            )
            rt = obs.map_event_to_runtime_event(ev, session_id="s1")
            self.assertTrue(rt["type"].startswith("runtime."))
            self.assertEqual(rt["terminal"], kind in terminal)

    def test_seat_failed_payload_conforms_to_runtime_failure_schema(self) -> None:
        # Upstream runtime_failure.v0.1 (errors.ts) requires ALL of
        # schema/category/retryable/actor/scope/message — a bare {"reason": ...}
        # would fail zod. Prove the mapping emits the full, valid object.
        ev = AdvisorBoardEvent(
            kind="seat.failed", board="b", sequence=2, occurred_at="2026-07-05T00:00:00+00:00",
            seat_key="codex|gpt-5.6-sol|max", payload={"status": "DEGRADED", "failure": {"reason": "boom"}},
        )
        rt = obs.map_event_to_runtime_event(ev, session_id="s1")
        failure = rt["payload"]["failure"]
        self.assertEqual(failure["schema"], "runtime_failure.v0.1")
        for req in ("category", "retryable", "actor", "scope", "message"):
            self.assertIn(req, failure)
        self.assertEqual(failure["scope"], "turn")
        self.assertEqual(failure["actor"], "harness")
        self.assertIsInstance(failure["retryable"], bool)
        self.assertTrue(failure["message"])  # zod: min length 1
        self.assertEqual(rt["payload"]["outcome"], "failed")

    def test_redaction_never_content_redacted_and_no_raw_key(self) -> None:
        for kind in EVENT_KINDS:
            ev = AdvisorBoardEvent(kind=kind, board="b", sequence=1,
                                   occurred_at="2026-07-05T00:00:00+00:00",
                                   seat_key=("s" if kind.startswith("seat.") else ""))
            rt = obs.map_event_to_runtime_event(ev, session_id="s1")
            self.assertIn(rt["redaction"], {"metadata_only", "content_allowed"})

    def test_new_session_id_is_not_the_board_name(self) -> None:
        sink = obs.StateLedgerSink(obs.JsonlLedgerWriter("/dev/null"))
        self.assertTrue(sink.session_id.startswith("session-"))
        self.assertNotEqual(sink.session_id, "default")


class JsonlLedgerWriterSeamTests(unittest.TestCase):
    def test_state_ledger_sink_writes_ndjson_records_a_ts_store_can_ingest(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "ledger.ndjson"
            writer = obs.JsonlLedgerWriter(path)
            sink = obs.StateLedgerSink(writer, session_id="session-fixed")
            res = pi.invoke_board(DEFAULT_BOARD, "artifact", spawn=_ok_spawn, sink=sink)
            self.assertTrue(all(l.status == "OK" for l in res.legs))
            lines = path.read_text().splitlines()
            self.assertTrue(lines)
            records = [json.loads(x) for x in lines]
            # every line is a valid-shaped state-ledger runtime_event record.
            for rec in records:
                self.assertEqual(rec["schema"], "state_ledger_record.v0.1")
                self.assertEqual(rec["kind"], "runtime_event")
                self.assertEqual(rec["sessionId"], "session-fixed")
                self.assertEqual(rec["payload"]["schema"], "runtime_event.v0.1")
            types = [r["payload"]["type"] for r in records]
            self.assertEqual(types[0], "runtime.session.created")
            self.assertEqual(types[-1], "runtime.session.closed")

    def test_writer_is_the_documented_ledgerwriter_seam(self) -> None:
        # A custom LedgerWriter (a real omniagent-plus binding would be one) is
        # accepted structurally — the cross-language boundary is this Protocol.
        captured: list[dict] = []

        class FakeBinding:
            def append_record(self, record):
                captured.append(dict(record))

        self.assertIsInstance(FakeBinding(), obs.LedgerWriter)
        sink = obs.StateLedgerSink(FakeBinding(), session_id="s")
        pi.invoke_board(DEFAULT_BOARD, "artifact", spawn=_ok_spawn, sink=sink)
        self.assertTrue(captured)
        self.assertTrue(all(r["kind"] == "runtime_event" for r in captured))


# ---------------------------------------------------------------------------
# 3. The native host leg appears in the plane WITHOUT being gatewayed.


class NativeHostLegInThePlaneTests(unittest.TestCase):
    def test_inside_claude_host_leg_spawns_natively_and_appears_in_the_sink(self) -> None:
        host = HostContext(host_harness="claude")
        sink = obs.CollectingSink()
        with patch.object(pi, "_default_spawn", return_value=("OK", "AGREE")) as ds:
            res = pi.invoke_board(DEFAULT_BOARD, "artifact", host=host, sink=sink)
        # native homebrew spawn (never a gateway) …
        self.assertIn("claude", {c.args[0] for c in ds.call_args_list})
        self.assertTrue(any(l.leg == "claude" and l.status == "OK" for l in res.legs))
        # … AND the claude host leg is observed in the plane.
        claude_seat_events = [
            e for e in sink.events if e.kind.startswith("seat.") and e.harness == "claude"
        ]
        self.assertTrue(claude_seat_events)
        self.assertTrue(any(e.kind == "seat.completed" for e in claude_seat_events))


# ---------------------------------------------------------------------------
# 4. Per-workload boundary — documented + enforced.


class PerWorkloadBoundaryTests(unittest.TestCase):
    def test_sinks_are_structurally_emit_only_cannot_launch(self) -> None:
        # An observability sink cannot create a session or send a turn — so the
        # plane can never relaunch the native host leg (launcher != plane, in code).
        for sink in (
            obs.CollectingSink(),
            obs.StateLedgerSink(obs.JsonlLedgerWriter("/dev/null")),
            obs.AsyncForwardingSink(obs.CollectingSink()),
        ):
            self.assertTrue(hasattr(sink, "emit"))
            self.assertFalse(hasattr(sink, "create_session"))
            self.assertFalse(hasattr(sink, "send_turn"))

    def test_sink_none_is_byte_neutral(self) -> None:
        # Same call with and without a sink returns identical leg tuples; sink=None
        # builds no envelope at all (default board unchanged).
        with patch.object(pi, "_default_spawn", return_value=("OK", "AGREE")):
            base = pi.invoke_board(DEFAULT_BOARD, "artifact")
        with patch.object(pi, "_default_spawn", return_value=("OK", "AGREE")):
            withsink = pi.invoke_board(DEFAULT_BOARD, "artifact", sink=obs.CollectingSink())
        self.assertEqual(
            [(l.leg, l.status, l.seat_key) for l in base.legs],
            [(l.leg, l.status, l.seat_key) for l in withsink.legs],
        )

    def test_gatewayed_host_leg_still_hard_raises_even_with_a_sink(self) -> None:
        # Observability does NOT relax the native-host-leg invariant: a host-leg
        # omnigent seat is still a hard raise, never a silently-forwarded skip.
        host = HostContext(host_harness="claude")
        board = Board(name="b", purpose="x", seats=(
            Seat(model="claude-sonnet-5", effort="max", harness="claude",
                 backing=BACKING_OMNIGENT, host_leg=True),
        ))
        with self.assertRaises(ValueError):
            pi.invoke_board(board, "artifact", host=host, spawn=_ok_spawn, sink=obs.CollectingSink())

    def test_workload_constants_name_the_boundary(self) -> None:
        self.assertEqual(obs.WORKLOAD_BOARD, "advisor_board")
        self.assertEqual(obs.WORKLOAD_PHASE_EXECUTION, "phase_execution")


if __name__ == "__main__":
    unittest.main()
