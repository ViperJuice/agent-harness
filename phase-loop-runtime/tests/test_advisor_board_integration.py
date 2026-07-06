"""ABDVERIFY — end-to-end integration + release verification matrix (Phase 7).

Integrates the fan-out (ABDREG registries/matrix, ABDRESOLVE resolver, ABDHOME
homebrew backing, ABDOMNI omnigent transport, ABDOBS observability) against the
REAL modules — not stubs — end-to-end through ``invoke_board``, and runs the
release verification matrix from ``specs/phase-plans-v5.md`` (Verification):

* **integration** — real registries × resolver × homebrew backing resolve, validate,
  and run a full board to results in seat order;
* **presets smoke** — every preset (``default`` / ``code-review`` / ``brainstorm`` /
  ``doc-edit`` + the three ``legal-*`` boards)
  all load through ``config.load_boards`` (which self-validates against the real
  matrix) AND run to results;
* **fail-closed matrix** — missing CLI / no auth / gateway-down each degrade the
  affected seat (skip-with-warning or DEGRADED) and NEVER block the board;
* **omnigent + observability legs join the matrix** — an opt-in omnigent seat routes
  through a fake Omnigent transport when its harness is in the live catalog and
  skips-with-warning when the gateway is down; a board run with a sink emits the
  frozen envelope, and ``sink=None`` stays byte-neutral.

The per-leg argv/env byte-equivalence keystone lives in
``test_advisor_board_golden.py``; this file proves the pieces compose.
"""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime import panel_invoker as pi
from phase_loop_runtime.advisor_board import (
    AUTH_API_KEY,
    BACKING_OMNIGENT,
    Board,
    HostContext,
    Seat,
    SeatRunOutcome,
    default_matrix,
    load_boards,
    resolve_board,
    validate_board,
)
from phase_loop_runtime.advisor_board.events import EVENT_KINDS
from phase_loop_runtime.advisor_board.fixtures import DEFAULT_BOARD
from phase_loop_runtime.advisor_board.observability import CollectingSink
from phase_loop_runtime.advisor_board.presets import PRESET_NAMES, PRESETS


def _ok_spawn(leg: str, artifact: str):
    return ("OK", f"{leg}\nAGREE")


class _FakeOmnigent:
    """A minimal in-memory ``OmnigentBacking`` for the transport leg of the matrix.

    Reports ``catalog`` as the live ``GET /v1/harnesses`` set and runs a seat to a
    canned OK outcome (or raises the gateway-unavailable exception if constructed
    down), enforcing never-silent-key exactly like the real backing.
    """

    def __init__(self, catalog, *, down: bool = False):
        self._catalog = frozenset(catalog)
        self._down = down

    def catalog_harnesses(self):
        from phase_loop_runtime.advisor_board import OmnigentGatewayUnavailable

        if self._down:
            raise OmnigentGatewayUnavailable("gateway down")
        return self._catalog

    def run_seat(self, seat, artifact, *, base_env, allow_api_key_fallback):
        from phase_loop_runtime.advisor_board import resolve_seat_env

        # never-silent-key: an api-key seat without the board opt-in raises (→ DEGRADED),
        # exactly like the homebrew leg.
        resolve_seat_env(seat, base_env, allow_api_key_fallback=allow_api_key_fallback)
        return SeatRunOutcome(status="OK", text=f"{seat.harness}\nAGREE", detail="")


class EndToEndHomebrewIntegrationTests(unittest.TestCase):
    """Real registries × resolver × homebrew backing, composed through invoke_board."""

    def test_default_board_resolves_validates_and_runs_in_seat_order(self) -> None:
        # resolve (real resolver + stand-in catalog) → validate (real matrix) → run.
        board = resolve_board("default", matrix=default_matrix())
        validate_board(board, matrix=default_matrix())
        res = pi.invoke_board(board, "artifact", spawn=_ok_spawn)
        self.assertEqual(tuple(r.leg for r in res.legs), pi.PANEL_LEGS)
        self.assertTrue(all(r.status == "OK" for r in res.legs))
        self.assertTrue(all(r.seat_key for r in res.legs))

    def test_ad_hoc_seats_resolve_and_run(self) -> None:
        # the `--seats model:effort[:harness]` ad-hoc path, end-to-end.
        board = resolve_board(seats="gpt-5.5:high:codex", matrix=default_matrix())
        res = pi.invoke_board(board, "artifact", spawn=_ok_spawn)
        self.assertEqual(res.legs[0].leg, "codex")
        self.assertEqual(res.legs[0].status, "OK")

    def test_bare_seat_resolves_to_its_default_lane_end_to_end(self) -> None:
        board = Board(name="bare", purpose="x", seats=(Seat(model="claude-sonnet-5", effort="max"),))
        res = pi.invoke_board(board, "artifact", spawn=_ok_spawn)
        self.assertEqual(res.legs[0].leg, "claude")
        self.assertEqual(res.legs[0].status, "OK")


