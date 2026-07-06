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
    get_preset,
)


class PresetTests(unittest.TestCase):
    def test_seven_named_presets(self) -> None:
        self.assertEqual(
            set(PRESET_NAMES),
            {
                "default", "code-review", "brainstorm", "doc-edit",
                "legal-review", "legal-strategy-review", "legal-brainstorm",
            },
        )

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

    def test_brainstorm_and_doc_edit_keep_sonnet(self) -> None:
        # The divergent-thinking boards deliberately KEEP Sonnet (diverse voice /
        # low-stakes copyedit) — unchanged by the Fable review-path fix.
        for name in ("brainstorm", "doc-edit"):
            models = {s.model for s in PRESETS[name].seats}
            self.assertIn("claude-sonnet-5", models, name)

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
