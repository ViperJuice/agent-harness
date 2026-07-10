"""SL-2: runtime resolution decoupling (BAML / skills / manifest).

Proves the runtime resolves its own resources without walking into the dotfiles
tree:
- ``baml_modular._baml_src_dir()`` resolves the packaged ``baml_src`` via
  ``importlib.resources`` (package-internal), independent of any repo-root copy;
- ``skill_inventory`` no longer performs the ``parents[4]`` dotfiles walk: with no
  ``PHASE_LOOP_RUNNER_REPO_ROOT`` and no ``skill_sources`` entry point, runner-repo
  resolution yields nothing rather than a fleet path, and ``skill_sources`` entry
  points / the env override drive discovery;
- ``plan_manifest`` resolves the manifest from an explicit root.
"""
from __future__ import annotations

import importlib.resources
import inspect
import os
import unittest
from pathlib import Path
from unittest import mock

import phase_loop_runtime.baml_modular as baml_modular
import phase_loop_runtime.plan_manifest as plan_manifest
import phase_loop_runtime.skill_inventory as skill_inventory


class BamlResourceResolutionTest(unittest.TestCase):
    def test_baml_src_dir_resolves_inside_the_package(self):
        resolved = baml_modular._baml_src_dir()
        self.assertTrue((resolved / "emit_phase_closeout.baml").is_file())
        pkg_dir = Path(importlib.resources.files("phase_loop_runtime")) / "baml_src"
        self.assertEqual(resolved.resolve(), pkg_dir.resolve())

    def test_baml_src_dir_uses_importlib_resources(self):
        # The primary resolution path must go through importlib.resources, not a
        # repo-relative parents[] walk, so the wheel-installed package works.
        source = inspect.getsource(baml_modular._baml_src_dir)
        self.assertIn("importlib.resources", source)


class SkillInventoryDecoupleTest(unittest.TestCase):
    def test_runner_repo_root_does_not_walk_into_dotfiles(self):
        # Source must not contain the parents[4] fleet walk anymore.
        source = inspect.getsource(skill_inventory)
        self.assertNotIn("parents[4]", source)

    def test_runner_repo_root_is_none_without_env(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(skill_inventory._runner_repo_root())

    def test_runner_repo_root_honors_env(self):
        with mock.patch.dict(os.environ, {"PHASE_LOOP_RUNNER_REPO_ROOT": "/tmp/x"}, clear=True):
            self.assertEqual(
                skill_inventory._runner_repo_root(),
                Path("/tmp/x").expanduser().resolve(),
            )

    def test_skill_source_roots_discovered_via_entry_points(self):
        # An entry point under phase_loop_runtime.skill_sources contributes a root
        # without any dotfiles walk.
        self.assertTrue(hasattr(skill_inventory, "iter_skill_source_roots"))
        fake_root = "some-profile/skills"

        class _EP:
            name = "dotfiles"

            def load(self):
                return lambda: {"claude": (fake_root,)}

        def _fake_entry_points(*, group):
            self.assertEqual(group, "phase_loop_runtime.skill_sources")
            return [_EP()]

        # iter_skill_source_roots is lru_cached; clear before and after so a stale
        # cached result (from another test) doesn't mask the mock, and the mocked
        # result doesn't leak into later tests.
        skill_inventory.iter_skill_source_roots.cache_clear()
        self.addCleanup(skill_inventory.iter_skill_source_roots.cache_clear)
        with mock.patch("importlib.metadata.entry_points", _fake_entry_points):
            roots = dict(skill_inventory.iter_skill_source_roots())
        self.assertIn(fake_root, roots.get("claude", ()))


class DoctorImportIsolationTest(unittest.TestCase):
    """AHADOPT lane (d): the `phase-loop doctor` import graph must pull NO
    dotfiles-domain module, transitively (it reuses repo_validation +
    install_status, so this also guards those two)."""

    def test_importing_doctor_pulls_no_dotfiles_domain_module(self):
        # Import in a fresh subprocess so an unrelated earlier import in this
        # session cannot mask a real leak.
        import subprocess
        import sys

        code = (
            "import phase_loop_runtime.doctor, sys; "
            "forbidden=['phase_loop_runtime.adoption_bundle',"
            "'phase_loop_runtime.build_bundle',"
            "'phase_loop_runtime.skill_sources_plugin',"
            "'phase_loop_runtime.dotfiles_profile_plugin']; "
            "leaked=[m for m in forbidden if m in sys.modules]; "
            "assert not leaked, leaked; print('clean')"
        )
        # Propagate the parent's sys.path so the fresh subprocess can import the
        # package whether it is pip-installed (CI/Gate A) or run from an
        # uninstalled checkout without PYTHONPATH=src. Isolation still holds: the
        # child imports only phase_loop_runtime.doctor and asserts no dotfiles
        # module is present.
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, env=env
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("clean", result.stdout)


class PlanManifestRootTest(unittest.TestCase):
    def test_manifest_path_takes_explicit_root(self):
        repo = Path("/tmp/some-repo")
        self.assertEqual(
            plan_manifest._manifest_path(repo),
            repo / plan_manifest.MANIFEST_PATH,
        )

    def test_manifest_path_relative_default_documented(self):
        # MANIFEST_PATH is a repo-relative default, never an absolute hardcode.
        self.assertFalse(Path(plan_manifest.MANIFEST_PATH).is_absolute())


if __name__ == "__main__":
    unittest.main()
