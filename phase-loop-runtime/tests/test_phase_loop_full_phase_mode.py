import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from _dotfiles_tree import dotfiles_tree_present

# TESTDECOUPLE SL-1: this file reads dotfiles fleet paths (absent in the
# extracted agent-harness layout). Skip at MODULE level before any such read so
# collection does not error standalone; the marker keeps it deselected by
# `pytest -m "not dotfiles_integration"` and the conftest run-time hook.
if not dotfiles_tree_present():
    pytest.skip("requires dotfiles tree", allow_module_level=True)

pytestmark = pytest.mark.dotfiles_integration

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "vendor" / "phase-loop-runtime" / "src"))
from phase_loop_runtime.cli import build_parser
from phase_loop_runtime.events import append_event, read_events
from phase_loop_runtime.launcher import LaunchResult
from phase_loop_runtime.runner import run_loop
from phase_loop_test_utils import build_fake_automation_output, commit_fixture_paths, make_repo, provenanced_event, write_named_roadmap, write_phase_plan


def _launch_actions(repo: Path) -> list[str]:
    actions = []
    for event in read_events(repo):
        request = event.get("metadata", {}).get("launch_request")
        if request:
            actions.append(request["action"])
    return actions


def _fake_full_phase_launch(repo: Path, roadmap: Path, completed: set[str]):
    def launch(spec, **_kwargs):
        action = spec.prompt_bundle.product_action
        phase = spec.prompt_bundle.workflow_command.split()[-1] if action == "plan" else Path(spec.prompt_bundle.workflow_command.split()[-1]).stem.rsplit("-", 1)[-1]
        if action == "plan":
            plan = write_phase_plan(
                repo,
                phase,
                roadmap,
                extra_frontmatter={"last_generated": "2026-05-23T00:00:00Z"},
            )
            commit_fixture_paths(repo, f"add {phase} plan", plan)
            output = build_fake_automation_output(
                status="planned",
                next_skill="codex-execute-phase",
                next_command=f"codex-execute-phase {plan.relative_to(repo)}",
                verification_status="not_run",
                artifact=str(plan),
                artifact_state="tracked",
            )
        else:
            completed.add(phase)
            output = build_fake_automation_output(status="complete", verification_status="passed", artifact=str(spec.prompt_bundle.workflow_command.split()[-1]))
        return LaunchResult(command=spec.command, returncode=0, output=output, executor=spec.executor)

    return launch


class PhaseLoopFullPhaseModeTest(unittest.TestCase):
    def test_parser_accepts_full_phase_only_for_run_and_resume(self):
        parser = build_parser()
        for command in ("run", "resume"):
            args = parser.parse_args([command, "--full-phase", "--no-deprecation-hints"])
            self.assertTrue(args.full_phase)
            self.assertTrue(args.no_deprecation_hints)
        for command in ("status", "execute", "reconcile", "maintain-skills"):
            with self.subTest(command=command), self.assertRaises(SystemExit):
                parser.parse_args([command, "--full-phase"])

    def test_max_phases_help_names_action_count_default(self):
        buffer = StringIO()
        with self.assertRaises(SystemExit), redirect_stdout(buffer):
            build_parser().parse_args(["run", "--help"])
        help_text = buffer.getvalue()
        self.assertIn("Maximum dispatched actions by default", help_text)
        self.assertIn("--full-phase", help_text)
        self.assertIn("--no-deprecation-hints", help_text)

    def test_legacy_max_phases_one_dispatches_one_action(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            completed: set[str] = set()

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=_fake_full_phase_launch(repo, roadmap, completed)):
                run_loop(repo, roadmap, phase="RUNNER", max_phases=1, full_phase=False)

            self.assertEqual(_launch_actions(repo), ["plan"])
            self.assertEqual(completed, set())

    def test_full_phase_one_runs_plan_then_execute(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            completed: set[str] = set()

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=_fake_full_phase_launch(repo, roadmap, completed)):
                run_loop(repo, roadmap, phase="RUNNER", max_phases=1, full_phase=True)

            self.assertEqual(_launch_actions(repo), ["plan", "execute"])
            self.assertEqual(completed, {"RUNNER"})

    def test_full_phase_two_completes_two_phase_cycles(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = write_named_roadmap(repo, (("RUNNER", "Runner"), ("ACCESS", "Access")))
            commit_fixture_paths(repo, "two phase roadmap", roadmap)
            completed: set[str] = set()

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=_fake_full_phase_launch(repo, roadmap, completed)):
                run_loop(repo, roadmap, max_phases=2, full_phase=True)

            self.assertEqual(_launch_actions(repo), ["plan", "execute", "plan", "execute"])
            self.assertEqual(completed, {"RUNNER", "ACCESS"})

    def test_plan_doc_skip_current_plan_execute_counts_as_full_cycle(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, extra_frontmatter={"last_generated": "2026-05-23T00:00:00Z"})
            commit_fixture_paths(repo, "add runner plan", plan)
            append_event(repo, provenanced_event(repo, roadmap, "RUNNER", "planned", action="plan"))
            completed: set[str] = set()

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=_fake_full_phase_launch(repo, roadmap, completed)):
                run_loop(repo, roadmap, phase="RUNNER", max_phases=1, full_phase=True)

            self.assertEqual(_launch_actions(repo), ["execute"])
            self.assertTrue(any(event["status"] == "plan_skipped" for event in read_events(repo)))
            self.assertEqual(completed, {"RUNNER"})

    def test_legacy_max_phases_hint_emits_once_and_can_be_suppressed(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            completed: set[str] = set()
            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=_fake_full_phase_launch(repo, roadmap, completed)):
                run_loop(repo, roadmap, phase="RUNNER", max_phases=1, max_phases_explicit=True)
            hints = [event for event in read_events(repo) if "max_phases_hint" in event.get("metadata", {})]
            self.assertEqual(len(hints), 1)

        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            completed = set()
            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=_fake_full_phase_launch(repo, roadmap, completed)):
                run_loop(repo, roadmap, phase="RUNNER", max_phases=1, max_phases_explicit=True, no_deprecation_hints=True)
            self.assertFalse([event for event in read_events(repo) if "max_phases_hint" in event.get("metadata", {})])
