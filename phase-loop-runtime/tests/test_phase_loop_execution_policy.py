import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.capability_registry import DEFAULT_EXECUTOR_POLICY
from phase_loop_runtime.models import ExecutionPolicyRule, ModelSelection
from phase_loop_runtime.profiles import resolve_execution_policy, resolve_model_selection_from_policy, resolve_profile_for_executor


class PhaseLoopExecutionPolicyTest(unittest.TestCase):
    def test_dfparsoak_policy_precedence_keeps_execute_default_and_explicit_fallbacks(self):
        self.assertEqual(DEFAULT_EXECUTOR_POLICY["execute"], "codex")
        self.assertEqual(resolve_profile_for_executor(action="execute", executor="pi").model, "auto")
        self.assertEqual(resolve_profile_for_executor(action="execute", executor="claude").model, "claude-opus-4-8")
        self.assertEqual(resolve_profile_for_executor(action="execute", executor="gemini").model, "auto")

        roadmap = ExecutionPolicyRule(
            selector="execute",
            action="execute",
            executor="pi",
            model="gpt-5.6-sol",
            effort="medium",
            work_unit_kind="lane_execute",
            source="roadmap:execute",
        )
        plan = ExecutionPolicyRule(
            selector="execute",
            action="execute",
            executor="codex",
            model="gpt-5.6-sol",
            effort="high",
            work_unit_kind="lane_execute",
            unsupported_policy_behavior="inherit_default",
            inherit_default=True,
            source="plan:execute",
        )

        resolved = resolve_execution_policy(
            action="execute",
            executor=DEFAULT_EXECUTOR_POLICY["execute"],
            model_selection=ModelSelection(profile="execute", model="gpt-5.6-sol", effort="medium"),
            plan_policy=plan,
            roadmap_policy=roadmap,
        )

        self.assertEqual(resolved.executor, "codex")
        self.assertEqual(resolved.executor_source, "phase-plan policy")
        self.assertEqual(resolved.execution_policy_source, "phase-plan policy")
        self.assertFalse(resolved.fallback_applied)

    def test_phase_plan_policy_overrides_roadmap_policy(self):
        roadmap = ExecutionPolicyRule(
            selector="execute",
            action="execute",
            executor="claude",
            model="claude-opus-4-8",
            effort="high",
            work_unit_kind="lane_execute",
            source="roadmap:execute",
        )
        plan = ExecutionPolicyRule(
            selector="execute",
            action="execute",
            executor="codex",
            model="gpt-5.6-sol",
            effort="xhigh",
            work_unit_kind="lane_execute",
            source="plan:execute",
        )

        resolved = resolve_execution_policy(
            action="execute",
            executor="claude",
            model_selection=ModelSelection(profile="execute", model="claude-opus-4-8", effort="high"),
            plan_policy=plan,
            roadmap_policy=roadmap,
        )

        self.assertEqual(resolved.executor, "codex")
        self.assertEqual(resolved.model, "gpt-5.6-sol")
        self.assertEqual(resolved.effort, "xhigh")
        self.assertEqual(resolved.execution_policy_source, "phase-plan policy")

    def test_operator_model_and_effort_override_policy(self):
        plan = ExecutionPolicyRule(
            selector="execute",
            action="execute",
            executor="codex",
            model="gpt-5.6-sol",
            effort="medium",
            work_unit_kind="lane_execute",
            source="plan:execute",
        )

        resolved = resolve_execution_policy(
            action="execute",
            executor="codex",
            model_selection=ModelSelection(profile="execute", model="gpt-5.6-sol", effort="medium"),
            operator_model="gpt-5.6-terra",
            operator_effort="high",
            plan_policy=plan,
        )
        selection = resolve_model_selection_from_policy(profile="execute", resolved_policy=resolved)

        self.assertEqual(selection.model, "gpt-5.6-terra")
        self.assertEqual(selection.effort, "high")
        self.assertEqual(resolved.model_source, "CLI/operator override")
        self.assertEqual(resolved.effort_source, "CLI/operator override")

    def test_invalid_gemini_alias_fails_closed_without_explicit_fallback(self):
        policy = ExecutionPolicyRule(
            selector="execute",
            action="execute",
            executor="gemini",
            model="phase-loop-unknown",
            effort="medium",
            work_unit_kind="lane_execute",
            source="plan:execute",
        )

        with self.assertRaisesRegex(ValueError, "unsupported model"):
            resolve_execution_policy(
                action="execute",
                executor="gemini",
                model_selection=ModelSelection(profile="execute", model="phase-loop-execute-medium", effort="medium"),
                plan_policy=policy,
            )

    def test_named_fallback_and_default_inheritance_are_recorded(self):
        fallback_policy = ExecutionPolicyRule(
            selector="execute",
            action="execute",
            executor="gemini",
            model="phase-loop-unknown",
            effort="medium",
            work_unit_kind="lane_execute",
            unsupported_policy_behavior="fallback",
            fallback="phase-loop-execute-medium",
            source="plan:execute",
        )
        inherited_policy = ExecutionPolicyRule(
            selector="execute",
            action="execute",
            executor="gemini",
            model="phase-loop-unknown",
            effort="xhigh",
            work_unit_kind="lane_execute",
            unsupported_policy_behavior="inherit_default",
            inherit_default=True,
            source="roadmap:execute",
        )

        fallback = resolve_execution_policy(
            action="execute",
            executor="gemini",
            model_selection=ModelSelection(profile="execute", model="phase-loop-execute-medium", effort="medium"),
            plan_policy=fallback_policy,
        )
        inherited = resolve_execution_policy(
            action="execute",
            executor="gemini",
            model_selection=ModelSelection(profile="execute", model="phase-loop-execute-medium", effort="medium"),
            roadmap_policy=inherited_policy,
        )

        self.assertEqual(fallback.model, "phase-loop-execute-medium")
        self.assertEqual(fallback.fallback, "phase-loop-execute-medium")
        self.assertEqual(inherited.model, "phase-loop-execute-medium")
        self.assertEqual(inherited.effort, "medium")
        self.assertTrue(inherited.fallback_applied)

    def test_default_executor_profiles_match_high_medium_high_policy(self):
        self.assertEqual(resolve_profile_for_executor(action="roadmap", executor="codex").effort, "high")
        self.assertEqual(resolve_profile_for_executor(action="plan", executor="codex").effort, "high")
        self.assertEqual(resolve_profile_for_executor(action="execute", executor="codex").effort, "medium")
        self.assertEqual(resolve_profile_for_executor(action="repair", executor="codex").effort, "medium")
        self.assertEqual(resolve_profile_for_executor(action="review", executor="codex").effort, "high")
        self.assertEqual(resolve_profile_for_executor(action="maintain-skills", executor="codex").effort, "high")

    def test_pi_policy_fails_closed_for_unsupported_effort_without_fallback(self):
        policy = ExecutionPolicyRule(
            selector="execute",
            action="execute",
            executor="pi",
            model="auto",
            effort="xhigh",
            work_unit_kind="lane_execute",
            source="plan:execute",
        )

        with self.assertRaisesRegex(ValueError, "unsupported effort"):
            resolve_execution_policy(
                action="execute",
                executor="pi",
                model_selection=ModelSelection(profile="execute", model="auto", effort="medium"),
                plan_policy=policy,
            )

    def test_pi_policy_can_inherit_default_effort(self):
        policy = ExecutionPolicyRule(
            selector="execute",
            action="execute",
            executor="pi",
            model="auto",
            effort="xhigh",
            work_unit_kind="lane_execute",
            unsupported_policy_behavior="inherit_default",
            inherit_default=True,
            source="plan:execute",
        )

        resolved = resolve_execution_policy(
            action="execute",
            executor="pi",
            model_selection=ModelSelection(profile="execute", model="auto", effort="medium"),
            plan_policy=policy,
        )
        self.assertEqual(resolved.executor, "pi")
        self.assertEqual(resolved.effort, "medium")
        self.assertTrue(resolved.fallback_applied)

    def test_claude_model_defaults_to_claude_executor_unless_pi_override_is_reasoned(self):
        defaulted = resolve_execution_policy(
            action="execute",
            executor="pi",
            model_selection=ModelSelection(profile="execute", model="claude-opus-4-8", effort="high"),
        )
        self.assertEqual(defaulted.executor, "claude")

        explicit_pi = ExecutionPolicyRule(
            selector="execute",
            action="execute",
            executor="pi",
            model="claude-opus-4-8",
            effort="medium",
            work_unit_kind="lane_execute",
            unsupported_policy_behavior="inherit_default",
            inherit_default=True,
            source="plan:execute",
            override_reason="explicit Pi-wrapped Claude route",
        )
        selected = resolve_execution_policy(
            action="execute",
            executor="pi",
            model_selection=ModelSelection(profile="execute", model="claude-opus-4-8", effort="medium"),
            plan_policy=explicit_pi,
        )
        self.assertEqual(selected.executor, "pi")
        self.assertEqual(selected.execution_policy_override_reason, "explicit Pi-wrapped Claude route")


if __name__ == "__main__":
    unittest.main()
