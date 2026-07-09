"""ABDFREEZE — seat/board schema, vendor projection, effort mapping, host-leg,
config location, and observability envelope (IF-0-ABDFREEZE-1, -3, -5).

The load-bearing assertions here are the ones downstream parallelism depends on:
the seat -> vendor-family projection must be BYTE-CONSISTENT with the existing
governed-gate logic, and the effort mapping must round-trip the built-3 to their
canonical forms.
"""
from __future__ import annotations

import unittest

from phase_loop_runtime.advisor_board import (
    AdvisorBoardEvent,
    AUTH_API_KEY,
    Board,
    HostContext,
    NullSink,
    Seat,
    best_effort_forward,
    board_config_path,
    identify_host_leg,
    render_gemini_model,
    render_seat_invocation,
    seat_vendor_family,
    vendor_family,
    vendor_of_harness,
    vendor_of_model,
)
from phase_loop_runtime.advisor_board.harness_mapping import (
    EffortMappingError,
    MECH_CONFIG,
    MECH_FLAG,
    MECH_MODEL_NAME,
)
from phase_loop_runtime import governed_review as gr


class SeatBoardValidationTests(unittest.TestCase):
    def test_seat_requires_model_and_valid_enums(self) -> None:
        with self.assertRaises(ValueError):
            Seat(model="", effort="max")
        with self.assertRaises(ValueError):
            Seat(model="gpt-5.6-sol", effort="turbo")  # not an EFFORT_LEVEL
        with self.assertRaises(ValueError):
            Seat(model="gpt-5.6-sol", effort="max", auth="token")
        with self.assertRaises(ValueError):
            Seat(model="gpt-5.6-sol", effort="max", backing="cloud")

    def test_seat_defaults_are_subscription_homebrew_subprocess(self) -> None:
        seat = Seat(model="gpt-5.6-sol", effort="max", harness="codex")
        self.assertEqual(seat.auth, "subscription")
        self.assertEqual(seat.backing, "homebrew")
        self.assertFalse(seat.host_leg)

    def test_board_rejects_api_key_seat_without_optin(self) -> None:
        seat = Seat(model="gpt-5.6-sol", effort="max", harness="codex", auth=AUTH_API_KEY)
        with self.assertRaises(ValueError):
            Board(name="b", purpose="p", seats=(seat,))
        # opt-in makes it expressible
        Board(name="b", purpose="p", seats=(seat,), allow_api_key_fallback=True)

    def test_seat_key_distinguishes_same_vendor_seats(self) -> None:
        a = Seat(model="gpt-5.6-sol", effort="high", harness="codex")
        b = Seat(model="gpt-5.6-sol", effort="high", harness="opencode")
        self.assertNotEqual(a.seat_key, b.seat_key)

    def test_seat_key_distinguishes_lens_only_difference(self) -> None:
        # a natural brainstorm board: same model/harness/effort, different lens
        adv = Seat(model="claude-sonnet-5", effort="max", harness="claude", lens="adversarial")
        sup = Seat(model="claude-sonnet-5", effort="max", harness="claude", lens="supportive")
        self.assertNotEqual(adv.seat_key, sup.seat_key)
        # a lens-less seat has no trailing lens segment
        plain = Seat(model="claude-sonnet-5", effort="max", harness="claude")
        self.assertFalse(plain.seat_key.endswith(":None"))


class VendorProjectionByteConsistencyTests(unittest.TestCase):
    """The projection MUST reproduce the existing governed-gate vendor logic."""

    def test_vendor_of_model_matches_author_vendor_for_model(self) -> None:
        for model in [
            "claude-sonnet-5", "claude-opus-4-8", "opus", "sonnet", "haiku",
            "gemini", "Gemini 3.1 Pro", "pro", "flash", "flash-lite", "auto",
            "gpt-5.6-sol", "gpt-4", "o1", "o3", "openai/gpt-5.6-sol",
            "some-unknown-model", "",
        ]:
            # author_vendor_for_model returns the family, or the bare model when
            # inconclusive; vendor_of_model returns "" when inconclusive, so the
            # equivalence is `vendor_of_model(m) or m.lower()`.
            expected = gr.author_vendor_for_model(model)
            got = vendor_of_model(model) or (model or "").lower()
            self.assertEqual(got, expected, f"model={model!r}")

    def test_vendor_of_harness_matches_author_vendor_for_executor(self) -> None:
        for harness in ["codex", "opencode", "claude", "gemini", "pi", "cursor", ""]:
            self.assertEqual(
                vendor_of_harness(harness),
                gr.author_vendor_for_executor(harness),
                f"harness={harness!r}",
            )

    def test_same_vendor_across_harnesses_projects_identically(self) -> None:
        codex_seat = Seat(model="gpt-5.6-sol", effort="high", harness="codex")
        opencode_seat = Seat(model="gpt-5.6-sol", effort="high", harness="opencode")
        self.assertEqual(seat_vendor_family(codex_seat), "codex")
        self.assertEqual(seat_vendor_family(opencode_seat), "codex")
        self.assertEqual(codex_seat.vendor_family, opencode_seat.vendor_family)

    def test_model_first_wins_over_harness_lane(self) -> None:
        # an anthropic model forced onto the codex lane still projects to claude
        self.assertEqual(vendor_family("claude-sonnet-5", "codex"), "claude")
        # an inconclusive model falls back to the harness lane
        self.assertEqual(vendor_family("mystery", "opencode"), "codex")
        # no model, no harness -> lowercased model
        self.assertEqual(vendor_family("Mystery", None), "mystery")


