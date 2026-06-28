"""model-routing-v1 P2 — governed planning gate (IF-0-P2-1)."""
import unittest
from unittest.mock import Mock

from phase_loop_runtime.closeout_validators import CloseoutContext, ReviewFinding
from phase_loop_runtime.governed_review import (
    author_vendor_for_executor,
    governed_planning_gate,
    resolve_run_mode,
    select_reviewer_pool,
)
from phase_loop_runtime.panel_invoker import PanelLegResult, PanelResult


def _panel(*legs):
    return PanelResult(legs=tuple(legs))


class RunModeTest(unittest.TestCase):
    def test_default_is_autonomous(self):
        self.assertEqual(resolve_run_mode({}), "autonomous")
        self.assertEqual(resolve_run_mode({"PHASE_LOOP_RUN_MODE": "governed"}), "governed")
        self.assertEqual(resolve_run_mode({"PHASE_LOOP_RUN_MODE": "nonsense"}), "autonomous")
        self.assertEqual(resolve_run_mode({}, explicit="governed"), "governed")

    def test_closeout_context_run_mode_defaults_autonomous(self):
        ctx = CloseoutContext(phase_alias="P", plan_path="p.md")
        self.assertEqual(ctx.run_mode, "autonomous")


class AutonomousShortCircuitTest(unittest.TestCase):
    def test_autonomous_makes_zero_panel_calls(self):
        invoke = Mock()  # if this is ever called, the guarantee is broken
        result = governed_planning_gate(
            artifact="ART", author_executor="claude", run_mode="autonomous", invoke=invoke,
        )
        invoke.assert_not_called()           # ZERO panel calls — stronger than "no human_required"
        self.assertFalse(result.ran)
        self.assertTrue(result.promoted)
        self.assertEqual(result.findings, ())


class GovernedGateTest(unittest.TestCase):
    def test_block_finding_holds_promotion(self):
        invoke = lambda art, pool, spawn=None: _panel(
            PanelLegResult(leg="codex", status="ok", text="DISAGREE — has a real bug"),
            PanelLegResult(leg="gemini", status="ok", text="AGREE"),
        )
        result = governed_planning_gate(
            artifact="ART", author_executor="claude", run_mode="governed",
            available_legs=("codex", "gemini"), invoke=invoke,
        )
        self.assertTrue(result.ran)
        self.assertFalse(result.promoted)     # an unresolved block holds promotion
        self.assertTrue(any(f.severity == "block" for f in result.findings))

    def test_no_block_promotes_with_nits_recorded(self):
        invoke = lambda art, pool, spawn=None: _panel(
            PanelLegResult(leg="codex", status="ok", text="AGREE, minor notes"),
            PanelLegResult(leg="gemini", status="ok", text="AGREE"),
        )
        result = governed_planning_gate(
            artifact="ART", author_executor="claude", run_mode="governed",
            available_legs=("codex", "gemini"), invoke=invoke,
        )
        self.assertTrue(result.promoted)
        self.assertTrue(result.findings)      # nits recorded (warn), non-gating
        self.assertTrue(all(f.severity == "warn" for f in result.findings))


class ReviewerPoolTest(unittest.TestCase):
    def test_pool_excludes_author_vendor(self):
        pool, reason = select_reviewer_pool("claude", ("codex", "gemini", "claude"))
        self.assertEqual(set(pool), {"codex", "gemini"})
        self.assertIsNone(reason)

    def test_author_vendor_only_degrades(self):
        pool, reason = select_reviewer_pool("claude", ("claude",))
        self.assertEqual(pool, ())
        self.assertEqual(reason, "author_vendor_only")

    def test_zero_authed_degrades(self):
        pool, reason = select_reviewer_pool("claude", ())
        self.assertEqual(reason, "no_reviewers")

    def test_author_vendor_mapping(self):
        self.assertEqual(author_vendor_for_executor("opencode"), "codex")
        self.assertEqual(author_vendor_for_executor("pi"), "pi")
        self.assertEqual(author_vendor_for_executor("claude"), "claude")

    def test_gate_degrades_to_autonomous_warn_not_pass(self):
        invoke = Mock()
        result = governed_planning_gate(
            artifact="ART", author_executor="claude", run_mode="governed",
            available_legs=("claude",), invoke=invoke,  # only author vendor authed
        )
        invoke.assert_not_called()            # no self-review spawned
        self.assertTrue(result.ran)
        self.assertTrue(result.degraded)      # marked NOT a real review
        self.assertEqual(result.reason, "author_vendor_only")
        self.assertTrue(any(f.code == "governed_review_degraded" and f.severity == "warn"
                            for f in result.findings))


if __name__ == "__main__":
    unittest.main()
