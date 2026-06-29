"""Governed planning-review gate (model-routing-v1 P2, IF-0-P2-1).

This is the **plan-stage** gate. It is deliberately NOT the closeout
`_VALIDATORS` registry: that hook fires at closeout time on every closeout and
is warn-gated by `PHASE_LOOP_REVIEW` — the wrong host for a run_mode-aware,
fail-closed, planning-stage gate (panel finding, verified). This module reuses
only the `ReviewFinding` / `block` / `nit` vocabulary.

Run modes (the second orthogonal axis; the first is `model_policy`):
- `autonomous` (default): the gate does NOT run — it returns immediately and
  **never invokes the panel** (no CLI spawn, no cost, no `human_required`).
- `governed` (opt-in): the panel reviews the artifact; `block` findings hold
  promotion, `nit` findings are recorded but non-gating.

Reviewer ≠ author: the panel pool must differ from the author in vendor. If the
only authed reviewer is the author's vendor — or none are authed — the gate
degrades to **autonomous-warn** (an advisory, recorded finding) rather than
rubber-stamping a same-vendor self-review as a pass.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Sequence

from .closeout_validators import ReviewFinding
from .panel_invoker import PanelResult, available_panel_legs, invoke_panel, terminal_verdict


RUN_MODES: tuple[str, ...] = ("autonomous", "governed")
DEFAULT_RUN_MODE = "autonomous"
RUN_MODE_ENV = "PHASE_LOOP_RUN_MODE"


def _leg_blocks(text: str) -> bool:
    """True iff a usable leg's review signals a blocking concern.

    A usable leg is conforming by construction (``_classify_leg`` only returns
    ``ok`` when ``terminal_verdict`` is non-None), so this is a pure read of the
    structured terminal verdict — only a bare ``DISAGREE`` blocks. No substring /
    negation guessing (that whack-a-mole mis-blocked "I cannot AGREE or DISAGREE"
    and mis-passed junk containing the words — advisor-panel reconciliation).
    """
    return terminal_verdict(text) == "DISAGREE"

# Executor → review "vendor" (for reviewer≠author disjointness). The panel legs
# are themselves vendor-named (codex/gemini/claude).
_EXECUTOR_VENDOR: dict[str, str] = {
    "codex": "codex",
    "opencode": "codex",   # openai-family models
    "claude": "claude",
    "gemini": "gemini",
    "pi": "pi",            # distinct → all panel legs are disjoint from a pi author
}


def author_vendor_for_executor(executor: str) -> str:
    return _EXECUTOR_VENDOR.get((executor or "").lower(), (executor or "").lower())


def author_vendor_for_model(model_id: str) -> str:
    """Map a concrete model id to its panel-leg vendor (codex/gemini/claude/...).

    Fallback author signal for reviewer≠author when no recorded executor is
    available: the implementing model's vendor must be excluded from the pool.
    """
    m = (model_id or "").lower()
    if not m:
        return ""
    if "claude" in m or "opus" in m or "sonnet" in m or "haiku" in m:
        return "claude"
    if "gemini" in m or m in {"pro", "flash", "flash-lite", "auto"}:
        return "gemini"
    if m.startswith("gpt") or m.startswith("o1") or m.startswith("o3") or m.startswith("openai/"):
        return "codex"  # the codex panel leg runs the openai-family model
    return m


def resolve_run_mode(env: Mapping[str, str] | None = None, explicit: str | None = None) -> str:
    if explicit:
        value = str(explicit).strip().lower()
        return value if value in RUN_MODES else DEFAULT_RUN_MODE
    value = str((env or {}).get(RUN_MODE_ENV) or "").strip().lower()
    return value if value in RUN_MODES else DEFAULT_RUN_MODE


def select_reviewer_pool(
    author_vendor: str | Iterable[str],
    available_legs: Sequence[str],
) -> tuple[tuple[str, ...], str | None]:
    """Return (pool, degraded_reason). The pool excludes EVERY author vendor.

    ``author_vendor`` may be a single vendor or a set of them — under
    rotation/repair a phase can be authored by more than one vendor (e.g. codex
    executes, claude repairs), and ALL of them must be excluded so no author
    reviews its own work (advisor-panel reconciliation, verified).
    ``degraded_reason`` is set when no disjoint reviewer is available.
    """
    authors = {author_vendor} if isinstance(author_vendor, str) else set(author_vendor)
    authors = {a for a in authors if a}
    pool = tuple(leg for leg in available_legs if leg not in authors)
    if pool:
        return pool, None
    return (), ("no_reviewers" if not available_legs else "author_vendor_only")


@dataclass(frozen=True)
class GateResult:
    ran: bool                       # did the governed gate actually evaluate?
    promoted: bool                  # may the artifact advance? (False only on unresolved block)
    findings: tuple[ReviewFinding, ...] = ()
    degraded: bool = False          # True => not a real review (advisory autonomous-warn)
    reason: str | None = None
    panel: PanelResult | None = None


def _findings_from_panel(panel: PanelResult) -> tuple[ReviewFinding, ...]:
    """Fail-closed translation of panel leg outputs into findings. A leg that is
    not usable (empty/timeout/degraded/unavailable) becomes a `warn` finding so
    the reduced confidence is recorded; a usable leg whose verdict signals a
    blocking concern becomes a `block` finding."""
    findings: list[ReviewFinding] = []
    for leg in panel.legs:
        if not leg.usable:
            # A leg with SUBSTANTIVE text but no conforming terminal verdict is a
            # review that violated the contract — we cannot confirm it approved, so
            # fail closed (BLOCK), never downgrade a possible objection to a
            # non-gating warn (CR finding). A leg with no usable text
            # (empty / timeout / unavailable / auth error) is "no review happened"
            # → a recorded warn (reduced confidence), not a block.
            if leg.text.strip():
                findings.append(ReviewFinding(
                    code="panel_nonconforming",
                    reason=(
                        f"panel leg {leg.leg} produced a review with no conforming "
                        f"terminal verdict ({leg.status}); holding fail-closed"
                    ),
                    severity="block",
                    blocker_class="review_gate_block",
                ))
            else:
                findings.append(ReviewFinding(
                    code="panel_leg_degraded",
                    reason=f"panel leg {leg.leg} unusable ({leg.status})",
                    severity="warn",
                ))
            continue
        if _leg_blocks(leg.text):
            findings.append(ReviewFinding(
                code="panel_block",
                reason=f"panel leg {leg.leg} raised a blocking concern",
                severity="block",
                blocker_class="review_gate_block",
            ))
        else:
            # A "nit" is non-blocking; recorded at `warn` severity (the rigor-v1
            # model has no separate nit literal — block vs not-block).
            findings.append(ReviewFinding(
                code="panel_nit",
                reason=f"panel leg {leg.leg} reviewed with non-blocking notes",
                severity="warn",
            ))
    return tuple(findings)


def _block_result(reason: str, code: str, detail: str) -> GateResult:
    """A fail-closed governed result: held (not promoted), non-degraded block."""
    return GateResult(
        ran=True,
        promoted=False,
        degraded=False,
        reason=reason,
        findings=(ReviewFinding(
            code=code,
            reason=detail,
            severity="block",
            blocker_class="review_gate_block",
        ),),
    )


def governed_planning_gate(
    *,
    artifact: str,
    author_executor: str | None = None,
    author_vendors: Iterable[str] | None = None,
    run_mode: str,
    available_legs: Sequence[str] | None = None,
    invoke: Callable[..., PanelResult] = invoke_panel,
    spawn=None,
) -> GateResult:
    """Evaluate a governed gate (plan-stage or pre-merge).

    AUTONOMOUS SHORT-CIRCUIT: when `run_mode != "governed"` this returns BEFORE
    selecting a pool or touching `invoke` — the panel is never spawned. This is
    the zero-panel-call guarantee for the default path.

    FAIL-CLOSED (governed is the opt-in enforcement mode): if there is no reviewer
    disjoint from the author vendor(s), or every selected leg is unusable / non-
    conforming, the gate HOLDS (non-human ``review_gate_block``) rather than
    advisory-passing a review that never really happened (advisor-panel
    reconciliation, verified — the prior advisory-pass was a fail-open).
    """
    if run_mode != "governed":
        return GateResult(ran=False, promoted=True)

    if author_vendors is not None:
        authors = frozenset(v for v in author_vendors if v)
    else:
        authors = frozenset({author_vendor_for_executor(author_executor or "")} - {""})
    if not authors:
        # Unknown author → CANNOT establish reviewer≠author. Fail closed (CR
        # finding): an empty author set otherwise excluded nothing and ran the
        # FULL panel including the author's own vendor (a silent self-review).
        return _block_result(
            "unknown_author",
            "governed_unknown_author",
            "governed mode could not determine the authoring vendor(s) for "
            "reviewer≠author exclusion; holding (non-human) rather than risk a "
            "self-review",
        )
    legs = tuple(available_legs) if available_legs is not None else available_panel_legs()
    pool, degraded_reason = select_reviewer_pool(authors, legs)
    if not pool:
        return _block_result(
            degraded_reason or "no_disjoint_reviewer",
            "governed_no_disjoint_reviewer",
            (
                f"governed mode requires a reviewer disjoint from author vendor(s) "
                f"{sorted(authors)} but none is available ({degraded_reason}); "
                f"holding (non-human). Multi-vendor phases (authored/repaired across "
                f"more than one vendor) need the claude panel leg, which is currently "
                f"deferred — see CHANGELOG (governed-mode limitation)."
            ),
        )

    panel = invoke(artifact, pool, spawn=spawn)
    findings = _findings_from_panel(panel)
    if not panel.usable_legs:
        # Pool existed but no leg produced a usable, conforming review → the review
        # did not actually happen. Fail closed, never silent-pass.
        return _block_result(
            "no_usable_review",
            "governed_no_usable_review",
            f"no disjoint reviewer produced a usable verdict ({len(panel.legs)} leg(s) unusable); holding (non-human)",
        )
    has_block = any(f.severity == "block" for f in findings)
    return GateResult(
        ran=True,
        promoted=not has_block,
        findings=findings,
        degraded=False,
        panel=panel,
    )
