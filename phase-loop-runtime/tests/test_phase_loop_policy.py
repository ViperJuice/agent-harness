import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.capability_registry import default_executor_for_action, default_executor_for_work_unit, provider_policy_capabilities
from phase_loop_runtime.models import (
    NORMALIZED_EFFORT_LEVELS,
    UNSUPPORTED_POLICY_BEHAVIORS,
    WORK_UNIT_KINDS,
    ProviderPolicyCapability,
    WorkUnitPolicy,
)
from phase_loop_runtime.profiles import ACTION_WORK_UNITS, DEFAULT_PROFILES, normalize_provider_effort


class PhaseLoopPolicyTest(unittest.TestCase):
    def test_workpolicy_literals_are_frozen(self):
        self.assertEqual(WORK_UNIT_KINDS, ("roadmap_build", "phase_plan", "lane_execute", "lane_review", "phase_reducer", "phase_verify", "repair", "closeout"))
        self.assertEqual(NORMALIZED_EFFORT_LEVELS, ("minimal", "low", "medium", "high", "xhigh", "max"))
        self.assertEqual(UNSUPPORTED_POLICY_BEHAVIORS, ("block", "fallback", "inherit_default"))

    def test_provider_capability_registry_covers_expected_providers(self):
        capabilities = provider_policy_capabilities()
        self.assertEqual(
            set(capabilities),
            {"codex", "claude", "gemini", "gemini-api", "grok", "opencode", "pi", "command", "manual"},
        )
        for capability in capabilities.values():
            self.assertIsInstance(capability, ProviderPolicyCapability)
            self.assertEqual(capability.unsupported_policy_behavior, "block")
            self.assertEqual(capability.supported_work_units, WORK_UNIT_KINDS)

    def test_gemini_cli_alias_contract_requires_run_local_user_scope(self):
        gemini = provider_policy_capabilities()["gemini"]
        self.assertEqual(gemini.provider, "gemini-cli")
        self.assertTrue(gemini.requires_run_local_user_scope)
        self.assertEqual(gemini.model_aliases["phase_plan"], "phase-loop-plan-high")
        self.assertEqual(gemini.model_aliases["lane_execute"], "phase-loop-execute-medium")
        self.assertEqual(gemini.model_aliases["lane_review"], "phase-loop-review-high")
        self.assertEqual(gemini.model_aliases["phase_reducer"], "phase-loop-review-high")
        self.assertEqual(gemini.model_aliases["phase_verify"], "phase-loop-review-high")
        self.assertEqual(gemini.model_aliases["repair"], "phase-loop-execute-medium")
        self.assertEqual(gemini.model_aliases["closeout"], "phase-loop-review-high")
        self.assertIn("modelConfigs.customAliases", " ".join(gemini.notes))
        self.assertIn("thinkingConfig.thinkingLevel", " ".join(gemini.notes))

    def test_simple_bounded_lane_execution_defaults_to_pi(self):
        self.assertEqual(default_executor_for_work_unit("lane_execute", scheduler_assigned=True), "pi")
        self.assertEqual(default_executor_for_action("execute"), "codex")
        self.assertEqual(default_executor_for_action("plan"), "codex")
        self.assertEqual(default_executor_for_action("repair"), "codex")
        self.assertEqual(default_executor_for_action("maintain-skills"), "codex")
        pi = provider_policy_capabilities()["pi"]
        self.assertIn("default executor for simple bounded scheduler-assigned lane execution", " ".join(pi.notes))
        self.assertEqual(provider_policy_capabilities()["manual"].executor, "manual")

    def test_default_profiles_freeze_planning_execution_review_posture(self):
        self.assertEqual(DEFAULT_PROFILES["roadmap"], ("gpt-5.6-sol", "high"))
        self.assertEqual(DEFAULT_PROFILES["plan"], ("gpt-5.6-sol", "high"))
        self.assertEqual(DEFAULT_PROFILES["execute"], ("gpt-5.6-sol", "medium"))
        self.assertEqual(DEFAULT_PROFILES["repair"], ("gpt-5.6-sol", "medium"))
        self.assertEqual(DEFAULT_PROFILES["review"], ("gpt-5.6-sol", "high"))
        self.assertEqual(DEFAULT_PROFILES["skill-maintenance"], ("gpt-5.6-sol", "high"))
        self.assertEqual(ACTION_WORK_UNITS["execute"], "lane_execute")
        self.assertEqual(ACTION_WORK_UNITS["review"], "lane_review")
        self.assertEqual(ACTION_WORK_UNITS["maintain-skills"], "phase_verify")

    def test_unsupported_policy_blocks_silent_effort_downgrade(self):
        policy = WorkUnitPolicy(work_unit_kind="lane_execute", effort="xhigh")
        with self.assertRaises(ValueError):
            normalize_provider_effort(provider_key="gemini", work_unit_policy=policy)

    def test_named_fallback_allows_explicit_effort_mapping(self):
        policy = WorkUnitPolicy(
            work_unit_kind="lane_execute",
            effort="xhigh",
            unsupported_policy_behavior="fallback",
            fallback="high",
        )
        self.assertEqual(normalize_provider_effort(provider_key="gemini", work_unit_policy=policy), "high")

    def test_explicit_default_inheritance_uses_provider_default(self):
        policy = WorkUnitPolicy(
            work_unit_kind="phase_verify",
            effort="minimal",
            unsupported_policy_behavior="inherit_default",
            inherit_default=True,
        )
        self.assertEqual(normalize_provider_effort(provider_key="gemini", work_unit_policy=policy), "medium")


if __name__ == "__main__":
    unittest.main()
