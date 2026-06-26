from __future__ import annotations

from pathlib import Path

from .capability_registry import resolve_dispatch_decision
from .discovery import dispatch_hints_for_action, parse_dispatch_hints, parse_plan_ownership
from .models import DelegationDecision, DelegationRequest, DispatchHints


DEFAULT_MAX_DELEGATION_DEPTH = 2
DEFAULT_MAX_DELEGATION_FANOUT = 2
DELEGATION_CHILD_ACTIONS = ("execute", "repair", "review")
DELEGATION_CHILD_EXECUTORS = ("codex", "claude")


def validate_delegation_request(
    repo: Path,
    roadmap: Path,
    plan: Path | None,
    request: DelegationRequest,
    *,
    active_loop_mode: str,
    current_depth: int = 0,
    current_fanout: int = 0,
    max_depth: int = DEFAULT_MAX_DELEGATION_DEPTH,
    max_fanout: int = DEFAULT_MAX_DELEGATION_FANOUT,
    dry_run: bool = False,
) -> DelegationDecision:
    if active_loop_mode != "product":
        return _deny(
            request,
            "active_loop_mode_denied",
            f"Delegation requires product loop mode; current mode is {active_loop_mode}.",
            observed_depth=current_depth,
            observed_fanout=current_fanout,
        )

    if request.product_action not in DELEGATION_CHILD_ACTIONS:
        return _deny(
            request,
            "unsupported_product_action",
            "Delegation requests may only externalize execute, repair, or review child work.",
            observed_depth=current_depth,
            observed_fanout=current_fanout,
        )

    if request.target_executor not in DELEGATION_CHILD_EXECUTORS:
        return _deny(
            request,
            "unsupported_target_executor",
            "Delegation requests may target only codex or claude child executors.",
            observed_depth=current_depth,
            observed_fanout=current_fanout,
        )

    ownership = parse_plan_ownership(repo, roadmap, plan)
    if not ownership.valid:
        return _deny(
            request,
            "ownership_contract_invalid",
            "Delegation denied because the active phase plan ownership contract is invalid or incomplete.",
            observed_depth=current_depth,
            observed_fanout=current_fanout,
        )

    if not request.owned_files:
        return _deny(
            request,
            "missing_owned_files_claim",
            "Delegation requests must claim at least one owned file path or glob.",
            observed_depth=current_depth,
            observed_fanout=current_fanout,
        )

    invalid_paths = tuple(path for path in request.owned_files if not ownership.matches(path))
    if invalid_paths:
        return _deny(
            request,
            "owned_files_out_of_bounds",
            f"Delegation denied because owned-file claims escape the active phase contract: {', '.join(invalid_paths)}.",
            observed_depth=current_depth,
            observed_fanout=current_fanout,
        )

    next_depth = current_depth + 1
    if next_depth > max_depth:
        return _deny(
            request,
            "depth_limit_exceeded",
            f"Delegation depth {next_depth} exceeds the configured limit {max_depth}.",
            observed_depth=next_depth,
            observed_fanout=current_fanout,
        )

    next_fanout = current_fanout + 1
    if next_fanout > max_fanout:
        return _deny(
            request,
            "fanout_limit_exceeded",
            f"Delegation fanout {next_fanout} exceeds the configured limit {max_fanout}.",
            observed_depth=next_depth,
            observed_fanout=next_fanout,
        )

    if request.budget is None or not request.budget.is_defined():
        return _deny(
            request,
            "missing_budget_metadata",
            "Delegation requests must include metadata-only budget guidance before the runner can approve them.",
            observed_depth=next_depth,
            observed_fanout=next_fanout,
        )

    plan_hints = dispatch_hints_for_action(parse_dispatch_hints(plan, kind="plan"), request.product_action) if plan else None
    roadmap_hints = dispatch_hints_for_action(parse_dispatch_hints(roadmap, kind="roadmap"), request.product_action)
    operator_hints = DispatchHints(
        preferred_executors=(request.target_executor,),
        allowed_executors=(request.target_executor,),
        source="delegation",
        action=request.product_action,
    )
    dispatch_decision = resolve_dispatch_decision(
        action=request.product_action,
        dry_run=dry_run,
        operator=operator_hints,
        plan=plan_hints,
        roadmap=roadmap_hints,
    )
    if dispatch_decision.blocked:
        return _deny(
            request,
            "dispatch_policy_denied",
            dispatch_decision.blocked_summary or "Delegation denied by dispatch policy.",
            dispatch_decision=dispatch_decision.to_json(),
            observed_depth=next_depth,
            observed_fanout=next_fanout,
        )
    if dispatch_decision.selected_executor != request.target_executor:
        return _deny(
            request,
            "dispatch_executor_mismatch",
            (
                f"Delegation requested {request.target_executor} but dispatch resolved "
                f"{dispatch_decision.selected_executor or 'none'}."
            ),
            dispatch_decision=dispatch_decision.to_json(),
            observed_depth=next_depth,
            observed_fanout=next_fanout,
        )
    return DelegationDecision(
        request_id=request.request_id,
        status="approved",
        reason_code="approved",
        summary=f"Delegation approved for {request.product_action} on {request.target_executor}.",
        selected_executor=request.target_executor,
        dispatch_decision=dispatch_decision.to_json(),
        observed_depth=next_depth,
        observed_fanout=next_fanout,
    )


def _deny(
    request: DelegationRequest,
    reason_code: str,
    summary: str,
    *,
    dispatch_decision: dict | None = None,
    observed_depth: int | None = None,
    observed_fanout: int | None = None,
) -> DelegationDecision:
    return DelegationDecision(
        request_id=request.request_id,
        status="denied",
        reason_code=reason_code,
        summary=summary,
        dispatch_decision=dispatch_decision,
        human_required=False,
        blocker_class="repeated_verification_failure",
        observed_depth=observed_depth,
        observed_fanout=observed_fanout,
    )
