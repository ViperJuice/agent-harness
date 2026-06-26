"""CR #1a/#1b: injection fails LOUD on a zero-resolved skill bundle in a context
that expects skills, and the window-closure (env opt-in / live entry point) makes it
succeed again.

The disentangle (EXTRACTSKILLS SL-2) emptied the generic HARNESS_SOURCE_ROOTS, so the
dotfiles overlay roots arrive only through the phase_loop_runtime.skill_sources seam.
If that seam is not live, skill resolution silently yields nothing; without this guard
the runner would inject an EMPTY bundle. These tests pin the loud failure and its
closure.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import phase_loop_runtime.skill_inventory as skill_inventory
from phase_loop_runtime.injection import (
    SkillBundleResolutionError,
    _bundle_sha256,
    _resolve_pack_skill_dirs,
    _skill_bodies,
    materialize_claude_plugin_bundle,
)


CLAUDE_PACK = ("claude-plan-phase", "claude-execute-phase")

import pytest

# TESTDECOUPLE SL-1 (overlay-dependent): builds a skill/adoption bundle or runs the
# runtime execute path, which resolves the dotfiles skill-source / profile overlay
# (claude-config/*, codex-config/* …) absent standalone. Run-time integration: the
# conftest hook skips it when no dotfiles tree is reachable.
pytestmark = pytest.mark.dotfiles_integration


def _no_entry_points(*, group):
    return []


class InjectionFailsLoudTest(unittest.TestCase):
    """CR #1a: a bundle-source harness with a non-empty pack that resolves NOTHING raises."""

    def setUp(self):
        # Strip BOTH the runner-root fallback and the skill-sources env opt-in, and
        # mock the entry-point group empty -> resolution genuinely yields nothing.
        skill_inventory.iter_skill_source_roots.cache_clear()
        self.addCleanup(skill_inventory.iter_skill_source_roots.cache_clear)
        self._env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("PHASE_LOOP_RUNNER_REPO_ROOT", "PHASE_LOOP_SKILL_SOURCE_PLUGINS")
        }

    def test_resolve_pack_raises_on_all_none_for_bundle_harness(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)  # empty repo: no skill sources anywhere
            with mock.patch.dict(os.environ, self._env, clear=True), mock.patch(
                "importlib.metadata.entry_points", _no_entry_points
            ):
                with self.assertRaises(SkillBundleResolutionError) as ctx:
                    _resolve_pack_skill_dirs(repo, "claude", CLAUDE_PACK)
        # The message must be actionable (names the seam + remediation).
        self.assertIn("skill_sources", str(ctx.exception))
        self.assertIn("bootstrap", str(ctx.exception))

    def test_skill_bodies_fails_loud(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            with mock.patch.dict(os.environ, self._env, clear=True), mock.patch(
                "importlib.metadata.entry_points", _no_entry_points
            ):
                with self.assertRaises(SkillBundleResolutionError):
                    _skill_bodies(repo, "claude", CLAUDE_PACK)

    def test_bundle_sha256_fails_loud_instead_of_none_none_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            with mock.patch.dict(os.environ, self._env, clear=True), mock.patch(
                "importlib.metadata.entry_points", _no_entry_points
            ):
                with self.assertRaises(SkillBundleResolutionError):
                    _bundle_sha256(
                        repo=repo,
                        harness_target="codex",
                        action="execute",
                        workflow_command="codex-execute-phase plan.md",
                        body="body",
                        expected_skill_pack=("codex-plan-phase",),
                    )

    def test_empty_pack_does_not_raise(self):
        # Standalone / no-skills-expected context: empty pack -> no error.
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            with mock.patch.dict(os.environ, self._env, clear=True), mock.patch(
                "importlib.metadata.entry_points", _no_entry_points
            ):
                self.assertEqual(_resolve_pack_skill_dirs(repo, "claude", ()), {})

    def test_pi_harness_is_exempt_from_failloud(self):
        # pi delivers skills via package-root, not resolve_source_skill_dir; an all-None
        # resolve for pi must NOT raise.
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            with mock.patch.dict(os.environ, self._env, clear=True), mock.patch(
                "importlib.metadata.entry_points", _no_entry_points
            ):
                self.assertEqual(
                    _resolve_pack_skill_dirs(repo, "pi", ("phase-loop-supervisor",)), {}
                )


class WindowClosureTest(unittest.TestCase):
    """CR #1b: with the skill-sources seam live (env opt-in), resolution succeeds again."""

    def setUp(self):
        skill_inventory.iter_skill_source_roots.cache_clear()
        self.addCleanup(skill_inventory.iter_skill_source_roots.cache_clear)

    def test_env_opt_in_closes_the_window(self):
        from phase_loop_test_utils import ROOT

        # With the dotfiles skill-sources plugin opted in via env (the production /
        # source-mode closure mechanism), the same pack resolves against the real tree
        # and materialization copies the skills instead of raising.
        env = dict(os.environ)
        env["PHASE_LOOP_SKILL_SOURCE_PLUGINS"] = (
            "phase_loop_runtime.skill_sources_plugin:register_skill_sources"
        )
        env.pop("PHASE_LOOP_RUNNER_REPO_ROOT", None)
        with tempfile.TemporaryDirectory() as td:
            run_root = Path(td)
            with mock.patch.dict(os.environ, env, clear=True), mock.patch(
                "importlib.metadata.entry_points", _no_entry_points
            ):
                resolved = _resolve_pack_skill_dirs(ROOT, "claude", CLAUDE_PACK)
        # Both claude skills resolve against the real dotfiles tree.
        self.assertEqual(set(resolved), set(CLAUDE_PACK))


if __name__ == "__main__":
    unittest.main()
