import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.events import read_events
from phase_loop_runtime.launcher import LaunchResult
from phase_loop_runtime.runner import run_loop
from phase_loop_test_utils import build_fake_automation_output, commit_fixture_paths, make_repo, write_phase_plan

import pytest

# TESTDECOUPLE SL-1 (overlay-dependent): builds a skill/adoption bundle or runs the
# runtime execute path, which resolves the dotfiles skill-source / profile overlay
# (claude-config/*, codex-config/* …) absent standalone. Run-time integration: the
# conftest hook skips it when no dotfiles tree is reachable.
pytestmark = pytest.mark.dotfiles_integration


class WorkerPoolFailureIsolationTest(unittest.TestCase):
    def test_worker_failure_records_blocked_phase_and_sibling_completion(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.write_text(
                textwrap.dedent(
                    """
                    # Roadmap

                    ### Phase 1 - Alpha (A)
                    **Depends on**
                    - (none)

                    ---

                    ### Phase 2 - Beta (B)
                    **Depends on**
                    - (none)

                    ---

                    ### Phase 3 - Gamma (C)
                    **Depends on**
                    - A
                    - B
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            plans = tuple(write_phase_plan(repo, phase, roadmap) for phase in ("A", "B", "C"))
            commit_fixture_paths(repo, "add workerpool plans", roadmap, *plans)

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                phase = "A" if "phase-plan-v1-A.md" in spec.prompt_bundle.render_prompt() else "B"
                status = "blocked" if phase == "A" else "complete"
                return LaunchResult(
                    command=spec.command,
                    returncode=0,
                    output=build_fake_automation_output(
                        status=status,
                        verification_status="blocked" if status == "blocked" else "passed",
                        blocker_class="repeated_verification_failure" if status == "blocked" else "none",
                        blocker_summary="fixture blocker" if status == "blocked" else "none",
                        artifact=str(repo / "plans" / f"phase-plan-v1-{phase}.md"),
                        artifact_state="tracked",
                    ),
                    executor=spec.executor,
                    log_path=str(log_path) if log_path else None,
                )

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                snapshot, results = run_loop(repo, roadmap, parallel_dispatch=True)

            self.assertEqual(len(results), 2)
            self.assertEqual(snapshot.phases["A"], "blocked")
            self.assertEqual(snapshot.phases["B"], "complete")
            self.assertNotEqual(snapshot.phases.get("C"), "complete")
            actions = [event["action"] for event in read_events(repo)]
            self.assertIn("coordinator.worker_dispatched", actions)
            self.assertIn("coordinator.worker_completed", actions)


if __name__ == "__main__":
    unittest.main()
