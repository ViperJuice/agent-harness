"""agent-harness#244/#245 (roadmap IF-0-PAR-1): preflight/closeout gate parity.

#244 — the execute-time preflight gates (verification-evidence + acceptance/
goal-coverage) were wired at a single call site on the direct execute-launch
path (runner.py, ~launch_action == "execute"), AFTER the lane-scheduler and
work-unit dispatch branches (runner.py, ~lane_scheduler_mode/work_unit_mode)
already returned. Both modes fired before the gates ran, so
PHASE_LOOP_VERIFY_ENFORCE=hard / PHASE_LOOP_ACCEPTANCE_ENFORCE=block were
silently inert for lane-scheduler and work-unit runs.

#245 — the closeout gates (produced-gates + goal-coverage) were re-checked at a
single site immediately before a phase's ``automation_status`` is known to be
``"delegated"`` (so both gates trivially pass — the status isn't "complete"
yet). When ``launch_delegated_child`` later reduces the delegated child to a
terminal status (including "complete"), NEITHER gate re-ran.

The fix factors each gate family into ONE shared helper
(``_execute_dispatch_preflight_gates`` for #244, ``_closeout_gate_recheck`` for
#245) that every path — direct, lane-scheduler, work-unit, delegated-child —
routes through, instead of re-deriving the gate calls per path. These tests
pin: (a) the lane-scheduler/work-unit dispatch now fires the preflight gate;
(b) a delegated-child completion re-checks both closeout gates; (c) the direct
and lane/work-unit paths funnel through the SAME preflight helper, and the
direct and delegated paths funnel through the SAME closeout helper.

Deliberately UNMARKED (no ``dotfiles_integration``): every scenario here
returns from its dispatch branch BEFORE ``build_prompt`` resolves a skill
bundle (the preflight gates fire ahead of it; the closeout scenarios patch
``build_prompt``/``launch_with_spec``/``launch_delegated_child`` directly), so
none of it depends on a dotfiles fleet tree.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.launcher import LaunchResult
from phase_loop_runtime.models import PromptBundle
from phase_loop_runtime.runner import run_loop
from phase_loop_test_utils import (
    build_fake_delegation_request,
    commit_fixture_paths,
    make_repo,
    write_phase_plan,
)


def _clean_plan(repo: Path, roadmap: Path, *, produces: str | None = None) -> Path:
    body = "# RUNNER\n\n"
    if produces:
        body += f"**Produces**: {produces}\n\n"
    body += "## Verification\n" f"- `{sys.executable} -c \"print('verify')\"`\n"
    plan = write_phase_plan(repo, "RUNNER", roadmap, body=body)
    commit_fixture_paths(repo, "add plan", roadmap, plan)
    return plan


def _goal_coverage_roadmap(repo: Path) -> Path:
    roadmap = repo / "specs" / "phase-plans-v1.md"
    roadmap.write_text(
        "# Roadmap\n\n## Context\nx\n\n## Phases\n\n### Phase 1 — Runner (RUNNER)\n\n"
        "**Objective**\nx\n\n**Exit criteria**\n- [ ] EC-RUNNER-1 — Do the thing.\n\n"
        "**Key files**\n- `x.py`\n\n**Scope notes**\nx\n\n**Depends on**\n- (none)\n\n"
        "## Top Interface-Freeze Gates\n\n## Phase Dependency DAG\nRUNNER\n\n"
        "## Execution Notes\nx\n\n## Verification\nx\n",
        encoding="utf-8",
    )
    return roadmap


class DispatchPreflightGateParityTest(unittest.TestCase):
    """agent-harness#244: lane-scheduler / work-unit dispatch now hits the
    shared execute-time preflight choke point instead of bypassing it."""

    def _plain_roadmap_and_plan(self, repo: Path) -> tuple[Path, Path]:
        roadmap = repo / "specs" / "phase-plans-v1.md"
        roadmap.write_text("# Roadmap\n\n### Phase 0 - Runner (RUNNER)\n", encoding="utf-8")
        plan = write_phase_plan(
            repo, "RUNNER", roadmap,
            body="# RUNNER\n\n## Verification\n" f"- `{sys.executable} -c \"print('verify')\"`\n",
        )
        commit_fixture_paths(repo, "add plan", roadmap, plan)
        return roadmap, plan

    def _wave_roadmap_and_plan(self, repo: Path) -> tuple[Path, Path]:
        roadmap = repo / "specs" / "phase-plans-v1.md"
        roadmap.write_text("# Roadmap\n\n### Phase 0 - Wave (WAVESCHED)\n", encoding="utf-8")
        body = (
            "# WAVESCHED\n\n"
            "## Lane Index & Dependencies\n\n"
            "- SL-0 - Producer; Depends on: (none); Blocks: (none); Parallel-safe: yes\n\n"
            "## Lanes\n\n"
            "### SL-0 - Producer\n"
            "- **Owned files**: `producer.py`\n"
            "- **Interfaces provided**: `producer.out`\n"
            "- **Interfaces consumed**: none\n"
            "- **Parallel-safe**: yes\n\n"
            "## Verification\n"
            f"- `{sys.executable} -c \"print('verify')\"`\n"
        )
        plan = write_phase_plan(repo, "WAVESCHED", roadmap, body=body)
        commit_fixture_paths(repo, "add plan", roadmap, plan)
        return roadmap, plan

    def test_lane_scheduler_mode_fires_verification_preflight_gate(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"PHASE_LOOP_VERIFY_ENFORCE": "hard"}):
            repo = make_repo(Path(td))
            roadmap, _plan = self._plain_roadmap_and_plan(repo)

            with patch("phase_loop_runtime.runner.launch_with_spec") as fake_launch:
                snapshot, results = run_loop(
                    repo, roadmap, phase="RUNNER", lane_scheduler_mode="serialized",
                )

            self.assertFalse(fake_launch.called)
            self.assertEqual(results, [])
            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            self.assertEqual(snapshot.blocker_class, "verification_evidence_missing")

    def test_work_unit_mode_fires_verification_preflight_gate(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"PHASE_LOOP_VERIFY_ENFORCE": "hard"}):
            repo = make_repo(Path(td))
            roadmap, _plan = self._plain_roadmap_and_plan(repo)

            with patch("phase_loop_runtime.runner.launch_with_spec") as fake_launch:
                snapshot, results = run_loop(
                    repo, roadmap, phase="RUNNER", work_unit_mode=True,
                )

            self.assertFalse(fake_launch.called)
            self.assertEqual(results, [])
            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            self.assertEqual(snapshot.blocker_class, "verification_evidence_missing")

    def test_lane_scheduler_dry_run_still_bypasses_the_choke_point(self):
        """Safety check: the new choke point is gated on ``not dry_run`` (matching
        the direct path's own ``not dry_run and launch_action == "execute"``
        guard), so an existing dry-run preview of a ready lane wave is
        unaffected by #244's fix."""
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"PHASE_LOOP_VERIFY_ENFORCE": "hard"}):
            repo = make_repo(Path(td))
            roadmap, _plan = self._wave_roadmap_and_plan(repo)

            snapshot, results = run_loop(
                repo, roadmap, phase="WAVESCHED", dry_run=True, lane_scheduler_mode="serialized",
            )

            self.assertEqual(results, [])
            self.assertNotEqual(snapshot.phases["WAVESCHED"], "blocked")

    def test_direct_and_dispatch_paths_share_single_preflight_helper(self):
        """agent-harness#244/#247: both the direct execute-launch site and the
        lane-scheduler dispatch branch must route through the SAME
        ``_execute_dispatch_preflight_gates`` helper rather than re-deriving the
        gate calls — pin the single invocation point directly."""
        calls: list[str] = []

        def _fake_preflight(repo, roadmap, plan):
            calls.append(str(plan))
            return (
                {
                    "human_required": False,
                    "blocker_class": "contract_bug",
                    "blocker_summary": "fake preflight block",
                    "required_human_inputs": (),
                    "access_attempts": (),
                },
                "verification_preflight",
            )

        with tempfile.TemporaryDirectory() as td:
            direct_repo = make_repo(Path(td) / "direct")
            direct_roadmap, _direct_plan = self._plain_roadmap_and_plan(direct_repo)
            lane_repo = make_repo(Path(td) / "lane")
            lane_roadmap, _lane_plan = self._plain_roadmap_and_plan(lane_repo)

            with patch(
                "phase_loop_runtime.runner._execute_dispatch_preflight_gates",
                side_effect=_fake_preflight,
            ) as fake_gate:
                direct_snapshot, _ = run_loop(direct_repo, direct_roadmap, phase="RUNNER", executor="codex")
                lane_snapshot, _ = run_loop(lane_repo, lane_roadmap, phase="RUNNER", lane_scheduler_mode="serialized")

            self.assertEqual(fake_gate.call_count, 2)
            self.assertEqual(direct_snapshot.phases["RUNNER"], "blocked")
            self.assertEqual(lane_snapshot.phases["RUNNER"], "blocked")
            self.assertEqual(direct_snapshot.blocker_summary, "fake preflight block")
            self.assertEqual(lane_snapshot.blocker_summary, "fake preflight block")


