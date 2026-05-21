from __future__ import annotations

from pathlib import Path

from .models import (
    ClaudeTeamPolicy,
    DispatchDecision,
    DispatchHints,
    ExecutorCapabilityRecord,
    NORMALIZED_EFFORT_LEVELS,
    PRODUCT_LOOP_ACTIONS,
    ProviderPolicyCapability,
    UNSUPPORTED_POLICY_BEHAVIORS,
    WORK_UNIT_KINDS,
)
from .state_degradation import active_degraded_executors


DEFAULT_EXECUTOR = "codex"
DEFAULT_LANE_EXECUTOR = "pi"
CLAUDE_HEAVY_MODEL = "claude-opus-4-7"
_CLAUDE_BASE_ALLOWED_TOOLS = ("Bash", "Read", "Edit", "MultiEdit", "Write", "Glob", "Grep", "LS")
_CLAUDE_COLLABORATION_TOOLS = (
    "Agent",
    "TaskCreate",
    "TaskUpdate",
    "TaskList",
    "TeamCreate",
    "TeamDelete",
    "SendMessage",
    "EnterWorktree",
    "ExitWorktree",
    "ToolSearch",
)
_CLAUDE_BASE_DISALLOWED_TOOLS = (
    "Agent",
    "TaskCreate",
    "TaskUpdate",
    "TaskList",
    "TeamCreate",
    "TeamDelete",
    "SendMessage",
    "EnterWorktree",
    "ExitWorktree",
    "AskUserQuestion",
    "ExitPlanMode",
    "ToolSearch",
    "advisor",
)
_CLAUDE_SUBAGENT_ALLOWED_TOOLS = _CLAUDE_BASE_ALLOWED_TOOLS + (
    "Agent",
    "TaskCreate",
    "TaskUpdate",
    "TaskList",
    "SendMessage",
    "ToolSearch",
)
_CLAUDE_TEAM_ALLOWED_TOOLS = _CLAUDE_BASE_ALLOWED_TOOLS + _CLAUDE_COLLABORATION_TOOLS
_CLAUDE_TEAM_DISALLOWED_TOOLS = (
    "AskUserQuestion",
    "ExitPlanMode",
    "advisor",
)

_ACTION_DEFAULT_PROFILES = {
    "roadmap": "roadmap",
    "plan": "plan",
    "execute": "execute",
    "repair": "repair",
    "review": "review",
    "maintain-skills": "skill-maintenance",
}

