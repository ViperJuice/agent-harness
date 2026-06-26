"""v45 SCHEDHARDEN — real-executor worktree integration (#130).

The v45 SCHED concurrent scheduler launches each ready phase's child in its OWN
git worktree (``--phase-scheduler concurrent``). The original integration step,
``integrate_phase_worktree``, only moves *committed* work back to the pipeline
branch (``rev-list base..temp``). But a real phase executor leaves its verified
work *DIRTY* (uncommitted) in the worktree and emits ``awaiting_phase_closeout``
— the parent runner's closeout is what stages+commits the dirty phase-owned
files (``_perform_phase_closeout`` in runner.py). So against a real (dirty)
child, ``integrate_phase_worktree`` is a no-op: the work never reaches main, the
finalize closeout runs on a clean main tree and finds nothing to commit, and the
worktree is torn down — silently losing the child's work.

These tests use a *side-effect* fake that writes real dirty owned files into the
child's worktree (the status-only SCHED fakes never exercised this). They prove:

  * with the real-exec integration ON, each concurrent child's dirty work is
    transported onto main and committed by the existing closeout → ``complete``;
  * two concurrent siblings BOTH land (the second's patch applies cleanly after
    the first's closeout commit advanced main — the disjointness-gate claim);
  * with the flag OFF (default cutover state), the dirty work is lost — the
    regression the fix repairs, proving the assertion is not green-on-both-sides.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.launcher import LaunchResult
from phase_loop_runtime.runner import run_loop
from phase_loop_test_utils import build_fake_automation_output, commit_fixture_paths, make_repo, write_phase_plan

MIDDLE_DIRTY = ("EXTRACT", "IMPORT")

import pytest

# TESTDECOUPLE SL-1 (overlay-dependent): builds a skill/adoption bundle or runs the
# runtime execute path, which resolves the dotfiles skill-source / profile overlay
# (claude-config/*, codex-config/* …) absent standalone. Run-time integration: the
# conftest hook skips it when no dotfiles tree is reachable.
pytestmark = pytest.mark.dotfiles_integration


def _phase_from_spec(spec) -> str:
    match = re.search(r"phase-plan-v1-([A-Z_]+)\.md", spec.prompt_bundle.render_prompt())
    assert match is not None, "spec prompt missing plan artifact reference"
    return match.group(1)


def _committed_on_main(repo: Path, rel_path: str) -> bool:
    """True iff ``rel_path`` exists in main's HEAD tree (i.e. was committed)."""

    return (
        subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-e", f"HEAD:{rel_path}"],
            capture_output=True,
        ).returncode
        == 0
    )


