import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


class PhaseLoopSkillPlannerContractTest(unittest.TestCase):
    def assert_tokens(self, path: Path, tokens: tuple[str, ...]) -> None:
        text = " ".join(path.read_text(encoding="utf-8").split())
        for token in tokens:
            self.assertIn(token, text, msg=f"{path} missing token: {token}")

    def test_plan_phase_skills_require_machine_checkable_verification(self):
        tokens = (
            "IF-0-VC-2",
            "machine-checkable verification commands",
            "effective `automation.suite_command`",
            "operational evidence artifact",
            "runner-stamped amendment mechanism",
        )
        for path in (
            ROOT / "claude-config" / "claude-skills" / "claude-plan-phase" / "SKILL.md",
            ROOT / "codex-config" / "skills" / "codex-plan-phase" / "SKILL.md",
            ROOT / "gemini-config" / "skills" / "gemini-plan-phase" / "SKILL.md",
            ROOT / "opencode-config" / "skills" / "opencode-plan-phase" / "SKILL.md",
        ):
            with self.subTest(path=path):
                self.assert_tokens(path, tokens)

    def test_plan_detailed_skills_require_verification_or_stamped_evidence(self):
        tokens = (
            "machine-checkable verification commands",
            "effective `automation.suite_command`",
            "operational evidence artifact",
            "runner-stamped amendment mechanism",
        )
        for path in (
            ROOT / "claude-config" / "claude-skills" / "claude-plan-detailed" / "SKILL.md",
            ROOT / "codex-config" / "skills" / "codex-plan-detailed" / "SKILL.md",
            ROOT / "gemini-config" / "skills" / "gemini-plan-detailed" / "SKILL.md",
            ROOT / "opencode-config" / "skills" / "opencode-plan-detailed" / "SKILL.md",
        ):
            with self.subTest(path=path):
                self.assert_tokens(path, tokens)

    def test_roadmap_builder_skills_require_suite_expectation(self):
        tokens = (
            "machine-checkable verification commands",
            "effective `automation.suite_command`",
            "operational evidence artifact",
            "runner-stamped amendment mechanism",
            "proxy evidence requires a roadmap amendment",
        )
        for path in (
            ROOT / "claude-config" / "claude-skills" / "claude-phase-roadmap-builder" / "SKILL.md",
            ROOT / "codex-config" / "skills" / "codex-phase-roadmap-builder" / "SKILL.md",
            ROOT / "gemini-config" / "skills" / "gemini-phase-roadmap-builder" / "SKILL.md",
            ROOT / "opencode-config" / "skills" / "opencode-phase-roadmap-builder" / "SKILL.md",
        ):
            with self.subTest(path=path):
                self.assert_tokens(path, tokens)


if __name__ == "__main__":
    unittest.main()