class EffortMappingRoundTripTests(unittest.TestCase):
    def test_claude_effort_flag(self) -> None:
        inv = render_seat_invocation("claude", "claude-sonnet-5", "max")
        self.assertEqual(inv.mechanism, MECH_FLAG)
        self.assertEqual(inv.model, "claude-sonnet-5")
        self.assertEqual(inv.effort_args, ("--effort", "max"))

    def test_codex_effort_config_max_is_xhigh(self) -> None:
        inv = render_seat_invocation("codex", "gpt-5.6-sol", "max")
        self.assertEqual(inv.mechanism, MECH_CONFIG)
        self.assertEqual(inv.effort_args, ("-c", "model_reasoning_effort=xhigh"))

    def test_gemini_effort_baked_into_model_name(self) -> None:
        inv = render_seat_invocation("gemini", "Gemini 3.1 Pro", "high")
        self.assertEqual(inv.mechanism, MECH_MODEL_NAME)
        self.assertEqual(inv.model, "Gemini 3.1 Pro (High)")
        self.assertEqual(inv.effort_args, ())

    def test_gemini_render_is_idempotent_on_baked_model(self) -> None:
        # re-rendering an already-baked model must not double the embed
        self.assertEqual(render_gemini_model("Gemini 3.1 Pro (High)", "high"), "Gemini 3.1 Pro (High)")
        self.assertEqual(render_gemini_model("Gemini 3.1 Pro (Low)", "max"), "Gemini 3.1 Pro (Max)")

    def test_breadth_harness_raises_until_populated(self) -> None:
        with self.assertRaises(EffortMappingError):
            render_seat_invocation("opencode", "gpt-5.6-sol", "high")


class HostLegIdentityTests(unittest.TestCase):
    def test_standalone_runner_has_no_host_leg(self) -> None:
        board = Board(
            name="b", purpose="p",
            seats=(Seat(model="claude-sonnet-5", effort="max", harness="claude"),),
        )
        # host_harness=None (standalone Python runner) -> no host leg, today's path
        self.assertIsNone(identify_host_leg(board, HostContext(host_harness=None)))
        self.assertIsNone(identify_host_leg(board, None))

    def test_host_leg_identified_when_running_inside_harness(self) -> None:
        claude_seat = Seat(model="claude-sonnet-5", effort="max", harness="claude")
        codex_seat = Seat(model="gpt-5.6-sol", effort="max", harness="codex")
        board = Board(name="b", purpose="p", seats=(codex_seat, claude_seat))
        host = identify_host_leg(board, HostContext(host_harness="claude"))
        self.assertIs(host, claude_seat)

    def test_explicit_host_leg_marker_wins(self) -> None:
        first = Seat(model="claude-sonnet-5", effort="high", harness="claude")
        marked = Seat(model="claude-opus-4-8", effort="max", harness="claude", host_leg=True)
        board = Board(name="b", purpose="p", seats=(first, marked))
        host = identify_host_leg(board, HostContext(host_harness="claude"))
        self.assertIs(host, marked)


class ConfigLocationTests(unittest.TestCase):
    def test_respects_xdg_config_home(self) -> None:
        p = board_config_path({"XDG_CONFIG_HOME": "/tmp/xdg"})
        self.assertEqual(str(p), "/tmp/xdg/agent-harness/advisor-boards.toml")

    def test_defaults_to_dot_config(self) -> None:
        p = board_config_path({})
        self.assertTrue(str(p).endswith("/.config/agent-harness/advisor-boards.toml"))


class EventEnvelopeTests(unittest.TestCase):
    def test_event_rejects_unknown_kind(self) -> None:
        with self.assertRaises(ValueError):
            AdvisorBoardEvent(kind="seat.exploded", board="default", sequence=1, occurred_at="t")

    def test_event_to_json_is_stable_shape(self) -> None:
        ev = AdvisorBoardEvent(
            kind="seat.completed", board="default", sequence=3, occurred_at="2026-07-05T00:00:00Z",
            seat_key="codex:gpt-5.6-sol:max", vendor_family="codex", harness="codex",
            payload={"status": "OK"},
        )
        j = ev.to_json()
        self.assertEqual(j["schema_version"], "advisor_board.event.v1")
        self.assertEqual(j["kind"], "seat.completed")
        self.assertEqual(j["payload"], {"status": "OK"})

    def test_best_effort_forward_never_raises(self) -> None:
        class Boom:
            def emit(self, event):  # noqa: ANN001
                raise RuntimeError("sink down")

        ev = AdvisorBoardEvent(kind="board.started", board="default", sequence=0, occurred_at="t")
        self.assertFalse(best_effort_forward(Boom(), ev))   # swallowed
        self.assertFalse(best_effort_forward(None, ev))     # no sink
        self.assertTrue(best_effort_forward(NullSink(), ev))


if __name__ == "__main__":
    unittest.main()