class DelegatedChildCloseoutGateParityTest(unittest.TestCase):
    """agent-harness#245: a delegated child that reduces to a terminal status
    re-checks BOTH closeout gates (produced-gates + goal-coverage) exactly as
    the direct closeout site does."""

    def _fake_build_prompt(self, *args, **kwargs) -> PromptBundle:
        return PromptBundle(workflow_command="execute", body="stub", injection_mode="context_file")

    def _run_delegated(self, repo: Path, roadmap: Path, plan: Path, *, closeout_result: dict, mutate=None):
        delegation_request = build_fake_delegation_request(request_id="req-1", target_executor="codex")

        def _fake_parsed_child_automation(result, spec):
            return {"automation_status": "delegated", "delegation_request": delegation_request}

        def _fake_launch_delegated_child(**kwargs):
            if mutate is not None:
                mutate()
            return {"launch_metadata": {"parent_child": {"child_closeout_result": dict(closeout_result)}}}

        with patch("phase_loop_runtime.runner.build_prompt", side_effect=self._fake_build_prompt), \
             patch(
                 "phase_loop_runtime.runner.launch_with_spec",
                 return_value=LaunchResult(command=["codex", "exec"], returncode=0, output="", executor="codex"),
             ), \
             patch(
                 "phase_loop_runtime.runner._parsed_child_automation",
                 side_effect=_fake_parsed_child_automation,
             ), \
             patch(
                 "phase_loop_runtime.runner.launch_delegated_child",
                 side_effect=_fake_launch_delegated_child,
             ) as fake_delegated:
            snapshot, results = run_loop(repo, roadmap, phase="RUNNER", executor="codex")
        return snapshot, results, fake_delegated

    def test_delegated_completion_recheck_blocks_goal_coverage_gap(self):
        """The plan is CLEAN (passes the plan-time preflight); the mutation
        happens DURING the delegated child's execution (simulated inside the
        ``launch_delegated_child`` fake) — the exact mutation window the
        goal-coverage closeout re-check exists to close (#211)."""
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"PHASE_LOOP_ACCEPTANCE_ENFORCE": "block"}):
            repo = make_repo(Path(td))
            roadmap = _goal_coverage_roadmap(repo)
            plan = write_phase_plan(
                repo, "RUNNER", roadmap,
                body=(
                    "# RUNNER\n\n## Acceptance Criteria\n"
                    "- [ ] EC-RUNNER-1 — proven by test.\n\n"
                    "## Verification\n" f"- `{sys.executable} -c \"print('verify')\"`\n"
                ),
            )
            commit_fixture_paths(repo, "add plan", roadmap, plan)

            def _drop_goal_reference():
                text = plan.read_text(encoding="utf-8")
                text = text.replace("- [ ] EC-RUNNER-1 — proven by test.\n", "- [ ] unrelated item.\n")
                plan.write_text(text, encoding="utf-8")

            snapshot, results, fake_delegated = self._run_delegated(
                repo, roadmap, plan,
                closeout_result={"status": "complete"},
                mutate=_drop_goal_reference,
            )

            self.assertTrue(fake_delegated.called, "the delegated-child launch must actually run")
            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            self.assertEqual(snapshot.blocker_class, "contract_bug")
            self.assertIn("goal-coverage", (snapshot.blocker_summary or "").lower())

    def test_delegated_completion_recheck_blocks_missing_produced_gates(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.write_text("# Roadmap\n\n### Phase 0 - Runner (RUNNER)\n", encoding="utf-8")
            plan = _clean_plan(repo, roadmap, produces="IF-0-TEST-1")

            snapshot, results, fake_delegated = self._run_delegated(
                repo, roadmap, plan,
                closeout_result={"status": "complete", "produced_if_gates": []},
            )

            self.assertTrue(fake_delegated.called, "the delegated-child launch must actually run")
            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            self.assertEqual(snapshot.blocker_class, "contract_bug")
            self.assertIn("produced_if_gates", snapshot.blocker_summary or "")

    def test_delegated_completion_with_satisfied_gates_is_not_blocked(self):
        """Negative control: a delegated child that legitimately satisfies both
        gates must NOT be blocked by the new re-check (warn-default posture,
        no new false positives)."""
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.write_text("# Roadmap\n\n### Phase 0 - Runner (RUNNER)\n", encoding="utf-8")
            plan = _clean_plan(repo, roadmap)

            snapshot, results, fake_delegated = self._run_delegated(
                repo, roadmap, plan,
                closeout_result={"status": "complete"},
            )

            self.assertTrue(fake_delegated.called)
            self.assertNotEqual(snapshot.phases["RUNNER"], "blocked")

    def test_direct_and_delegated_paths_share_single_closeout_helper(self):
        """agent-harness#245/#247: pin that the delegated-child completion
        routes through the SAME ``_closeout_gate_recheck`` helper as the direct
        launch-result reduction, rather than a second, drift-prone copy."""
        calls: list[object] = []

        from phase_loop_runtime.runner import _CloseoutGateOutcome

        def _fake_recheck_outcome(repo, roadmap, plan, child_automation, automation_status, event_blocker):
            calls.append(automation_status)
            return _CloseoutGateOutcome(automation_status, event_blocker, None, (), ())

        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.write_text("# Roadmap\n\n### Phase 0 - Runner (RUNNER)\n", encoding="utf-8")
            plan = _clean_plan(repo, roadmap)

            with patch(
                "phase_loop_runtime.runner._closeout_gate_recheck",
                side_effect=_fake_recheck_outcome,
            ) as fake_recheck:
                snapshot, results, fake_delegated = self._run_delegated(
                    repo, roadmap, plan,
                    closeout_result={"status": "complete"},
                )

            self.assertTrue(fake_delegated.called)
            # One call for the (pre-delegation) direct reduction site, seeing
            # automation_status == "delegated"; one call for the post-delegation
            # completion re-check, seeing automation_status == "complete".
            self.assertEqual(fake_recheck.call_count, 2)
            self.assertIn("delegated", calls)
            self.assertIn("complete", calls)


if __name__ == "__main__":
    unittest.main()
