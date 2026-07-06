"""ABDVERIFY — back-compat GOLDEN proof (the release keystone, IF-0-ABDFREEZE-4).

This is the gate that protects every existing caller of the panel (the governed
gates in ``governed_review``/``governed_premerge`` and standalone use). It fills in
the FULL golden dimensions the ABDFREEZE scaffold
(``tests/test_advisor_board_backcompat.py::DeferredGoldenDimensionsScaffold``)
deferred to ABDVERIFY, proving the ``default`` board reproduces today's 3-leg
``invoke_panel`` behavior byte-for-byte — NOT just seat count.

The proof is deliberately split into two complementary halves so it maps cleanly
onto the real code paths (the three legs do NOT hit the subprocess boundary
uniformly — claude drives the TUI session + a poll loop, codex/gemini fire an
auth preflight before the leg cmd), rather than one fragile global
``subprocess.run`` diff:

* **Proof A — per-leg launch (argv / env / timeout).** The ``default`` board's
  seats, rendered THROUGH the seam (``render_seat_invocation`` +
  ``resolve_seat_env``), produce the exact argv + scrubbed env + timeout of the
  legacy effort/env-absent path. Captured at each leg's real launcher.
* **Proof B — whole-board behavior (launch order / statuses / text / result keys
  / failure semantics).** One shared recording ``spawn`` drives the full
  ``invoke_panel(artifact, PANEL_LEGS)`` and ``invoke_board(DEFAULT_BOARD)`` and
  asserts the two ``PanelResult``s agree on ``leg`` / ``status`` / ``text`` /
  ``detail`` and on failure classification (raise → DEGRADED, empty-on-OK →
  EMPTY, unknown status → DEGRADED).

**The one contract-sanctioned delta — ``seat_key``.** ``invoke_panel`` leaves
``PanelLegResult.seat_key`` unset, so it defaults to the bare ``leg`` (e.g.
``"codex"``); ``invoke_board`` carries the seat's richer stable label (e.g.
``"codex:gpt-5.5:max"``). This is the ABDRESOLVE finding-4 leg→seat re-key
(``PanelLegResult`` docstring; ``advisor_board.resolver.key_results_by_seat``): a
STRICT SUPERSET — ``.leg`` is preserved, ``.seat_key`` is enriched so two
same-vendor seats become expressible — and it is asserted explicitly here as the
sole difference, never silently dropped. Existing callers key on ``.leg`` /
``.status`` / ``.usable`` and are unaffected (they also still call
``invoke_panel``, which is untouched — ``invoke_board`` has no live caller yet).

Absolute-literal anchoring already lives in ``fixtures.py`` (the hard-coded
``DEFAULT_SEAT_RENDERED_MODEL`` / effort args) cross-checked against the live
``panel_invoker`` constants in ``test_advisor_board_backcompat.py``; this file
proves the two live code paths agree, so it does not re-snapshot literals.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime import panel_invoker as pi
from phase_loop_runtime.advisor_board import Seat, resolve_seat_env
from phase_loop_runtime.advisor_board.fixtures import DEFAULT_BOARD


def _capture_run(stdout: str = "", returncode: int = 0):
    """Capture the LAST ``subprocess.run`` call's cmd/env/timeout. codex/gemini
    fire an auth-preflight ``run`` first; the leg cmd overwrites it (rc0 ⇒ the
    preflight passes, exactly as the homebrew test relies on)."""
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["env"] = kwargs.get("env")
        captured["timeout"] = kwargs.get("timeout")

        class _R:
            pass

        r = _R()
        r.returncode = returncode
        r.stdout = stdout
        r.stderr = ""
        return r

    return captured, fake_run


def _default_seat(harness: str) -> Seat:
    (seat,) = [s for s in DEFAULT_BOARD.seats if s.harness == harness]
    return seat


# ---------------------------------------------------------------------------
# Proof A — per-leg launch: argv + env + timeout byte-equivalence.
# ---------------------------------------------------------------------------


class GoldenPerLegLaunchTests(unittest.TestCase):
    """Each default seat, rendered through the seam, launches with the exact argv,
    scrubbed env, and timeout of the legacy (effort/env-absent) path."""

    def _exec_leg_pair(self, leg: str, stdout: str = ""):
        base = dict(os.environ)
        seat = _default_seat(leg)
        with tempfile.TemporaryDirectory() as rd, tempfile.TemporaryDirectory() as od:
            legacy, fake = _capture_run(stdout=stdout)
            with patch.object(subprocess, "run", fake):
                pi._exec_leg(leg, Path(rd), Path(od))  # legacy: effort/env absent
            seam, fake2 = _capture_run(stdout=stdout)
            with patch.object(subprocess, "run", fake2):
                pi._exec_leg(
                    leg, Path(rd), Path(od),
                    effort=seat.effort, model=seat.model,
                    env=resolve_seat_env(seat, base),
                )
        return legacy, seam

    def test_codex_argv_env_and_timeout_equal_legacy(self) -> None:
        legacy, seam = self._exec_leg_pair("codex")
        self.assertEqual(legacy["cmd"], seam["cmd"])       # argv
        self.assertEqual(legacy["env"], seam["env"])       # scrubbed env
        self.assertEqual(legacy["timeout"], seam["timeout"])  # timeout metadata
        # anchor: the codex effort literal really is in the argv both ways.
        self.assertIn("model_reasoning_effort=xhigh", seam["cmd"])

    def test_gemini_argv_env_and_timeout_equal_legacy(self) -> None:
        legacy, seam = self._exec_leg_pair("gemini", stdout="AGREE")
        self.assertEqual(legacy["cmd"], seam["cmd"])
        self.assertEqual(legacy["env"], seam["env"])
        self.assertEqual(legacy["timeout"], seam["timeout"])
        # anchor: the agy leg bakes effort INTO the model name, both ways.
        self.assertEqual(seam["cmd"][seam["cmd"].index("--model") + 1], "Gemini 3.1 Pro (High)")

    def test_claude_argv_and_env_equal_legacy(self) -> None:
        seat = _default_seat("claude")
        with tempfile.TemporaryDirectory() as rd:
            legacy_cmd = pi._claude_tui_command(Path(rd), Path(rd), None, None)
            seam_cmd = pi._claude_tui_command(Path(rd), Path(rd), seat.model, seat.effort)
        self.assertEqual(legacy_cmd, seam_cmd)
        # env: a subscription claude seat resolves to exactly today's _subscription_env().
        self.assertEqual(resolve_seat_env(seat, dict(os.environ)), pi._subscription_env())
        # anchor: the claude effort flag is present both ways.
        self.assertIn("--effort", seam_cmd)

    def test_per_leg_timeout_is_a_pure_function_of_the_staged_artifact(self) -> None:
        # claude's timeout is not leg-cmd-local (the TUI session); it comes from
        # _leg_timeout_for(review_dir) in _default_spawn, shared by BOTH paths and
        # a pure function of the staged bytes. Identical artifact + mode ⇒ identical
        # timeout, so no per-path divergence is possible.
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            for d in (a, b):
                (Path(d) / "review-bundle.md").write_text("artifact-body", encoding="utf-8")
                (Path(d) / "review-instructions.md").write_text(pi._mode_instructions("review"), encoding="utf-8")
            self.assertEqual(pi._leg_timeout_for(Path(a)), pi._leg_timeout_for(Path(b)))


# ---------------------------------------------------------------------------
# Proof B — whole-board behavior: order / statuses / text / result keys /
# failure semantics, invoke_panel vs invoke_board(default).
# ---------------------------------------------------------------------------


class _RecordingSpawn:
    """A spawn shared by both paths; both call ``spawn(leg, artifact)`` identically.

    Thread-safe: the panel/board now fan legs out concurrently, so ``calls`` is
    appended from multiple worker threads. The lock keeps the record consistent;
    the *order* of ``calls`` is wall-clock scheduling order (non-deterministic under
    concurrency) — assertions read it as a SET, never a sequence."""

    def __init__(self, reply):
        self._reply = reply
        self._lock = threading.Lock()
        self.calls: list[tuple[str, str]] = []

    def __call__(self, leg: str, artifact: str):
        with self._lock:
            self.calls.append((leg, artifact))
        return self._reply(leg, artifact)


class GoldenWholeBoardBehaviorTests(unittest.TestCase):
    """The default board through ``invoke_board`` reproduces ``invoke_panel``'s
    launch order, per-leg status/text/detail, and failure classification —
    ``seat_key`` being the one documented additive delta."""

    ARTIFACT = "the-review-bundle"

    def _run_both(self, reply):
        panel_spawn = _RecordingSpawn(reply)
        board_spawn = _RecordingSpawn(reply)
        panel = pi.invoke_panel(self.ARTIFACT, pi.PANEL_LEGS, spawn=panel_spawn)
        board = pi.invoke_board(DEFAULT_BOARD, self.ARTIFACT, spawn=board_spawn)
        return panel, board, panel_spawn, board_spawn

    def _assert_leg_status_text_parity(self, panel, board) -> None:
        # Byte-for-byte on the fields every existing caller reads.
        self.assertEqual([r.leg for r in panel.legs], [r.leg for r in board.legs])
        self.assertEqual([r.status for r in panel.legs], [r.status for r in board.legs])
        self.assertEqual([r.text for r in panel.legs], [r.text for r in board.legs])
        self.assertEqual([r.detail for r in panel.legs], [r.detail for r in board.legs])

    def test_every_leg_launched_once_and_results_in_order_both_paths(self) -> None:
        # Legs now fan out CONCURRENTLY, so wall-clock launch order is no longer a
        # deterministic sequence — the meaningful invariants are (a) every leg is
        # launched exactly once, (b) each is fed the same artifact, and (c) RESULTS
        # come back in canonical PANEL_LEGS / seat order (positional re-key + the
        # order/content assertions below depend on this, NOT on scheduling order).
        panel, board, ps, bs = self._run_both(lambda leg, art: ("OK", f"{leg}\nAGREE"))
        for spawn in (ps, bs):
            self.assertEqual(
                sorted(leg for leg, _ in spawn.calls), sorted(pi.PANEL_LEGS)
            )  # each leg launched exactly once (set + count)
            self.assertTrue(all(art == self.ARTIFACT for _, art in spawn.calls))
        # RESULT order is the load-bearing invariant and is preserved deterministically.
        self.assertEqual([r.leg for r in panel.legs], list(pi.PANEL_LEGS))
        self.assertEqual([r.leg for r in board.legs], list(pi.PANEL_LEGS))
        self._assert_leg_status_text_parity(panel, board)

    def test_ok_results_are_byte_identical_except_seat_key(self) -> None:
        panel, board, _, _ = self._run_both(lambda leg, art: ("OK", f"{leg}\nAGREE"))
        self._assert_leg_status_text_parity(panel, board)
        self.assertTrue(all(r.status == "OK" for r in board.legs))

    def test_seat_key_is_the_sole_documented_delta(self) -> None:
        # invoke_panel: seat_key defaults to the bare leg. invoke_board: the richer
        # per-seat label (finding 4). This is the ONLY field that differs — asserted,
        # never silently dropped. .leg is preserved in both (superset, not a rename).
        panel, board, _, _ = self._run_both(lambda leg, art: ("OK", f"{leg}\nAGREE"))
        self.assertEqual([r.seat_key for r in panel.legs], list(pi.PANEL_LEGS))
        self.assertEqual(
            [r.seat_key for r in board.legs],
            ["codex:gpt-5.5:max", "gemini:Gemini 3.1 Pro:high", "claude:claude-fable-5:max"],
        )
        for p, b in zip(panel.legs, board.legs):
            self.assertEqual(p.leg, b.leg)             # bare vendor lane unchanged
            self.assertTrue(b.seat_key.startswith(b.leg))  # richer key extends the leg

    def test_failure_semantics_raise_degrades_both_paths(self) -> None:
        def _raise(leg, art):
            raise RuntimeError(f"{leg} boom")
        panel, board, _, _ = self._run_both(_raise)
        self.assertTrue(all(r.status == "DEGRADED" for r in panel.legs))
        self._assert_leg_status_text_parity(panel, board)

    def test_failure_semantics_empty_on_ok_becomes_empty_both_paths(self) -> None:
        panel, board, _, _ = self._run_both(lambda leg, art: ("OK", "   "))
        self.assertTrue(all(r.status == "EMPTY" for r in panel.legs))
        self._assert_leg_status_text_parity(panel, board)

    def test_failure_semantics_unknown_status_degrades_both_paths(self) -> None:
        panel, board, _, _ = self._run_both(lambda leg, art: ("NONSENSE", "body"))
        self.assertTrue(all(r.status == "DEGRADED" for r in panel.legs))
        self._assert_leg_status_text_parity(panel, board)

    def test_mixed_per_leg_outcomes_classify_identically(self) -> None:
        # A heterogeneous board run: one OK, one empty-on-OK, one raising — the two
        # paths must classify each leg identically (order-sensitive).
        def _reply(leg, art):
            return {
                "codex": ("OK", "codex\nAGREE"),
                "gemini": ("OK", ""),
            }.get(leg) or (_ for _ in ()).throw(RuntimeError("claude down"))
        panel, board, _, _ = self._run_both(_reply)
        self.assertEqual([r.status for r in panel.legs], ["OK", "EMPTY", "DEGRADED"])
        self._assert_leg_status_text_parity(panel, board)


class GoldenApiStabilityTests(unittest.TestCase):
    """The ``invoke_panel`` API and the default-board result cardinality are frozen
    (the release contract the governed gates depend on)."""

    def test_default_board_yields_exactly_three_legs_in_panel_order(self) -> None:
        board = pi.invoke_board(DEFAULT_BOARD, "x", spawn=lambda leg, art: ("OK", f"{leg}\nAGREE"))
        self.assertEqual(tuple(r.leg for r in board.legs), pi.PANEL_LEGS)

    def test_invoke_board_default_matches_invoke_panel_usable_semantics(self) -> None:
        # .usable (status OK + non-empty text) is what governed_review keys on.
        reply = lambda leg, art: ("OK", f"{leg}\nAGREE")
        panel = pi.invoke_panel("x", pi.PANEL_LEGS, spawn=lambda leg, art: reply(leg, art))
        board = pi.invoke_board(DEFAULT_BOARD, "x", spawn=lambda leg, art: reply(leg, art))
        self.assertEqual([r.usable for r in panel.legs], [r.usable for r in board.legs])


class ConcurrencyProofTests(unittest.TestCase):
    """The legs run CONCURRENTLY, not serially — proven with a ``threading.Barrier``
    that only releases when all N legs are in-flight at the SAME time, so no real
    sleeps and no wall-clock assertions.

    If execution were serial, the first leg's ``barrier.wait()`` would block for the
    others (which never start until it returns) until the barrier times out →
    ``BrokenBarrierError`` → the fail-closed spawn wrapper records that leg DEGRADED →
    the ``all OK`` assertion fails. So an all-OK result is only reachable when every
    leg is genuinely in-flight simultaneously. Result order is still asserted, proving
    concurrency did not disturb the positional contract."""

    _BARRIER_TIMEOUT_S = 5.0

    def _barrier_spawn(self, n: int):
        barrier = threading.Barrier(n, timeout=self._BARRIER_TIMEOUT_S)

        def spawn(leg: str, artifact: str):
            # Blocks until all n legs have reached the barrier — only possible if the
            # pool runs them concurrently. Serial execution ⇒ BrokenBarrierError here,
            # caught by the invoke_* fail-closed wrapper ⇒ DEGRADED (not OK).
            barrier.wait()
            return ("OK", f"{leg}\nAGREE")

        return spawn

    def test_invoke_board_runs_seats_concurrently(self) -> None:
        n = len(DEFAULT_BOARD.seats)
        res = pi.invoke_board(DEFAULT_BOARD, "artifact", spawn=self._barrier_spawn(n))
        self.assertTrue(
            all(r.status == "OK" for r in res.legs),
            f"a leg did not run concurrently (barrier timed out): "
            f"{[(r.leg, r.status) for r in res.legs]}",
        )
        self.assertEqual([r.leg for r in res.legs], list(pi.PANEL_LEGS))  # order preserved

    def test_invoke_panel_runs_legs_concurrently(self) -> None:
        n = len(pi.PANEL_LEGS)
        res = pi.invoke_panel("artifact", pi.PANEL_LEGS, spawn=self._barrier_spawn(n))
        self.assertTrue(
            all(r.status == "OK" for r in res.legs),
            f"a leg did not run concurrently (barrier timed out): "
            f"{[(r.leg, r.status) for r in res.legs]}",
        )
        self.assertEqual([r.leg for r in res.legs], list(pi.PANEL_LEGS))  # order preserved

    # --- the opt-in escape hatch: max_concurrency=1 forces SEQUENTIAL --------

    def _unsatisfiable_barrier_spawn(self, n: int):
        # A Barrier(n) that can NEVER be satisfied when only one leg runs at a time,
        # with a short timeout (we EXPECT it to time out under serial execution).
        barrier = threading.Barrier(n, timeout=0.5)

        def spawn(leg: str, artifact: str):
            barrier.wait()  # serial ⇒ the lone leg waits alone ⇒ BrokenBarrierError
            return ("OK", f"{leg}\nAGREE")

        return spawn

    def test_invoke_board_max_concurrency_1_is_sequential(self) -> None:
        # With a single worker, N seats can NEVER be in-flight at once, so the
        # Barrier(N) is unsatisfiable → every leg's wait() breaks → fail-closed
        # DEGRADED. That DEGRADED result IS the proof the seats never overlapped.
        n = len(DEFAULT_BOARD.seats)
        res = pi.invoke_board(
            DEFAULT_BOARD, "artifact", spawn=self._unsatisfiable_barrier_spawn(n), max_concurrency=1
        )
        self.assertTrue(
            all(r.status == "DEGRADED" for r in res.legs),
            f"max_concurrency=1 must serialize (barrier unsatisfiable ⇒ DEGRADED): "
            f"{[(r.leg, r.status) for r in res.legs]}",
        )
        self.assertEqual([r.leg for r in res.legs], list(pi.PANEL_LEGS))  # order preserved

    def test_invoke_panel_max_concurrency_1_is_sequential(self) -> None:
        n = len(pi.PANEL_LEGS)
        res = pi.invoke_panel(
            "artifact", pi.PANEL_LEGS, spawn=self._unsatisfiable_barrier_spawn(n), max_concurrency=1
        )
        self.assertTrue(
            all(r.status == "DEGRADED" for r in res.legs),
            f"max_concurrency=1 must serialize (barrier unsatisfiable ⇒ DEGRADED): "
            f"{[(r.leg, r.status) for r in res.legs]}",
        )
        self.assertEqual([r.leg for r in res.legs], list(pi.PANEL_LEGS))  # order preserved

    def test_max_concurrency_1_preserves_order_and_results(self) -> None:
        # Sequential mode still produces the same ordered, byte-identical results as
        # the parallel default — concurrency is a timing knob, never an outcome one.
        reply = lambda leg, art: ("OK", f"{leg}\nAGREE")
        par = pi.invoke_board(DEFAULT_BOARD, "artifact", spawn=reply)
        seq = pi.invoke_board(DEFAULT_BOARD, "artifact", spawn=reply, max_concurrency=1)
        self.assertEqual([r.leg for r in seq.legs], list(pi.PANEL_LEGS))
        self.assertTrue(all(r.status == "OK" for r in seq.legs))
        for p, s in zip(par.legs, seq.legs):
            self.assertEqual((p.leg, p.status, p.text, p.seat_key), (s.leg, s.status, s.text, s.seat_key))


if __name__ == "__main__":
    unittest.main()
