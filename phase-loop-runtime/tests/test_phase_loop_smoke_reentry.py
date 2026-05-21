import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.events import read_events
from phase_loop_runtime.reconcile import reconcile
from phase_loop_smoke_utils import (
    append_manual_import_event,
    append_phase_event,
    claude_team_live_smoke_enabled,
    enabled_live_smoke_executors,
    fake_manual_import_executors,
    isolated_codex_home,
    make_live_repair_fixture,
    make_live_review_fixture,
    make_live_roadmap_fixture,
    make_live_team_fixture,
    make_live_execute_fixture,
    make_live_plan_fixture,
    make_two_phase_repo,
    run_live_smoke,
    run_live_loop_action,
    live_harness,
    opencode_live_smoke_enabled,
    write_phase_state,
    write_plan,
    write_skill_handoff,
)


class PhaseLoopSmokeReentryTest(unittest.TestCase):
    def _assert_shared_live_artifacts(self, repo: Path, expected_executor: str) -> None:
        self.assertTrue((repo / ".phase-loop" / "events.jsonl").exists())
        self.assertTrue((repo / ".phase-loop" / "tui-handoff.md").exists())
        launch_dir = max((repo / ".phase-loop" / "runs").iterdir(), key=lambda item: item.name)
        self.assertTrue((launch_dir / "launch.json").exists())
        self.assertTrue((launch_dir / "terminal-summary.json").exists())
        event = read_events(repo)[-1]
        self.assertEqual(event["metadata"]["launch"]["executor"], expected_executor)

    def test_resume_preserves_planned_phase_without_execution_event(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_plan(repo, "ALPHA", tracked=True)
            write_phase_state(repo, roadmap, {"ALPHA": "planned"})
            snapshot = reconcile(repo, roadmap)
            self.assertEqual(snapshot.phases["ALPHA"], "planned")
            self.assertEqual(snapshot.current_phase, "ALPHA")

    def test_interrupted_executing_state_with_dirty_work_becomes_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            (repo / "README.md").write_text("dirty\n")
            write_phase_state(repo, roadmap, {"ALPHA": "executing"})
            snapshot = reconcile(repo, roadmap)
            self.assertEqual(snapshot.phases["ALPHA"], "unknown")

    def test_manual_handoff_and_event_continue_mid_loop(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = make_two_phase_repo(root)
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_plan(repo, "ALPHA")
            write_phase_state(repo, roadmap, {"ALPHA": "executing"})
            with isolated_codex_home(root) as codex_home:
                write_skill_handoff(codex_home, repo, "codex-execute-phase", "ALPHA", "complete", plan)
                append_phase_event(repo, roadmap, "ALPHA", "complete", source="manual")
                snapshot = reconcile(repo, roadmap)
            self.assertEqual(snapshot.phases["ALPHA"], "complete")
            self.assertEqual(snapshot.current_phase, "BETA")

    def test_cross_harness_execute_handoff_continues_mid_loop(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = make_two_phase_repo(root)
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_plan(repo, "ALPHA")
            write_phase_state(repo, roadmap, {"ALPHA": "executing"})
            with isolated_codex_home(root) as codex_home:
                write_skill_handoff(codex_home, repo, "gemini-execute-phase", "ALPHA", "complete", plan)
                append_phase_event(repo, roadmap, "ALPHA", "complete", source="fixture")
                snapshot = reconcile(repo, roadmap)
            self.assertEqual(snapshot.phases["ALPHA"], "complete")
            self.assertEqual(snapshot.current_phase, "BETA")

    def test_manual_import_completion_from_claude_stays_visible(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = make_two_phase_repo(root)
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_plan(repo, "ALPHA")
            write_phase_state(repo, roadmap, {"ALPHA": "executing"})
            append_manual_import_event(
                repo,
                roadmap,
                "ALPHA",
                "complete",
                harness="claude",
                skill="claude-execute-phase",
                artifact=plan,
                installed_skill_warnings=("claude: installed skill missing",),
            )
            snapshot = reconcile(repo, roadmap)
            self.assertEqual(snapshot.phases["ALPHA"], "complete")
            self.assertEqual(snapshot.current_phase, "BETA")

    def test_manual_import_completion_matrix_stays_visible_across_fake_harnesses(self):
        for harness in fake_manual_import_executors():
            with self.subTest(harness=harness):
                with tempfile.TemporaryDirectory() as td:
                    root = Path(td)
                    repo = make_two_phase_repo(root)
                    roadmap = repo / "specs" / "phase-plans-v1.md"
                    plan = write_plan(repo, "ALPHA")
                    write_phase_state(repo, roadmap, {"ALPHA": "executing"})
                    append_manual_import_event(
                        repo,
                        roadmap,
                        "ALPHA",
                        "complete",
                        harness=harness,
                        skill=f"{harness}-execute-phase",
                        artifact=plan,
                        installed_skill_warnings=(f"{harness}: fake matrix warning",),
                    )
                    snapshot = reconcile(repo, roadmap)
                self.assertEqual(snapshot.phases["ALPHA"], "complete")
                self.assertEqual(snapshot.current_phase, "BETA")

    @unittest.skipUnless(opencode_live_smoke_enabled(), "set PHASE_LOOP_ENABLE_OPENCODE_LIVE_TEST=1 with a local OpenCode session to run live smoke")
    def test_live_opencode_plan_smoke_can_create_a_phase_plan(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = make_live_plan_fixture(Path(td), "opencode")
            result = run_live_smoke(fixture.repo, fixture.roadmap, fixture.execute_phase, "opencode")
            self.assertEqual(result.returncode, 0, msg=f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}")
            self.assertTrue((fixture.repo / "plans" / "phase-plan-v1-PLANONLY.md").exists())

    @unittest.skipUnless(opencode_live_smoke_enabled(), "set PHASE_LOOP_ENABLE_OPENCODE_LIVE_TEST=1 with a local OpenCode session to run live smoke")
    def test_live_opencode_execute_smoke_can_leave_docs_only_closeout(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = make_live_execute_fixture(Path(td), "opencode")
            result = run_live_smoke(fixture.repo, fixture.roadmap, fixture.execute_phase, "opencode")
            self.assertEqual(result.returncode, 0, msg=f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}")
            self.assertTrue((fixture.repo / ".phase-loop" / "runs").exists())

    def test_live_single_harness_plan_matrix(self):
        executors = enabled_live_smoke_executors()
        if not executors:
            self.skipTest("enable one or more PHASE_LOOP_ENABLE_*_LIVE_TEST flags with local authenticated CLIs to run live matrix smokes")

        for executor in executors:
            with self.subTest(executor=executor):
                with tempfile.TemporaryDirectory() as td:
                    fixture = make_live_plan_fixture(Path(td), executor)
                    result = run_live_smoke(fixture.repo, fixture.roadmap, fixture.execute_phase, executor)
                    self.assertEqual(result.returncode, 0, msg=f"{executor} stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}")
                    self.assertTrue((fixture.repo / "plans" / "phase-plan-v1-PLANONLY.md").exists())
                    self._assert_shared_live_artifacts(fixture.repo, executor)

    def test_live_single_harness_execute_matrix(self):
        executors = enabled_live_smoke_executors()
        if not executors:
            self.skipTest("enable one or more PHASE_LOOP_ENABLE_*_LIVE_TEST flags with local authenticated CLIs to run live matrix smokes")

        for executor in executors:
            with self.subTest(executor=executor):
                with tempfile.TemporaryDirectory() as td:
                    fixture = make_live_execute_fixture(Path(td), executor)
                    result = run_live_smoke(fixture.repo, fixture.roadmap, fixture.execute_phase, executor)
                    self.assertEqual(result.returncode, 0, msg=f"{executor} stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}")
                    self.assertTrue((fixture.repo / ".phase-loop" / "runs").exists())
                    self.assertTrue((fixture.repo / ".phase-loop" / "tui-handoff.md").exists())
                    launch_dir = max((fixture.repo / ".phase-loop" / "runs").iterdir(), key=lambda item: item.name)
                    self.assertTrue((launch_dir / "launch.json").exists())
                    self.assertTrue((launch_dir / "terminal-summary.json").exists())
                    self.assertIn(live_harness(executor).binary, " ".join(result.args))

    def test_live_single_harness_roadmap_matrix(self):
        executors = enabled_live_smoke_executors()
        if not executors:
            self.skipTest("enable one or more PHASE_LOOP_ENABLE_*_LIVE_TEST flags with local authenticated CLIs to run live matrix smokes")

        for executor in executors:
            with self.subTest(executor=executor):
                with tempfile.TemporaryDirectory() as td:
                    fixture = make_live_roadmap_fixture(Path(td), executor)
                    snapshot, results = run_live_loop_action(
                        fixture.repo,
                        fixture.roadmap,
                        fixture.execute_phase,
                        executor,
                        "roadmap",
                    )
                    self.assertEqual(len(results), 1)
                    self.assertFalse(results[0].failed)
                    self.assertIn(snapshot.phases[fixture.execute_phase], {"planned", "complete", "executed", "unplanned"})
                    self._assert_shared_live_artifacts(fixture.repo, executor)

    def test_live_single_harness_review_matrix(self):
        executors = enabled_live_smoke_executors()
        if not executors:
            self.skipTest("enable one or more PHASE_LOOP_ENABLE_*_LIVE_TEST flags with local authenticated CLIs to run live matrix smokes")

        for executor in executors:
            with self.subTest(executor=executor):
                with tempfile.TemporaryDirectory() as td:
                    fixture = make_live_review_fixture(Path(td), executor)
                    snapshot, results = run_live_loop_action(
                        fixture.repo,
                        fixture.roadmap,
                        fixture.execute_phase,
                        executor,
                        "review",
                    )
                    self.assertEqual(len(results), 1)
                    self.assertFalse(results[0].failed)
                    self.assertIn(snapshot.phases[fixture.execute_phase], {"complete", "executed", "planned", "awaiting_phase_closeout"})
                    self._assert_shared_live_artifacts(fixture.repo, executor)

    def test_live_single_harness_repair_matrix(self):
        executors = enabled_live_smoke_executors()
        if not executors:
            self.skipTest("enable one or more PHASE_LOOP_ENABLE_*_LIVE_TEST flags with local authenticated CLIs to run live matrix smokes")

        for executor in executors:
            with self.subTest(executor=executor):
                with tempfile.TemporaryDirectory() as td:
                    fixture = make_live_repair_fixture(Path(td), executor)
                    snapshot, results = run_live_loop_action(
                        fixture.repo,
                        fixture.roadmap,
                        fixture.execute_phase,
                        executor,
                        "repair",
                    )
                    self.assertEqual(len(results), 1)
                    self.assertFalse(results[0].failed)
                    self.assertIn(snapshot.phases[fixture.execute_phase], {"blocked", "planned", "executed", "complete"})
                    self._assert_shared_live_artifacts(fixture.repo, executor)

    @unittest.skipUnless(claude_team_live_smoke_enabled(), "set PHASE_LOOP_ENABLE_CLAUDE_LIVE_TEST=1 and PHASE_LOOP_ENABLE_CLAUDE_TEAM_LIVE_TEST=1 to run Claude native-team live smoke")
    def test_live_claude_team_matrix(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = make_live_team_fixture(Path(td))
            snapshot, results = run_live_loop_action(
                fixture.repo,
                fixture.roadmap,
                fixture.execute_phase,
                "claude",
                "execute",
                claude_execution_mode="agent_team",
            )
            self.assertEqual(len(results), 1)
            self._assert_shared_live_artifacts(fixture.repo, "claude")
            launch_dir = max((fixture.repo / ".phase-loop" / "runs").iterdir(), key=lambda item: item.name)
            task_snapshot = launch_dir / "task-snapshot.json"
            if task_snapshot.exists():
                self.assertFalse(results[0].failed)
                self.assertIn(snapshot.phases[fixture.execute_phase], {"complete", "executed", "planned"})
            else:
                event = read_events(fixture.repo)[-1]
                self.assertEqual(event["status"], "blocked")
                self.assertEqual(event["blocker"]["blocker_class"], "repeated_verification_failure")
                self.assertIn("automation closeout", event["blocker"]["blocker_summary"])


if __name__ == "__main__":
    unittest.main()
