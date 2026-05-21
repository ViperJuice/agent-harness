from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.capability_registry import DEFAULT_CAPABILITY_REGISTRY
from phase_loop_runtime.events import read_events
from phase_loop_runtime.launcher import LaunchResult
from phase_loop_runtime.models import DelegationBudget, DelegationRequest, ParentChildRunMetadata
from phase_loop_runtime.prompts import build_prompt
from phase_loop_runtime.runner import launch_delegated_child, run_loop
from phase_loop_runtime.state_degradation import record_degradation
from phase_loop_test_utils import make_repo, write_phase_plan


AUTOMATION_OUTPUT = (
    "automation:\n"
    "  status: executed\n"
    "  next_skill: none\n"
    "  next_command: none\n"
    "  next_model_hint: none\n"
    "  next_effort_hint: none\n"
    "  human_required: false\n"
    "  blocker_class: none\n"
    "  blocker_summary: none\n"
    "  required_human_inputs: []\n"
    "  verification_status: passed\n"
    "  artifact: none\n"
    "  artifact_state: none\n"
)


class PhaseLoopMixedRunDispatchTest(unittest.TestCase):
    def test_session_degraded_preferred_executor_falls_back_to_live_executor(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.write_text(
                roadmap.read_text(encoding="utf-8")
                + "\n## Dispatch Hints\n"
                + "- execute preferred executors: `claude`\n"
                + "- execute fallback executors: `gemini`\n",
                encoding="utf-8",
            )
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            subprocess.run(["git", "add", str(plan.relative_to(repo)), str(roadmap.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "fixture degraded fallback"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            record_degradation(repo, "claude", "account_or_billing_setup", "RUNNER", "Claude auth missing", 300)

            def fake_launch(spec, dry_run=False, log_path=None, heartbeat_path=None, **kwargs):
                return LaunchResult(
                    command=["gemini", "-p"],
                    returncode=0,
                    output=AUTOMATION_OUTPUT,
                    executor="gemini",
                    log_path=str(log_path) if log_path else None,
                    heartbeat_path=str(heartbeat_path) if heartbeat_path else None,
                )

            with patch("phase_loop_runtime.runner.run_auth_preflight", return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})()), patch(
                "phase_loop_runtime.runner.launch_with_spec",
                side_effect=fake_launch,
            ):
                _snapshot, results = run_loop(repo, roadmap, phase="RUNNER")

            self.assertEqual(results[0].executor, "gemini")

    def test_all_session_degraded_candidates_block_dispatch(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.write_text(
                roadmap.read_text(encoding="utf-8")
                + "\n## Dispatch Hints\n"
                + "- execute preferred executors: `claude`\n"
                + "- execute allowed executors: `claude`\n",
                encoding="utf-8",
            )
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            subprocess.run(["git", "add", str(plan.relative_to(repo)), str(roadmap.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "fixture all degraded"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            record_degradation(repo, "claude", "account_or_billing_setup", "RUNNER", "Claude auth missing", 300)

            with patch("phase_loop_runtime.runner.launch_with_spec") as fake_launch:
                _snapshot, _results = run_loop(repo, roadmap, phase="RUNNER")

            event = read_events(repo)[-1]
            self.assertEqual(event["status"], "blocked")
            self.assertEqual(event["metadata"]["dispatch_decision"]["blocked_reason"], "all_candidates_session_degraded")
            fake_launch.assert_not_called()

    def test_proof_gated_preferred_executor_falls_back_to_live_executor(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.write_text(
                roadmap.read_text(encoding="utf-8")
                + "\n## Dispatch Hints\n"
                + "- execute preferred executors: `claude`\n"
                + "- execute fallback executors: `gemini`\n",
                encoding="utf-8",
            )
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            subprocess.run(["git", "add", str(plan.relative_to(repo)), str(roadmap.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "fixture proof gated fallback"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

            patched_registry = dict(DEFAULT_CAPABILITY_REGISTRY)
            patched_registry["claude"] = replace(
                DEFAULT_CAPABILITY_REGISTRY["claude"],
                live_available=False,
                promotion_status="proof_gated",
                live_proof_gate="disposable_proof_required",
            )

            def fake_launch(spec, dry_run=False, log_path=None, heartbeat_path=None, **kwargs):
                return LaunchResult(
                    command=["gemini", "-p"],
                    returncode=0,
                    output=AUTOMATION_OUTPUT,
                    executor="gemini",
                    log_path=str(log_path) if log_path else None,
                    heartbeat_path=str(heartbeat_path) if heartbeat_path else None,
                )

            with patch("phase_loop_runtime.capability_registry.DEFAULT_CAPABILITY_REGISTRY", patched_registry), patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
            ), patch(
                "phase_loop_runtime.runner.launch_with_spec",
                side_effect=fake_launch,
            ):
                _snapshot, results = run_loop(repo, roadmap, phase="RUNNER")

            self.assertEqual(results[0].executor, "gemini")

    def test_fallback_selection_persists_in_launch_metadata_and_events(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.write_text(
                roadmap.read_text(encoding="utf-8")
                + "\n## Dispatch Hints\n"
                + "- execute preferred executors: `claude`\n"
                + "- execute fallback executors: `gemini`\n",
                encoding="utf-8",
            )
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            subprocess.run(["git", "add", str(plan.relative_to(repo)), str(roadmap.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "fixture mixedrun fallback"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            patched_registry = dict(DEFAULT_CAPABILITY_REGISTRY)
            patched_registry["claude"] = replace(DEFAULT_CAPABILITY_REGISTRY["claude"], live_available=False)

            def fake_launch(spec, dry_run=False, log_path=None, heartbeat_path=None, **kwargs):
                return LaunchResult(
                    command=["gemini", "-p"],
                    returncode=0,
                    output=AUTOMATION_OUTPUT,
                    executor="gemini",
                    log_path=str(log_path) if log_path else None,
                    heartbeat_path=str(heartbeat_path) if heartbeat_path else None,
                )

            with patch("phase_loop_runtime.capability_registry.DEFAULT_CAPABILITY_REGISTRY", patched_registry), patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
            ), patch(
                "phase_loop_runtime.runner.launch_with_spec",
                side_effect=fake_launch,
            ) as fake_launch:
                _snapshot, results = run_loop(repo, roadmap, phase="RUNNER")

            self.assertEqual(results[0].executor, "gemini")
            self.assertEqual(fake_launch.call_args.args[0].dispatch_decision.selected_executor, "gemini")
            artifacts = read_events(repo)[-1]["metadata"]["artifacts"]
            launch_metadata = json.loads(Path(artifacts["metadata"]).read_text(encoding="utf-8"))
            self.assertEqual(launch_metadata["dispatch_decision"]["selected_executor"], "gemini")
            self.assertEqual(launch_metadata["dispatch_decision"]["selected_via"], "fallback")
            self.assertEqual(launch_metadata["dispatch_decision"]["considered_executors"], ["claude", "gemini"])

    def test_execution_policy_persists_in_launch_metadata_and_events(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.write_text(
                roadmap.read_text(encoding="utf-8")
                + "\n## Execution Policy\n"
                + "- execute: executor=`codex`, model=`gpt-5.5`, effort=`high`, work-unit=`lane_execute`, reason=`policy test`\n",
                encoding="utf-8",
            )
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            subprocess.run(["git", "add", str(plan.relative_to(repo)), str(roadmap.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "fixture execution policy metadata"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

            def fake_launch(spec, dry_run=False, log_path=None, heartbeat_path=None, **kwargs):
                return LaunchResult(
                    command=["codex", "exec"],
                    returncode=0,
                    output=AUTOMATION_OUTPUT,
                    executor="codex",
                    log_path=str(log_path) if log_path else None,
                    heartbeat_path=str(heartbeat_path) if heartbeat_path else None,
                )

            with patch("phase_loop_runtime.runner.run_auth_preflight", return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})()), patch(
                "phase_loop_runtime.runner.launch_with_spec",
                side_effect=fake_launch,
            ):
                _snapshot, _results = run_loop(repo, roadmap, phase="RUNNER")

            event_metadata = read_events(repo)[-1]["metadata"]
            artifacts = event_metadata["artifacts"]
            launch_metadata = json.loads(Path(artifacts["metadata"]).read_text(encoding="utf-8"))
            self.assertEqual(launch_metadata["execution_policy"]["effort"], "high")
            self.assertEqual(launch_metadata["execution_policy"]["execution_policy_source"], "roadmap policy")
            self.assertEqual(event_metadata["execution_policy"]["model"], "gpt-5.5")

    def test_unsupported_execution_policy_blocks_before_launch(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.write_text(
                roadmap.read_text(encoding="utf-8")
                + "\n## Execution Policy\n"
                + "- execute: executor=`gemini`, model=`phase-loop-unknown`, effort=`medium`, work-unit=`lane_execute`\n",
                encoding="utf-8",
            )
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            subprocess.run(["git", "add", str(plan.relative_to(repo)), str(roadmap.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "fixture blocked execution policy"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

            with patch("phase_loop_runtime.runner.launch_with_spec") as fake_launch:
                _snapshot, _results = run_loop(repo, roadmap, phase="RUNNER")

            event = read_events(repo)[-1]
            self.assertEqual(event["status"], "blocked")
            self.assertIn("Execution policy failed closed", event["blocker"]["blocker_summary"])
            fake_launch.assert_not_called()

    def test_delegated_child_launch_keeps_dispatch_decision_and_cross_executor_lineage(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "MIXEDRUN", roadmap, owned_files=("notes.md",))
            request = DelegationRequest(
                request_id="req-cross-executor",
                product_action="review",
                target_executor="claude",
                reason="Route review through the live Claude executor.",
                owned_files=("notes.md",),
                expected_output="Review findings",
                budget=DelegationBudget(max_seconds=60, notes="metadata only"),
            )

            outcome = launch_delegated_child(
                repo=repo,
                roadmap=roadmap,
                parent_phase="MIXEDRUN",
                parent_action="execute",
                parent_executor="codex",
                plan=plan,
                request=request,
                parent_run_id="run-parent-3",
                dry_run=True,
            )

            self.assertEqual(outcome["decision"]["status"], "approved")
            self.assertEqual(outcome["launch_metadata"]["dispatch_decision"]["selected_executor"], "claude")
        self.assertEqual(outcome["launch_metadata"]["dispatch_decision"]["selected_via"], "preferred")
        self.assertEqual(outcome["launch_metadata"]["parent_child"]["parent_executor"], "codex")
        self.assertEqual(outcome["launch_metadata"]["parent_child"]["child_executor"], "claude")
        self.assertEqual(outcome["launch_metadata"]["parent_child"]["child_worktree_root"], str(repo.resolve()))

    def test_delegation_prompt_context_names_parent_and_child_executor(self):
        metadata = ParentChildRunMetadata(
            parent_phase="MIXEDRUN",
            parent_action="execute",
            parent_executor="codex",
            parent_run_id="run-parent-4",
            child_action="review",
            child_executor="claude",
            request_id="req-context",
            child_worktree_root="/repo",
        )
        request = DelegationRequest(
            request_id="req-context",
            product_action="review",
            target_executor="claude",
            reason="Cross-executor review",
            owned_files=("notes.md",),
            expected_output="Review findings",
            budget=DelegationBudget(max_seconds=30),
        )

        bundle = build_prompt(
            "review",
            roadmap=ROOT / "specs" / "phase-plans-v4.md",
            phase="MIXEDRUN",
            plan=ROOT / "plans" / "phase-plan-v4-MIXEDRUN.md",
            harness_target="claude",
            delegation_request=request,
            parent_child_metadata=metadata,
        )

        self.assertIn("parent executor: `codex`", bundle.render_context())
        self.assertIn("resolved child executor: `claude`", bundle.render_context())
        self.assertIn("child worktree root: `/repo`", bundle.render_context())


if __name__ == "__main__":
    unittest.main()
