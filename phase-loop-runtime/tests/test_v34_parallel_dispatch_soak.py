from __future__ import annotations

import re
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.events import read_events
from phase_loop_runtime.launcher import AuthPreflightResult, LaunchResult
from phase_loop_runtime.plan_ir import iter_waves
from phase_loop_runtime.runner import run_loop
from phase_loop_runtime.worker_pool import PhaseWorkerJob, run_phase_worker_pool
from phase_loop_test_utils import build_fake_automation_output, commit_fixture_paths, make_repo, write_phase_plan

import pytest

# TESTDECOUPLE SL-1 (overlay-dependent): builds a skill/adoption bundle or runs the
# runtime execute path, which resolves the dotfiles skill-source / profile overlay
# (claude-config/*, codex-config/* …) absent standalone. Run-time integration: the
# conftest hook skips it when no dotfiles tree is reachable.
pytestmark = pytest.mark.dotfiles_integration


class V34ParallelDispatchSoakTest(unittest.TestCase):
    def test_soak_roadmap_declares_three_phase_wave_then_reducer(self):
        roadmap = Path(__file__).resolve().parents[3] / "specs" / "phase-plans-v34-soak-parallel.md"

        self.assertEqual(list(iter_waves(roadmap)), [("A", "B", "C"), ("D",)])

    def test_run_loop_completes_synthetic_soak_and_records_wave_telemetry(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = _write_soak_roadmap(repo)
            plans = tuple(write_phase_plan(repo, phase, roadmap) for phase in ("A", "B", "C", "D"))
            commit_fixture_paths(repo, "add v34 soak plans", roadmap, *plans)

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                phase = _phase_from_spec(spec)
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

            with (
                patch("phase_loop_runtime.runner.run_auth_preflight", return_value=AuthPreflightResult(ok=True, metadata={})),
                patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch),
            ):
                snapshot, results = run_loop(repo, roadmap, parallel_dispatch=True)

            self.assertEqual(len(results), 4)
            self.assertEqual(snapshot.phases, {"A": "complete", "B": "complete", "C": "complete", "D": "complete"})
            events = read_events(repo)
            worker_dispatches = [event["metadata"]["coordinator"] for event in events if event["action"] == "coordinator.worker_dispatched"]
            worker_completions = [event["metadata"]["coordinator"] for event in events if event["action"] == "coordinator.worker_completed"]
            wave_completions = [event["metadata"]["coordinator"] for event in events if event["action"] == "coordinator.wave_completed"]

            self.assertEqual([event["phase_alias"] for event in worker_dispatches], ["A", "B", "C", "D"])
            self.assertEqual({event["phase_alias"] for event in worker_completions[:3]}, {"A", "B", "C"})
            self.assertEqual(worker_dispatches[0]["phase_aliases"], ["A", "B", "C"])
            self.assertEqual(worker_dispatches[3]["phase_aliases"], ["D"])
            self.assertEqual(wave_completions[0]["succeeded_phases"], ["A", "B", "C"])
            self.assertEqual(wave_completions[1]["succeeded_phases"], ["D"])
            self.assertLess(
                _event_index(events, "coordinator.wave_completed", "C"),
                _event_index(events, "coordinator.worker_dispatched", "D"),
            )

    def test_first_wave_worker_pool_runs_under_one_and_half_times_longest_phase(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v34-soak-parallel.md"
            roadmap.write_text("# Roadmap\n", encoding="utf-8")
            delay = 0.08
            jobs = tuple(PhaseWorkerJob(phase=phase, spec=_Spec(phase)) for phase in ("A", "B", "C"))

            def fake_launch(spec, **kwargs):
                time.sleep(delay)
                return LaunchResult(command=["fake", spec.phase], returncode=0, executor="codex")

            before = time.monotonic()
            with patch("phase_loop_runtime.worker_pool.launch_with_spec", side_effect=fake_launch):
                results = run_phase_worker_pool(repo, roadmap, jobs, max_workers=3)
            elapsed = time.monotonic() - before

            self.assertEqual(tuple(result.phase for result in results), ("A", "B", "C"))
            self.assertLess(elapsed, delay * 1.5)


class _Spec:
    def __init__(self, phase: str) -> None:
        self.phase = phase
        self.executor = "codex"


def _write_soak_roadmap(repo: Path) -> Path:
    roadmap = repo / "specs" / "phase-plans-v34-soak-parallel.md"
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
            - (none)

            ---

            ### Phase 4 - Delta (D)
            **Depends on**
            - A
            - B
            - C
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return roadmap


def _phase_from_spec(spec) -> str:
    match = re.search(r"phase-plan-v1-([A-Z]+)\.md", spec.prompt_bundle.render_prompt())
    if match is None:
        raise AssertionError("missing phase plan path in launch spec")
    return match.group(1)


def _event_index(events: list[dict], action: str, phase: str) -> int:
    for index, event in enumerate(events):
        if event["action"] == action and event["phase"] == phase:
            return index
    raise AssertionError(f"missing {action} event for {phase}")


if __name__ == "__main__":
    unittest.main()
