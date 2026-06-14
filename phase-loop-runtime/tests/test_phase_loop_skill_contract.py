import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


class PhaseLoopSkillContractTest(unittest.TestCase):
    def assert_file_contains(self, path: Path, tokens: tuple[str, ...]) -> None:
        text = path.read_text(encoding="utf-8")
        for token in tokens:
            self.assertIn(token, text, msg=f"{path} missing token: {token}")

    def assert_file_contains_normalized(self, path: Path, tokens: tuple[str, ...]) -> None:
        text = " ".join(path.read_text(encoding="utf-8").split())
        for token in tokens:
            self.assertIn(token, text, msg=f"{path} missing token: {token}")

    def test_runtime_state_docs_reference_shared_contract(self):
        runtime_docs = (
            ROOT / "codex-config" / "shared" / "runtime-state.md",
            ROOT / "claude-config" / "shared" / "runtime-state.md",
            ROOT / "gemini-config" / "shared" / "runtime-state.md",
            ROOT / "opencode-config" / "shared" / "runtime-state.md",
        )
        required = (
            "shared/phase-loop/protocol.md",
            "automation:",
            "verification_status",
            "dirty_worktree_conflict",
            "branch_sync_conflict",
            "repeated_verification_failure",
        )
        for path in runtime_docs:
            self.assert_file_contains(path, required)

    def test_operator_docs_keep_parity_claims_aligned(self):
        matrix_path = ROOT / "docs" / "phase-loop" / "harness-capability-matrix.md"
        closeout_path = ROOT / "docs" / "phase-loop" / "v4-maturity-closeout.md"

        self.assert_file_contains(
            matrix_path,
            (
                "proof-blocked",
                "promotion_status=proof_gated",
                "Missing `automation:` block",
                "Missing terminal summary",
                "Stale handoff",
                "repairable non-human",
            ),
        )
        self.assert_file_contains(
            closeout_path,
            (
                "proof-blocked",
                "promotion_status=proof_gated",
                "missing `automation:` block",
                "missing terminal summary",
                "stale handoff",
                "repairable non-human",
            ),
        )

    def test_harness_workflow_skills_reference_shared_contract_literals(self):
        harnesses = {
            "codex": {
                "roadmap": ROOT / "codex-config" / "skills" / "codex-phase-roadmap-builder" / "SKILL.md",
                "plan": ROOT / "codex-config" / "skills" / "codex-plan-phase" / "SKILL.md",
                "execute": ROOT / "codex-config" / "skills" / "codex-execute-phase" / "SKILL.md",
                "bridge": ROOT / "codex-config" / "skills" / "codex-phase-loop" / "SKILL.md",
            },
            "claude": {
                "roadmap": ROOT / "claude-config" / "claude-skills" / "claude-phase-roadmap-builder" / "SKILL.md",
                "plan": ROOT / "claude-config" / "claude-skills" / "claude-plan-phase" / "SKILL.md",
                "execute": ROOT / "claude-config" / "claude-skills" / "claude-execute-phase" / "SKILL.md",
                "bridge": ROOT / "claude-config" / "claude-skills" / "claude-phase-loop" / "SKILL.md",
            },
            "gemini": {
                "roadmap": ROOT / "gemini-config" / "skills" / "gemini-phase-roadmap-builder" / "SKILL.md",
                "plan": ROOT / "gemini-config" / "skills" / "gemini-plan-phase" / "SKILL.md",
                "execute": ROOT / "gemini-config" / "skills" / "gemini-execute-phase" / "SKILL.md",
                "bridge": ROOT / "gemini-config" / "skills" / "gemini-phase-loop" / "SKILL.md",
            },
            "opencode": {
                "roadmap": ROOT / "opencode-config" / "skills" / "opencode-phase-roadmap-builder" / "SKILL.md",
                "plan": ROOT / "opencode-config" / "skills" / "opencode-plan-phase" / "SKILL.md",
                "execute": ROOT / "opencode-config" / "skills" / "opencode-execute-phase" / "SKILL.md",
                "bridge": ROOT / "opencode-config" / "skills" / "opencode-phase-loop" / "SKILL.md",
            },
        }
        roadmap_tokens = (
            "shared/phase-loop/protocol.md",
            "automation:",
            "next_skill",
            "next_command",
            "verification_status",
        )
        plan_tokens = (
            "shared/phase-loop/protocol.md",
            "phase_loop_plan_version: 1",
            "roadmap_sha256",
            "## Dispatch Hints",
        )
        execute_tokens = (
            "automation:",
            "verification_status",
            "dirty_worktree_conflict",
            "manual event",
            "roadmap amendment",
        )
        bridge_tokens = (
            ".phase-loop/state.json",
            "status --json",
            "monitor --once --json",
            "injected-skill metadata",
            "installed-skill drift",
            "sync-skills --apply",
        )

        for harness in harnesses.values():
            self.assert_file_contains(harness["roadmap"], roadmap_tokens)
            self.assert_file_contains(harness["plan"], plan_tokens)
            self.assert_file_contains(harness["execute"], execute_tokens)
            self.assert_file_contains(harness["bridge"], bridge_tokens)

    def test_codex_plan_phase_names_planbundle_frontmatter_rules(self):
        path = ROOT / "codex-config" / "skills" / "codex-plan-phase" / "SKILL.md"
        self.assert_file_contains(
            path,
            (
                "Pipeline-aware metadata is additive",
                "validated bundle context or explicit pipeline-required run context",
                "PLANBUNDLE-frontmatter-guidance",
                "phase-source-bundle.v1",
                "`source_bundle` with the bundle path",
                "`source_bundle_sha256` with the computed bundle file hash",
                "`pipeline_phase_id`",
                "`pipeline_mode`",
                "standalone phase-loop plans",
                "PLANBUNDLE-stale-input-blocker-guidance",
                "Protected-source entries",
                "delegated write policy",
                "exact path or glob",
                "`.pipeline/**`",
                "governed-pipeline specs",
                "Portal contracts",
                "Greenfield authority files",
                "legacy `.codex/phase-loop/` state",
                "human_required=false",
                "blocker_class=contract_bug",
            ),
        )

    def test_codex_execute_phase_names_execfresh_contract(self):
        path = ROOT / "codex-config" / "skills" / "codex-execute-phase" / "SKILL.md"
        self.assert_file_contains(
            path,
            (
                "source_bundle_sha256",
                "freshness.source_bundle_hash",
                "phase-source-bundle.v1",
                "pipeline_execution_plan_diagnostic",
                "pipeline_write_boundary_diagnostic",
                "protected Pipeline",
                "protected-source hashes",
                "pipeline.definition.json",
                "governed-pipeline specs",
                "Portal contracts",
                "Greenfield authority files",
                "private evidence",
                "raw data",
                "credentials",
                "provider payloads",
                "legacy `.codex/phase-loop/` state",
                "standalone execution behavior",
                "missing, stale, malformed, or mismatched source bundles",
                "Execution Policy",
                "Dispatch Hints",
                "work-unit=phase_reducer",
                "work-unit=phase_verify",
                "policy precedence",
                "human_required=false",
                "blocker_class=contract_bug",
            ),
        )

    def test_codex_phase_loop_names_pipeline_closeout_export_contract(self):
        path = ROOT / "codex-config" / "skills" / "codex-phase-loop" / "SKILL.md"
        self.assert_file_contains(
            path,
            (
                "phase_loop_closeout.v1",
                "Pipeline closeout export",
                "one shared\n`automation:`",
                ".phase-loop/",
                "redacted access attempts",
                "Governed Pipeline ingest outside dotfiles",
                "Portal lifecycle state",
                "Greenfield reduction",
                "metadata-only authority refs",
                "not dotfiles write targets",
                "Legacy `.codex/phase-loop/` compatibility-only",
            ),
        )

    def test_codex_phase_skills_preserve_skill_prompt_guardrails(self):
        plan_skill = ROOT / "codex-config" / "skills" / "codex-plan-phase" / "SKILL.md"
        execute_skill = ROOT / "codex-config" / "skills" / "codex-execute-phase" / "SKILL.md"
        bridge_skill = ROOT / "codex-config" / "skills" / "codex-phase-loop" / "SKILL.md"

        shared_tokens = (
            "standalone",
            "pipeline_required",
            "source bundle",
            "protected-source",
            "active plan and source bundle explicitly own the exact path or glob",
            "`.pipeline/**`",
            "governed-pipeline specs",
            "Portal contracts",
            "Greenfield authority files",
            "raw evidence",
            "provider payloads",
            "credentials",
            "legacy `.codex/phase-loop/` state",
        )
        for path in (plan_skill, execute_skill, bridge_skill):
            self.assert_file_contains_normalized(path, shared_tokens)

    def test_shared_runner_skills_keep_portal_greenfield_mediated(self):
        runner_skills = (
            ROOT / "shared" / "skills" / "code-cli-runner" / "SKILL.md",
            ROOT / "shared" / "skills" / "codex-cli-runner" / "SKILL.md",
            ROOT / "shared" / "skills" / "gemini-cli-runner" / "SKILL.md",
        )
        required = (
            "phase-loop mediated boundary",
            "governed-pipeline closeout ingest",
            "Portal projection",
            "Greenfield metadata-only authority refs",
            "not direct dotfiles write targets",
            "active plan and source bundle explicitly own the exact path or glob",
            "`.pipeline/**`",
            "governed-pipeline specs",
            "Portal contracts",
            "Greenfield authority files",
            "raw evidence",
            "provider payloads",
            "credentials",
        )
        for path in runner_skills:
            self.assert_file_contains_normalized(path, required)

    def test_codex_policy_selector_vocabulary_is_preserved(self):
        policy_paths = (
            ROOT / "codex-config" / "skills" / "codex-plan-phase" / "SKILL.md",
            ROOT / "codex-config" / "skills" / "codex-execute-phase" / "SKILL.md",
            ROOT / "vendor" / "phase-loop-runtime" / "src" / "phase_loop_runtime" / "prompts.py",
            ROOT / "docs" / "phase-loop" / "granular-execution-policy.md",
        )
        required = (
            "work-unit defaults",
            "roadmap",
            "plan",
            "execute",
            "repair",
            "review",
            "maintain-skills",
            "SL-2",
            "work-unit=phase_reducer",
            "work-unit=phase_verify",
            "Dispatch Hints",
            "CLI/operator override",
            "phase-plan policy",
            "roadmap policy",
            "registry defaults",
            "silent downgrade",
        )
        for path in policy_paths:
            self.assert_file_contains(path, required)

    def test_executor_skills_name_lane_work_unit_contract(self):
        execute_skills = (
            ROOT / "codex-config" / "skills" / "codex-execute-phase" / "SKILL.md",
            ROOT / "claude-config" / "claude-skills" / "claude-execute-phase" / "SKILL.md",
            ROOT / "gemini-config" / "skills" / "gemini-execute-phase" / "SKILL.md",
            ROOT / "opencode-config" / "skills" / "opencode-execute-phase" / "SKILL.md",
        )
        required = (
            "HarnessLaneAssignment",
            "selected `lane_id`",
            "`owned_files`",
            "`consumed_interfaces`",
            "one shared\n`automation:` closeout",
            "Installed-skill drift is\nwarning-only",
            "closeout prompts are distinct from implementation",
            "non-human blocker",
        )
        for path in execute_skills:
            self.assert_file_contains(path, required)

    def test_claude_plan_validator_rejects_noncanonical_roadmap_frontmatter(self):
        script = ROOT / "claude-config" / "claude-skills" / "claude-plan-phase" / "scripts" / "validate_plan_doc.py"
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            roadmap = repo / "planning" / "phase-artifacts" / "notes-loop-v1" / "phase-roadmap.md"
            roadmap.parent.mkdir(parents=True)
            roadmap.write_text("### Phase 1: Runner (RUNNER)\n", encoding="utf-8")
            (repo / "plans").mkdir()
            plan = repo / "plans" / "phase-plan-v1-RUNNER.md"
            plan.write_text(
                self._sample_plan_doc(
                    roadmap_path="notes-loop-v1/phase-roadmap.md",
                    roadmap_sha=hashlib.sha256(roadmap.read_bytes()).hexdigest(),
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                ["python3", str(script), str(plan)],
                cwd=repo,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("roadmap", result.stderr)

    def test_claude_plan_validator_accepts_canonical_roadmap_frontmatter(self):
        script = ROOT / "claude-config" / "claude-skills" / "claude-plan-phase" / "scripts" / "validate_plan_doc.py"
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            roadmap = repo / "planning" / "phase-artifacts" / "notes-loop-v1" / "phase-roadmap.md"
            roadmap.parent.mkdir(parents=True)
            roadmap.write_text("### Phase 1: Runner (RUNNER)\n", encoding="utf-8")
            (repo / "README.md").write_text("sample\n", encoding="utf-8")
            (repo / "plans").mkdir()
            plan = repo / "plans" / "phase-plan-v1-RUNNER.md"
            plan.write_text(
                self._sample_plan_doc(
                    roadmap_path="planning/phase-artifacts/notes-loop-v1/phase-roadmap.md",
                    roadmap_sha=hashlib.sha256(roadmap.read_bytes()).hexdigest(),
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                ["python3", str(script), str(plan)],
                cwd=repo,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_claude_roadmap_validator_rejects_phase_heading_without_alias(self):
        script = ROOT / "claude-config" / "claude-skills" / "claude-phase-roadmap-builder" / "scripts" / "validate_roadmap.py"
        with tempfile.TemporaryDirectory() as td:
            roadmap = Path(td) / "phase-roadmap.md"
            roadmap.write_text(
                "# Roadmap\n\n"
                "## Context\ncontext\n\n"
                "## Top Interface-Freeze Gates\n\n"
                "## Phases\n\n"
                "### Phase 1 - Foundation\n\n"
                "**Objective**\nBuild the foundation.\n\n"
                "**Exit criteria**\n- [ ] It works.\n\n"
                "**Scope notes**\n- preamble / interface-only phase.\n\n"
                "**Key files**\n- README.md\n\n"
                "**Depends on**\n- (none)\n\n"
                "**Produces**\n- (none)\n\n"
                "## Phase Dependency DAG\nP1\n\n"
                "## Execution Notes\nPlan P1.\n\n"
                "## Verification\npytest\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                ["python3", str(script), str(roadmap)],
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid phase heading", result.stderr)
            self.assertIn("(<ALIAS>)", result.stderr)

    def test_claude_roadmap_validator_parses_mnemonic_depends_on_aliases(self):
        script = ROOT / "claude-config" / "claude-skills" / "claude-phase-roadmap-builder" / "scripts" / "validate_roadmap.py"
        with tempfile.TemporaryDirectory() as td:
            roadmap = Path(td) / "phase-roadmap.md"
            roadmap.write_text(
                "# Roadmap\n\n"
                "## Context\ncontext\n\n"
                "## Top Interface-Freeze Gates\n\n"
                "## Phases\n\n"
                "### Phase 1 - Base (BASE)\n\n"
                "**Objective**\nBuild the base.\n\n"
                "**Exit criteria**\n- [ ] It works.\n\n"
                "**Scope notes**\n- preamble / interface-only phase.\n\n"
                "**Key files**\n- README.md\n\n"
                "**Depends on**\n- FUTURE\n\n"
                "**Produces**\n- (none)\n\n"
                "### Phase 2 - Follow (FOLLOW)\n\n"
                "**Objective**\nFollow the base.\n\n"
                "**Exit criteria**\n- [ ] It works.\n\n"
                "**Scope notes**\n- preamble / interface-only phase.\n\n"
                "**Key files**\n- README.md\n\n"
                "**Depends on**\n- BASE\n\n"
                "**Produces**\n- (none)\n\n"
                "## Phase Dependency DAG\nBASE -> FOLLOW\n\n"
                "## Execution Notes\nPlan BASE, then FOLLOW.\n\n"
                "## Verification\npytest\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                ["python3", str(script), str(roadmap)],
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unknown alias 'FUTURE'", result.stderr)

    def test_claude_roadmap_validator_accepts_decimal_and_branch_alias_headings(self):
        script = ROOT / "claude-config" / "claude-skills" / "claude-phase-roadmap-builder" / "scripts" / "validate_roadmap.py"
        with tempfile.TemporaryDirectory() as td:
            roadmap = Path(td) / "phase-roadmap.md"
            roadmap.write_text(
                "# Roadmap\n\n"
                "## Context\ncontext\n\n"
                "## Top Interface-Freeze Gates\n\n"
                "## Phases\n\n"
                "### Phase 4.0 - Read-In (P40)\n\n"
                "**Objective**\nRead in.\n\n"
                "**Exit criteria**\n- [ ] It works.\n\n"
                "**Scope notes**\n- preamble / interface-only phase.\n\n"
                "**Key files**\n- README.md\n\n"
                "**Depends on**\n- (none)\n\n"
                "**Produces**\n- (none)\n\n"
                "### Phase 4A - Build (P4A)\n\n"
                "**Objective**\nBuild.\n\n"
                "**Exit criteria**\n- [ ] It works.\n\n"
                "**Scope notes**\n- preamble / interface-only phase.\n\n"
                "**Key files**\n- README.md\n\n"
                "**Depends on**\n- P40\n\n"
                "**Produces**\n- (none)\n\n"
                "## Phase Dependency DAG\nP40 -> P4A\n\n"
                "## Execution Notes\nPlan P40, then P4A.\n\n"
                "## Verification\npytest\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                ["python3", str(script), str(roadmap)],
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_claude_roadmap_validator_accepts_word_lane_counts_and_lanes_section(self):
        script = ROOT / "claude-config" / "claude-skills" / "claude-phase-roadmap-builder" / "scripts" / "validate_roadmap.py"
        with tempfile.TemporaryDirectory() as td:
            roadmap = Path(td) / "phase-roadmap.md"
            roadmap.write_text(
                "# Roadmap\n\n"
                "## Context\ncontext\n\n"
                "## Top Interface-Freeze Gates\n- IF-0-A-1 — gate.\n\n"
                "## Phases\n\n"
                "### Phase 1 — Base (A)\n\n"
                "**Objective**\nBase.\n\n"
                "**Exit criteria**\n- [ ] ok.\n\n"
                "**Scope notes**\nFour lanes covering disjoint files.\n\n"
                "**Key files**\n- a.py\n\n"
                "**Depends on**\n- (none)\n\n"
                "**Produces**\n- IF-0-A-1\n\n"
                "### Phase 2 — Build (B)\n\n"
                "**Objective**\nBuild.\n\n"
                "**Exit criteria**\n- [ ] ok.\n\n"
                "**Lanes** (parallel)\n- B-lane-one: x.\n- B-lane-two: y.\n\n"
                "**Scope notes**\nSee lanes above.\n\n"
                "**Key files**\n- b.py\n\n"
                "**Depends on**\n- A\n\n"
                "**Produces**\n- (none)\n\n"
                "## Phase Dependency DAG\nA -> B\n\n"
                "## Execution Notes\nPlan A then B.\n\n"
                "## Verification\npytest\n",
                encoding="utf-8",
            )
            result = subprocess.run(["python3", str(script), str(roadmap)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("(G)", result.stderr)

    def test_claude_roadmap_validator_flags_single_lane_non_preamble_phase(self):
        script = ROOT / "claude-config" / "claude-skills" / "claude-phase-roadmap-builder" / "scripts" / "validate_roadmap.py"
        with tempfile.TemporaryDirectory() as td:
            roadmap = Path(td) / "phase-roadmap.md"
            roadmap.write_text(
                "# Roadmap\n\n"
                "## Context\ncontext\n\n"
                "## Top Interface-Freeze Gates\n- IF-0-A-1 — gate.\n\n"
                "## Phases\n\n"
                "### Phase 1 — Solo (A)\n\n"
                "**Objective**\nOne thing.\n\n"
                "**Exit criteria**\n- [ ] ok.\n\n"
                "**Scope notes**\nJust does one thing; no parallelism.\n\n"
                "**Key files**\n- a.py\n\n"
                "**Depends on**\n- (none)\n\n"
                "**Produces**\n- IF-0-A-1\n\n"
                "## Phase Dependency DAG\nA\n\n"
                "## Execution Notes\nPlan A.\n\n"
                "## Verification\npytest\n",
                encoding="utf-8",
            )
            result = subprocess.run(["python3", str(script), str(roadmap)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("(G)", result.stderr)

    def test_claude_plan_validator_rejects_prose_owned_files(self):
        script = ROOT / "claude-config" / "claude-skills" / "claude-plan-phase" / "scripts" / "validate_plan_doc.py"
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.parent.mkdir()
            roadmap.write_text("### Phase 1: Runner (RUNNER)\n", encoding="utf-8")
            (repo / "README.md").write_text("sample\n", encoding="utf-8")
            (repo / "plans").mkdir()
            plan = repo / "plans" / "phase-plan-v1-RUNNER.md"
            plan.write_text(
                self._sample_plan_doc(
                    roadmap_path="specs/phase-plans-v1.md",
                    roadmap_sha=hashlib.sha256(roadmap.read_bytes()).hexdigest(),
                ).replace("`README.md`", "`README.md`, callsite wiring inside auth hook", 1),
                encoding="utf-8",
            )

            result = subprocess.run(
                ["python3", str(script), str(plan)],
                cwd=repo,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Owned files", result.stderr)

    def _sample_plan_doc(self, *, roadmap_path: str, roadmap_sha: str) -> str:
        return (
            "---\n"
            "phase_loop_plan_version: 1\n"
            "phase: RUNNER\n"
            f"roadmap: {roadmap_path}\n"
            f"roadmap_sha256: {roadmap_sha}\n"
            "---\n\n"
            "# RUNNER: Example\n\n"
            "## Context\nExample context.\n\n"
            "## Interface Freeze Gates\n- [ ] IF-0-RUNNER-1 — `RunnerContract`\n\n"
            "## Lane Index & Dependencies\n\n"
            "SL-1 — Core\n"
            "  Depends on: (none)\n"
            "  Blocks: (none)\n"
            "  Parallel-safe: yes\n\n"
            "## Lanes\n\n"
            "### SL-1 — Core\n"
            "- **Scope**: Implement the smallest valid contract.\n"
            "- **Owned files**: `README.md`\n"
            "- **Interfaces provided**: `RunnerContract`\n"
            "- **Interfaces consumed**: (none)\n\n"
            "| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |\n"
            "|---|---|---|---|---|---|\n"
            "| SL-1.1 | test | — | `tests/test_runner.py` | `RunnerContract` | `pytest tests/test_runner.py` |\n"
            "| SL-1.2 | impl | SL-1.1 | `README.md` | — | — |\n"
            "| SL-1.3 | verify | SL-1.2 | `README.md` | all | `pytest tests/test_runner.py` |\n\n"
            "## Execution Notes\n- Known destructive changes: none.\n\n"
            "## Acceptance Criteria\n- [ ] `pytest tests/test_runner.py` passes.\n\n"
            "## Verification\n`pytest tests/test_runner.py`\n"
        )


if __name__ == "__main__":
    unittest.main()