DEFAULT_CAPABILITY_REGISTRY = {
    "codex": ExecutorCapabilityRecord(
        executor="codex",
        supported_actions=PRODUCT_LOOP_ACTIONS,
        capabilities=(
            "live_launch",
            "dry_run",
            "skill_bundle_injection",
            "subagents",
            "explicit_approval_controls",
            "structured_output",
        ),
        strengths=("repo-native execution", "stable automation contract", "shared phase-loop ownership"),
        limits=("requires local Codex subscription auth",),
        injection_mode="prompt_only",
        permission_posture="explicit",
        subagent_posture="native",
        live_available=True,
        dry_run_available=True,
        live_proof_gate="none",
        promotion_status="live",
        promotion_requirements=("shared launch contract", "shared terminal summary contract"),
        auth_preflight_mode="metadata_only",
        auth_preflight_probes=("codex --version", "codex --help", "codex login status"),
        timeout_posture="runner_managed",
        output_capture_format="json_stream",
        default_model_profiles=dict(_ACTION_DEFAULT_PROFILES),
    ),
    "claude": ExecutorCapabilityRecord(
        executor="claude",
        supported_actions=("roadmap", "plan", "execute", "repair", "review"),
        capabilities=(
            "live_launch",
            "dry_run",
            "skill_bundle_injection",
            "inline_instructions",
            "context_file_instructions",
            "subagents",
            "explicit_approval_controls",
            "structured_output",
        ),
        strengths=("native prompt injection", "documented effort controls", "authenticated local CLI"),
        limits=(
            "requires local Claude subscription auth",
            "current autonomous live proof is blocked on non-interactive closeout parity",
        ),
        injection_mode="inline",
        permission_posture="explicit",
        subagent_posture="native",
        live_available=True,
        dry_run_available=True,
        live_proof_gate="disposable_proof_required",
        promotion_status="proof_gated",
        promotion_requirements=(
            "fake harness parity regression proof",
            "disposable live roadmap proof",
            "launch.json",
            "terminal-summary.json",
        ),
        auth_preflight_mode="metadata_only",
        auth_preflight_probes=("claude --version", "claude --help", "claude auth status"),
        timeout_posture="runner_managed",
        output_capture_format="terminal_summary",
        known_failure_cases=(
            "non_interactive_timeout",
            "empty_or_unusable_output_capture",
            "missing_automation_block",
            "missing_terminal_summary",
            "stale_handoff_after_repair",
        ),
        default_model_profiles={action: _ACTION_DEFAULT_PROFILES[action] for action in ("roadmap", "plan", "execute", "repair", "review")},
        default_claude_execution_mode="solo",
        claude_execution_policies=(
            ClaudeTeamPolicy(
                execution_mode="solo",
                maturity_label="proof_blocked",
                live_proof_gate="disposable_proof_required",
                promotion_status="proof_gated",
                launch_default=True,
                default_model=CLAUDE_HEAVY_MODEL,
                default_effort="high",
                budget_guidance={"mode": "metadata_only", "notes": "Native solo launch keeps team fanout at zero."},
                allowed_actions=("roadmap", "plan", "execute", "repair", "review"),
                allowed_tools=_CLAUDE_BASE_ALLOWED_TOOLS,
                disallowed_tools=_CLAUDE_BASE_DISALLOWED_TOOLS,
                task_lifecycle_supported=False,
            ),
            ClaudeTeamPolicy(
                execution_mode="subagent",
                maturity_label="experimental",
                live_proof_gate="disposable_proof_required",
                promotion_status="proof_gated",
                max_native_tasks=2,
                max_delegation_depth=1,
                max_fanout=2,
                default_model=CLAUDE_HEAVY_MODEL,
                default_effort="high",
                budget_guidance={"mode": "metadata_only", "max_cost_usd": 3.0, "notes": "Bounded native subagent fanout only."},
                allowed_actions=("execute", "repair", "review"),
                disallowed_actions=("roadmap", "plan", "maintain-skills"),
                allowed_tools=_CLAUDE_SUBAGENT_ALLOWED_TOOLS,
                disallowed_tools=_CLAUDE_TEAM_DISALLOWED_TOOLS + ("TeamCreate", "TeamDelete", "EnterWorktree", "ExitWorktree"),
                requires_disjoint_owned_files=True,
                task_lifecycle_supported=True,
            ),
            ClaudeTeamPolicy(
                execution_mode="agent_team",
                maturity_label="experimental",
                live_proof_gate="disposable_proof_required",
                promotion_status="proof_gated",
                max_teammates=3,
                max_native_tasks=4,
                max_delegation_depth=1,
                max_fanout=2,
                default_model=CLAUDE_HEAVY_MODEL,
                default_effort="high",
                budget_guidance={"mode": "metadata_only", "max_cost_usd": 5.0, "notes": "Task-list or teammate activity stays gated behind TEAMGOV and TASKLEDGER evidence."},
                allowed_actions=("execute", "repair", "review"),
                disallowed_actions=("roadmap", "plan", "maintain-skills"),
                allowed_tools=_CLAUDE_TEAM_ALLOWED_TOOLS,
                disallowed_tools=_CLAUDE_TEAM_DISALLOWED_TOOLS,
                requires_disjoint_owned_files=True,
                direct_teammate_messaging_allowed=True,
                task_lifecycle_supported=True,
            ),
        ),
    ),
    "gemini": ExecutorCapabilityRecord(
        executor="gemini",
        supported_actions=("roadmap", "plan", "execute", "repair", "review"),
        capabilities=(
            "live_launch",
            "dry_run",
            "skill_bundle_injection",
            "inline_instructions",
            "context_file_instructions",
            "explicit_approval_controls",
            "structured_output",
        ),
        strengths=("native skill ecosystem", "context-file friendly", "authenticated local CLI"),
        limits=("requires local Gemini subscription auth",),
        injection_mode="context_file",
        permission_posture="explicit",
        subagent_posture="limited",
        live_available=True,
        dry_run_available=True,
        live_proof_gate="disposable_proof_recorded",
        promotion_status="live",
        promotion_requirements=(
            "fake harness parity regression proof",
            "disposable live roadmap proof",
            "launch.json",
            "terminal-summary.json",
        ),
        auth_preflight_mode="metadata_only",
        auth_preflight_probes=("gemini --version", "gemini --help"),
        timeout_posture="runner_managed",
        output_capture_format="terminal_summary",
        default_model_profiles={action: _ACTION_DEFAULT_PROFILES[action] for action in ("roadmap", "plan", "execute", "repair", "review")},
    ),
    "opencode": ExecutorCapabilityRecord(
        executor="opencode",
        supported_actions=("roadmap", "plan", "execute", "repair", "review"),
        capabilities=(
            "live_launch",
            "dry_run",
            "skill_bundle_injection",
            "inline_instructions",
            "context_file_instructions",
            "explicit_approval_controls",
            "structured_output",
        ),
        strengths=("agent-oriented CLI", "context-file delivery"),
        limits=("requires local OpenCode subscription auth", "live launch requires explicit opt-in when the selected agent posture is permissive"),
        injection_mode="context_file",
        permission_posture="explicit",
        subagent_posture="limited",
        live_available=True,
        dry_run_available=True,
        live_proof_gate="disposable_proof_recorded",
        promotion_status="live",
        promotion_requirements=(
            "fake harness parity regression proof",
            "disposable live roadmap proof",
            "launch.json",
            "terminal-summary.json",
        ),
        auth_preflight_mode="metadata_only",
        auth_preflight_probes=("opencode --version", "opencode run --help", "opencode agent list"),
        timeout_posture="runner_managed",
        output_capture_format="terminal_summary",
        default_model_profiles={action: _ACTION_DEFAULT_PROFILES[action] for action in ("roadmap", "plan", "execute", "repair", "review")},
    ),
    "pi": ExecutorCapabilityRecord(
        executor="pi",
        supported_actions=("roadmap", "plan", "execute", "repair", "review"),
        capabilities=(
            "live_launch",
            "dry_run",
            "skill_bundle_injection",
            "context_file_instructions",
            "explicit_approval_controls",
            "structured_output",
        ),
        strengths=(
            "repo-local phase-loop-pi package",
            "bounded simple-lane child runner",
            "shared automation closeout contract",
        ),
        limits=(
            "requires local Pi Agent CLI and provider auth outside phase-loop",
            "not a global scheduler, runtime ledger owner, worktree allocator, or merge reducer",
        ),
        injection_mode="context_file",
        permission_posture="explicit",
        subagent_posture="none",
        live_available=True,
        dry_run_available=True,
        live_proof_gate="disposable_proof_recorded",
        promotion_status="live",
        promotion_requirements=(
            "fake harness parity regression proof",
            "fake Pi adapter matrix",
            "launch.json",
            "terminal-summary.json",
        ),
        auth_preflight_mode="metadata_only",
        auth_preflight_probes=("pi --version", "pi --help"),
        timeout_posture="runner_managed",
        output_capture_format="terminal_summary",
        default_model_profiles={action: _ACTION_DEFAULT_PROFILES[action] for action in ("roadmap", "plan", "execute", "repair", "review")},
    ),
    "command": ExecutorCapabilityRecord(
        executor="command",
        supported_actions=("roadmap", "plan", "execute", "repair", "review"),
        capabilities=("live_launch", "dry_run", "context_file_instructions", "structured_output"),
        strengths=("explicit command wrapper", "runner-owned launch artifacts"),
        limits=("requires explicit adapter template", "non-default executor", "unsupported command shapes fail closed"),
        injection_mode="context_file",
        permission_posture="manual",
        subagent_posture="none",
        live_available=True,
        dry_run_available=True,
        live_proof_gate="none",
        promotion_status="manual_only",
        promotion_requirements=(
            "fake harness parity regression proof",
            "typed adapter contract",
            "explicit operator selection",
            "launch.json",
            "terminal-summary.json",
        ),
        timeout_posture="runner_managed",
        output_capture_format="terminal_summary",
        default_model_profiles={
            "roadmap": _ACTION_DEFAULT_PROFILES["roadmap"],
            "plan": _ACTION_DEFAULT_PROFILES["plan"],
            "execute": _ACTION_DEFAULT_PROFILES["execute"],
            "repair": _ACTION_DEFAULT_PROFILES["repair"],
            "review": _ACTION_DEFAULT_PROFILES["review"],
        },
    ),
    "manual": ExecutorCapabilityRecord(
        executor="manual",
        supported_actions=("repair", "review"),
        capabilities=("dry_run", "manual_handoff"),
        strengths=("operator handoff"),
        limits=("metadata-only until manual import completes",),
        injection_mode="manual",
        permission_posture="manual",
        subagent_posture="none",
        live_available=False,
        dry_run_available=True,
        promotion_status="manual_only",
        promotion_requirements=("manual import", "shared automation handoff"),
        timeout_posture="unknown",
        default_model_profiles={"repair": "repair", "review": "review"},
    ),
}

