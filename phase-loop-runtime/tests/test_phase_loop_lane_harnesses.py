import json
import hashlib
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.launcher import build_launch_request, build_launch_spec
from phase_loop_runtime.models import CommandAdapterConfig, HarnessLaneAssignment, LaneWorktreeAssignment
from phase_loop_runtime.observability import run_artifacts
from phase_loop_runtime.profiles import resolve_profile, resolve_profile_for_executor
from phase_loop_runtime.prompts import build_prompt
from phase_loop_runtime.runner import launch_harness_lane_work_unit
from phase_loop_test_utils import make_repo, write_phase_plan
from test_phase_loop_pipeline_bundle import _write_bundle, _write_protected_source

import pytest

# TESTDECOUPLE SL-1 (overlay-dependent): builds a skill/adoption bundle or runs the
# runtime execute path, which resolves the dotfiles skill-source / profile overlay
# (claude-config/*, codex-config/* …) absent standalone. Run-time integration: the
# conftest hook skips it when no dotfiles tree is reachable.
pytestmark = pytest.mark.dotfiles_integration


class PhaseLoopLaneHarnessesTest(unittest.TestCase):
    def test_dfparsoak_route_matrix_covers_pi_claude_codex_and_gemini(self):
        routes = {
            "pi": "pi-agent-watch",
            "claude": "claude",
            "codex": "codex",
            "gemini": "agy",  # v46 EXEC: the gemini executor drives the agy CLI
        }
        for executor, command_token in routes.items():
            with self.subTest(executor=executor):
                with tempfile.TemporaryDirectory() as td:
                    repo = make_repo(Path(td))
                    roadmap = repo / "specs" / "phase-plans-v1.md"
                    plan = write_phase_plan(repo, "DFPARSOAK", roadmap)
                    selection = resolve_profile_for_executor(action="execute", executor=executor)
                    fallback_reason = "preferred-default" if executor == "pi" else "explicit-route-proof"
                    assignment = HarnessLaneAssignment(
                        phase="DFPARSOAK",
                        lane_id=f"SL-{len(executor)}",
                        work_unit_kind="lane_execute",
                        prompt_kind="implementation",
                        owned_files=(f"vendor/phase-loop-runtime/tests/fixtures/phase_loop_soak/{executor}-route.json",),
                        consumed_interfaces=("DFPARSOAK-soak-input-contract",),
                        execution_policy={
                            "executor": executor,
                            "model": selection.model,
                            "effort": selection.effort,
                            "fallback_reason": fallback_reason,
                        },
                    )
                    bundle = build_prompt(
                        "execute",
                        roadmap,
                        phase="DFPARSOAK",
                        plan=plan,
                        harness_target=executor,
                        harness_lane_assignment=assignment,
                    )
                    request = build_launch_request(
                        executor=executor,
                        action="execute",
                        repo=repo,
                        roadmap=roadmap,
                        phase="DFPARSOAK",
                        plan=plan,
                        model_selection=selection,
                        prompt_bundle=bundle,
                        harness_lane_assignment=assignment,
                        json_output=False,
                        bypass_approvals=False,
                    )

                    spec = build_launch_spec(request)
                    artifacts = run_artifacts(repo, "DFPARSOAK", "execute", 1, spec)
                    metadata = json.loads(artifacts["metadata"].read_text(encoding="utf-8"))

                    self.assertEqual(spec.executor, executor)
                    self.assertIn(command_token, spec.command[0])
                    self.assertEqual(metadata["harness_lane_assignment"]["execution_policy"]["fallback_reason"], fallback_reason)
                    self.assertEqual(metadata["harness_lane_assignment"]["owned_files"], [f"vendor/phase-loop-runtime/tests/fixtures/phase_loop_soak/{executor}-route.json"])
                    self.assertEqual(metadata["selected_model"], selection.model)
                    self.assertNotIn("API_KEY", json.dumps(metadata))
                    self.assertNotIn("provider_payload", json.dumps(metadata))

    def test_fake_harness_matrix_receives_lane_assignment_and_closeout_schema(self):
        assignment = HarnessLaneAssignment(
            phase="HARNESSLANE",
            lane_id="SL-4",
            work_unit_kind="lane_execute",
            prompt_kind="implementation",
            owned_files=("runner.py",),
            consumed_interfaces=("LaunchSpec.harness_lane_assignment", "WorkUnitCloseout"),
            execution_policy={"work_unit_kind": "lane_execute", "effort": "high"},
        )

        for executor in ("codex", "claude", "gemini", "opencode", "command"):
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
                    launch = json.loads(artifacts["metadata"].read_text(encoding="utf-8"))

                    self.assertEqual(spec.harness_lane_assignment.lane_id, "SL-4")
                    self.assertEqual(launch["harness_lane_assignment"]["lane_id"], "SL-4")
                    self.assertEqual(launch["harness_lane_assignment"]["owned_files"], ["runner.py"])
                    self.assertIn("automation.status", launch["harness_lane_assignment"]["closeout_schema_required"])
                    self.assertIn("lane_id: `SL-4`", bundle.render_context())

    def test_dfparsoak_harness_assignment_records_route_policy_and_worktree_identity(self):
        assignment = HarnessLaneAssignment(
            phase="DFPARSOAK",
            lane_id="SL-2",
            work_unit_kind="lane_execute",
            prompt_kind="implementation",
            wave_id="wave-dfparsoak-001",
            owned_files=("vendor/phase-loop-runtime/src/phase_loop_runtime/lane_scheduler.py",),
            consumed_interfaces=("docs/phase-loop/dfpromptsync-contract-map.md",),
            execution_policy={"execution_policy_source": "phase-plan", "work_unit_kind": "lane_execute"},
            worktree_assignment=LaneWorktreeAssignment(
                lane_id="SL-2",
                worktree_path="<WORKTREE-PATH-REDACTED>",
                isolation_mode="git_worktree",
                base_sha="b" * 40,
            ),
            harness_route="codex",
            model="gpt-5.5",
            effort="high",
            fallback_reason="codex_cli_fallback",
            metadata={"evidence_refs": [{"path": ".phase-loop/runs/dfparsoak/terminal-summary.json", "sha256": "a" * 64}]},
        )

        data = assignment.to_json()

        self.assertEqual(data["wave_id"], "wave-dfparsoak-001")
        self.assertEqual(data["worktree_assignment"]["isolation_mode"], "git_worktree")
        self.assertEqual(data["worktree_assignment"]["base_sha"], "b" * 40)
        self.assertEqual(data["harness_route"], "codex")
        self.assertEqual(data["model"], "gpt-5.5")
        self.assertEqual(data["effort"], "high")
        self.assertEqual(data["fallback_reason"], "codex_cli_fallback")

    def test_pipeline_source_bundle_metadata_reaches_harness_lane_assignment(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            source = _write_protected_source(repo)
            bundle = _write_bundle(repo, protected_sha=hashlib.sha256(source.read_bytes()).hexdigest())
            plan = write_phase_plan(
                repo,
                "RUNNER",
                roadmap,
                extra_frontmatter={
                    "source_bundle": str(bundle.resolve().relative_to(repo.resolve())),
                    "source_bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
                    "pipeline_phase_id": "pipeline.phase.runner",
                    "pipeline_mode": "pipeline_required",
                },
                owned_files=("plans/phase-plan-v1-RUNNER.md",),
            )
            assignment = HarnessLaneAssignment(
                phase="RUNNER",
                lane_id="SL-0",
                work_unit_kind="lane_execute",
                owned_files=("plans/phase-plan-v1-RUNNER.md",),
            )

            result = launch_harness_lane_work_unit(
                repo=repo,
                roadmap=roadmap,
                plan=plan,
                assignment=assignment,
                dry_run=True,
            )
            metadata = json.loads(Path(result["artifacts"]["metadata"]).read_text(encoding="utf-8"))

            self.assertEqual(result["terminal_summary"]["verification_status"], "passed")
            self.assertIn("pipeline_source_bundle", metadata["harness_lane_assignment"]["metadata"])
            self.assertEqual(
                metadata["harness_lane_assignment"]["metadata"]["pipeline_source_bundle"]["path"],
                ".pipeline/artifacts/phase-source-bundle.json",
            )


if __name__ == "__main__":
    unittest.main()
