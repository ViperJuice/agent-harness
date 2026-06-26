"""v45 SCHED — cross-phase concurrent scheduler (IF-0-SCHED-1).

Drives ``run_loop(..., phase_scheduler_mode="concurrent")`` over the canonical
``FOUND → {EXTRACT ∥ IMPORT ∥ COACHPROF ∥ MEMORY} → VERIFY`` shape and proves:
  * the four middle phases dispatch *concurrently* (a threading.Barrier that only
    releases when all four are in-flight at once — it would deadlock under serial
    dispatch), each in its own isolated git worktree;
  * each child's LaunchSpec targets its worktree (cwd + embedded repo path), not
    the main tree;
  * disjoint results integrate back and every phase reaches a terminal status;
  * overlapping owned files serialize with a typed diagnostic instead of racing.

The RECONCILE-reclassification exit criterion (an already-complete renamed phase
must not be re-dispatched) is NOT asserted here: it depends on the deferred
RECONCILE body (#129); ``reconcile_against_git_reality`` ships as a no-op.

Fake executors do no git ops, so they exercise the orchestration + isolation
wiring, not real-executor commit/closeout integration semantics (a follow-up).
"""
from __future__ import annotations

import os
import re
import tempfile
import textwrap
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.events import read_events
from phase_loop_runtime.launcher import LaunchResult
from phase_loop_runtime.runner import run_loop
from phase_loop_test_utils import build_fake_automation_output, commit_fixture_paths, make_repo, write_phase_plan

MIDDLE = ("EXTRACT", "IMPORT", "COACHPROF", "MEMORY")

import pytest

# TESTDECOUPLE SL-1 (overlay-dependent): builds a skill/adoption bundle or runs the
# runtime execute path, which resolves the dotfiles skill-source / profile overlay
# (claude-config/*, codex-config/* …) absent standalone. Run-time integration: the
# conftest hook skips it when no dotfiles tree is reachable.
pytestmark = pytest.mark.dotfiles_integration


def _phase_from_spec(spec) -> str:
    match = re.search(r"phase-plan-v1-([A-Z]+)\.md", spec.prompt_bundle.render_prompt())
    assert match is not None, "spec prompt missing plan artifact reference"
    return match.group(1)


