import tempfile
import unittest
from pathlib import Path

from phase_loop_test_utils import ROOT
from phase_loop_runtime.plan_manifest import (
    DotfilesPlanEntry,
    DotfilesPlanLifecycleEvent,
    DotfilesPlanRef,
    append_entry,
    read_manifest,
    update_lifecycle,
    validate_manifest,
)


PLAN_SKILLS = (
    ("claude-plan-detailed", ROOT / "claude-config/claude-skills/claude-plan-detailed/SKILL.md"),
    ("codex-plan-detailed", ROOT / "codex-config/skills/codex-plan-detailed/SKILL.md"),
    ("gemini-plan-detailed", ROOT / "gemini-config/skills/gemini-plan-detailed/SKILL.md"),
    ("opencode-plan-detailed", ROOT / "opencode-config/skills/opencode-plan-detailed/SKILL.md"),
)

EXECUTE_SKILLS = (
    ("claude-execute-detailed", ROOT / "claude-config/claude-skills/claude-execute-detailed/SKILL.md"),
    ("codex-execute-detailed", ROOT / "codex-config/skills/codex-execute-detailed/SKILL.md"),
    ("gemini-execute-detailed", ROOT / "gemini-config/skills/gemini-execute-detailed/SKILL.md"),
    ("opencode-execute-detailed", ROOT / "opencode-config/skills/opencode-execute-detailed/SKILL.md"),
)

PHASE_PLAN_SKILLS = (
    ("claude-plan-phase", ROOT / "claude-config/claude-skills/claude-plan-phase/SKILL.md"),
    ("codex-plan-phase", ROOT / "codex-config/skills/codex-plan-phase/SKILL.md"),
    ("gemini-plan-phase", ROOT / "gemini-config/skills/gemini-plan-phase/SKILL.md"),
    ("opencode-plan-phase", ROOT / "opencode-config/skills/opencode-plan-phase/SKILL.md"),
)

PHASE_EXECUTE_SKILLS = (
    ("claude-execute-phase", ROOT / "claude-config/claude-skills/claude-execute-phase/SKILL.md"),
    ("codex-execute-phase", ROOT / "codex-config/skills/codex-execute-phase/SKILL.md"),
    ("gemini-execute-phase", ROOT / "gemini-config/skills/gemini-execute-phase/SKILL.md"),
    ("opencode-execute-phase", ROOT / "opencode-config/skills/opencode-execute-phase/SKILL.md"),
)

import pytest

# TESTDECOUPLE SL-1 (overlay-dependent): builds a skill/adoption bundle or runs the
# runtime execute path, which resolves the dotfiles skill-source / profile overlay
# (claude-config/*, codex-config/* …) absent standalone. Run-time integration: the
# conftest hook skips it when no dotfiles tree is reachable.
pytestmark = pytest.mark.dotfiles_integration


