import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.events import append_event, read_events
from phase_loop_runtime.launcher import LaunchResult
from phase_loop_runtime.models import LoopEvent, utc_now
from phase_loop_runtime.provenance import event_provenance
from phase_loop_runtime.runner import run_loop
from phase_loop_test_utils import build_fake_automation_output, commit_fixture_paths, make_repo, write_phase_plan

import pytest

# TESTDECOUPLE SL-1 (overlay-dependent): builds a skill/adoption bundle or runs the
# runtime execute path, which resolves the dotfiles skill-source / profile overlay
# (claude-config/*, codex-config/* …) absent standalone. Run-time integration: the
# conftest hook skips it when no dotfiles tree is reachable.
pytestmark = pytest.mark.dotfiles_integration


class PhaseLoopRepairSkippedWhenBlockerClearedTest(unittest.TestCase):
    def test_phase_loop_repair_skipped_when_blocker_cleared(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("README.md",))
            commit_fixture_paths(repo, "add contract plan", plan)
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="CONTRACT",
                    action="execute",
                    status="blocked",
                    model="gpt-5.6-terra",
                    reasoning_effort="medium",
                    source="fixture",
                    blocker={
                        "human_required": False,
                        "blocker_class": "dirty_worktree_conflict",
                        "blocker_summary": "Historical dirty output required repair.",
                        "required_human_inputs": (),
                        "access_attempts": (),
                    },
                    metadata={
                        "terminal_summary": {
                            "terminal_status": "blocked",
                            "verification_status": "blocked",
                            "next_action": "Repair stale dirty output.",
                            "dirty_paths": ["README.md"],
                            "phase_owned_dirty": False,
                            "phase_owned_dirty_paths": [],
                            "unowned_dirty_paths": ["README.md"],
                            "pre_existing_dirty_paths": [],
                        }
                    },
                    **event_provenance(roadmap, "CONTRACT"),
                ),
            )
            launched_actions: list[str] = []

            def fake_launch(spec, **_kwargs):
                launched_actions.append(spec.prompt_bundle.product_action)
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
                snapshot, results = run_loop(repo, roadmap, phase="CONTRACT", max_phases=1, executor="codex")

            self.assertEqual(len(results), 1)
            self.assertEqual(launched_actions, ["execute"])
            self.assertNotEqual(launched_actions, ["repair"])
            self.assertEqual(snapshot.phases["CONTRACT"], "executed")
            transitions = [
                event
                for event in read_events(repo)
                if event.get("action") == "state_transition"
                and event.get("metadata", {}).get("state_transition", {}).get("reason")
                == "repair_precondition_cleared"
            ]
            self.assertEqual(len(transitions), 1)
            transition = transitions[0]["metadata"]["state_transition"]
            self.assertEqual(transition["from"], "blocked")
            self.assertEqual(transition["to"], "planned")
            self.assertEqual(transition["trigger"], "live_dirty_worktree_check")


if __name__ == "__main__":
    unittest.main()
