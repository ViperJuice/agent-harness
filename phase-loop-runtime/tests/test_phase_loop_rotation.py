import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.events import read_events
from phase_loop_runtime.launcher import LaunchResult
from phase_loop_runtime.runner import RotationState, run_loop, status_snapshot
from phase_loop_runtime.state import load_work_unit_state, write_work_unit_state
from phase_loop_runtime.state_degradation import record_degradation
from phase_loop_test_utils import commit_fixture_paths, make_repo, write_named_roadmap, write_phase_plan


AUTOMATION_COMPLETE = (
    "automation:\n"
    "  status: complete\n"
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

import pytest

# TESTDECOUPLE SL-1 (overlay-dependent): builds a skill/adoption bundle or runs the
# runtime execute path, which resolves the dotfiles skill-source / profile overlay
# (claude-config/*, codex-config/* …) absent standalone. Run-time integration: the
# conftest hook skips it when no dotfiles tree is reachable.
pytestmark = pytest.mark.dotfiles_integration


def _complete_launch(spec, dry_run=False, log_path=None, heartbeat_path=None, **kwargs):
    return LaunchResult(
        command=spec.command,
        returncode=0,
        output=AUTOMATION_COMPLETE,
        executor=spec.executor,
        log_path=str(log_path) if log_path else None,
        heartbeat_path=str(heartbeat_path) if heartbeat_path else None,
    )


def _preflight_ok():
    return type("Preflight", (), {"ok": True, "metadata": {"probes": []}})()


def _executor_events(repo):
    return [event for event in read_events(repo) if event.get("selected_executor")]


class PhaseLoopRotationTest(unittest.TestCase):
    def test_rotation_state_normalizes_deduplicates_and_rejects_invalid_lists(self):
        state = RotationState.from_csv(" codex, claude, codex ", mode="phase", on_policy_pin="skip")
        self.assertEqual(state.executors, ("codex", "claude"))
        with self.assertRaises(ValueError):
            RotationState.from_csv("codex,bogus", mode="phase", on_policy_pin="skip")
        with self.assertRaises(ValueError):
            RotationState.from_csv(" , ", mode="phase", on_policy_pin="skip")

    def test_phase_mode_rotates_selected_executor_history_and_metrics(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = write_named_roadmap(repo, (("ALPHA", "Alpha"), ("BETA", "Beta"), ("GAMMA", "Gamma")))
            plans = tuple(write_phase_plan(repo, phase, roadmap) for phase in ("ALPHA", "BETA", "GAMMA"))
            commit_fixture_paths(repo, "rotation fixture", roadmap, *plans)

            with patch("phase_loop_runtime.runner.run_auth_preflight", return_value=_preflight_ok()), patch(
                "phase_loop_runtime.runner.launch_with_spec", side_effect=_complete_launch
            ):
                snapshot, results = run_loop(
                    repo,
                    roadmap,
                    max_phases=3,
                    rotate_executors="codex,claude,gemini",
                    rotation_mode="phase",
                )

            self.assertEqual([result.executor for result in results], ["codex", "claude", "gemini"])
            self.assertEqual([event["selected_executor"] for event in _executor_events(repo)], ["codex", "claude", "gemini"])
            status = status_snapshot(repo, roadmap)
            self.assertEqual(status.metrics_summary["by_executor"]["codex"], 1)
            self.assertEqual(status.metrics_summary["by_executor"]["claude"], 1)
            self.assertEqual(status.metrics_summary["by_executor"]["gemini"], 1)

    def test_policy_pin_skip_consumes_rotation_turn_and_fallback_next_preserves_it(self):
        for pin_mode, expected in (("skip", ["codex", "codex", "gemini"]), ("fallback-next", ["codex", "codex", "claude"])):
            with self.subTest(pin_mode=pin_mode), tempfile.TemporaryDirectory() as td:
                repo = make_repo(Path(td))
                roadmap = write_named_roadmap(repo, (("ALPHA", "Alpha"), ("BETA", "Beta"), ("GAMMA", "Gamma")))
                plans = [
                    write_phase_plan(repo, "ALPHA", roadmap),
                    write_phase_plan(
                        repo,
                        "BETA",
                        roadmap,
                        body="# BETA\n\n## Execution Policy\n\n- execute: executor=`codex`, effort=`medium`\n",
                    ),
                    write_phase_plan(repo, "GAMMA", roadmap),
                ]
                commit_fixture_paths(repo, f"rotation pin {pin_mode}", roadmap, *plans)

                with patch("phase_loop_runtime.runner.run_auth_preflight", return_value=_preflight_ok()), patch(
                    "phase_loop_runtime.runner.launch_with_spec", side_effect=_complete_launch
                ):
                    _snapshot, results = run_loop(
                        repo,
                        roadmap,
                        max_phases=3,
                        rotate_executors="codex,claude,gemini",
                        rotation_mode="phase",
                        rotation_on_policy_pin=pin_mode,
                    )

                self.assertEqual([result.executor for result in results], expected)
                self.assertEqual([event["selected_executor"] for event in _executor_events(repo)], expected)

    def test_degraded_rotation_candidate_falls_through_to_next_executor(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = write_named_roadmap(repo, (("ALPHA", "Alpha"),))
            plan = write_phase_plan(repo, "ALPHA", roadmap)
            commit_fixture_paths(repo, "rotation degradation fixture", roadmap, plan)
            record_degradation(repo, "codex", "account_or_billing_setup", "ALPHA", "Codex auth missing", 300)

            with patch("phase_loop_runtime.runner.run_auth_preflight", return_value=_preflight_ok()), patch(
                "phase_loop_runtime.runner.launch_with_spec", side_effect=_complete_launch
            ):
                _snapshot, results = run_loop(repo, roadmap, phase="ALPHA", rotate_executors="codex,claude", rotation_mode="phase")

            self.assertEqual(results[0].executor, "claude")
            event = _executor_events(repo)[-1]
            self.assertEqual(event["selected_executor"], "claude")
            self.assertEqual(event["metadata"]["dispatch_decision"]["considered_executors"], ["codex", "claude"])

    def test_work_unit_mode_records_selected_executor_on_launch(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(
                repo,
                "RUNNER",
                roadmap,
                body=(
                    "# RUNNER\n\n"
                    "## Lane Index & Dependencies\n\n"
                    "- SL-0 - Producer; Depends on: (none); Blocks: (none); Parallel-safe: no\n\n"
                    "## Lanes\n\n"
                    "### SL-0 - Producer\n"
                    "- **Owned files**: `producer.py`\n"
                ),
            )
            commit_fixture_paths(repo, "work unit rotation fixture", plan)

            snapshot, results = run_loop(
                repo,
                roadmap,
                phase="RUNNER",
                dry_run=True,
                work_unit_mode=True,
                rotate_executors="codex,claude",
                rotation_mode="work_unit",
            )

            self.assertEqual(results, [])
            self.assertEqual(snapshot.phases["RUNNER"], "executing")
            state = next(iter(load_work_unit_state(repo).values()))
            self.assertEqual(state.policy["executor"], "codex")
            event = _executor_events(repo)[-1]
            self.assertEqual(event["selected_executor"], "codex")

    def test_invalid_rotation_list_blocks_before_launch(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)

            with patch("phase_loop_runtime.runner.launch_with_spec") as fake_launch:
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER", rotate_executors="bogus")

            self.assertEqual(results, [])
            self.assertEqual(snapshot.blocker_class, "contract_bug")
            fake_launch.assert_not_called()
            event = read_events(repo)[-1]
            self.assertEqual(event["status"], "blocked")
            self.assertEqual(event["blocker"]["blocker_class"], "contract_bug")


if __name__ == "__main__":
    unittest.main()
