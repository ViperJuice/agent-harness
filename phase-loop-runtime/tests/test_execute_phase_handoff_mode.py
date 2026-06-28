"""rigor-v1 P3 — mode-aware handoff & subagent dispatch.

Standalone (reads the skill *source* under ``phase-loop-skills/``, not the
dotfiles-deployed copy), so it runs in the extracted agent-harness layout.

Guards the fix for the inert `/clear` instruction that leaked into the
autonomous loop: `/clear` must be labeled interactive-only, the adapter
(autonomous) section must forbid it, and the autonomous continuity story
(written handoff + fresh runner process, or a dispatched subagent) must be
documented.
"""
import re
import unittest
from pathlib import Path

import pytest

from _dotfiles_tree import skills_bundle_present

# TESTDECOUPLE (#9): this reads the workflow-skill source under the sibling
# phase-loop-skills/ bundle, absent in the standalone-from-wheel clean-room. Guard at module
# level — the read happens in setUpClass, before marker-based collection skips would apply.
if not skills_bundle_present():
    pytest.skip(
        "requires the sibling phase-loop-skills bundle (absent in the standalone-from-wheel clean-room)",
        allow_module_level=True,
    )

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "phase-loop-skills" / "execute-phase" / "_overrides" / "claude" / "SKILL.md"


class ExecutePhaseHandoffModeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SKILL.read_text(encoding="utf-8")
        # Strip markdown blockquote markers so phrases that wrap across `>` lines
        # flatten cleanly (e.g. "dispatch a fresh\n> subagent").
        unquoted = re.sub(r"(?m)^\s*>\s?", " ", cls.text)
        cls.flat = " ".join(unquoted.split())

    def test_skill_source_exists(self):
        self.assertTrue(SKILL.is_file(), f"missing skill source: {SKILL}")

    def test_clear_is_labeled_interactive_only(self):
        # /clear must never appear without an interactive-only qualifier nearby.
        self.assertIn("/clear", self.flat)
        self.assertIn("interactive only", self.flat.lower())
        self.assertIn("interactive tui path only", self.flat.lower())

    def test_adapter_mode_forbids_clear(self):
        # The adapter-mode section explicitly forbids emitting /clear.
        self.assertRegex(
            self.flat,
            r"[Dd]o not emit (a )?`?/clear`?.{0,80}context-reset",
        )

    def test_autonomous_continuity_documented(self):
        low = self.flat.lower()
        self.assertIn("fresh executor process", low)
        self.assertIn("handoff", low)

    def test_subagent_dispatch_offered_in_interactive_path(self):
        self.assertIn("dispatch a fresh subagent", self.flat.lower())


if __name__ == "__main__":
    unittest.main()
