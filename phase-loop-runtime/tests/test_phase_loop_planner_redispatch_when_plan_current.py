import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.events import append_event, read_events
from phase_loop_runtime.launcher import LaunchResult
from phase_loop_runtime.runner import run_loop
from phase_loop_test_utils import build_fake_automation_output, commit_fixture_paths, make_repo, provenanced_event, write_phase_plan


PLANNER_COMMANDS = ("codex-plan-phase", "claude-plan-phase", "gemini-plan-phase", "opencode-plan-phase")


class PhaseLoopPlannerRedispatchWhenPlanCurrentTest(unittest.TestCase):
    def test_phase_loop_planner_redispatch_when_plan_current(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            commit_fixture_paths(repo, "add runner plan", plan)
            append_event(repo, provenanced_event(repo, roadmap, "RUNNER", "planned", action="plan"))
            launched = []

            def fake_launch(spec, **_kwargs):
                launched.append(spec.prompt_bundle)
                output = build_fake_automation_output(
                    status="blocked",
                    human_required=False,
                    blocker_class="repeated_verification_failure",
                    blocker_summary="Fake executor blocker after dispatch choice.",
                    verification_status="blocked",
                    artifact=str(plan),
                    artifact_state="tracked",
                )
                return LaunchResult(command=spec.command, returncode=0, output=output, executor=spec.executor)

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                run_loop(repo, roadmap, phase="RUNNER", max_phases=1, dry_run=False, observe=False, force_replan=False)

            self.assertEqual([bundle.product_action for bundle in launched], ["execute"])
            workflow_commands = [bundle.workflow_command for bundle in launched]
            self.assertFalse(any(command in workflow for command in PLANNER_COMMANDS for workflow in workflow_commands))

            events = read_events(repo)
            skipped = [event for event in events if event["status"] == "plan_skipped"]
            self.assertEqual(len(skipped), 1)
            self.assertEqual(skipped[0]["metadata"]["plan_doc_skip"]["reason"], "plan_doc_current")
            self.assertEqual(skipped[0]["metadata"]["plan_doc_skip"]["plan_artifact"], "plans/phase-plan-v1-RUNNER.md")
            self.assertFalse(skipped[0]["metadata"]["plan_doc_skip"]["forced_replan"])


if __name__ == "__main__":
    unittest.main()
