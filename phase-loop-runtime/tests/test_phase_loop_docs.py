import unittest
from pathlib import Path
import json
import re

from phase_loop_runtime.skill_install import REQUIRED_SKILLS
from phase_loop_runtime.skill_inventory import CANONICAL_WORKFLOW_SKILLS, HARNESS_INSTALL_ROOT_HINTS, HARNESS_SOURCE_ROOTS


ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


class PhaseLoopDocsTest(unittest.TestCase):
    def test_skillpack_manifest_lists_canonical_skills_and_roots(self):
        matrix = (ROOT / "docs" / "phase-loop" / "harness-skill-matrix.md").read_text(encoding="utf-8")
        self.assertIn("SKILLPACK manifest", matrix)
        self.assertIn("IF-0-SKILLPACK-1", matrix)

        for harness, skills in CANONICAL_WORKFLOW_SKILLS.items():
            if harness not in {"codex", "claude", "gemini", "opencode"}:
                continue
            expected = tuple(f"{harness}-{skill}" for skill in REQUIRED_SKILLS)
            self.assertEqual(set(skills), set(expected))
            for skill in expected:
                with self.subTest(harness=harness, skill=skill):
                    self.assertIn(f"`{skill}`", matrix)
            for source_root in HARNESS_SOURCE_ROOTS[harness]:
                self.assertIn(f"`{source_root}/**`", matrix)
            for install_root in HARNESS_INSTALL_ROOT_HINTS[harness]:
                self.assertIn(f"`{install_root}`", matrix)

    def test_workflow_skill_frontmatter_matches_harness_matrix(self):
        matrix = (ROOT / "docs" / "phase-loop" / "harness-skill-matrix.md").read_text(encoding="utf-8")
        harness_roots = {
            "codex": ROOT / "codex-config" / "skills",
            "claude": ROOT / "claude-config" / "claude-skills",
            "gemini": ROOT / "gemini-config" / "skills",
            "opencode": ROOT / "opencode-config" / "skills",
        }

        for harness, root in harness_roots.items():
            for skill_path in sorted(root.glob("*/SKILL.md")):
                with self.subTest(skill=str(skill_path.relative_to(ROOT))):
                    text = skill_path.read_text(encoding="utf-8")
                    expected_name = skill_path.parent.name
                    self.assertIn(f"name: {expected_name}", text.splitlines()[:4])
                    self.assertIn(f"`{expected_name}`", matrix)
                    description = next(line for line in text.splitlines() if line.startswith("description:"))
                    self.assertRegex(description.lower(), re.escape(harness if harness != "claude" else "claude"))
                    self.assertNotRegex(description, r'["\s](plan-phase|execute-phase)(?:["\s.,]|$)')

        pi_root = ROOT / "phase-loop-pi" / "skills"
        pi_names = {path.parent.name for path in pi_root.glob("*/SKILL.md")}
        self.assertEqual(pi_names, {"phase-loop-supervisor", "phase-loop-repair", "phase-loop-closeout"})
        for name in pi_names:
            text = (pi_root / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn(f"name: {name}", text.splitlines()[:4])

    def test_skill_matrix_preserves_direct_route_compatibility(self):
        matrix = (ROOT / "docs" / "phase-loop" / "harness-skill-matrix.md").read_text(encoding="utf-8")

        self.assertIn("Direct Codex, direct Gemini, and direct OpenCode launcher routes remain", matrix)
        self.assertIn("compatibility-supported", matrix)
        self.assertIn("Claude Code execution continues to use the first-party non-interactive\n`claude -p` path", matrix)
        self.assertNotIn("Pi-backed aliases", matrix)

    def test_readme_presents_neutral_command_as_generic_surface(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("`phase-loop` is the generic command", text)
        self.assertIn("`codex-phase-loop` remains valid for Codex bridge skills", text)
        self.assertIn("Both wrappers call the same `phase_loop_runtime.cli` parser", text)
        self.assertIn("`execute <phase> --bundle --output --mode execute|repair|review`", text)
        self.assertIn("`.phase-loop/state.json`", text)
        self.assertIn("`.codex/phase-loop/` state remains readable", text)
        self.assertIn("no code has\nbeen moved", text)
        self.assertIn("bootstrap installation behavior\nhas not been rewritten", text)
        self.assertIn("`phase-loop-runtime`", text)
        self.assertIn("Governed-pipeline v7 does not wait for extraction", text)
        self.assertIn("PI loop-control docs live at `docs/phase-loop/pi-loop-control.md`", text)

    def test_bootstrap_preserves_neutral_command_and_codex_alias(self):
        text = (ROOT / "bootstrap.sh").read_text(encoding="utf-8")
        self.assertIn("codex-phase-loop", text)
        self.assertIn("phase-loop", text)
        self.assertIn("separate backward-compatible entrypoints over the same parser", text)

    def test_neutralize_docs_define_skills_bundle_contract(self):
        protocol = (ROOT / "vendor" / "phase-loop-runtime" / "protocol" / "protocol.md").read_text(encoding="utf-8")
        readme = (ROOT / "vendor" / "phase-loop-runtime" / "README.md").read_text(encoding="utf-8")
        bootstrap = (ROOT / "bootstrap.sh").read_text(encoding="utf-8")
        runtime_docs = [
            (ROOT / "claude-config" / "shared" / "runtime-state.md").read_text(encoding="utf-8"),
            (ROOT / "codex-config" / "shared" / "runtime-state.md").read_text(encoding="utf-8"),
            (ROOT / "gemini-config" / "shared" / "runtime-state.md").read_text(encoding="utf-8"),
            (ROOT / "opencode-config" / "shared" / "runtime-state.md").read_text(encoding="utf-8"),
        ]

        for text in (protocol, readme):
            self.assertIn("Skills Bundle", text)
            self.assertIn("phase_loop_runtime.skill_paths", text)
            self.assertIn("phase-loop install", text)
            self.assertIn("vendor/phase-loop-skills", text)
            self.assertIn("_overrides/<harness>", text)

        for token in ("~/.claude/skills", "~/.codex/skills", "~/.gemini/skills", "~/.config/opencode/skills"):
            self.assertIn(token, protocol)

        self.assertIn("phase-loop install --harness", bootstrap)
        for text in runtime_docs:
            self.assertIn("phase_loop_runtime.skill_paths", text)
            self.assertIn("repo-local resolver path under `.dev-skills/handoffs/<skill>/`", text)
            self.assertNotIn("write handoffs under harness home skill roots", text)

    def test_runnerpack_docs_freeze_package_boundary(self):
        runtime = (ROOT / "docs" / "phase-loop" / "runtime-boundary.md").read_text(encoding="utf-8")
        extraction = (ROOT / "docs" / "phase-loop" / "extraction-readiness.md").read_text(encoding="utf-8")

        self.assertIn("`version`: Print the installed phase-loop version", runtime)
        self.assertIn("`phase_loop_runtime.runtime_paths`", runtime)
        self.assertIn("`.codex/phase-loop/`: Legacy compatibility root", runtime)
        self.assertIn("without shell profile sourcing", runtime)
        self.assertIn("without ambient `~/.codex` state", runtime)

        self.assertIn("Package-ready boundary, extraction not yet performed", extraction)
        self.assertIn("`phase-loop-runtime`", extraction)
        self.assertIn("Python import package `phase_loop_runtime`", extraction)
        self.assertIn("neutral command `phase-loop`", extraction)
        self.assertIn("backward-compatible alias\n`codex-phase-loop`", extraction)
        self.assertIn("Extraction is not required for governed-pipeline v7 execution", extraction)
        self.assertIn("exact allowlist", extraction)
        self.assertIn("private data, ignored raw data,\n  credentials, evidence-source files, or runner state", extraction)
        self.assertIn("Submodule or Package Integration", extraction)
        self.assertIn("bridge fixture, adapter proof, and py_compile checks", extraction)
        self.assertIn("without provider credentials", extraction)
        self.assertIn("SUBSTRATESOAK may continue against the dotfiles-hosted\nruntime boundary", extraction)
        self.assertIn("Remaining Package Prerequisites", extraction)
        self.assertIn("Preserve the neutral `phase-loop` command", extraction)
        self.assertIn("backward-compatible\n  `codex-phase-loop` alias", extraction)
        self.assertIn("does not create a submodule, move code, or rewrite bootstrap", extraction)

    def test_toolbags_distinguish_neutral_runner_from_codex_alias(self):
        supervisor = (ROOT / "docs" / "phase-loop-supervisor-tool-bag.md").read_text(encoding="utf-8")
        runner = (ROOT / "docs" / "phase-loop-runner-tool-bag.md").read_text(encoding="utf-8")
        inventory = (ROOT / "docs" / "phase-loop-tool-use-inventory.md").read_text(encoding="utf-8")
        self.assertIn("phase-loop state --json", supervisor)
        self.assertIn("`codex-phase-loop` remains only a Codex bridge alias", supervisor)
        self.assertIn("This is the tool bag for the built `phase-loop` system", runner)
        self.assertIn("PI Loop-Control Tool Bag", runner)
        self.assertIn("the built neutral `phase-loop` runner", inventory)
        self.assertIn("phase_loop_state", inventory)

    def test_pi_loop_control_doc_states_decoupling_contract(self):
        text = (ROOT / "docs" / "phase-loop" / "pi-loop-control.md").read_text(encoding="utf-8")
        self.assertIn("PI does not own the state machine", text)
        self.assertIn("`.phase-loop/`: canonical runtime artifact root", text)
        self.assertIn("`.codex/phase-loop/`: legacy compatibility root", text)
        self.assertIn("The PI package calls `phase-loop`, not `codex-phase-loop`.", text)

    def test_native_contracts_are_documented_at_canonical_protocol_path(self):
        protocol = (ROOT / "vendor" / "phase-loop-runtime" / "protocol" / "protocol.md").read_text(encoding="utf-8")
        shared = (ROOT / "shared" / "phase-loop" / "protocol.md").read_text(encoding="utf-8")

        for token in (
            "Native Output Schema Enforcement",
            "IF-Gate Tier 1 Validation",
            "`phase-loop init [--repo <path>] [--dry-run]`",
            "`--output-schema <path>`",
            "`--json-schema <compact-json>`",
            "Schema-Flow Architecture",
            "`produced_if_gates: []`",
        ):
            self.assertIn(token, protocol)
        self.assertIn("Native Output Schema Enforcement", shared)
        self.assertIn("Schema-Flow Architecture", shared)
        self.assertIn("IF-Gate Tier 1 Validation", shared)
        self.assertIn("`phase-loop init`", shared)

    def test_runnergate_verification_evidence_contract_is_documented(self):
        protocol = (ROOT / "vendor" / "phase-loop-runtime" / "protocol" / "protocol.md").read_text(encoding="utf-8")

        for token in (
            "agent_reported_verification_status",
            "--verification-log <path>",
            "PHASE_LOOP_VERIFY_ENFORCE",
            "verification_evidence_missing",
            "append_evidence_entry",
            "fresh entry",
        ):
            self.assertIn(token, protocol)

    def test_adoption_bundle_lifecycle_docs_freeze_refresh_contract(self):
        protocol = (ROOT / "vendor" / "phase-loop-runtime" / "protocol" / "protocol.md").read_text(encoding="utf-8")

        for token in (
            "Adoption Bundle Lifecycle",
            "phase-loop adoption-bundle status",
            "phase-loop adoption-bundle refresh",
            ".githooks/pre-commit-adoption-bundle",
            "phase-loop init --install-hooks",
            "never installed by default",
        ):
            self.assertIn(token, protocol)

    def test_profiledoc_docs_freeze_granular_policy_contract(self):
        guide = (ROOT / "docs" / "phase-loop" / "granular-execution-policy.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        pi = (ROOT / "docs" / "phase-loop" / "pi-loop-control.md").read_text(encoding="utf-8")
        matrix = (ROOT / "docs" / "phase-loop" / "harness-capability-matrix.md").read_text(encoding="utf-8")

        self.assertIn("high -> medium -> high", guide)
        self.assertIn("gemini_cli_config_unverified_but_usable", guide)
        self.assertIn("`pro` for planning/review", guide)
        self.assertIn("`auto` for execution/repair", guide)
        self.assertIn("modelConfigs.customAliases", guide)
        self.assertIn("thinkingConfig.thinkingLevel", guide)
        self.assertIn("automatic push is not promoted", guide)
        self.assertIn("v9 consumes this default profile", guide)

        self.assertIn("docs/phase-loop/granular-execution-policy.md", readme)
        self.assertIn("high -> medium -> high", readme)
        self.assertIn("--closeout-mode commit", readme)
        self.assertIn("automatic push is not a default profile behavior", readme)

        self.assertIn("runner's high -> medium -> high posture", pi)
        self.assertIn("Gemini CLI routing defaults", pi)
        self.assertIn("`pro`", pi)
        self.assertIn("`auto`", pi)
        self.assertIn("Automatic push is not a published default", pi)
        self.assertIn(".phase-loop/metrics.jsonl", matrix)
        self.assertIn("gemini_cli_config_unverified_but_usable", matrix)
        self.assertIn("simple bounded\nscheduler-assigned lane execution defaults to Pi Agent", matrix)
        self.assertIn("Claude/Anthropic model\nlanes default to Claude Code CLI", matrix)
        self.assertIn("Codex/Gemini fallback routes remain\nCLI-based and reason-coded", matrix)
        self.assertIn("must\nnot record raw command stdout", matrix)
        self.assertIn("selected harness, model, effort, profile source", guide)
        self.assertIn("fallback reason", guide)
        self.assertIn("API-key command adapters unless policy explicitly selects `executor=command`", guide)

    def test_legacy_skill_cleanup_doc_freezes_cleanup_classifications(self):
        cleanup = (ROOT / "docs" / "phase-loop" / "legacy-skill-cleanup.md").read_text(encoding="utf-8")
        matrix = (ROOT / "docs" / "phase-loop" / "harness-skill-matrix.md").read_text(encoding="utf-8")

        for token in (
            "canonical",
            "legacy-utility",
            "pi-role",
            "archived-history",
            "remove",
            "claude-plan-phase",
            "claude-execute-phase",
            ".phase-loop/**",
            ".codex/**",
            "handoff",
            "reflection",
        ):
            self.assertIn(token, cleanup)

        self.assertIn("docs/phase-loop/legacy-skill-cleanup.md", matrix)

    def test_migrateloop_docs_freeze_lane_scheduler_operator_contract(self):
        guide_path = ROOT / "docs" / "phase-loop" / "lane-scheduler.md"
        self.assertTrue(guide_path.exists())
        guide = guide_path.read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        policy = (ROOT / "docs" / "phase-loop" / "granular-execution-policy.md").read_text(encoding="utf-8")
        pi = (ROOT / "docs" / "phase-loop" / "pi-loop-control.md").read_text(encoding="utf-8")

        self.assertIn("coarse phase execution", guide)
        self.assertIn("`.phase-loop/metrics.jsonl`", guide)
        self.assertIn("push is never promoted", guide)
        self.assertIn("--lane-scheduler serialized", guide)
        self.assertIn("--lane-scheduler concurrent", guide)
        self.assertIn("stale, a lane already has an active work unit", guide)
        self.assertNotIn("Migration-gated", guide)
        self.assertNotIn("concurrent_migration_gated", guide)

        self.assertIn("docs/phase-loop/lane-scheduler.md", readme)
        self.assertIn("Lane scheduling stays opt-in", readme)
        self.assertIn("`.phase-loop/metrics.jsonl`", policy)
        self.assertIn("push remains explicit", policy)
        self.assertIn("canonical `.phase-loop/` state", pi)
        self.assertIn("Simple bounded scheduler-assigned lane execution defaults to Pi Agent", guide)
        self.assertIn("Claude/Anthropic model lanes default to Claude Code\nCLI", guide)
        self.assertIn("generic `command` adapters remain non-default", guide)
        self.assertIn("docs/phase-loop/dffakesmoke-substrate-receipt.md", guide)

    def test_dffakesmoke_receipt_names_pipeline_consumable_fields(self):
        receipt_path = ROOT / "docs" / "phase-loop" / "dffakesmoke-substrate-receipt.md"
        self.assertTrue(receipt_path.exists())
        receipt = receipt_path.read_text(encoding="utf-8")
        scheduler = (ROOT / "docs" / "phase-loop" / "lane-scheduler.md").read_text(encoding="utf-8")
        runtime = (ROOT / "docs" / "phase-loop" / "runtime-boundary.md").read_text(encoding="utf-8")
        matrix = (ROOT / "docs" / "phase-loop" / "harness-capability-matrix.md").read_text(encoding="utf-8")
        protocol = (ROOT / "vendor" / "phase-loop-runtime" / "protocol" / "protocol.md").read_text(encoding="utf-8")

        for token in (
            "`phase`",
            "`roadmap_sha256`",
            "`plan_path`",
            "`fake_fixture_matrix`",
            "`smoke_commands`",
            "`work_unit_evidence_refs`",
            "`verification_status`",
            "`changed_path_boundaries`",
            "`redaction_posture`",
            "vendor/phase-loop-runtime/tests/fixtures/phase_loop_fake_smoke/matrix.json",
            "scripts/smoke-codex-phase-loop",
            "scripts/smoke-phase-loop-live-adapters",
            "Governed Pipeline owns roadmap-wide scheduling",
            "Dotfiles owns the\nlocal phase-loop runner substrate proof",
            "no raw command output",
        ):
            self.assertIn(token, receipt)

        for text in (scheduler, runtime, matrix, protocol):
            self.assertIn("docs/phase-loop/dffakesmoke-substrate-receipt.md", text)

    def test_dfpromptsync_docs_define_prompt_safe_contract(self):
        contract_path = ROOT / "docs" / "phase-loop" / "dfpromptsync-contract-map.md"
        readiness_path = ROOT / "docs" / "phase-loop" / "dfpromptsync-readiness.md"
        fixture_path = FIXTURES / "phase_loop_prompt_sync" / "matrix.json"
        self.assertTrue(contract_path.exists())
        self.assertTrue(readiness_path.exists())
        self.assertTrue(fixture_path.exists())

        contract = contract_path.read_text(encoding="utf-8")
        readiness = readiness_path.read_text(encoding="utf-8")
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        scheduler = (ROOT / "docs" / "phase-loop" / "lane-scheduler.md").read_text(encoding="utf-8")
        pi = (ROOT / "docs" / "phase-loop" / "pi-loop-control.md").read_text(encoding="utf-8")
        protocol = (ROOT / "vendor" / "phase-loop-runtime" / "protocol" / "protocol.md").read_text(encoding="utf-8")
        granular = (ROOT / "docs" / "phase-loop" / "granular-execution-policy.md").read_text(encoding="utf-8")
        matrix = (ROOT / "docs" / "phase-loop" / "harness-capability-matrix.md").read_text(encoding="utf-8")

        for token in (
            "parallel_work_unit.v0.1",
            "Pi Agent launch/result/evidence",
            "lane-result evidence refs",
            "worktree_assignment.v1",
            "scheduler-owned worktree assignment",
            "machine-verified disjoint lanes",
            "Claude Code CLI exception",
            "CLI-based reason-coded fallback",
            "raw secrets",
            "raw transcripts",
            "raw diffs",
            "raw provider payloads",
            "credential file contents",
            "local env values",
            "prompt-only containment claims",
            "vendor/phase-loop-runtime/tests/fixtures/phase_loop_prompt_sync/matrix.json",
        ):
            self.assertIn(token, contract)

        for token in (
            "DFFAKESMOKE",
            "GFPROMPTEXPORT",
            "GPPROMPTOPT",
            "Governed Pipeline owns scheduling",
            "Greenfield owns authority schemas",
            "`.phase-loop/` remains the canonical runner state surface",
            "DFPARSOAK",
        ):
            self.assertIn(token, readiness)

        self.assertIn("Greenfield parallel_work_unit.v0.1", fixture["valid_schema_citations"])
        self.assertIn("raw provider payloads", fixture["forbidden_prompt_inputs"])
        self.assertEqual(fixture["authority_boundaries"]["dotfiles"].split(", ")[0], "local runner substrate")

        for text in (scheduler, pi, protocol, granular, matrix):
            self.assertIn("docs/phase-loop/dfpromptsync-contract-map.md", text)

    def test_dfparsoak_docs_define_integrated_soak_receipt_and_runbook(self):
        source_map = ROOT / "docs" / "phase-loop" / "dfparsoak-source-map.md"
        receipt_path = ROOT / "docs" / "phase-loop" / "dfparsoak-receipt.md"
        runbook_path = ROOT / "docs" / "phase-loop" / "dfparsoak-runbook.md"
        fixture_path = FIXTURES / "phase_loop_dfparsoak" / "matrix.json"
        self.assertTrue(source_map.exists())
        self.assertTrue(receipt_path.exists())
        self.assertTrue(runbook_path.exists())
        self.assertTrue(fixture_path.exists())

        source_text = source_map.read_text(encoding="utf-8")
        receipt = receipt_path.read_text(encoding="utf-8")
        runbook = runbook_path.read_text(encoding="utf-8")
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        runtime = (ROOT / "docs" / "phase-loop" / "runtime-boundary.md").read_text(encoding="utf-8")
        scheduler = (ROOT / "docs" / "phase-loop" / "lane-scheduler.md").read_text(encoding="utf-8")
        matrix = (ROOT / "docs" / "phase-loop" / "harness-capability-matrix.md").read_text(encoding="utf-8")
        protocol = (ROOT / "vendor" / "phase-loop-runtime" / "protocol" / "protocol.md").read_text(encoding="utf-8")
        granular = (ROOT / "docs" / "phase-loop" / "granular-execution-policy.md").read_text(encoding="utf-8")
        pi = (ROOT / "docs" / "phase-loop" / "pi-loop-control.md").read_text(encoding="utf-8")

        for token in (
            "docs/phase-loop/dffakesmoke-substrate-receipt.md",
            "docs/phase-loop/dfpromptsync-contract-map.md",
            "docs/phase-loop/dfpromptsync-readiness.md",
            "Greenfield GFPARSOAK",
            "governed-pipeline GPPARSOAK",
            "metadata-safe citations",
        ):
            self.assertIn(token, source_text)

        for token in (
            "`phase_loop_closeout.v1`",
            "`lane_id`",
            "`wave_id`",
            "`worktree_path`",
            "`isolation_mode`",
            "`base_sha`",
            "`harness_route`",
            "`model`",
            "`effort`",
            "`policy_source`",
            "`fallback_reason`",
            "redacted evidence refs",
            "Governed Pipeline owns scheduling",
            "Greenfield contract-pack references remain read-only",
        ):
            self.assertIn(token, receipt)

        self.assertIn("<WORKTREE-PATH-REDACTED>", runbook)
        self.assertIn("Do not run destructive worktree cleanup", runbook)
        self.assertEqual(len(fixture["lanes"]), 4)
        self.assertIn("pi", {lane["harness_route"] for lane in fixture["lanes"]})

        for text in (runtime, scheduler, matrix, protocol, granular, pi):
            self.assertIn("docs/phase-loop/dfparsoak-receipt.md", text)
            self.assertIn("docs/phase-loop/dfparsoak-runbook.md", text)

    def test_dotsubstrate_manifest_defines_harness_path_boundary(self):
        manifest_path = ROOT / "docs" / "phase-loop" / "harness-substrate-manifest.md"
        self.assertTrue(manifest_path.exists())
        text = manifest_path.read_text(encoding="utf-8")

        self.assertIn("`vendor/phase-loop-runtime/src/phase_loop_runtime/**`", text)
        self.assertIn("`phase-loop`", text)
        self.assertIn("`shared/phase-loop/protocol.md`", text)
        self.assertIn("`shared/skills/code-cli-runner/**`", text)
        self.assertIn("`vendor/phase-loop-runtime/tests/fixtures/phase_loop_pipeline_bridge/**`", text)
        self.assertIn("`.phase-loop/state.json`", text)
        self.assertIn("`.codex/phase-loop/**`: legacy read fallback only", text)

        self.assertIn("Host bootstrap", text)
        self.assertIn("Shell config", text)
        self.assertIn("Terminal and Zellij config", text)
        self.assertIn("SSH configuration", text)
        self.assertIn("Generic 1Password setup", text)
        self.assertIn("MCP gateway setup", text)
        self.assertIn("Unrelated editor configuration", text)
        self.assertIn("provider payloads", text.lower())
        self.assertIn("local\n  environment values", text)
        self.assertIn("IF-0-SUBSTRATE-1", text)

    def test_dotsubstrate_manifest_is_cited_from_primary_docs(self):
        manifest = "docs/phase-loop/harness-substrate-manifest.md"
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        runtime = (ROOT / "docs" / "phase-loop" / "runtime-boundary.md").read_text(encoding="utf-8")
        extraction = (ROOT / "docs" / "phase-loop" / "extraction-readiness.md").read_text(encoding="utf-8")
        protocol = (ROOT / "vendor" / "phase-loop-runtime" / "protocol" / "protocol.md").read_text(encoding="utf-8")

        self.assertIn(manifest, readme)
        self.assertIn(manifest, runtime)
        self.assertIn(manifest, extraction)
        self.assertIn(manifest, protocol)
        self.assertIn("this protocol remains", protocol)
        self.assertIn("schema and artifact contract", protocol)

    def test_substrate_docs_freeze_public_inventory_and_denials(self):
        manifest = (ROOT / "docs" / "phase-loop" / "harness-substrate-manifest.md").read_text(encoding="utf-8")
        matrix = (ROOT / "docs" / "phase-loop" / "harness-skill-matrix.md").read_text(encoding="utf-8")
        runtime = (ROOT / "docs" / "phase-loop" / "runtime-boundary.md").read_text(encoding="utf-8")
        shared = (ROOT / "shared" / "phase-loop" / "protocol.md").read_text(encoding="utf-8")
        protocol = (ROOT / "vendor" / "phase-loop-runtime" / "protocol" / "protocol.md").read_text(encoding="utf-8")
        combined = " ".join((manifest + "\n" + matrix + "\n" + runtime + "\n" + shared + "\n" + protocol).split())

        for token in (
            "docs/phase-loop/harness-substrate-manifest.md",
            "IF-0-SUBSTRATE-1",
            "`vendor/phase-loop-runtime/**`",
            "CLI wrappers",
            "bridge skills",
            "shared runner skills",
            "protocol docs",
            "fixtures",
            "tests",
            "canonical `.phase-loop/**` state",
            "Host bootstrap",
            "Shell config",
            "MCP gateway",
            "provider payloads",
            "local environment values",
        ):
            self.assertIn(" ".join(token.split()), combined)

        for token in (
            "requires the full dotfiles",
            "client dependency on the dotfiles root",
            "must install owner dotfiles",
            "must source shell profile",
        ):
            self.assertNotIn(token, combined)

    def test_instruction_scope_contract_classifies_required_surfaces(self):
        contract_path = ROOT / "docs" / "phase-loop" / "instruction-scope-contract.md"
        self.assertTrue(contract_path.exists())
        text = contract_path.read_text(encoding="utf-8")

        for token in (
            "owner-fleet",
            "reusable-harness",
            "repo-local-collaborator",
            "claude-config/CLAUDE.md",
            "claude-config/AGENTS.md",
            "shared/instructions/core.md",
            ".agents/skills",
            "Harness skill roots",
            "Bootstrap scripts",
            "Runtime protocol closeout surfaces",
            "IF-0-INSTRINV-1",
            "IF-0-SUBSTRATE-1",
        ):
            self.assertIn(token, text)

        for token in (
            "Governed Pipeline",
            "Portal",
            "ReGenesis",
            "External collaborators",
            "docs/phase-loop/harness-substrate-manifest.md",
            "docs/phase-loop/collaborator-bootstrap.md",
            "shared/phase-loop/protocol.md",
            "vendor/phase-loop-runtime/baml_src/emit_phase_closeout.baml",
        ):
            self.assertIn(token, text)

    def test_instruction_scope_reusable_docs_reject_owner_global_dependency_language(self):
        doc_paths = (
            ROOT / "docs" / "phase-loop" / "instruction-scope-contract.md",
            ROOT / "docs" / "phase-loop" / "harness-substrate-manifest.md",
            ROOT / "docs" / "phase-loop" / "runtime-boundary.md",
            ROOT / "shared" / "phase-loop" / "protocol.md",
        )
        combined = "\n".join(path.read_text(encoding="utf-8") for path in doc_paths)

        for phrase in (
            "requires the full dotfiles",
            "client dependency on the dotfiles root",
            "must install owner dotfiles",
            "must source shell profile",
        ):
            self.assertNotIn(phrase, combined)

    def test_claude_loader_guidance_freezes_repo_local_import_pattern(self):
        claude_global = (ROOT / "claude-config" / "CLAUDE.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        onboarding = (ROOT / "ONBOARDING.md").read_text(encoding="utf-8")
        contract = (ROOT / "docs" / "phase-loop" / "instruction-scope-contract.md").read_text(
            encoding="utf-8"
        )
        collaborator_docs = "\n".join((readme, onboarding, contract))

        for token in (
            "@AGENTS.md",
            "owner-fleet global Claude overlay",
            "repo-local `CLAUDE.md`",
            "Claude-specific overlay",
        ):
            self.assertIn(token, claude_global)

        for token in (
            "IF-0-CLAUDELOAD-1",
            "@AGENTS.md",
            "project-local `CLAUDE.md`",
            "repo-local `CLAUDE.md`",
            "owner-fleet global overlay",
            "Claude-specific overlay",
        ):
            self.assertIn(token, collaborator_docs)

        for phrase in (
            "project behavior through `~/.claude/AGENTS.md`",
            "repo behavior through `~/.claude/AGENTS.md`",
            "must install owner dotfiles",
            "requires the full dotfiles",
        ):
            self.assertNotIn(phrase, collaborator_docs)

    def test_dfskillgovsoak_docs_define_release_gate_boundary(self):
        runbook_path = ROOT / "docs" / "phase-loop" / "dfskillgovsoak.md"
        bridge_readme_path = FIXTURES / "phase_loop_pipeline_bridge" / "README.md"
        self.assertTrue(runbook_path.exists())

        runbook = runbook_path.read_text(encoding="utf-8")
        bridge_readme = bridge_readme_path.read_text(encoding="utf-8")
        matrix = (ROOT / "docs" / "phase-loop" / "harness-skill-matrix.md").read_text(encoding="utf-8")
        manifest = (ROOT / "docs" / "phase-loop" / "harness-substrate-manifest.md").read_text(encoding="utf-8")

        for token in (
            "DFSKILLGOVSOAK",
            "GPSKILLSOAK",
            "governed-pipeline mirrored",
            "dotfiles-only",
            "metadata-only",
            "optional live",
            "not required",
            "temporary legacy aliases",
            "governed-pipeline-owned",
            "mirror writes",
            "closeout ingest",
            "canonical refresh",
            "preflight block",
            "`phase_loop_closeout.v1`",
            "`skill_bundle_sha256`",
            "`next_skill`",
            "`next_command`",
            "`claude -p`",
        ):
            self.assertIn(token, runbook)

        for token in (
            "DFSKILLGOVSOAK Scenario Classification",
            "governed-pipeline mirrored",
            "dotfiles-only",
            "malformed rejection",
            "canonical refresh advisory",
            "temporary legacy alias",
            "unknown-skill coverage",
            "mirror writes",
            "closeout ingest",
            "canonical refresh",
            "preflight block",
        ):
            self.assertIn(token, bridge_readme)

        for text in (matrix, manifest):
            self.assertIn("docs/phase-loop/dfskillgovsoak.md", text)
            self.assertIn("optional live", text)


if __name__ == "__main__":
    unittest.main()
