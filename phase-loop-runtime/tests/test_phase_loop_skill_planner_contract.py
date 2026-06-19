import unittest
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


class PhaseLoopSkillPlannerContractTest(unittest.TestCase):
    def assert_tokens(self, path: Path, tokens: tuple[str, ...]) -> None:
        text = " ".join(path.read_text(encoding="utf-8").split())
        for token in tokens:
            self.assertIn(token, text, msg=f"{path} missing token: {token}")

    def test_plan_phase_skills_require_machine_checkable_verification(self):
        tokens = (
            "IF-0-VC-2",
            "machine-checkable verification commands",
            "effective `automation.suite_command`",
            "operational evidence artifact",
            "runner-stamped amendment mechanism",
        )
        for path in (
            ROOT / "claude-config" / "claude-skills" / "claude-plan-phase" / "SKILL.md",
            ROOT / "codex-config" / "skills" / "codex-plan-phase" / "SKILL.md",
            ROOT / "gemini-config" / "skills" / "gemini-plan-phase" / "SKILL.md",
            ROOT / "opencode-config" / "skills" / "opencode-plan-phase" / "SKILL.md",
        ):
            with self.subTest(path=path):
                self.assert_tokens(path, tokens)

    def test_plan_detailed_skills_require_verification_or_stamped_evidence(self):
        tokens = (
            "machine-checkable verification commands",
            "effective `automation.suite_command`",
            "operational evidence artifact",
            "runner-stamped amendment mechanism",
        )
        for path in (
            ROOT / "claude-config" / "claude-skills" / "claude-plan-detailed" / "SKILL.md",
            ROOT / "codex-config" / "skills" / "codex-plan-detailed" / "SKILL.md",
            ROOT / "gemini-config" / "skills" / "gemini-plan-detailed" / "SKILL.md",
            ROOT / "opencode-config" / "skills" / "opencode-plan-detailed" / "SKILL.md",
        ):
            with self.subTest(path=path):
                self.assert_tokens(path, tokens)

    def test_roadmap_builder_skills_require_suite_expectation(self):
        tokens = (
            "machine-checkable verification commands",
            "effective `automation.suite_command`",
            "operational evidence artifact",
            "runner-stamped amendment mechanism",
            "proxy evidence requires a roadmap amendment",
        )
        for path in (
            ROOT / "claude-config" / "claude-skills" / "claude-phase-roadmap-builder" / "SKILL.md",
            ROOT / "codex-config" / "skills" / "codex-phase-roadmap-builder" / "SKILL.md",
            ROOT / "gemini-config" / "skills" / "gemini-phase-roadmap-builder" / "SKILL.md",
            ROOT / "opencode-config" / "skills" / "opencode-phase-roadmap-builder" / "SKILL.md",
        ):
            with self.subTest(path=path):
                self.assert_tokens(path, tokens)

    def test_roadmap_builder_skills_require_spec_delta_policy(self):
        tokens = (
            "spec_delta_closeout.v1",
            "Spec Delta Policy",
            "target surfaces",
            "evidence paths",
            "metadata_only",
            "dotfiles_skill_source_update",
        )
        for path in (
            ROOT / "claude-config" / "claude-skills" / "claude-phase-roadmap-builder" / "SKILL.md",
            ROOT / "codex-config" / "skills" / "codex-phase-roadmap-builder" / "SKILL.md",
            ROOT / "gemini-config" / "skills" / "gemini-phase-roadmap-builder" / "SKILL.md",
            ROOT / "opencode-config" / "skills" / "opencode-phase-roadmap-builder" / "SKILL.md",
        ):
            with self.subTest(path=path):
                self.assert_tokens(path, tokens)

    def test_plan_phase_skills_require_spec_closeout_plan(self):
        tokens = (
            "Spec Closeout Plan",
            "spec_delta_closeout.v1",
            "target surfaces",
            "evidence paths",
            "redaction posture",
            "metadata_only",
            "work-unit=phase_reducer",
            "work-unit=phase_verify",
        )
        for path in (
            ROOT / "claude-config" / "claude-skills" / "claude-plan-phase" / "SKILL.md",
            ROOT / "codex-config" / "skills" / "codex-plan-phase" / "SKILL.md",
            ROOT / "gemini-config" / "skills" / "gemini-plan-phase" / "SKILL.md",
            ROOT / "opencode-config" / "skills" / "opencode-plan-phase" / "SKILL.md",
        ):
            with self.subTest(path=path):
                self.assert_tokens(path, tokens)

    def test_plan_validator_rejects_missing_spec_closeout_plan(self):
        script = ROOT / "vendor" / "phase-loop-skills" / "plan-phase" / "scripts" / "validate_plan_doc.py"
        plan = """---
phase_loop_plan_version: 1
phase: SAMPLE
roadmap: specs/phase-plans-v1.md
roadmap_sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
---

# SAMPLE: sample

## Context

## Interface Freeze Gates
- [ ] IF-0-SAMPLE-1 - sample

## Lane Index & Dependencies
SL-0 — sample
Depends on: (none)
Blocks: (none)
Parallel-safe: no

## Lanes

### SL-0 — sample
- **Scope**: sample
- **Owned files**: `README.md`
- **Interfaces provided**: `IF-0-SAMPLE-1`
- **Interfaces consumed**: none
- **Tasks**:
  - test: sample
  - impl: sample

## Execution Notes

## Acceptance Criteria
- [ ] sample

## Verification
pytest
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "phase-plan.md"
            path.write_text(plan, encoding="utf-8")
            result = subprocess.run(
                ["python3", str(script), str(path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Spec Closeout Plan", result.stderr)


if __name__ == "__main__":
    unittest.main()