DEFAULT_EXECUTOR_POLICY = {
    "roadmap": "codex",
    "plan": "codex",
    "execute": "codex",
    "repair": "codex",
    "review": "codex",
    "maintain-skills": "codex",
}

_ALL_WORK_UNITS = WORK_UNIT_KINDS
_ALL_EFFORTS = NORMALIZED_EFFORT_LEVELS
_FAIL_CLOSED = UNSUPPORTED_POLICY_BEHAVIORS[0]

DEFAULT_PROVIDER_POLICY_CAPABILITIES = {
    "codex": ProviderPolicyCapability(
        provider="openai",
        executor="codex",
        supported_work_units=_ALL_WORK_UNITS,
        supported_efforts=_ALL_EFFORTS,
        unsupported_policy_behavior=_FAIL_CLOSED,
        default_effort="medium",
        effort_map={effort: effort for effort in _ALL_EFFORTS},
        notes=("Codex/OpenAI accepts normalized reasoning effort metadata directly.",),
    ),
    "claude": ProviderPolicyCapability(
        provider="claude-code",
        executor="claude",
        supported_work_units=_ALL_WORK_UNITS,
        supported_efforts=("low", "medium", "high", "max"),
        unsupported_policy_behavior=_FAIL_CLOSED,
        default_effort="high",
        effort_map={
            "minimal": "low",
            "low": "low",
            "medium": "medium",
            "high": "high",
            "xhigh": "max",
            "max": "max",
        },
        notes=("Claude Code has documented effort controls but no separate xhigh literal.",),
    ),
    "gemini": ProviderPolicyCapability(
        provider="gemini-cli",
        executor="gemini",
        supported_work_units=_ALL_WORK_UNITS,
        supported_efforts=("medium", "high"),
        unsupported_policy_behavior=_FAIL_CLOSED,
        default_effort="medium",
        effort_map={
            "minimal": "medium",
            "low": "medium",
            "medium": "medium",
            "high": "high",
            "xhigh": "high",
            "max": "high",
        },
        model_aliases={
            "roadmap_build": "phase-loop-plan-high",
            "phase_plan": "phase-loop-plan-high",
            "lane_execute": "phase-loop-execute-medium",
            "lane_review": "phase-loop-review-high",
            "phase_reducer": "phase-loop-review-high",
            "phase_verify": "phase-loop-review-high",
            "repair": "phase-loop-execute-medium",
            "closeout": "phase-loop-review-high",
        },
        requires_run_local_user_scope=True,
        notes=(
            "Gemini CLI fallback stays CLI-based and reason-coded; API-key execution requires an explicit command adapter.",
            "Gemini CLI defaults use built-in routing aliases (`pro` for planning/review and `auto` for execution/repair) to preserve CLI fallback behavior.",
            "Run-local user-scope modelConfigs.customAliases remain available only for explicit phase-loop thinking-level proof runs.",
            "thinkingConfig.thinkingLevel is carried by custom aliases and is not exposed as a CLI flag.",
        ),
    ),
    "gemini-api": ProviderPolicyCapability(
        provider="gemini-api-openai-compatible",
        executor="command",
        supported_work_units=_ALL_WORK_UNITS,
        supported_efforts=("medium", "high"),
        unsupported_policy_behavior=_FAIL_CLOSED,
        default_effort="medium",
        effort_map={
            "minimal": "medium",
            "low": "medium",
            "medium": "medium",
            "high": "high",
            "xhigh": "high",
            "max": "high",
        },
        notes=("Gemini API/OpenAI-compatible policy is command-adapter metadata only in v8.",),
    ),
    "opencode": ProviderPolicyCapability(
        provider="opencode",
        executor="opencode",
        supported_work_units=_ALL_WORK_UNITS,
        supported_efforts=_ALL_EFFORTS,
        unsupported_policy_behavior=_FAIL_CLOSED,
        default_effort="medium",
        effort_map={effort: effort for effort in _ALL_EFFORTS},
        notes=("OpenCode policy is normalized for future adapter selection without changing dispatch.",),
    ),
    "pi": ProviderPolicyCapability(
        provider="pi-agent",
        executor="pi",
        supported_work_units=_ALL_WORK_UNITS,
        supported_efforts=("low", "medium", "high"),
        unsupported_policy_behavior=_FAIL_CLOSED,
        default_effort="medium",
        effort_map={
            "minimal": "low",
            "low": "low",
            "medium": "medium",
            "high": "high",
            "xhigh": "high",
            "max": "high",
        },
        notes=(
            "Pi Agent is the default executor for simple bounded scheduler-assigned lane execution.",
            "Pi Agent runner path is repo-local and bounded to one scheduler-assigned lane.",
            "Claude/Anthropic model work defaults to Claude Code CLI unless policy explicitly selects a Pi-wrapped Claude route with an override reason.",
            "Unsupported effort or tool-policy requests fail closed unless the phase policy explicitly falls back.",
        ),
    ),
    "command": ProviderPolicyCapability(
        provider="generic-command-adapter",
        executor="command",
        supported_work_units=_ALL_WORK_UNITS,
        supported_efforts=_ALL_EFFORTS,
        unsupported_policy_behavior=_FAIL_CLOSED,
        default_effort="medium",
        effort_map={effort: effort for effort in _ALL_EFFORTS},
        notes=("Generic command adapters must fail closed when unsupported policy cannot be mapped.",),
    ),
    "manual": ProviderPolicyCapability(
        provider="manual-handoff",
        executor="manual",
        supported_work_units=_ALL_WORK_UNITS,
        supported_efforts=_ALL_EFFORTS,
        unsupported_policy_behavior=_FAIL_CLOSED,
        default_effort="medium",
        effort_map={effort: effort for effort in _ALL_EFFORTS},
        notes=("Manual handoff is non-default and must be selected explicitly.",),
    ),
}


