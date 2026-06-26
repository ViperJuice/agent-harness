import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.events import read_events
from phase_loop_runtime.launcher import AuthPreflightResult, LaunchResult
from phase_loop_runtime.runner import run_loop
from phase_loop_test_utils import commit_fixture_paths, make_repo, write_phase_plan

import pytest

# TESTDECOUPLE SL-1 (overlay-dependent): builds a skill/adoption bundle or runs the
# runtime execute path, which resolves the dotfiles skill-source / profile overlay
# (claude-config/*, codex-config/* …) absent standalone. Run-time integration: the
# conftest hook skips it when no dotfiles tree is reachable.
pytestmark = pytest.mark.dotfiles_integration


class PhaseLoopTerminalSummaryMirrorsBamlCloseoutTest(unittest.TestCase):
    def test_terminal_summary_mirrors_baml_closeout(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = self._fixture(Path(td))
            payload = self._closeout_payload()

            def fake_launch(spec, **_kwargs):
                return LaunchResult(command=spec.command, returncode=0, output=json.dumps(payload), executor=spec.executor)

            with patch("phase_loop_runtime.runner.run_auth_preflight", return_value=AuthPreflightResult(ok=True, metadata={})), patch(
                "phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch
            ):
                run_loop(repo, roadmap, phase="RSA", executor="codex")

            event = self._latest_terminal_event(repo)
            summary = event["metadata"]["terminal_summary"]
            persisted = json.loads(Path(event["metadata"]["artifacts"]["terminal"]).read_text(encoding="utf-8"))
            for data in (summary, persisted):
                self.assertEqual(data["terminal_status"], "awaiting_phase_closeout")
                self.assertEqual(data["verification_status"], "passed")
                self.assertEqual(data["produced_if_gates"], ["IF-0-RECONCILESTATEAUDIT-1"])
                self.assertIsNone(data.get("terminal_blocker"))
                self.assertNotIn("extraction_failure", data)

    def test_terminal_summary_uses_final_well_formed_closeout(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = self._fixture(Path(td))
            earlier = {
                "terminal_status": "executed",
                "verification_status": "not_run",
                "dirty_paths": [],
                "produced_if_gates": [],
            }
            final = self._closeout_payload()
            output = f"{json.dumps(earlier)}\nnoise\n{json.dumps(final)}\n"

            with patch("phase_loop_runtime.runner.run_auth_preflight", return_value=AuthPreflightResult(ok=True, metadata={})), patch(
                "phase_loop_runtime.runner.launch_with_spec",
                return_value=LaunchResult(command=["codex", "exec"], returncode=0, output=output, executor="codex"),
            ):
                run_loop(repo, roadmap, phase="RSA", executor="codex")

            summary = self._latest_terminal_event(repo)["metadata"]["terminal_summary"]
            self.assertEqual(summary["terminal_status"], "awaiting_phase_closeout")
            self.assertEqual(summary["verification_status"], "passed")
            self.assertEqual(summary["produced_if_gates"], ["IF-0-RECONCILESTATEAUDIT-1"])

    def test_malformed_closeout_falls_back_and_records_extraction_failure(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = self._fixture(Path(td))
            output = '{"terminal_status":"awaiting_phase_closeout","verification_status":"passed","dirty_paths":['

            with patch("phase_loop_runtime.runner.run_auth_preflight", return_value=AuthPreflightResult(ok=True, metadata={})), patch(
                "phase_loop_runtime.runner.launch_with_spec",
                return_value=LaunchResult(command=["codex", "exec"], returncode=0, output=output, executor="codex"),
            ):
                run_loop(repo, roadmap, phase="RSA", executor="codex")

            summary = self._latest_terminal_event(repo)["metadata"]["terminal_summary"]
            self.assertEqual(summary["terminal_status"], "blocked")
            self.assertEqual(summary["verification_status"], "blocked")
            self.assertEqual(summary["extraction_failure"]["reason"], "truncated_output")
            self.assertNotIn("terminal_status", json.dumps(summary["extraction_failure"]))
            self.assertNotIn("dirty_paths", json.dumps(summary["extraction_failure"]))

    def _fixture(self, tmp_path: Path) -> tuple[Path, Path]:
        repo = make_repo(tmp_path)
        roadmap = repo / "specs" / "phase-plans-v29.md"
        roadmap.write_text("# v29\n\n### Phase 0 - Reconcile State Audit (RSA)\n", encoding="utf-8")
        plan = write_phase_plan(
            repo,
            "RSA",
            roadmap,
            body=(
                "# RSA\n\n"
                "**Produces**: `IF-0-RECONCILESTATEAUDIT-1`\n\n"
                "## Lanes\n\n"
                "### SL-0 - Regression\n"
                "- **Owned files**: `runner.py`\n"
                "- **Interfaces provided**: `IF-0-RECONCILESTATEAUDIT-1`\n"
            ),
        )
        commit_fixture_paths(repo, "add rsa fixture", roadmap, plan)
        return repo, roadmap

    def _closeout_payload(self) -> dict[str, object]:
        return {
            "terminal_status": "awaiting_phase_closeout",
            "verification_status": "passed",
            "dirty_paths": [],
            "produced_if_gates": ["IF-0-RECONCILESTATEAUDIT-1"],
            "next_action": "none",
            "blocker_class": "none",
            "blocker_summary": "none",
            "human_required": False,
            "required_human_inputs": [],
        }

    def _latest_terminal_event(self, repo: Path) -> dict[str, object]:
        for event in reversed(read_events(repo)):
            if event.get("metadata", {}).get("terminal_summary"):
                return event
        raise AssertionError("expected a terminal summary event")


if __name__ == "__main__":
    unittest.main()