class V45SchedHardenRealExecTest(unittest.TestCase):
    def setUp(self):
        # Pin cpu_count so the 2-wide middle wave actually runs 2 workers.
        patcher = patch("phase_loop_runtime.worker_pool.os.cpu_count", return_value=8)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.owned = {
            "FOUND": "src/found.py",
            "EXTRACT": "src/extract.py",
            "IMPORT": "src/import_.py",
            "VERIFY": "src/verify.py",
        }

    def _write_roadmap(self, repo: Path) -> Path:
        roadmap = repo / "specs" / "phase-plans-v1.md"
        roadmap.write_text(
            textwrap.dedent(
                """
                # Roadmap

                ### Phase 0 - Foundation (FOUND)
                **Depends on**
                - (none)

                ---

                ### Phase 1 - Extract (EXTRACT)
                **Depends on**
                - FOUND

                ---

                ### Phase 2 - Import (IMPORT)
                **Depends on**
                - FOUND

                ---

                ### Phase 3 - Verify (VERIFY)
                **Depends on**
                - EXTRACT
                - IMPORT
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        return roadmap

    def _write_plans(self, repo: Path, roadmap: Path) -> None:
        plans = [
            write_phase_plan(repo, phase, roadmap, owned_files=(owned_file,))
            for phase, owned_file in self.owned.items()
        ]
        commit_fixture_paths(repo, "add schedharden plans", roadmap, *plans)

    def _fake_launch(self):
        """A side-effect fake.

        Middle phases behave like a real executor: write the phase-owned file
        into the child's worktree and leave it DIRTY (bare LaunchResult → the
        runner's executed-fallback dirty path → awaiting_phase_closeout). FOUND
        and VERIFY self-report complete with a clean tree to isolate the variable
        under test (concurrent dirty-worktree integration).
        """

        def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
            phase = _phase_from_spec(spec)
            if phase in MIDDLE_DIRTY:
                target = Path(spec.wrapped_cwd) / self.owned[phase]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(f"{phase} real-exec output\n", encoding="utf-8")
                # Bare result: no automation closeout → executed fallback → the
                # runner detects the dirty owned file and reduces to
                # awaiting_phase_closeout, then closes out (commits) on main.
                return LaunchResult(command=spec.command, returncode=0, executor=spec.executor)
            return LaunchResult(
                command=spec.command,
                returncode=0,
                output=build_fake_automation_output(
                    status="complete",
                    verification_status="passed",
                    artifact=str(Path(spec.wrapped_cwd) / "plans" / f"phase-plan-v1-{phase}.md"),
                    artifact_state="tracked",
                ),
                executor=spec.executor,
            )

        return fake_launch

    def _run(self, repo: Path, roadmap: Path):
        def fake_worktree_path(repo_arg, *, branch, lane_id, project=None, workspace_mount=None):
            return repo.parent / "worktrees" / f"{branch}-{lane_id}"

        with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=self._fake_launch()), patch(
            "phase_loop_runtime.worker_pool.launch_with_spec", side_effect=self._fake_launch()
        ), patch(
            "phase_loop_runtime.phase_worktree_executor.lane_worktree_path",
            side_effect=fake_worktree_path,
        ):
            # closeout_mode="commit": a real concurrent run must commit each phase's
            # closeout (the default "manual" mode stops at awaiting_phase_closeout
            # and would strand dirty work on main across waves).
            return run_loop(
                repo,
                roadmap,
                phase_scheduler_mode="concurrent",
                closeout_mode="commit",
                max_phases=1,
            )

    def test_concurrent_real_exec_dirty_work_commits_on_main(self):
        with patch.dict(os.environ, {"PHASE_LOOP_CONCURRENT_REAL_EXEC": "true"}):
            with tempfile.TemporaryDirectory() as td:
                repo = make_repo(Path(td))
                roadmap = self._write_roadmap(repo)
                self._write_plans(repo, roadmap)

                snapshot, _results = self._run(repo, roadmap)

                # Both concurrent children's verified work landed and closed out.
                for phase in MIDDLE_DIRTY:
                    self.assertEqual(snapshot.phases[phase], "complete", phase)
                    self.assertTrue(
                        _committed_on_main(repo, self.owned[phase]),
                        f"{phase} dirty work was not committed on main",
                    )
                # No work stranded in the main tree.
                dirty = subprocess.check_output(
                    ["git", "-C", str(repo), "status", "--porcelain"], text=True
                ).strip()
                self.assertEqual(dirty, "", f"main tree left dirty: {dirty!r}")

    def test_concurrent_real_exec_never_commits_preexisting_unowned_dirt(self):
        # Baseline-composition safety: pre_launch_dirty_paths is captured against
        # MAIN and completion is measured on main after transport, so unrelated
        # dirt already present on main is classified as pre-existing — never folded
        # into a phase's ownership-gated closeout commit. (Unexpected main dirt may
        # legitimately BLOCK a phase via the runner's completion-dirty safety check;
        # the invariant under test is no *silent corruption*: the operator's file is
        # neither committed as phase output nor mutated.)
        with patch.dict(os.environ, {"PHASE_LOOP_CONCURRENT_REAL_EXEC": "true"}):
            with tempfile.TemporaryDirectory() as td:
                repo = make_repo(Path(td))
                roadmap = self._write_roadmap(repo)
                self._write_plans(repo, roadmap)
                # Unrelated, unowned dirt present on main before the run.
                (repo / "scratch.txt").write_text("operator scratch\n", encoding="utf-8")

                self._run(repo, roadmap)

                # The pre-existing unowned file was neither committed nor mutated.
                self.assertFalse(_committed_on_main(repo, "scratch.txt"))
                self.assertEqual((repo / "scratch.txt").read_text(), "operator scratch\n")

    def test_without_flag_concurrent_dirty_work_is_lost(self):
        # Cutover safety / regression proof: with the flag OFF (default), the
        # legacy committed-only integration is a no-op on dirty children, so the
        # work never reaches main — the phase BLOCKS (the closeout finds no
        # phase-owned dirty paths) and the worktree is torn down, discarding the
        # child's work. This is the behavior the fix repairs; it must FAIL the
        # land-on-main assertions to prove the green above is real.
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = self._write_roadmap(repo)
            self._write_plans(repo, roadmap)

            snapshot, _results = self._run(repo, roadmap)

            for phase in MIDDLE_DIRTY:
                self.assertFalse(
                    _committed_on_main(repo, self.owned[phase]),
                    f"{phase}: legacy integration unexpectedly landed dirty work without the flag",
                )
                # Pin the specific failure mode: a loud block, not a silent
                # complete-with-work-gone.
                self.assertEqual(
                    snapshot.phases[phase], "blocked",
                    f"{phase}: expected a block when dirty work never reached main",
                )

    def test_concurrent_transfer_conflict_preserves_branch_and_blocks(self):
        # Gate-bypass safety net at the runner level: if transport cannot apply,
        # the runner records a typed conflict event, KEEPS the temp branch
        # (work recoverable), and lets finalize block — never a silent success.
        from phase_loop_runtime.phase_worktree_executor import WorktreeTransferResult

        def fake_transfer(repo, handle, *, commit_message=None):
            return WorktreeTransferResult(
                phase=handle.phase,
                temp_branch=handle.temp_branch,
                had_changes=True,
                applied=False,
                conflict=True,
                reason="forced conflict for test",
            )

        with patch.dict(os.environ, {"PHASE_LOOP_CONCURRENT_REAL_EXEC": "true"}):
            with tempfile.TemporaryDirectory() as td:
                repo = make_repo(Path(td))
                roadmap = self._write_roadmap(repo)
                self._write_plans(repo, roadmap)

                with patch(
                    "phase_loop_runtime.runner.transfer_phase_worktree_dirty",
                    side_effect=fake_transfer,
                ):
                    snapshot, _results = self._run(repo, roadmap)

                for phase in MIDDLE_DIRTY:
                    self.assertNotEqual(snapshot.phases[phase], "complete", phase)
                    self.assertFalse(_committed_on_main(repo, self.owned[phase]), phase)
                from phase_loop_runtime.events import read_events

                conflicts = [
                    e
                    for e in read_events(repo)
                    if e["action"] == "coordinator.concurrent_transfer_conflict"
                ]
                self.assertEqual({c["phase"] for c in conflicts}, set(MIDDLE_DIRTY))
                self.assertTrue(
                    all(c["metadata"]["coordinator"]["transfer"]["conflict"] for c in conflicts)
                )
                self.assertTrue(
                    all(c["metadata"]["coordinator"]["preserved_branch"] for c in conflicts)
                )

    def test_concurrent_real_exec_with_manual_closeout_is_refused(self):
        # Footgun guard: manual closeout would strand transported dirty work on
        # main across waves, so the runner must refuse at startup rather than fail
        # opaquely at the next wave's start gate.
        with patch.dict(os.environ, {"PHASE_LOOP_CONCURRENT_REAL_EXEC": "true"}):
            with tempfile.TemporaryDirectory() as td:
                repo = make_repo(Path(td))
                roadmap = self._write_roadmap(repo)
                self._write_plans(repo, roadmap)
                with self.assertRaises(ValueError) as ctx:
                    run_loop(
                        repo,
                        roadmap,
                        phase_scheduler_mode="concurrent",
                        closeout_mode="manual",
                        max_phases=1,
                    )
                self.assertIn("closeout-mode", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
