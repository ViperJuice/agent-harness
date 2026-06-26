"""SL-2: shared/overlay disentangle via the skill_sources entry-point seam.

Proves the per-harness dotfiles overlay roots no longer live in the generic
runtime's ``HARNESS_SOURCE_ROOTS`` constant but arrive through the
``phase_loop_runtime.skill_sources`` plugin seam, and that BOTH direct readers of
the constant (``resolve_source_skill_dir`` and ``classify_skill_like_directories``)
were re-routed through the merged builtin+plugin roots.
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

import phase_loop_runtime.skill_inventory as skill_inventory
from phase_loop_runtime.build_bundle import DEFAULT_SOURCES
from phase_loop_runtime.skill_inventory import (
    HARNESS_SOURCE_ROOTS,
    classify_skill_like_directories,
    iter_skill_source_roots,
    resolve_source_skill_dir,
)
from phase_loop_runtime.skill_sources_plugin import register_skill_sources
from phase_loop_test_utils import ROOT


# The dotfiles overlay roots are authoritative in build_bundle.DEFAULT_SOURCES.
OVERLAY_ROOTS = dict(DEFAULT_SOURCES)

import pytest

# TESTDECOUPLE SL-1 (overlay-dependent): builds a skill/adoption bundle or runs the
# runtime execute path, which resolves the dotfiles skill-source / profile overlay
# (claude-config/*, codex-config/* …) absent standalone. Run-time integration: the
# conftest hook skips it when no dotfiles tree is reachable.
pytestmark = pytest.mark.dotfiles_integration


class HarnessSourceRootsEmptiedTest(unittest.TestCase):
    def test_generic_constant_no_longer_hardcodes_dotfiles_paths(self):
        # (a) The 4 per-harness entries are empty; the keys are retained.
        for harness in ("claude", "codex", "gemini", "opencode"):
            with self.subTest(harness=harness):
                self.assertIn(harness, HARNESS_SOURCE_ROOTS)
                self.assertEqual(HARNESS_SOURCE_ROOTS[harness], ())
        # No dotfiles path token survives in the generic constant.
        flat = tuple(root for roots in HARNESS_SOURCE_ROOTS.values() for root in roots)
        self.assertEqual(flat, ())


class SkillSourcesProviderTest(unittest.TestCase):
    def test_provider_is_single_sourced_from_default_sources(self):
        mapping = register_skill_sources()
        self.assertEqual(
            mapping,
            {harness: (path,) for harness, path in DEFAULT_SOURCES.items()},
        )


class OverlayResolvesViaSeamTest(unittest.TestCase):
    """(b)/(c): with the dotfiles plugin loaded, the overlay roots resolve."""

    def test_resolve_source_skill_dir_uses_overlay_root_for_each_harness(self):
        # The conftest opts the dotfiles skill_sources plugin in suite-wide, so the
        # seam supplies the overlay roots even though the constant is empty.
        cases = (
            ("claude", "claude-plan-phase"),
            ("codex", "codex-plan-phase"),
            ("gemini", "gemini-plan-phase"),
            ("opencode", "opencode-plan-phase"),
        )
        for harness, skill_name in cases:
            with self.subTest(harness=harness):
                resolved = resolve_source_skill_dir(ROOT, harness, skill_name)
                expected = (ROOT / OVERLAY_ROOTS[harness] / skill_name).resolve()
                self.assertEqual(resolved, expected)

    def test_overlay_roots_arrive_from_the_seam_not_the_constant(self):
        # Prove provenance: the merged roots equal the plugin-provided roots, since
        # the builtin constant is empty.
        merged = dict(iter_skill_source_roots())
        for harness in ("claude", "codex", "gemini", "opencode"):
            with self.subTest(harness=harness):
                self.assertEqual(HARNESS_SOURCE_ROOTS[harness], ())
                self.assertIn(OVERLAY_ROOTS[harness], merged.get(harness, ()))


class CleanRuntimeYieldsNothingTest(unittest.TestCase):
    """(d): a clean runtime -- no entry point, no env opt-in -- couples no path."""

    def test_no_plugin_yields_no_source_roots_or_resolution(self):
        def _no_entry_points(*, group):
            return []

        # Clear the env opt-in AND mock entry_points empty AND cache_clear before;
        # restore the cache after so the live (conftest-opted-in) plugin returns for
        # later same-process tests.
        iter_skill_source_roots.cache_clear()
        self.addCleanup(iter_skill_source_roots.cache_clear)
        env_without_optin = {
            key: value
            for key, value in os.environ.items()
            if key != "PHASE_LOOP_SKILL_SOURCE_PLUGINS"
        }
        with mock.patch.dict(os.environ, env_without_optin, clear=True), mock.patch(
            "importlib.metadata.entry_points", _no_entry_points
        ):
            self.assertEqual(dict(iter_skill_source_roots()), {})
            for harness in ("claude", "codex", "gemini", "opencode"):
                with self.subTest(harness=harness):
                    skill = f"{harness}-plan-phase"
                    self.assertIsNone(resolve_source_skill_dir(ROOT, harness, skill))


class ClassifySecondConsumerGuardTest(unittest.TestCase):
    """(e): classify_skill_like_directories still emits canonical via the seam."""

    def test_classify_emits_canonical_through_merged_roots(self):
        # classify_skill_like_directories reads the roots directly; after SL-2 it
        # iterates merged builtin+plugin roots, so canonical records must still emit
        # for the 4 harnesses against the live dotfiles tree.
        for harness in ("claude", "codex", "gemini", "opencode"):
            with self.subTest(harness=harness):
                records = classify_skill_like_directories(ROOT, (harness,))
                canonical = {
                    record.skill_name
                    for record in records
                    if record.classification == "canonical"
                }
                self.assertIn(f"{harness}-plan-phase", canonical)

    def test_classify_does_not_duplicate_canonical_records(self):
        # Dedup guard: even if a root is contributed more than once, classify must
        # not emit duplicate canonical records for the same skill dir.
        records = classify_skill_like_directories(ROOT, ("claude",))
        canonical_paths = [
            record.path for record in records if record.classification == "canonical"
        ]
        self.assertEqual(len(canonical_paths), len(set(canonical_paths)))


class RegisteredProviderFailsLoudTest(unittest.TestCase):
    """CR #2: a REGISTERED-but-raising provider propagates, not silently empty."""

    def _clear(self):
        iter_skill_source_roots.cache_clear()

    def test_provider_that_raises_on_invoke_propagates(self):
        class _EP:
            name = "broken"

            def load(self):
                def _raises():
                    raise ValueError("boom in provider")

                return _raises

        def _eps(*, group):
            return [_EP()] if group == "phase_loop_runtime.skill_sources" else []

        self._clear()
        self.addCleanup(self._clear)
        # Strip the env opt-in so only the broken entry point is configured.
        env = {k: v for k, v in os.environ.items() if k != "PHASE_LOOP_SKILL_SOURCE_PLUGINS"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch(
            "importlib.metadata.entry_points", _eps
        ):
            with self.assertRaises(skill_inventory.SkillSourcePluginError):
                iter_skill_source_roots()

    def test_entry_point_that_fails_to_load_propagates(self):
        class _EP:
            name = "unloadable"

            def load(self):
                raise ImportError("cannot import provider module")

        def _eps(*, group):
            return [_EP()] if group == "phase_loop_runtime.skill_sources" else []

        self._clear()
        self.addCleanup(self._clear)
        env = {k: v for k, v in os.environ.items() if k != "PHASE_LOOP_SKILL_SOURCE_PLUGINS"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch(
            "importlib.metadata.entry_points", _eps
        ):
            with self.assertRaises(skill_inventory.SkillSourcePluginError):
                iter_skill_source_roots()

    def test_absent_provider_is_not_an_error(self):
        # The clean-runtime case (nothing configured) yields empty, never raises.
        def _no_eps(*, group):
            return []

        self._clear()
        self.addCleanup(self._clear)
        env = {k: v for k, v in os.environ.items() if k != "PHASE_LOOP_SKILL_SOURCE_PLUGINS"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch(
            "importlib.metadata.entry_points", _no_eps
        ):
            self.assertEqual(dict(iter_skill_source_roots()), {})

    def test_malformed_opt_in_spec_propagates_when_unresolvable(self):
        # A spec the operator DID configure that won't import is a real bug.
        def _no_eps(*, group):
            return []

        self._clear()
        self.addCleanup(self._clear)
        with mock.patch.dict(
            os.environ,
            {"PHASE_LOOP_SKILL_SOURCE_PLUGINS": "nonexistent_module_xyz:provider"},
            clear=True,
        ), mock.patch("importlib.metadata.entry_points", _no_eps):
            with self.assertRaises(skill_inventory.SkillSourcePluginError):
                iter_skill_source_roots()


class EnvAfterFirstCallTakesEffectTest(unittest.TestCase):
    """CR #4: setting the env AFTER a first call must take effect (cache keyed on env)."""

    def _clear(self):
        iter_skill_source_roots.cache_clear()

    def test_env_set_after_first_call_is_honored(self):
        def _no_eps(*, group):
            return []

        self._clear()
        self.addCleanup(self._clear)
        with mock.patch("importlib.metadata.entry_points", _no_eps):
            # First call: env opt-in absent -> empty.
            env_empty = {
                k: v for k, v in os.environ.items() if k != "PHASE_LOOP_SKILL_SOURCE_PLUGINS"
            }
            with mock.patch.dict(os.environ, env_empty, clear=True):
                self.assertEqual(dict(iter_skill_source_roots()), {})
            # Second call WITHOUT clearing the cache: env now set -> roots appear.
            with mock.patch.dict(
                os.environ,
                {
                    "PHASE_LOOP_SKILL_SOURCE_PLUGINS": "phase_loop_runtime.skill_sources_plugin:register_skill_sources"
                },
                clear=True,
            ):
                roots = dict(iter_skill_source_roots())
        self.assertIn("claude-config/claude-skills", roots.get("claude", ()))


if __name__ == "__main__":
    unittest.main()