def capability_registry() -> dict[str, ExecutorCapabilityRecord]:
    return DEFAULT_CAPABILITY_REGISTRY


def provider_policy_capabilities() -> dict[str, ProviderPolicyCapability]:
    return DEFAULT_PROVIDER_POLICY_CAPABILITIES


def claude_support_slice_posture() -> dict[str, dict[str, object]]:
    record = DEFAULT_CAPABILITY_REGISTRY["claude"]
    policy_by_mode = {
        policy.execution_mode: policy for policy in record.claude_execution_policies
    }
    solo = policy_by_mode["solo"]
    subagent = policy_by_mode["subagent"]
    agent_team = policy_by_mode["agent_team"]
    return {
        "claude_solo": {
            "execution_mode": "solo",
            "maturity_label": solo.maturity_label,
            "live_proof_gate": solo.live_proof_gate,
            "promotion_status": solo.promotion_status,
            "launch_default": solo.launch_default,
            "requires_disjoint_owned_files": solo.requires_disjoint_owned_files,
            "allows_read_only_lanes": solo.allows_read_only_lanes,
            "max_delegation_depth": 0,
            "max_fanout": 0,
            "budget_guidance": solo.budget_guidance,
        },
        "claude_delegated_worker": {
            "execution_mode": "delegated_worker",
            "maturity_label": "proof_blocked",
            "live_proof_gate": record.live_proof_gate,
            "promotion_status": record.promotion_status,
            "launch_default": False,
            "requires_disjoint_owned_files": True,
            "allows_read_only_lanes": True,
            "max_delegation_depth": 1,
            "max_fanout": 1,
            "budget_guidance": {
                "mode": "metadata_only",
                "notes": "Runner-brokered Claude child work stays bounded by typed delegation budget metadata.",
            },
        },
        "claude_subagent": {
            "execution_mode": "subagent",
            "maturity_label": subagent.maturity_label,
            "live_proof_gate": subagent.live_proof_gate,
            "promotion_status": subagent.promotion_status,
            "launch_default": subagent.launch_default,
            "requires_disjoint_owned_files": subagent.requires_disjoint_owned_files,
            "allows_read_only_lanes": subagent.allows_read_only_lanes,
            "max_delegation_depth": subagent.max_delegation_depth,
            "max_fanout": subagent.max_fanout,
            "budget_guidance": subagent.budget_guidance,
        },
        "claude_agent_team": {
            "execution_mode": "agent_team",
            "maturity_label": agent_team.maturity_label,
            "live_proof_gate": agent_team.live_proof_gate,
            "promotion_status": agent_team.promotion_status,
            "launch_default": agent_team.launch_default,
            "requires_disjoint_owned_files": agent_team.requires_disjoint_owned_files,
            "allows_read_only_lanes": agent_team.allows_read_only_lanes,
            "max_delegation_depth": agent_team.max_delegation_depth,
            "max_fanout": agent_team.max_fanout,
            "budget_guidance": agent_team.budget_guidance,
        },
    }


