"""#26 item 2 — install-time re-expansion of the ``<harness>-`` body placeholder.

The canonical bundle keeps skill-name references harness-neutral as
``<harness>-<skill>`` in prose (so the base collapses and avoids per-harness
override bloat). On install for a concrete harness, that placeholder must be
re-expanded to the real per-harness form (e.g. ``claude-execute-phase``) in the
SKILL.md BODY — not only in the ``name:`` frontmatter.

These tests build a minimal synthetic bundle so they run in standalone CI
(the real bundle-source install tests skip without the dotfiles tree).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from phase_loop_runtime.skill_install import REQUIRED_SKILLS, install_skills


def _make_bundle(root: Path) -> Path:
    for name in REQUIRED_SKILLS:
        skill_dir = root / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            f"name: {name}\n"
            "description: synthetic test skill\n"
            "---\n\n"
            "The prompt begins with `<harness>-execute-phase <plan>`; then run\n"
            "`<harness>-plan-phase` and `<harness>-skill-editor`.\n",
            encoding="utf-8",
        )
    return root


def test_install_expands_harness_placeholder_in_body_for_claude():
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dest:
        _make_bundle(Path(src))
        install_skills(
            harness="claude", source=Path(src), destination=Path(dest), mode="copy", apply=True
        )
        body = (Path(dest) / "claude-execute-phase" / "SKILL.md").read_text(encoding="utf-8")
        assert "<harness>-" not in body, (
            "#26 item 2 VIOLATED: the <harness>- body placeholder was not re-expanded on install"
        )
        assert "claude-execute-phase" in body
        assert "claude-plan-phase" in body
        assert "claude-skill-editor" in body
        # name frontmatter is still rewritten to the installed name
        assert "name: claude-execute-phase" in body


def test_install_expands_harness_placeholder_for_non_claude_harness():
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dest:
        _make_bundle(Path(src))
        install_skills(
            harness="codex", source=Path(src), destination=Path(dest), mode="copy", apply=True
        )
        body = (Path(dest) / "codex-execute-phase" / "SKILL.md").read_text(encoding="utf-8")
        assert "<harness>-" not in body
        assert "codex-execute-phase" in body
        assert "name: codex-execute-phase" in body
