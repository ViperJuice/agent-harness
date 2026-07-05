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
    def test_four_named_presets(self) -> None:
        self.assertEqual(set(PRESET_NAMES), {"default", "code-review", "brainstorm", "doc-edit"})

    def test_default_preset_is_the_shared_default_board_fixture(self) -> None:
        # Identity, not equality: the preset reconstructs today's exact 3 seats.
        self.assertIs(PRESETS["default"], DEFAULT_BOARD)
        self.assertIs(get_preset("default"), DEFAULT_BOARD)
        self.assertEqual(
            tuple((s.model, s.effort, s.harness) for s in PRESETS["default"].seats),
            (
                ("gpt-5.5", "max", "codex"),
                ("Gemini 3.1 Pro", "high", "gemini"),
                ("claude-sonnet-5", "max", "claude"),
            ),
        )

    def test_all_presets_default_to_no_api_key_fallback(self) -> None:
        for name in PRESET_NAMES:
            self.assertFalse(PRESETS[name].allow_api_key_fallback, name)

    def test_code_review_preset_matches_example_toml(self) -> None:
        seats = get_preset("code-review").seats
        self.assertEqual((seats[0].model, seats[0].harness), ("gpt-5.5", "codex"))
        self.assertEqual(seats[1].lens, "adversarial")

    def test_unknown_preset_raises_with_known_list(self) -> None:
        with self.assertRaises(KeyError):
            get_preset("no-such-preset")


if __name__ == "__main__":
    unittest.main()
