import tempfile
import unittest
from pathlib import Path

from phase_loop_test_utils import ROOT
from phase_loop_runtime.plan_manifest import (
    DotfilesPlanEntry,
    DotfilesPlanLifecycleEvent,
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


class SkillPlanManifestWriteTest(unittest.TestCase):
    def test_all_canonical_detailed_skills_have_stable_marker(self):
        for skill_name, path in (*PLAN_SKILLS, *EXECUTE_SKILLS):
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


if __name__ == "__main__":
    unittest.main()