class SkillPlanManifestWriteTest(unittest.TestCase):
    def test_all_canonical_detailed_skills_have_stable_marker(self):
        for skill_name, path in (*PLAN_SKILLS, *EXECUTE_SKILLS):
            with self.subTest(skill=skill_name):
                self.assertIn("plan-manifest append", path.read_text(encoding="utf-8"))

    def test_all_canonical_phase_skills_have_stable_marker(self):
        for skill_name, path in (*PHASE_PLAN_SKILLS, *PHASE_EXECUTE_SKILLS):
            with self.subTest(skill=skill_name):
                self.assertIn("plan-manifest append", path.read_text(encoding="utf-8"))

    def test_plan_detailed_skills_describe_manifest_append_contract(self):
        required_terms = (
            "append_entry",
            "type=detailed",
            "status=committed",
            "slug",
            "file",
            "created_at",
            "owner_skill",
            "task_summary",
            "acceptance_criteria_count",
            "handoff_path",
            "best-effort",
            "non-fatal",
            "ledger warning",
            "mandatory reflection",
        )
        for skill_name, path in PLAN_SKILLS:
            with self.subTest(skill=skill_name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("### Manifest write", text)
                for term in required_terms:
                    self.assertIn(term, text)

    def test_execute_detailed_skills_describe_lifecycle_contract(self):
        required_terms = (
            "update_lifecycle",
            "executing",
            "completed",
            "failed",
            "verification metadata",
            "reflection metadata",
            "best-effort",
            "non-fatal",
            "ledger warning",
            "mandatory reflection",
        )
        for skill_name, path in EXECUTE_SKILLS:
            with self.subTest(skill=skill_name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("### Manifest lifecycle", text)
                for term in required_terms:
                    self.assertIn(term, text)

    def test_plan_phase_skills_describe_manifest_append_contract(self):
        required_terms = (
            "append_entry",
            "type=phase",
            "status=committed",
            "slug",
            "file",
            "created_at",
            "owner_skill",
            "handoff_ref",
            "roadmap_ref",
            "phase_alias",
            "if_gates_produced",
            "lanes",
            "best-effort",
            "non-fatal",
            "ledger warning",
            "mandatory reflection",
        )
        for skill_name, path in PHASE_PLAN_SKILLS:
            with self.subTest(skill=skill_name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("### Manifest write", text)
                for term in required_terms:
                    self.assertIn(term, text)

    def test_execute_phase_skills_describe_lifecycle_contract(self):
        required_terms = (
            "update_lifecycle",
            "executing",
            "completed",
            "failed",
            "verification metadata",
            "reflection metadata",
            "produced-gate metadata",
            "best-effort",
            "non-fatal",
            "ledger warning",
            "mandatory reflection",
        )
        for skill_name, path in PHASE_EXECUTE_SKILLS:
            with self.subTest(skill=skill_name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("### Manifest lifecycle", text)
                for term in required_terms:
                    self.assertIn(term, text)

    def test_detailed_plan_manifest_roundtrip_models_skill_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            plan = repo / "plans/detailed-dh-example.md"
            plan.parent.mkdir()
            plan.write_text(
                "# Detailed plan: DH example\n\n## Acceptance criteria\n- [ ] Example criterion\n",
                encoding="utf-8",
            )
            timestamp = "2026-05-30T00:00:00Z"
            append_entry(
                repo,
                DotfilesPlanEntry(
                    slug="detailed-dh-example",
                    file="plans/detailed-dh-example.md",
                    type="detailed",
                    status="committed",
                    created_at=timestamp,
                    updated_at=timestamp,
                    owner_skill="codex-plan-detailed",
                    handoff_ref=".dev-skills/handoffs/codex-plan-detailed/run.md",
                    task_summary="Example detailed plan manifest write",
                    acceptance_criteria_count=1,
                    lifecycle=(
                        DotfilesPlanLifecycleEvent(
                            transition="committed",
                            by="codex-plan-detailed",
                            at=timestamp,
                            metadata={"handoff_path": ".dev-skills/handoffs/codex-plan-detailed/run.md"},
                        ),
                    ),
                ),
            )

            update_lifecycle(
                repo,
                "detailed-dh-example",
                "executing",
                "codex-execute-detailed",
                {"run_id": "run-1", "plan_file": "plans/detailed-dh-example.md"},
            )
            update_lifecycle(
                repo,
                "detailed-dh-example",
                "completed",
                "codex-execute-detailed",
                {"verification_status": "passed", "reflection_path": "reflections/run-1.md"},
            )

            manifest = read_manifest(repo)
            self.assertTrue(validate_manifest(repo / "plans/manifest.json").valid)
            self.assertEqual(len(manifest.plans), 1)
            entry = manifest.plans[0]
            self.assertEqual(entry.type, "detailed")
            self.assertEqual(entry.status, "completed")
            self.assertEqual(entry.owner_skill, "codex-plan-detailed")
            self.assertEqual(entry.acceptance_criteria_count, 1)
            self.assertEqual([event.transition for event in entry.lifecycle], ["committed", "executing", "completed"])
            self.assertEqual(entry.lifecycle[-1].metadata["verification_status"], "passed")
            self.assertEqual(entry.lifecycle[-1].metadata["reflection_path"], "reflections/run-1.md")

    def test_phase_plan_manifest_roundtrip_models_skill_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            roadmap = repo / "specs/phase-plans-v38.md"
            plan = repo / "plans/phase-plan-v38-PH.md"
            roadmap.parent.mkdir()
            plan.parent.mkdir()
            roadmap.write_text("# Roadmap\n\n### Phase 0 - Phase Hook (PH)\n", encoding="utf-8")
            plan.write_text("# PH\n\n## Interface Freeze Gates\n- [ ] IF-0-PH-1\n", encoding="utf-8")
            timestamp = "2026-05-30T00:00:00Z"
            append_entry(
                repo,
                DotfilesPlanEntry(
                    slug="v38-PH",
                    file="plans/phase-plan-v38-PH.md",
                    type="phase",
                    status="committed",
                    created_at=timestamp,
                    updated_at=timestamp,
                    owner_skill="codex-plan-phase",
                    handoff_ref=".dev-skills/handoffs/codex-plan-phase/run.md",
                    roadmap_ref=DotfilesPlanRef(
                        slug="phase-plans-v38",
                        file="specs/phase-plans-v38.md",
                        type="phase",
                        status="committed",
                    ),
                    phase_alias="PH",
                    if_gates_produced=("IF-0-PH-1",),
                    lanes=("SL-0", "SL-1"),
                    lifecycle=(
                        DotfilesPlanLifecycleEvent(
                            transition="committed",
                            by="codex-plan-phase",
                            at=timestamp,
                            metadata={"handoff_ref": ".dev-skills/handoffs/codex-plan-phase/run.md"},
                        ),
                    ),
                ),
            )

            update_lifecycle(
                repo,
                "v38-PH",
                "executing",
                "codex-execute-phase",
                {"run_id": "run-1", "phase_alias": "PH"},
            )
            update_lifecycle(
                repo,
                "v38-PH",
                "completed",
                "codex-execute-phase",
                {
                    "verification_status": "passed",
                    "reflection_ref": "reflections/run-1.md",
                    "produced_if_gates": ["IF-0-PH-1"],
                },
            )

            manifest = read_manifest(repo)
            self.assertTrue(validate_manifest(repo / "plans/manifest.json").valid)
            entry = manifest.plans[0]
            self.assertEqual(entry.type, "phase")
            self.assertEqual(entry.status, "completed")
            self.assertEqual(entry.phase_alias, "PH")
            self.assertEqual(entry.if_gates_produced, ("IF-0-PH-1",))
            self.assertEqual(entry.lanes, ("SL-0", "SL-1"))
            self.assertEqual([event.transition for event in entry.lifecycle], ["committed", "executing", "completed"])
            self.assertEqual(entry.lifecycle[-1].metadata["verification_status"], "passed")
            self.assertEqual(entry.lifecycle[-1].metadata["produced_if_gates"], ["IF-0-PH-1"])


if __name__ == "__main__":
    unittest.main()
