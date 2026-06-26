"""SL-0 freeze: entry-point groups declared + baml_src shipped as package-data.

These tests pin the DECOUPLE package layout that SL-1/SL-2 build on:
- the plugin entry-point groups (`profile_commands`, `skill_sources`) are declared
  as a *convention* in pyproject (the group exists; DECOUPLE does not self-register
  the in-package dotfiles plugin under it -- that is opt-in only);
- `baml_src/*.baml` lives inside the package (`src/phase_loop_runtime/baml_src/`)
  and ships as package-data, so it travels in the wheel via importlib.resources
  regardless of installer;
- the old `[tool.setuptools.data-files]` -> `share/` BAML shipping is gone.
"""
from __future__ import annotations

import unittest
from pathlib import Path

import pytest

# TESTDECOUPLE SL-1 (overlay-dependent): builds a skill/adoption bundle or runs the
# runtime execute path, which resolves the dotfiles skill-source / profile overlay
# (claude-config/*, codex-config/* …) absent standalone. Run-time integration: the
# conftest hook skips it when no dotfiles tree is reachable.
pytestmark = pytest.mark.dotfiles_integration

try:  # py3.11+ stdlib; fall back to the vendored backport name on 3.10
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on 3.10
    import tomli as tomllib  # type: ignore[no-redef]


ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
PKG_BAML_DIR = ROOT / "src" / "phase_loop_runtime" / "baml_src"

BAML_FILES = (
    "emit_phase_closeout.baml",
    "dotfiles_adoption_manifest.baml",
    "dotfiles_runtime_projection.baml",
    "dotfiles_plan_manifest.baml",
    "dotfiles_c4_document.baml",
    "dotfiles_task_catalog.baml",
    "evaluate_suspected_fake_evidence.baml",
    "verification_evidence.baml",
)


def _config() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


class PackageLayoutFreezeTest(unittest.TestCase):
    def test_entry_point_groups_declared(self):
        cfg = _config()
        groups = cfg.get("project", {}).get("entry-points", {})
        self.assertIn(
            "phase_loop_runtime.profile_commands",
            groups,
            "pyproject must declare the profile_commands entry-point group",
        )
        self.assertIn(
            "phase_loop_runtime.skill_sources",
            groups,
            "pyproject must declare the skill_sources entry-point group",
        )

    def test_in_tree_dotfiles_profile_is_registered(self):
        # Option A (PR #78 review): DECOUPLE is an *in-place* decouple, so the
        # in-tree dotfiles profile IS registered under the profile_commands group --
        # a normal fleet install must keep adoption-bundle/sync-skills/build-bundle/
        # hotfix working with no env var. (An empty group broke the fleet.) The seam
        # is proven by Gate-A, which strips the group from the installed dist-info.
        cfg = _config()
        profile_group = cfg.get("project", {}).get("entry-points", {}).get(
            "phase_loop_runtime.profile_commands", {}
        )
        self.assertEqual(
            profile_group.get("dotfiles"),
            "phase_loop_runtime.dotfiles_profile_plugin:register_profile_commands",
            "the in-tree dotfiles profile must be registered under profile_commands (Option A)",
        )

    def test_baml_src_lives_inside_the_package(self):
        self.assertTrue(
            PKG_BAML_DIR.is_dir(),
            f"baml_src must live inside the package at {PKG_BAML_DIR}",
        )
        for name in BAML_FILES:
            self.assertTrue(
                (PKG_BAML_DIR / name).is_file(),
                f"missing packaged baml source: {name}",
            )

    def test_baml_src_shipped_as_package_data(self):
        cfg = _config()
        pkg_data = (
            cfg.get("tool", {})
            .get("setuptools", {})
            .get("package-data", {})
            .get("phase_loop_runtime", [])
        )
        self.assertTrue(
            any("baml_src" in entry for entry in pkg_data),
            "package-data must include baml_src/*.baml",
        )

    def test_no_data_files_share_baml(self):
        cfg = _config()
        data_files = cfg.get("tool", {}).get("setuptools", {}).get("data-files", {})
        for target in data_files:
            self.assertNotIn(
                "baml_src",
                target,
                "baml_src must not ship via data-files -> share/ anymore",
            )


if __name__ == "__main__":
    unittest.main()
