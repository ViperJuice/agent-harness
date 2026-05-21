import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
from unittest.mock import patch

from phase_loop_runtime.launcher import (
    CLAUDE_ADAPTER_ALLOWED_TOOLS,
    CLAUDE_ADAPTER_DISALLOWED_TOOLS,
    CLAUDE_CONTEXT_PLACEHOLDER,
    LaunchResult,
    build_codex_command,
    build_launch_request,
    build_launch_spec,
    extract_executor_output_text,
    launch,
    launch_with_spec,
    run_auth_preflight,
)
from phase_loop_runtime.models import CommandAdapterConfig, HarnessLaneAssignment, LaneWorktreeAssignment, PhaseSourceBundle, PipelineProtectedSource
from phase_loop_runtime.profiles import resolve_profile, resolve_profile_for_executor
from phase_loop_runtime.prompts import build_prompt
from phase_loop_test_utils import make_repo, write_phase_plan


class PhaseLoopLauncherTest(unittest.TestCase):
    def test_profile_overrides_record_reason(self):
        selection = resolve_profile("execute", model="gpt-5.5", effort="high")
        self.assertEqual(selection.source, "user_override")
        self.assertIn("--model", build_codex_command(Path("/repo"), selection, "prompt"))

    def test_claude_executor_uses_executor_specific_model_alias(self):
        selection = resolve_profile_for_executor(action="plan", executor="claude")
        self.assertEqual(selection.model, "claude-opus-4-7")
        self.assertEqual(selection.effort, "high")
        execute_selection = resolve_profile_for_executor(action="execute", executor="claude")
        self.assertEqual(execute_selection.model, "claude-opus-4-7")
        self.assertEqual(execute_selection.effort, "high")
        self.assertEqual(selection.source, "claude_default")

    def test_opencode_executor_uses_provider_qualified_model_alias(self):
        selection = resolve_profile_for_executor(action="plan", executor="opencode")
        self.assertEqual(selection.model, "openai/gpt-5.5")
        self.assertEqual(selection.source, "opencode_default")

    def test_command_vector_and_dry_run(self):
        selection = resolve_profile("plan")
        bundle = build_prompt("plan", Path("/repo/specs/phase-plans-v1.md"), phase="RUNNER")
        command = build_codex_command(Path("/repo"), selection, bundle.render_prompt(), json_output=True)
        self.assertEqual(command[:4], ["codex", "exec", "--cd", "/repo"])
        self.assertIn('model_reasoning_effort="high"', command)
        self.assertIn("--json", command)
        self.assertIn("plans/phase-plan-v1-RUNNER.md", bundle.render_prompt())
        result = launch(command, dry_run=True)
        self.assertTrue(result.dry_run)
        self.assertIsNone(result.returncode)

    def test_plan_prompt_uses_git_root_for_nested_roadmap_path(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            roadmap = repo / "planning" / "phase-artifacts" / "notes-loop-v1" / "phase-roadmap.md"
            roadmap.parent.mkdir(parents=True)
            roadmap.write_text("### Phase 1 — Runner (RUNNER)\n", encoding="utf-8")

            bundle = build_prompt("plan", roadmap, phase="RUNNER", harness_target="gemini")

        self.assertIn(
            "Use `roadmap: planning/phase-artifacts/notes-loop-v1/phase-roadmap.md`",
            bundle.render_prompt(),
        )
        self.assertNotIn("Use `roadmap: notes-loop-v1/phase-roadmap.md`", bundle.render_prompt())

    def test_plan_prompt_includes_pipeline_source_bundle_context_when_provided(self):
        bundle_context = PhaseSourceBundle(
            path=".pipeline/artifacts/phase-source-bundle.json",
            sha256="a" * 64,
            phase_id="pipeline.phase.runner",
            phase_alias="RUNNER",
            phase_plan_path="plans/phase-plan-v1-RUNNER.md",
            roadmap_path="specs/phase-plans-v1.md",
            roadmap_sha256="b" * 64,
            protected_sources=(
                PipelineProtectedSource(path="specs/protected-source.md", category="specs", sha256="c" * 64),
            ),
            delegated_write_policy={
                "owned_files": ["plans/phase-plan-v1-RUNNER.md"],
                "read_only_files": ["specs/protected-source.md"],
            },
            source_files=(
                {
                    "path": "specs/phase-plans-v1.md",
                    "purpose": "roadmap",
                    "sha256": "b" * 64,
                },
            ),
            artifact_target_root=".pipeline/artifacts/phases/pipeline.phase.runner",
            freshness={"status": "fresh"},
            pipeline_mode="pipeline_required",
        )

        prompt = build_prompt(
            "plan",
            Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            planner_source_bundle_context=bundle_context,
        ).render_prompt()

        self.assertIn("Pipeline planning source bundle", prompt)
        self.assertIn("source_bundle_sha256", prompt)
        self.assertIn("pipeline_phase_id", prompt)
        self.assertIn("pipeline_mode", prompt)
        self.assertIn("protected_sources", prompt)
        self.assertIn("protected-source entries", prompt)
        self.assertIn("delegated_write_policy.owned_files", prompt)
        self.assertIn("delegated write policy", prompt)
        self.assertIn("exact artifact path supplied by the bundle", prompt)
        self.assertIn("plans/phase-plan-v1-RUNNER.md", prompt)
        self.assertIn("`.pipeline/**`", prompt)
        self.assertIn("Portal contracts", prompt)
        self.assertIn("Greenfield authority files", prompt)

    def test_standalone_plan_prompt_omits_pipeline_source_bundle_context(self):
        prompt = build_prompt("plan", Path("/repo/specs/phase-plans-v1.md"), phase="RUNNER").render_prompt()

        self.assertNotIn("Pipeline planning source bundle", prompt)
        self.assertNotIn("source_bundle_sha256:", prompt)
        self.assertNotIn("pipeline_phase_id:", prompt)
        self.assertNotIn("pipeline_mode:", prompt)

    def test_plan_prompt_preserves_state_and_policy_guardrails(self):
        prompt = build_prompt("plan", Path("/repo/specs/phase-plans-v1.md"), phase="RUNNER").render_prompt()

        for token in (
            "Treat `.phase-loop/` as the authoritative runner state",
            "Legacy `.codex/phase-loop/` files are compatibility artifacts only",
            "work-unit defaults",
            "roadmap",
            "plan",
            "execute",
            "repair",
            "review",
            "maintain-skills",
            "SL-2",
            "Dispatch Hints",
            "Policy precedence is CLI/operator override",
            "do not invent action selectors like `reduce` or `verify`",
            "silent downgrade is forbidden",
        ):
            self.assertIn(token, prompt)

    def test_codex_output_reduction_uses_agent_messages(self):
        selection = resolve_profile("execute")
        request = build_launch_request(
            executor="codex",
            action="execute",
            repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
            model_selection=selection,
            prompt_bundle=build_prompt(
                "execute",
                Path("/repo/specs/phase-plans-v1.md"),
                phase="RUNNER",
                plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
            ),
            json_output=True,
            bypass_approvals=False,
        )
        spec = build_launch_spec(request)
        output = (
            '{"type":"item.completed","item":{"type":"agent_message","text":"Read a file containing '
            'automation:\\n  status: planned\\n  verification_status: not_run"}}\n'
            '{"type":"item.completed","item":{"type":"agent_message","text":"automation:\\n'
            '  status: blocked\\n  next_skill: none\\n  next_command: none\\n  human_required: false\\n'
            '  blocker_class: repeated_verification_failure\\n  blocker_summary: SG-0 failed.\\n'
            '  required_human_inputs: []\\n  verification_status: blocked"}}\n'
        )

        text = extract_executor_output_text(LaunchResult(command=["codex"], returncode=0, output=output), spec)

        self.assertIn("status: blocked", text)
        self.assertIn("SG-0 failed.", text)

    def test_launch_request_and_codex_spec_preserve_existing_command(self):
        selection = resolve_profile("execute")
        bundle = build_prompt("execute", Path("/repo/specs/phase-plans-v1.md"), phase="ADAPTER", plan=Path("/repo/plans/phase-plan-v1-ADAPTER.md"))
        request = build_launch_request(
            executor="codex",
            action="execute",
            repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v1.md"),
            phase="ADAPTER",
            plan=Path("/repo/plans/phase-plan-v1-ADAPTER.md"),
            model_selection=selection,
            prompt_bundle=bundle,
            json_output=True,
            bypass_approvals=False,
        )
        spec = build_launch_spec(request)
        self.assertTrue(spec.available)
        self.assertEqual(spec.executor, "codex")
        self.assertEqual(spec.delivery_mode, "prompt_only")
        self.assertEqual(spec.injection_metadata.harness_target, "codex")
        self.assertEqual(spec.injection_metadata.injection_mode, "prompt_only")
        self.assertEqual(spec.injection_metadata.context_sha256, bundle.context_sha256())
        self.assertEqual(spec.injection_metadata.context_line_count, bundle.context_line_count())
        self.assertEqual(spec.injection_metadata.context_char_count, bundle.context_char_count())
        self.assertEqual(spec.injection_metadata.expected_skill_pack, ("codex-execute-phase",))
        self.assertTrue(spec.injection_metadata.skill_bundle_sha256)
        self.assertEqual(spec.live_proof_gate, "none")
        self.assertEqual(spec.promotion_status, "live")
        self.assertEqual(spec.auth_preflight_mode, "metadata_only")
        self.assertEqual(spec.auth_preflight_probes, ("codex --version", "codex --help", "codex login status"))
        self.assertEqual(spec.output_capture_format, "json_stream")
        self.assertEqual(spec.terminal_summary_artifact, "terminal-summary.json")
        self.assertEqual(spec.command, build_codex_command(Path("/repo"), selection, bundle.render_prompt(), json_output=True))
        self.assertIn("<prompt redacted sha256=", spec.to_json()["command"][-1])

    def test_launch_spec_preserves_harness_lane_assignment_metadata(self):
        selection = resolve_profile("execute")
        assignment = HarnessLaneAssignment(
            phase="HARNESSLANE",
            lane_id="SL-2",
            work_unit_kind="lane_execute",
            prompt_kind="implementation",
            owned_files=("vendor/phase-loop-runtime/src/phase_loop_runtime/launcher.py",),
            consumed_interfaces=("build_lane_prompt_bundle",),
            worktree_assignment=LaneWorktreeAssignment(lane_id="SL-2", worktree_path="/repo", isolation_mode="main_worktree"),
            execution_policy={"executor": "codex", "effort": "high"},
        )
        bundle = build_prompt(
            "execute",
            Path("/repo/specs/phase-plans-v9.md"),
            phase="HARNESSLANE",
            plan=Path("/repo/plans/phase-plan-v9-HARNESSLANE.md"),
            harness_lane_assignment=assignment,
        )
        request = build_launch_request(
            executor="codex",
            action="execute",
            repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v9.md"),
            phase="HARNESSLANE",
            plan=Path("/repo/plans/phase-plan-v9-HARNESSLANE.md"),
            model_selection=selection,
            prompt_bundle=bundle,
            harness_lane_assignment=assignment,
            json_output=True,
            bypass_approvals=False,
        )

        spec = build_launch_spec(request)

        self.assertEqual(spec.harness_lane_assignment, assignment)
        self.assertEqual(spec.to_json()["harness_lane_assignment"]["lane_id"], "SL-2")
        self.assertIn("lane_id: `SL-2`", spec.prompt_bundle.render_prompt())

    def test_claude_executor_builds_live_launch_spec(self):
        selection = resolve_profile("plan")
        bundle = build_prompt("plan", Path("/repo/specs/phase-plans-v1.md"), phase="RUNNER", harness_target="claude")
        request = build_launch_request(
            executor="claude",
            action="plan",
            repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            plan=None,
            model_selection=selection,
            prompt_bundle=bundle,
            json_output=False,
            bypass_approvals=False,
        )
        spec = build_launch_spec(request)
        self.assertTrue(spec.available)
        self.assertFalse(spec.dry_run_only)
        self.assertEqual(spec.delivery_mode, "context_file")
        self.assertIsNone(spec.reason)
        self.assertEqual(spec.live_proof_gate, "disposable_proof_required")
        self.assertEqual(spec.promotion_status, "proof_gated")
        self.assertEqual(spec.auth_preflight_mode, "metadata_only")
        self.assertEqual(spec.auth_preflight_probes, ("claude --version", "claude --help", "claude auth status"))
        self.assertEqual(spec.output_capture_format, "terminal_summary")
        self.assertEqual(spec.command[-1], CLAUDE_CONTEXT_PLACEHOLDER)
        self.assertEqual(spec.wrapped_cwd, "/repo")
        with tempfile.TemporaryDirectory() as td:
            result = launch_with_spec(spec, dry_run=True, log_path=Path(td) / "output.log")
        self.assertTrue(result.dry_run)
        self.assertEqual(result.executor, "claude")
        self.assertEqual(result.injection_mode, "context_file")
        self.assertEqual(result.context_sha256, spec.injection_metadata.context_sha256)
        self.assertEqual(
            result.expected_skill_pack,
            (
                "claude-phase-roadmap-builder",
                "claude-plan-phase",
                "claude-execute-phase",
                "claude-phase-loop",
            ),
        )
        self.assertEqual(result.auth_preflight_mode, "metadata_only")
        self.assertEqual(result.terminal_summary_artifact, "terminal-summary.json")
        self.assertEqual(spec.prompt_bundle.workflow_command, "claude-plan-phase /repo/specs/phase-plans-v1.md RUNNER")
        self.assertIn("execution mode", bundle.body)
        self.assertNotIn("## Skill:", spec.prompt_bundle.render_context())
        self.assertEqual(spec.command[:5], ["claude", "-p", "--verbose", "--output-format", "stream-json"])
        self.assertIn("--plugin-dir", spec.command)
        self.assertIn("--settings", spec.command)
        self.assertIn("--agents", spec.command)
        self.assertIn("--mcp-config", spec.command)
        self.assertIn(CLAUDE_ADAPTER_ALLOWED_TOOLS, spec.command)
        self.assertIn(CLAUDE_ADAPTER_DISALLOWED_TOOLS, spec.command)
        self.assertIn("--permission-mode", spec.command)
        self.assertIn("acceptEdits", spec.command)
        self.assertIn("Read the workflow command", result.command[-1])
        self.assertIn("context.md", result.command[-1])
        self.assertTrue(any(part.endswith("/claude-bundle/plugin") for part in result.command))
        self.assertTrue(any(part.startswith("{") and part.endswith("}") for part in result.command))

    def test_claude_execute_uses_noninteractive_bash_permission_mode(self):
        selection = resolve_profile_for_executor(action="execute", executor="claude")
        bundle = build_prompt(
            "execute",
            Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
            harness_target="claude",
        )
        request = build_launch_request(
            executor="claude",
            action="execute",
            repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
            model_selection=selection,
            prompt_bundle=bundle,
            json_output=False,
            bypass_approvals=False,
        )
        spec = build_launch_spec(request)
        permission_index = spec.command.index("--permission-mode") + 1
        self.assertEqual(spec.command[permission_index], "bypassPermissions")

    def test_claude_team_mode_records_governed_policy_for_team_safe_phase(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(
                repo,
                "RUNNER",
                roadmap,
                body=(
                    "# RUNNER\n\n"
                    "## Lanes\n\n"
                    "### SL-0 - One\n"
                    "- **Owned files**: `src/one.py`\n\n"
                    "### SL-1 - Two\n"
                    "- **Owned files**: `src/two.py`\n"
                ),
            )
            request = build_launch_request(
                executor="claude",
                action="execute",
                repo=repo,
                roadmap=roadmap,
                phase="RUNNER",
                plan=plan,
                model_selection=resolve_profile("execute"),
                prompt_bundle=build_prompt("execute", roadmap, phase="RUNNER", plan=plan, harness_target="claude"),
                json_output=False,
                bypass_approvals=False,
                claude_execution_mode="agent_team",
            )
            spec = build_launch_spec(request)

            self.assertTrue(spec.available)
            self.assertEqual(spec.claude_execution_mode, "agent_team")
            self.assertEqual(spec.claude_team_policy.execution_mode, "agent_team")
            self.assertTrue(spec.phase_team_eligibility.eligible_for_native_team)
            self.assertIn("TaskList", spec.to_json()["claude_team_policy"]["allowed_tools"])
            tools = spec.command[spec.command.index("--tools") + 1].split(",")
            self.assertIn("TeamCreate", tools)
            self.assertIn("EnterWorktree", tools)
            self.assertNotIn("TaskList", spec.to_json()["claude_team_policy"]["disallowed_tools"])

    def test_unsafe_claude_team_mode_fails_before_launch(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(
                repo,
                "RUNNER",
                roadmap,
                body=(
                    "# RUNNER\n\n"
                    "## Lanes\n\n"
                    "### SL-0 - One\n"
                    "- **Owned files**: `src/*.py`\n\n"
                    "### SL-1 - Two\n"
                    "- **Owned files**: `src/app.py`\n"
                ),
            )
            request = build_launch_request(
                executor="claude",
                action="execute",
                repo=repo,
                roadmap=roadmap,
                phase="RUNNER",
                plan=plan,
                model_selection=resolve_profile("execute"),
                prompt_bundle=build_prompt("execute", roadmap, phase="RUNNER", plan=plan, harness_target="claude"),
                json_output=False,
                bypass_approvals=False,
                claude_execution_mode="agent_team",
            )
            spec = build_launch_spec(request)

            self.assertFalse(spec.available)
            self.assertIn("not team-safe", spec.reason)
            self.assertEqual(spec.claude_execution_mode, "agent_team")

    def test_launch_with_spec_passes_wrapped_cwd_to_launcher(self):
        selection = resolve_profile("plan")
        bundle = build_prompt("plan", Path("/repo/specs/phase-plans-v1.md"), phase="RUNNER", harness_target="claude")
        request = build_launch_request(
            executor="claude",
            action="plan",
            repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            plan=None,
            model_selection=selection,
            prompt_bundle=bundle,
            json_output=False,
            bypass_approvals=False,
        )
        spec = build_launch_spec(request)
        with patch("phase_loop_runtime.launcher.launch", return_value=LaunchResult(command=spec.command, returncode=0)) as mocked:
            launch_with_spec(spec, log_path=Path("/tmp/output.log"))
        self.assertEqual(mocked.call_args.kwargs["cwd"], "/repo")

    def test_claude_auth_preflight_reduces_missing_login_to_typed_blocker(self):
        selection = resolve_profile("execute")
        request = build_launch_request(
            executor="claude",
            action="execute",
            repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
            model_selection=selection,
            prompt_bundle=build_prompt(
                "execute",
                Path("/repo/specs/phase-plans-v1.md"),
                phase="RUNNER",
                plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
                harness_target="claude",
            ),
            json_output=False,
            bypass_approvals=False,
        )
        spec = build_launch_spec(request)

        def fake_run(cmd, shell, text, capture_output, check):
            class Completed:
                def __init__(self, returncode, stdout="", stderr=""):
                    self.returncode = returncode
                    self.stdout = stdout
                    self.stderr = stderr

            if cmd == "claude auth status":
                return Completed(0, '{"loggedIn": false}')
            return Completed(0, "ok")

        with patch("phase_loop_runtime.launcher.subprocess.run", side_effect=fake_run):
            preflight = run_auth_preflight(spec)

        self.assertFalse(preflight.ok)
        self.assertEqual(preflight.blocker_class, "account_or_billing_setup")
        self.assertIn("not logged in", preflight.blocker_summary)
        serialized = str(preflight.metadata)
        self.assertNotIn("stdout_excerpt", serialized)
        self.assertNotIn("stderr_excerpt", serialized)
        self.assertIn("stdout_present", serialized)
        self.assertEqual(preflight.suggested_ttl_seconds, 300)
        self.assertEqual(preflight.demoted_to, "proof_gated")

    def test_claude_auth_preflight_reduces_capacity_to_outage_blocker(self):
        selection = resolve_profile("execute")
        request = build_launch_request(
            executor="claude",
            action="execute",
            repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
            model_selection=selection,
            prompt_bundle=build_prompt(
                "execute",
                Path("/repo/specs/phase-plans-v1.md"),
                phase="RUNNER",
                plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
                harness_target="claude",
            ),
            json_output=False,
            bypass_approvals=False,
        )
        spec = build_launch_spec(request)

        def fake_run(cmd, shell, text, capture_output, check):
            class Completed:
                def __init__(self, returncode, stdout="", stderr=""):
                    self.returncode = returncode
                    self.stdout = stdout
                    self.stderr = stderr

            if cmd == "claude auth status":
                return Completed(0, '{"loggedIn": true, "subscriptionType": "pro", "quota": "exhausted"}')
            return Completed(0, "ok")

        with patch("phase_loop_runtime.launcher.subprocess.run", side_effect=fake_run):
            preflight = run_auth_preflight(spec)

        self.assertFalse(preflight.ok)
        self.assertEqual(preflight.blocker_class, "unretryable_external_outage")
        self.assertEqual(preflight.suggested_ttl_seconds, 1800)
        self.assertEqual(preflight.demoted_to, "manual_only")
        serialized = str(preflight.metadata)
        self.assertNotIn("stdout_excerpt", serialized)
        self.assertNotIn("stderr_excerpt", serialized)

    def test_extract_executor_output_text_normalizes_legacy_claude_closeout(self):
        selection = resolve_profile("plan")
        request = build_launch_request(
            executor="claude",
            action="plan",
            repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            plan=None,
            model_selection=selection,
            prompt_bundle=build_prompt("plan", Path("/repo/specs/phase-plans-v1.md"), phase="RUNNER", harness_target="claude"),
            json_output=False,
            bypass_approvals=False,
        )
        spec = build_launch_spec(request)
        result = LaunchResult(
            command=["claude", "-p"],
            returncode=0,
            executor="claude",
            output=(
                '{"type":"result","result":"```yaml\\nautomation:\\n'
                "  skill: claude-plan-phase\\n"
                "  phase: runner\\n"
                "  phase_id: RUNNER\\n"
                "  verification_status: passed\\n"
                "  next_phase: RUNNER - execution ready\\n"
                "  next_command: /claude-execute-phase runner\\n"
                '```"}'
            ),
        )

        text = extract_executor_output_text(result, spec)

        self.assertIn("status: planned", text)
        self.assertIn("next_skill: claude-execute-phase", text)
        self.assertIn("next_command: /claude-execute-phase runner", text)
        self.assertIn("human_required: false", text)
        self.assertIn("blocker_class: none", text)

    def test_extract_executor_output_text_canonicalizes_success_literals(self):
        selection = resolve_profile("execute")
        request = build_launch_request(
            executor="claude",
            action="execute",
            repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
            model_selection=selection,
            prompt_bundle=build_prompt(
                "execute",
                Path("/repo/specs/phase-plans-v1.md"),
                phase="RUNNER",
                plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
                harness_target="claude",
            ),
            json_output=False,
            bypass_approvals=False,
        )
        spec = build_launch_spec(request)
        result = LaunchResult(
            command=["claude", "-p"],
            returncode=0,
            executor="claude",
            output=(
                '{"type":"result","result":"```yaml\\nautomation:\\n'
                "  status: ok\\n"
                "  verification_status: passed — validate_roadmap.py reports OK — 3 phase(s) in specs/phase-plans-v1.md\\n"
                "  next_phase: none - roadmap complete\\n"
                "  next_command: none\\n"
                '```"}'
            ),
        )

        text = extract_executor_output_text(result, spec)

        self.assertIn("status: complete", text)
        self.assertIn("verification_status: passed", text)
        self.assertIn("next_skill: none", text)

    def test_extract_executor_output_text_treats_plan_only_closeout_as_planned(self):
        selection = resolve_profile("plan")
        request = build_launch_request(
            executor="claude",
            action="plan",
            repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            plan=None,
            model_selection=selection,
            prompt_bundle=build_prompt("plan", Path("/repo/specs/phase-plans-v1.md"), phase="RUNNER", harness_target="claude"),
            json_output=False,
            bypass_approvals=False,
        )
        spec = build_launch_spec(request)
        result = LaunchResult(
            command=["claude", "-p"],
            returncode=0,
            executor="claude",
            output=(
                '{"type":"result","result":"```yaml\\nautomation:\\n'
                "  status: success\\n"
                "  next_skill: claude-execute-phase\\n"
                "  next_command: claude-execute-phase /repo/plans/phase-plan-v1-RUNNER.md RUNNER\\n"
                "  human_required: false\\n"
                "  blocker_class: none\\n"
                "  blocker_summary: none\\n"
                "  required_human_inputs: []\\n"
                "  verification_status: plan-only (no verification commands run; this is a plan-phase artifact)\\n"
                '```"}'
            ),
        )

        text = extract_executor_output_text(result, spec)

        self.assertIn("status: planned", text)
        self.assertIn("verification_status: passed", text)
        self.assertIn("blocker_class: none", text)

    def test_extract_executor_output_text_treats_evidence_checked_repair_as_planned(self):
        selection = resolve_profile("repair")
        request = build_launch_request(
            executor="claude",
            action="repair",
            repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
            model_selection=selection,
            prompt_bundle=build_prompt(
                "repair",
                Path("/repo/specs/phase-plans-v1.md"),
                phase="RUNNER",
                plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
                harness_target="claude",
            ),
            json_output=False,
            bypass_approvals=False,
        )
        spec = build_launch_spec(request)
        result = LaunchResult(
            command=["claude", "-p"],
            returncode=0,
            executor="claude",
            output=(
                '{"type":"result","result":"```yaml\\nautomation:\\n'
                "  status: success\\n"
                "  next_skill: claude-execute-phase\\n"
                "  next_command: claude-execute-phase /repo/plans/phase-plan-v1-RUNNER.md RUNNER\\n"
                "  human_required: false\\n"
                "  blocker_class: none\\n"
                "  blocker_summary: none\\n"
                "  required_human_inputs: []\\n"
                "  verification_status: evidence_checked\\n"
                '```"}'
            ),
        )

        text = extract_executor_output_text(result, spec)

        self.assertIn("status: planned", text)
        self.assertIn("verification_status: passed", text)
        self.assertIn("blocker_class: none", text)

    def test_extract_executor_output_text_canonicalizes_done_and_repaired_literals(self):
        selection = resolve_profile("repair")
        request = build_launch_request(
            executor="claude",
            action="repair",
            repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
            model_selection=selection,
            prompt_bundle=build_prompt(
                "repair",
                Path("/repo/specs/phase-plans-v1.md"),
                phase="RUNNER",
                plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
                harness_target="claude",
            ),
            json_output=False,
            bypass_approvals=False,
        )
        spec = build_launch_spec(request)

        done = extract_executor_output_text(
            LaunchResult(
                command=["claude", "-p"],
                returncode=0,
                executor="claude",
                output=(
                    '{"type":"result","result":"```yaml\\nautomation:\\n'
                    "  status: done\\n"
                    "  verification_status: success\\n"
                    "  next_phase: none - roadmap complete\\n"
                    "  next_command: none\\n"
                    '```"}'
                ),
            ),
            spec,
        )
        repaired = extract_executor_output_text(
            LaunchResult(
                command=["claude", "-p"],
                returncode=0,
                executor="claude",
                output=(
                    '{"type":"result","result":"```yaml\\nautomation:\\n'
                    "  status: repaired\\n"
                    "  verification_status: success\\n"
                    "  next_phase: RUNNER - repair applied\\n"
                    "  next_command: codex-phase-loop resume\\n"
                    '```"}'
                ),
            ),
            spec,
        )
        review_complete = extract_executor_output_text(
            LaunchResult(
                command=["claude", "-p"],
                returncode=0,
                executor="claude",
                output=(
                    '{"type":"result","result":"```yaml\\nautomation:\\n'
                    "  status: review_complete\\n"
                    "  verification_status: success\\n"
                    "  next_phase: none - roadmap complete\\n"
                    "  next_command: none\\n"
                    '```"}'
                ),
            ),
            spec,
        )
        partial_complete = extract_executor_output_text(
            LaunchResult(
                command=["claude", "-p"],
                returncode=0,
                executor="claude",
                output=(
                    '{"type":"result","result":"```yaml\\nautomation:\\n'
                    "  status: done\\n"
                    "  human_required: false\\n"
                    "  blocker_class: none\\n"
                    "  blocker_summary: none\\n"
                    "  verification_status: partial\\n"
                    "  next_phase: none - roadmap complete\\n"
                    "  next_command: none\\n"
                    '```"}'
                ),
            ),
            spec,
        )
        roadmap_ready = extract_executor_output_text(
            LaunchResult(
                command=["claude", "-p"],
                returncode=0,
                executor="claude",
                output=(
                    '{"type":"result","result":"```yaml\\nautomation:\\n'
                    "  status: success\\n"
                    "  next_skill: claude-plan-phase\\n"
                    "  next_command: /claude-plan-phase CONTRACT\\n"
                    "  verification_status: passed — validate_roadmap.py reports OK — 3 phase(s) in specs/phase-plans-v1.md\\n"
                    '```"}'
                ),
            ),
            spec,
        )

        self.assertIn("status: complete", done)
        self.assertIn("status: executed", repaired)
        self.assertIn("verification_status: passed", repaired)
        self.assertIn("status: complete", review_complete)
        self.assertIn("status: complete", partial_complete)
        self.assertIn("verification_status: passed", partial_complete)
        self.assertIn("status: planned", roadmap_ready)
        self.assertIn("next_skill: claude-plan-phase", roadmap_ready)
        self.assertIn("verification_status: passed", roadmap_ready)

    def test_observed_launch_writes_heartbeat_for_quiet_child(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_path = root / "output.log"
            heartbeat_path = root / "heartbeat.json"
            result = launch(
                [sys.executable, "-c", "import time; time.sleep(0.2); print('done')"],
                log_path=log_path,
                heartbeat_path=heartbeat_path,
                heartbeat_interval_seconds=1,
                quiet_warning_seconds=1,
                quiet_blocker_seconds=5,
            )

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.heartbeat_path, str(heartbeat_path))
            self.assertEqual(result.terminal_path, str(root / "terminal-summary.json"))
            self.assertTrue(heartbeat_path.exists())
            self.assertIn("done", log_path.read_text(encoding="utf-8"))
            self.assertEqual(result.heartbeat_summary["returncode"], 0)
            self.assertIn("quiet_level", result.heartbeat_summary)

    def test_launch_without_log_closes_stdin_when_no_payload(self):
        class Completed:
            returncode = 0
            stdout = "ok\n"
            stderr = ""

        with patch("phase_loop_runtime.launcher.subprocess.run", return_value=Completed()) as mocked:
            result = launch(["example-cli"])

        self.assertEqual(result.returncode, 0)
        self.assertEqual(mocked.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertNotIn("input", mocked.call_args.kwargs)

    def test_observed_launch_closes_stdin_when_no_payload(self):
        captured_kwargs = {}

        class FakeProcess:
            pid = 12345
            stdout = io.StringIO("ok\n")

            def poll(self):
                return 0

            def wait(self):
                return 0

        def fake_popen(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return FakeProcess()

        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "output.log"
            with (
                patch("phase_loop_runtime.launcher.subprocess.Popen", side_effect=fake_popen),
                patch("phase_loop_runtime.launcher._process_group_id", return_value=12345),
            ):
                result = launch(["example-cli"], log_path=log_path)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(captured_kwargs["stdin"], subprocess.DEVNULL)

    def test_timeout_launch_cleans_up_process_group_and_records_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_path = root / "output.log"
            heartbeat_path = root / "heartbeat.json"

            result = launch(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                log_path=log_path,
                heartbeat_path=heartbeat_path,
                timeout_seconds=1,
            )

            self.assertTrue(result.timed_out)
            self.assertIsNotNone(result.cleanup_evidence)
            self.assertEqual(result.cleanup_evidence["reason"], "timeout")
            self.assertIn("SIGTERM", result.cleanup_evidence["signals_sent"])
            self.assertFalse(result.cleanup_evidence["process_alive_after_cleanup"])
            self.assertIsNotNone(result.process_group_id)

    def test_repair_prompt_uses_deterministic_checklist_and_recovery_entrypoints(self):
        prompt = build_prompt(
            "repair",
            Path("/repo/specs/phase-plans-v2.md"),
            phase="REPAIRDX",
            plan=Path("/repo/plans/phase-plan-v2-REPAIRDX.md"),
            blocker_summary="Trusted repair needed.",
            repair_context={
                "terminal_summary": {
                    "terminal_status": "executed",
                    "verification_status": "blocked",
                    "next_action": "Inspect the machine state.",
                },
                "dirty_paths": ["README.md"],
                "phase_owned_dirty_paths": ["README.md"],
                "phase_owned_dirty": True,
                "closeout_summary": {
                    "closeout_mode": "manual",
                    "closeout_action": "awaiting_phase_closeout",
                    "verification_status": "passed",
                },
                "artifact_paths": {
                    "log": "/repo/.phase-loop/runs/x/output.log",
                    "terminal": "/repo/.phase-loop/runs/x/terminal-summary.json",
                    "metadata": "/repo/.phase-loop/runs/x/launch.json",
                },
            },
        )
        rendered = prompt.render_prompt()

        self.assertIn("Repair checklist:", rendered)
        self.assertIn("`.phase-loop/state.json`", rendered)
        self.assertIn("`.phase-loop/events.jsonl`", rendered)
        self.assertIn("`phase-loop handoff`", rendered)
        self.assertIn("`phase-loop status --json`", rendered)
        self.assertIn("Runner-owned `.phase-loop/` ledger writes are optional", rendered)
        self.assertIn("the parent runner can reconcile the repair", rendered)
        self.assertIn("Allowed outcomes only:", rendered)
        self.assertIn("Trusted machine context:", rendered)
        self.assertIn("dirty_paths=README.md", rendered)
        self.assertIn("closeout_mode=manual", rendered)
        self.assertIn("latest_run_log=/repo/.phase-loop/runs/x/output.log", rendered)
        self.assertEqual(prompt.expected_skill_pack, ("codex-phase-loop", "codex-execute-phase"))

    def test_context_file_delivery_keeps_payload_out_of_stub_command(self):
        selection = resolve_profile("execute")
        bundle = build_prompt(
            "execute",
            Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
            harness_target="gemini",
        )
        request = build_launch_request(
            executor="gemini",
            action="execute",
            repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
            model_selection=selection,
            prompt_bundle=bundle,
            json_output=False,
            bypass_approvals=False,
        )
        spec = build_launch_spec(request)
        self.assertEqual(spec.delivery_mode, "context_file")
        self.assertNotIn(bundle.render_context(), " ".join(spec.command))
        self.assertEqual(spec.to_json()["delivery_mode"], "context_file")

    def test_command_executor_requires_explicit_adapter_inputs(self):
        selection = resolve_profile("execute")
        request = build_launch_request(
            executor="command",
            action="execute",
            repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
            model_selection=selection,
            prompt_bundle=build_prompt(
                "execute",
                Path("/repo/specs/phase-plans-v1.md"),
                phase="RUNNER",
                plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
                harness_target="command",
                injection_mode_override="context_file",
            ),
            json_output=False,
            bypass_approvals=False,
        )
        spec = build_launch_spec(request)
        self.assertFalse(spec.available)
        self.assertIn("requires explicit adapter inputs", spec.reason)

    def test_command_executor_builds_context_file_launch_spec(self):
        selection = resolve_profile("execute")
        request = build_launch_request(
            executor="command",
            action="execute",
            repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
            model_selection=selection,
            prompt_bundle=build_prompt(
                "execute",
                Path("/repo/specs/phase-plans-v1.md"),
                phase="RUNNER",
                plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
                harness_target="command",
                injection_mode_override="context_file",
            ),
            json_output=False,
            bypass_approvals=False,
            command_adapter=CommandAdapterConfig(
                name="wrapped-cli",
                template="wrapped-cli --cwd {cwd} --plan {plan} --context-file {context_file} --model {model}",
            ),
        )
        spec = build_launch_spec(request)
        self.assertTrue(spec.available)
        self.assertEqual(spec.delivery_mode, "context_file")
        self.assertEqual(spec.command_adapter_name, "wrapped-cli")
        self.assertEqual(spec.wrapped_cwd, "/repo")
        self.assertIn("--context-file", spec.command)
        self.assertIn("__PHASE_LOOP_CONTEXT_FILE__", " ".join(spec.command))
        self.assertIn("--model", spec.command)

    def test_gemini_executor_builds_live_launch_spec(self):
        selection = resolve_profile("plan")
        request = build_launch_request(
            executor="gemini",
            action="plan",
            repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            plan=None,
            model_selection=selection,
            prompt_bundle=build_prompt("plan", Path("/repo/specs/phase-plans-v1.md"), phase="RUNNER", harness_target="gemini"),
            json_output=False,
            bypass_approvals=False,
        )
        spec = build_launch_spec(request)
        self.assertTrue(spec.available)
        self.assertFalse(spec.dry_run_only)
        self.assertEqual(spec.delivery_mode, "context_file")
        self.assertEqual(spec.live_proof_gate, "disposable_proof_recorded")
        self.assertEqual(spec.promotion_status, "live")
        self.assertEqual(spec.auth_preflight_mode, "metadata_only")
        self.assertEqual(spec.auth_preflight_probes, ("gemini --version", "gemini --help"))
        self.assertEqual(spec.command[:8], ["gemini", "-p", spec.command[2], "--skip-trust", "--approval-mode", "yolo", "--include-directories", "/repo"])
        self.assertIn("__PHASE_LOOP_CONTEXT_FILE__", spec.command[2])
        self.assertIn("do not assume a tool named `run_shell_command` exists", spec.prompt_bundle.render_context())
        result = launch_with_spec(spec, dry_run=True, log_path=Path("/tmp/gemini/output.log"))
        self.assertTrue(result.dry_run)
        self.assertEqual(result.executor, "gemini")
        self.assertEqual(result.injection_mode, "context_file")
        self.assertEqual(result.expected_skill_pack, ("gemini-plan-phase",))

    def test_opencode_executor_builds_live_launch_spec(self):
        selection = resolve_profile_for_executor(action="plan", executor="opencode")
        request = build_launch_request(
            executor="opencode",
            action="plan",
            repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            plan=None,
            model_selection=selection,
            prompt_bundle=build_prompt("plan", Path("/repo/specs/phase-plans-v1.md"), phase="RUNNER", harness_target="opencode"),
            json_output=False,
            bypass_approvals=True,
        )
        spec = build_launch_spec(request)
        self.assertTrue(spec.available)
        self.assertFalse(spec.dry_run_only)
        self.assertEqual(spec.delivery_mode, "context_file")
        self.assertEqual(spec.live_proof_gate, "disposable_proof_recorded")
        self.assertEqual(spec.promotion_status, "live")
        self.assertEqual(spec.auth_preflight_mode, "metadata_only")
        self.assertEqual(spec.auth_preflight_probes, ("opencode --version", "opencode run --help", "opencode agent list"))
        self.assertEqual(spec.permission_posture, "explicit")
        self.assertEqual(spec.selected_agent, "build")
        self.assertEqual(spec.selected_model, "openai/gpt-5.5")
        self.assertEqual(
            spec.command[:10],
            ["opencode", "run", spec.command[2], "--dir", "/repo", "--agent", "build", "--model", "openai/gpt-5.5", "--format"],
        )
        self.assertIn("__PHASE_LOOP_CONTEXT_FILE__", spec.command[2])
        result = launch_with_spec(spec, dry_run=True, log_path=Path("/tmp/opencode/output.log"))
        self.assertTrue(result.dry_run)
        self.assertEqual(result.executor, "opencode")
        self.assertEqual(result.injection_mode, "context_file")
        self.assertEqual(result.expected_skill_pack, ("opencode-plan-phase",))
        self.assertEqual(result.permission_posture, "explicit")
        self.assertEqual(result.selected_agent, "build")
        self.assertEqual(result.selected_model, "openai/gpt-5.5")

    def test_pi_executor_builds_repo_local_launch_spec(self):
        selection = resolve_profile_for_executor(action="execute", executor="pi")
        request = build_launch_request(
            executor="pi",
            action="execute",
            repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
            model_selection=selection,
            prompt_bundle=build_prompt(
                "execute",
                Path("/repo/specs/phase-plans-v1.md"),
                phase="RUNNER",
                plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
                harness_target="pi",
            ),
            json_output=False,
            bypass_approvals=False,
        )
        spec = build_launch_spec(request)
        self.assertTrue(spec.available)
        self.assertFalse(spec.dry_run_only)
        self.assertEqual(spec.delivery_mode, "context_file")
        self.assertEqual(spec.live_proof_gate, "disposable_proof_recorded")
        self.assertEqual(spec.promotion_status, "live")
        self.assertEqual(spec.auth_preflight_mode, "metadata_only")
        self.assertEqual(spec.auth_preflight_probes, ("pi --version", "pi --help"))
        self.assertEqual(spec.permission_posture, "explicit")
        self.assertEqual(spec.selected_model, "auto")
        self.assertEqual(spec.selected_effort, "medium")
        self.assertEqual(spec.profile_source, "pi_default")
        self.assertEqual(spec.override_reason, "pi live adapter default model alias")
        self.assertEqual(spec.command[:4], ["pi-agent-watch", "--repo", "/repo", "--prompt-file"])
        self.assertIn("__PHASE_LOOP_CONTEXT_FILE__", spec.command)
        result = launch_with_spec(spec, dry_run=True, log_path=Path("/tmp/pi/output.log"))
        self.assertTrue(result.dry_run)
        self.assertEqual(result.executor, "pi")
        self.assertEqual(result.injection_mode, "context_file")
        self.assertEqual(result.expected_skill_pack, ("phase-loop-supervisor", "phase-loop-closeout"))

    def test_metadata_preflight_for_live_harnesses_never_records_raw_output(self):
        def fake_run(cmd, shell, text, capture_output, check):
            class Completed:
                def __init__(self, stdout="Usage: fake --help\n", stderr=""):
                    self.returncode = 0
                    self.stdout = stdout
                    self.stderr = stderr

            if cmd == "claude auth status":
                return Completed('{"loggedIn": true, "subscriptionType": "pro"}')
            if cmd == "codex login status":
                return Completed("logged in")
            return Completed()

        for executor in ("codex", "claude", "gemini", "opencode", "pi"):
            with self.subTest(executor=executor):
                action = "execute"
                plan = Path("/repo/plans/phase-plan-v1-RUNNER.md")
                request = build_launch_request(
                    executor=executor,
                    action=action,
                    repo=Path("/repo"),
                    roadmap=Path("/repo/specs/phase-plans-v1.md"),
                    phase="RUNNER",
                    plan=plan,
                    model_selection=resolve_profile_for_executor(action=action, executor=executor),
                    prompt_bundle=build_prompt(
                        action,
                        Path("/repo/specs/phase-plans-v1.md"),
                        phase="RUNNER",
                        plan=plan,
                        harness_target=executor,
                    ),
                    json_output=False,
                    bypass_approvals=False,
                )
                with patch("phase_loop_runtime.launcher.subprocess.run", side_effect=fake_run):
                    preflight = run_auth_preflight(build_launch_spec(request))

                self.assertTrue(preflight.ok)
                serialized = json_like = str(preflight.metadata)
                self.assertNotIn("Usage: fake", serialized)
                self.assertNotIn("stdout_excerpt", serialized)
                self.assertNotIn("stderr_excerpt", serialized)
                self.assertIn("command_available", json_like)

    def test_opencode_auth_preflight_refuses_permissive_default_without_opt_in(self):
        selection = resolve_profile("execute")
        request = build_launch_request(
            executor="opencode",
            action="execute",
            repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
            model_selection=selection,
            prompt_bundle=build_prompt(
                "execute",
                Path("/repo/specs/phase-plans-v1.md"),
                phase="RUNNER",
                plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
                harness_target="opencode",
            ),
            json_output=False,
            bypass_approvals=False,
        )
        spec = build_launch_spec(request)
        permissive_spec = spec.__class__(**{**spec.__dict__, "permission_posture": "permissive"})

        def fake_run(cmd, shell, text, capture_output, check):
            class Completed:
                def __init__(self, returncode, stdout="", stderr=""):
                    self.returncode = returncode
                    self.stdout = stdout
                    self.stderr = stderr

            return Completed(0, "ok")

        with patch("phase_loop_runtime.launcher.subprocess.run", side_effect=fake_run):
            preflight = run_auth_preflight(permissive_spec)

        self.assertFalse(preflight.ok)
        self.assertEqual(preflight.blocker_class, "product_decision_missing")
        self.assertIn("permissive agent posture", preflight.blocker_summary)

    def test_gemini_executor_includes_user_configured_model(self):
        selection = resolve_profile("plan", model="gemini-3.1-pro-preview")
        request = build_launch_request(
            executor="gemini",
            action="plan",
            repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            plan=None,
            model_selection=selection,
            prompt_bundle=build_prompt("plan", Path("/repo/specs/phase-plans-v1.md"), phase="RUNNER", harness_target="gemini"),
            json_output=False,
            bypass_approvals=False,
        )
        spec = build_launch_spec(request)
        self.assertIn("--model", spec.command)
        self.assertIn("gemini-3.1-pro-preview", spec.command)

    def test_gemini_executor_includes_default_planning_model_routing_alias(self):
        selection = resolve_profile_for_executor(action="plan", executor="gemini")
        request = build_launch_request(
            executor="gemini",
            action="plan",
            repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            plan=None,
            model_selection=selection,
            prompt_bundle=build_prompt("plan", Path("/repo/specs/phase-plans-v1.md"), phase="RUNNER", harness_target="gemini"),
            json_output=False,
            bypass_approvals=False,
        )
        spec = build_launch_spec(request)
        self.assertEqual(spec.selected_model, "pro")
        self.assertIn("--model", spec.command)
        self.assertIn("pro", spec.command)

    def test_gemini_executor_includes_default_execution_model_routing_alias(self):
        selection = resolve_profile_for_executor(action="execute", executor="gemini")
        request = build_launch_request(
            executor="gemini",
            action="execute",
            repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
            model_selection=selection,
            prompt_bundle=build_prompt(
                "execute",
                Path("/repo/specs/phase-plans-v1.md"),
                phase="RUNNER",
                plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
                harness_target="gemini",
            ),
            json_output=False,
            bypass_approvals=False,
        )
        spec = build_launch_spec(request)
        self.assertEqual(spec.selected_model, "auto")
        self.assertIn("--model", spec.command)
        self.assertIn("auto", spec.command)

    def test_gemini_output_reduction_handles_json_and_stream_json(self):
        selection = resolve_profile("execute")
        request = build_launch_request(
            executor="gemini",
            action="execute",
            repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
            model_selection=selection,
            prompt_bundle=build_prompt(
                "execute",
                Path("/repo/specs/phase-plans-v1.md"),
                phase="RUNNER",
                plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
                harness_target="gemini",
            ),
            json_output=False,
            bypass_approvals=False,
        )
        spec = build_launch_spec(request)
        json_output = (
            'Warning: 256-color support not detected.\n'
            '{"session_id":"abc","response":"automation:\\n  status: complete\\n  next_skill: none\\n'
            '  next_command: none\\n  human_required: false\\n  blocker_class: none\\n'
            '  blocker_summary: none\\n  required_human_inputs: []\\n  verification_status: passed"}'
        )
        retry_error_then_response = (
            'Warning: 256-color support not detected.\n'
            '[{"error":{"code":429,"status":"RESOURCE_EXHAUSTED"}}]\n'
            '{"session_id":"abc","response":"automation:\\n  status: complete\\n  next_skill: none\\n'
            '  next_command: none\\n  human_required: false\\n  blocker_class: none\\n'
            '  blocker_summary: none\\n  required_human_inputs: []\\n  verification_status: passed"}'
        )
        stream_output = (
            '{"type":"message","role":"assistant","content":"automation:\\n  status:","delta":true}\n'
            '{"type":"message","role":"assistant","content":" complete\\n  next_skill: none\\n","delta":true}\n'
            '{"type":"message","role":"assistant","content":"  next_command: none\\n  human_required: false\\n  blocker_class: none\\n","delta":true}\n'
            '{"type":"message","role":"assistant","content":"  blocker_summary: none\\n  required_human_inputs: []\\n  verification_status: passed","delta":true}\n'
            '{"type":"result","status":"success"}\n'
        )
        self.assertIn("verification_status: passed", extract_executor_output_text(LaunchResult(command=["gemini"], returncode=0, output=json_output), spec))
        self.assertIn(
            "verification_status: passed",
            extract_executor_output_text(LaunchResult(command=["gemini"], returncode=0, output=retry_error_then_response), spec),
        )
        self.assertIn("next_skill: none", extract_executor_output_text(LaunchResult(command=["gemini"], returncode=0, output=stream_output), spec))

    def test_claude_output_reduction_handles_json_and_stream_json(self):
        selection = resolve_profile_for_executor(action="execute", executor="claude")
        request = build_launch_request(
            executor="claude",
            action="execute",
            repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
            model_selection=selection,
            prompt_bundle=build_prompt(
                "execute",
                Path("/repo/specs/phase-plans-v1.md"),
                phase="RUNNER",
                plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
                harness_target="claude",
            ),
            json_output=False,
            bypass_approvals=True,
        )
        spec = build_launch_spec(request)
        json_output = (
            '{"type":"result","result":"automation:\\n  status: complete\\n  next_skill: none\\n'
            '  next_command: none\\n  human_required: false\\n  blocker_class: none\\n'
            '  blocker_summary: none\\n  required_human_inputs: []\\n  verification_status: passed"}'
        )
        stream_output = (
            '{"type":"system","subtype":"init"}\n'
            '{"type":"assistant","message":{"content":[{"type":"text","text":"draft"}]}}\n'
            '{"type":"result","result":"automation:\\n  status: complete\\n  next_skill: none\\n'
            '  next_command: none\\n  human_required: false\\n  blocker_class: none\\n'
            '  blocker_summary: none\\n  required_human_inputs: []\\n  verification_status: passed"}\n'
        )
        self.assertIn("verification_status: passed", extract_executor_output_text(LaunchResult(command=["claude"], returncode=0, output=json_output), spec))
        self.assertIn("next_skill: none", extract_executor_output_text(LaunchResult(command=["claude"], returncode=0, output=stream_output), spec))

    def test_claude_legacy_plan_written_closeout_normalizes_to_planned(self):
        selection = resolve_profile_for_executor(action="plan", executor="claude")
        request = build_launch_request(
            executor="claude",
            action="plan",
            repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            plan=None,
            model_selection=selection,
            prompt_bundle=build_prompt(
                "plan",
                Path("/repo/specs/phase-plans-v1.md"),
                phase="RUNNER",
                harness_target="claude",
            ),
            json_output=False,
            bypass_approvals=True,
        )
        spec = build_launch_spec(request)
        output = (
            '{"type":"result","result":"automation:\\n  status: success\\n'
            '  next_skill: claude-execute-phase\\n'
            '  next_command: claude-execute-phase plans/phase-plan-v1-RUNNER.md\\n'
            '  human_required: false\\n  blocker_class: none\\n'
            '  blocker_summary: \\"\\"\\n  required_human_inputs: []\\n'
            '  verification_status: plan_written"}'
        )
        reduced = extract_executor_output_text(LaunchResult(command=["claude"], returncode=0, output=output), spec)
        self.assertIn("status: planned", reduced)
        self.assertIn("verification_status: not_run", reduced)

    def test_claude_output_reduction_treats_completed_roadmap_builder_handoff_as_complete(self):
        selection = resolve_profile_for_executor(action="execute", executor="claude")
        request = build_launch_request(
            executor="claude",
            action="execute",
            repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
            model_selection=selection,
            prompt_bundle=build_prompt(
                "execute",
                Path("/repo/specs/phase-plans-v1.md"),
                phase="RUNNER",
                plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
                harness_target="claude",
            ),
            json_output=False,
            bypass_approvals=True,
        )
        spec = build_launch_spec(request)
        output = (
            '{"type":"result","result":"automation:\\n  status: completed\\n'
            '  next_skill: claude-phase-roadmap-builder\\n'
            '  next_command: claude-phase-roadmap-builder specs/phase-plans-v1.md\\n'
            '  human_required: false\\n  blocker_class: none\\n'
            '  blocker_summary: none\\n  required_human_inputs: []\\n'
            '  verification_status: pass"}'
        )
        reduced = extract_executor_output_text(LaunchResult(command=["claude"], returncode=0, output=output), spec)
        self.assertIn("status: complete", reduced)
        self.assertIn("verification_status: passed", reduced)

    def test_opencode_output_reduction_handles_json_and_plain_default_output(self):
        selection = resolve_profile_for_executor(action="execute", executor="opencode")
        request = build_launch_request(
            executor="opencode",
            action="execute",
            repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
            model_selection=selection,
            prompt_bundle=build_prompt(
                "execute",
                Path("/repo/specs/phase-plans-v1.md"),
                phase="RUNNER",
                plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
                harness_target="opencode",
            ),
            json_output=False,
            bypass_approvals=True,
        )
        spec = build_launch_spec(request)
        json_output = (
            '{"role":"assistant","content":"automation:\\n  status: complete\\n  next_skill: none\\n'
            '  next_command: none\\n  human_required: false\\n  blocker_class: none\\n'
            '  blocker_summary: none\\n  required_human_inputs: []\\n  verification_status: passed"}\n'
        )
        default_output = (
            "status update\n\n"
            "automation:\n"
            "  status: complete\n"
            "  next_skill: none\n"
            "  next_command: none\n"
            "  human_required: false\n"
            "  blocker_class: none\n"
            "  blocker_summary: none\n"
            "  required_human_inputs: []\n"
            "  verification_status: passed\n"
        )
        self.assertIn("verification_status: passed", extract_executor_output_text(LaunchResult(command=["opencode"], returncode=0, output=json_output), spec))
        self.assertIn("next_skill: none", extract_executor_output_text(LaunchResult(command=["opencode"], returncode=0, output=default_output), spec))


if __name__ == "__main__":
    unittest.main()
