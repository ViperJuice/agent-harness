import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SKILL_PATHS = tuple(
    Path(prefix) / name / "SKILL.md"
    for prefix, names in {
        "claude-config/claude-skills": (
            "claude-execute-phase",
            "claude-plan-phase",
            "claude-plan-detailed",
            "claude-phase-roadmap-builder",
            "claude-skill-editor",
            "claude-skill-improvement-planner",
        ),
        "codex-config/skills": (
            "codex-execute-phase",
            "codex-plan-phase",
            "codex-plan-detailed",
            "codex-phase-roadmap-builder",
            "codex-skill-editor",
            "codex-skill-improvement-planner",
        ),
        "gemini-config/skills": (
            "gemini-execute-phase",
            "gemini-plan-phase",
            "gemini-plan-detailed",
            "gemini-phase-roadmap-builder",
            "gemini-skill-editor",
            "gemini-skill-improvement-planner",
        ),
        "opencode-config/skills": (
            "opencode-execute-phase",
            "opencode-plan-phase",
            "opencode-plan-detailed",
            "opencode-phase-roadmap-builder",
            "opencode-skill-editor",
            "opencode-skill-improvement-planner",
        ),
    }.items()
    for name in names
)

import pytest

# TESTDECOUPLE SL-1 (overlay-dependent): builds a skill/adoption bundle or runs the
# runtime execute path, which resolves the dotfiles skill-source / profile overlay
# (claude-config/*, codex-config/* …) absent standalone. Run-time integration: the
# conftest hook skips it when no dotfiles tree is reachable.
pytestmark = pytest.mark.dotfiles_integration


class PhaseLoopSkillBamlCloseoutTest(unittest.TestCase):
    def test_exact_skill_set_references_baml_closeout_schema(self):
        self.assertEqual(len(SKILL_PATHS), 24)
        for rel_path in SKILL_PATHS:
            with self.subTest(path=str(rel_path)):
                text = (ROOT / rel_path).read_text(encoding="utf-8")
                self.assertRegex(text, r"EmitPhaseCloseout|emit_phase_closeout\\.baml")

    def test_skill_files_do_not_retain_automation_field_yaml_ceremony(self):
        for rel_path in SKILL_PATHS:
            with self.subTest(path=str(rel_path)):
                text = (ROOT / rel_path).read_text(encoding="utf-8")
                self.assertNotIn("```yaml\nautomation:", text)


if __name__ == "__main__":
    unittest.main()
