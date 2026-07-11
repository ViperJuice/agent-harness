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
import time
from dataclasses import dataclass, field
from typing import Mapping

from .capability_registry import (
    ExecutorCapabilityRecord,
    capability_registry,
    default_executor_for_action,
)
from .harness_env_signatures import detect_run_from_harness
from .injection import HARNESS_INJECTION_MODES

# Escape-hatch env var. When set to exactly "1", layers 2-3 are skipped entirely.
DISABLE_AUTOSEL_ENV = "EXECDISPATCH_DISABLE_AUTOSEL"

# Wall-clock budget for the whole Layer-3 single-available scan (grok CR #2). Auth
# probes are per-executor bounded (_PROBE_TIMEOUT_SECONDS) and cached; this caps the
# aggregate so a cold resolution with several wedged CLIs degrades to codex legacy
# rather than stalling the dispatch hot path for minutes.
_LAYER3_SCAN_BUDGET_SECONDS = 20.0

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
    # Merged operator/plan/roadmap dispatch constraints. The AUTO gate must reject
    # any candidate that `resolve_dispatch_decision` would HARD-BLOCK as a preferred
    # pick — otherwise AUTOSEL seeds X and dispatch blocks on X. Dispatch hard-blocks
    # a preferred candidate on: disabled, not-in-allowed, unsupported-action,
    # missing-required-capability, and not-live-available. (degraded and dry-run
    # unavailability are soft-continue in dispatch, so they are not gated here.)
    allowed_executors: tuple[str, ...] = ()
    disabled_executors: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()


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
    ctx: DefaultResolutionContext,
) -> str | None:
    """Return ``None`` if ``executor`` passes the AUTO gate, else a short reason
    string for provenance. Every probe failure is a *reason*, never a raise —
    a hung/absent CLI degrades to a rejected candidate, not a resolver crash.

    The gate mirrors every condition under which ``resolve_dispatch_decision``
    would HARD-BLOCK ``executor`` as a preferred pick, so an AUTO selection is
    always something dispatch can actually launch."""
    action, dry_run = ctx.action, ctx.dry_run
    if record is None:
        return "no_registry_record"
    # Operator/plan/roadmap policy constraints (dispatch hard-blocks a preferred
    # candidate on each of these). Empty allowed == no constraint (dispatch treats
    # an empty allow-list as "every supporting executor").
    if executor in ctx.disabled_executors:
        return "disabled_by_hints"
    if ctx.allowed_executors and executor not in ctx.allowed_executors:
        return "not_in_allowed_set"
    if action not in record.supported_actions:
        return f"action_unsupported:{action}"
    missing = tuple(cap for cap in ctx.required_capabilities if cap not in record.capabilities)
    if missing:
        return f"missing_required_capabilities:{','.join(missing)}"
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
        # Layer 2 — run-from harness (honors the self-vs-child sentinel). Multiple
        # signatures can match (leaky host markers alongside session-specific ones);
        # adopt the first candidate that passes the gate, so a self-eliminating
        # match (e.g. tty-only claude) never masks the real run-from harness.
        detection = detect_run_from_harness(e)
        for candidate in detection.candidates:
            reason = _gate_candidate(candidate, registry.get(candidate), ctx=ctx)
            if reason is None:
                return DefaultSelection(
                    executor=candidate,
                    layer=LAYER_RUN_FROM,
                    reason=f"detected run-from harness ({detection.reason})",
                    rejected=tuple(rejected),
                )
            rejected.append(RejectedCandidate(candidate, f"run_from:{reason}"))

        # Layer 3 — single-available registry scan. Pick iff exactly one executor
        # passes the AUTO gate (never guess among several).
        #
        # Fail-BOUNDED, not just fail-closed per probe (grok CR #2): the auth probe
        # is the only slow step (bounded to _PROBE_TIMEOUT_SECONDS each). Two guards
        # keep the whole scan bounded even when several installed CLIs wedge:
        #   * short-circuit once a SECOND executor passes — we already know it is not
        #     single-available, so probing the rest is pointless (bounds the healthy
        #     multi-executor case to two probe sets);
        #   * a wall-clock scan budget — once exceeded, the scan is treated as
        #     INCOMPLETE and can never conclude single-available (an unprobed
        #     executor might also have passed), so it degrades to codex legacy. This
        #     bounds a cold resolution with wedged CLIs to ~budget + one in-flight
        #     probe, and never auto-picks a non-codex default on partial evidence.
        passing: list[str] = []
        scan_incomplete = False
        scan_deadline = time.monotonic() + _LAYER3_SCAN_BUDGET_SECONDS
        for executor, record in registry.items():
            if time.monotonic() > scan_deadline:
                rejected.append(RejectedCandidate(executor, "scan:budget_exhausted"))
                scan_incomplete = True
                break  # remaining executors are unproved; a partial scan is not single-available
            reason = _gate_candidate(executor, record, ctx=ctx)
            if reason is None:
                passing.append(executor)
                if len(passing) > 1:
                    break  # not single-available; stop scanning (bounded)
            else:
                rejected.append(RejectedCandidate(executor, f"scan:{reason}"))
        # Only a COMPLETE scan with exactly one passer is single-available; an
        # incomplete (budget-exhausted) scan degrades to codex legacy (grok r3 #1).
        if len(passing) == 1 and not scan_incomplete:
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
