import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.events import append_event
from phase_loop_runtime.models import LoopEvent, utc_now
from phase_loop_runtime.provenance import event_provenance
from phase_loop_runtime.runner import _classify_dirty_paths
from phase_loop_test_utils import commit_fixture_paths, make_repo, write_phase_plan


class PhaseLoopPreviousPhaseOwnedDirtyPathTest(unittest.TestCase):
    def test_phase_loop_dirty_path_classify_previous_phase_owned(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, owned_files=("README.md",))
            commit_fixture_paths(repo, "add runner plan", plan)
            self._append_previous_owned_event(repo, roadmap, "RUNNER", ["README.md"])
            (repo / "README.md").write_text("previous phase output\n", encoding="utf-8")

            summary = _classify_dirty_paths(
                repo,
                roadmap,
                plan,
                ["README.md"],
                ["README.md"],
                current_phase="RUNNER",
            )

            self.assertEqual(summary["previous_phase_owned_paths"], ["README.md"])
            self.assertEqual(summary["phase_owned_dirty_paths"], [])
            self.assertEqual(summary["pre_existing_dirty_paths"], [])
            self.assertEqual(summary["unowned_dirty_paths"], [])
            self.assertTrue(summary["phase_owned_dirty"])

    def test_different_phase_dirty_evidence_does_not_match(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, owned_files=("README.md",))
            commit_fixture_paths(repo, "add runner plan", plan)
            self._append_previous_owned_event(repo, roadmap, "OTHER", ["README.md"])
            (repo / "README.md").write_text("operator edit\n", encoding="utf-8")

            summary = _classify_dirty_paths(
                repo,
                roadmap,
                plan,
                ["README.md"],
                ["README.md"],
                current_phase="RUNNER",
            )

            self.assertEqual(summary["previous_phase_owned_paths"], [])
            self.assertEqual(summary["pre_existing_dirty_paths"], ["README.md"])

    def test_no_ledger_evidence_keeps_pre_existing_dirty_path(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, owned_files=("README.md",))
            commit_fixture_paths(repo, "add runner plan", plan)
            (repo / "README.md").write_text("operator edit\n", encoding="utf-8")

            summary = _classify_dirty_paths(
                repo,
                roadmap,
                plan,
                ["README.md"],
                ["README.md"],
                current_phase="RUNNER",
            )

            self.assertEqual(summary["previous_phase_owned_paths"], [])
            self.assertEqual(summary["pre_existing_dirty_paths"], ["README.md"])

    def _append_previous_owned_event(self, repo: Path, roadmap: Path, phase: str, paths: list[str]) -> None:
        append_event(
            repo,
            LoopEvent(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phase=phase,
                action="execute",
                status="awaiting_phase_closeout",
                model="gpt-5.6-terra",
                reasoning_effort="medium",
                source="fixture",
                metadata={
                    "incomplete_execute_dirty_worktree": {
                        "reason": "execute_status_without_completion_with_dirty_worktree",
                        "terminal_status": "executed",
                        "dirty_paths": paths,
                        "phase_owned_dirty_paths": paths,
                        "unowned_dirty_paths": [],
                        "pre_existing_dirty_paths": [],
                        "phase_owned_dirty": True,
                    }
                },
                **event_provenance(roadmap, phase),
            ),
        )
