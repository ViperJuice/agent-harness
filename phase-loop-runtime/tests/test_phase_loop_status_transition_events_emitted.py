import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.events import append_event, read_events
from phase_loop_runtime.launcher import LaunchResult
from phase_loop_runtime.runner import run_loop
from phase_loop_test_utils import (
    build_fake_automation_output,
    commit_fixture_paths,
    make_repo,
    provenanced_event,
    write_phase_plan,
)

import pytest

# TESTDECOUPLE SL-1 (overlay-dependent): builds a skill/adoption bundle or runs the
# runtime execute path, which resolves the dotfiles skill-source / profile overlay
# (claude-config/*, codex-config/* …) absent standalone. Run-time integration: the
# conftest hook skips it when no dotfiles tree is reachable.
pytestmark = pytest.mark.dotfiles_integration


class PhaseLoopStatusTransitionEventsEmittedTest(unittest.TestCase):
    def test_phase_loop_status_transition_events_emitted(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "CONTRACT", roadmap)
            commit_fixture_paths(repo, "add contract plan", plan)
            append_event(repo, provenanced_event(repo, roadmap, "CONTRACT", "planned", action="plan"))

            def fake_launch(spec, **_kwargs):
                return LaunchResult(
                    command=spec.command,
                    returncode=0,
                    output=build_fake_automation_output(
                        status="executed",
                        verification_status="passed",
                        artifact=str(plan),
                        artifact_state="tracked",
                    ),
                    executor=spec.executor,
                )

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                snapshot, _results = run_loop(repo, roadmap, phase="CONTRACT", max_phases=1, executor="codex")

            self.assertEqual(snapshot.phases["CONTRACT"], "executed")
            transitions = [
                event["metadata"]["state_transition"]
                for event in read_events(repo)
                if event.get("action") == "state_transition"
            ]
            self.assertTrue(transitions)
            self.assertIn(
                {
                    "from": "planned",
                    "to": "executed",
                    "reason": "launch_result_reduction",
                    "trigger": "execute",
                },
                transitions,
            )
            for transition in transitions:
                self.assertTrue(transition["from"])
                self.assertTrue(transition["to"])
                self.assertTrue(transition["reason"])
                self.assertTrue(transition["trigger"])


if __name__ == "__main__":
    unittest.main()
