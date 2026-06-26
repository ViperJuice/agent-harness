import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.launcher import AuthPreflightResult, LaunchResult
from phase_loop_runtime.runner import run_loop
from phase_loop_test_utils import make_repo, write_phase_plan

import pytest

# TESTDECOUPLE SL-1 (overlay-dependent): builds a skill/adoption bundle or runs the
# runtime execute path, which resolves the dotfiles skill-source / profile overlay
# (claude-config/*, codex-config/* …) absent standalone. Run-time integration: the
# conftest hook skips it when no dotfiles tree is reachable.
pytestmark = pytest.mark.dotfiles_integration


class PhaseLoopSpuriousCompletionRejectionTest(unittest.TestCase):
    def test_database_style_zero_gate_repair_closeout_cannot_complete_phase(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(
                repo,
                "RUNNER",
                roadmap,
                body=(
                    "# RUNNER\n\n"
                    "**Produces**: `IF-0-NATIVE-1`\n\n"
                    "## Lanes\n\n"
                    "### SL-0 - Native\n"
                    "- **Owned files**: `runner.py`\n"
                    "- **Interfaces provided**: `IF-0-NATIVE-1`\n"
                ),
            )

            def fake_launch(spec, **_kwargs):
                payload = {
                    "terminal_status": "complete",
                    "verification_status": "passed",
                    "dirty_paths": [],
                    "produced_if_gates": [],
                }
                return LaunchResult(command=spec.command, returncode=0, output=json.dumps(payload), executor=spec.executor)

            for executor in ("codex", "claude", "gemini", "opencode", "pi"):
                with self.subTest(executor=executor), patch(
                    "phase_loop_runtime.runner.run_auth_preflight", return_value=AuthPreflightResult(ok=True, metadata={})
                ), patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                    snapshot, _results = run_loop(repo, roadmap, phase="RUNNER", executor=executor, action="repair")

                self.assertEqual(snapshot.phases["RUNNER"], "blocked")
                self.assertEqual(snapshot.blocker_class, "contract_bug")
                self.assertIn("zero produced_if_gates", snapshot.blocker_summary)


if __name__ == "__main__":
    unittest.main()
