"""#12 — a pinned/pip install resolves the workflow skill pack from the bundle
shipped inside the package, with no dotfiles overlay and no sibling
`phase-loop-skills/`.

This exercises the *packaged* `phase_loop_runtime/skills_bundle/` (committed, ships
in the wheel), so it runs standalone — the in-process equivalent of the from-wheel
Gate A clean-room probe.
"""
import os
import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime import skill_inventory
from phase_loop_runtime.injection import _resolve_pack_skill_dirs

CORE_SKILLS = (
    "claude-phase-roadmap-builder",
    "claude-plan-phase",
    "claude-execute-phase",
    "claude-phase-loop",
)


class PackagedSkillBundleTest(unittest.TestCase):
    def setUp(self):
        # Simulate a pinned consumer: no source-root opt-in, no runner-root override,
        # and a consumer repo that contains no skills of its own.
        self._saved = {
            k: os.environ.pop(k, None)
            for k in ("PHASE_LOOP_SKILL_SOURCE_PLUGINS", "PHASE_LOOP_RUNNER_REPO_ROOT")
        }
        skill_inventory.iter_skill_source_roots.cache_clear()
        skill_inventory._packaged_skills_bundle_dir.cache_clear()
        self._td = tempfile.TemporaryDirectory()
        self.repo = Path(self._td.name)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v
        skill_inventory.iter_skill_source_roots.cache_clear()
        self._td.cleanup()

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
        # The exact path run/dry-run take — must not raise SkillBundleResolutionError.
        resolved = _resolve_pack_skill_dirs(self.repo, "claude", CORE_SKILLS)
        self.assertEqual(set(resolved), set(CORE_SKILLS))

    def test_neutral_names_do_not_overresolve(self):
        # Only assembled `<harness>-<skill>` dirs ship; bare neutral names must not resolve.
        self.assertIsNone(skill_inventory.resolve_source_skill_dir(self.repo, "claude", "plan-phase"))
        self.assertIsNone(skill_inventory.resolve_source_skill_dir(self.repo, "claude", "nonexistent-skill"))

    def test_other_harnesses_resolve_too(self):
        for harness, skill in (("codex", "codex-plan-phase"), ("gemini", "gemini-phase-loop")):
            resolved = skill_inventory.resolve_source_skill_dir(self.repo, harness, skill)
            self.assertIsNotNone(resolved, f"{skill} did not resolve")
            self.assertIn("skills_bundle", str(resolved))


if __name__ == "__main__":
    unittest.main()
