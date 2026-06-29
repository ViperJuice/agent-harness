"""model-routing-v2 P4 — end-to-end governed flow (mocked panel; NO live frontier calls).

Composes the real governed helpers — `governed_planning_gate` (plan stage) and
`run_governed_premerge_loop` (pre-merge stage) — through a full governed phase
lifecycle: plan-gate promote → execute (the produced bundle) → pre-merge review
→ fix round → mergeable, plus the non-convergence terminal. The panel is
injected; the autonomous path is asserted untouched.
"""
import unittest
from unittest.mock import Mock

from phase_loop_runtime.governed_review import GateResult, governed_planning_gate
from phase_loop_runtime.governed_premerge import run_governed_premerge_loop
from phase_loop_runtime.panel_invoker import PanelLegResult, PanelResult
from phase_loop_runtime.closeout_validators import ReviewFinding


def _panel(*legs):
    return PanelResult(legs=tuple(legs))


def _approving():
    return _panel(
        PanelLegResult(leg="codex", status="ok", text="AGREE"),
        PanelLegResult(leg="gemini", status="ok", text="AGREE"),
    )


def _premerge_gate(promoted, *, block=False):
    findings = (ReviewFinding(code="x", reason="r", severity="block"),) if block else ()
    return GateResult(ran=True, promoted=promoted, findings=findings)


class GovernedEndToEndTest(unittest.TestCase):
    def test_full_governed_flow_plan_then_premerge_block_fix_pass(self):
        # 1. PLAN GATE — panel approves the plan → promoted, proceed to execute.
        plan = governed_planning_gate(
            artifact="plan-bundle", author_executor="claude", run_mode="governed",
            available_legs=("codex", "gemini"), invoke=lambda art, pool, spawn=None: _approving(),
        )
        self.assertTrue(plan.ran and plan.promoted)

        # 2. EXECUTE — the phase produces an implementation diff (the bundle).
        #    (real executor; here represented by the bundle artifact.)

        # 3. PRE-MERGE — round1 block → fix re-dispatch → round2 clean → mergeable.
        seq = [_premerge_gate(False, block=True), _premerge_gate(True)]
        fixes = []
        res = run_governed_premerge_loop(
            artifact="impl-bundle-v0", author_executor="claude", run_mode="governed",
            apply_fix=lambda rnd, cur, find: (fixes.append(rnd), "impl-bundle-v1")[1],
            available_legs=("codex", "gemini"), invoke=lambda **kw: seq.pop(0),
        )
        self.assertTrue(res.mergeable and res.ran)
        self.assertEqual(res.rounds, 2)
        self.assertEqual(fixes, [1])  # exactly one fix re-dispatch

    def test_full_governed_flow_non_convergence_is_non_human_terminal(self):
        plan = governed_planning_gate(
            artifact="plan-bundle", author_executor="claude", run_mode="governed",
            available_legs=("codex", "gemini"), invoke=lambda art, pool, spawn=None: _approving(),
        )
        self.assertTrue(plan.promoted)
        res = run_governed_premerge_loop(
            artifact="impl", author_executor="claude", run_mode="governed",
            apply_fix=lambda rnd, cur, find: "impl2",
            available_legs=("codex", "gemini"),
            invoke=lambda **kw: _premerge_gate(False, block=True),  # never converges
            max_rounds=3,
        )
        self.assertFalse(res.mergeable)
        self.assertIsNotNone(res.terminal_blocker)
        self.assertFalse(res.terminal_blocker["human_required"])
        self.assertEqual(res.terminal_blocker["blocker_class"], "review_gate_block")

    def test_autonomous_lifecycle_spawns_no_panel(self):
        plan_invoke, premerge_invoke = Mock(), Mock()
        plan = governed_planning_gate(
            artifact="plan", author_executor="claude", run_mode="autonomous", invoke=plan_invoke,
        )
        res = run_governed_premerge_loop(
            artifact="impl", author_executor="claude", run_mode="autonomous", invoke=premerge_invoke,
        )
        self.assertFalse(plan.ran)          # plan gate short-circuits
        self.assertTrue(plan.promoted)
        self.assertTrue(res.mergeable and not res.ran)
        plan_invoke.assert_not_called()     # ZERO panel spawn end to end on the default path
        premerge_invoke.assert_not_called()


if __name__ == "__main__":
    unittest.main()
