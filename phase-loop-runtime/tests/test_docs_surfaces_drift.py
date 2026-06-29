"""docs-freshness v4 P3 — drift guard for the vendored release-surface taxonomy.

`validate_plan_doc.py` is a stdlib-only bundled skill script that CANNOT import
the runtime, so it carries a VENDORED copy of
`docs_surfaces.RELEASE_AFFECTING_PATTERNS`. This test fails the moment that copy
diverges from canonical — the only drift-proof option for a vendored copy
(combined with the #12 byte-identity guard that keeps the shipped bundle equal to
these canonical sources).

Skips when the sibling `phase-loop-skills/` is absent (standalone-from-wheel,
TESTDECOUPLE) — same posture as the #12 drift guard.
"""
import ast
import unittest
from pathlib import Path

from phase_loop_runtime.docs_surfaces import RELEASE_AFFECTING_PATTERNS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = (
    _REPO_ROOT / "phase-loop-skills" / "plan-phase" / "scripts" / "validate_plan_doc.py",
    _REPO_ROOT / "phase-loop-skills" / "execute-phase" / "scripts" / "validate_plan_doc.py",
)
_CONST = "_VENDORED_RELEASE_AFFECTING_PATTERNS"


def _extract_tuple(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        targets = (
            [node.target] if isinstance(node, ast.AnnAssign) else
            list(node.targets) if isinstance(node, ast.Assign) else []
        )
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            return tuple(ast.literal_eval(node.value))
    return None


class DocsSurfacesDriftTest(unittest.TestCase):
    def test_vendored_release_patterns_match_canonical(self):
        checked = 0
        for path in _SCRIPTS:
            if not path.is_file():
                continue  # standalone-from-wheel: canonical sources absent
            vendored = _extract_tuple(path, _CONST)
            self.assertIsNotNone(vendored, f"missing {_CONST} in {path}")
            self.assertEqual(
                vendored,
                RELEASE_AFFECTING_PATTERNS,
                f"DRIFT: {path} vendored {_CONST} != docs_surfaces canonical",
            )
            checked += 1
        if checked == 0:
            self.skipTest("phase-loop-skills/ sources absent (standalone-from-wheel)")


if __name__ == "__main__":
    unittest.main()
