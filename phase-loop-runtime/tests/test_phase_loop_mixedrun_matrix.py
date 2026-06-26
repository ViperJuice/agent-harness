from __future__ import annotations

import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.capability_registry import DEFAULT_CAPABILITY_REGISTRY
from phase_loop_runtime.launcher import LaunchResult
from phase_loop_runtime.models import DelegationBudget, DelegationRequest
from phase_loop_runtime.runner import launch_delegated_child, run_loop
from phase_loop_test_utils import make_repo, write_phase_plan


AUTOMATION_OUTPUT = (
    "automation:\n"
    "  status: executed\n"
    "  next_skill: none\n"
    "  next_command: none\n"
    "  next_model_hint: none\n"
    "  next_effort_hint: none\n"
    "  human_required: false\n"
    "  blocker_class: none\n"
    "  blocker_summary: none\n"
    "  required_human_inputs: []\n"
    "  verification_status: passed\n"
    "  artifact: none\n"
    "  artifact_state: none\n"
)

import pytest

# TESTDECOUPLE SL-1 (overlay-dependent): builds a skill/adoption bundle or runs the
# runtime execute path, which resolves the dotfiles skill-source / profile overlay
# (claude-config/*, codex-config/* …) absent standalone. Run-time integration: the
# conftest hook skips it when no dotfiles tree is reachable.
pytestmark = pytest.mark.dotfiles_integration


class PhaseLoopMixedRunMatrixTest(unittest.TestCase):
    def test_preferred_executor_can_fallback_to_another_live_executor(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.write_text(
                roadmap.read_text(encoding="utf-8")
                + "\n## Dispatch Hints\n"
                + "- execute preferred executors: `claude`\n"
                + "- execute fallback executors: `gemini`\n",
                encoding="utf-8",
            )
            plan = write_phase_plan(repo, "MIXEDRUN", roadmap)
            subprocess.run(["git", "add", str(plan.relative_to(repo)), str(roadmap.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "fixture mixedrun matrix"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            patched_registry = dict(DEFAULT_CAPABILITY_REGISTRY)
            patched_registry["claude"] = replace(DEFAULT_CAPABILITY_REGISTRY["claude"], live_available=False)

            with patch("phase_loop_runtime.capability_registry.DEFAULT_CAPABILITY_REGISTRY", patched_registry), patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
            ), patch(
                "phase_loop_runtime.runner.launch_with_spec",
                return_value=LaunchResult(command=["gemini", "-p"], returncode=0, output=AUTOMATION_OUTPUT, executor="gemini"),
            ):
                _snapshot, results = run_loop(repo, roadmap, phase="MIXEDRUN")

            self.assertEqual(results[0].executor, "gemini")

    def test_child_run_can_launch_from_codex_parent_to_claude_child(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "MIXEDRUN", roadmap, owned_files=("notes.md",))
            request = DelegationRequest(
                request_id="req-matrix-cross",
                product_action="review",
                target_executor="claude",
                reason="Cross-executor review proof.",
                owned_files=("notes.md",),
                expected_output="Review findings",
                budget=DelegationBudget(max_seconds=30),
            )
            outcome = launch_delegated_child(
                repo=repo,
                roadmap=roadmap,
                parent_phase="MIXEDRUN",
                parent_action="execute",
                parent_executor="codex",
                plan=plan,
                request=request,
                dry_run=True,
            )

            self.assertEqual(outcome["launch_metadata"]["parent_child"]["parent_executor"], "codex")
            self.assertEqual(outcome["launch_metadata"]["parent_child"]["child_executor"], "claude")
            self.assertEqual(outcome["launch_metadata"]["parent_child"]["child_worktree_root"], str(repo.resolve()))

    def test_child_run_can_launch_from_claude_parent_to_codex_child(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "MIXEDRUN", roadmap, owned_files=("notes.md",))
            request = DelegationRequest(
                request_id="req-matrix-claude-to-codex",
                product_action="repair",
                target_executor="codex",
                reason="Claude parent needs a codex repair pass.",
                owned_files=("notes.md",),
                expected_output="Repair summary",
                budget=DelegationBudget(max_seconds=30),
            )
            outcome = launch_delegated_child(
                repo=repo,
                roadmap=roadmap,
                parent_phase="MIXEDRUN",
                parent_action="execute",
                parent_executor="claude",
                plan=plan,
                request=request,
                dry_run=True,
            )

            self.assertEqual(outcome["decision"]["status"], "approved")
            self.assertEqual(outcome["launch_metadata"]["parent_child"]["parent_executor"], "claude")
            self.assertEqual(outcome["launch_metadata"]["parent_child"]["child_executor"], "codex")

    def test_child_run_can_launch_from_claude_parent_to_claude_child(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "MIXEDRUN", roadmap, owned_files=("notes.md",))
            request = DelegationRequest(
                request_id="req-matrix-claude-to-claude",
                product_action="review",
                target_executor="claude",
                reason="Claude parent requests a bounded claude review child.",
                owned_files=("notes.md",),
                expected_output="Review findings",
                budget=DelegationBudget(max_seconds=30),
            )
            outcome = launch_delegated_child(
                repo=repo,
                roadmap=roadmap,
                parent_phase="MIXEDRUN",
                parent_action="execute",
                parent_executor="claude",
                plan=plan,
                request=request,
                dry_run=True,
            )

            self.assertEqual(outcome["decision"]["status"], "approved")
            self.assertEqual(outcome["launch_metadata"]["parent_child"]["parent_executor"], "claude")
            self.assertEqual(outcome["launch_metadata"]["parent_child"]["child_executor"], "claude")

    def test_policy_rejected_executor_stays_typed_and_deterministic(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "MIXEDRUN", roadmap, owned_files=("notes.md",))
            request = DelegationRequest(
                request_id="req-matrix-denied",
                product_action="execute",
                target_executor="manual",
                reason="This should fail closed under dispatch policy.",
                owned_files=("notes.md",),
                expected_output="Execution output",
                budget=DelegationBudget(max_seconds=30),
            )
            outcome = launch_delegated_child(
                repo=repo,
                roadmap=roadmap,
                parent_phase="MIXEDRUN",
                parent_action="execute",
                parent_executor="codex",
                plan=plan,
                request=request,
                dry_run=False,
            )

            self.assertEqual(outcome["decision"]["status"], "denied")
            self.assertEqual(outcome["decision"]["reason_code"], "unsupported_target_executor")
            self.assertEqual(outcome["terminal_summary"]["terminal_status"], "blocked")


if __name__ == "__main__":
    unittest.main()
