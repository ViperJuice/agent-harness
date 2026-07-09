from __future__ import annotations

from .capability_registry import (
    CLAUDE_HEAVY_MODEL,
    DEFAULT_EXECUTOR,
    default_model_profile_for_executor,
    provider_policy_capabilities,
)
from .models import (
    ExecutionPolicyRule,
    ModelSelection,
    ResolvedExecutionPolicy,
    WorkUnitPolicy,
    require_literal,
)


OPENAI_HEAVY_MODEL = "gpt-5.6-sol"
OPENCODE_OPENAI_HEAVY_MODEL = "openai/gpt-5.6-sol"
GEMINI_PRO_ROUTED_MODEL = "pro"
GEMINI_AUTO_ROUTED_MODEL = "auto"
GEMINI_FLASH_MODEL = "Gemini 3.5 Flash (High)"
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

# --- model-routing-v1: vendor-agnostic model_class -> concrete model ----------
# Where a provider exposes no separate implementer/worker tier, all classes map
# to its single model (pi). Non-`phase-loop-` model strings pass through
# `_resolve_policy_model` unchanged for every executor (claude/codex have no
# model_aliases; gemini/pi pass through non-alias strings), so these resolve.
CLAUDE_IMPLEMENTER_MODEL = "claude-sonnet-5"
CLAUDE_WORKER_MODEL = "claude-haiku-4-5"
OPENAI_IMPLEMENTER_MODEL = "gpt-5.6-terra"
OPENAI_WORKER_MODEL = "gpt-5.6-luna"
OPENCODE_OPENAI_IMPLEMENTER_MODEL = "openai/gpt-5.6-terra"
OPENCODE_OPENAI_WORKER_MODEL = "openai/gpt-5.6-luna"
# Gemini planner stays on the CLI `pro` alias; bounded implementer/worker lanes
# use the validated agy model name directly rather than the broad `auto` alias.
GEMINI_IMPLEMENTER_MODEL = GEMINI_FLASH_MODEL
GEMINI_WORKER_MODEL = GEMINI_FLASH_MODEL

CLASS_MODEL_OVERRIDES = {
    "claude": {
        "planner": CLAUDE_HEAVY_MODEL,
        "implementer": CLAUDE_IMPLEMENTER_MODEL,
        "worker": CLAUDE_WORKER_MODEL,
    },
    "codex": {
        "planner": OPENAI_HEAVY_MODEL,
        "implementer": OPENAI_IMPLEMENTER_MODEL,
        "worker": OPENAI_WORKER_MODEL,
    },
    "opencode": {
        "planner": OPENCODE_OPENAI_HEAVY_MODEL,
        "implementer": OPENCODE_OPENAI_IMPLEMENTER_MODEL,
        "worker": OPENCODE_OPENAI_WORKER_MODEL,
    },
    "gemini": {
        "planner": GEMINI_PRO_ROUTED_MODEL,
        "implementer": GEMINI_IMPLEMENTER_MODEL,
        "worker": GEMINI_WORKER_MODEL,
    },
    "pi": {
        "planner": PI_AUTO_ROUTED_MODEL,
        "implementer": PI_AUTO_ROUTED_MODEL,
        "worker": PI_AUTO_ROUTED_MODEL,
    },
}


def resolve_model_class(executor: str, model_class: str) -> str | None:
    """Map (model_class, executor) -> concrete model, or None if unmapped."""
    return CLASS_MODEL_OVERRIDES.get(executor, {}).get(model_class)


# Actions that author a final patch. The `worker` class (bounded, high-volume
# subtasks) must never own these — enforced as a routing invariant (P5).
PATCH_AUTHORING_ACTIONS: tuple[str, ...] = ("execute", "repair")


def max_effort_planner_eligible(executor: str) -> bool:
    """True iff `executor`'s planner-class model can actually run at `max` effort.

    The "planner of record" for a max-effort planning action must deliver max
    reasoning. Gemini ceilings at `high` (its `effort_map` clamps `max -> high`),
    so it is NOT eligible as the max-effort planner of record — it serves as a
    panel member instead, never the authoritative planner. This is the
    dispatch-selection guard the effort clamp alone does not provide: the clamp
    keeps gemini from *running* at max, but only this guard keeps it from being
    *selected* as the max-effort planner.
    """
    capability = provider_policy_capabilities().get(executor)
    return bool(capability and "max" in capability.supported_efforts)


