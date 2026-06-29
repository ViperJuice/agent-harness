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

import re
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from .closeout_validators import ReviewFinding
from .panel_invoker import PanelResult, available_panel_legs, invoke_panel

RUN_MODES: tuple[str, ...] = ("autonomous", "governed")
DEFAULT_RUN_MODE = "autonomous"
RUN_MODE_ENV = "PHASE_LOOP_RUN_MODE"

# The panel brief requires each leg to END with one of AGREE / PARTIALLY AGREE /
# DISAGREE. Classify on that terminal verdict token, not a naive substring match
# anywhere in the prose — "no blockers", "non-blocking nits", "no disagreements"
# all contain BLOCK/DISAGREE yet are approvals (code-review finding, verified).
_VERDICT_LINE_RE = re.compile(r"\b(PARTIALLY\s+AGREE|DISAGREE|AGREE)\b", re.IGNORECASE)


def _leg_blocks(text: str) -> bool:
    """True iff a usable leg's review signals a blocking concern.

    Prefer the structured terminal verdict (the LAST AGREE/PARTIALLY AGREE/
    DISAGREE token) — only a bare `DISAGREE` blocks. If the leg omitted the
    structured verdict, fall back to a word-boundary `BLOCK`/`DISAGREE` that is
    not immediately negated ("no ", "non-", "without ", "zero ").
    """
    matches = list(_VERDICT_LINE_RE.finditer(text))
    if matches:
        final = matches[-1].group(0).upper().split()
        # "AGREE" / "PARTIALLY AGREE" => not a block; only a bare "DISAGREE" blocks.
        return final == ["DISAGREE"]
    # No structured verdict — be conservative but negation-aware.
    return bool(re.search(r"(?<![A-Za-z-])(?<!no )(?<!non-)\bDISAGREE\b", text, re.IGNORECASE)) or bool(
        re.search(r"(?<!no )(?<!non-)(?<!without )(?<!zero )\bBLOCK(?:ING|S|ED)?\b", text, re.IGNORECASE)
    )

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
    author_vendor: str,
    available_legs: Sequence[str],
) -> tuple[tuple[str, ...], str | None]:
    """Return (pool, degraded_reason). The pool excludes the author's vendor.
    degraded_reason is set when no disjoint reviewer is available."""
    pool = tuple(leg for leg in available_legs if leg != author_vendor)
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


def governed_planning_gate(
    *,
    artifact: str,
    author_executor: str,
    run_mode: str,
    available_legs: Sequence[str] | None = None,
    invoke: Callable[..., PanelResult] = invoke_panel,
    spawn=None,
) -> GateResult:
    """Evaluate the plan-stage governed gate.

    AUTONOMOUS SHORT-CIRCUIT: when `run_mode != "governed"` this returns BEFORE
    selecting a pool or touching `invoke` — the panel is never spawned. This is
    the zero-panel-call guarantee for the default path.
    """
    if run_mode != "governed":
        return GateResult(ran=False, promoted=True)

    author = author_vendor_for_executor(author_executor)
    legs = tuple(available_legs) if available_legs is not None else available_panel_legs()
    pool, degraded_reason = select_reviewer_pool(author, legs)
    if not pool:
        # Degrade to autonomous-warn: advisory, recorded — NOT a pass-as-reviewed.
        return GateResult(
            ran=True,
            promoted=True,
            degraded=True,
            reason=degraded_reason,
            findings=(ReviewFinding(
                code="governed_review_degraded",
                reason=(
                    f"no reviewer disjoint from author vendor '{author}' "
                    f"({degraded_reason}); degraded to autonomous-warn"
                ),
                severity="warn",
            ),),
        )

    panel = invoke(artifact, pool, spawn=spawn)
    findings = _findings_from_panel(panel)
    has_block = any(f.severity == "block" for f in findings)
    return GateResult(
        ran=True,
        promoted=not has_block,
        findings=findings,
        degraded=False,
        panel=panel,
    )