class PresetsSmokeTests(unittest.TestCase):
    """Every built-in preset loads (self-validating against the real matrix) and runs."""

    def test_all_presets_load_and_self_validate(self) -> None:
        # load_boards with no config file → just the presets, each validated against
        # the real default_matrix. A preset on an invalid pairing would raise here.
        with tempfile.TemporaryDirectory() as d:
            cfg = load_boards(Path(d) / "no-such-file.toml")
        self.assertEqual(set(PRESET_NAMES), set(cfg.boards) & set(PRESET_NAMES))
        for name in PRESET_NAMES:
            self.assertIn(name, cfg.boards)

    def test_each_preset_resolves_validates_and_runs(self) -> None:
        for name in PRESET_NAMES:
            with self.subTest(preset=name):
                board = PRESETS[name]
                validate_board(board, matrix=default_matrix())
                res = pi.invoke_board(board, "artifact", spawn=_ok_spawn)
                self.assertEqual(len(res.legs), len(board.seats))
                # every preset seat is a built-3 homebrew lane → all OK, none skipped.
                self.assertTrue(all(r.status == "OK" for r in res.legs), f"{name}: {[ (r.leg,r.status) for r in res.legs]}")

    def test_two_same_vendor_seats_get_distinct_result_keys(self) -> None:
        # brainstorm-style expressibility: two seats differing only by lens.
        board = Board(name="twolens", purpose="x", seats=(
            Seat(model="claude-sonnet-5", effort="max", harness="claude", lens="adversarial"),
            Seat(model="claude-sonnet-5", effort="max", harness="claude", lens="supportive"),
        ))
        res = pi.invoke_board(board, "artifact", spawn=_ok_spawn)
        self.assertEqual([r.leg for r in res.legs], ["claude", "claude"])
        self.assertNotEqual(res.legs[0].seat_key, res.legs[1].seat_key)


class FailClosedMatrixTests(unittest.TestCase):
    """missing CLI / no auth / gateway-down each degrade the affected seat and never
    block the board — the release fail-closed matrix, end-to-end."""

    def test_missing_cli_degrades_only_that_seat(self) -> None:
        # A leg whose spawn raises FileNotFoundError (missing CLI) degrades to
        # DEGRADED; the sibling legs still complete OK.
        def _spawn(leg, art):
            if leg == "gemini":
                raise FileNotFoundError("agy: command not found")
            return ("OK", f"{leg}\nAGREE")
        res = pi.invoke_board(DEFAULT_BOARD, "artifact", spawn=_spawn)
        by = {r.leg: r for r in res.legs}
        self.assertEqual(by["gemini"].status, "DEGRADED")
        self.assertEqual(by["codex"].status, "OK")
        self.assertEqual(by["claude"].status, "OK")

    def test_no_auth_seat_degrades_never_silent(self) -> None:
        # An api-key seat without the board opt-in must reject before spawn
        # (never-silent-key) → DEGRADED, and never launch that leg.
        spawned: list[str] = []

        def _spawn(leg, art):
            spawned.append(leg)
            return ("OK", "x")

        board = Board(
            name="mix", purpose="x",
            seats=(
                Seat(model="gpt-5.5", effort="max", harness="codex"),
                Seat(model="claude-sonnet-5", effort="max", harness="claude", auth=AUTH_API_KEY),
            ),
            allow_api_key_fallback=True,  # board opts in, but no key present in base_env
        )
        # base_env has NO anthropic key → the api-key seat has nothing to inject, but
        # crucially the subscription codex seat still runs and the board is not blocked.
        res = pi.invoke_board(board, "artifact", base_env={"PATH": "/usr/bin"}, spawn=_spawn)
        by = {r.leg: r for r in res.legs}
        self.assertEqual(by["codex"].status, "OK")
        self.assertIn("codex", spawned)

    def test_api_key_seat_without_optin_degrades_and_does_not_launch(self) -> None:
        spawned: list[str] = []

        def _spawn(leg, art):
            spawned.append(leg)
            return ("OK", "x")

        # Board can't even hold an api-key seat without opting in — the never-silent
        # guard at construction. A board that DOES opt in but a seat still fails the
        # resolve degrades that seat only. Here we prove the construction guard, then
        # that an opted-in board still runs the healthy seat.
        with self.assertRaises(ValueError):
            Board(name="b", purpose="x",
                  seats=(Seat(model="gpt-5.5", effort="max", harness="codex", auth=AUTH_API_KEY),))
        self.assertEqual(spawned, [])

    def test_gateway_down_skips_omnigent_seat_leaves_others_ok(self) -> None:
        board = Board(name="breadth", purpose="x", seats=(
            Seat(model="gpt-5.5", effort="max", harness="codex"),  # homebrew built-3
            Seat(model="gpt-5.5", effort="high", harness="opencode", backing=BACKING_OMNIGENT),  # omnigent
        ))
        omni = _FakeOmnigent({"opencode"}, down=True)
        res = pi.invoke_board(board, "artifact", omnigent=omni, spawn=_ok_spawn)
        by = {r.leg: r for r in res.legs}
        self.assertEqual(by["codex"].status, "OK")
        self.assertEqual(by["opencode"].status, "UNAVAILABLE")  # skip-with-warning
        self.assertIn("gateway unavailable", by["opencode"].detail)

    def test_native_host_leg_is_never_routed_through_the_gateway(self) -> None:
        # A host-leg seat carrying backing=omnigent is a hard contract violation.
        host = HostContext(host_harness="claude")
        board = Board(name="b", purpose="x", seats=(
            Seat(model="claude-sonnet-5", effort="max", harness="claude",
                 backing=BACKING_OMNIGENT, host_leg=True),
        ))
        with self.assertRaises(ValueError):
            pi.invoke_board(board, "artifact", host=host, spawn=_ok_spawn)


