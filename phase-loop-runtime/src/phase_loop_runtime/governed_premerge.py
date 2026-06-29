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

from dataclasses import dataclass
from typing import Callable, Sequence

from .closeout_validators import ReviewFinding
from .governed_review import GateResult, governed_planning_gate
from .panel_invoker import invoke_panel

#: A class is escalated once it has failed this many times (tests or patch retries).
ESCALATION_FAIL_THRESHOLD = 2
#: Default cap on governed pre-merge review→fix rounds (bounded; never infinite).
DEFAULT_MAX_REVIEW_ROUNDS = 3

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
    max_rounds: int = DEFAULT_MAX_REVIEW_ROUNDS,
) -> LoopResult:
    """Bounded governed pre-merge review loop.

    Autonomous mode is a no-op (mergeable, no panel). Governed mode reviews up to
    ``max_rounds`` times, applying ``apply_fix`` between rounds while ``block``
    findings remain. Terminals are always non-human. ``author_vendors`` (the set
    of vendors that authored the phase) takes precedence over ``author_executor``
    for reviewer≠author exclusion when provided.
    """
    if run_mode != "governed":
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
        gate = invoke(
            artifact=current,
            author_executor=author_executor,
            author_vendors=author_vendors,
            run_mode="governed",
            available_legs=available_legs,
            spawn=spawn,
        )
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
