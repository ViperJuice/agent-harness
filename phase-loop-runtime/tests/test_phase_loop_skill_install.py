from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.cli import build_parser, main
from phase_loop_runtime.skill_install import REQUIRED_SKILLS, install_skills
from phase_loop_runtime.skill_paths import (
    current_harness,
    resolve_handoff_root,
    resolve_reflection_root,
    resolve_skill_bundle_root,
)


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
BUNDLE = ROOT / "vendor" / "phase-loop-skills"


class PhaseLoopSkillInstallTest(unittest.TestCase):
    def test_resolver_defaults_and_environment_overrides(self):
        self.assertEqual(current_harness("codex"), "codex")
        with patch.dict(os.environ, {"PHASE_LOOP_HARNESS": "gemini"}, clear=False):
            self.assertEqual(current_harness(), "gemini")
        with patch.dict(os.environ, {"PHASE_LOOP_SKILL_BUNDLE": "/tmp/phase-loop-skills"}, clear=False):
            self.assertEqual(resolve_skill_bundle_root("claude"), Path("/tmp/phase-loop-skills"))

        self.assertEqual(resolve_skill_bundle_root("claude"), Path("~/.claude/skills").expanduser())
        self.assertEqual(resolve_skill_bundle_root("codex"), Path("~/.codex/skills").expanduser())
        self.assertEqual(resolve_skill_bundle_root("gemini"), Path("~/.gemini/skills").expanduser())
        self.assertEqual(resolve_skill_bundle_root("opencode"), Path("~/.config/opencode/skills").expanduser())
        self.assertEqual(resolve_reflection_root("codex-plan-phase", "codex").name, "reflections")
        self.assertEqual(resolve_handoff_root(ROOT), ROOT / ".dev-skills" / "handoffs")

    def test_missing_required_base_skill_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                install_skills(harness="codex", source=Path(tmp), destination=Path(tmp) / "out")

    def test_dry_run_reports_metadata_without_mutating_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "codex-skills"
            actions = install_skills(harness="codex", source=BUNDLE, destination=dest, mode="symlink", apply=False)
            self.assertEqual(len(actions), len(REQUIRED_SKILLS))
            self.assertFalse(dest.exists())
            first = actions[0]
            self.assertEqual(first.harness, "codex")
            self.assertIn(first.skill_name, REQUIRED_SKILLS)
            self.assertIn("execute-detailed", REQUIRED_SKILLS)
            self.assertTrue(first.source.endswith(first.skill_name))
            self.assertTrue(first.destination.endswith(first.installed_name))

    def test_dry_run_covers_codex_and_non_codex_required_workflow_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for harness in ("codex", "claude"):
                dest = root / f"{harness}-skills"
                actions = install_skills(harness=harness, source=BUNDLE, destination=dest, apply=False)
                self.assertEqual(
                    tuple(action.installed_name for action in actions),
                    tuple(f"{harness}-{skill}" for skill in REQUIRED_SKILLS),
                )
                self.assertFalse(dest.exists())

    def test_symlink_and_copy_installs_are_idempotent_for_each_harness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for harness in ("claude", "codex", "gemini", "opencode"):
                dest = root / f"{harness}-skills"
                first = install_skills(harness=harness, source=BUNDLE, destination=dest, mode="symlink", apply=True)
                second = install_skills(harness=harness, source=BUNDLE, destination=dest, mode="symlink", apply=True)
                self.assertEqual(len(first), len(REQUIRED_SKILLS))
                self.assertTrue((dest / f"{harness}-execute-detailed").exists())
                self.assertIn(
                    f"name: {harness}-execute-detailed",
                    (dest / f"{harness}-execute-detailed" / "SKILL.md").read_text(encoding="utf-8"),
                )
                self.assertTrue((dest / f"{harness}-execute-phase").exists())
                self.assertIn(
                    f"name: {harness}-execute-phase",
                    (dest / f"{harness}-execute-phase" / "SKILL.md").read_text(encoding="utf-8"),
                )
                self.assertTrue(all(action.action in {"unchanged", "replace"} for action in second))

                copy_dest = root / f"{harness}-copy"
                install_skills(harness=harness, source=BUNDLE, destination=copy_dest, mode="copy", apply=True)
                self.assertTrue((copy_dest / f"{harness}-execute-detailed" / "SKILL.md").is_file())
                self.assertTrue((copy_dest / f"{harness}-plan-phase" / "SKILL.md").is_file())

    def test_cli_install_parser_and_apply_smoke(self):
        args = build_parser().parse_args(["install", "--harness", "codex", "--dry-run"])
        self.assertEqual(args.command, "install")
        self.assertEqual(args.harness, "codex")
        self.assertTrue(args.dry_run)

        with tempfile.TemporaryDirectory() as tmp:
            rc = main(
                [
                    "--repo",
                    str(ROOT),
                    "install",
                    "--harness",
                    "codex",
                    "--source",
                    str(BUNDLE),
                    "--destination",
                    str(Path(tmp) / "skills"),
                    "--copy",
                    "--apply",
                    "--json",
                ]
            )
            self.assertEqual(rc, 0)
            self.assertTrue((Path(tmp) / "skills" / "codex-execute-detailed" / "SKILL.md").exists())
            self.assertTrue((Path(tmp) / "skills" / "codex-execute-phase" / "SKILL.md").exists())

    def test_cross_harness_handoff_root_is_repo_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            claude = resolve_handoff_root(repo) / "claude-plan-phase" / "latest.md"
            codex = resolve_handoff_root(repo) / "codex-plan-phase" / "latest.md"
            self.assertEqual(claude.parents[1], codex.parents[1])
            self.assertEqual(claude.parents[1], repo.resolve() / ".dev-skills" / "handoffs")

    def test_workflow_skills_do_not_hardcode_home_skill_roots(self):
        roots = (
            ROOT / "claude-config" / "claude-skills",
            ROOT / "codex-config" / "skills",
            ROOT / "gemini-config" / "skills",
            ROOT / "opencode-config" / "skills",
        )
        forbidden = ("~/.claude/skills", "~/.codex/skills", "~/.gemini/skills", "~/.config/opencode/skills")
        installed_root_docs = ("skill-editor", "skill-improvement-planner")
        for root in roots:
            for path in root.glob("*/SKILL.md"):
                text = path.read_text(encoding="utf-8")
                with self.subTest(path=path.relative_to(ROOT)):
                    if not path.parent.name.endswith(installed_root_docs):
                        self.assertFalse(any(token in text for token in forbidden))
                    self.assertIn("phase_loop_runtime.skill_paths", text)
