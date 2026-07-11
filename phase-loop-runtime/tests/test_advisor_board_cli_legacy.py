"""LEGACY (CLEANSHIP P7) — `phase-loop advisor-board <artifact>` is the RUNNABLE
agent-facing default for the 4-vendor board.

Pins that the CLI subcommand:
  - composes AVAILABILITY-AWARE via `compose_review_board` (REVIEWGOV IF-0-REVIEWGOV-1),
  - dispatches through `invoke_board` (NOT the legacy `invoke_panel`), staging the
    artifact by-reference, and
  - fails closed on a missing artifact / an empty (no authed vendor) board.

Hermetic: the real `compose_review_board` default shells out to `default_board_auth_ok`
(live auth probes), so the tests inject `is_available` (auth defaults to pass-through
per the documented test affordance) to build a real board without shelling out, and
patch `invoke_board` so no vendor CLI is spawned.
"""
from __future__ import annotations

import tempfile
import unittest
import unittest.mock
from pathlib import Path

from phase_loop_runtime import panel_invoker as pi_mod
from phase_loop_runtime.advisor_board import composition as comp_mod
from phase_loop_runtime.cli import main as cli_main
from phase_loop_runtime.panel_invoker import PanelLegResult, PanelResult


# Bind the REAL composer at import time so the hermetic helper never re-enters the
# patched name (which would recurse infinitely under side_effect).
_REAL_COMPOSE = comp_mod.compose_review_board


def _hermetic_board(*_a, **_k):
    # Real composition, injected availability → auth pass-through (no live probe).
    return _REAL_COMPOSE(is_available=lambda v: v in {"codex", "gemini", "claude", "grok"})


# A realistic composed-board result: 4 seats, 3 usable OK verdicts + the claude leg
# deferring to a native Agent (UNAVAILABLE) — exactly the Claude-Code shape. Usable
# count 3 == FLOOR_SEATS, so this is a usable board (exit 0).
_CANNED = PanelResult(
    legs=(
        PanelLegResult(leg="grok", status="OK", text="AGREE", seat_key="grok:adversarial"),
        PanelLegResult(leg="codex", status="OK", text="PARTIALLY AGREE", seat_key="codex:red-team"),
        PanelLegResult(leg="gemini", status="OK", text="AGREE", seat_key="gemini:alt"),
        PanelLegResult(leg="claude", status="UNAVAILABLE", text="", detail="deferred to native Agent", seat_key="claude:corr"),
    )
)


