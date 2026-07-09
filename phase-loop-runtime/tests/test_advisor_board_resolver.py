"""ABDRESOLVE — board resolution, seat validation, leg->seat re-key, back-compat.

Codes against the FROZEN ABDFREEZE interfaces + the shared canonical fixtures
(``advisor_board.fixtures``) and the fixture-backed stand-in registries
(``advisor_board.standin``). No dependency on ABDREG's live output — integration
is at ABDVERIFY.
"""
from __future__ import annotations

import unittest

from phase_loop_runtime import panel_invoker as pi
from phase_loop_runtime.advisor_board import (
    DEFAULT_BOARD,
    TWO_SAME_VENDOR_BOARD,
    Board,
    BoardResolutionError,
    BoardResolver,
    Seat,
    SeatSpecError,
    SeatValidationError,
    key_results_by_seat,
    parse_seat_spec,
    parse_seats,
    render_seat_invocation,
    resolve_board,
    validate_board,
    validate_seat,
)
from phase_loop_runtime.advisor_board.standin import (
    StandinCompatibilityMatrix,
    StandinModelRegistry,
)

MATRIX = StandinCompatibilityMatrix()
MODELS = StandinModelRegistry()


# --- Lane 1: resolver -------------------------------------------------------


class BoardResolverTests(unittest.TestCase):
    def test_bare_resolves_to_default_board(self) -> None:
        self.assertEqual(BoardResolver().resolve(), DEFAULT_BOARD)
        self.assertEqual(BoardResolver().resolve(None), DEFAULT_BOARD)
        self.assertEqual(BoardResolver().resolve(""), DEFAULT_BOARD)

    def test_named_board_resolves(self) -> None:
        board = BoardResolver().resolve("code-review")
        self.assertEqual(board.name, "code-review")
        self.assertEqual(board.purpose, "code-review")
        self.assertTrue(len(board.seats) >= 1)

    def test_advisor_panel_is_a_working_alias_of_default(self) -> None:
        # The historical name keeps resolving after the rename.
        self.assertEqual(BoardResolver().resolve("advisor-panel"), DEFAULT_BOARD)
        self.assertEqual(BoardResolver().resolve("panel"), DEFAULT_BOARD)

    def test_settable_default_board(self) -> None:
        resolver = BoardResolver()
        resolver.set_default("code-review")
        self.assertEqual(resolver.default_board, "code-review")
        self.assertEqual(resolver.resolve().name, "code-review")

    def test_settable_default_rejects_unknown(self) -> None:
        with self.assertRaises(BoardResolutionError):
            BoardResolver().set_default("nope")

    def test_unknown_board_lists_available(self) -> None:
        with self.assertRaises(BoardResolutionError) as ctx:
            BoardResolver().resolve("ghost")
        msg = str(ctx.exception)
        self.assertIn("ghost", msg)
        self.assertIn("default", msg)
        self.assertIn("code-review", msg)

    def test_injected_catalog_and_matrix(self) -> None:
        catalog = {"only": Board(name="only", purpose="p", seats=DEFAULT_BOARD.seats)}
        resolver = BoardResolver(catalog, default_board="only", matrix=MATRIX)
        self.assertEqual(resolver.resolve().name, "only")

    def test_convenience_resolve_board(self) -> None:
        self.assertEqual(resolve_board(), DEFAULT_BOARD)
        self.assertEqual(resolve_board("advisor-panel"), DEFAULT_BOARD)


class AdHocSeatParsingTests(unittest.TestCase):
    def test_model_effort_harness(self) -> None:
        seat = parse_seat_spec("gpt-5.6-sol:max:codex")
        self.assertEqual((seat.model, seat.effort, seat.harness), ("gpt-5.6-sol", "max", "codex"))

    def test_model_effort_defaults_lane_via_matrix(self) -> None:
        seat = parse_seat_spec("gpt-5.6-sol:high", matrix=MATRIX)
        self.assertEqual(seat.harness, "codex")  # default_lane(gpt-5.6-sol)

    def test_model_effort_without_matrix_leaves_lane_unresolved(self) -> None:
        seat = parse_seat_spec("gpt-5.6-sol:high")
        self.assertIsNone(seat.harness)

    def test_bad_effort_rejected(self) -> None:
        with self.assertRaises(SeatSpecError):
            parse_seat_spec("gpt-5.6-sol:turbo")

    def test_malformed_specs_rejected(self) -> None:
        for bad in ("", "gpt-5.6-sol", "gpt-5.6-sol:max:codex:extra", ":max:codex"):
            with self.subTest(bad=bad), self.assertRaises(SeatSpecError):
                parse_seat_spec(bad)

    def test_parse_seats_list_and_csv_preserve_order(self) -> None:
        csv = parse_seats("gpt-5.6-sol:high:codex, gpt-5.6-sol:high:opencode")
        lst = parse_seats(["gpt-5.6-sol:high:codex", "gpt-5.6-sol:high:opencode"])
        self.assertEqual(csv, lst)
        self.assertEqual(tuple(s.harness for s in csv), ("codex", "opencode"))

    def test_resolver_synthesizes_ad_hoc_board(self) -> None:
        board = BoardResolver(matrix=MATRIX).resolve(seats="gpt-5.6-sol:high:codex,claude-sonnet-5:max:claude")
        self.assertEqual(board.name, "ad-hoc")
        self.assertEqual(len(board.seats), 2)


