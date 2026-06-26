import unittest
from pathlib import Path


import pytest
from _dotfiles_tree import dotfiles_tree_present

# TESTDECOUPLE SL-1: this file reads dotfiles fleet paths (absent in the
# extracted agent-harness layout). Skip at MODULE level before any such read so
# collection does not error standalone; the marker keeps it deselected by
# `pytest -m "not dotfiles_integration"` and the conftest run-time hook.
if not dotfiles_tree_present():
    pytest.skip("requires dotfiles tree", allow_module_level=True)

pytestmark = pytest.mark.dotfiles_integration

ROOT = Path(__file__).resolve().parents[3]


class PhaseLoopSkillExecuteContractTest(unittest.TestCase):
    def assert_tokens(self, path: Path, tokens: tuple[str, ...]) -> None:
        text = " ".join(path.read_text(encoding="utf-8").split())
        for token in tokens:
            self.assertIn(token, text, msg=f"{path} missing token: {token}")

    def test_execute_phase_skills_require_runner_verification_artifact(self):
        tokens = (
            "verification_artifact_path",
            "artifact summary line",
            "must not report `verification_status=passed`",
            "dependency-manifest install refresh",
            "full suite before closeout",
            "originally specified runner check",
            "proxy evidence requires a roadmap or plan amendment",
        )
        for path in (
            ROOT / "claude-config" / "claude-skills" / "claude-execute-phase" / "SKILL.md",
            ROOT / "codex-config" / "skills" / "codex-execute-phase" / "SKILL.md",
            ROOT / "gemini-config" / "skills" / "gemini-execute-phase" / "SKILL.md",
            ROOT / "opencode-config" / "skills" / "opencode-execute-phase" / "SKILL.md",
        ):
            with self.subTest(path=path):
                self.assert_tokens(path, tokens)

    def test_execute_detailed_skills_require_runner_verification_artifact(self):
        tokens = (
            "verification_artifact_path",
            "artifact summary line",
            "must not report `verification_status=passed`",
            "dependency-manifest install refresh",
            "full suite before closeout",
            "originally specified runner check",
            "proxy evidence requires a roadmap or plan amendment",
        )
        for path in (
            ROOT / "claude-config" / "claude-skills" / "claude-execute-detailed" / "SKILL.md",
            ROOT / "codex-config" / "skills" / "codex-execute-detailed" / "SKILL.md",
            ROOT / "gemini-config" / "skills" / "gemini-execute-detailed" / "SKILL.md",
            ROOT / "opencode-config" / "skills" / "opencode-execute-detailed" / "SKILL.md",
        ):
            with self.subTest(path=path):
                self.assert_tokens(path, tokens)


if __name__ == "__main__":
    unittest.main()
