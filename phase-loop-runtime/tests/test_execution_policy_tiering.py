"""rigor-v1 P4 — planner-driven model/effort tiering.

Proves the `## Execution Policy` syntax the plan skills teach actually resolves
to per-lane effort tiers in the runtime, so a planner can right-size a trivial
lane down and escalate a hard lane with a reason.
"""
import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.discovery import (
    execution_policy_for_action,
    execution_policy_for_lane,
    parse_execution_policy,
)

PLAN = """# Phase plan

## Execution Policy
- default: effort=low, reason=most lanes are mechanical this phase
- execute: effort=medium
- SL-3: effort=high, reason=constant-time comparison is easy to get subtly wrong
- SL-7: effort=minimal, reason=docs sweep only

## Lanes
- SL-3 (crypto)
- SL-7 (docs)
"""


class ExecutionPolicyTieringTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.path = Path(self._td.name) / "plan.md"
        self.path.write_text(PLAN, encoding="utf-8")
        self.doc = parse_execution_policy(self.path, kind="plan")

    def tearDown(self):
        self._td.cleanup()

    def test_parses_without_error(self):
        self.assertIsNone(self.doc.parse_error)
        self.assertTrue(self.doc.rules)

    def test_action_default_effort(self):
        rule = execution_policy_for_action(self.doc, "execute")
        self.assertEqual(rule.effort, "medium")  # execute override

    def test_trivial_lane_tiers_down(self):
        rule = execution_policy_for_lane(self.doc, "execute", "SL-7")
        self.assertEqual(rule.effort, "minimal")

    def test_hard_lane_escalates_with_reason(self):
        rule = execution_policy_for_lane(self.doc, "execute", "SL-3")
        self.assertEqual(rule.effort, "high")
        self.assertIn("constant-time", (rule.override_reason or ""))

    def test_unlisted_lane_inherits_action(self):
        rule = execution_policy_for_lane(self.doc, "execute", "SL-9")
        self.assertEqual(rule.effort, "medium")  # falls back to the execute action rule


if __name__ == "__main__":
    unittest.main()