def claude_team_capability_posture() -> dict[str, dict[str, object]]:
    slice_posture = claude_support_slice_posture()
    return {
        "solo": slice_posture["claude_solo"],
        "subagent": slice_posture["claude_subagent"],
        "agent_team": slice_posture["claude_agent_team"],
    }


def default_executor_for_action(action: str) -> str:
    if action == "maintain-skills":
        return "codex"
    return DEFAULT_EXECUTOR_POLICY.get(action, DEFAULT_EXECUTOR)


def default_executor_for_work_unit(work_unit_kind: str, *, scheduler_assigned: bool = False) -> str:
    if scheduler_assigned and work_unit_kind == "lane_execute":
        return DEFAULT_LANE_EXECUTOR
    return default_executor_for_action("execute" if work_unit_kind == "lane_execute" else "review")


def merge_dispatch_hints(
    *,
    action: str,
    operator: DispatchHints | None = None,
    plan: DispatchHints | None = None,
    roadmap: DispatchHints | None = None,
) -> DispatchHints:
    preferred = _first_nonempty(operator, plan, roadmap, field="preferred_executors")
    allowed = _first_nonempty(operator, plan, roadmap, field="allowed_executors")
    fallback = _first_nonempty(operator, plan, roadmap, field="fallback_executors")
    disabled = _union(operator, plan, roadmap, field="disabled_executors")
    required = _union(operator, plan, roadmap, field="required_capabilities")
    source = _source_name(operator, plan, roadmap, preferred, allowed, fallback)
    return DispatchHints(
        preferred_executors=preferred,
        allowed_executors=allowed,
        fallback_executors=fallback,
        disabled_executors=disabled,
        required_capabilities=required,
        source=source,
        action=action,
    )


