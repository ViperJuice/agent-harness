import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
from phase_loop_runtime.injection import (
    CLAUDE_CONTEXT_MAX_CHARS,
    CLAUDE_CONTEXT_MAX_LINES,
    HARNESS_ACTION_SKILLS,
    build_lane_prompt_bundle,
    build_prompt_bundle,
    render_harness_reducer_context,
    render_harness_review_context,
)
from phase_loop_runtime.models import HarnessLaneAssignment
from phase_loop_test_utils import make_repo


class PhaseLoopInjectionTest(unittest.TestCase):
    def test_harness_action_skills_use_only_canonical_workflow_names(self):
        matrix = (ROOT / "docs" / "phase-loop" / "harness-skill-matrix.md").read_text(encoding="utf-8")

        for harness in ("codex", "claude", "gemini", "opencode"):
            for action, skills in HARNESS_ACTION_SKILLS[harness].items():
                with self.subTest(harness=harness, action=action):
                    self.assertTrue(skills)
                    for skill in skills:
                        self.assertIn(f"`{skill}`", matrix)
                        self.assertNotIn(skill, {"plan-phase", "execute-phase"})

        for action, skills in HARNESS_ACTION_SKILLS["pi"].items():
            with self.subTest(harness="pi", action=action):
                self.assertLessEqual(
                    set(skills),
                    {"phase-loop-supervisor", "phase-loop-repair", "phase-loop-closeout"},
                )

        serialized = repr(HARNESS_ACTION_SKILLS)
        self.assertNotIn(".pipeline/skills", serialized)
        self.assertNotIn(".codex/phase-loop", serialized)

    def test_codex_prompt_bundle_uses_repo_skill_bundle_metadata_without_inlining(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            bundle = build_prompt_bundle(
                repo=repo,
                harness_target="codex",
                action="plan",
                roadmap=roadmap,
                phase="RUNNER",
            )
            self.assertEqual(bundle.workflow_command, f"codex-plan-phase {roadmap} RUNNER")
            self.assertEqual(bundle.injection_mode, "prompt_only")
            self.assertEqual(bundle.expected_skill_pack, ("codex-plan-phase",))
            self.assertIn("EmitPhaseCloseout", bundle.body)
            self.assertIn(bundle.workflow_command, bundle.render_context())
            self.assertIn("## Skill: codex-plan-phase", bundle.render_context())
            self.assertTrue(bundle.skill_bundle_sha256)

    def test_claude_prompt_bundle_switches_to_harness_specific_skill_and_inline_fallback(self):
        repo = ROOT
        roadmap = repo / "specs" / "phase-plans-v3.md"
        bundle = build_prompt_bundle(
            repo=repo,
            harness_target="claude",
            action="execute",
            roadmap=roadmap,
            phase="RUNNER",
            plan=repo / "plans" / "phase-plan-v1-RUNNER.md",
            body="Do the bounded phase work.",
        )
        self.assertEqual(bundle.workflow_command, f"claude-execute-phase {repo / 'plans' / 'phase-plan-v1-RUNNER.md'}")
        self.assertEqual(bundle.injection_mode, "inline")
        self.assertEqual(bundle.fallback_mode, "context_file")
        self.assertEqual(
            bundle.expected_skill_pack,
            (
                "claude-phase-roadmap-builder",
                "claude-plan-phase",
                "claude-execute-phase",
                "claude-phase-loop",
            ),
        )
        self.assertIn("Do the bounded phase work.", bundle.body)
        self.assertIn("EmitPhaseCloseout", bundle.body)
        self.assertEqual(bundle.skill_bundle_id, "phase-loop-claude-execute")
        self.assertIn("Do the bounded phase work.", bundle.render_context())
        self.assertIn("## Repo-owned Claude bundle", bundle.render_context())
        self.assertIn("phase-loop-claude-execute", bundle.render_context())
        self.assertIn("`claude-execute-phase`", bundle.render_context())
        self.assertIn("Do not list or read `claude-bundle/plugin/skills/**`", bundle.render_context())
        self.assertNotIn("## Skill:", bundle.render_context())
        self.assertLessEqual(bundle.context_line_count(), CLAUDE_CONTEXT_MAX_LINES)
        self.assertLessEqual(bundle.context_char_count(), CLAUDE_CONTEXT_MAX_CHARS)

    def test_claude_roadmap_context_blocks_repo_local_dot_claude_state_writes(self):
        repo = ROOT
        roadmap = repo / "specs" / "phase-plans-v3.md"
        bundle = build_prompt_bundle(
            repo=repo,
            harness_target="claude",
            action="roadmap",
            roadmap=roadmap,
        )

        self.assertIn("do not create or edit repo-local `.claude/**` state", bundle.render_context())
        self.assertIn("`.claude/docs-catalog.json`", bundle.render_context())
        self.assertIn("phase artifact required by this run", bundle.render_context())

    def test_missing_installed_skill_roots_warn_without_blocking(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            with patch("phase_loop_runtime.skill_inventory.discover_installed_skill_roots", return_value=()):
                bundle = build_prompt_bundle(
                    repo=repo,
                    harness_target="opencode",
                    action="plan",
                    roadmap=roadmap,
                    phase="RUNNER",
                )
            self.assertIn("~/.config/opencode/skills", bundle.recommended_installed_roots)
            self.assertTrue(bundle.installed_skill_warnings)
            self.assertIn("installed bridge root missing", bundle.installed_skill_warnings[0])
            self.assertEqual(bundle.bridge_skill_inventory[0]["parity_status"], "missing_root")

    def test_external_repo_falls_back_to_runner_skill_source(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            bundle = build_prompt_bundle(
                repo=repo,
                harness_target="gemini",
                action="plan",
                roadmap=roadmap,
                phase="RUNNER",
            )
            self.assertIn("## Skill: gemini-plan-phase", bundle.render_context())
            self.assertNotIn("repo source missing", "\n".join(bundle.installed_skill_warnings))

    def test_pi_prompt_bundle_uses_repo_local_package_contract(self):
        repo = ROOT
        roadmap = repo / "specs" / "phase-plans-v13.md"
        plan = repo / "plans" / "phase-plan-v13-DFPIAGENT.md"
        bundle = build_prompt_bundle(
            repo=repo,
            harness_target="pi",
            action="execute",
            roadmap=roadmap,
            phase="DFPIAGENT",
            plan=plan,
            body="Execute the selected scheduler-assigned lane.",
        )

        self.assertEqual(bundle.workflow_command, f"pi-agent-watch --phase-plan {plan} --max-phases 1 --closeout-mode manual")
        self.assertEqual(bundle.injection_mode, "context_file")
        self.assertEqual(bundle.fallback_mode, "manual")
        self.assertEqual(bundle.expected_skill_pack, ("phase-loop-supervisor", "phase-loop-closeout"))
        context = bundle.render_context()
        self.assertIn("Repo-owned Pi Agent bundle", context)
        self.assertIn("phase-loop-pi", context)
        self.assertIn("pi-config", context)
        self.assertIn("explicit system prompt", context)
        self.assertIn("tool policy", context)
        self.assertIn("allowed writes", context)
        self.assertIn("read-only refs", context)
        self.assertIn("forbidden refs", context)
        self.assertIn("output roots", context)
        self.assertIn("verification intent", context)
        self.assertIn("Greenfield authority", context)
        self.assertIn("governed-pipeline assignment fields", context)
        self.assertIn("worktree path", context)
        self.assertIn("fallback reason", context)
        self.assertIn("phase_loop_closeout.v1", context)

    def test_mismatched_installed_skill_tree_warns_without_blocking(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = make_repo(root / "repo-root")
            roadmap = repo / "specs" / "phase-plans-v1.md"
            installed_root = root / "installed"
            skill_dir = installed_root / "gemini-phase-loop"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("different installed copy\n", encoding="utf-8")
            with patch("phase_loop_runtime.skill_inventory.discover_installed_skill_roots", return_value=(str(installed_root),)):
                bundle = build_prompt_bundle(
                    repo=ROOT,
                    harness_target="gemini",
                    action="plan",
                    roadmap=roadmap,
                    phase="RUNNER",
                )
            self.assertTrue(bundle.installed_skill_warnings)
            self.assertIn("installed bridge skill drifted from repo source", bundle.installed_skill_warnings[0])
            self.assertEqual(bundle.bridge_skill_inventory[0]["parity_status"], "drifted")

    def test_lane_prompt_bundle_scopes_each_harness_to_selected_lane(self):
        assignment = HarnessLaneAssignment(
            phase="HARNESSLANE",
            lane_id="SL-1",
            work_unit_kind="lane_execute",
            prompt_kind="implementation",
            owned_files=("vendor/phase-loop-runtime/src/phase_loop_runtime/injection.py",),
            consumed_interfaces=("HarnessLaneAssignment", "PromptBundle"),
            execution_policy={"executor": "codex", "effort": "high"},
            metadata={"schema": "harness_lane_assignment.v1"},
        )
        for harness in ("codex", "claude", "gemini", "opencode", "pi", "command"):
            bundle = build_lane_prompt_bundle(
                repo=ROOT,
                harness_target=harness,
                action="execute",
                roadmap=ROOT / "specs" / "phase-plans-v9.md",
                plan=ROOT / "plans" / "phase-plan-v9-HARNESSLANE.md",
                assignment=assignment,
            )
            context = bundle.render_context()
            self.assertIn("lane_id: `SL-1`", context)
            self.assertIn("owned_files: `vendor/phase-loop-runtime/src/phase_loop_runtime/injection.py`", context)
            self.assertIn("consumed_interfaces: `HarnessLaneAssignment, PromptBundle`", context)
            self.assertIn("work_unit_kind: `lane_execute`", context)
            self.assertIn("execution_policy:", context)
            self.assertIn("Delegation broker contract:", context)
            self.assertIn("Do not spawn peer harnesses directly.", context)
            self.assertIn("EmitPhaseCloseout", context)
            self.assertIn("Do not widen into whole-phase implementation authority", context)

    def test_dfparsoak_prompt_context_records_route_model_effort_and_current_prompt_inputs(self):
        assignment = HarnessLaneAssignment(
            phase="DFPARSOAK",
            lane_id="SL-2",
            work_unit_kind="lane_execute",
            prompt_kind="implementation",
            owned_files=("vendor/phase-loop-runtime/src/phase_loop_runtime/injection.py",),
            consumed_interfaces=(
                "docs/phase-loop/dfpromptsync-contract-map.md",
                "docs/phase-loop/dfpromptsync-readiness.md",
            ),
            execution_policy={"execution_policy_source": "phase-plan"},
            harness_route="gemini",
            model="auto",
            effort="medium",
            fallback_reason="gemini_cli_fallback",
        )

        context = build_lane_prompt_bundle(
            repo=ROOT,
            harness_target="gemini",
            action="execute",
            roadmap=ROOT / "specs" / "phase-plans-v13.md",
            plan=ROOT / "plans" / "phase-plan-v13-DFPARSOAK.md",
            assignment=assignment,
        ).render_context()

        self.assertIn("harness_route: `gemini`", context)
        self.assertIn("model: `auto`", context)
        self.assertIn("effort: `medium`", context)
        self.assertIn("fallback_reason: `gemini_cli_fallback`", context)
        self.assertIn("docs/phase-loop/dfpromptsync-contract-map.md", context)
        self.assertIn("docs/phase-loop/dfpromptsync-readiness.md", context)
        self.assertNotIn("older downstream DFPARSOAK plan", context)

    def test_review_and_reducer_contexts_are_distinct_from_implementation(self):
        review_assignment = HarnessLaneAssignment(
            phase="HARNESSLANE",
            lane_id="SL-2",
            work_unit_kind="lane_review",
            prompt_kind="review",
            owned_files=("vendor/phase-loop-runtime/tests/test_phase_loop_injection.py",),
        )
        reducer_assignment = HarnessLaneAssignment(
            phase="HARNESSLANE",
            lane_id="SL-5",
            work_unit_kind="phase_reducer",
            prompt_kind="reducer",
            owned_files=(),
        )

        self.assertIn("must not make production edits", render_harness_review_context(review_assignment))
        self.assertIn("must not claim write authority", render_harness_reducer_context(reducer_assignment))


if __name__ == "__main__":
    unittest.main()
