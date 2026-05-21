import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.launcher import LaunchResult, build_launch_request, build_launch_spec, extract_executor_output_text
from phase_loop_runtime.profiles import resolve_profile_for_executor
from phase_loop_runtime.prompts import build_prompt
from phase_loop_runtime.runner import run_loop
from phase_loop_test_utils import make_repo, write_phase_plan


class PhaseLoopPiExecutorTest(unittest.TestCase):
    def test_pi_fake_fixtures_cover_required_matrix(self):
        fixture_root = Path(__file__).resolve().parent / "fixtures" / "phase_loop_pi"
        for name in (
            "success.json",
            "blocked.json",
            "malformed-output.txt",
            "redaction-rejection.txt",
            "unsupported-tool-policy.json",
        ):
            self.assertTrue((fixture_root / name).exists(), name)
        self.assertIn("automation:", (fixture_root / "success.json").read_text(encoding="utf-8"))
        self.assertIn("api_key=should-not-survive", (fixture_root / "redaction-rejection.txt").read_text(encoding="utf-8"))

    def test_pi_fake_success_reduces_to_shared_closeout(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(["git", "commit", "-m", "add pi runner plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

            output = (
                '{"result":"automation:\\n  status: complete\\n  next_skill: none\\n'
                '  next_command: none\\n  next_model_hint: none\\n  next_effort_hint: none\\n'
                '  human_required: false\\n  blocker_class: none\\n  blocker_summary: none\\n'
                '  required_human_inputs: []\\n  verification_status: passed\\n'
                f'  artifact: {plan}\\n  artifact_state: tracked\\n"}}'
            )

            with patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type("Preflight", (), {"ok": True, "metadata": {"executor": "pi", "probes": []}})(),
            ), patch(
                "phase_loop_runtime.runner.launch_with_spec",
                return_value=LaunchResult(command=["pi-agent-watch"], returncode=0, output=output, executor="pi"),
            ):
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER", executor="pi")

            self.assertEqual(len(results), 1)
            self.assertIn(snapshot.phases["RUNNER"], {"planned", "complete"})

    def test_pi_output_reduction_handles_result_and_message_payloads(self):
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
        for payload in (
            {"result": "automation:\n  status: complete\n  verification_status: passed"},
            {"message": {"content": "automation:\n  status: blocked\n  verification_status: blocked"}},
        ):
            text = extract_executor_output_text(
                LaunchResult(command=["pi-agent-watch"], returncode=0, output=json.dumps(payload), executor="pi"),
                spec,
            )
            self.assertIn("automation:", text)

    def test_pi_fake_redaction_rejection_and_unsupported_policy_block(self):
        secret_shaped = "automation:\n  status: complete\napi_key=should-not-survive\n"
        self.assertRegex(secret_shaped, r"api[_-]?key")

        with self.assertRaisesRegex(ValueError, "unsupported effort"):
            from phase_loop_runtime.models import ExecutionPolicyRule, ModelSelection
            from phase_loop_runtime.profiles import resolve_execution_policy

            resolve_execution_policy(
                action="execute",
                executor="pi",
                model_selection=ModelSelection(profile="execute", model="auto", effort="medium"),
                plan_policy=ExecutionPolicyRule(
                    selector="execute",
                    action="execute",
                    executor="pi",
                    model="auto",
                    effort="xhigh",
                    work_unit_kind="lane_execute",
                ),
            )


if __name__ == "__main__":
    unittest.main()
