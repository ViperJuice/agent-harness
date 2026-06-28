"""#12 — a pinned/pip install resolves the workflow skill pack from the bundle
shipped inside the package, with no dotfiles overlay and no sibling
`phase-loop-skills/`; and (CR fix) a *source* checkout still fails loud rather than
silently serving the committed bundle.

These exercise the *packaged* `phase_loop_runtime/skills_bundle/` (committed, ships in
the wheel). Since the suite runs in-tree (where the sibling `phase-loop-skills/` source
IS present), the pinned case is simulated by patching the source-present gate to False;
the fail-loud case patches it to True — both deterministic regardless of layout.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime import skill_inventory
from phase_loop_runtime.injection import SkillBundleResolutionError, _resolve_pack_skill_dirs

CORE_SKILLS = (
    "claude-phase-roadmap-builder",
    "claude-plan-phase",
    "claude-execute-phase",
    "claude-phase-loop",
)


def _clear_caches():
    skill_inventory.iter_skill_source_roots.cache_clear()
    skill_inventory._packaged_skills_bundle_dir.cache_clear()
    skill_inventory._canonical_skill_source_present.cache_clear()


class _BaseTest(unittest.TestCase):
    def setUp(self):
        self._saved = {
            k: os.environ.pop(k, None)
            for k in ("PHASE_LOOP_SKILL_SOURCE_PLUGINS", "PHASE_LOOP_RUNNER_REPO_ROOT")
        }
        _clear_caches()
        self._td = tempfile.TemporaryDirectory()
        self.repo = Path(self._td.name)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v
        _clear_caches()  # incl. _packaged_skills_bundle_dir + _canonical_skill_source_present (CR #7)
        self._td.cleanup()


class PackagedSkillBundleTest(_BaseTest):
    """Pinned install: no canonical source reachable -> the packaged bundle resolves."""

    def setUp(self):
        super().setUp()
        # Simulate a pinned consumer (no sibling phase-loop-skills/ source tree).
        p = patch.object(skill_inventory, "_canonical_skill_source_present", return_value=False)
        p.start()
        self.addCleanup(p.stop)

    def test_packaged_bundle_ships(self):
        bundle = skill_inventory._packaged_skills_bundle_dir()
        self.assertIsNotNone(bundle, "phase_loop_runtime/skills_bundle/ not packaged")
        self.assertTrue((bundle / "claude-phase-loop" / "SKILL.md").is_file())

    def test_core_skills_resolve_from_package(self):
        for skill in CORE_SKILLS:
            resolved = skill_inventory.resolve_source_skill_dir(self.repo, "claude", skill)
            self.assertIsNotNone(resolved, f"{skill} did not resolve from the packaged bundle")
            self.assertIn("skills_bundle", str(resolved))
            self.assertTrue((resolved / "SKILL.md").is_file())

    def test_resolve_pack_does_not_raise(self):
        resolved = _resolve_pack_skill_dirs(self.repo, "claude", CORE_SKILLS)
        self.assertEqual(set(resolved), set(CORE_SKILLS))

    def test_neutral_names_do_not_overresolve(self):
        self.assertIsNone(skill_inventory.resolve_source_skill_dir(self.repo, "claude", "plan-phase"))
        self.assertIsNone(skill_inventory.resolve_source_skill_dir(self.repo, "claude", "nonexistent-skill"))

    def test_other_harnesses_resolve_too(self):
        for harness, skill in (("codex", "codex-plan-phase"), ("gemini", "gemini-phase-loop")):
            resolved = skill_inventory.resolve_source_skill_dir(self.repo, harness, skill)
            self.assertIsNotNone(resolved, f"{skill} did not resolve")
            self.assertIn("skills_bundle", str(resolved))


class SourceCheckoutFailsLoudTest(_BaseTest):
    """CR #1: in a source checkout (canonical source present) the packaged fallback is
    suppressed — an un-bootstrapped overlay must fail loud, not serve stale skills."""

    def test_fallback_suppressed_when_source_present(self):
        with patch.object(skill_inventory, "_canonical_skill_source_present", return_value=True):
            self.assertIsNone(
                skill_inventory.resolve_source_skill_dir(self.repo, "claude", "claude-phase-loop")
            )

    def test_resolve_pack_raises_loud_when_source_present(self):
        with patch.object(skill_inventory, "_canonical_skill_source_present", return_value=True):
            with self.assertRaises(SkillBundleResolutionError):
                _resolve_pack_skill_dirs(self.repo, "claude", CORE_SKILLS)

    def test_real_gate_detects_sibling_source_in_tree(self):
        # In this repo, phase-loop-skills/ is a real sibling -> the probe must see it.
        sibling = Path(skill_inventory.__file__).resolve().parents[2].parent / "phase-loop-skills"
        if not sibling.is_dir():
            self.skipTest("sibling phase-loop-skills/ source not present (from-wheel layout)")
        self.assertTrue(skill_inventory._canonical_skill_source_present())


if __name__ == "__main__":
    unittest.main()
