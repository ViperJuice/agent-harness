"""LEGACY (CLEANSHIP P7) lane (b) — the advisor `-panel`/`-board` twins are ONE
canonical skill per harness plus an alias, and cannot drift.

The audit's hazard was agents GUESSING between two apparently-different skills. Prior
ABDRESOLVE work collapsed that: the canonical source is `<harness>-advisor-board`,
and `<harness>-advisor-panel` is installed as a byte-identical ALIAS of it
(`SKILL_ALIASES`), refreshed from the canonical on every run so it can never dangle.
This test makes the collapse an explicit, enforced invariant (the lead's exit
criterion): the alias set is exactly `advisor-panel → advisor-board`, every typed
`-advisor-panel` name resolves to the board canonical, and every packaged twin is
byte-identical to its `-advisor-board` canonical except the `name:` line.
"""
from __future__ import annotations

import unittest
from pathlib import Path

import phase_loop_runtime
from phase_loop_runtime.build_bundle import ACTIVE_HARNESSES
from phase_loop_runtime.skill_install import REQUIRED_SKILLS, SKILL_ALIASES, canonical_skill_name

# Resolve the packaged bundle via the INSTALLED package location (works both from
# the src tree AND from a wheel install — the standalone-from-wheel clean-room gate
# has no src/ tree).
_BUNDLE = Path(phase_loop_runtime.__file__).resolve().parent / "skills_bundle"


class AdvisorTwinAliasTest(unittest.TestCase):
    def test_alias_set_is_exactly_panel_to_board(self):
        # The ONLY alias is the historical advisor-panel → advisor-board mapping.
        self.assertEqual(SKILL_ALIASES, {"advisor-panel": "advisor-board"})
        # The canonical is a REQUIRED skill; the alias name is NOT (it is not authored).
        self.assertIn("advisor-board", REQUIRED_SKILLS)
        self.assertNotIn("advisor-panel", REQUIRED_SKILLS)

    def test_typed_panel_names_resolve_to_board_canonical(self):
        self.assertEqual(canonical_skill_name("advisor-panel"), "advisor-board")
        self.assertEqual(canonical_skill_name("advisor-board"), "advisor-board")
        for harness in ACTIVE_HARNESSES:
            self.assertEqual(canonical_skill_name(f"{harness}-advisor-panel"), "advisor-board")
            self.assertEqual(canonical_skill_name(f"{harness}-advisor-board"), "advisor-board")

    def test_packaged_twins_are_byte_identical_except_name(self):
        # Each harness ships both dirs; the -panel alias body is byte-identical to its
        # -board canonical apart from the single `name:` frontmatter line.
        for harness in ACTIVE_HARNESSES:
            board = (_BUNDLE / f"{harness}-advisor-board" / "SKILL.md").read_text().splitlines()
            panel = (_BUNDLE / f"{harness}-advisor-panel" / "SKILL.md").read_text().splitlines()
            self.assertEqual(len(board), len(panel), f"{harness}: twin line counts differ")
            diffs = [(b, p) for b, p in zip(board, panel) if b != p]
            self.assertEqual(len(diffs), 1, f"{harness}: twins differ on >1 line: {diffs}")
            b_line, p_line = diffs[0]
            self.assertEqual(b_line, f"name: {harness}-advisor-board")
            self.assertEqual(p_line, f"name: {harness}-advisor-panel")


if __name__ == "__main__":
    unittest.main()
