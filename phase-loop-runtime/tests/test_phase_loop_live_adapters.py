import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.capability_registry import DEFAULT_CAPABILITY_REGISTRY, claude_support_slice_posture
from phase_loop_runtime.launcher import build_launch_request, build_launch_spec
from phase_loop_runtime.models import CommandAdapterConfig, HarnessLaneAssignment
from phase_loop_runtime.observability import run_artifacts
from phase_loop_runtime.profiles import resolve_profile
from phase_loop_runtime.prompts import build_prompt
from phase_loop_runtime.runner import run_loop
from phase_loop_smoke_utils import fake_live_gate_prerequisite, live_smoke_enabled
from phase_loop_test_utils import make_repo, write_phase_plan


class PhaseLoopLiveAdaptersTest(unittest.TestCase):
    def test_live_wrapper_names_fake_proof_prerequisite(self):
        self.assertEqual(fake_live_gate_prerequisite(), "fake harness parity regression proof")
        script = (ROOT / "scripts" / "smoke-phase-loop-live-adapters").read_text(encoding="utf-8")
        self.assertIn("fake-proof prerequisite", script)
        self.assertIn("Harness policy fake matrix prerequisite", script)
        self.assertIn("vendor/phase-loop-runtime/tests/fixtures/phase_loop_harness_policy", script)
        self.assertIn("Pipeline bridge fake-proof prerequisite", script)
        self.assertIn("DFFAKESMOKE fake-smoke matrix prerequisite", script)
        self.assertIn("vendor/phase-loop-runtime/tests/fixtures/phase_loop_fake_smoke/matrix.json", script)
        self.assertIn("test_phase_loop_pipeline_bridge", script)
        self.assertIn("test_phase_loop_runner.PhaseLoopRunnerTest.test_fake_executor_matrix_contract_self_check", script)

    def test_registry_keeps_claude_support_slices_conservative_until_live_closeout_is_proven(self):
        claude = DEFAULT_CAPABILITY_REGISTRY["claude"]
        gemini = DEFAULT_CAPABILITY_REGISTRY["gemini"]
        opencode = DEFAULT_CAPABILITY_REGISTRY["opencode"]
        pi = DEFAULT_CAPABILITY_REGISTRY["pi"]
        command = DEFAULT_CAPABILITY_REGISTRY["command"]
        slices = claude_support_slice_posture()

        self.assertTrue(claude.live_available)
        self.assertEqual(claude.live_proof_gate, "disposable_proof_required")
        self.assertEqual(claude.promotion_status, "proof_gated")
        self.assertIn("fake harness parity regression proof", claude.promotion_requirements)
        self.assertEqual(
            claude.known_failure_cases,
            (
                "non_interactive_timeout",
                "empty_or_unusable_output_capture",
                "missing_automation_block",
                "missing_terminal_summary",
                "stale_handoff_after_repair",
            ),
        )
        self.assertEqual(
            tuple(slices.keys()),
            (
                "claude_solo",
                "claude_delegated_worker",
                "claude_subagent",
                "claude_agent_team",
            ),
        )
        self.assertEqual(slices["claude_solo"]["maturity_label"], "proof_blocked")
        self.assertTrue(slices["claude_solo"]["launch_default"])
        self.assertEqual(slices["claude_delegated_worker"]["maturity_label"], "proof_blocked")
        self.assertEqual(slices["claude_delegated_worker"]["max_delegation_depth"], 1)
        self.assertEqual(slices["claude_delegated_worker"]["max_fanout"], 1)
        self.assertEqual(slices["claude_subagent"]["maturity_label"], "experimental")
        self.assertEqual(slices["claude_subagent"]["max_fanout"], 2)
        self.assertEqual(slices["claude_agent_team"]["maturity_label"], "experimental")
        self.assertEqual(slices["claude_agent_team"]["max_fanout"], 2)
        self.assertEqual(gemini.promotion_status, "live")
        self.assertIn("fake harness parity regression proof", gemini.promotion_requirements)
        self.assertEqual(opencode.promotion_status, "live")
        self.assertIn("fake harness parity regression proof", opencode.promotion_requirements)
        self.assertEqual(pi.promotion_status, "live")
        self.assertIn("fake Pi adapter matrix", pi.promotion_requirements)
        self.assertIn("pi --help", pi.auth_preflight_probes)
        self.assertEqual(command.promotion_status, "manual_only")
        self.assertIn("fake harness parity regression proof", command.promotion_requirements)

    def test_promote_docs_freeze_operator_support_story_and_safe_examples(self):
        matrix = (ROOT / "docs" / "phase-loop" / "harness-capability-matrix.md").read_text(encoding="utf-8")
        closeout = (ROOT / "docs" / "phase-loop" / "v6-claude-collaboration-closeout.md").read_text(encoding="utf-8")
        research = (ROOT / "docs" / "phase-loop" / "claude-code-v6-research.md").read_text(encoding="utf-8")
        liveproof = (ROOT / "docs" / "phase-loop" / "claude-code-v6-live-proof.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("| Codex | Live-supported |", matrix)
        self.assertIn("| Claude Code | Proof-blocked |", matrix)
        self.assertIn("| `claude_solo` | `proof-blocked` |", matrix)
        self.assertIn("| `claude_delegated_worker` | `proof-blocked` |", matrix)
        self.assertIn("| `claude_subagent` | `experimental` |", matrix)
        self.assertIn("| `claude_agent_team` | `experimental` |", matrix)
        self.assertIn("| Gemini CLI | Live-supported |", matrix)
        self.assertIn("| OpenCode | Live-supported |", matrix)
        self.assertIn("| Generic `command` adapter | Experimental |", matrix)
        self.assertIn("| ThawedCode-specific automation | Manual-only |", matrix)
        self.assertIn("PROMOTE finalizes the operator-facing maturity story", matrix)
        self.assertIn("scripts/smoke-phase-loop-live-adapters", matrix)
        self.assertIn("claude-code-v6-research.md", matrix)
        self.assertIn("claude-code-v6-live-proof.md", matrix)
        self.assertIn("Runner-brokered Claude child work stays proof-blocked", matrix)
        self.assertIn("PHASE_LOOP_ENABLE_CLAUDE_TEAM_LIVE_TEST", liveproof)
        self.assertIn("runner-brokered delegation", liveproof)
        self.assertIn("Solo roadmap", liveproof)
        self.assertIn("Solo review", liveproof)
        self.assertIn("Solo repair", liveproof)
        self.assertIn("run that specific proof from a normal", readme)
        self.assertIn("shell session.", readme)
        self.assertIn("phase-loop run --repo <repo> --roadmap <roadmap> --executor claude --max-phases 1", readme)
        self.assertIn("phase-loop run --repo <repo> --roadmap <roadmap> --executor codex --max-phases 1", readme)
        self.assertIn("phase-loop run --repo <repo> --roadmap <roadmap> --executor codex --max-phases 1 --closeout-mode commit", readme)
        self.assertIn("phase-loop run --repo <repo> --roadmap <roadmap> --executor claude --max-phases 1 --closeout-mode manual", readme)
        self.assertIn("Child-executor switching stays runner-owned", readme)
        self.assertIn("PHASE_LOOP_ENABLE_CLAUDE_TEAM_LIVE_TEST=1", readme)
        self.assertIn("installed bridge skills are the recommended manual reentry surface", readme)
        self.assertIn("## Final Status", closeout)
        self.assertIn("## Support Slices", closeout)
        self.assertIn("## Commands", closeout)
        self.assertIn("## Verification", closeout)
        self.assertIn("## Evidence Paths", closeout)
        self.assertIn("## Deferred Follow-up", closeout)
        self.assertIn("proof-blocked", closeout)
        self.assertIn("experimental", closeout)
        self.assertIn("repo-owned bundle", closeout)
        self.assertIn("typed delegation request", closeout)
        self.assertIn("The installed local help shows every required automation flag except `--teammate-mode`.", research)
        self.assertIn("Status: documented upstream, absent from installed local help.", research)
        self.assertIn("request_id", research)
        self.assertIn("product_action", research)
        self.assertIn("target_executor", research)

    def test_harness_policy_fake_matrix_covers_default_exception_fallbacks_and_explicit_routes(self):
        fixture = json.loads((Path(__file__).resolve().parent / "fixtures" / "phase_loop_harness_policy" / "matrix.json").read_text(encoding="utf-8"))
        routes = {route["case"]: route for route in fixture["routes"]}

        self.assertEqual(routes["simple_bounded_lane_default"]["executor"], "pi")
        self.assertFalse(routes["simple_bounded_lane_default"]["requires_explicit_selection"])
        self.assertEqual(routes["claude_model_exception"]["executor"], "claude")
        self.assertEqual(routes["codex_cli_fallback"]["executor"], "codex")
        self.assertIn("cli_fallback", routes["codex_cli_fallback"]["fallback_reason"])
        self.assertEqual(routes["gemini_cli_fallback"]["executor"], "gemini")
        self.assertIn("cli_fallback", routes["gemini_cli_fallback"]["fallback_reason"])
        for case in ("opencode_explicit_selection", "manual_explicit_selection", "command_explicit_selection"):
            self.assertTrue(routes[case]["requires_explicit_selection"])
        self.assertIn("command_available", fixture["preflight_fields"])
        self.assertIn("stdout_excerpt", fixture["forbidden_fields"])

    def test_codex_live_smoke_is_disabled_inside_active_codex_thread(self):
        with patch.dict("os.environ", {"PHASE_LOOP_ENABLE_CODEX_LIVE_TEST": "1", "CODEX_THREAD_ID": "fixture-thread"}, clear=False):
            self.assertFalse(live_smoke_enabled("codex"))

    def test_context_file_launch_artifacts_record_warning_only_skill_parity(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            parity = type(
                "Parity",
                (),
                {
                    "recommended_installed_roots": ("~/.gemini/skills",),
                    "installed_skill_roots": (),
                    "installed_skill_warnings": ("gemini:gemini-phase-loop: installed bridge root missing",),
                    "bridge_skill_inventory": (
                        {
                            "harness": "gemini",
                            "skill_name": "gemini-phase-loop",
                            "parity_status": "missing_root",
                        },
                    ),
                },
            )()

            with patch("phase_loop_runtime.injection.inspect_skill_parity", return_value=parity):
                request = build_launch_request(
                    executor="gemini",
                    action="execute",
                    repo=repo,
                    roadmap=roadmap,
                    phase="RUNNER",
                    plan=repo / "plans" / "phase-plan-v1-RUNNER.md",
                    model_selection=resolve_profile("execute"),
                    prompt_bundle=build_prompt(
                        "execute",
                        roadmap,
                        phase="RUNNER",
                        plan=repo / "plans" / "phase-plan-v1-RUNNER.md",
                        harness_target="gemini",
                    ),
                    json_output=False,
                    bypass_approvals=False,
                )
            spec = build_launch_spec(request)
            artifacts = run_artifacts(repo, "RUNNER", "execute", 1, spec)
            metadata = json.loads(artifacts["metadata"].read_text(encoding="utf-8"))

            self.assertTrue(spec.available)
            self.assertIn("context", artifacts)
            self.assertEqual(metadata["injection_mode"], "context_file")
            self.assertEqual(metadata["installed_skill_warnings"], ["gemini:gemini-phase-loop: installed bridge root missing"])
            self.assertEqual(metadata["bridge_skill_inventory"][0]["parity_status"], "missing_root")
            self.assertEqual(metadata["context_path"], str(artifacts["context"]))

    def test_lane_launch_artifacts_record_assignment_metadata_for_all_harnesses(self):
        assignment = HarnessLaneAssignment(
            phase="HARNESSLANE",
            lane_id="SL-4",
            work_unit_kind="lane_execute",
            prompt_kind="implementation",
            owned_files=("vendor/phase-loop-runtime/tests/test_phase_loop_lane_harnesses.py",),
            consumed_interfaces=("LaunchSpec.harness_lane_assignment",),
        )
        for executor in ("codex", "claude", "gemini", "opencode", "pi", "command"):
            with self.subTest(executor=executor):
                with tempfile.TemporaryDirectory() as td:
                    repo = make_repo(Path(td))
                    roadmap = repo / "specs" / "phase-plans-v1.md"
                    plan = write_phase_plan(repo, "HARNESSLANE", roadmap)
                    bundle = build_prompt(
                        "execute",
                        roadmap,
                        phase="HARNESSLANE",
                        plan=plan,
                        harness_target=executor,
                        harness_lane_assignment=assignment,
                    )
                    request = build_launch_request(
                        executor=executor,
                        action="execute",
                        repo=repo,
                        roadmap=roadmap,
                        phase="HARNESSLANE",
                        plan=plan,
                        model_selection=resolve_profile("execute"),
                        prompt_bundle=bundle,
                        command_adapter=(
                            CommandAdapterConfig(name="fake", template="fake --context {context_file}", delivery_mode="context_file")
                            if executor == "command"
                            else None
                        ),
                        harness_lane_assignment=assignment,
                        json_output=False,
                        bypass_approvals=False,
                    )
                    spec = build_launch_spec(request)
                    artifacts = run_artifacts(repo, "HARNESSLANE", "execute", 1, spec)
                    metadata = json.loads(artifacts["metadata"].read_text(encoding="utf-8"))

                    self.assertEqual(metadata["harness_lane_assignment"]["lane_id"], "SL-4")
                    self.assertEqual(metadata["lane_id"], "SL-4")
                    self.assertEqual(metadata["work_unit_kind"], "lane_execute")
                    self.assertEqual(metadata["harness_lane_assignment"]["owned_files"], ["vendor/phase-loop-runtime/tests/test_phase_loop_lane_harnesses.py"])

    def test_claude_launch_artifacts_record_context_file_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            request = build_launch_request(
                executor="claude",
                action="plan",
                repo=repo,
                roadmap=roadmap,
                phase="RUNNER",
                plan=None,
                model_selection=resolve_profile("plan"),
                prompt_bundle=build_prompt("plan", roadmap, phase="RUNNER", harness_target="claude"),
                json_output=False,
                bypass_approvals=False,
            )
            spec = build_launch_spec(request)
            artifacts = run_artifacts(repo, "RUNNER", "plan", 1, spec)
            metadata = json.loads(artifacts["metadata"].read_text(encoding="utf-8"))

            self.assertTrue(spec.available)
            self.assertIn("context", artifacts)
            self.assertEqual(spec.delivery_mode, "context_file")
            self.assertEqual(metadata["injection_mode"], "inline")
            self.assertEqual(metadata["fallback_mode"], "context_file")
            self.assertEqual(metadata["context_path"], str(artifacts["context"]))
            self.assertEqual(metadata["context_line_count"], spec.prompt_bundle.context_line_count())
            self.assertEqual(metadata["context_char_count"], spec.prompt_bundle.context_char_count())
            self.assertEqual(metadata["skill_bundle_id"], "phase-loop-claude-plan")
            self.assertEqual(
                metadata["expected_skill_pack"],
                [
                    "claude-phase-roadmap-builder",
                    "claude-plan-phase",
                    "claude-execute-phase",
                    "claude-phase-loop",
                ],
            )
            self.assertTrue(metadata["plugin_bundle_artifacts"]["plugin_dir"].endswith("/claude-bundle/plugin"))
            self.assertTrue(metadata["plugin_bundle_artifacts"]["settings_path"].endswith("/claude-bundle/settings.json"))
            self.assertTrue(metadata["plugin_bundle_artifacts"]["agents_path"].endswith("/claude-bundle/agents.json"))
            self.assertTrue(metadata["plugin_bundle_artifacts"]["mcp_config_path"].endswith("/claude-bundle/mcp.json"))
            context = artifacts["context"].read_text(encoding="utf-8")
            self.assertIn("## Phase-loop adapter constraints", context)
            self.assertIn("this adapter constraint wins", context)
            self.assertIn("do not read or write `~/.claude/**`", context)
            self.assertIn("do not create or edit repo-local `.claude/**` state", context)
            self.assertIn("`.claude/docs-catalog.json`", context)
            self.assertIn("Do not list or read `claude-bundle/plugin/skills/**`", context)
            self.assertIn("`Agent`", context)
            self.assertIn("## Repo-owned Claude bundle", context)
            self.assertNotIn("## Skill:", context)

    def test_claude_skill_bundles_pin_noninteractive_adapter_mode(self):
        plan_skill = (ROOT / "claude-config" / "claude-skills" / "claude-plan-phase" / "SKILL.md").read_text(encoding="utf-8")
        execute_skill = (ROOT / "claude-config" / "claude-skills" / "claude-execute-phase" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("## Phase-Loop Adapter Mode", plan_skill)
        self.assertIn("Do not read installed handoffs", plan_skill)
        self.assertIn("`advisor()`", plan_skill)
        self.assertIn("Do not read or write `~/.claude/**`", plan_skill)
        self.assertIn("Do not wait for", plan_skill)
        self.assertIn("interactive approval", plan_skill)
        self.assertIn("repo-owned bundle includes the full Claude workflow pack", plan_skill)
        self.assertIn("## Phase-Loop Adapter Mode", execute_skill)
        self.assertIn("Do not read installed handoffs", execute_skill)
        self.assertIn("Do not create `.claude/worktrees/`", execute_skill)
        self.assertIn("Do not read or write `~/.claude/**`", execute_skill)
        self.assertIn("Do not wait for", execute_skill)
        self.assertIn("interactive approval", execute_skill)
        self.assertIn("repo-owned bundle includes the full Claude workflow pack", execute_skill)

    def test_live_auth_preflight_blocker_stays_machine_readable(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add runner plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

            with patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type(
                    "Preflight",
                    (),
                    {
                        "ok": False,
                        "blocker_class": "account_or_billing_setup",
                        "blocker_summary": "Gemini auth missing",
                        "metadata": {"executor": "gemini", "probes": [{"probe": "gemini --help", "returncode": 0}]},
                    },
                )(),
            ), patch("phase_loop_runtime.runner.launch_with_spec") as fake_launch:
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER", executor="gemini")

            fake_launch.assert_not_called()
            self.assertEqual(results, [])
            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            event = json.loads((repo / ".phase-loop" / "events.jsonl").read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(event["blocker"]["blocker_class"], "account_or_billing_setup")
            self.assertEqual(event["metadata"]["auth_preflight_result"]["executor"], "gemini")
            self.assertEqual(event["metadata"]["terminal_summary"]["terminal_status"], "blocked")

    def test_live_nonzero_child_exit_preserves_returncode_and_terminal_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add runner plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

            with patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
            ), patch(
                "phase_loop_runtime.runner.launch_with_spec",
                return_value=type(
                    "Launch",
                    (),
                    {
                        "command": ["gemini", "-p"],
                        "returncode": 17,
                        "output": "fatal live child exit\n",
                        "executor": "gemini",
                        "injection_mode": "context_file",
                        "context_sha256": "abc",
                        "expected_skill_pack": ("gemini-execute-phase",),
                        "available": True,
                        "dry_run_only": False,
                        "unavailable_reason": None,
                        "live_proof_gate": "disposable_proof_recorded",
                        "promotion_status": "live",
                        "promotion_requirements": ("disposable live roadmap proof",),
                        "auth_preflight_mode": "metadata_only",
                        "auth_preflight_probes": ("gemini --version",),
                        "timeout_posture": "runner_managed",
                        "output_capture_format": "terminal_summary",
                        "terminal_summary_artifact": "terminal-summary.json",
                        "permission_posture": "explicit",
                        "selected_agent": None,
                        "selected_model": "gpt-5.4",
                        "selected_variant": None,
                        "log_path": None,
                        "heartbeat_path": None,
                        "terminal_path": None,
                        "process_pid": None,
                        "process_group_id": None,
                        "started_at": None,
                        "finished_at": None,
                        "timed_out": False,
                        "interrupted": False,
                        "stalled": False,
                        "cleanup_evidence": None,
                        "heartbeat_summary": None,
                        "failed": True,
                        "event_metadata": lambda self: {
                            "returncode": 17,
                            "executor": "gemini",
                            "injection_mode": "context_file",
                            "context_sha256": "abc",
                            "expected_skill_pack": ["gemini-execute-phase"],
                            "live_proof_gate": "disposable_proof_recorded",
                            "promotion_status": "live",
                            "auth_preflight_mode": "metadata_only",
                            "auth_preflight_probes": ["gemini --version"],
                            "timeout_posture": "runner_managed",
                            "output_capture_format": "terminal_summary",
                            "terminal_summary_artifact": "terminal-summary.json",
                            "permission_posture": "explicit",
                            "selected_model": "gpt-5.4",
                        },
                    },
                )(),
            ):
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER", executor="gemini")

            self.assertEqual(len(results), 1)
            self.assertEqual(snapshot.phases["RUNNER"], "unknown")
            event = json.loads((repo / ".phase-loop" / "events.jsonl").read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(event["metadata"]["launch"]["returncode"], 17)
            self.assertEqual(event["metadata"]["terminal_summary"]["verification_status"], "failed")
            self.assertTrue(Path(event["metadata"]["artifacts"]["terminal"]).exists())

    def test_malformed_live_closeout_fails_closed_with_context_artifact_paths(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add runner plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

            with patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
            ), patch(
                "phase_loop_runtime.runner.launch_with_spec",
                return_value=type(
                    "Launch",
                    (),
                    {
                        "command": ["opencode", "run"],
                        "returncode": 0,
                        "output": '{"content":"no automation here"}',
                        "executor": "opencode",
                        "injection_mode": "context_file",
                        "context_sha256": "abc",
                        "expected_skill_pack": ("opencode-execute-phase",),
                        "available": True,
                        "dry_run_only": False,
                        "unavailable_reason": None,
                        "live_proof_gate": "disposable_proof_recorded",
                        "promotion_status": "live",
                        "promotion_requirements": ("disposable live roadmap proof",),
                        "auth_preflight_mode": "metadata_only",
                        "auth_preflight_probes": ("opencode --version",),
                        "timeout_posture": "runner_managed",
                        "output_capture_format": "terminal_summary",
                        "terminal_summary_artifact": "terminal-summary.json",
                        "permission_posture": "explicit",
                        "selected_agent": "build",
                        "selected_model": "openai/gpt-5.4",
                        "selected_variant": "high",
                        "log_path": None,
                        "heartbeat_path": None,
                        "terminal_path": None,
                        "process_pid": None,
                        "process_group_id": None,
                        "started_at": None,
                        "finished_at": None,
                        "timed_out": False,
                        "interrupted": False,
                        "stalled": False,
                        "cleanup_evidence": None,
                        "heartbeat_summary": None,
                        "failed": False,
                        "event_metadata": lambda self: {
                            "returncode": 0,
                            "executor": "opencode",
                            "injection_mode": "context_file",
                            "context_sha256": "abc",
                            "expected_skill_pack": ["opencode-execute-phase"],
                            "live_proof_gate": "disposable_proof_recorded",
                            "promotion_status": "live",
                            "auth_preflight_mode": "metadata_only",
                            "auth_preflight_probes": ["opencode --version"],
                            "timeout_posture": "runner_managed",
                            "output_capture_format": "terminal_summary",
                            "terminal_summary_artifact": "terminal-summary.json",
                            "permission_posture": "explicit",
                            "selected_agent": "build",
                            "selected_model": "openai/gpt-5.4",
                            "selected_variant": "high",
                        },
                    },
                )(),
            ):
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER", executor="opencode")

            self.assertEqual(len(results), 1)
            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            event = json.loads((repo / ".phase-loop" / "events.jsonl").read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(event["blocker"]["blocker_class"], "repeated_verification_failure")
            self.assertEqual(event["metadata"]["launch"]["executor"], "opencode")
            self.assertTrue(Path(event["metadata"]["artifacts"]["metadata"]).exists())
            self.assertTrue(Path(event["metadata"]["artifacts"]["terminal"]).exists())


if __name__ == "__main__":
    unittest.main()