class V45SchedConcurrentTest(unittest.TestCase):
    def setUp(self):
        # The worker pool sizes to min(len(jobs), os.cpu_count()). Pin cpu_count
        # so a 4-wide wave actually runs 4 workers (and the Barrier(4) concurrency
        # proof executes) regardless of the host's real core count — otherwise the
        # proof silently skips on <4-core CI and green tells us nothing.
        patcher = patch("phase_loop_runtime.worker_pool.os.cpu_count", return_value=8)
        patcher.start()
        self.addCleanup(patcher.stop)

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

                ### Phase 3 - Coach Profile (COACHPROF)
                **Depends on**
                - FOUND

                ---

                ### Phase 4 - Memory (MEMORY)
                **Depends on**
                - FOUND

                ---

                ### Phase 5 - Verify (VERIFY)
                **Depends on**
                - EXTRACT
                - IMPORT
                - COACHPROF
                - MEMORY
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        return roadmap

    def _write_plans(self, repo: Path, roadmap: Path, *, owned):
        plans = []
        for phase, owned_files in owned.items():
            plans.append(write_phase_plan(repo, phase, roadmap, owned_files=owned_files))
        commit_fixture_paths(repo, "add v45 sched plans", roadmap, *plans)

    def test_concurrent_wave_dispatches_disjoint_phases_in_isolated_worktrees(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = self._write_roadmap(repo)
            owned = {
                "FOUND": ("src/found.py",),
                "EXTRACT": ("src/extract.py",),
                "IMPORT": ("src/import_.py",),
                "COACHPROF": ("src/coachprof.py",),
                "MEMORY": ("src/memory.py",),
                "VERIFY": ("src/verify.py",),
            }
            self._write_plans(repo, roadmap, owned=owned)

            barrier = threading.Barrier(len(MIDDLE), timeout=15)
            captured_specs: dict[str, object] = {}
            lock = threading.Lock()

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                phase = _phase_from_spec(spec)
                with lock:
                    captured_specs[phase] = spec
                if phase in MIDDLE:
                    # Releases only when all four middle phases are in-flight at
                    # once: deadlocks (→ timeout → test failure) under serial.
                    barrier.wait()
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

            def fake_worktree_path(repo_arg, *, branch, lane_id, project=None, workspace_mount=None):
                return Path(td) / "worktrees" / f"{branch}-{lane_id}"

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch), patch(
                "phase_loop_runtime.worker_pool.launch_with_spec", side_effect=fake_launch
            ), patch(
                "phase_loop_runtime.phase_worktree_executor.lane_worktree_path",
                side_effect=fake_worktree_path,
            ):
                # max_phases=1 is the CLI default: concurrent mode must auto-size
                # to walk every wave (FOUND -> {middle} -> VERIFY), not stop after
                # the first wave.
                snapshot, results = run_loop(
                    repo,
                    roadmap,
                    phase_scheduler_mode="concurrent",
                    max_phases=1,
                )

            # Every phase reached complete.
            for phase in ("FOUND", *MIDDLE, "VERIFY"):
                self.assertEqual(snapshot.phases[phase], "complete", phase)

            # The four middle phases were dispatched as one concurrent wave.
            events = read_events(repo)
            wave_started = [
                event for event in events if event["action"] == "coordinator.concurrent_wave_started"
            ]
            self.assertEqual(len(wave_started), 1)
            self.assertEqual(
                sorted(wave_started[0]["metadata"]["coordinator"]["wave"]), sorted(MIDDLE)
            )

            # Each middle phase's spec targeted its OWN worktree (cwd + the
            # embedded repo path), not the main tree.
            branch = snapshot_branch(snapshot, repo)
            for phase in MIDDLE:
                spec = captured_specs[phase]
                expected = str(Path(td) / "worktrees" / f"{branch}-{phase}")
                self.assertEqual(spec.wrapped_cwd, expected, f"{phase} cwd")
                self.assertNotEqual(spec.wrapped_cwd, str(repo), f"{phase} must not run in main tree")

    def test_overlapping_ownership_serializes_with_diagnostic(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = self._write_roadmap(repo)
            # EXTRACT and IMPORT both own src/shared.py → overlapping → must not race.
            owned = {
                "FOUND": ("src/found.py",),
                "EXTRACT": ("src/shared.py",),
                "IMPORT": ("src/shared.py",),
                "COACHPROF": ("src/coachprof.py",),
                "MEMORY": ("src/memory.py",),
                "VERIFY": ("src/verify.py",),
            }
            self._write_plans(repo, roadmap, owned=owned)

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

            def fake_worktree_path(repo_arg, *, branch, lane_id, project=None, workspace_mount=None):
                return Path(td) / "worktrees" / f"{branch}-{lane_id}"

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch), patch(
                "phase_loop_runtime.worker_pool.launch_with_spec", side_effect=fake_launch
            ), patch(
                "phase_loop_runtime.phase_worktree_executor.lane_worktree_path",
                side_effect=fake_worktree_path,
            ):
                snapshot, results = run_loop(
                    repo,
                    roadmap,
                    phase_scheduler_mode="concurrent",
                    max_phases=20,
                )

            events = read_events(repo)
            serialized = [
                event
                for event in events
                if event["action"] == "coordinator.concurrent_overlap_serialized"
            ]
            self.assertTrue(serialized, "expected an overlap-serialized diagnostic event")
            kinds = {
                diagnostic["kind"]
                for diagnostic in serialized[0]["metadata"]["coordinator"]["diagnostics"]
            }
            self.assertIn("overlapping_write_ownership", kinds)
            # The overlapping pair must never appear in a concurrent wave together
            # (a later disjoint subset legitimately may run concurrently once one
            # of the pair has completed).
            concurrent_waves = [
                set(event["metadata"]["coordinator"]["wave"])
                for event in events
                if event["action"] == "coordinator.concurrent_wave_started"
            ]
            for wave in concurrent_waves:
                self.assertFalse(
                    {"EXTRACT", "IMPORT"}.issubset(wave),
                    f"overlapping EXTRACT+IMPORT raced in wave {wave}",
                )
            # The phases still complete (overlapping ones serialized one-at-a-time).
            for phase in ("FOUND", *MIDDLE, "VERIFY"):
                self.assertEqual(snapshot.phases[phase], "complete", phase)

    def test_full_phase_concurrent_counts_cycles_and_completes(self):
        # --full-phase + concurrent: the dispatched-wave `continue` skips the loop
        # tail that increments phase_cycles_completed in serial mode, so the wave
        # path must account for terminal phases itself. Regression: the full DAG
        # still completes (and the loop terminates) under full_phase.
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = self._write_roadmap(repo)
            owned = {
                "FOUND": ("src/found.py",),
                "EXTRACT": ("src/extract.py",),
                "IMPORT": ("src/import_.py",),
                "COACHPROF": ("src/coachprof.py",),
                "MEMORY": ("src/memory.py",),
                "VERIFY": ("src/verify.py",),
            }
            self._write_plans(repo, roadmap, owned=owned)

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

            def fake_worktree_path(repo_arg, *, branch, lane_id, project=None, workspace_mount=None):
                return Path(td) / "worktrees" / f"{branch}-{lane_id}"

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch), patch(
                "phase_loop_runtime.worker_pool.launch_with_spec", side_effect=fake_launch
            ), patch(
                "phase_loop_runtime.phase_worktree_executor.lane_worktree_path",
                side_effect=fake_worktree_path,
            ):
                snapshot, results = run_loop(
                    repo,
                    roadmap,
                    phase_scheduler_mode="concurrent",
                    full_phase=True,
                    max_phases=12,
                )

            for phase in ("FOUND", *MIDDLE, "VERIFY"):
                self.assertEqual(snapshot.phases[phase], "complete", phase)

    def _write_two_root_roadmap(self, repo: Path) -> Path:
        roadmap = repo / "specs" / "phase-plans-v1.md"
        roadmap.write_text(
            "# Roadmap\n\n"
            "### Phase 0 - Alpha (ALPHA)\n**Depends on**\n- (none)\n\n---\n\n"
            "### Phase 1 - Beta (BETA)\n**Depends on**\n- (none)\n",
            encoding="utf-8",
        )
        return roadmap

    def test_concurrent_wave_halts_on_operator_stop(self):
        # Review BLOCKER: stop_requested must halt an in-flight concurrent wave, not
        # be silently discarded. Two root phases form a concurrent first wave; with
        # stop requested, prepare emits operator_halt + breaks, the wave returns
        # "halt", and NO child launches.
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = self._write_two_root_roadmap(repo)
            self._write_plans(repo, roadmap, owned={"ALPHA": ("src/alpha.py",), "BETA": ("src/beta.py",)})
            launched: list[str] = []

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                launched.append(_phase_from_spec(spec))
                return LaunchResult(command=spec.command, returncode=0, output="", executor=spec.executor)

            def fake_worktree_path(repo_arg, *, branch, lane_id, project=None, workspace_mount=None):
                return Path(td) / "worktrees" / f"{branch}-{lane_id}"

            with patch("phase_loop_runtime.runner.stop_requested", return_value=True), patch(
                "phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch
            ), patch("phase_loop_runtime.worker_pool.launch_with_spec", side_effect=fake_launch), patch(
                "phase_loop_runtime.phase_worktree_executor.lane_worktree_path", side_effect=fake_worktree_path
            ):
                snapshot, results = run_loop(repo, roadmap, phase_scheduler_mode="concurrent", max_phases=5)

            self.assertEqual(launched, [], "stop must halt the wave before any child launches")
            events = read_events(repo)
            halts = [event for event in events if event["action"] == "operator_halt"]
            # Exactly one: post-fix the wave breaks immediately on the first phase's
            # break outcome. Pre-fix (control discarded) it returns "dispatched" and
            # the loop re-enters, re-emitting operator_halt every iteration until
            # max_phases — so this would be >1 on the buggy code.
            self.assertEqual(len(halts), 1, f"expected one operator_halt, got {len(halts)}")
            self.assertNotEqual(snapshot.phases.get("ALPHA"), "complete")

    def test_concurrent_wave_tears_down_worktrees_when_pool_raises(self):
        # Review BLOCKER: a mid-wave raise must not leak worktrees. Force the pool to
        # raise after worktrees are created; the try/finally must remove them all.
        created: list[Path] = []

        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = self._write_roadmap(repo)
            owned = {
                "FOUND": ("src/found.py",),
                "EXTRACT": ("src/extract.py",),
                "IMPORT": ("src/import_.py",),
                "COACHPROF": ("src/coachprof.py",),
                "MEMORY": ("src/memory.py",),
                "VERIFY": ("src/verify.py",),
            }
            self._write_plans(repo, roadmap, owned=owned)

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

            def fake_worktree_path(repo_arg, *, branch, lane_id, project=None, workspace_mount=None):
                path = Path(td) / "worktrees" / f"{branch}-{lane_id}"
                created.append(path)
                return path

            def boom(*args, **kwargs):
                raise RuntimeError("pool boom")

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch), patch(
                "phase_loop_runtime.runner.run_phase_worker_pool", side_effect=boom
            ), patch(
                "phase_loop_runtime.phase_worktree_executor.lane_worktree_path", side_effect=fake_worktree_path
            ):
                with self.assertRaises(RuntimeError):
                    run_loop(repo, roadmap, phase_scheduler_mode="concurrent", max_phases=12)

            # FOUND dispatched serially; the 4-middle wave created worktrees, the pool
            # raised, and the finally must have torn every one of them down.
            wave_worktrees = [p for p in created if any(m in p.name for m in MIDDLE)]
            self.assertTrue(wave_worktrees, "expected middle-phase worktrees to be created")
            for path in wave_worktrees:
                self.assertFalse(path.exists(), f"leaked worktree {path}")


class V45SchedWiringTest(unittest.TestCase):
    def test_phase_scheduler_arg_parses_choices(self):
        from phase_loop_runtime.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["--phase-scheduler", "concurrent", "run"])
        self.assertEqual(args.phase_scheduler_mode, "concurrent")

    def test_phase_scheduler_defaults_off(self):
        from phase_loop_runtime.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["run"])
        self.assertEqual(getattr(args, "phase_scheduler_mode", "off"), "off")

    def test_run_loop_rejects_invalid_phase_scheduler_mode(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            with self.assertRaises(ValueError):
                run_loop(repo, roadmap, phase_scheduler_mode="bogus", dry_run=True)


def snapshot_branch(snapshot, repo: Path) -> str:
    import subprocess

    return subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"], text=True
    ).strip()


if __name__ == "__main__":
    unittest.main()
