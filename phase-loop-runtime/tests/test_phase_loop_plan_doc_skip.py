import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.cli import build_parser
from phase_loop_runtime.events import append_event, read_events
from phase_loop_runtime.launcher import LaunchResult
from phase_loop_runtime.models import EVENT_STATUSES, PHASE_STATUSES, LoopEvent, StateSnapshot, utc_now
from phase_loop_runtime.runner import is_plan_doc_current, run_loop
from phase_loop_test_utils import commit_fixture_paths, make_repo, provenanced_event, write_phase_plan

import pytest

# TESTDECOUPLE SL-1 (overlay-dependent): builds a skill/adoption bundle or runs the
# runtime execute path, which resolves the dotfiles skill-source / profile overlay
# (claude-config/*, codex-config/* …) absent standalone. Run-time integration: the
# conftest hook skips it when no dotfiles tree is reachable.
pytestmark = pytest.mark.dotfiles_integration


def _fake_launch(spec, **_kwargs):
    return LaunchResult(command=spec.command, returncode=0, output="", dry_run=True, executor=spec.executor)


def _launched_action(events):
    return events[-1]["metadata"]["launch_request"]["action"]


class PhaseLoopPlanDocSkipTest(unittest.TestCase):
    def test_planned_current_plan_dispatches_execute_and_records_plan_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            commit_fixture_paths(repo, "add runner plan", plan)
            append_event(repo, provenanced_event(repo, roadmap, "RUNNER", "planned", action="plan"))

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=_fake_launch):
                run_loop(repo, roadmap, phase="RUNNER", dry_run=True, observe=False)

            events = read_events(repo)
            skipped = [event for event in events if event["status"] == "plan_skipped"]
            self.assertEqual(_launched_action(events), "execute")
            self.assertEqual(skipped[-1]["metadata"]["plan_doc_skip"]["reason"], "plan_doc_current")
            self.assertEqual(skipped[-1]["metadata"]["plan_doc_skip"]["plan_artifact"], "plans/phase-plan-v1-RUNNER.md")
            self.assertFalse(skipped[-1]["metadata"]["plan_doc_skip"]["forced_replan"])

    def test_planned_with_no_plan_dispatches_plan(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(repo, provenanced_event(repo, roadmap, "RUNNER", "planned", action="plan"))

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=_fake_launch):
                run_loop(repo, roadmap, phase="RUNNER", dry_run=True, observe=False)

            self.assertEqual(_launched_action(read_events(repo)), "plan")

    def test_unplanned_phase_dispatches_plan(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=_fake_launch):
                run_loop(repo, roadmap, phase="RUNNER", dry_run=True, observe=False)

            self.assertEqual(_launched_action(read_events(repo)), "plan")

    def test_reopened_phase_with_committed_plan_dispatches_execute(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            commit_fixture_paths(repo, "add runner plan", plan)
            append_event(repo, provenanced_event(repo, roadmap, "RUNNER", "complete", action="execute"))
            append_event(repo, provenanced_event(repo, roadmap, "RUNNER", "planned", action="reopen"))

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=_fake_launch):
                run_loop(repo, roadmap, phase="RUNNER", dry_run=True, observe=False)

            self.assertEqual(_launched_action(read_events(repo)), "execute")

    def test_force_replan_dispatches_plan_even_when_plan_is_current(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            commit_fixture_paths(repo, "add runner plan", plan)
            append_event(repo, provenanced_event(repo, roadmap, "RUNNER", "planned", action="plan"))

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=_fake_launch):
                run_loop(repo, roadmap, phase="RUNNER", dry_run=True, observe=False, force_replan=True)

            events = read_events(repo)
            self.assertEqual(_launched_action(events), "plan")
            self.assertFalse(any(event["status"] == "plan_skipped" for event in events))

    def test_plan_skipped_event_json_roundtrips(self):
        event = LoopEvent(
            timestamp=utc_now(),
            repo="/repo",
            roadmap="/repo/specs/phase-plans-v1.md",
            phase="RUNNER",
            action="run",
            status="plan_skipped",
            model="gpt-5.4",
            reasoning_effort="medium",
            source="fixture",
            metadata={"plan_doc_skip": {"reason": "plan_doc_current", "plan_artifact": "plans/phase-plan-v1-RUNNER.md", "forced_replan": False}},
        )
        self.assertEqual(event.to_json()["status"], "plan_skipped")
        self.assertEqual(event.to_json()["metadata"]["plan_doc_skip"]["reason"], "plan_doc_current")

    def test_event_status_drift_keeps_plan_skipped_out_of_phase_statuses(self):
        self.assertIn("plan_skipped", EVENT_STATUSES)
        self.assertNotIn("plan_skipped", PHASE_STATUSES)
        with self.assertRaises(ValueError):
            StateSnapshot(timestamp=utc_now(), repo="/repo", roadmap="/repo/specs/phase-plans-v1.md", phases={"RUNNER": "plan_skipped"})

    def test_last_generated_frontmatter_marks_uncommitted_matching_plan_current(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, extra_frontmatter={"last_generated": "2026-05-22T00:00:00Z"})

            self.assertTrue(is_plan_doc_current(repo, "RUNNER", plan, roadmap))

    def test_phase_frontmatter_match_marks_plan_current_without_last_generated_or_recent_commit(self):
        # Regenesis DEF-2: plan exists at expected path, frontmatter says
        # phase: RUNNER, but it has no last_generated and never appeared in
        # recent git activity. The runner should still treat it as current
        # because the frontmatter phase matches the queried phase.
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            # Deliberately do NOT commit the plan, and do NOT set
            # last_generated. Frontmatter phase: RUNNER is set by
            # write_phase_plan unconditionally.
            self.assertTrue(is_plan_doc_current(repo, "RUNNER", plan, roadmap))

    def test_phase_frontmatter_mismatch_falls_through_to_git_lookup(self):
        # Plan exists at the expected path for queried phase but its
        # frontmatter `phase:` field disagrees AND it has no last_generated
        # AND no git activity. is_plan_doc_current should NOT treat it as
        # current via the frontmatter shortcut.
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = repo / "plans" / "phase-plan-v1-RUNNER.md"
            import hashlib
            roadmap_hash = hashlib.sha256(roadmap.read_bytes()).hexdigest()
            plan.write_text(
                "---\n"
                "phase_loop_plan_version: 1\n"
                "phase: ACCESS\n"  # mismatched on purpose
                f"roadmap: specs/phase-plans-v1.md\n"
                f"roadmap_sha256: {roadmap_hash}\n"
                "---\n"
                "# body\n"
            )
            self.assertFalse(is_plan_doc_current(repo, "RUNNER", plan, roadmap))

    def test_force_replan_help_is_limited_to_runner_commands(self):
        parser = build_parser()
        for command in ("run", "resume", "dry-run"):
            self.assertTrue(parser.parse_args([command, "--force-replan"]).force_replan)
        with self.assertRaises(SystemExit):
            parser.parse_args(["status", "--force-replan"])
        self.assertTrue(parser.parse_args(["--force-replan"]).force_replan)

