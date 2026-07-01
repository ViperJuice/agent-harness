"""model-routing-v1 P1 — model_class layer, shipped model_policy, effort clamp.

Two axes, kept separate: the *empty-policy* path (no model_policy) is byte-for-byte
unchanged (back-compat for downstream repos); the *shipped* model_policy is THIS
repo's default (planning at max, implementation at the implementer model).
"""
import unittest

from phase_loop_runtime.models import ExecutionPolicyRule
from phase_loop_runtime.profiles import (
    resolve_execution_policy,
    resolve_model_class,
    resolve_profile_for_executor,
    shipped_model_policy_rule,
)


def _resolve(action, executor, *, model_policy=False, plan_policy=None,
             operator_model=None, operator_effort=None):
    selection = resolve_profile_for_executor(action=action, executor=executor)
    rule = shipped_model_policy_rule(action) if model_policy else None
    rp = resolve_execution_policy(
        action=action, executor=executor, model_selection=selection,
        plan_policy=plan_policy, model_policy_rule=rule,
        operator_model=operator_model, operator_effort=operator_effort,
    )
    return rp.model, rp.effort


class ModelClassResolutionTest(unittest.TestCase):
    def test_class_to_model_per_executor(self):
        self.assertEqual(resolve_model_class("claude", "planner"), "claude-opus-4-8")
        self.assertEqual(resolve_model_class("claude", "implementer"), "claude-sonnet-5")
        self.assertEqual(resolve_model_class("claude", "worker"), "claude-haiku-4-5")
        self.assertEqual(resolve_model_class("codex", "implementer"), "gpt-5.4")
        # Gemini keeps `pro` for planning while implementer/worker route through
        # the validated agy Flash model name.
        self.assertEqual(resolve_model_class("gemini", "planner"), "pro")
        self.assertEqual(resolve_model_class("gemini", "implementer"), "Gemini 3.5 Flash (High)")
        self.assertEqual(resolve_model_class("gemini", "worker"), "Gemini 3.5 Flash (High)")
        self.assertIsNone(resolve_model_class("claude", "bogus"))

    def test_model_class_field_validates(self):
        ExecutionPolicyRule(model_class="planner")  # ok
        with self.assertRaises(ValueError):
            ExecutionPolicyRule(model_class="not_a_class")


class EmptyPolicyBackCompatTest(unittest.TestCase):
    def test_plan_codex_unchanged(self):
        self.assertEqual(_resolve("plan", "codex", model_policy=False), ("gpt-5.5", "high"))

    def test_execute_claude_unchanged(self):
        # opus @ high is today's claude execute baseline (the empty-policy path).
        self.assertEqual(_resolve("execute", "claude", model_policy=False), ("claude-opus-4-8", "high"))


class ShippedPolicyTest(unittest.TestCase):
    def test_plan_codex_becomes_max(self):
        self.assertEqual(_resolve("plan", "codex", model_policy=True), ("gpt-5.5", "max"))

    def test_roadmap_is_max(self):
        self.assertEqual(_resolve("roadmap", "codex", model_policy=True)[1], "max")

    def test_execute_claude_becomes_sonnet_medium(self):
        self.assertEqual(_resolve("execute", "claude", model_policy=True), ("claude-sonnet-5", "medium"))


class EffortClampTest(unittest.TestCase):
    def test_gemini_plan_max_clamps_to_high_with_shipped_policy(self):
        # The shipped policy sets fallback so gemini's effort_map maps max->high.
        self.assertEqual(_resolve("plan", "gemini", model_policy=True), ("pro", "high"))

    def test_gemini_max_raises_without_clamp(self):
        # Documenting the verified runtime behavior: a max request for a sub-max
        # provider RAISES unless the rule opts into fallback/inherit_default.
        no_clamp = ExecutionPolicyRule(
            selector="plan", action="plan", effort="max",
            unsupported_policy_behavior="block", source="test",
        )
        selection = resolve_profile_for_executor(action="plan", executor="gemini")
        with self.assertRaises(ValueError):
            resolve_execution_policy(
                action="plan", executor="gemini",
                model_selection=selection, model_policy_rule=no_clamp,
            )


class PrecedenceTest(unittest.TestCase):
    def test_operator_effort_beats_model_policy(self):
        # CLI > model_policy: operator --effort low overrides shipped max.
        self.assertEqual(_resolve("plan", "codex", model_policy=True, operator_effort="low")[1], "low")

    def test_operator_model_beats_model_policy(self):
        self.assertEqual(
            _resolve("plan", "codex", model_policy=True, operator_model="gpt-5.4")[0], "gpt-5.4"
        )

    def test_plan_policy_beats_model_policy(self):
        # plan ## Execution Policy > model_policy: explicit effort xhigh wins.
        plan_rule = ExecutionPolicyRule(selector="plan", action="plan", effort="xhigh", source="phase-plan policy")
        self.assertEqual(_resolve("plan", "codex", model_policy=True, plan_policy=plan_rule)[1], "xhigh")

    def test_plan_policy_effort_only_inherits_shipped_model_class(self):
        # CR fix: a plan Execution Policy pinning ONLY effort must still inherit
        # the shipped model_policy's implementer class (layered merge), not revert
        # to the registry heavy model.
        plan = ExecutionPolicyRule(selector="execute", action="execute", effort="low", source="phase-plan policy")
        model, effort = _resolve("execute", "claude", model_policy=True, plan_policy=plan)
        self.assertEqual(model, "claude-sonnet-5")  # implementer, from model_policy
        self.assertEqual(effort, "low")               # plan's effort wins

    def test_plan_policy_executor_only_inherits_shipped_model_class(self):
        plan = ExecutionPolicyRule(selector="execute", action="execute", executor="claude", source="phase-plan policy")
        model, _effort = _resolve("execute", "claude", model_policy=True, plan_policy=plan)
        self.assertEqual(model, "claude-sonnet-5")  # implementer still applied


class MaxEffortPlannerGuardTest(unittest.TestCase):
    def test_gemini_planner_max_clamps_via_guard_without_explicit_clamp(self):
        # CR fix: planner@max on gemini (no explicit clamp policy) must not RAISE —
        # the wired max-effort-planner guard forces the clamp to the ceiling.
        plan = ExecutionPolicyRule(
            selector="plan", action="plan", model_class="planner", effort="max", source="phase-plan policy"
        )
        model, effort = _resolve("plan", "gemini", plan_policy=plan)
        self.assertEqual(effort, "high")  # clamped via the guard, not raised
        self.assertEqual(model, "pro")    # gemini planner alias

    def test_codex_planner_max_stays_max(self):
        # codex IS max-eligible — the guard does not touch it.
        plan = ExecutionPolicyRule(
            selector="plan", action="plan", model_class="planner", effort="max", source="phase-plan policy"
        )
        self.assertEqual(_resolve("plan", "codex", plan_policy=plan)[1], "max")


if __name__ == "__main__":
    unittest.main()