def resolve_dispatch_decision(
    *,
    action: str,
    dry_run: bool,
    repo: Path | None = None,
    registry: dict[str, ExecutorCapabilityRecord] | None = None,
    operator: DispatchHints | None = None,
    plan: DispatchHints | None = None,
    roadmap: DispatchHints | None = None,
) -> DispatchDecision:
    registry = registry or capability_registry()
    if action == "maintain-skills":
        return DispatchDecision(
            action=action,
            selected_executor="codex",
            source="maintain-skills-fixed",
            considered_executors=("codex",),
            selected_via="fixed_action_policy",
        )

    merged = merge_dispatch_hints(action=action, operator=operator, plan=plan, roadmap=roadmap)
    allowed = merged.allowed_executors or tuple(
        executor for executor, record in registry.items() if action in record.supported_actions
    )
    preferred = merged.preferred_executors or (default_executor_for_action(action),)
    fallback = tuple(executor for executor in merged.fallback_executors if executor not in preferred)
    candidate_order = _dedupe((*preferred, *fallback, *allowed, default_executor_for_action(action)))
    considered: list[str] = []
    degraded = active_degraded_executors(repo) if repo is not None and not dry_run else set()
    degraded_viable: list[str] = []

    for executor in candidate_order:
        considered.append(executor)
        if executor in merged.disabled_executors:
            if executor in preferred:
                return _blocked_decision(
                    action,
                    merged,
                    tuple(considered),
                    "disabled_executor",
                    f"Dispatch policy rejected `{executor}` for `{action}` because it is disabled by hints.",
                )
            continue
        if executor not in allowed:
            if executor in preferred:
                return _blocked_decision(
                    action,
                    merged,
                    tuple(considered),
                    "executor_not_allowed",
                    f"Dispatch policy rejected `{executor}` for `{action}` because it is outside the allowed executor set.",
                )
            continue
        record = registry.get(executor)
        if record is None or action not in record.supported_actions:
            if executor in preferred:
                return _blocked_decision(
                    action,
                    merged,
                    tuple(considered),
                    "unsupported_action",
                    f"Dispatch policy rejected `{executor}` for `{action}` because the registry does not support that action.",
                )
            continue
        missing = tuple(capability for capability in merged.required_capabilities if capability not in record.capabilities)
        if missing:
            if executor in preferred:
                return _blocked_decision(
                    action,
                    merged,
                    tuple(considered),
                    "missing_required_capabilities",
                    f"Dispatch policy rejected `{executor}` for `{action}` because it lacks required capabilities: {', '.join(missing)}.",
                )
            continue
        if dry_run:
            if not record.dry_run_available:
                continue
        elif not record.live_available:
            if executor in preferred and not fallback:
                return _blocked_decision(
                    action,
                    merged,
                    tuple(considered),
                    "live_launch_unavailable",
                    f"Dispatch policy selected `{executor}` for `{action}`, but that executor is currently dry-run-only.",
                )
            if executor in preferred and fallback:
                continue
            continue
        if executor in degraded:
            degraded_viable.append(executor)
            continue
        return DispatchDecision(
            action=action,
            selected_executor=executor,
            source=merged.source,
            preferred_executors=preferred,
            allowed_executors=allowed,
            fallback_executors=fallback,
            disabled_executors=merged.disabled_executors,
            required_capabilities=merged.required_capabilities,
            considered_executors=tuple(considered),
            fallback_applied=executor not in preferred,
            selected_via="fallback" if executor not in preferred else "preferred",
        )

    if degraded_viable:
        return _blocked_decision(
            action,
            merged,
            tuple(considered or candidate_order),
            "all_candidates_session_degraded",
            f"Dispatch policy could not resolve a live executor for `{action}` because all otherwise viable candidates are session-degraded.",
        )

    return _blocked_decision(
        action,
        merged,
        tuple(considered or candidate_order),
        "no_allowed_executor",
        f"Dispatch policy could not resolve an executor for `{action}` with the current hints and registry.",
    )


