import tempfile
import textwrap
import unittest
import re
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.events import read_events
from phase_loop_runtime.launcher import LaunchResult
from phase_loop_runtime.runner import run_loop
from phase_loop_test_utils import build_fake_automation_output, commit_fixture_paths, make_repo, write_phase_plan

import pytest

# TESTDECOUPLE SL-1 (overlay-dependent): builds a skill/adoption bundle or runs the
# runtime execute path, which resolves the dotfiles skill-source / profile overlay
# (claude-config/*, codex-config/* …) absent standalone. Run-time integration: the
# conftest hook skips it when no dotfiles tree is reachable.
pytestmark = pytest.mark.dotfiles_integration


class CoordinatorWaveLoopTest(unittest.TestCase):
    def test_parallel_dispatch_walks_waves_serially_and_emits_telemetry(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = self._write_mixed_roadmap(repo)
            plans = tuple(write_phase_plan(repo, phase, roadmap) for phase in ("A", "B", "C"))
            commit_fixture_paths(repo, "add coordinator plans", roadmap, *plans)
            launched: list[str] = []

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                phase = self._phase_from_spec(spec)
                launched.append(phase)
                return LaunchResult(
                    command=spec.command,
                    returncode=0,
                    output=build_fake_automation_output(
                        status="complete",
                        verification_status="passed",
                        artifact=str(repo / "plans" / f"phase-plan-v1-{phase}.md"),
                        artifact_state="tracked",
                    ),
                    executor=spec.executor,
                    log_path=str(log_path) if log_path else None,
                )

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                snapshot, results = run_loop(repo, roadmap, parallel_dispatch=True)

            self.assertEqual(launched, ["A", "B", "C"])
            self.assertEqual(len(results), 3)
            self.assertEqual(snapshot.phases["A"], "complete")
            self.assertEqual(snapshot.phases["B"], "complete")
            self.assertEqual(snapshot.phases["C"], "complete")
            events = read_events(repo)
            self.assertEqual(self._actions(events, "coordinator.wave_started"), ["A", "C"])
            self.assertEqual(self._actions(events, "coordinator.phase_dispatched"), ["A", "B", "C"])
            self.assertEqual(self._actions(events, "coordinator.phase_completed"), ["A", "B", "C"])
            completed = [event["metadata"]["coordinator"] for event in events if event["action"] == "coordinator.wave_completed"]
            self.assertEqual([item["wave_index"] for item in completed], [0, 1])
            self.assertEqual(completed[0]["succeeded_phases"], ["A", "B"])
            self.assertEqual(completed[1]["succeeded_phases"], ["C"])

    def test_blocked_phase_finishes_current_wave_but_gates_next_wave(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = self._write_mixed_roadmap(repo)
            plans = tuple(write_phase_plan(repo, phase, roadmap) for phase in ("A", "B", "C"))
            commit_fixture_paths(repo, "add coordinator plans", roadmap, *plans)
            launched: list[str] = []

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                phase = self._phase_from_spec(spec)
                launched.append(phase)
                status = "blocked" if phase == "A" else "complete"
                return LaunchResult(
                    command=spec.command,
                    returncode=0,
                    output=build_fake_automation_output(
                        status=status,
                        verification_status="blocked" if status == "blocked" else "passed",
                        blocker_class="repeated_verification_failure" if status == "blocked" else "none",
                        blocker_summary="fixture blocker" if status == "blocked" else "none",
                        artifact=str(repo / "plans" / f"phase-plan-v1-{phase}.md"),
                        artifact_state="tracked",
                    ),
                    executor=spec.executor,
                    log_path=str(log_path) if log_path else None,
                )

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                snapshot, results = run_loop(repo, roadmap, parallel_dispatch=True)

            self.assertEqual(launched, ["A", "B"])
            self.assertEqual(len(results), 2)
            self.assertEqual(snapshot.phases["A"], "blocked")
            self.assertEqual(snapshot.phases["B"], "complete")
            self.assertNotEqual(snapshot.phases.get("C"), "complete")
            completed = [event["metadata"]["coordinator"] for event in read_events(repo) if event["action"] == "coordinator.wave_completed"]
            self.assertEqual(completed[-1]["failed_phases"], ["A"])
            self.assertEqual(completed[-1]["succeeded_phases"], ["B"])

    def _write_mixed_roadmap(self, repo: Path) -> Path:
        roadmap = repo / "specs" / "phase-plans-v1.md"
        roadmap.write_text(
            textwrap.dedent(
                """
                # Roadmap

                ### Phase 1 - Alpha (A)
                **Depends on**
                - (none)

                ---

                ### Phase 2 - Beta (B)
                **Depends on**
                - (none)

                ---

                ### Phase 3 - Gamma (C)
                **Depends on**
                - A
                - B
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        return roadmap

    def _actions(self, events: list[dict], action: str) -> list[str]:
        return [event["phase"] for event in events if event["action"] == action]

    def _phase_from_spec(self, spec) -> str:
        match = re.search(r"phase-plan-v1-([A-Z]+)\.md", spec.prompt_bundle.render_prompt())
        self.assertIsNotNone(match)
        return match.group(1)


if __name__ == "__main__":
    unittest.main()
