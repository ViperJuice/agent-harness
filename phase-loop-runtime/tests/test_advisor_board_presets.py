"""ABDREG — board presets (Phase 2, lane 4).

The keystone: the ``default`` preset IS today's three seats (it is the shared
``DEFAULT_BOARD`` fixture by identity, not a re-declared copy), so back-compat
holds by construction.
"""
from __future__ import annotations

import unittest

from phase_loop_runtime.advisor_board import (
    DEFAULT_BOARD,
    PRESET_NAMES,
    PRESETS,
    default_matrix,
    get_preset,
    validate_board,
)


class PresetTests(unittest.TestCase):
    def test_nine_named_presets(self) -> None:
        self.assertEqual(
            set(PRESET_NAMES),
            {
                "default", "code-review", "brainstorm", "doc-edit",
                "legal-review", "legal-strategy-review", "legal-brainstorm",
                "general", "solo",
            },
        )

    def test_general_and_solo_are_top_end_catch_alls(self) -> None:
        # general = 3 frontier vendors (domain-agnostic); solo = 1 top-end member.
        general = PRESETS["general"]
        self.assertEqual(len(general.seats), 3)
        self.assertEqual(
            {s.model for s in general.seats},
            {"gpt-5.5", "Gemini 3.1 Pro", "claude-fable-5"},
        )
        solo = PRESETS["solo"]
        self.assertEqual(len(solo.seats), 1)  # a 1-seat board is fully valid
        self.assertEqual(solo.seats[0].model, "claude-fable-5")
        # neither seats Sonnet — an unmodeled task is not assumed low-stakes.
        for board in (general, solo):
            self.assertNotIn("claude-sonnet-5", {s.model for s in board.seats})
            self.assertEqual(board.purpose, "general")
            validate_board(board, default_matrix())  # both pass matrix validation

    def test_default_preset_is_the_shared_default_board_fixture(self) -> None:
        # Identity, not equality: the preset reconstructs today's exact 3 seats.
        # The claude seat runs Fable (review-path model), not the implementer.
        self.assertIs(PRESETS["default"], DEFAULT_BOARD)
        self.assertIs(get_preset("default"), DEFAULT_BOARD)
        self.assertEqual(
            tuple((s.model, s.effort, s.harness) for s in PRESETS["default"].seats),
            (
                ("gpt-5.5", "max", "codex"),
                ("Gemini 3.1 Pro", "high", "gemini"),
                ("claude-fable-5", "max", "claude"),
            ),
        )

    def test_all_presets_default_to_no_api_key_fallback(self) -> None:
        for name in PRESET_NAMES:
            self.assertFalse(PRESETS[name].allow_api_key_fallback, name)

    def test_code_review_is_three_adversarial_frontier_seats(self) -> None:
        # review-class = three frontier vendors, always. Each seat adversarial;
        # the claude seat is Fable, not the implementer sonnet.
        seats = get_preset("code-review").seats
        self.assertEqual(
            tuple((s.model, s.effort, s.harness, s.lens) for s in seats),
            (
                ("gpt-5.5", "max", "codex", "adversarial"),
                ("Gemini 3.1 Pro", "high", "gemini", "adversarial"),
                ("claude-fable-5", "max", "claude", "adversarial"),
            ),
        )

    def test_review_class_boards_seat_fable_not_the_implementer(self) -> None:
        # The review-class coding + legal boards never seat claude-sonnet-5.
        for name in ("default", "code-review", "legal-review", "legal-strategy-review"):
            claude_seats = [s for s in PRESETS[name].seats if s.harness == "claude"]
            self.assertTrue(claude_seats, name)
            for s in claude_seats:
                self.assertEqual(s.model, "claude-fable-5", f"{name}: {s.model}")

    def test_brainstorm_and_doc_edit_are_byte_neutral(self) -> None:
        # The divergent-thinking boards deliberately KEEP Sonnet and are otherwise
        # UNCHANGED by the Fable review-path fix. Pin the full seat tuples (model /
        # effort / harness / lens) so any drift — not just a dropped Sonnet — trips.
        unchanged = {
            "brainstorm": (
                ("claude-sonnet-5", "high", "claude", "adversarial"),
                ("gpt-5.5", "high", "codex", "supportive"),
                ("Gemini 3.1 Pro", "high", "gemini", "lateral"),
            ),
            "doc-edit": (
                ("claude-sonnet-5", "medium", "claude", "copyedit"),
                ("gpt-5.5", "medium", "codex", "structure"),
            ),
        }
        for name, expected in unchanged.items():
            self.assertEqual(
                tuple((s.model, s.effort, s.harness, s.lens) for s in PRESETS[name].seats),
                expected,
                name,
            )

    def test_legal_boards_present_and_shaped(self) -> None:
        legal = {
            "legal-review": (
                ("gpt-5.5", "max", "codex", "opposing-counsel"),
                ("Gemini 3.1 Pro", "high", "gemini", "risk-liability"),
                ("claude-fable-5", "max", "claude", "authority-verification"),
            ),
            "legal-strategy-review": (
                ("gpt-5.5", "max", "codex", "red-team"),
                ("Gemini 3.1 Pro", "high", "gemini", "alternatives"),
                ("claude-fable-5", "max", "claude", "downside-ethics"),
            ),
            "legal-brainstorm": (
                ("claude-sonnet-5", "high", "claude", "aggressive"),
                ("gpt-5.5", "high", "codex", "conservative"),
                ("Gemini 3.1 Pro", "high", "gemini", "creative"),
            ),
        }
        for name, expected in legal.items():
            board = get_preset(name)
            self.assertEqual(board.purpose, name)
            self.assertEqual(
                tuple((s.model, s.effort, s.harness, s.lens) for s in board.seats),
                expected,
                name,
            )

    def test_unknown_preset_raises_with_known_list(self) -> None:
        with self.assertRaises(KeyError):
            get_preset("no-such-preset")


if __name__ == "__main__":
    unittest.main()
