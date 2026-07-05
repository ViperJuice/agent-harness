"""ABDVERIFY — the ``/<harness>-advisor-panel`` alias RESOLVES end-to-end (deliverable #4).

The maintainer relies on ``/claude-advisor-panel`` (the slash-command invocation)
still working after the ABDRESOLVE rename to ``advisor-board``. ABDRESOLVE wired
``SKILL_ALIASES`` into ``install_skills``; this proves the RESULT of a real install
(not the branch logic) end-to-end:

1. **fresh install** → a ``<harness>-advisor-panel`` skill dir is installed, its
   ``SKILL.md`` ``name:`` is rewritten to ``<harness>-advisor-panel``, and its body
   is the CURRENT ``advisor-board`` content (installed FROM the canonical source, so
   the slash command resolves to today's advisor-board, not a frozen copy).
2. **stale-overwrite** → a pre-existing, DRIFTED ``<harness>-advisor-panel`` dir is
   REPLACED (content refreshed, orphan files pruned) — never left drifting.
3. **string resolution** → ``canonical_skill_name`` maps every
   ``<harness>-advisor-panel`` back to ``advisor-board`` for callers that resolve by
   name.

What this proves is the INSTALL ARTIFACT: the physical ``<harness>-advisor-panel``
skill dir the harness's slash-command resolver reads. The harness resolving
``/claude-advisor-panel`` is it loading that dir by name; we assert the dir is
present, correctly named, and content-current, which is the condition that makes
that resolution land on advisor-board.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.skill_install import (
    SKILL_ALIASES,
    canonical_skill_name,
    install_skills,
)
from phase_loop_runtime.skill_paths import HARNESS_DEFAULT_SKILL_ROOTS

# The neutral (unprefixed) skills bundle install_skills reads FROM, at the repo root.
_BUNDLE_ROOT = Path(__file__).resolve().parents[2] / "phase-loop-skills"


def _name_line(skill_md: Path) -> str:
    for line in skill_md.read_text(encoding="utf-8").splitlines():
        if line.startswith("name: "):
            return line
    raise AssertionError(f"no name: line in {skill_md}")


def _body_sans_name(skill_md: Path) -> list[str]:
    return [l for l in skill_md.read_text(encoding="utf-8").splitlines() if not l.startswith("name: ")]


class AliasInstallResolvesTests(unittest.TestCase):
    def setUp(self) -> None:
        if not (_BUNDLE_ROOT / "advisor-board" / "SKILL.md").is_file():
            self.skipTest(f"neutral skills bundle not present at {_BUNDLE_ROOT}")

    def _install(self, dest: Path, harness: str = "claude"):
        return install_skills(harness=harness, source=_BUNDLE_ROOT, destination=dest,
                              mode="copy", apply=True)

    def test_fresh_install_creates_prefixed_alias_pointing_at_advisor_board(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d)
            self._install(dest)
            alias_md = dest / "claude-advisor-panel" / "SKILL.md"
            board_md = dest / "claude-advisor-board" / "SKILL.md"
            self.assertTrue(alias_md.is_file(), "the /claude-advisor-panel skill dir must exist")
            self.assertEqual(_name_line(alias_md), "name: claude-advisor-panel")
            # body is the CURRENT advisor-board content (sans the name line each rewrites).
            self.assertEqual(_body_sans_name(alias_md), _body_sans_name(board_md))

    def test_stale_alias_dir_is_replaced_not_left_drifting(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d)
            stale = dest / "claude-advisor-panel"
            stale.mkdir(parents=True)
            (stale / "SKILL.md").write_text("---\nname: claude-advisor-panel\n---\nSTALE PRE-RENAME BODY\n", encoding="utf-8")
            (stale / "ORPHAN.md").write_text("this file must be pruned", encoding="utf-8")
            self._install(dest)
            alias_md = stale / "SKILL.md"
            text = alias_md.read_text(encoding="utf-8")
            self.assertNotIn("STALE PRE-RENAME BODY", text)                 # content refreshed
            self.assertFalse((stale / "ORPHAN.md").exists())                # orphan pruned
            self.assertEqual(_body_sans_name(alias_md),
                             _body_sans_name(dest / "claude-advisor-board" / "SKILL.md"))

    def test_alias_installed_for_every_supported_harness_prefix(self) -> None:
        for harness in sorted(HARNESS_DEFAULT_SKILL_ROOTS):
            with self.subTest(harness=harness), tempfile.TemporaryDirectory() as d:
                dest = Path(d)
                self._install(dest, harness=harness)
                alias_md = dest / f"{harness}-advisor-panel" / "SKILL.md"
                self.assertTrue(alias_md.is_file(), f"{harness}-advisor-panel must install")
                self.assertEqual(_name_line(alias_md), f"name: {harness}-advisor-panel")

    def test_install_records_the_alias_action(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            actions = self._install(Path(d))
        installed = {a.installed_name for a in actions}
        self.assertIn("claude-advisor-panel", installed)
        self.assertIn("claude-advisor-board", installed)

    def test_canonical_skill_name_maps_alias_back_to_advisor_board(self) -> None:
        self.assertEqual(SKILL_ALIASES["advisor-panel"], "advisor-board")
        for harness in ("claude", "codex", "gemini", "opencode"):
            self.assertEqual(canonical_skill_name(f"{harness}-advisor-panel"), "advisor-board")
        self.assertEqual(canonical_skill_name("advisor-panel"), "advisor-board")
        # a non-aliased skill is returned canonical (prefix stripped), unchanged.
        self.assertEqual(canonical_skill_name("claude-advisor-board"), "advisor-board")


if __name__ == "__main__":
    unittest.main()
