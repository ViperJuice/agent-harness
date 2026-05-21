from __future__ import annotations

from .capability_registry import DEFAULT_EXECUTOR, default_model_profile_for_executor, provider_policy_capabilities
from .models import (
    ExecutionPolicyRule,
    ModelSelection,
    ResolvedExecutionPolicy,
    WorkUnitPolicy,
    require_literal,
)


CLAUDE_HEAVY_MODEL = "claude-opus-4-7"
OPENAI_HEAVY_MODEL = "gpt-5.5"
OPENCODE_OPENAI_HEAVY_MODEL = "openai/gpt-5.5"
GEMINI_PRO_ROUTED_MODEL = "pro"
GEMINI_AUTO_ROUTED_MODEL = "auto"
PI_AUTO_ROUTED_MODEL = "auto"

DEFAULT_PROFILES = {
    "roadmap": (OPENAI_HEAVY_MODEL, "high"),
    "plan": (OPENAI_HEAVY_MODEL, "high"),
    "execute": (OPENAI_HEAVY_MODEL, "medium"),
    "repair": (OPENAI_HEAVY_MODEL, "medium"),
    "review": (OPENAI_HEAVY_MODEL, "high"),
    "skill-maintenance": (OPENAI_HEAVY_MODEL, "high"),
}

ACTION_WORK_UNITS = {
    "roadmap": "roadmap_build",
    "plan": "phase_plan",
    "execute": "lane_execute",
    "repair": "repair",
    "review": "lane_review",
    "maintain-skills": "phase_verify",
}

EXECUTOR_MODEL_OVERRIDES = {
    "claude": {
        "roadmap": CLAUDE_HEAVY_MODEL,
        "plan": CLAUDE_HEAVY_MODEL,
        "execute": CLAUDE_HEAVY_MODEL,
        "repair": CLAUDE_HEAVY_MODEL,
        "review": CLAUDE_HEAVY_MODEL,
    },
    "opencode": {
        "roadmap": OPENCODE_OPENAI_HEAVY_MODEL,
        "plan": OPENCODE_OPENAI_HEAVY_MODEL,
        "execute": OPENCODE_OPENAI_HEAVY_MODEL,
        "repair": OPENCODE_OPENAI_HEAVY_MODEL,
        "review": OPENCODE_OPENAI_HEAVY_MODEL,
    },
    "gemini": {
        "roadmap": GEMINI_PRO_ROUTED_MODEL,
        "plan": GEMINI_PRO_ROUTED_MODEL,
        "execute": GEMINI_AUTO_ROUTED_MODEL,
        "repair": GEMINI_AUTO_ROUTED_MODEL,
        "review": GEMINI_PRO_ROUTED_MODEL,
    },
    "pi": {
        "roadmap": PI_AUTO_ROUTED_MODEL,
        "plan": PI_AUTO_ROUTED_MODEL,
        "execute": PI_AUTO_ROUTED_MODEL,
        "repair": PI_AUTO_ROUTED_MODEL,
        "review": PI_AUTO_ROUTED_MODEL,
    },
}

EXECUTOR_EFFORT_OVERRIDES = {
    "claude": {
        "roadmap": "high",
        "plan": "high",
        "execute": "high",
        "repair": "high",
        "review": "high",
    },
}


def normalize_provider_effort(
    *,
    provider_key: str,
    work_unit_policy: WorkUnitPolicy,
    default_effort: str | None = None,
) -> str:
    capabilities = provider_policy_capabilities()
    if provider_key not in capabilities:
        raise ValueError(f"unknown provider policy capability: {provider_key}")

    capability = capabilities[provider_key]
    require_literal(work_unit_policy.work_unit_kind, capability.supported_work_units, "provider work-unit kind")
    requested_effort = work_unit_policy.effort or default_effort or capability.default_effort
    if requested_effort is None:
        if work_unit_policy.unsupported_policy_behavior == "inherit_default" and work_unit_policy.inherit_default:
            requested_effort = capability.default_effort
        if requested_effort is None:
            raise ValueError(f"no default effort for provider policy capability: {provider_key}")

    if requested_effort in capability.supported_efforts:
        return requested_effort

    if work_unit_policy.unsupported_policy_behavior == "inherit_default" and capability.default_effort:
        return capability.default_effort
    if work_unit_policy.unsupported_policy_behavior == "fallback" and work_unit_policy.fallback:
        fallback_effort = capability.effort_map.get(work_unit_policy.fallback, work_unit_policy.fallback)
        if fallback_effort in capability.supported_efforts:
            return fallback_effort
    raise ValueError(f"unsupported effort `{requested_effort}` for provider `{provider_key}`")


def resolve_profile(profile: str, model: str | None = None, effort: str | None = None) -> ModelSelection:
    default_model, default_effort = DEFAULT_PROFILES[profile]
    selected_model = model or default_model
    selected_effort = effort or default_effort
    if model or effort:
        return ModelSelection(
            profile=profile,
            model=selected_model,
            effort=selected_effort,
            source="user_override",
            override_reason="user supplied --model or --effort",
        )
    return ModelSelection(profile=profile, model=selected_model, effort=selected_effort)


