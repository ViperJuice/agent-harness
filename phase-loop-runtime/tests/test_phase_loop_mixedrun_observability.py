from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.launcher import build_launch_request, build_launch_spec
from phase_loop_runtime.observability import run_artifacts
from phase_loop_runtime.profiles import resolve_profile
from phase_loop_runtime.prompts import build_prompt
from phase_loop_runtime.handoff import write_tui_handoff
from phase_loop_runtime.models import DelegationBudget, DelegationRequest, StateSnapshot, utc_now
from phase_loop_runtime.provenance import snapshot_provenance
from phase_loop_runtime.runner import launch_delegated_child
from phase_loop_runtime.state_ops import inspect_state
from phase_loop_test_utils import make_repo, write_phase_plan

import pytest

# TESTDECOUPLE SL-1 (overlay-dependent): builds a skill/adoption bundle or runs the
# runtime execute path, which resolves the dotfiles skill-source / profile overlay
# (claude-config/*, codex-config/* …) absent standalone. Run-time integration: the
# conftest hook skips it when no dotfiles tree is reachable.
pytestmark = pytest.mark.dotfiles_integration


class PhaseLoopMixedRunObservabilityTest(unittest.TestCase):
    def test_claude_team_launch_writes_task_snapshot_and_hook_inventory(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(
                repo,
                "MIXEDRUN",
                roadmap,
                body=(
                    "# MIXEDRUN\n\n"
                    "## Lanes\n\n"
                    "### SL-0 - One\n"
                    "- **Owned files**: `src/one.py`\n\n"
                    "### SL-1 - Two\n"
                    "- **Owned files**: `src/two.py`\n"
                ),
            )
            request = build_launch_request(
                executor="claude",
                action="execute",
                repo=repo,
                roadmap=roadmap,
                phase="MIXEDRUN",
                plan=plan,
                model_selection=resolve_profile("execute"),
                prompt_bundle=build_prompt("execute", roadmap, phase="MIXEDRUN", plan=plan, harness_target="claude"),
                json_output=False,
                bypass_approvals=False,
                claude_execution_mode="agent_team",
            )
            spec = build_launch_spec(request)

            artifacts = run_artifacts(repo, "MIXEDRUN", "execute", 1, spec)
            metadata = json.loads(artifacts["metadata"].read_text(encoding="utf-8"))
            snapshot = json.loads(artifacts["task_snapshot"].read_text(encoding="utf-8"))
            hook_manifest = json.loads(artifacts["hook_manifest"].read_text(encoding="utf-8"))

            self.assertEqual(metadata["task_ledger_artifacts"]["snapshot_path"], str(artifacts["task_snapshot"]))
            self.assertEqual(snapshot["execution_mode"], "agent_team")
            self.assertEqual(snapshot["latest_activity"]["classification"], "claude_agent_team_active")
            self.assertEqual(snapshot["tasks"][0]["ownership_claims"], ["src/one.py"])
            self.assertEqual(snapshot["teammates"][1]["teammate_label"], "SL-1 - Two")
            self.assertEqual(
                [record["event_name"] for record in hook_manifest["hook_policy_inventory"]],
                ["TaskCreated", "TaskCompleted", "TeammateIdle", "SubagentStop", "PostToolBatch", "WorktreeCreate"],
            )

    def test_monitor_and_handoff_surface_selection_path_and_lineage(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "MIXEDRUN", roadmap, owned_files=("notes.md",))
            request = DelegationRequest(
                request_id="req-observe",
                product_action="review",
                target_executor="claude",
                reason="Need a cross-executor review pass.",
                owned_files=("notes.md",),
                expected_output="Review findings",
                budget=DelegationBudget(max_seconds=30),
            )
            launch_delegated_child(
                repo=repo,
                roadmap=roadmap,
                parent_phase="MIXEDRUN",
                parent_action="execute",
                parent_executor="codex",
                plan=plan,
                request=request,
                parent_run_id="run-parent-observe",
                dry_run=True,
            )

            summary = inspect_state(repo, roadmap)
            self.assertEqual(summary["monitor_status"]["selected_executor"], "claude")
            self.assertEqual(summary["monitor_status"]["selected_via"], "preferred")
            self.assertEqual(summary["monitor_status"]["considered_executors"], ["claude"])
            self.assertEqual(summary["monitor_status"]["delegation_lineage"]["parent_executor"], "codex")
            self.assertEqual(summary["monitor_status"]["delegation_lineage"]["child_worktree_root"], str(repo.resolve()))
            self.assertEqual(summary["monitor_status"]["delegation_lineage"]["child_closeout_result"]["status"], "planned")

            snapshot = StateSnapshot(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phases={"MIXEDRUN": "planned"},
                current_phase="MIXEDRUN",
                **snapshot_provenance(roadmap),
            )
            handoff = write_tui_handoff(repo, roadmap, snapshot, action="status").read_text(encoding="utf-8")
            self.assertIn("dispatch path: `preferred`", handoff)
            self.assertIn("considered executors: `claude`", handoff)
            self.assertIn("parent executor: `codex`", handoff)
            self.assertIn("child artifacts:", handoff)
            self.assertIn("child worktree:", handoff)
            self.assertIn("child closeout:", handoff)

    def test_denied_delegation_surfaces_reason_code_in_monitor_and_handoff(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "MIXEDRUN", roadmap, owned_files=("notes.md",))
            request = DelegationRequest(
                request_id="req-denied-monitor",
                product_action="execute",
                target_executor="manual",
                reason="Manual executor should be rejected for autonomous execute.",
                owned_files=("notes.md",),
                expected_output="Execution output",
                budget=DelegationBudget(max_seconds=30),
            )
            launch_delegated_child(
                repo=repo,
                roadmap=roadmap,
                parent_phase="MIXEDRUN",
                parent_action="execute",
                parent_executor="codex",
                plan=plan,
                request=request,
                dry_run=False,
            )

            summary = inspect_state(repo, roadmap)
            self.assertEqual(summary["monitor_status"]["delegation"]["status"], "denied")
            self.assertEqual(summary["monitor_status"]["delegation"]["reason_code"], "unsupported_target_executor")

            snapshot = StateSnapshot(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phases={"MIXEDRUN": "blocked"},
                current_phase="MIXEDRUN",
                **snapshot_provenance(roadmap),
            )
            handoff = write_tui_handoff(repo, roadmap, snapshot, action="status").read_text(encoding="utf-8")
            self.assertIn("decision code: `unsupported_target_executor`", handoff)

    def test_missing_task_snapshot_fails_closed_in_monitor_state(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(
                repo,
                "MIXEDRUN",
                roadmap,
                body=(
                    "# MIXEDRUN\n\n"
                    "## Lanes\n\n"
                    "### SL-0 - One\n"
                    "- **Owned files**: `src/one.py`\n"
                ),
            )
            request = build_launch_request(
                executor="claude",
                action="execute",
                repo=repo,
                roadmap=roadmap,
                phase="MIXEDRUN",
                plan=plan,
                model_selection=resolve_profile("execute"),
                prompt_bundle=build_prompt("execute", roadmap, phase="MIXEDRUN", plan=plan, harness_target="claude"),
                json_output=False,
                bypass_approvals=False,
                claude_execution_mode="agent_team",
            )
            spec = build_launch_spec(request)
            artifacts = run_artifacts(repo, "MIXEDRUN", "execute", 1, spec)
            artifacts["task_snapshot"].unlink()

            summary = inspect_state(repo, roadmap)

            self.assertEqual(summary["monitor_status"]["task_snapshot_freshness"], "missing")
            self.assertEqual(summary["monitor_status"]["wait_classification"], "team_state_unavailable")


if __name__ == "__main__":
    unittest.main()