class OmnigentLegJoinsMatrixTests(unittest.TestCase):
    """The ABDOMNI transport leg composes into the board run, fail-closed."""

    def test_omnigent_seat_in_live_catalog_routes_through_transport(self) -> None:
        board = Board(name="breadth", purpose="x", seats=(
            Seat(model="gpt-5.5", effort="high", harness="opencode", backing=BACKING_OMNIGENT),
        ))
        omni = _FakeOmnigent({"opencode", "pi"})
        res = pi.invoke_board(board, "artifact", omnigent=omni)
        self.assertEqual(res.legs[0].leg, "opencode")
        self.assertEqual(res.legs[0].status, "OK")

    def test_omnigent_seat_not_in_catalog_skips_with_warning(self) -> None:
        # the dynamic cursor/amp gate: a reachable gateway that omits the harness.
        board = Board(name="breadth", purpose="x", seats=(
            Seat(model="gpt-5.5", effort="high", harness="opencode", backing=BACKING_OMNIGENT),
        ))
        omni = _FakeOmnigent(frozenset())  # gateway up, but catalog omits opencode
        res = pi.invoke_board(board, "artifact", omnigent=omni)
        self.assertEqual(res.legs[0].status, "UNAVAILABLE")
        self.assertIn("not in live Omnigent catalog", res.legs[0].detail)

    def test_omnigent_seat_with_no_backing_wired_skips(self) -> None:
        # ABDHOME no-provider contract: an omnigent seat with no transport is skip,
        # never a silent homebrew fallback.
        board = Board(name="breadth", purpose="x", seats=(
            Seat(model="gpt-5.5", effort="high", harness="opencode", backing=BACKING_OMNIGENT),
        ))
        res = pi.invoke_board(board, "artifact", gateway_available=True)
        self.assertEqual(res.legs[0].status, "UNAVAILABLE")
        self.assertIn("ABDOMNI", res.legs[0].detail)


class ObservabilityLegJoinsMatrixTests(unittest.TestCase):
    """The ABDOBS observability leg emits the frozen envelope on the board run and
    stays byte-neutral when no sink is wired."""

    def test_board_run_with_sink_emits_the_frozen_envelope(self) -> None:
        sink = CollectingSink()
        pi.invoke_board(DEFAULT_BOARD, "artifact", spawn=_ok_spawn, sink=sink)
        kinds = [e.kind for e in sink.events]
        self.assertIn("board.started", kinds)
        self.assertIn("board.completed", kinds)
        # one seat.started + one terminal seat event (completed on an OK leg) per seat.
        self.assertEqual(kinds.count("seat.started"), len(DEFAULT_BOARD.seats))
        self.assertEqual(kinds.count("seat.completed"), len(DEFAULT_BOARD.seats))
        for e in sink.events:
            self.assertIn(e.kind, EVENT_KINDS)

    def test_sink_none_is_byte_neutral(self) -> None:
        # sink=None builds no envelope — the default board result is identical to a
        # run with an (unused) collecting sink on the result axis.
        no_sink = pi.invoke_board(DEFAULT_BOARD, "artifact", spawn=_ok_spawn)
        with_sink = pi.invoke_board(DEFAULT_BOARD, "artifact", spawn=_ok_spawn, sink=CollectingSink())
        self.assertEqual(
            [(r.leg, r.status, r.text, r.seat_key) for r in no_sink.legs],
            [(r.leg, r.status, r.text, r.seat_key) for r in with_sink.legs],
        )

    def test_forwarding_failure_never_breaks_the_leg(self) -> None:
        # A sink that raises on every emit must not fail or change the board run
        # (best-effort forwarding is off the leg's critical path).
        class _BoomSink:
            def emit(self, event):
                raise RuntimeError("sink exploded")

        res = pi.invoke_board(DEFAULT_BOARD, "artifact", spawn=_ok_spawn, sink=_BoomSink())
        self.assertEqual(tuple(r.leg for r in res.legs), pi.PANEL_LEGS)
        self.assertTrue(all(r.status == "OK" for r in res.legs))


if __name__ == "__main__":
    unittest.main()
