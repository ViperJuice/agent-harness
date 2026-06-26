import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.discovery import parse_pipeline_plan_metadata, pipeline_execution_plan_diagnostic
from phase_loop_runtime.events import append_event, read_events
from phase_loop_runtime.launcher import LaunchResult
from phase_loop_runtime.models import LoopEvent, utc_now
from phase_loop_runtime.provenance import event_provenance
from phase_loop_runtime.reconcile import reconcile
from phase_loop_runtime.runner import run_loop
from phase_loop_test_utils import (
    build_fake_automation_output,
    make_code_index_blocker_fixture,
    make_greenfield_closeout_fixture,
    make_regenesis_amendment_fixture,
    make_repo,
    provenanced_event,
    write_phase_plan,
)


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "phase_loop_migration"

import pytest

# TESTDECOUPLE SL-1 (overlay-dependent): builds a skill/adoption bundle or runs the
# runtime execute path, which resolves the dotfiles skill-source / profile overlay
# (claude-config/*, codex-config/* …) absent standalone. Run-time integration: the
# conftest hook skips it when no dotfiles tree is reachable.
pytestmark = pytest.mark.dotfiles_integration


class PhaseLoopMigrationRegressionsTest(unittest.TestCase):
    def test_observed_case_fixtures_are_named_and_typed(self):
        expected = {
            "stale_handoff_repair",
            "verified_dirty_closeout",
            "roadmap_amendment",
            "true_human_blocker",
            "gemini_noisy_output",
            "malformed_child_closeout",
        }
        found = {path.stem for path in FIXTURE_ROOT.glob("*.json")}
        self.assertEqual(found, expected)
        for path in FIXTURE_ROOT.glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("case", data)
            self.assertIn("human_required", data)

    def test_standalone_plan_and_dry_run_do_not_require_pipeline_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add standalone plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            append_event(repo, provenanced_event(repo, roadmap, "RUNNER", "planned", action="plan"))

            metadata = parse_pipeline_plan_metadata(plan)
            diagnostic = pipeline_execution_plan_diagnostic(repo, plan, phase="RUNNER", roadmap=roadmap)
            _snapshot, results = run_loop(
                repo,
                roadmap,
                phase="RUNNER",
                dry_run=True,
                product_action_override="execute",
            )

            self.assertTrue(metadata.empty)
            self.assertIsNone(diagnostic)
            self.assertFalse((repo / ".pipeline").exists())
            self.assertEqual(len(results), 1)
            terminal_summary = read_events(repo)[-1]["metadata"]["terminal_summary"]
            self.assertNotIn("phase_loop_closeout", terminal_summary)

    def test_stale_handoff_repair_reduces_to_current_unplanned_state(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            append_event(repo, provenanced_event(repo, roadmap, "RUNNER", "complete", action="execute"))
            roadmap.write_text(roadmap.read_text() + "\n### Phase 3 - Docs (DOCS)\n")
            subprocess.run(["git", "add", str(roadmap.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "amend roadmap"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.phases["RUNNER"], "unplanned")
            self.assertFalse(snapshot.human_required)
            self.assertTrue(plan.exists())

    def test_verified_dirty_closeout_reduces_to_awaiting_closeout(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = make_greenfield_closeout_fixture(Path(td))
            repo = fixture.repo
            roadmap = fixture.roadmap

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                output_path = repo / "artifacts" / "enforce-report.json"
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text('{"ok": true}\n', encoding="utf-8")
                return LaunchResult(
                    command=spec.command,
                    returncode=0,
                    output=build_fake_automation_output(status="complete", verification_status="passed", artifact=str(fixture.plan)),
                    log_path=str(log_path) if log_path is not None else None,
                )

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                snapshot, _results = run_loop(repo, roadmap, phase=fixture.execute_phase)

            self.assertEqual(snapshot.phases[fixture.execute_phase], "awaiting_phase_closeout")
            self.assertFalse(snapshot.human_required)

    def test_roadmap_amendment_routes_to_inserted_downstream_phase(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = make_regenesis_amendment_fixture(Path(td))
            repo = fixture.repo
            roadmap = fixture.roadmap

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                roadmap.write_text(
                    "# Roadmap\n\n"
                    "### Phase 0 - Affordance Verification (AFFVERIFY)\n\n"
                    "### Phase 1 - Mobile Shell (MOBSHELL)\n\n"
                    "### Phase 2 - Visual Fidelity (VISUAL)\n"
                )
                subprocess.run(["git", "add", str(roadmap.relative_to(repo))], cwd=repo, check=True)
                subprocess.run(["git", "commit", "-m", "steer roadmap"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
                append_event(repo, provenanced_event(repo, roadmap, fixture.execute_phase, "complete", action="execute"))
                return LaunchResult(command=spec.command, returncode=0)

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                snapshot, _results = run_loop(repo, roadmap, phase=fixture.execute_phase, max_phases=1)

            self.assertEqual(snapshot.current_phase, "MOBSHELL")
            self.assertEqual(snapshot.phases["VISUAL"], "unplanned")

    def test_true_human_blocker_remains_human_required(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = make_code_index_blocker_fixture(Path(td))
            append_event(
                fixture.repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(fixture.repo),
                    roadmap=str(fixture.roadmap),
                    phase=fixture.execute_phase,
                    action="execute",
                    status="blocked",
                    model="gpt-5.5",
                    reasoning_effort="medium",
                    source="fixture",
                    blocker={
                        "human_required": True,
                        "blocker_class": "product_decision_missing",
                        "blocker_summary": "Operator must choose the release target.",
                        "required_human_inputs": ("release target",),
                    },
                    **event_provenance(fixture.roadmap, fixture.execute_phase),
                ),
            )

            snapshot = reconcile(fixture.repo, fixture.roadmap)

            self.assertEqual(snapshot.phases[fixture.execute_phase], "blocked")
            self.assertTrue(snapshot.human_required)
            self.assertEqual(snapshot.blocker_class, "product_decision_missing")

    def test_gemini_noisy_output_and_malformed_closeout_reduce_deterministically(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add runner plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            noisy_output = "warning: noisy prelude\n" + build_fake_automation_output(status="complete", verification_status="passed")

            with patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
            ), patch(
                "phase_loop_runtime.runner.launch_with_spec",
                return_value=LaunchResult(command=["gemini"], returncode=0, output=noisy_output, executor="gemini"),
            ):
                snapshot, _results = run_loop(repo, roadmap, phase="RUNNER", executor="gemini")

            self.assertEqual(snapshot.phases["RUNNER"], "complete")
            self.assertFalse(snapshot.human_required)

        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add runner plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

            with patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
            ), patch(
                "phase_loop_runtime.runner.launch_with_spec",
                return_value=LaunchResult(command=["gemini"], returncode=0, output="no automation here", executor="gemini"),
            ):
                snapshot, _results = run_loop(repo, roadmap, phase="RUNNER", executor="gemini")

            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            self.assertFalse(snapshot.human_required)
            self.assertEqual(snapshot.blocker_class, "repeated_verification_failure")


if __name__ == "__main__":
    unittest.main()