# --- Lane 2: validation -----------------------------------------------------


class SeatValidationTests(unittest.TestCase):
    def test_valid_seat_returns_verdict_with_auth(self) -> None:
        verdict = validate_seat(Seat(model="gpt-5.6-sol", effort="max", harness="codex"), MATRIX)
        self.assertEqual(verdict.harness, "codex")
        self.assertTrue(verdict.auth.subscription)

    def test_same_vendor_different_harness_is_valid(self) -> None:
        verdict = validate_seat(Seat(model="gpt-5.6-sol", effort="high", harness="opencode"), MATRIX)
        self.assertEqual(verdict.harness, "opencode")

    def test_invalid_pairing_fails_fast_with_actionable_diagnostic(self) -> None:
        seat = Seat(model="gpt-5.6-sol", effort="max", harness="claude")
        with self.assertRaises(SeatValidationError) as ctx:
            validate_seat(seat, MATRIX, models=MODELS)
        msg = str(ctx.exception)
        self.assertIn("gpt-5.6-sol", msg)
        self.assertIn("claude", msg)
        self.assertIn("codex", msg)  # did-you-mean: the lanes gpt-5.6-sol runs on

    def test_bare_seat_resolves_lane_via_matrix_default(self) -> None:
        # harness omitted -> matrix.default_lane resolves it before validation.
        verdict = validate_seat(Seat(model="claude-sonnet-5", effort="max"), MATRIX)
        self.assertEqual(verdict.harness, "claude")

    def test_validate_board_aggregates_all_offenders(self) -> None:
        bad = Board(
            name="bad",
            purpose="p",
            seats=(
                Seat(model="gpt-5.6-sol", effort="max", harness="claude"),
                Seat(model="claude-sonnet-5", effort="max", harness="codex"),
            ),
        )
        with self.assertRaises(SeatValidationError) as ctx:
            validate_board(bad, MATRIX, models=MODELS)
        self.assertEqual(len(ctx.exception.seat_errors), 2)

    def test_default_board_validates_clean(self) -> None:
        verdicts = validate_board(DEFAULT_BOARD, MATRIX)
        self.assertEqual(len(verdicts), 3)

    def test_effort_ceiling_gate_rejects_over_ceiling_seat(self) -> None:
        # The effort-ceiling gate folded in from matrix.validate_seat: a seat above
        # its model's ceiling is rejected. Every *real* model ceilings at "max"
        # (the ladder top), so the gate is exercised here with a stand-in registry
        # that caps gpt-5.6-sol at "high" — proving the fold enforces, not just parses.
        from phase_loop_runtime.advisor_board.registries import ModelSpec

        class _CappedModels:
            def get(self, model: str) -> ModelSpec:
                return ModelSpec(
                    model=model,
                    vendor_family="openai",
                    default_lane="codex",
                    runnable_by=("codex", "opencode"),
                    effort_ceiling="high",
                )

            def default_lane(self, model: str) -> str:
                return "codex"

        capped = _CappedModels()
        # at-ceiling passes …
        ok = validate_seat(Seat(model="gpt-5.6-sol", effort="high", harness="codex"), MATRIX, models=capped)
        self.assertEqual(ok.harness, "codex")
        # … over-ceiling rejects with an actionable message.
        with self.assertRaises(SeatValidationError) as ctx:
            validate_seat(Seat(model="gpt-5.6-sol", effort="max", harness="codex"), MATRIX, models=capped)
        msg = str(ctx.exception)
        self.assertIn("exceeds", msg)
        self.assertIn("high", msg)


