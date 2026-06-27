"""Pluggable closeout-validator hook (rigor-v1 P1).

The single seam through which review gates (doc-delta, verification-evidence,
visual-evidence, …) plug into the closeout pass/fail decision **without** each
gate editing ``closeout.py``'s status logic. ``build_phase_loop_closeout`` runs
the registered validators once and applies their findings.

Autonomy-first severity model (see ``specs/phase-plans-v1.md`` and the
``[[phase-loop-autonomy-first-guardrails]]`` constraint):

* Each finding carries a severity of ``warn`` or ``block``.
* The global ``PHASE_LOOP_REVIEW`` control (``off`` | ``warn`` | ``block``,
  **default ``warn``**) sets the effective behavior:
    - ``off``   — validators do not run; no findings.
    - ``warn``  — validators run; every finding is forced to ``warn`` (recorded
      to the closeout for later human spot-check, the loop **continues**).
    - ``block`` — validators run; a ``block`` finding refuses ``complete``.
* **No validator may set ``human_required``.** A blocking finding produces a
  non-human, agent-recoverable blocker — never a stall waiting on a person.

Back-compat: with zero validators registered (the state shipped by P1), the
runner returns no findings and closeout behavior is byte-for-byte unchanged.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Mapping

ReviewSeverity = str  # "warn" | "block"
REVIEW_SEVERITIES: tuple[str, ...] = ("warn", "block")
REVIEW_MODES: tuple[str, ...] = ("off", "warn", "block")
DEFAULT_REVIEW_MODE = "warn"
REVIEW_MODE_ENV = "PHASE_LOOP_REVIEW"


@dataclass(frozen=True)
class ReviewFinding:
    """A single review-gate observation about a closeout."""

    code: str
    reason: str
    severity: ReviewSeverity = "warn"
    blocker_class: str | None = None

    def __post_init__(self) -> None:
        if self.severity not in REVIEW_SEVERITIES:
            raise ValueError(f"invalid review severity: {self.severity!r}")

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": "review_finding",
            "code": self.code,
            "reason": self.reason,
            "severity": self.severity,
        }
        if self.blocker_class is not None:
            payload["blocker_class"] = self.blocker_class
        return payload


@dataclass(frozen=True)
class CloseoutContext:
    """Read-only view of the closeout a validator inspects."""

    phase_alias: str
    plan_path: str
    terminal: Mapping[str, Any] = field(default_factory=dict)
    automation: Mapping[str, Any] = field(default_factory=dict)
    blocker: Mapping[str, Any] = field(default_factory=dict)
    changed_paths: tuple[str, ...] = ()


# A validator receives the context and returns zero or more findings.
CloseoutValidator = Callable[[CloseoutContext], Iterable[ReviewFinding]]

_VALIDATORS: list[CloseoutValidator] = []


def register_closeout_validator(fn: CloseoutValidator) -> CloseoutValidator:
    """Register a closeout validator. Returns ``fn`` so it can be used as a decorator."""
    if fn not in _VALIDATORS:
        _VALIDATORS.append(fn)
    return fn


def clear_closeout_validators() -> None:
    """Drop all registered validators (test hook)."""
    _VALIDATORS.clear()


def registered_closeout_validators() -> tuple[CloseoutValidator, ...]:
    return tuple(_VALIDATORS)


def resolve_review_mode(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    value = str(env.get(REVIEW_MODE_ENV) or "").strip().lower()
    return value if value in REVIEW_MODES else DEFAULT_REVIEW_MODE


def run_closeout_validators(
    ctx: CloseoutContext,
    env: Mapping[str, str] | None = None,
) -> list[ReviewFinding]:
    """Run every registered validator and return findings at their effective severity.

    A validator that raises is skipped — a review gate must never break closeout.
    """
    mode = resolve_review_mode(env)
    if mode == "off":
        return []
    findings: list[ReviewFinding] = []
    for fn in tuple(_VALIDATORS):
        try:
            produced = fn(ctx) or ()
        except Exception:
            continue
        for finding in produced:
            effective = "warn" if mode == "warn" else finding.severity
            findings.append(replace(finding, severity=effective))
    return findings


def apply_review_findings(
    *,
    findings: list[ReviewFinding],
    terminal: dict[str, Any],
    automation: dict[str, Any],
    blocker: dict[str, Any],
) -> dict[str, Any]:
    """Fold findings into the closeout dicts.

    ``warn`` findings are recorded as results (audit trail) and do not change
    the outcome. The first ``block`` finding turns the closeout into a
    non-human ``blocked`` outcome, mirroring the verification-evidence gate.
    """
    updated_terminal = dict(terminal)
    updated_automation = dict(automation)
    updated_blocker = dict(blocker)
    results = [f.to_json() for f in findings]

    blocking = next((f for f in findings if f.severity == "block"), None)
    if blocking is not None:
        blocker_class = blocking.blocker_class or "review_gate_block"
        summary = f"Review gate blocked closeout: {blocking.code} — {blocking.reason}"
        updated_terminal["terminal_status"] = "blocked"
        updated_automation["status"] = "blocked"
        updated_automation["blocker_class"] = blocker_class
        updated_automation["blocker_summary"] = summary
        updated_automation["human_required"] = False
        updated_blocker.update(
            {
                "human_required": False,
                "blocker_class": blocker_class,
                "blocker_summary": summary,
                "required_human_inputs": (),
            }
        )
    return {
        "terminal": updated_terminal,
        "automation": updated_automation,
        "blocker": updated_blocker,
        "results": results,
    }


def load_builtin_closeout_validators() -> None:
    """Import the built-in validator modules so they self-register.

    Extension point: each downstream rigor phase adds its validator module
    (e.g. ``doc_delta_validator``) and one guarded import line here. Imports are
    guarded so an incremental checkout missing a module never breaks closeout.
    P1 ships no built-in validators — the registry is empty by default.
    """
    # P2: from . import doc_delta_validator  # noqa: F401
    # P5: from . import verification_evidence_validator  # noqa: F401
    # P6: from . import visual_evidence_validator  # noqa: F401
    return None


load_builtin_closeout_validators()
