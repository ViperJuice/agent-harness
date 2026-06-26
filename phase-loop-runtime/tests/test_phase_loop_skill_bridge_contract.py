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


class PhaseLoopSkillBridgeContractTest(unittest.TestCase):
    def assert_tokens(self, path: Path, tokens: tuple[str, ...]) -> None:
        text = " ".join(path.read_text(encoding="utf-8").split())
        for token in tokens:
            self.assertIn(token, text, msg=f"{path} missing token: {token}")

    def test_bridge_skills_preserve_artifact_backed_reverdicting(self):
        tokens = (
            "artifact-backed re-verdicting",
            "originally specified runner check",
            "proxy evidence requires a roadmap amendment",
            "canonical `.phase-loop/` state takes precedence",
        )
        for path in (
            ROOT / "claude-config" / "claude-skills" / "claude-phase-loop" / "SKILL.md",
            ROOT / "codex-config" / "skills" / "codex-phase-loop" / "SKILL.md",
            ROOT / "gemini-config" / "skills" / "gemini-phase-loop" / "SKILL.md",
            ROOT / "opencode-config" / "skills" / "opencode-phase-loop" / "SKILL.md",
        ):
            with self.subTest(path=path):
                self.assert_tokens(path, tokens)

    def test_shared_protocol_preserves_artifact_backed_reverdicting(self):
        self.assert_tokens(
            ROOT / "shared" / "phase-loop" / "protocol.md",
            (
                "artifact-backed re-verdicting",
                "originally specified runner check",
                "proxy evidence requires a roadmap amendment",
                "canonical `.phase-loop/` state takes precedence",
                "policy precedence",
            ),
        )


if __name__ == "__main__":
    unittest.main()