class AdvisorBoardCliTest(unittest.TestCase):
    def test_cli_composes_auth_aware_and_dispatches_board(self):
        with tempfile.TemporaryDirectory() as td:
            artifact = Path(td) / "bundle.md"
            artifact.write_text("review me\n")
            with unittest.mock.patch.object(
                comp_mod, "compose_review_board", side_effect=_hermetic_board
            ) as compose_spy, unittest.mock.patch.object(
                pi_mod, "invoke_board", return_value=_CANNED
            ) as invoke_spy:
                rc = cli_main(["advisor-board", str(artifact)])
            self.assertEqual(rc, 0)
            # The board path is the entry — availability-aware composition, then dispatch.
            # No-kwargs pin (Fable nit): the CLI must call compose_review_board with NO
            # arguments so it relies on the auth-aware production default
            # (auth_ok=default_board_auth_ok). Passing a predicate here would silently
            # opt into the PATH-only test-affordance. This guards that default.
            compose_spy.assert_called_once_with()
            invoke_spy.assert_called_once()
            # The artifact is staged BY REFERENCE (absolute path) into the board.
            _pos, kwargs = invoke_spy.call_args
            self.assertEqual(kwargs.get("artifact_ref"), str(artifact.resolve()))
            # Write boundary (item 5): the spawn cwd is constrained to a scratch dir,
            # not the process CWD.
            self.assertIsNotNone(kwargs.get("repo_dir"))
            self.assertNotEqual(Path(kwargs["repo_dir"]).resolve(), Path.cwd().resolve())
            # It dispatched the composed board (invoke_board's first positional).
            self.assertTrue(getattr(invoke_spy.call_args.args[0], "seats", None))

    def test_compose_drops_unauthed_vendor_at_the_seam(self):
        # Item 1: the auth-aware seam the CLI uses drops an on-PATH-but-UNAUTHED vendor
        # at COMPOSE and backfills onto authed vendors (board stays 4 seats, all authed
        # families). Exercises compose_review_board(auth_ok=...) directly (hermetic).
        board = comp_mod.compose_review_board(
            is_available=lambda v: True,  # every vendor on PATH
            auth_ok=lambda v: v != "grok",  # ...but grok is NOT authenticated
        )
        families = {seat.harness for seat in board.seats}
        self.assertNotIn("grok", families, "an unauthed on-PATH vendor must be dropped at compose")
        self.assertEqual(len(board.seats), 4, "the freed seat must be backfilled to a full board")

    def test_cli_below_floor_board_exits_nonzero(self):
        # Item 4: a board with FEWER than FLOOR_SEATS (3) usable OK legs is below its
        # independence floor → nonzero exit (pins floor semantics, not just zero-usable):
        # here 2 OK + 2 failed = 2 usable < 3.
        with tempfile.TemporaryDirectory() as td:
            artifact = Path(td) / "bundle.md"
            artifact.write_text("x\n")
            below_floor = PanelResult(
                legs=(
                    PanelLegResult(leg="grok", status="OK", text="AGREE", seat_key="grok:adv"),
                    PanelLegResult(leg="codex", status="OK", text="DISAGREE", seat_key="codex:red"),
                    PanelLegResult(leg="gemini", status="DEGRADED", text="", detail="capped", seat_key="gemini:alt"),
                    PanelLegResult(leg="claude", status="UNAVAILABLE", text="", detail="deferred", seat_key="claude:corr"),
                )
            )
            with unittest.mock.patch.object(
                comp_mod, "compose_review_board", side_effect=_hermetic_board
            ), unittest.mock.patch.object(pi_mod, "invoke_board", return_value=below_floor):
                rc = cli_main(["advisor-board", str(artifact)])
            self.assertEqual(rc, 1, "usable legs below the floor → nonzero exit")

    def test_cli_json_emits_independence_and_legs(self):
        import json

        with tempfile.TemporaryDirectory() as td:
            artifact = Path(td) / "bundle.md"
            artifact.write_text("review me\n")
            with unittest.mock.patch.object(
                comp_mod, "compose_review_board", side_effect=_hermetic_board
            ), unittest.mock.patch.object(pi_mod, "invoke_board", return_value=_CANNED):
                import contextlib
                import io

                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = cli_main(["advisor-board", str(artifact), "--json"])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertIn("independence", payload)
            # 3 OK legs == FLOOR_SEATS → usable (the 4th, claude, defers UNAVAILABLE).
            self.assertTrue(payload["usable"])
            self.assertEqual(
                [leg["status"] for leg in payload["legs"]], ["OK", "OK", "OK", "UNAVAILABLE"]
            )
            # The reviewer's actual verdict TEXT must be preserved (CR codex, major):
            # a board whose output drops the verdicts cannot be reconciled.
            self.assertEqual(
                [leg["text"] for leg in payload["legs"]], ["AGREE", "PARTIALLY AGREE", "AGREE", ""]
            )

    def test_cli_human_output_includes_verdict_text(self):
        import contextlib
        import io

        with tempfile.TemporaryDirectory() as td:
            artifact = Path(td) / "bundle.md"
            artifact.write_text("review me\n")
            with unittest.mock.patch.object(
                comp_mod, "compose_review_board", side_effect=_hermetic_board
            ), unittest.mock.patch.object(pi_mod, "invoke_board", return_value=_CANNED):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = cli_main(["advisor-board", str(artifact)])
            self.assertEqual(rc, 0)
            out = buf.getvalue()
            self.assertIn("AGREE", out)
            self.assertIn("PARTIALLY AGREE", out)

    def test_cli_missing_artifact_fails_closed(self):
        rc = cli_main(["advisor-board", "/no/such/artifact.md"])
        self.assertEqual(rc, 2)

    def test_cli_empty_board_fails_closed(self):
        # No vendor both available and authed → empty board → nothing to compose.
        with tempfile.TemporaryDirectory() as td:
            artifact = Path(td) / "bundle.md"
            artifact.write_text("x\n")
            empty = _REAL_COMPOSE(is_available=lambda v: False)
            with unittest.mock.patch.object(
                comp_mod, "compose_review_board", return_value=empty
            ), unittest.mock.patch.object(pi_mod, "invoke_board") as invoke_spy:
                rc = cli_main(["advisor-board", str(artifact)])
            self.assertEqual(rc, 2)
            invoke_spy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