def describe_dispatch_decision(decision: DispatchDecision) -> str:
    if decision.blocked:
        return decision.blocked_summary or "dispatch blocked"
    parts = [f"selected `{decision.selected_executor}` via {decision.source}"]
    if decision.fallback_applied:
        parts.append("fallback applied")
    if decision.required_capabilities:
        parts.append(f"required capabilities: {', '.join(decision.required_capabilities)}")
    return "; ".join(parts)


def default_model_profile_for_executor(action: str, executor: str) -> str:
    record = capability_registry()[executor]
    return record.default_model_profiles.get(action, _ACTION_DEFAULT_PROFILES[action])


def _first_nonempty(*hints: DispatchHints | None, field: str) -> tuple[str, ...]:
    for hint in hints:
        if hint is None:
            continue
        values = getattr(hint, field)
        if values:
            return tuple(values)
    return ()


def _union(*hints: DispatchHints | None, field: str) -> tuple[str, ...]:
    values: list[str] = []
    for hint in reversed(hints):
        if hint is None:
            continue
        values.extend(getattr(hint, field))
    return _dedupe(values)


def _dedupe(values: tuple[str, ...] | list[str] | tuple[str, ...]) -> tuple[str, ...]:
    seen: list[str] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return tuple(seen)


def _source_name(
    operator: DispatchHints | None,
    plan: DispatchHints | None,
    roadmap: DispatchHints | None,
    preferred: tuple[str, ...],
    allowed: tuple[str, ...],
    fallback: tuple[str, ...],
) -> str:
    for source_name, hint in (("operator", operator), ("plan", plan), ("roadmap", roadmap)):
        if hint is None:
            continue
        if preferred and hint.preferred_executors == preferred:
            return source_name
        if allowed and hint.allowed_executors == allowed:
            return source_name
        if fallback and hint.fallback_executors == fallback:
            return source_name
    return "registry_default"


def _blocked_decision(
    action: str,
    merged: DispatchHints,
    considered: tuple[str, ...],
    blocked_reason: str,
    blocked_summary: str,
) -> DispatchDecision:
    return DispatchDecision(
        action=action,
        selected_executor=None,
        source=merged.source,
        preferred_executors=merged.preferred_executors or (default_executor_for_action(action),),
        allowed_executors=merged.allowed_executors,
        fallback_executors=merged.fallback_executors,
        disabled_executors=merged.disabled_executors,
        required_capabilities=merged.required_capabilities,
        considered_executors=considered,
        blocked_reason=blocked_reason,
        blocked_summary=blocked_summary,
        selected_via=None,
    )
