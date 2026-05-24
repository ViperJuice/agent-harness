import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_test_utils import commit_fixture_paths, make_repo, write_named_roadmap, write_phase_plan
from phase_loop_runtime.events import append_event, read_events
from phase_loop_runtime.launcher import LaunchResult
from phase_loop_runtime.models import LoopEvent, utc_now
from phase_loop_runtime.observability import build_terminal_summary
from phase_loop_runtime.provenance import event_provenance
from phase_loop_runtime.runner import run_loop


def _append_phase_owned_dirty_event(
    repo: Path,
    roadmap: Path,
    phase: str,
    path: str,
    *,
    status: str = "awaiting_phase_closeout",
) -> None:
    append_event(
        repo,
        LoopEvent(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phase=phase,
            action="execute",
            status=status,
            model="fixture",
            reasoning_effort="medium",
            source="fixture",
            metadata={
                "terminal_summary": build_terminal_summary(
                    terminal_status="executed",
                    terminal_blocker=None,
                    verification_status="passed",
                    next_action="Preserve phase-owned output.",
                    dirty_paths=(path,),
                    phase_owned_dirty=True,
                    phase_owned_dirty_paths=(path,),
                )
            },
            **event_provenance(roadmap, phase),
        ),
    )


def _beta_fixture(repo: Path) -> Path:
    roadmap = write_named_roadmap(repo, (("ALPHA", "Alpha"), ("BETA", "Beta")))
    alpha_plan = write_phase_plan(repo, "ALPHA", roadmap, owned_files=("alpha-output.txt",))
    beta_plan = write_phase_plan(repo, "BETA", roadmap, owned_files=("beta-output.txt",))
    commit_fixture_paths(repo, "add alpha beta plans", roadmap, alpha_plan, beta_plan)
    return roadmap


class PhaseLoopStartGateTest(unittest.TestCase):
    def test_phase_loop_start_gate_refuses_cross_phase_dirty(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = _beta_fixture(repo)
            _append_phase_owned_dirty_event(repo, roadmap, "ALPHA", "alpha-output.txt")
            (repo / "alpha-output.txt").write_text("dirty alpha output\n", encoding="utf-8")

            with patch("phase_loop_runtime.runner.launch_with_spec") as fake_launch:
                snapshot, results = run_loop(repo, roadmap, phase="BETA", max_phases=1, executor="codex")

            self.assertEqual(results, [])
            fake_launch.assert_not_called()
            self.assertEqual(snapshot.current_phase, "BETA")
            self.assertEqual(snapshot.blocker_class, "dirty_worktree_conflict")

            event = read_events(repo)[-1]
            self.assertEqual(event["action"], "start_gate_refused")
            self.assertEqual(event["status"], "blocked")
            self.assertEqual(event["blocker"]["blocker_class"], "dirty_worktree_conflict")
            gate = event["metadata"]["start_gate"]
            self.assertEqual(gate["status"], "refused")
            self.assertEqual(gate["current_phase"], "BETA")
            self.assertEqual(gate["offending_phase"], "ALPHA")
            self.assertEqual(gate["overlapping_dirty_paths"], ["alpha-output.txt"])
            self.assertLessEqual(gate["scanned_events"], 50)
            self.assertTrue(
                any("phase-loop reconcile --phase ALPHA --to-status planned --reason" in action for action in gate["next_actions"])
            )
            self.assertEqual(event["metadata"]["terminal_summary"]["terminal_status"], "blocked")

    def test_allow_cross_phase_dirty_records_bypass_and_dispatches(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = _beta_fixture(repo)
            _append_phase_owned_dirty_event(repo, roadmap, "ALPHA", "alpha-output.txt")
            (repo / "alpha-output.txt").write_text("dirty alpha output\n", encoding="utf-8")

            with patch(
                "phase_loop_runtime.runner.launch_with_spec",
                return_value=LaunchResult(command=["codex", "exec"], returncode=0),
            ) as fake_launch:
                run_loop(
                    repo,
                    roadmap,
                    phase="BETA",
                    max_phases=1,
                    executor="codex",
                    allow_cross_phase_dirty_reason="operator accepted overlap",
                )

            fake_launch.assert_called()
            bypass_events = [event for event in read_events(repo) if event.get("action") == "start_gate_bypassed"]
            self.assertEqual(len(bypass_events), 1)
            gate = bypass_events[0]["metadata"]["start_gate"]
            self.assertEqual(gate["status"], "bypassed")
            self.assertEqual(gate["current_phase"], "BETA")
            self.assertEqual(gate["offending_phase"], "ALPHA")
            self.assertEqual(gate["reason"], "operator accepted overlap")
            self.assertEqual(gate["overlapping_dirty_paths"], ["alpha-output.txt"])

    def test_start_gate_ignores_unrelated_manual_dirty_paths(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = _beta_fixture(repo)
            _append_phase_owned_dirty_event(repo, roadmap, "ALPHA", "alpha-output.txt")
            (repo / "manual-note.txt").write_text("operator note\n", encoding="utf-8")

            with patch(
                "phase_loop_runtime.runner.launch_with_spec",
                return_value=LaunchResult(command=["codex", "exec"], returncode=0),
            ) as fake_launch:
                run_loop(repo, roadmap, phase="BETA", max_phases=1, executor="codex")

            fake_launch.assert_called()
            self.assertFalse(any(event.get("action") == "start_gate_refused" for event in read_events(repo)))

    def test_start_gate_ignores_same_phase_dirty_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = _beta_fixture(repo)
            _append_phase_owned_dirty_event(repo, roadmap, "BETA", "beta-output.txt", status="executed")
            (repo / "beta-output.txt").write_text("dirty beta output\n", encoding="utf-8")

            with patch(
                "phase_loop_runtime.runner.launch_with_spec",
                return_value=LaunchResult(command=["codex", "exec"], returncode=0),
            ) as fake_launch:
                run_loop(repo, roadmap, phase="BETA", max_phases=1, executor="codex")

            fake_launch.assert_called()
            self.assertFalse(any(event.get("action") == "start_gate_refused" for event in read_events(repo)))


if __name__ == "__main__":
    unittest.main()
