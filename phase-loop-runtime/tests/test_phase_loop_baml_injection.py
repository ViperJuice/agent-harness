import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.baml_modular import build_baml_request, parse_baml_response
from phase_loop_runtime.models import BLOCKER_CLASSES, PHASE_STATUSES
from phase_loop_runtime.injection import build_lane_prompt_bundle, build_prompt_bundle
from phase_loop_runtime.models import HarnessLaneAssignment


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


class PhaseLoopBamlInjectionTest(unittest.TestCase):
    def test_prompt_bundles_include_baml_closeout_prompt_and_adapter_constraints(self):
        for action in ("plan", "execute", "repair", "review"):
            with self.subTest(action=action):
                bundle = build_prompt_bundle(
                    repo=ROOT,
                    harness_target="claude",
                    action=action,
                    roadmap=ROOT / "specs/phase-plans-v20.md",
                    phase="BAMLBASE",
                    plan=ROOT / "plans/phase-plan-v20-BAMLBASE.md",
                    body="phase-loop launch body",
                )
                context = bundle.render_context()
                self.assertIn("EmitPhaseCloseout", context)
                self.assertIn("vendor/phase-loop-runtime/src/phase_loop_runtime/baml_src/emit_phase_closeout.baml", context)
                self.assertIn("IF-0-BAMLBASE-1", context)
                self.assertIn("Phase-loop adapter constraints", context)

    def test_lane_prompt_uses_baml_reference_instead_of_field_list_source_of_truth(self):
        assignment = HarnessLaneAssignment(
            phase="BAMLBASE",
            lane_id="SL-3",
            work_unit_kind="lane_execute",
            prompt_kind="implementation",
            owned_files=("vendor/phase-loop-runtime/src/phase_loop_runtime/injection.py",),
            consumed_interfaces=("IF-0-BAMLBASE-3",),
        )
        context = build_lane_prompt_bundle(
            repo=ROOT,
            harness_target="codex",
            action="execute",
            roadmap=ROOT / "specs/phase-plans-v20.md",
            plan=ROOT / "plans/phase-plan-v20-BAMLBASE.md",
            assignment=assignment,
        ).render_context()
        self.assertIn("BAML closeout schema instruction:", context)
        self.assertIn("EmitPhaseCloseout", context)
        self.assertIn("Do not spawn peer harnesses directly.", context)
        self.assertNotIn("Required shared automation closeout fields:", context)

    def test_baml_closeout_literals_align_with_runtime_models(self):
        prompt = build_baml_request(
            "EmitPhaseCloseout",
            {
                "phase_alias": "SPECGATE",
                "plan_produces": ["IF-0-SPECGATE-1"],
                "plan_owned_files": [],
                "closeout_commit_sha": None,
            },
        ).prompt
        for literal in PHASE_STATUSES:
            self.assertIn(literal, prompt)
        self.assertIn("dry_run is an event-level execution mode", prompt)
        for literal in BLOCKER_CLASSES:
            self.assertIn(literal, prompt)

    def test_prompt_bundle_without_plan_keeps_closeout_instruction_renderable(self):
        context = build_prompt_bundle(
            repo=ROOT,
            harness_target="codex",
            action="roadmap",
            roadmap=ROOT / "specs/phase-plans-v42.md",
            phase="SUBSTRATE",
            plan=None,
            body="build roadmap",
        ).render_context()

        self.assertIn("EmitPhaseCloseout", context)
        self.assertIn("produced_if_gates", context)

    def test_prompt_bundle_lists_only_plan_produced_if_gates(self):
        plan_text = """# COLLABBOOT

## Context
`IF-0-INSTRINV-1` and `IF-0-SPECGATE-1` are available upstream.

## Interface Freeze Gates
- [ ] IF-0-COLLABBOOT-1 - collaborator boundary

## Lanes

### SL-0 - Docs
- **Interfaces provided**: `IF-0-COLLABBOOT-1`
- **Interfaces consumed**: `IF-0-INSTRINV-1`; `IF-0-SPECGATE-1`.
"""
        with tempfile.TemporaryDirectory() as td:
            plan = Path(td) / "phase-plan-v42-COLLABBOOT.md"
            plan.write_text(plan_text, encoding="utf-8")
            context = build_prompt_bundle(
                repo=ROOT,
                harness_target="codex",
                action="execute",
                roadmap=ROOT / "specs/phase-plans-v42.md",
                phase="COLLABBOOT",
                plan=plan,
                body="execute COLLABBOOT",
            ).render_context()

        self.assertIn("IF-0-COLLABBOOT-1", context)
        self.assertNotIn("IF-0-INSTRINV-1", context)
        self.assertNotIn("IF-0-SPECGATE-1", context)

    def test_baml_closeout_rejects_dry_run_terminal_status(self):
        with self.assertRaises(Exception):
            parse_baml_response(
                "EmitPhaseCloseout",
                '{"terminal_status":"dry_run","verification_status":"not_run","dirty_paths":[],"produced_if_gates":[],"next_action":null,"blocker_class":null,"blocker_summary":null,"human_required":null,"required_human_inputs":[]}',
            )


if __name__ == "__main__":
    unittest.main()
