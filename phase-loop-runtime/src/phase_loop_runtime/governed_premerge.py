"""Implementation escalation ladder + governed pre-merge review loop
(model-routing-v1 P3).

Two behaviors, both **mode-branched** and **non-human terminal**:

1. ``next_escalation`` — the failure ladder. On repeated failure the model_class
   escalates ``implementer → planner``; a still-failing planner then forks by
   run_mode: ``governed`` invokes the panel, ``autonomous`` terminates as a
   **repairable non-human blocker** (``repeated_verification_failure``, never
   ``human_required``).

2. ``run_governed_premerge_loop`` — the bounded pre-merge gate. In ``governed``
   mode it runs panel review → fix → re-review, capped at ``max_rounds`` (default
   3). Zero ``block`` findings ⇒ mergeable (``nit`` findings recorded, non-gating).
   Non-convergence — or losing the panel while still failing — terminates as a
   non-human ``review_gate_block`` + halt, surfaced in the run-end summary. In
   ``autonomous`` mode the loop does NOT run and never spawns a panel.

These are pure, runner-importable functions with a mockable panel seam; nothing
here adds ``human_required`` and the autonomous path is a literal no-op.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from .closeout_validators import ReviewFinding
from .fab_canonical import EquivalenceBinding
from .fab_canonical import equivalent as fab_equivalent
from .fab_provenance import EQUIVALENCE_EQUIVALENT, EquivalenceResult
from .governed_review import GateResult, governed_planning_gate
from .panel_invoker import invoke_panel

#: A class is escalated once it has failed this many times (tests or patch retries).
ESCALATION_FAIL_THRESHOLD = 2
#: Default cap on governed pre-merge review→fix rounds (bounded; never infinite).
DEFAULT_MAX_REVIEW_ROUNDS = 3

#: FAB (Consiliency/agent-harness#191) design §4.4 promotion-time
#: re-assertion — activation milestone piece 1. Opt-in env control mirroring
#: `closeout_validators.REVIEW_MODE_ENV`'s posture: default OFF/absent means
#: no PRODUCTION caller (`train_runner._live_merge_pr`,
#: `runner.governed_premerge_for_run`) even ATTEMPTS to construct a
#: `FabPromotionCheck` — the merge path stays byte-for-byte unchanged. This
#: flag does NOT itself gate `run_governed_premerge_loop`/
#: `_fab_promotion_override`, which already branch on
#: ``fab_promotion_check is None`` regardless of any env var (so tests can
#: exercise the override directly without setting this); it gates whether
#: the PRODUCTION wiring is willing to even look for FAB provenance.
FAB_PROMOTION_ENV = "PHASE_LOOP_FAB"


def fab_promotion_enabled(env: Mapping[str, str] | None = None) -> bool:
    """True iff ``PHASE_LOOP_FAB`` is set to a truthy token (``1``/``true``/
    ``yes``/``on``, case-insensitive). Unset — or any other value — is OFF,
    the byte-neutral default."""
    env = os.environ if env is None else env
    value = str(env.get(FAB_PROMOTION_ENV) or "").strip().lower()
    return value in {"1", "true", "yes", "on"}

# implementer → planner is the only model_class escalation; planner is terminal.
_NEXT_CLASS = {"implementer": "planner", "worker": "implementer"}


def _non_human_blocker(blocker_class: str, summary: str) -> dict:
    """Every terminal in this module is a repairable, non-human blocker."""
    return {
        "human_required": False,
        "blocker_class": blocker_class,
        "blocker_summary": summary,
        "required_human_inputs": (),
    }


@dataclass(frozen=True)
class EscalationDecision:
    action: str          # "retry" | "escalate_class" | "invoke_panel" | "terminal_blocker"
    model_class: str     # the class to use for the next attempt
    run_mode: str
    reason: str
    blocker: dict | None = None   # set iff action == "terminal_blocker" (non-human)


def next_escalation(
    *,
    model_class: str,
    failed_tests: int = 0,
    patch_retries: int = 0,
    run_mode: str = "autonomous",
) -> EscalationDecision:
    """The mode-branched failure ladder.

    Below threshold → ``retry`` (same class). At/over threshold:
    ``implementer → planner`` (escalate_class); a failing ``planner`` forks by
    run_mode — ``governed`` ⇒ ``invoke_panel``, ``autonomous`` ⇒ non-human
    ``terminal_blocker``.
    """
    failing = failed_tests >= ESCALATION_FAIL_THRESHOLD or patch_retries >= ESCALATION_FAIL_THRESHOLD
    if not failing:
        return EscalationDecision(
            action="retry", model_class=model_class, run_mode=run_mode,
            reason="below escalation threshold",
        )
    nxt = _NEXT_CLASS.get(model_class)
    if nxt is not None:
        return EscalationDecision(
            action="escalate_class", model_class=nxt, run_mode=run_mode,
            reason=f"{model_class} failed threshold → escalate to {nxt}",
        )
    # planner (terminal class) still failing → branch by mode.
    if run_mode == "governed":
        return EscalationDecision(
            action="invoke_panel", model_class=model_class, run_mode=run_mode,
            reason="planner tier failing in governed mode → invoke advisor panel",
        )
    return EscalationDecision(
        action="terminal_blocker", model_class=model_class, run_mode=run_mode,
        reason="planner tier failing in autonomous mode → non-human terminal",
        blocker=_non_human_blocker(
            "repeated_verification_failure",
            "implementer→planner escalation exhausted; autonomous run halts as a "
            "repairable non-human blocker (no panel, no human_required)",
        ),
    )


@dataclass(frozen=True, kw_only=True)
class FabPromotionCheck:
    """FAB (Consiliency/agent-harness#191) Lane D, design §4.4 — the
    promotion-time re-assertion binding. Carries the bound tuple
    `(binding.repo_slug, binding.base_ref_name, binding.base_sha,
    binding.expected_head_digest)` FAB's gate recorded (re-derive via
    `fab_gate.resolve_equivalence_binding(artifact)` against the SAME trusted
    provenance the gate read — `GateStatus` itself does not carry enough to
    re-derive this, see `fab_gate`'s module docstring resolved-ambiguity #1)
    plus the LIVE PR values to re-run `fab_canonical.equivalent()` against
    immediately before merge. `live_base_ref_name`/`live_head_sha` MUST be
    resolved by the caller from the LIVE PR/host state AT MERGE TIME (never a
    cached value from when the gate originally passed) — this is exactly the
    "conflict resolved outside the head after a pass" backstop design §4.2's
    residual closure describes; it holds even if GitHub's "require branches
    up to date" branch-protection setting is absent or misconfigured."""

    binding: EquivalenceBinding
    repo_dir: str | Path
    live_base_ref_name: str
    live_head_sha: str
    origin: str = "origin"


def _fab_promotion_override(
    check: FabPromotionCheck | None,
    equivalent_fn: Callable[..., EquivalenceResult],
) -> LoopResult | None:
    """design §4.4: re-run `equivalent()` against the LIVE PR immediately
    before merge. Returns a blocking, non-human `LoopResult` iff `check` is
    supplied and equivalence no longer holds; returns `None` (no override)
    when there is nothing to check or it still holds — the caller's own
    `mergeable=True` result is left untouched in that case."""
    if check is None:
        return None
    result = equivalent_fn(
        check.binding,
        check.repo_dir,
        live_base_ref_name=check.live_base_ref_name,
        live_head_sha=check.live_head_sha,
        origin=check.origin,
    )
    if result.result == EQUIVALENCE_EQUIVALENT:
        return None
    return LoopResult(
        mergeable=False,
        ran=True,
        rounds=0,
        terminal_blocker=_non_human_blocker(
            "review_gate_block",
            "FAB promotion-time re-assertion failed immediately before merge (design §4.4): "
            f"{result.reason} — refusing to merge (non-human, agent-recoverable)",
        ),
        reason="fab_promotion_reassertion_failed",
    )


def fab_promotion_refusal_reason(
    check: FabPromotionCheck | None,
    equivalent_fn: Callable[..., EquivalenceResult] = fab_equivalent,
) -> str | None:
    """Public wrapper around `_fab_promotion_override` for a REAL merge
    caller that is not reached through `run_governed_premerge_loop` itself —
    activation milestone piece 1's other production choke point,
    `train_runner._live_merge_pr`'s `gh pr merge` (the loop's own
    ``fab_promotion_check`` covers only the loop's TWO ``mergeable=True``
    return points, :213/:269; a caller whose real merge command lives
    elsewhere must invoke this immediately before issuing it, per this
    module's own §4.4 docstring). Reuses `_fab_promotion_override` rather
    than re-implementing the equivalence re-check.

    Returns ``None`` when the merge may proceed (``check`` is ``None``, or
    the live re-assertion still finds `EQUIVALENT`); returns the non-human
    blocker's ``blocker_summary`` text (never raises) when a supplied
    check's live re-assertion fails — the caller decides how to surface that
    (e.g. as a ``RuntimeError``) in its own idiom."""
    override = _fab_promotion_override(check, equivalent_fn)
    if override is None:
        return None
    blocker = override.terminal_blocker or {}
    return blocker.get("blocker_summary") or override.reason or "fab_promotion_reassertion_failed"


@dataclass(frozen=True)
class LoopResult:
    mergeable: bool
    ran: bool
    rounds: int = 0
    findings: tuple[ReviewFinding, ...] = ()
    degraded: bool = False
    terminal_blocker: dict | None = None
    reason: str | None = None


def run_governed_premerge_loop(
    *,
    artifact: str,
    author_executor: str,
    run_mode: str,
    author_vendors: Sequence[str] | None = None,
    apply_fix: Callable[[int, str, tuple[ReviewFinding, ...]], str] | None = None,
    available_legs: Sequence[str] | None = None,
    invoke: Callable[..., GateResult] = governed_planning_gate,
    spawn=None,
    repo_dir=None,
    max_rounds: int = DEFAULT_MAX_REVIEW_ROUNDS,
    max_concurrency: int | None = None,
    fab_promotion_check: FabPromotionCheck | None = None,
    fab_equivalent_fn: Callable[..., EquivalenceResult] = fab_equivalent,
) -> LoopResult:
    """Bounded governed pre-merge review loop — ALSO the FAB (Consiliency/
    agent-harness#191) design §4.4 promotion-time re-assertion host: this
    function is "the pre-merge gate" that design §4.4 names, so it is the
    natural place to re-run FAB's §4 `equivalent()` check against the LIVE PR
    IMMEDIATELY before merge, independent of ``run_mode``.

    Autonomous mode is a no-op (mergeable, no panel). Governed mode reviews up to
    ``max_rounds`` times, applying ``apply_fix`` between rounds while ``block``
    findings remain. Terminals are always non-human. ``author_vendors`` (the set
    of vendors that authored the phase) takes precedence over ``author_executor``
    for reviewer≠author exclusion when provided.

    ``fab_promotion_check`` (default ``None`` — byte-for-byte unchanged
    behavior for every existing caller) is checked as the LAST step before
    EITHER of this function's two ``mergeable=True`` outcomes (the autonomous
    no-op and a converged governed round): when supplied, a non-EQUIVALENT
    live re-check overrides an otherwise-mergeable result into a non-human
    ``review_gate_block`` — the runtime fail-closed backstop for "conflict
    resolved outside the head after a pass" (design §4.2/§4.4), independent
    of whether GitHub's "require branches up to date" branch-protection
    setting is configured.
    """
    if run_mode != "governed":
        override = _fab_promotion_override(fab_promotion_check, fab_equivalent_fn)
        if override is not None:
            return override
        return LoopResult(mergeable=True, ran=False, reason="autonomous")

    # Reasons that mean "the gate could not run a real review" (no disjoint
    # reviewer / unknown author / no usable verdict) — surfaced verbatim in the
    # terminal so the operator sees the ACCURATE cause, not a generic
    # "non_convergence" (CR finding).
    _STRUCTURAL_HOLD = frozenset({
        "unknown_author", "no_disjoint_reviewer", "author_vendor_only",
        "no_reviewers", "no_usable_review",
    })
    seen_block = False
    current = artifact
    collected: list[ReviewFinding] = []
    rnd = 0
    last_reason: str | None = None
    for rnd in range(1, max_rounds + 1):
        invoke_kwargs = {
            "artifact": current,
            "author_executor": author_executor,
            "author_vendors": author_vendors,
            "run_mode": "governed",
            "available_legs": available_legs,
            "spawn": spawn,
        }
        if repo_dir is not None:
            invoke_kwargs["repo_dir"] = repo_dir
        # Parallel by default; threaded only when a caller requests a cap/sequential
        # (byte-neutral for the default path + a strict-signature custom ``invoke``).
        if max_concurrency is not None:
            invoke_kwargs["max_concurrency"] = max_concurrency
        gate = invoke(**invoke_kwargs)
        collected.extend(gate.findings)
        last_reason = gate.reason

        if gate.degraded:
            # FAIL-CLOSED (advisor-panel reconciliation): in governed mode, "no
            # usable disjoint reviewer" is NOT an advisory pass — the prior
            # advisory-pass-before-any-block was a fail-open (a codex-empty phase
            # whose only disjoint reviewer was offline both rendered an empty diff
            # AND advisory-passed). The gate now blocks on no-usable-review
            # directly (promoted=False), so reaching here means a degraded result
            # we treat as a non-human halt regardless of `seen_block`.
            return LoopResult(
                mergeable=False, ran=True, rounds=rnd, findings=tuple(collected),
                terminal_blocker=_non_human_blocker(
                    "review_gate_block",
                    f"governed pre-merge review could not obtain a usable disjoint "
                    f"reviewer after {rnd} round(s); halting (non-human)",
                ),
                reason=gate.reason or "panel_unavailable",
            )

        if gate.promoted:  # zero block findings; any nits already in `collected`
            override = _fab_promotion_override(fab_promotion_check, fab_equivalent_fn)
            if override is not None:
                return LoopResult(
                    mergeable=False, ran=True, rounds=rnd, findings=tuple(collected),
                    terminal_blocker=override.terminal_blocker, reason=override.reason,
                )
            return LoopResult(
                mergeable=True, ran=True, rounds=rnd, findings=tuple(collected),
            )

        # Unresolved block findings this round.
        seen_block = True
        if apply_fix is None or rnd == max_rounds:
            break
        current = apply_fix(rnd, current, gate.findings)

    # Fell out of the loop while held. Distinguish a fail-closed STRUCTURAL hold
    # (no disjoint reviewer / unknown author / no usable verdict — the review never
    # really ran) from genuine NON-CONVERGENCE (real block findings unresolved), so
    # the terminal carries the accurate cause instead of always "non_convergence".
    if last_reason in _STRUCTURAL_HOLD:
        summary = (
            f"governed pre-merge review held ({last_reason}) — no real review could "
            f"run after {rnd} round(s); halting (non-human, surfaced in run-end summary)"
        )
        reason = last_reason
    else:
        summary = (
            f"governed pre-merge review did not converge to zero block findings "
            f"after {rnd} round(s); halting (non-human, surfaced in run-end summary)"
        )
        reason = "non_convergence"
    return LoopResult(
        mergeable=False, ran=True, rounds=rnd, findings=tuple(collected),
        terminal_blocker=_non_human_blocker("review_gate_block", summary),
        reason=reason,
    )