# --- Lane 4: leg->seat re-key + back-compat proof ---------------------------


class LegToSeatRekeyTests(unittest.TestCase):
    def test_panel_leg_result_seat_key_defaults_to_leg(self) -> None:
        # Back-compat: seat_key defaults to leg so the default board is unchanged.
        result = pi.PanelLegResult(leg="codex", status="OK", text="AGREE")
        self.assertEqual(result.seat_key, "codex")

    def test_two_same_vendor_seats_are_expressible(self) -> None:
        # Both seats project to the codex vendor family, so leg alone (== "codex")
        # cannot tell them apart; seat_key does. A PanelResult can now hold two
        # same-vendor legs distinctly — the expressibility the re-key delivers.
        seats = TWO_SAME_VENDOR_BOARD.seats
        self.assertEqual(len({s.seat_key for s in seats}), 2)
        results = tuple(
            pi.PanelLegResult(leg=s.harness, status="OK", text="AGREE", seat_key=s.seat_key)
            for s in seats
        )
        panel = pi.PanelResult(legs=results)
        # Both are the codex family, yet the two results are distinct by seat_key.
        self.assertEqual([r.seat_key for r in panel.legs], [s.seat_key for s in seats])
        self.assertEqual(len({r.seat_key for r in panel.legs}), 2)

    def test_key_results_by_seat_pairs_by_position(self) -> None:
        seats = TWO_SAME_VENDOR_BOARD.seats
        results = tuple(
            pi.PanelLegResult(leg=s.harness, status="OK", text="ok", seat_key=s.seat_key)
            for s in seats
        )
        paired = key_results_by_seat(seats, results)
        self.assertEqual(len(paired), 2)
        for seat, result in paired:
            self.assertEqual(seat.seat_key, result.seat_key)

    def test_key_results_by_seat_rejects_count_mismatch(self) -> None:
        seats = TWO_SAME_VENDOR_BOARD.seats
        with self.assertRaises(ValueError):
            key_results_by_seat(seats, (pi.PanelLegResult(leg="codex", status="OK", text="x"),))

    def test_invoke_panel_request_reconciled_as_entry_point(self) -> None:
        request = pi.PanelRequest(artifact="review me", legs=("codex", "gemini", "claude"))
        panel = pi.invoke_panel_request(request, spawn=lambda leg, art: ("OK", f"{leg}: AGREE"))
        self.assertEqual([r.leg for r in panel.legs], ["codex", "gemini", "claude"])
        self.assertTrue(all(r.usable for r in panel.legs))


class DefaultBoardBackCompatResolutionTests(unittest.TestCase):
    """Resolution-level proof: the `default` board reproduces today's 3-leg panel
    (the full golden proof is ABDFREEZE-4 / ABDVERIFY)."""

    def test_default_and_alias_and_bare_all_resolve_to_default(self) -> None:
        resolver = BoardResolver()
        self.assertEqual(resolver.resolve("default"), DEFAULT_BOARD)
        self.assertEqual(resolver.resolve("advisor-panel"), DEFAULT_BOARD)
        self.assertEqual(resolver.resolve(), DEFAULT_BOARD)

    def test_resolved_default_reproduces_todays_three_legs_in_order(self) -> None:
        board = BoardResolver().resolve()
        # seats project to the exact PANEL_LEGS vendor order, each rendering to
        # today's live DEFAULT_LEG_MODELS string + effort form.
        self.assertEqual(
            tuple(s.vendor_family for s in board.seats), pi.PANEL_LEGS
        )
        for seat in board.seats:
            inv = render_seat_invocation(seat.harness, seat.model, seat.effort)
            self.assertEqual(inv.model, pi.DEFAULT_LEG_MODELS[seat.vendor_family])

    def test_resolved_default_maps_one_seat_per_leg(self) -> None:
        # The default board is 1 seat per vendor, so leg-keying and seat-keying
        # coincide — the byte-equivalence anchor for back-compat.
        board = BoardResolver().resolve()
        legs = [s.harness for s in board.seats]
        panel = pi.invoke_panel("a", legs=legs, spawn=lambda leg, art: ("OK", f"{leg} AGREE"))
        # no seat_keys passed -> seat_key defaults to leg, exactly as today.
        self.assertEqual([r.seat_key for r in panel.legs], [r.leg for r in panel.legs])


if __name__ == "__main__":
    unittest.main()
