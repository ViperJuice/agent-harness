import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.baml_modular import BamlValidationError, build_baml_request, parse_baml_response
from phase_loop_runtime.closeout_validation import validate_produced_gates
from phase_loop_runtime.launcher import AuthPreflightResult, LaunchResult
from phase_loop_runtime.runner import run_loop
from phase_loop_test_utils import make_repo, write_phase_plan

import pytest

# TESTDECOUPLE SL-1 (overlay-dependent): builds a skill/adoption bundle or runs the
# runtime execute path, which resolves the dotfiles skill-source / profile overlay
# (claude-config/*, codex-config/* …) absent standalone. Run-time integration: the
# conftest hook skips it when no dotfiles tree is reachable.
pytestmark = pytest.mark.dotfiles_integration


class PhaseLoopBamlEndToEndTest(unittest.TestCase):
    def test_prompt_to_fake_cli_to_baml_parse_to_if_gate_validation(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap, plan = self._fixture(Path(td))
            request = build_baml_request(
                "EmitPhaseCloseout",
                {
                    "phase_alias": "BAMLBASE",
                    "plan_produces": ["IF-0-BAMLBASE-1"],
                    "plan_owned_files": ["contract.py"],
                    "closeout_commit_sha": None,
                },
            )
            self.assertIn("IF-0-BAMLBASE-1", request.prompt)

            payload = {
                "terminal_status": "complete",
                "verification_status": "passed",
                "dirty_paths": [],
                "produced_if_gates": ["IF-0-BAMLBASE-1"],
                "next_action": None,
                "blocker_class": None,
                "blocker_summary": None,
                "human_required": None,
                "required_human_inputs": [],
            }
            parsed = parse_baml_response("EmitPhaseCloseout", json.dumps(payload))
            self.assertTrue(validate_produced_gates(plan, parsed.payload).ok)

            def fake_launch(spec, **_kwargs):
                return LaunchResult(command=spec.command, returncode=0, output=json.dumps(payload), executor=spec.executor)

            with patch("phase_loop_runtime.runner.run_auth_preflight", return_value=AuthPreflightResult(ok=True, metadata={})), patch(
                "phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch
            ):
                snapshot, _results = run_loop(repo, roadmap, phase="BAMLBASE", executor="codex")

            self.assertIn(snapshot.phases["BAMLBASE"], {"complete", "awaiting_phase_closeout"})

    def test_schema_invalid_closeout_rejects_before_gate_validation(self):
        with self.assertRaises(BamlValidationError):
            parse_baml_response(
                "EmitPhaseCloseout",
                json.dumps(
                    {
                        "terminal_status": "complete",
                        "verification_status": "passed",
                        "dirty_paths": [],
                        "next_action": None,
                        "blocker_class": None,
                        "blocker_summary": None,
                        "human_required": None,
                        "required_human_inputs": [],
                    }
                ),
            )

    def _fixture(self, tmp_path: Path) -> tuple[Path, Path, Path]:
        repo = make_repo(tmp_path)
        roadmap = repo / "specs" / "phase-plans-v20.md"
        roadmap.parent.mkdir(parents=True, exist_ok=True)
        roadmap.write_text("# v20\n\n### Phase 2 - BAML Base (BAMLBASE)\n", encoding="utf-8")
        plan = write_phase_plan(
            repo,
            "BAMLBASE",
            roadmap,
            body=(
                "# BAMLBASE\n\n"
                "**Produces**: `IF-0-BAMLBASE-1`\n\n"
                "## Lanes\n\n"
                "### SL-0 - Contract\n"
                "- **Owned files**: `contract.py`\n"
                "- **Interfaces provided**: `IF-0-BAMLBASE-1`\n"
            ),
        )
        return repo, roadmap, plan


if __name__ == "__main__":
    unittest.main()
