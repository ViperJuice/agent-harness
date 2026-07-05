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


class PopulatedRegistryTests(unittest.TestCase):
    """ABDREG: the populated six-harness / model registries (the frozen stubs
    above still raise — that contract is unchanged)."""

    def test_six_harnesses_registered_with_cli_and_backing(self) -> None:
        from phase_loop_runtime.advisor_board import DefaultHarnessRegistry

        reg = DefaultHarnessRegistry()
        names = tuple(h.name for h in reg.list_harnesses())
        self.assertEqual(names, ("claude", "codex", "gemini", "opencode", "pi", "cursor"))
        # built-3 are homebrew; breadth (opencode/pi/cursor) default to omnigent.
        by = {h.name: h for h in reg.list_harnesses()}
        for built in ("claude", "codex", "gemini"):
            self.assertEqual(by[built].backing, "homebrew")
        for breadth in ("opencode", "pi", "cursor"):
            self.assertEqual(by[breadth].backing, "omnigent")
        # probe binaries reflect reality: gemini -> agy, cursor -> cursor-agent.
        self.assertEqual(by["gemini"].cli, "agy")
        self.assertEqual(by["cursor"].cli, "cursor-agent")

    def test_cursor_availability_is_gated_on_cursor_agent_binary(self) -> None:
        from phase_loop_runtime.advisor_board import DefaultHarnessRegistry

        present = DefaultHarnessRegistry(probe=lambda cli: cli == "cursor-agent")
        self.assertTrue(present.is_available("cursor"))
        absent = DefaultHarnessRegistry(probe=lambda cli: False)
        self.assertFalse(absent.is_available("cursor"))

    def test_unknown_harness_raises_with_known_list(self) -> None:
        from phase_loop_runtime.advisor_board import DefaultHarnessRegistry, UnknownHarnessError

        with self.assertRaises(UnknownHarnessError):
            DefaultHarnessRegistry().get("amp")

    def test_model_default_lane_pins_gpt55_to_codex_not_opencode(self) -> None:
        # gpt-5.5 is runnable_by both codex and opencode, but a bare seat MUST
        # resolve onto the built-3 codex leg (default-board back-compat).
        from phase_loop_runtime.advisor_board import DEFAULT_MODEL_REGISTRY

        spec = DEFAULT_MODEL_REGISTRY.get("gpt-5.5")
        self.assertEqual(spec.default_lane, "codex")
        self.assertEqual(spec.runnable_by, ("codex", "opencode"))
        self.assertEqual(spec.vendor_family, "codex")  # derived from schema.vendor_family

    def test_unknown_model_raises_with_known_list(self) -> None:
        from phase_loop_runtime.advisor_board import DEFAULT_MODEL_REGISTRY, UnknownModelError

        with self.assertRaises(UnknownModelError):
            DEFAULT_MODEL_REGISTRY.default_lane("gpt-9-imaginary")


if __name__ == "__main__":
    unittest.main()
