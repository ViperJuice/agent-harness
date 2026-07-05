"""ABDFREEZE — registry interfaces + matrix API are importable stubs
(IF-0-ABDFREEZE-2).

INTERFACES ONLY: the return types + method surface are frozen; the six-harness
data is ABDREG. A phase that leans on real data before ABDREG must fail loudly,
so the stubs raise ``NotImplementedError``.
"""
from __future__ import annotations

import unittest

from phase_loop_runtime.advisor_board import (
    AuthAvailability,
    CompatibilityMatrix,
    HarnessRegistry,
    HarnessSpec,
    ModelRegistry,
    ModelSpec,
    StubCompatibilityMatrix,
    StubHarnessRegistry,
    StubModelRegistry,
)


class ReturnTypeTests(unittest.TestCase):
    def test_auth_availability_is_concrete_and_defaults_fail_closed(self) -> None:
        aa = AuthAvailability()
        self.assertFalse(aa.subscription)
        self.assertFalse(aa.api_key)
        self.assertFalse(aa.any_available)
        self.assertTrue(AuthAvailability(subscription=True).any_available)

    def test_specs_are_frozen_records(self) -> None:
        hs = HarnessSpec(name="codex", cli="codex")
        ms = ModelSpec(model="gpt-5.5", vendor_family="codex", default_lane="codex")
        with self.assertRaises(Exception):
            hs.name = "x"  # frozen
        with self.assertRaises(Exception):
            ms.model = "y"  # frozen
        self.assertEqual(hs.auth_lanes, ("subscription",))
        self.assertEqual(hs.backing, "homebrew")
        self.assertIsNone(hs.available)  # probe result, not frozen data


class StubTests(unittest.TestCase):
    def test_stubs_structurally_satisfy_protocols(self) -> None:
        self.assertIsInstance(StubHarnessRegistry(), HarnessRegistry)
        self.assertIsInstance(StubModelRegistry(), ModelRegistry)
        self.assertIsInstance(StubCompatibilityMatrix(), CompatibilityMatrix)

    def test_stub_accessors_raise_until_abdreg(self) -> None:
        with self.assertRaises(NotImplementedError):
            StubHarnessRegistry().list_harnesses()
        with self.assertRaises(NotImplementedError):
            StubModelRegistry().default_lane("gpt-5.5")
        with self.assertRaises(NotImplementedError):
            StubCompatibilityMatrix().is_valid("gpt-5.5", "codex")

    def test_matrix_is_valid_return_shape(self) -> None:
        # Freeze the tuple[bool, AuthAvailability] shape via a hand-built verdict —
        # this is what ABDREG's matrix must return and ABDRESOLVE's validation reads.
        verdict: tuple[bool, AuthAvailability] = (True, AuthAvailability(subscription=True))
        ok, avail = verdict
        self.assertTrue(ok)
        self.assertTrue(avail.subscription)


if __name__ == "__main__":
    unittest.main()
