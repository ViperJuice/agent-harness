"""Layered DEFAULT executor resolution (AUTOSEL IF-0-AUTOSEL-2).

Replaces the hard-coded ``codex`` default (historically the bare ``... or "codex"``
seed that ``resolve_dispatch_decision`` injects when no operator/plan/roadmap hint
names an executor) with an ordered, gated resolution:

    1. explicit override   — an operator/CLI/plan/roadmap hint names an executor.
                             Returned verbatim; the AUTO layers never run.
    2. run-from harness     — the harness phase-loop is invoked from (env markers),
                             adopted only if it passes the AUTO gate.
    3. single-available     — exactly one registry executor passes the AUTO gate.
    4. codex legacy         — ``default_executor_for_action`` (codex for every
                             shipped action). Always terminal; never gated.

Only layers 2-3 are new. Each hard-gates every AUTO candidate on

    is_available AND auth_ok AND launch_complete AND headless_launchable
                 AND (dry_run_available if dry_run else live_available)

and falls through on failure — an AUTO layer never hard-picks something dispatch
would then reject. ``live_available`` is included deliberately: an installed,
authed, headless executor that is not promoted (``live_available=False``) would be
picked here yet blocked by ``resolve_dispatch_decision`` as ``live_launch_unavailable``;
gating on it keeps the resolver's pick-set a subset of what dispatch can launch.
Gating does NOT include ``promotion_status`` (grok is ``proof_gated`` yet
headless-launchable and ``live_available``).

Escape hatch: ``EXECDISPATCH_DISABLE_AUTOSEL=1`` collapses resolution to
explicit-override + codex-legacy (the pre-AUTOSEL behavior), for operators who
want the old deterministic default back.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

from .capability_registry import (
    ExecutorCapabilityRecord,
    capability_registry,
    default_executor_for_action,
)
from .harness_env_signatures import detect_run_from_harness
from .injection import HARNESS_INJECTION_MODES

# Escape-hatch env var. When truthy, layers 2-3 are skipped entirely.
DISABLE_AUTOSEL_ENV = "EXECDISPATCH_DISABLE_AUTOSEL"

LAYER_EXPLICIT = "explicit_override"
LAYER_RUN_FROM = "run_from_harness"
LAYER_SINGLE_AVAILABLE = "single_available"
LAYER_CODEX_LEGACY = "codex_legacy"


@dataclass(frozen=True)
class DefaultResolutionContext:
    """Inputs to default-executor resolution.

    ``explicit_executor`` is the concrete Layer-1 signal: the operator/CLI
    ``--executor`` or a plan/roadmap ``preferred_executors`` hint (i.e. exactly the
    thing that makes ``merge_dispatch_hints`` produce a non-empty
    ``preferred_executors``). When set, the AUTO layers never run.
    """

    action: str
    explicit_executor: str | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class RejectedCandidate:
    executor: str
    reason: str


@dataclass(frozen=True)
class DefaultSelection:
    """The resolved default plus provenance (which layer chose, what was rejected)."""

    executor: str
    layer: str
    reason: str
    rejected: tuple[RejectedCandidate, ...] = field(default_factory=tuple)
    autosel_disabled: bool = False

    @property
    def is_auto(self) -> bool:
        """True iff a NEW (layer 2/3) auto-pick chose — the case that must be
        logged with a discoverable escape hatch (change #6)."""
        return self.layer in (LAYER_RUN_FROM, LAYER_SINGLE_AVAILABLE)

    def provenance_log(self) -> str:
        parts = [f"AUTOSEL default `{self.executor}` via {self.layer}: {self.reason}"]
        if self.rejected:
            rej = "; ".join(f"{c.executor} ({c.reason})" for c in self.rejected)
            parts.append(f"rejected: {rej}")
        if self.is_auto:
            parts.append(f"disable with {DISABLE_AUTOSEL_ENV}=1")
        return " | ".join(parts)


def is_launch_complete(executor: str) -> bool:
    """The launchability precondition referenced by the AUTO gate (change #4):
    the executor is wired into the injection map, so ``build_prompt`` /
    ``build_launch_spec`` will not ``KeyError`` on it. The enumeration-parity CI
    test guarantees the other wiring sites stay consistent with this membership,
    so this cheap check is sufficient on the dispatch hot path."""
    return executor in HARNESS_INJECTION_MODES


def _gate_candidate(
    executor: str,
    record: ExecutorCapabilityRecord | None,
    *,
    action: str,
    dry_run: bool,
) -> str | None:
    """Return ``None`` if ``executor`` passes the AUTO gate, else a short reason
    string for provenance. Every probe failure is a *reason*, never a raise —
    a hung/absent CLI degrades to a rejected candidate, not a resolver crash."""
    if record is None:
        return "no_registry_record"
    if action not in record.supported_actions:
        return f"action_unsupported:{action}"
    if not is_launch_complete(executor):
        return "not_launch_complete"
    if not record.headless_launchable:
        return "requires_controlling_terminal"
    if dry_run:
        if not record.dry_run_available:
            return "dry_run_unavailable"
    elif not record.live_available:
        # An installed+authed+headless-but-unpromoted executor: dispatch would
        # reject it as live_launch_unavailable, so the resolver must not pick it.
        return "not_live_available"
    is_available = record.is_available
    if is_available is None or not _safe_probe(is_available):
        return "cli_not_on_path"
    auth_ok = record.auth_ok
    if auth_ok is None or not _safe_probe(auth_ok):
        return "auth_not_ok"
    return None


def _safe_probe(probe) -> bool:
    """Run a bound availability/auth probe, failing CLOSED on any exception (a
    probe raising must never crash resolution — it means 'not usable')."""
    try:
        return bool(probe())
    except Exception:
        return False


def resolve_default_executor(
    ctx: DefaultResolutionContext,
    registry: dict[str, ExecutorCapabilityRecord] | None = None,
    env: Mapping[str, str] | None = None,
) -> DefaultSelection:
    """Resolve the default executor for ``ctx.action`` under the four-layer policy."""
    registry = registry if registry is not None else capability_registry()
    e = os.environ if env is None else env
    codex_legacy = default_executor_for_action(ctx.action)

    # Layer 1 — explicit override (operator/CLI/plan/roadmap). Never gated: an
    # operator naming an executor is honored as-is (dispatch still validates it).
    if ctx.explicit_executor:
        return DefaultSelection(
            executor=ctx.explicit_executor,
            layer=LAYER_EXPLICIT,
            reason="operator/CLI/plan hint named an executor",
        )

    autosel_disabled = str(e.get(DISABLE_AUTOSEL_ENV, "")).strip() == "1"
    rejected: list[RejectedCandidate] = []

    if not autosel_disabled:
        # Layer 2 — run-from harness (honors the self-vs-child sentinel).
        detection = detect_run_from_harness(e)
        if detection.executor is not None:
            reason = _gate_candidate(
                detection.executor,
                registry.get(detection.executor),
                action=ctx.action,
                dry_run=ctx.dry_run,
            )
            if reason is None:
                return DefaultSelection(
                    executor=detection.executor,
                    layer=LAYER_RUN_FROM,
                    reason=f"detected run-from harness ({detection.reason})",
                    rejected=tuple(rejected),
                )
            rejected.append(RejectedCandidate(detection.executor, f"run_from:{reason}"))

        # Layer 3 — single-available registry scan. Pick iff exactly one executor
        # passes the AUTO gate (never guess among several).
        passing: list[str] = []
        for executor, record in registry.items():
            reason = _gate_candidate(executor, record, action=ctx.action, dry_run=ctx.dry_run)
            if reason is None:
                passing.append(executor)
            else:
                rejected.append(RejectedCandidate(executor, f"scan:{reason}"))
        if len(passing) == 1:
            return DefaultSelection(
                executor=passing[0],
                layer=LAYER_SINGLE_AVAILABLE,
                reason="exactly one executor passed the availability/auth gate",
                rejected=tuple(rejected),
            )

    # Layer 4 — codex legacy fallback. Terminal, never gated.
    return DefaultSelection(
        executor=codex_legacy,
        layer=LAYER_CODEX_LEGACY,
        reason=(
            "AUTOSEL disabled via escape hatch" if autosel_disabled
            else "no explicit/run-from/single-available pick; legacy default"
        ),
        rejected=tuple(rejected),
        autosel_disabled=autosel_disabled,
    )
