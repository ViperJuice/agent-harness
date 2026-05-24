import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from phase_loop_test_utils import commit_fixture_paths, make_repo, write_named_roadmap, write_phase_plan
from phase_loop_runtime.cli import main
from phase_loop_runtime.events import append_event, read_events
from phase_loop_runtime.models import LoopEvent, utc_now
from phase_loop_runtime.provenance import event_provenance
from phase_loop_runtime.reconcile import reconcile


def _run(argv: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            code = main(argv)
        except SystemExit as exc:
            code = int(exc.code or 0)
    return code, stdout.getvalue(), stderr.getvalue()


class PhaseLoopReconcileToStatusPlannedRecoveryTest(unittest.TestCase):
    def _make_alpha_repo(self, td: str, *, with_plan: bool = True) -> tuple[Path, Path]:
        repo = make_repo(Path(td))
        roadmap = write_named_roadmap(repo, (("ALPHA", "Alpha"), ("BETA", "Beta")))
        commit_fixture_paths(repo, "add alpha roadmap", roadmap)
        if with_plan:
            plan = write_phase_plan(repo, "ALPHA", roadmap)
            commit_fixture_paths(repo, "add alpha plan", plan)
        return repo, roadmap

    def _blocked_event(
        self,
        repo: Path,
        roadmap: Path,
        *,
        blocker_class: str = "dirty_worktree_conflict",
        human_required: bool = False,
    ) -> LoopEvent:
        return LoopEvent(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phase="ALPHA",
            action="execute",
            status="blocked",
            model="gpt-5.5",
            reasoning_effort="medium",
            source="fixture",
            blocker={
                "human_required": human_required,
                "blocker_class": blocker_class,
                "blocker_summary": f"fixture {blocker_class} blocker",
                "required_human_inputs": ("secret",) if human_required else (),
                "access_attempts": (),
            },
            metadata={
                "terminal_summary": {
                    "terminal_status": "blocked",
                    "verification_status": "blocked",
                    "dirty_paths": ["outputs/alpha.txt"],
                    "phase_owned_dirty": True,
                    "phase_owned_dirty_paths": ["outputs/alpha.txt"],
                },
                "incomplete_execute_dirty_worktree": {
                    "dirty_paths": ["outputs/alpha.txt"],
                    "phase_owned_dirty": True,
                    "phase_owned_dirty_paths": ["outputs/alpha.txt"],
                    "previous_phase_owned_paths": [],
                    "unowned_dirty_paths": [],
                    "pre_existing_dirty_paths": [],
                    "terminal_status": "blocked",
                },
            },
            **event_provenance(roadmap, "ALPHA"),
        )

    def _recovery_args(self, repo: Path, roadmap: Path, *extra: str) -> list[str]:
        return [
            "reconcile",
            "--repo",
            str(repo),
            "--roadmap",
            str(roadmap),
            "--phase",
            "ALPHA",
            "--to-status",
            "planned",
            *extra,
        ]

    def test_phase_loop_reconcile_to_status_planned_recovery(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = self._make_alpha_repo(td)
            append_event(repo, self._blocked_event(repo, roadmap))

            code, _, stderr = _run(
                self._recovery_args(repo, roadmap, "--reason", "operator recovered dirty output")
            )

            self.assertEqual(code, 0, stderr)
            last = read_events(repo)[-1]
            self.assertEqual(last["action"], "manual_recovery")
            self.assertEqual(last["status"], "planned")
            recovery = last["metadata"]["manual_recovery"]
            self.assertEqual(recovery["from"], "blocked")
            self.assertEqual(recovery["to"], "planned")
            self.assertEqual(recovery["reason"], "operator recovered dirty output")
            self.assertEqual(recovery["trigger"], "cli")
            self.assertTrue(recovery["clears_blocker"])
            self.assertEqual(recovery["verification_status"], "not_run")

            first = reconcile(repo, roadmap)
            second = reconcile(repo, roadmap)
            self.assertEqual(first.phases["ALPHA"], "planned")
            self.assertEqual(second.phases["ALPHA"], "planned")
            self.assertFalse(first.human_required)
            self.assertIsNone(first.blocker_class)
            self.assertIsNone(first.blocker_summary)
            self.assertIsNone(first.terminal_summary)
            self.assertEqual(first.phase_owned_dirty_paths, ())
            self.assertEqual(first.previous_phase_owned_paths, ())

    def test_no_plan_recovery_transitions_to_unplanned(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = self._make_alpha_repo(td, with_plan=False)
            append_event(repo, self._blocked_event(repo, roadmap))

            code, _, stderr = _run(self._recovery_args(repo, roadmap, "--reason", "operator recovered"))

            self.assertEqual(code, 0, stderr)
            self.assertEqual(read_events(repo)[-1]["status"], "unplanned")
            self.assertEqual(read_events(repo)[-1]["metadata"]["manual_recovery"]["to"], "unplanned")
            self.assertEqual(reconcile(repo, roadmap).phases["ALPHA"], "unplanned")

    def test_sticky_blocker_is_refused_without_recovery_event(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = self._make_alpha_repo(td)
            append_event(
                repo,
                self._blocked_event(
                    repo,
                    roadmap,
                    blocker_class="missing_secret",
                    human_required=True,
                ),
            )
            before = len(read_events(repo))

            code, _, stderr = _run(self._recovery_args(repo, roadmap, "--reason", "operator recovered"))

            self.assertEqual(code, 2)
            self.assertIn("missing_secret", stderr)
            self.assertEqual(len(read_events(repo)), before)

    def test_to_status_planned_rejects_verification_status_passed(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = self._make_alpha_repo(td)

            code, _, stderr = _run(
                self._recovery_args(repo, roadmap, "--reason", "operator recovered", "--verification-status", "passed")
            )

            self.assertEqual(code, 2)
            self.assertIn("not allowed with argument", stderr)

    def test_completion_reconcile_still_emits_manual_repair(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = self._make_alpha_repo(td)

            code, _, stderr = _run(
                [
                    "reconcile",
                    "--repo",
                    str(repo),
                    "--roadmap",
                    str(roadmap),
                    "--phase",
                    "ALPHA",
                    "--repair-summary",
                    "legacy completion",
                    "--verification-status",
                    "passed",
                ]
            )

            self.assertEqual(code, 0, stderr)
            last = read_events(repo)[-1]
            self.assertEqual(last["action"], "manual_repair")
            self.assertEqual(last["status"], "complete")
            self.assertEqual(last["metadata"]["manual_repair"]["verification_status"], "passed")


if __name__ == "__main__":
    unittest.main()
