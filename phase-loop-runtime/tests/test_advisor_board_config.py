"""ABDREG — user-editable board config loader (Phase 2, lane 4).

Proves: presets load from config and self-validate; ``allow_api_key_fallback``
defaults false; unknown keys ERROR (never a silent drop); a matrix-invalid user
board is rejected at load time; the frozen example TOML parses; and the shipped
example fixture round-trips.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from phase_loop_runtime.advisor_board import (
    DEFAULT_BOARD,
    BoardConfigError,
    default_matrix,
    load_boards,
)

_ALL_PRESENT = lambda cli: True
_MATRIX = default_matrix(probe=_ALL_PRESENT, env={})


def _write(tmp: str, body: str) -> Path:
    p = Path(tmp) / "advisor-boards.toml"
    p.write_text(body, encoding="utf-8")
    return p


class PresetSelfValidationTests(unittest.TestCase):
    def test_load_with_no_user_config_self_validates_every_preset(self) -> None:
        # No file -> presets only. This is the guardrail: if any preset seat used
        # an unregistered model or an incompatible lane, this raises.
        cfg = load_boards(Path("/nonexistent/advisor-boards.toml"), matrix=_MATRIX)
        self.assertEqual(
            set(cfg.names()),
            {
                "default", "code-review", "brainstorm", "doc-edit",
                "legal-review", "legal-strategy-review", "legal-brainstorm",
            },
        )

    def test_default_board_resolves_to_todays_three_seats(self) -> None:
        cfg = load_boards(Path("/nonexistent/advisor-boards.toml"), matrix=_MATRIX)
        self.assertIs(cfg.get("default"), DEFAULT_BOARD)
        self.assertIs(cfg.get(), DEFAULT_BOARD)  # bare advisor-board -> default
        self.assertEqual(len(cfg.get().seats), 3)


class ConfigLoadTests(unittest.TestCase):
    def test_user_board_layers_over_presets(self) -> None:
        body = """
default_board = "mine"

[[boards]]
name = "mine"
purpose = "custom"

  [[boards.seats]]
  model = "claude-sonnet-5"
  effort = "high"
  harness = "claude"
"""
        with TemporaryDirectory() as tmp:
            cfg = load_boards(_write(tmp, body), matrix=_MATRIX)
        self.assertIn("mine", cfg.names())
        self.assertIn("default", cfg.names())  # presets still present
        self.assertEqual(cfg.default_board, "mine")
        self.assertEqual(cfg.get().name, "mine")

    def test_allow_api_key_fallback_defaults_false(self) -> None:
        body = """
[[boards]]
name = "sub-only"
purpose = "x"
  [[boards.seats]]
  model = "gpt-5.5"
  effort = "high"
  harness = "codex"
"""
        with TemporaryDirectory() as tmp:
            cfg = load_boards(_write(tmp, body), matrix=_MATRIX)
        self.assertFalse(cfg.get("sub-only").allow_api_key_fallback)

    def test_api_key_seat_without_optin_is_rejected(self) -> None:
        body = """
[[boards]]
name = "leaky"
purpose = "x"
  [[boards.seats]]
  model = "gpt-5.5"
  effort = "high"
  harness = "codex"
  auth = "api_key"
"""
        with TemporaryDirectory() as tmp:
            with self.assertRaises(BoardConfigError):
                load_boards(_write(tmp, body), matrix=_MATRIX)

    def test_api_key_seat_with_optin_is_accepted(self) -> None:
        body = """
[[boards]]
name = "keyed"
purpose = "x"
allow_api_key_fallback = true
  [[boards.seats]]
  model = "gpt-5.5"
  effort = "high"
  harness = "codex"
  auth = "api_key"
"""
        with TemporaryDirectory() as tmp:
            cfg = load_boards(_write(tmp, body), matrix=_MATRIX)
        self.assertTrue(cfg.get("keyed").allow_api_key_fallback)


class UnknownKeyErrorsTests(unittest.TestCase):
    def test_unknown_top_level_key_errors(self) -> None:
        body = 'wat = 1\n[[boards]]\nname="x"\npurpose="y"\n'
        with TemporaryDirectory() as tmp:
            with self.assertRaises(BoardConfigError) as ctx:
                load_boards(_write(tmp, body), matrix=_MATRIX)
        self.assertIn("wat", str(ctx.exception))

    def test_unknown_board_key_errors(self) -> None:
        body = '[[boards]]\nname="x"\npurpose="y"\ncolor="blue"\n'
        with TemporaryDirectory() as tmp:
            with self.assertRaises(BoardConfigError) as ctx:
                load_boards(_write(tmp, body), matrix=_MATRIX)
        self.assertIn("color", str(ctx.exception))

    def test_unknown_seat_key_errors(self) -> None:
        body = """