# The repo's SHIPPED model_policy. THIS repo's default: planning at max,
# implementation at the implementer model. `clamp=True` resolves a sub-max
# provider's `max` request to its ceiling via the provider effort_map fallback
# (otherwise normalize_provider_effort RAISES). A downstream repo that ships no
# policy keeps the registry defaults — that empty-policy path is the back-compat
# contract (callers pass model_policy_rule=None to get it).
SHIPPED_MODEL_POLICY = {
    "roadmap": {"model_class": "planner", "effort": "max", "clamp": True},
    "plan": {"model_class": "planner", "effort": "max", "clamp": True},
    "execute": {"model_class": "implementer", "effort": "medium"},
    "repair": {"model_class": "implementer", "effort": "medium"},
    "review": {"model_class": "planner", "effort": "high", "clamp": True},
}


def shipped_model_policy_rule(action: str) -> ExecutionPolicyRule | None:
    """The shipped model_policy rule for an action, or None if unmapped."""
    spec = SHIPPED_MODEL_POLICY.get(action)
    if spec is None:
        return None
    clamp = bool(spec.get("clamp", False))
    return ExecutionPolicyRule(
        selector=action,
        action=action,
        model_class=spec.get("model_class"),
        effort=spec.get("effort"),
        unsupported_policy_behavior="fallback" if clamp else "block",
        fallback="high" if clamp else None,
        source="model_policy",
        override_reason="shipped model_policy (model-routing-v1)",
    )


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
    model_policy_rule: ExecutionPolicyRule | None = None,
    lane: str | None = None,
) -> ResolvedExecutionPolicy:
    require_literal(action, tuple(ACTION_WORK_UNITS.keys()), "execution policy action")
    policy, source = _merge_policies(plan_policy, roadmap_policy, model_policy_rule)
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
        elif policy.model_class is not None:
            # model_class -> concrete model for the resolved executor. An
            # explicit `model` always wins; a class only fills in when no model
            # is given (model-routing-v1).
            class_model = resolve_model_class(policy_executor, policy.model_class)
            if class_model is not None:
                policy_model = class_model
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

    # model-routing-v1 guard (wired, not just asserted): an executor whose
    # planner-class model cannot actually run at `max` (gemini/pi) must never be
    # the max-effort planner of record. Force the clamp so its `max` request
    # resolves to the provider ceiling instead of raising, regardless of whether
    # the policy opted into a fallback. This makes the effort clamp + this guard
    # jointly enforce the invariant at the dispatch-resolution boundary.
    policy_model_class = policy.model_class if policy else None
    if (
        policy_model_class == "planner"
        and policy_effort == "max"
        and not max_effort_planner_eligible(policy_executor)
    ):
        unsupported_behavior = "fallback"
        if not fallback:
            fallback = "high"

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
        model_class=policy.model_class if policy else None,
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
        model_class=resolved_policy.model_class,
    )


def _merge_policies(
    plan_policy: ExecutionPolicyRule | None,
    roadmap_policy: ExecutionPolicyRule | None,
    model_policy_rule: ExecutionPolicyRule | None = None,
) -> tuple[ExecutionPolicyRule | None, str | None]:
    # Precedence: plan > roadmap > model_policy > registry defaults — but LAYERED,
    # not winner-take-all. A higher-precedence policy overrides only the fields it
    # specifies; the rest fall through to the lower layer. This is the fix for the
    # tiering-bypass bug: a plan policy that pins only `executor=`/`effort=` (no
    # model/model_class) still inherits the shipped model_policy's `model_class`
    # and its clamp, instead of silently reverting to the registry heavy model.
    layers = [
        (model_policy_rule, "model_policy"),
        (roadmap_policy, "roadmap policy"),
        (plan_policy, "phase-plan policy"),
    ]
    present = [(rule, src) for rule, src in layers if rule is not None]
    if not present:
        return None, None
    top_rule, top_source = present[-1]
    merged: dict[str, object] = {
        "selector": top_rule.selector,
        "action": top_rule.action,
        "lane": top_rule.lane,
        "executor": None,
        "model": None,
        "model_class": None,
        "effort": None,
        "work_unit_kind": None,
        "unsupported_policy_behavior": "block",
        "fallback": None,
        "inherit_default": False,
        "source": top_source,
        "override_reason": None,
    }
    for rule, _src in present:  # low → high overlay
        for field_name in ("executor", "model", "model_class", "effort",
                           "work_unit_kind", "fallback", "override_reason", "action", "lane"):
            value = getattr(rule, field_name)
            if value is not None:
                merged[field_name] = value
        if rule.unsupported_policy_behavior and rule.unsupported_policy_behavior != "block":
            merged["unsupported_policy_behavior"] = rule.unsupported_policy_behavior
        if rule.inherit_default:
            merged["inherit_default"] = True
    return ExecutionPolicyRule(**merged), top_source


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
