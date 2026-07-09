"""model-routing-v1 P5 — routing invariants (CI lock).

One file that pins the contracts P1–P4 must honor, so a future change that
violates one fails CI. Every invariant asserts behavior that is actually true
today (the governed loop logic is wired + unit-tested; the autonomous default is
a proven no-op).
"""
import unittest
from unittest.mock import Mock

from phase_loop_runtime.profiles import (
    PATCH_AUTHORING_ACTIONS,
    SHIPPED_MODEL_POLICY,
    max_effort_planner_eligible,
    resolve_execution_policy,
    resolve_profile_for_executor,
    shipped_model_policy_rule,
)
from phase_loop_runtime.governed_review import GateResult, governed_planning_gate, select_reviewer_pool
from phase_loop_runtime.governed_premerge import next_escalation, run_governed_premerge_loop
from phase_loop_runtime.closeout_validators import ReviewFinding
from phase_loop_runtime.runner import governed_premerge_for_run


def _resolve(action, executor, *, model_policy=False):
    selection = resolve_profile_for_executor(action=action, executor=executor)
    rule = shipped_model_policy_rule(action) if model_policy else None
    rp = resolve_execution_policy(
        action=action, executor=executor, model_selection=selection, model_policy_rule=rule
    )
    return rp.model, rp.effort


_BLOCK = ReviewFinding(code="x", reason="unresolved", severity="block", blocker_class="review_gate_block")


class RoutingInvariantsTest(unittest.TestCase):
    # 1 — the empty-policy path is byte-for-byte unchanged (back-compat).
    def test_empty_policy_resolution_unchanged(self):
        self.assertEqual(_resolve("plan", "codex"), ("gpt-5.6-sol", "high"))
        self.assertEqual(_resolve("execute", "claude"), ("claude-opus-4-8", "high"))

    # 2 — the worker class never authors a final patch.
    def test_worker_never_authors_a_patch(self):
        for action in PATCH_AUTHORING_ACTIONS:
            self.assertNotEqual(SHIPPED_MODEL_POLICY[action].get("model_class"), "worker")
        # And no shipped action at all routes a patch author to worker.
        for action, spec in SHIPPED_MODEL_POLICY.items():
            if action in PATCH_AUTHORING_ACTIONS:
                self.assertIn(spec.get("model_class"), ("planner", "implementer"))

    # 3 — a governed merge requires a panel pass, or an explicit advisory degrade.
    def test_governed_merge_requires_panel_pass(self):
        clean = run_governed_premerge_loop(
            artifact="A", author_executor="claude", run_mode="governed",
            available_legs=("codex", "gemini"),
            invoke=lambda **kw: GateResult(ran=True, promoted=True),
        )
        self.assertTrue(clean.mergeable and clean.ran)

    def test_governed_unresolved_block_is_non_human_terminal(self):
        blocked = run_governed_premerge_loop(
            artifact="A", author_executor="claude", run_mode="governed",
            apply_fix=lambda rnd, art, findings: art,  # no real fix → block persists
            available_legs=("codex", "gemini"), max_rounds=3,
            invoke=lambda **kw: GateResult(ran=True, promoted=False, findings=(_BLOCK,)),
        )
        self.assertFalse(blocked.mergeable)
        self.assertIsNotNone(blocked.terminal_blocker)
        self.assertFalse(blocked.terminal_blocker["human_required"])

    def test_governed_no_disjoint_reviewer_blocks_not_self_review(self):
        # FAIL-CLOSED (advisor-panel reconciliation): when only the author's own
        # vendor is authed, governed mode HOLDS (block) rather than advisory-passing
        # — and never spawns a same-vendor self-review.
        invoke = Mock()
        g = governed_planning_gate(
            artifact="A", author_executor="claude", run_mode="governed",
            available_legs=("claude",), invoke=invoke,  # only the author's vendor authed
        )
        self.assertFalse(g.promoted)                  # held, not advisory-passed
        self.assertTrue(any(f.severity == "block" for f in g.findings))
        invoke.assert_not_called()                    # never a same-vendor self-review spawn

    # 4 — the reviewer pool is vendor-disjoint from the author.
    def test_reviewer_pool_excludes_author_vendor(self):
        pool, reason = select_reviewer_pool("claude", ["claude", "codex", "gemini"])
        self.assertNotIn("claude", pool)
        self.assertIsNone(reason)
        _, only = select_reviewer_pool("claude", ["claude"])
        self.assertEqual(only, "author_vendor_only")
        _, none = select_reviewer_pool("claude", [])
        self.assertEqual(none, "no_reviewers")

    # 5 — gemini is never the max-effort planner of record (selection-time guard).
    def test_gemini_not_max_planner_of_record(self):
        self.assertFalse(max_effort_planner_eligible("gemini"))
        self.assertFalse(max_effort_planner_eligible("pi"))
        self.assertTrue(max_effort_planner_eligible("claude"))
        self.assertTrue(max_effort_planner_eligible("codex"))
        # The shipped policy plans at max → its planner of record must be max-capable.
        self.assertEqual(SHIPPED_MODEL_POLICY["plan"]["effort"], "max")

    # 6 — an autonomous run makes zero panel calls.
    def test_autonomous_makes_zero_panel_calls(self):
        invoke = Mock()
        r = governed_premerge_for_run(
            artifact="A", author_executor="claude", run_mode="autonomous", invoke=invoke,
        )
        self.assertTrue(r.mergeable and not r.ran)
        invoke.assert_not_called()
        # ... and the autonomous escalation terminal is non-human (no panel).
        d = next_escalation(model_class="planner", failed_tests=2, run_mode="autonomous")
        self.assertEqual(d.action, "terminal_blocker")
        self.assertFalse(d.blocker["human_required"])
        # ... while governed escalates to the panel.
        g = next_escalation(model_class="planner", failed_tests=2, run_mode="governed")
        self.assertEqual(g.action, "invoke_panel")

    # P4 — the governed fix-round counter is independent of the runner's repair pivot.
    def test_governed_fix_round_counter_independent_of_repair_pivot(self):
        # The pre-merge loop bounds rounds by its OWN max_rounds (internal counter),
        # never the runner's _recent_repeated_repair_failures executor-vendor pivot.
        res = run_governed_premerge_loop(
            artifact="b", author_executor="claude", run_mode="governed",
            apply_fix=lambda rnd, cur, find: "b2",
            available_legs=("codex", "gemini"),
            invoke=lambda **kw: GateResult(ran=True, promoted=False, findings=(_BLOCK,)),
            max_rounds=2,
        )
        self.assertEqual(res.rounds, 2)  # capped by its own max_rounds, not the pivot
        import inspect
        import phase_loop_runtime.governed_premerge as gp
        self.assertNotIn("_recent_repeated_repair_failures", inspect.getsource(gp))


if __name__ == "__main__":
    unittest.main()