[[boards]]
name = "x"
purpose = "y"
  [[boards.seats]]
  model = "gpt-5.5"
  effort = "high"
  harness = "codex"
  temperature = 0.7
"""
        with TemporaryDirectory() as tmp:
            with self.assertRaises(BoardConfigError) as ctx:
                load_boards(_write(tmp, body), matrix=_MATRIX)
        self.assertIn("temperature", str(ctx.exception))


class StrictBoolParseTests(unittest.TestCase):
    """A boolean config key is read STRICTLY — a present value MUST be a literal
    TOML boolean, never coerced. Coercion would silently flip an opt-in gate:
    ``bool("false")`` is ``True``, so a quoted ``allow_api_key_fallback = "false"``
    would enable the api-key fallback (a no-silent-key hole)."""

    def test_quoted_false_allow_api_key_fallback_is_rejected_not_coerced(self) -> None:
        # The hole: the string "false" is truthy under bool(), so coercion would
        # ENABLE the fallback the author meant to disable. Strict parse rejects it.
        body = """
[[boards]]
name = "leaky"
purpose = "x"
allow_api_key_fallback = "false"
  [[boards.seats]]
  model = "gpt-5.5"
  effort = "high"
  harness = "codex"
"""
        with TemporaryDirectory() as tmp:
            with self.assertRaises(BoardConfigError) as ctx:
                load_boards(_write(tmp, body), matrix=_MATRIX)
        msg = str(ctx.exception)
        self.assertIn("allow_api_key_fallback", msg)
        self.assertIn("boolean", msg)

    def test_nonbool_host_leg_is_rejected_not_coerced(self) -> None:
        body = """
[[boards]]
name = "b"
purpose = "x"
  [[boards.seats]]
  model = "gpt-5.5"
  effort = "high"
  harness = "codex"
  host_leg = "true"
"""
        with TemporaryDirectory() as tmp:
            with self.assertRaises(BoardConfigError) as ctx:
                load_boards(_write(tmp, body), matrix=_MATRIX)
        msg = str(ctx.exception)
        self.assertIn("host_leg", msg)
        self.assertIn("boolean", msg)

    def test_literal_bool_still_accepted(self) -> None:
        body = """
[[boards]]
name = "keyed"
purpose = "x"
allow_api_key_fallback = true
  [[boards.seats]]
  model = "gpt-5.5"
  effort = "high"
  harness = "codex"
  auth = "api_key"
  host_leg = true
"""
        with TemporaryDirectory() as tmp:
            cfg = load_boards(_write(tmp, body), matrix=_MATRIX)
        board = cfg.get("keyed")
        self.assertTrue(board.allow_api_key_fallback)
        self.assertTrue(board.seats[0].host_leg)


class MatrixInvalidBoardRejectedTests(unittest.TestCase):
    def test_invalid_pairing_in_user_board_rejected_at_load(self) -> None:
        body = """
[[boards]]
name = "bad"
purpose = "x"
  [[boards.seats]]
  model = "gpt-5.5"
  effort = "high"
  harness = "claude"
"""
        with TemporaryDirectory() as tmp:
            with self.assertRaises(BoardConfigError) as ctx:
                load_boards(_write(tmp, body), matrix=_MATRIX)
        msg = str(ctx.exception)
        self.assertIn("gpt-5.5", msg)
        self.assertIn("bad", msg)  # board name located


class ShippedExampleFixtureTests(unittest.TestCase):
    def test_frozen_example_toml_parses_and_validates(self) -> None:
        # Resolve via the INSTALLED package (works from source AND from the
        # standalone-from-wheel clean room, where only tests/ is copied and the
        # package lives in site-packages — a source-tree-relative path fails there).
        import phase_loop_runtime.advisor_board as _ab

        example = Path(_ab.__file__).resolve().parent / "fixtures" / "advisor-boards.example.toml"
        self.assertTrue(example.exists(), example)
        cfg = load_boards(example, matrix=_MATRIX)
        # the example defines default + code-review; presets fill the rest.
        self.assertIn("default", cfg.names())
        self.assertIn("code-review", cfg.names())


if __name__ == "__main__":
    unittest.main()