def resolve_profile_for_executor(
    *,
    action: str,
    executor: str,
    profile: str | None = None,
    model: str | None = None,
    effort: str | None = None,
) -> ModelSelection:
    selected_profile = profile or default_model_profile_for_executor(action, executor)
    selection = resolve_profile(selected_profile, model=model, effort=effort)
    if model is not None:
        return selection
    executor_default = EXECUTOR_MODEL_OVERRIDES.get(executor, {}).get(action)
    effort_default = EXECUTOR_EFFORT_OVERRIDES.get(executor, {}).get(action)
    if not executor_default and not effort_default:
        return selection
    return ModelSelection(
        profile=selection.profile,
        model=executor_default or selection.model,
        effort=effort or effort_default or selection.effort,
        source=f"{executor}_default",
        override_reason=f"{executor} live adapter default model alias",
    )


def resolve_execution_policy(
    *,
    action: str,
    executor: str,
    model_selection: ModelSelection,
    operator_model: str | None = None,
    operator_effort: str | None = None,
    plan_policy: ExecutionPolicyRule | None = None,
    roadmap_policy: ExecutionPolicyRule | None = None,
    lane: str | None = None,
) -> ResolvedExecutionPolicy:
    require_literal(action, tuple(ACTION_WORK_UNITS.keys()), "execution policy action")
    policy, source = _first_policy(plan_policy, roadmap_policy)
    if _claude_model_needs_claude_executor(executor, model_selection.model, policy):
        executor = "claude"
    work_unit_kind = (
        (policy.work_unit_kind if policy else None)
        or ACTION_WORK_UNITS[action]
    )
    policy_executor = executor
    executor_source = "dispatch decision"
    policy_model = model_selection.model
    model_source = model_selection.source
    policy_effort = model_selection.effort
    effort_source = model_selection.source
    fallback = policy.fallback if policy else None
    fallback_source = source or "registry defaults"
    unsupported_behavior = policy.unsupported_policy_behavior if policy else "block"
    override_reason = policy.override_reason if policy else model_selection.override_reason

    if policy is not None:
        if policy.executor is not None:
            policy_executor = policy.executor
            executor_source = source or policy.source
        if policy.model is not None:
            policy_model = policy.model
            model_source = source or policy.source
        if policy.effort is not None:
            policy_effort = policy.effort
            effort_source = source or policy.source

    if operator_model is not None:
        policy_model = operator_model
        model_source = "CLI/operator override"
        override_reason = "operator supplied --model"
    if operator_effort is not None:
        policy_effort = operator_effort
        effort_source = "CLI/operator override"
        override_reason = "operator supplied --effort"

    work_unit_policy = WorkUnitPolicy(
        work_unit_kind=work_unit_kind,
        effort=policy_effort,
        unsupported_policy_behavior=unsupported_behavior,
        fallback=fallback,
        inherit_default=bool(policy.inherit_default) if policy else False,
    )
    normalized_effort = normalize_provider_effort(
        provider_key=policy_executor,
        work_unit_policy=work_unit_policy,
        default_effort=model_selection.effort,
    )
    fallback_applied = normalized_effort != policy_effort
    resolved_model = _resolve_policy_model(policy_executor, work_unit_kind, policy_model, fallback, unsupported_behavior)
    return ResolvedExecutionPolicy(
        action=action,
        lane=lane,
        executor=policy_executor,
        model=resolved_model,
        effort=normalized_effort,
        work_unit_kind=work_unit_kind,
        fallback=fallback,
        unsupported_policy_behavior=unsupported_behavior,
        execution_policy_source=source or "registry defaults",
        execution_policy_override_reason=override_reason,
        executor_source=executor_source,
        model_source=model_source,
        effort_source=effort_source,
        fallback_source=fallback_source,
        fallback_applied=fallback_applied,
    )


def _claude_model_needs_claude_executor(
    executor: str,
    model: str,
    policy: ExecutionPolicyRule | None,
) -> bool:
    if executor != "pi":
        return False
    if policy is not None and policy.executor == "pi" and policy.override_reason:
        return False
    return model.lower().startswith(("claude", "anthropic/claude"))


def resolve_model_selection_from_policy(
    *,
    profile: str,
    resolved_policy: ResolvedExecutionPolicy,
) -> ModelSelection:
    return ModelSelection(
        profile=profile,
        model=resolved_policy.model,
        effort=resolved_policy.effort,
        source=resolved_policy.execution_policy_source,
        override_reason=resolved_policy.execution_policy_override_reason,
    )


def _first_policy(
    plan_policy: ExecutionPolicyRule | None,
    roadmap_policy: ExecutionPolicyRule | None,
) -> tuple[ExecutionPolicyRule | None, str | None]:
    if plan_policy is not None:
        return plan_policy, "phase-plan policy"
    if roadmap_policy is not None:
        return roadmap_policy, "roadmap policy"
    return None, None


def _resolve_policy_model(
    executor: str,
    work_unit_kind: str,
    model: str,
    fallback: str | None,
    unsupported_behavior: str,
) -> str:
    capability = provider_policy_capabilities()[executor]
    if not capability.model_aliases:
        return model
    allowed = set(capability.model_aliases.values())
    default_alias = capability.model_aliases.get(work_unit_kind)
    if model in allowed:
        return model
    if unsupported_behavior == "inherit_default" and default_alias:
        return default_alias
    if unsupported_behavior == "fallback" and fallback in allowed:
        return fallback
    if model.startswith("phase-loop-"):
        raise ValueError(f"unsupported model `{model}` for provider `{executor}`")
    return model
