"""ABDREG — (model x harness) compatibility + auth matrix + config-time
validation (Phase 2, lane 3).

The acceptance oracle is the SHARED canonical fixtures: the matrix must accept
every ``CANONICAL_VALID_PAIRS`` and reject every ``CANONICAL_INVALID_PAIRS`` —
proving ``claude:gpt-5.5`` is rejected at config time with an actionable message.
"""
from __future__ import annotations

import unittest

from phase_loop_runtime.advisor_board import (
    CANONICAL_INVALID_PAIRS,
    CANONICAL_VALID_PAIRS,
    Seat,
    SeatValidationError,
    default_matrix,
    validate_seat,
)

_ALL_PRESENT = lambda cli: True  # every CLI on PATH (deterministic)
_NONE_PRESENT = lambda cli: False


class MatrixVerdictTests(unittest.TestCase):
    def test_every_canonical_valid_pair_is_valid(self) -> None:
        m = default_matrix(probe=_ALL_PRESENT, env={})
        for model, harness in CANONICAL_VALID_PAIRS:
            ok, avail = m.is_valid(model, harness)
            self.assertTrue(ok, f"{model}:{harness} should be valid")
            # a valid pair with the CLI present has the subscription lane usable
            self.assertTrue(avail.subscription, f"{model}:{harness} subscription")

    def test_every_canonical_invalid_pair_is_rejected_with_message(self) -> None:
        m = default_matrix(probe=_ALL_PRESENT, env={})
        for model, harness in CANONICAL_INVALID_PAIRS:
            ok, avail = m.is_valid(model, harness)
            self.assertFalse(ok, f"{model}:{harness} should be invalid")
            self.assertIn(model, avail.detail)
            self.assertIn(harness, avail.detail)
            self.assertIn("family", avail.detail)  # actionable, names the mismatch

    def test_default_lane_delegates_to_model_registry(self) -> None:
        m = default_matrix()
        self.assertEqual(m.default_lane("gpt-5.5"), "codex")
        self.assertEqual(m.default_lane("claude-sonnet-5"), "claude")

    def test_unknown_model_and_harness_are_total_not_raising(self) -> None:
        m = default_matrix()
        ok, avail = m.is_valid("no-such-model", "codex")
        self.assertFalse(ok)
        self.assertIn("no-such-model", avail.detail)
        ok2, avail2 = m.is_valid("gpt-5.5", "no-such-harness")
        self.assertFalse(ok2)
        self.assertIn("no-such-harness", avail2.detail)


class AuthAvailabilityTests(unittest.TestCase):
    def test_subscription_lane_tracks_cli_presence(self) -> None:
        present = default_matrix(probe=_ALL_PRESENT, env={})
        _, avail = present.is_valid("gpt-5.5", "codex")
        self.assertTrue(avail.subscription)
        absent = default_matrix(probe=_NONE_PRESENT, env={})
        _, avail2 = absent.is_valid("gpt-5.5", "codex")
        self.assertFalse(avail2.subscription)  # valid pairing, but no usable lane

    def test_api_key_lane_requires_the_vendor_key_in_env(self) -> None:
        # no-silent-key is TESTABLE: api_key lane is available only when the
        # vendor's key var is actually present.
        no_key = default_matrix(probe=_ALL_PRESENT, env={})
        _, avail = no_key.is_valid("gpt-5.5", "codex")
        self.assertFalse(avail.api_key)
        with_key = default_matrix(probe=_ALL_PRESENT, env={"OPENAI_API_KEY": "sk-x"})
        _, avail2 = with_key.is_valid("gpt-5.5", "codex")
        self.assertTrue(avail2.api_key)
        # and it's the codex vendor's key specifically — an anthropic key doesn't count
        wrong_key = default_matrix(probe=_ALL_PRESENT, env={"ANTHROPIC_API_KEY": "sk-x"})
        _, avail3 = wrong_key.is_valid("gpt-5.5", "codex")
        self.assertFalse(avail3.api_key)


class ConfigTimeSeatValidationTests(unittest.TestCase):
    def test_invalid_pairing_rejected_at_config_time(self) -> None:
        m = default_matrix(probe=_ALL_PRESENT, env={})
        with self.assertRaises(SeatValidationError) as ctx:
            validate_seat(Seat(model="gpt-5.5", effort="max", harness="claude"), matrix=m)
        msg = str(ctx.exception)
        self.assertIn("gpt-5.5", msg)
        self.assertIn("claude", msg)
        self.assertIn("codex", msg)  # names the valid lane

    def test_bare_seat_resolves_default_lane_and_validates(self) -> None:
        m = default_matrix(probe=_ALL_PRESENT, env={})
        # harness omitted -> default_lane(gpt-5.5) == codex -> valid
        avail = validate_seat(Seat(model="gpt-5.5", effort="max"), matrix=m)
        self.assertTrue(avail.subscription)

    def test_unknown_model_seat_rejected(self) -> None:
        with self.assertRaises(SeatValidationError):
            validate_seat(Seat(model="ghost-model", effort="max", harness="codex"))

    def test_valid_but_unauthed_seat_is_not_rejected(self) -> None:
        # A valid pairing with no usable lane degrades at launch (skip-with-
        # warning), it is NOT a config-time rejection.
        m = default_matrix(probe=_NONE_PRESENT, env={})
        avail = validate_seat(Seat(model="gpt-5.5", effort="max", harness="codex"), matrix=m)
        self.assertFalse(avail.any_available)


if __name__ == "__main__":
    unittest.main()
