"""model-routing-v1 P3 — escalation ladder + bounded governed pre-merge loop."""
import unittest
from unittest.mock import Mock

from phase_loop_runtime.closeout_validators import ReviewFinding
from phase_loop_runtime.governed_review import GateResult
from phase_loop_runtime.governed_premerge import (
    DEFAULT_MAX_REVIEW_ROUNDS,
    next_escalation,
    run_governed_premerge_loop,
)
from phase_loop_runtime.profiles import resolve_model_class, shipped_model_policy_rule
from phase_loop_runtime.runner import governed_premerge_for_run


def _gate(*, promoted=True, degraded=False, block=False, reason=None):
    findings = (
        ReviewFinding(code="panel_block", reason="x", severity="block", blocker_class="review_gate_block"),
    ) if block else (ReviewFinding(code="panel_nit", reason="n", severity="warn"),)
    return GateResult(ran=True, promoted=promoted, findings=findings, degraded=degraded, reason=reason)


def _scripted_invoke(results):
    seq = iter(results)
    def invoke(**kwargs):
        return next(seq)
    return invoke


class ImplementerDispatchTest(unittest.TestCase):
    def test_execute_resolves_to_implementer(self):
        self.assertEqual(shipped_model_policy_rule("execute").model_class, "implementer")
        self.assertEqual(resolve_model_class("claude", "implementer"), "claude-sonnet-4-6")
        self.assertEqual(resolve_model_class("codex", "implementer"), "gpt-5.4")


class EscalationLadderTest(unittest.TestCase):
    def test_below_threshold_retries(self):
        d = next_escalation(model_class="implementer", failed_tests=1)
        self.assertEqual(d.action, "retry")
        self.assertEqual(d.model_class, "implementer")

    def test_implementer_escalates_to_planner(self):
        d = next_escalation(model_class="implementer", failed_tests=2)
        self.assertEqual(d.action, "escalate_class")
        self.assertEqual(d.model_class, "planner")

    def test_planner_failing_governed_invokes_panel(self):
        d = next_escalation(model_class="planner", patch_retries=2, run_mode="governed")
        self.assertEqual(d.action, "invoke_panel")

    def test_planner_failing_autonomous_is_non_human_terminal(self):
        d = next_escalation(model_class="planner", failed_tests=2, run_mode="autonomous")
        self.assertEqual(d.action, "terminal_blocker")
        self.assertFalse(d.blocker["human_required"])
        self.assertEqual(d.blocker["blocker_class"], "repeated_verification_failure")


class PremergeLoopTest(unittest.TestCase):
    def test_autonomous_is_noop_and_makes_zero_panel_calls(self):
        invoke = Mock()
        r = run_governed_premerge_loop(artifact="A", author_executor="claude",
                                       run_mode="autonomous", invoke=invoke)
        self.assertTrue(r.mergeable)
        self.assertFalse(r.ran)
        invoke.assert_not_called()

    def test_clean_first_round_is_mergeable(self):
        r = run_governed_premerge_loop(artifact="A", author_executor="claude", run_mode="governed",
                                       invoke=_scripted_invoke([_gate(promoted=True)]))
        self.assertTrue(r.mergeable)
        self.assertEqual(r.rounds, 1)

    def test_block_then_fix_then_clean(self):
        fixes = []
        def apply_fix(rnd, art, findings):
            fixes.append(rnd)
            return art + f".fix{rnd}"
        r = run_governed_premerge_loop(
            artifact="A", author_executor="claude", run_mode="governed",
            apply_fix=apply_fix,
            invoke=_scripted_invoke([_gate(promoted=False, block=True), _gate(promoted=True)]),
        )
        self.assertTrue(r.mergeable)
        self.assertEqual(r.rounds, 2)
        self.assertEqual(fixes, [1])  # one fix applied between the two rounds

    def test_non_convergence_is_non_human_terminal_and_bounded(self):
        calls = {"n": 0}
        def invoke(**kwargs):
            calls["n"] += 1
            return _gate(promoted=False, block=True)
        r = run_governed_premerge_loop(
            artifact="A", author_executor="claude", run_mode="governed",
            apply_fix=lambda rnd, art, f: art, invoke=invoke,
        )
        self.assertFalse(r.mergeable)
        self.assertEqual(r.rounds, DEFAULT_MAX_REVIEW_ROUNDS)     # bounded
        self.assertEqual(calls["n"], DEFAULT_MAX_REVIEW_ROUNDS)   # not infinite
        self.assertFalse(r.terminal_blocker["human_required"])
        self.assertEqual(r.terminal_blocker["blocker_class"], "review_gate_block")
        self.assertEqual(r.reason, "non_convergence")

    def test_no_usable_reviewer_first_round_blocks_fail_closed(self):
        # FAIL-CLOSED (advisor-panel reconciliation): a degraded result (no usable
        # disjoint reviewer) is NOT an advisory pass in governed mode — the prior
        # advisory-pass-before-any-block was a fail-open. It halts non-human.
        r = run_governed_premerge_loop(artifact="A", author_executor="claude", run_mode="governed",
                                       invoke=_scripted_invoke([_gate(degraded=True, reason="no_reviewers")]))
        self.assertFalse(r.mergeable)
        self.assertEqual(r.terminal_blocker["blocker_class"], "review_gate_block")
        self.assertFalse(r.terminal_blocker["human_required"])

    def test_panel_lost_while_failing_is_non_human_terminal(self):
        r = run_governed_premerge_loop(
            artifact="A", author_executor="claude", run_mode="governed",
            apply_fix=lambda rnd, art, f: art,
            invoke=_scripted_invoke([_gate(promoted=False, block=True), _gate(degraded=True)]),
        )
        self.assertFalse(r.mergeable)
        self.assertEqual(r.terminal_blocker["blocker_class"], "review_gate_block")
        self.assertFalse(r.terminal_blocker["human_required"])


class RunnerWiringTest(unittest.TestCase):
    def test_governed_premerge_for_run_autonomous_is_noop(self):
        invoke = Mock()
        r = governed_premerge_for_run(artifact="A", author_executor="claude",
                                      run_mode="autonomous", invoke=invoke)
        self.assertTrue(r.mergeable)
        self.assertFalse(r.ran)
        invoke.assert_not_called()   # zero panel calls on the default path


if __name__ == "__main__":
    unittest.main()
