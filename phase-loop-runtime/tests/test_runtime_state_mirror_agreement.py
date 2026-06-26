"""PROTO (roadmap v40) — harness mirror agreement.

Each of the four harness ``*-config/shared/runtime-state.md`` mirrors must restate
the frozen closeout-exception vocabulary so every harness agrees with the canonical
protocol (Cross-Cutting Principle 6).
"""

from pathlib import Path
import unittest

import phase_loop_runtime.models as m

REPO_ROOT = Path(__file__).resolve().parents[3]
MIRRORS = (
    "claude-config/shared/runtime-state.md",
    "codex-config/shared/runtime-state.md",
    "gemini-config/shared/runtime-state.md",
    "opencode-config/shared/runtime-state.md",
)

import pytest

# TESTDECOUPLE SL-1 (overlay-dependent): builds a skill/adoption bundle or runs the
# runtime execute path, which resolves the dotfiles skill-source / profile overlay
# (claude-config/*, codex-config/* …) absent standalone. Run-time integration: the
# conftest hook skips it when no dotfiles tree is reachable.
pytestmark = pytest.mark.dotfiles_integration


class RuntimeStateMirrorAgreementTest(unittest.TestCase):
    def test_each_mirror_carries_closeout_exception_vocabulary(self):
        required_terms = (
            list(m.SENSITIVITY_CLASSES)            # all 8 taxonomy members
            + list(m.CLOSEOUT_EXCEPTION_KINDS)     # soft, break_glass
            + ["closeout_scope_violation", m.CLOSEOUT_EXCEPTIONS_METADATA_KEY]
        )
        for rel in MIRRORS:
            path = REPO_ROOT / rel
            self.assertTrue(path.exists(), f"missing mirror: {rel}")
            text = path.read_text()
            missing = [term for term in required_terms if term not in text]
            self.assertEqual(missing, [], f"{rel} missing closeout-exception terms: {missing}")


if __name__ == "__main__":
    unittest.main()
