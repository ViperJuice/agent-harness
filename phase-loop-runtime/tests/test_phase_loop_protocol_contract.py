import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.models import (
    BLOCKER_CLASSES,
    BLOCKER_POSTURES,
    CANONICAL_REFRESH_REASON_CODES,
    CHANGED_PATH_CATEGORIES,
    CLOSEOUT_MODES,
    FAILURE_KINDS,
    OPERATOR_MATURITY_LABELS,
    PHASE_STATUSES,
    EVENT_STATUSES,
    PIPELINE_MODE_LITERALS,
    PIPELINE_PROTECTED_SOURCE_CATEGORIES,
    PIPELINE_PROTECTED_SOURCE_ROLES,
    PROMOTION_STATUSES,
    REDACTION_POSTURES,
    TERMINAL_SUMMARY_FIELDS,
    WORK_UNIT_STATUSES,
)
from phase_loop_runtime.observability import NOTIFICATION_PAYLOAD_FIELDS, build_notification_payload, build_terminal_summary


class PhaseLoopProtocolContractTest(unittest.TestCase):
    def test_protocol_documents_baml_closeout_schema_and_strict_transition(self):
        text = (ROOT / "vendor/phase-loop-runtime/protocol/protocol.md").read_text(encoding="utf-8")
        self.assertIn("## BAML Closeout Schema", text)
        self.assertIn("### Strict Mode Transition", text)
        self.assertIn("EmitPhaseCloseout", text)
        self.assertIn("parse_baml_response", text)

    def test_lane_ir_contract_is_documented(self):
        text = (ROOT / "vendor" / "phase-loop-runtime" / "protocol" / "protocol.md").read_text(encoding="utf-8")
        for expected in (
            "PhasePlanIR",
            "PhasePlanLane",
            "LaneDependency",
            "LaneTaskSet",
            "LaneIRDiagnostic",
            "overlapping_write_ownership",
            "missing_producer_dependency",
            "unsupported_lane_policy",
            "repairable non-human blocker",
            "phase_loop_plan_version: 1",
            "WorkUnitIdentity",
            "WorkUnitState",
            "WorkUnitCloseout",
            "WorkUnitEventMetadata",
            "LaneWave",
            "LaneWaveDecision",
            "LaneWorktreeAssignment",
            "DirtyPathClassification",
            "HarnessLaneAssignment",
            "lane id",
            "wave id",
            "worktree path",
            "base SHA",
            "isolation mode",
            "owned files",
            "read-only refs",
            "harness route",
            "fallback reason",
            "redacted evidence refs",
            "Greenfield authority refs",
            "governed-pipeline assignments",
            "HarnessWorkUnitPromptKind",
            "harness_lane_assignment.v1",
            "build_lane_prompt_bundle",
            "LaunchSpec.harness_lane_assignment",
            "docs/phase-loop/dffakesmoke-substrate-receipt.md",
            "docs/phase-loop/dfpromptsync-contract-map.md",
            "docs/phase-loop/dfpromptsync-readiness.md",
            "vendor/phase-loop-runtime/tests/fixtures/phase_loop_fake_smoke/matrix.json",
            "vendor/phase-loop-runtime/tests/fixtures/phase_loop_prompt_sync/matrix.json",
            "docs/phase-loop/dfparsoak-source-map.md",
            "docs/phase-loop/dfparsoak-receipt.md",
            "docs/phase-loop/dfparsoak-runbook.md",
            "vendor/phase-loop-runtime/tests/fixtures/phase_loop_dfparsoak/matrix.json",
            "policy_source",
        ):
            self.assertIn(expected, text)

    def setUp(self) -> None:
        self.protocol_path = ROOT / "vendor" / "phase-loop-runtime" / "protocol" / "protocol.md"
        self.protocol_text = self.protocol_path.read_text(encoding="utf-8")

    def assertTokenInText(self, token, text: str, *, msg: str | None = None) -> None:
        if isinstance(token, tuple):
            self.assertTrue(any(option in text for option in token), msg=msg or f"missing token: {token}")
            return
        self.assertIn(token, text, msg=msg)

    def test_protocol_headings_are_present(self):
        headings = (
            "## Plan Frontmatter",
            "## Automation Handoffs",
            "## Event Ledger Records",
            "## Live Adapter Contract",
            "## Launch Artifacts",
            "## Terminal Summary",
            "## Monitor Payloads",
            "## Human-Required Blockers",
            "## Lane IR Plan Parser Contract",
            "## Lane Scheduler",
            "## Harness Lane Workflows",
            "## Closeout Modes",
            "## Roadmap Amendments",
            "## Delegation Broker",
            "## Manual Advancement and Import",
            "## Runtime Path",
        )
        for heading in headings:
            self.assertIn(heading, self.protocol_text)

    def test_protocol_documents_tier2_evidence_audit_contract(self):
        for token in (
            "--tier-2",
            "loose-uniform",
            "boilerplate-text",
            "size-distribution",
            "tier2:",
            "tier2_findings",
            "default invocation\nremains Tier 1 only",
            "Runner closeout integration may also run Tier 2",
        ):
            self.assertTokenInText(token, self.protocol_text)

    def test_protocol_documents_tier3_runner_integration_contract(self):
        for token in (
            "--tier-3-budget",
            "tier3_budget",
            "tier3_calls_made",
            "UNCERTAIN-OPERATOR-REVIEW",
            "evidence_audit_tier3",
            "prompt_sha256",
            "response_sha256",
            "token_counts",
            "estimated_cost_usd",
            ".phase-loop/evidence-audit.yaml",
            "phase_aliases_exclude_tier3",
            "T2DETECTORS",
            "T3SCHEMA",
            "T3RUNNER",
            "T3VALIDATE",
            "metadata.tier3_judgment",
        ):
            self.assertTokenInText(token, self.protocol_text)

    def test_protocol_documents_tier3_rollout_enablement_path(self):
        for token in (
            "Enabling Tier 3",
            "python3 tests/calibrate_tier3.py --dry-run",
            "confidence threshold",
            "phase-loop status --tier-3-history",
            "rollback",
            "false positives",
            "cost",
            "phase_aliases_exclude_tier3",
        ):
            self.assertTokenInText(token, self.protocol_text)

    def test_protocol_documents_closeout_evidence_audit(self):
        for token in (
            "Closeout Evidence Audit",
            "closeout_evidence_audit",
            "closeout_evidence_drift",
            "closeout claims have no matching files",
            "closeout diff",
            "raw commit bodies",
            "raw diff bodies",
        ):
            self.assertIn(token, self.protocol_text)

    def test_protocol_documents_hotfix_lane_contract(self):
        text = self.protocol_text
        runtime_doc = (ROOT / "docs" / "runtime" / "verification-evidence-contract.md").read_text(encoding="utf-8")
        for token in (
            "phase-loop hotfix",
            "work_unit: hotfix",
            "--init-stub",
            "verification_artifact_path",
            "verification_log_path",
            "single bounded change",
        ):
            self.assertTokenInText(token, text)
            self.assertIn(token, runtime_doc)

    def test_protocol_includes_frozen_literals(self):
        for literal in PHASE_STATUSES:
            self.assertIn(f"`{literal}`", self.protocol_text)
        for literal in EVENT_STATUSES:
            self.assertIn(f"`{literal}`", self.protocol_text)
        for literal in BLOCKER_CLASSES:
            self.assertIn(f"`{literal}`", self.protocol_text)
        for literal in WORK_UNIT_STATUSES:
            self.assertIn(f"`{literal}`", self.protocol_text)
        for literal in CLOSEOUT_MODES:
            self.assertIn(f"`{literal}`", self.protocol_text)
        for literal in PROMOTION_STATUSES:
            self.assertIn(f"`{literal}`", self.protocol_text)
        for literal in FAILURE_KINDS:
            self.assertIn(f"`{literal}`", self.protocol_text)
        for literal in BLOCKER_POSTURES:
            self.assertIn(f"`{literal}`", self.protocol_text)
        for literal in ("live-supported", "proof-blocked", "experimental", "manual-only"):
            self.assertIn(f"`{literal}`", self.protocol_text)

    def test_protocol_documents_plan_doc_current_heuristic(self):
        for token in (
            "Plan-Doc-Current Heuristic",
            "plan_skipped",
            "--force-replan",
            "EVENT_STATUSES",
            "PHASE_STATUSES",
            "is_plan_doc_current",
            "metadata.plan_doc_skip",
        ):
            self.assertIn(token, self.protocol_text)

    def test_protocol_documents_run_loop_mode(self):
        for token in (
            "Run Loop Mode",
            "--max-phases",
            "dispatched actions by default",
            "--full-phase",
            "complete phase cycles",
            "--no-deprecation-hints",
            "Existing operators",
        ):
            self.assertIn(token, self.protocol_text)

    def test_protocol_documents_start_gate(self):
        for token in (
            "## Start Gate",
            "start_gate_refused",
            "--allow-cross-phase-dirty",
            "phase_owned_dirty_paths",
            "previous_phase_owned_paths",
            "manual_recovery",
            "STARTGATE",
            "last 50",
        ):
            self.assertIn(token, self.protocol_text)

    def test_protocol_mentions_plan_frontmatter_and_manual_repair_rules(self):
        for token in (
            "phase_loop_plan_version: 1",
            "roadmap_sha256",
            "phase_loop_mutation",
            "release_base_ref",
            "source_bundle",
            "source_bundle_sha256",
            "pipeline_phase_id",
            "pipeline_mode",
            "Pipeline-aware metadata is additive",
            "Pipeline metadata is required only when `pipeline_mode` is `pipeline_required`",
            "LaunchRequest",
            "LaunchSpec",
            "LaunchResult",
            "ExecutorCapabilityRecord",
            "launch.json",
            "terminal-summary.json",
            "request_id",
            "target_executor",
            "budget",
            "manual_repair",
            "manual_recovery",
            "clears_blocker=true",
            "--to-status planned",
            "blocked-state recovery",
            "verification_status=not_run",
            ".phase-loop/",
            "context.md",
            "context_path",
            "context_sha256",
            "command_template",
            "wrapped_cwd",
            "expected_skill_pack",
            "skill_bundle_sha256",
            "fallback_mode",
            "dispatch_decision",
            "delegation_decision",
            "parent_child",
            "claude_execution_mode",
            "claude_team_policy",
            "phase_team_eligibility",
            "solo",
            "subagent",
            "agent_team",
            "selected_via",
            "considered_executors",
            "parent_executor",
            "child_artifact_root",
            "child_worktree_root",
            "child_closeout_result",
            "prompt_only",
            "inline",
            "stdin",
            "context_file",
            "manual",
            "adapter_failure",
            "phase_failure",
            "human_required",
            "repairable_non_human",
            "proof_gated",
            "live-supported",
            "proof-blocked",
            "branch_sync_conflict",
            "product_action",
            "reason",
            "owned_files",
            "expected_output",
            "priority",
            "review_context",
            "repair_context",
            "Native Claude team tasks stay internal",
            "Lane scheduler modes are `off`, `serialized`, and `concurrent`",
            "work_unit_metric.v1.wave_id",
            "launch.json.harness_lane_assignment",
            "phase-loop-execute-medium",
            "phase-loop-review-high",
            "Simple bounded scheduler-assigned lane",
            "defaults to `executor=pi`",
            "Claude or Anthropic model lanes default to\nClaude Code CLI",
            "Codex and Gemini fallback routes are\nCLI-based",
            "Manual handoff: non-default",
        ):
            self.assertIn(token, self.protocol_text)

    def test_pipeline_plan_frontmatter_contract_is_documented(self):
        for literal in PIPELINE_MODE_LITERALS:
            self.assertIn(f"`{literal}`", self.protocol_text)
        for category in PIPELINE_PROTECTED_SOURCE_CATEGORIES:
            self.assertIn(f"`{category}`", self.protocol_text)
        for role in PIPELINE_PROTECTED_SOURCE_ROLES:
            self.assertIn(f"`{role}`", self.protocol_text)
        for token in (
            "PIPELINE_PLAN_FRONTMATTER_CONTRACT",
            "PIPELINE_PROTECTED_SOURCE_CATEGORIES",
            "PIPELINE_PROTECTED_SOURCE_ROLES",
            "Pipeline specs",
            "Pipeline diagrams",
            "Pipeline adapter config",
            "Pipeline definition files",
            "Portal contracts",
            "Pipeline phase artifacts",
            "canonical_sources.adoption_mode",
            "canonical_sources.active_canonical_root",
            "canonical_sources.mirror_root",
            "canonical_sources.archive_manifest_path",
            "canonical_sources.spec_intake_roots",
            "canonical_sources.legacy_seed_roots",
            "`track_existing`",
            "`greenfield_single_spec`",
            "`greenfield_spec_bundle`",
            "`brownfield_existing_specs`",
            "Standalone `phase_loop_plan_version: 1` plans remain valid without Pipeline or\ncanonical adoption metadata",
            "repairable non-human\n`contract_bug` diagnostic",
            "root `specs/**` is the default human-visible future-spec\ndiscovery root",
            "Legacy or project-specific seed roots such as `Specs/**` are\nexplicit input roots only",
            "Governed-pipeline also owns canonical spec adoption",
            "archive creation",
            "managed\nmirror refresh",
            "protected sources by inference",
            "standalone plans",
        ):
            self.assertIn(token, self.protocol_text)

    def test_source_truth_impact_contract_is_documented(self):
        for category in CHANGED_PATH_CATEGORIES:
            self.assertIn(f"`{category}`", self.protocol_text)
        for reason in CANONICAL_REFRESH_REASON_CODES:
            self.assertIn(f"`{reason}`", self.protocol_text)
        for posture in REDACTION_POSTURES:
            self.assertIn(f"`{posture}`", self.protocol_text)
        for token in (
            "source_truth_impact",
            "changed_path_boundaries",
            "canonical_refresh_recommended",
            "canonical_refresh_reason_codes",
            "redaction_posture",
            "Impact hints are advisory",
            "governed-pipeline owns canonical refresh, replan, and block decisions",
            "raw diffs",
            "raw spec bodies",
            "raw transcripts",
            "secret-like values",
            "absolute private paths",
            ("provider payloads", "provider-supplied payloads"),
            ("credential payloads", "credential-bearing payloads"),
            ("local environment values", "local environment contents"),
            "private evidence bytes",
        ):
            self.assertTokenInText(token, self.protocol_text)

    def test_bridge_fixture_boundary_is_documented(self):
        runtime_boundary = (ROOT / "docs" / "phase-loop" / "runtime-boundary.md").read_text(encoding="utf-8")
        substrate_manifest = (ROOT / "docs" / "phase-loop" / "harness-substrate-manifest.md").read_text(encoding="utf-8")
        spec_discovery = (ROOT / "docs" / "phase-loop" / "spec-discovery-roots.md").read_text(encoding="utf-8")
        fixture_readme = (Path(__file__).resolve().parent / "fixtures" / "phase_loop_pipeline_bridge" / "README.md").read_text(encoding="utf-8")
        for text in (self.protocol_text, runtime_boundary, substrate_manifest, fixture_readme):
            for token in (
                "vendor/phase-loop-runtime/tests/fixtures/phase_loop_pipeline_bridge/",
                "packages/pipeline-runtime/test/fixtures/phase-loop-bridge/",
                "governed-pipeline-owned",
            ):
                self.assertIn(token, text)
        for token in (
            "downstream mirror location",
            "not a dotfiles write target",
            "mirror updates",
            "closeout ingest",
            "canonical refresh",
            "replan",
            "preflight block decisions",
        ):
            self.assertIn(token, runtime_boundary)
            self.assertIn(token, substrate_manifest)
        for text in (runtime_boundary, substrate_manifest, spec_discovery):
            text_flat = " ".join(text.split())
            for token in (
                "root `specs/**`",
                "Legacy or project-specific seed roots such as `Specs/**`",
                "explicit input roots only",
                "canonical spec adoption",
                "archive manifests",
                "managed mirror manifests",
                "mirror writes",
                "source-truth reconciliation",
                "source-bundle emission",
                "canonical refresh",
                "replan",
                "preflight block decisions",
                "governed-pipeline-owned",
            ):
                self.assertIn(" ".join(token.split()), text_flat)
        for token in (
            "deprecated root-level v5 automation aliases",
            "`status`",
            "`next_skill`",
            "`next_command`",
            "`verification_status`",
            "`artifact`",
            "`artifact_state`",
            "Terminal-summary extraction remains a legacy compatibility path",
        ):
            self.assertIn(token, self.protocol_text)

    def test_operating_mode_contract_is_documented(self):
        section = re.search(
            r"## Operating Modes\n(?P<body>.*?)(?=^##\s+\S|\Z)",
            self.protocol_text,
            flags=re.M | re.S,
        )
        self.assertIsNotNone(section)
        assert section is not None
        body = section.group("body")
        body_flat = " ".join(body.split())
        for literal in PIPELINE_MODE_LITERALS:
            self.assertIn(f"`{literal}`", body)
        for token in (
            "phase_loop_plan_version: 1",
            "remains valid without Pipeline metadata",
            "Standalone runs do not require",
            "governed-pipeline, `.pipeline/**`, Portal, Greenfield, or a source bundle",
            "fail closed before child launch",
            "validated `source_bundle`",
            "`source_bundle_sha256`",
            "`pipeline_phase_id`",
            "bundle freshness",
            "protected-source entries",
            "protected-source hash checks before execution",
            "`freshness.source_bundle_hash` is a SHA-256 digest",
            "Governed-pipeline owns canonical source-truth refresh",
            "source-bundle emission",
            "protected-source freshness",
            "scheduling",
            "closeout ingest",
            "Greenfield reduction",
            "Portal projection",
            "Governed Pipeline owns adoption",
            "canonical refresh",
            "replan",
        ):
            self.assertIn(token, body_flat)

    def test_substrate_boundary_is_metadata_only_and_governed_owned(self):
        text = " ".join(self.protocol_text.split())
        for token in (
            "IF-0-SUBSTRATE-1",
            "broader dotfiles checkout contents are not client dependencies",
            "Governed Pipeline owns adoption",
            "source-bundle emission",
            "canonical refresh",
            "replan",
            "closeout ingest",
            "Portal projection",
            "Host bootstrap",
            "Shell config",
            "MCP gateway setup",
            ("provider payloads", "provider-supplied payloads"),
            ("local environment values", "local environment contents"),
            "Legacy `.codex/phase-loop/` is never a new write target",
        ):
            self.assertTokenInText(token, text)

    def test_execution_policy_selector_contract_rejects_reduce_verify_actions(self):
        body = self.protocol_text
        for selector in ("work-unit defaults", "roadmap", "plan", "execute", "repair", "review", "maintain-skills", "SL-2"):
            self.assertIn(f"`{selector}`", body)
        self.assertIn("`work-unit=phase_reducer`", body)
        self.assertIn("`work-unit=phase_verify`", body)
        self.assertIn("selectors such as `reduce` and `verify` are invalid", body)

    def test_rotation_contract_is_documented(self):
        body = self.protocol_text
        for token in (
            "### Rotation",
            "`--rotate-executors <csv>`",
            "`--rotation-mode <phase|work_unit>`",
            "`--rotation-on-policy-pin <skip|fallback-next>`",
            "`phase`",
            "`work_unit`",
            "`skip`",
            "`fallback-next`",
            "`selected_executor`",
            "policy pin",
            "executor-degradation.json",
            "model and effort",
            "operator-layer preferred executor",
        ):
            self.assertIn(token, body)

    def test_dftruthsoak_preflight_contract_is_documented(self):
        plan_text = (ROOT / "plans" / "phase-plan-v14-DFTRUTHSOAK.md").read_text(encoding="utf-8")
        roadmap_text = (ROOT / "specs" / "phase-plans-v14.md").read_text(encoding="utf-8")
        runtime_boundary = (ROOT / "docs" / "phase-loop" / "runtime-boundary.md").read_text(encoding="utf-8")
        runtime_flat = " ".join(runtime_boundary.split())
        for text in (plan_text, roadmap_text):
            self.assertIn("DFSKILLGUARD", text)
            self.assertIn("GPTRUTHSOAK", text)
            self.assertIn("DFTRUTHSOAK", text)
        for token in (
            "Execution of this phase must not start until DFSKILLGUARD is complete",
            "governed-pipeline `GPTRUTHSOAK` fixtures are ready",
            "missing DFSKILLGUARD completion or governed-pipeline fixture proof is a non-human execution prerequisite",
            "not a reason to edit governed-pipeline from dotfiles",
            ".phase-loop/` remains the authoritative runner state",
            "legacy `.codex/phase-loop/` artifacts were not used as blockers or superseding state",
        ):
            self.assertIn(token, plan_text)
        for token in (
            ".phase-loop/` is the canonical runtime state",
            ".codex/phase-loop/` is a legacy read fallback only when canonical",
            "Governed-pipeline owns mirror updates, closeout ingest, canonical refresh, replan, and preflight block decisions",
        ):
            self.assertIn(token, runtime_flat)

    def test_skill_prompt_guardrails_are_documented(self):
        docs = (
            ROOT / "docs" / "phase-loop" / "runtime-boundary.md",
            ROOT / "docs" / "phase-loop" / "harness-substrate-manifest.md",
            ROOT / "docs" / "phase-loop" / "harness-capability-matrix.md",
            ROOT / "docs" / "phase-loop" / "granular-execution-policy.md",
        )
        required = (
            "standalone dotfiles",
            "governed-pipeline closeout ingest",
            "Portal projection",
            "Greenfield metadata-only authority refs",
            "not direct dotfiles write targets",
            "`.pipeline/**`",
            "governed-pipeline specs",
            "Portal contracts",
            "Greenfield authority files",
            ("provider payloads", "provider-supplied payloads"),
            "legacy `.codex/phase-loop/` state",
        )
        for path in docs:
            text = " ".join(path.read_text(encoding="utf-8").split())
            for token in required:
                self.assertTokenInText(token, text, msg=f"{path} missing token: {token}")

    def test_terminal_summary_fields_are_frozen_in_code(self):
        summary = build_terminal_summary(
            terminal_status="complete",
            terminal_blocker=None,
            verification_status="passed",
            next_action="none",
            dirty_paths=("a",),
            phase_owned_dirty=False,
            phase_owned_dirty_paths=(),
            unowned_dirty_paths=(),
            pre_existing_dirty_paths=(),
            artifact_paths={"plan": "/tmp/plan.md"},
        )
        self.assertEqual(tuple(summary.keys()), TERMINAL_SUMMARY_FIELDS)
        for field in TERMINAL_SUMMARY_FIELDS:
            self.assertRegex(self.protocol_text, rf"`{re.escape(field)}`")

    def test_native_schema_and_if_gate_contract_are_documented(self):
        for token in (
            "## Native Output Schema Enforcement",
            "`CLOSEOUT_SCHEMA`",
            "`terminal_status`",
            "`verification_status`",
            "`dirty_paths`",
            "`produced_if_gates`",
            "`--output-schema <path>`",
            "`LaunchSpec.cleanup_paths`",
            "`--json-schema <compact-json>`",
            "Gemini, OpenCode, PI, command adapters, and manual paths",
            "## Schema-Flow Architecture",
            "`export_function_schema(\"EmitPhaseCloseout\")`",
            "`inject_schema_description(prompt, schema)`",
            "repairable non-human `contract_bug`",
            "## IF-Gate Tier 1 Validation",
            "`validate_produced_gates(plan_path, closeout_payload)`",
            "`Produces`",
            "`Interfaces provided`",
            "NATIVE compatibility window",
            "`contract_bug`",
            "filesystem evidence verification remains out of scope",
            "`phase-loop init [--repo <path>] [--dry-run]`",
            "`/.dev-skills/`",
            "`.dev-skills/handoffs/`",
        ):
            self.assertIn(token, self.protocol_text)

    def test_notification_payload_fields_are_frozen_in_code(self):
        payload = build_notification_payload(
            repo=ROOT,
            roadmap=ROOT / "specs" / "phase-plans-v3.md",
            monitor_status={
                "event_kind": "blocked",
                "current_status": "blocked",
                "recommended_action": "Inspect",
            },
            state_summary={
                "current_phase": "PROTOCOL",
                "human_required": True,
                "blocker_class": "dirty_worktree_conflict",
                "blocker_summary": "Need review",
                "required_human_inputs": ("review",),
                "latest_heartbeat": {"log_path": "/tmp/run.log"},
                "latest_terminal_summary": {"terminal_status": "blocked"},
                "state_path": "/tmp/state.json",
                "event_path": "/tmp/events.jsonl",
                "tui_handoff_path": "/tmp/handoff.md",
            },
        )
        self.assertEqual(tuple(payload.keys()), NOTIFICATION_PAYLOAD_FIELDS)
        for field in NOTIFICATION_PAYLOAD_FIELDS:
            self.assertRegex(self.protocol_text, rf"`{re.escape(field)}`")


if __name__ == "__main__":
    unittest.main()
