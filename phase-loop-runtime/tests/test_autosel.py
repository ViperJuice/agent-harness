"""AUTOSEL (EXECDISPATCH Phase 2) — layered DEFAULT executor resolution.

Covers IF-0-AUTOSEL-1 (env-signature map + run-from detector with self-vs-child
disambiguation) and IF-0-AUTOSEL-2 (``resolve_default_executor`` four-layer
resolver + its wiring into ``resolve_dispatch_decision``), plus the cross-cutting
change-set: probe fail-closed reliance, launchability gate via ``headless_launchable``,
enumeration parity, the outbound child-env marker, and the resolver->dispatch
composition seam (an AUTOSEL pick must never be one dispatch then rejects).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from phase_loop_runtime import executor_availability as ea
from phase_loop_runtime import launcher
from phase_loop_runtime.capability_registry import (
    ExecutorCapabilityRecord,
    capability_registry,
    resolve_dispatch_decision,
)
from phase_loop_runtime.default_executor_resolver import (
    DISABLE_AUTOSEL_ENV,
    LAYER_CODEX_LEGACY,
    LAYER_EXPLICIT,
    LAYER_RUN_FROM,
    LAYER_SINGLE_AVAILABLE,
    DefaultResolutionContext,
    is_launch_complete,
    resolve_default_executor,
)
from phase_loop_runtime.harness_env_signatures import (
    CLAUDE_CODE_SELF_MARKERS,
    HARNESS_ENV_SIGNATURES,
    PHASE_LOOP_CHILD_ENV,
    UNKNOWN,
    child_executor_env,
    detect_run_from_harness,
    verified_harness_count,
)
from phase_loop_runtime.injection import HARNESS_INJECTION_MODES
from phase_loop_runtime.launcher import PROMPT_INJECTED_CLOSEOUT_EXECUTORS
from phase_loop_runtime.models import EXECUTORS
from phase_loop_runtime.prompts import build_prompt

_ROADMAP = Path("/repo/specs/phase-plans-v1.md")
_PLAN = Path("/repo/plans/phase-plan-v1-X.md")


# --------------------------------------------------------------------------
# Test registry helper — bind is_available/auth_ok to fixed verdicts.
# --------------------------------------------------------------------------

def _registry(available: set[str], authed: set[str]) -> dict[str, ExecutorCapabilityRecord]:
    out: dict[str, ExecutorCapabilityRecord] = {}
    for name, rec in capability_registry().items():
        out[name] = rec.bind_runtime(
            is_available=(lambda n=name: n in available),
            auth_ok=(lambda n=name: n in authed),
        )
    return out


def _ctx(action: str = "execute", **kw) -> DefaultResolutionContext:
    return DefaultResolutionContext(action=action, **kw)


# ==========================================================================
# IF-0-AUTOSEL-1 — env-signature map + run-from detector.
# ==========================================================================

def test_signature_map_is_non_vacuous():
    # A map where every harness is unknown/degrade fails the exit criterion.
    assert verified_harness_count() >= 2
    adoptable = [s for s in HARNESS_ENV_SIGNATURES.values() if s.verification != UNKNOWN]
    assert len(adoptable) >= 2
    assert {"claude", "codex"} <= {s.executor for s in adoptable}


def test_detect_run_from_claude_code_live_markers():
    d = detect_run_from_harness({"CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli"})
    assert d.executor == "claude"


def test_detect_run_from_codex_marker():
    d = detect_run_from_harness({"CODEX_THREAD_ID": "019f-uuid"})
    assert d.executor == "codex"


def test_detect_run_from_none_when_no_markers():
    assert detect_run_from_harness({"PATH": "/usr/bin"}).executor is None


@pytest.mark.parametrize(
    "env",
    [
        # Nested phase-loop: an executor child that itself was under claude-code.
        {"CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli", PHASE_LOOP_CHILD_ENV: "1"},
        # A codex child of a phase-loop (codex markers present but we stamped it).
        {"CODEX_THREAD_ID": "uuid", PHASE_LOOP_CHILD_ENV: "1"},
        # CI that exports CLAUDECODE into a phase-loop-spawned child.
        {"CI": "1", "CLAUDECODE": "1", PHASE_LOOP_CHILD_ENV: "1"},
    ],
)
def test_self_vs_child_sentinel_suppresses_run_from(env):
    # The sentinel is authoritative: leaked host markers are NOT adopted.
    d = detect_run_from_harness(env)
    assert d.executor is None
    assert d.reason == "phase_loop_child_sentinel"


def test_child_executor_env_scrubs_and_stamps():
    base = {
        "CLAUDECODE": "1",
        "CLAUDE_CODE_ENTRYPOINT": "cli",
        "PATH": "/usr/bin",
        "HOME": "/home/x",
    }
    out = child_executor_env(base)
    for marker in CLAUDE_CODE_SELF_MARKERS:
        assert marker not in out, f"{marker} not scrubbed from child env"
    assert out[PHASE_LOOP_CHILD_ENV] == "1"
    # Everything else is preserved (PATH/HOME/etc.).
    assert out["PATH"] == "/usr/bin"
    assert out["HOME"] == "/home/x"


def test_child_env_defeats_run_from_roundtrip():
    # A child spawned under claude-code must NOT detect claude as its run-from.
    host = {"CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli"}
    assert detect_run_from_harness(host).executor == "claude"
    child = child_executor_env(host)
    assert detect_run_from_harness(child).executor is None


# ==========================================================================
# IF-0-AUTOSEL-2 — resolver, one behavior test per layer.
# ==========================================================================

def test_layer1_explicit_override_wins_and_is_ungated():
    # An operator/CLI hint is honored verbatim, even if it would fail the gate.
    reg = _registry(available=set(), authed=set())
    sel = resolve_default_executor(_ctx(explicit_executor="grok"), registry=reg, env={})
    assert sel.executor == "grok"
    assert sel.layer == LAYER_EXPLICIT


def test_layer2_run_from_harness_selected_when_gate_passes():
    reg = _registry(available={"codex"}, authed={"codex"})
    sel = resolve_default_executor(_ctx(), registry=reg, env={"CODEX_THREAD_ID": "u"})
    assert sel.executor == "codex"
    assert sel.layer == LAYER_RUN_FROM


def test_layer3_single_available_scan():
    # Only grok passes the gate -> single-available picks it (proof_gated but live).
    reg = _registry(available={"grok"}, authed={"grok"})
    sel = resolve_default_executor(_ctx(), registry=reg, env={})
    assert sel.executor == "grok"
    assert sel.layer == LAYER_SINGLE_AVAILABLE


def test_layer4_codex_legacy_when_nothing_resolves():
    reg = _registry(available=set(), authed=set())
    sel = resolve_default_executor(_ctx(), registry=reg, env={})
    assert sel.executor == "codex"
    assert sel.layer == LAYER_CODEX_LEGACY


def test_selection_carries_provenance():
    reg = _registry(available={"codex"}, authed={"codex"})
    # gemini on PATH but unauthed -> shows up as a rejected candidate with a reason.
    reg["gemini"] = reg["gemini"].bind_runtime(is_available=lambda: True, auth_ok=lambda: False)
    sel = resolve_default_executor(_ctx(), registry=reg, env={})
    rejected = {c.executor: c.reason for c in sel.rejected}
    assert "gemini" in rejected
    assert "auth_not_ok" in rejected["gemini"]
    log = sel.provenance_log()
    assert "gemini" in log


# --- FM1: run-from detected but candidate fails the gate -> fall through ----

def test_fm1_under_claude_code_does_not_select_claude():
    # Running under claude-code: claude is detected as run-from but is tty-only
    # (headless_launchable=False), so the resolver must NOT pick claude.
    reg = _registry(available={"claude", "codex"}, authed={"claude", "codex"})
    sel = resolve_default_executor(_ctx(), registry=reg, env={"CLAUDECODE": "1"})
    assert sel.executor != "claude"
    # Falls through to the single-available layer (codex is the only headless pass).
    assert sel.executor == "codex"
    reasons = [c.reason for c in sel.rejected if c.executor == "claude"]
    assert any("requires_controlling_terminal" in r for r in reasons)


# --- FM2: single-available skips unavailable-or-unauthed candidates ---------

def test_fm2_single_available_skips_unavailable_and_unauthed():
    # codex on PATH but unauthed; grok fully available -> grok is the only pass.
    reg = _registry(available={"codex", "grok"}, authed={"grok"})
    sel = resolve_default_executor(_ctx(), registry=reg, env={})
    assert sel.executor == "grok"
    assert sel.layer == LAYER_SINGLE_AVAILABLE
    codex_reason = next(c.reason for c in sel.rejected if c.executor == "codex")
    assert "auth_not_ok" in codex_reason


# --- Escape hatch -----------------------------------------------------------

def test_escape_hatch_collapses_to_explicit_plus_codex_legacy():
    # Even under a codex run-from with grok available, the hatch forces codex legacy.
    reg = _registry(available={"grok"}, authed={"grok"})
    env = {"CODEX_THREAD_ID": "u", DISABLE_AUTOSEL_ENV: "1"}
    sel = resolve_default_executor(_ctx(), registry=reg, env=env)
    assert sel.executor == "codex"
    assert sel.layer == LAYER_CODEX_LEGACY
    assert sel.autosel_disabled is True


def test_escape_hatch_still_honors_explicit_override():
    reg = _registry(available=set(), authed=set())
    sel = resolve_default_executor(
        _ctx(explicit_executor="pi"), registry=reg, env={DISABLE_AUTOSEL_ENV: "1"}
    )
    assert sel.executor == "pi"
    assert sel.layer == LAYER_EXPLICIT


# --- Legacy regression: multiple available, no hint -> codex ----------------

def test_legacy_regression_multiple_available_defaults_to_codex():
    reg = _registry(available={"codex", "gemini", "grok"}, authed={"codex", "gemini", "grok"})
    sel = resolve_default_executor(_ctx(), registry=reg, env={})
    assert sel.executor == "codex"
    assert sel.layer == LAYER_CODEX_LEGACY


# --- Do NOT gate on promotion_status (grok is proof_gated yet eligible) ------

def test_proof_gated_grok_is_still_auto_eligible():
    assert capability_registry()["grok"].promotion_status == "proof_gated"
    reg = _registry(available={"grok"}, authed={"grok"})
    sel = resolve_default_executor(_ctx(), registry=reg, env={})
    assert sel.executor == "grok"


# --- live_available seam guard (advisor's flagged divergence) ----------------

def test_auto_layers_reject_installed_but_unpromoted_executor():
    # An executor on PATH + authed + headless but live_available=False would be
    # picked by a naive gate, then blocked by dispatch as live_launch_unavailable.
    # The resolver must reject it so its pick-set stays a subset of dispatch's.
    from dataclasses import replace

    reg = _registry(available={"opencode"}, authed={"opencode"})
    reg["opencode"] = replace(reg["opencode"], live_available=False).bind_runtime(
        is_available=lambda: True, auth_ok=lambda: True
    )
    sel = resolve_default_executor(_ctx(), registry=reg, env={})
    assert sel.executor == "codex"  # opencode rejected, falls to legacy
    reason = next(c.reason for c in sel.rejected if c.executor == "opencode")
    assert "not_live_available" in reason


def test_dry_run_uses_dry_run_available_gate():
    from dataclasses import replace

    # live_available False but dry_run_available True -> eligible under dry_run.
    reg = _registry(available={"opencode"}, authed={"opencode"})
    reg["opencode"] = replace(
        reg["opencode"], live_available=False, dry_run_available=True
    ).bind_runtime(is_available=lambda: True, auth_ok=lambda: True)
    sel = resolve_default_executor(_ctx(dry_run=True), registry=reg, env={})
    assert sel.executor == "opencode"
    assert sel.layer == LAYER_SINGLE_AVAILABLE


# ==========================================================================
# Non-mocked exit test — drives the REAL availability/auth primitives.
# ==========================================================================

def test_non_mocked_real_primitives_fail_closed():
    # Real is_executor_available: an executor whose CLI is absent (which->None) is
    # False. Real auth_ok_for: a probe with rc!=0 fails closed. No mocks on the
    # gate primitives themselves — only PATH/probe *inputs* are injected.
    assert ea.is_executor_available("codex", which=lambda _c: None) is False
    ea.clear_auth_cache()
    assert (
        ea.auth_ok_for("codex", ("codex login status",), runner=lambda _p: subprocess.CompletedProcess("p", 1))
        is False
    )
    # And the resolver, given a registry whose records bind those REAL primitives
    # against an empty PATH, resolves to codex-legacy (every AUTO candidate fails).
    reg = {}
    for name, rec in capability_registry().items():
        reg[name] = rec.bind_runtime(
            is_available=(lambda n=name: ea.is_executor_available(n, which=lambda _c: None)),
            auth_ok=(lambda: False),
        )
    sel = resolve_default_executor(_ctx(), registry=reg, env={})
    assert sel.executor == "codex"
    assert sel.layer == LAYER_CODEX_LEGACY


def test_env_signature_verified_for_at_least_two_harnesses_non_all_unknown():
    # The env-signature "≥2 harnesses verified, no all-unknown" criterion, live.
    statuses = {s.executor: s.verification for s in HARNESS_ENV_SIGNATURES.values()}
    assert not all(v == UNKNOWN for v in statuses.values())
    assert sum(1 for v in statuses.values() if v != UNKNOWN) >= 2


# ==========================================================================
# Enumeration parity (change #4) — catch half-wired executors pre-merge.
# ==========================================================================

@pytest.mark.parametrize("executor", EXECUTORS)
def test_every_executor_is_launch_wired(executor):
    # Present in the injection map (the KeyError class), launch_complete agrees,
    # and build_prompt renders without KeyError for a real --executor run.
    assert executor in HARNESS_INJECTION_MODES
    assert is_launch_complete(executor) is True
    bundle = build_prompt("execute", _ROADMAP, phase="X", plan=_PLAN, harness_target=executor)
    assert bundle is not None


def test_closeout_injection_set_is_subset_of_known_executors():
    assert PROMPT_INJECTED_CLOSEOUT_EXECUTORS <= set(EXECUTORS)


def test_launch_failure_blocker_membership_is_known_executors():
    # The launch-failure-blocker map keys on a fixed executor set; every member
    # must be a real executor (a stale name would silently misroute blockers).
    from phase_loop_runtime.runner import _executor_launch_failure_blocker

    # Executors NOT in the map return None (no auth-shaped blocker); members with
    # an auth marker return a structured account_or_billing blocker (no KeyError).
    for executor in EXECUTORS:
        out = _executor_launch_failure_blocker(executor, "PHASE", "please log in")
        assert out is None or out["blocker_class"] == "account_or_billing_setup"


# ==========================================================================
# Wiring + composition — resolver -> resolve_dispatch_decision.
# ==========================================================================

def test_dispatch_seed_override_used_when_no_preferred_hint():
    # No operator/plan/roadmap hint -> the injected default_executor is the seed.
    decision = resolve_dispatch_decision(
        action="execute", dry_run=False, default_executor="grok"
    )
    assert decision.selected_executor == "grok"


def test_dispatch_seed_override_ignored_when_operator_hint_present():
    from phase_loop_runtime.models import DispatchHints

    operator = DispatchHints(action="execute", preferred_executors=("codex",))
    decision = resolve_dispatch_decision(
        action="execute", dry_run=False, operator=operator, default_executor="grok"
    )
    assert decision.selected_executor == "codex"


def test_dispatch_default_none_reproduces_codex_legacy():
    decision = resolve_dispatch_decision(action="execute", dry_run=False, default_executor=None)
    assert decision.selected_executor == "codex"


def test_composition_autosel_pick_is_never_a_spurious_dispatch_block():
    # The seam the advisor flagged: AUTOSEL picks X (proof_gated-but-live grok) ->
    # resolve_dispatch_decision must actually SELECT X, not block it as
    # live_launch_unavailable. Uses the real registry (grok live_available=True).
    reg = _registry(available={"grok"}, authed={"grok"})
    sel = resolve_default_executor(_ctx(), registry=reg, env={})
    assert sel.executor == "grok"
    decision = resolve_dispatch_decision(
        action="execute", dry_run=False, default_executor=sel.executor
    )
    assert not decision.blocked
    assert decision.selected_executor == "grok"


# ==========================================================================
# Outbound child-env marker on the real spawn path (change #5).
# ==========================================================================

# ==========================================================================
# CR fixes (grok cross-vendor review) — regression guards.
# ==========================================================================

def test_cr1_explicit_hint_short_circuits_before_probing():
    # grok #1: Layer 1 must return WITHOUT running any availability/auth probe.
    # A registry whose probes raise if called proves no probe fired.
    def _boom():
        raise AssertionError("probe must not run when an explicit hint is present")

    reg = {}
    for name, rec in capability_registry().items():
        reg[name] = rec.bind_runtime(is_available=_boom, auth_ok=_boom)
    sel = resolve_default_executor(_ctx(explicit_executor="grok"), registry=reg, env={"CODEX_THREAD_ID": "u"})
    assert sel.executor == "grok"
    assert sel.layer == LAYER_EXPLICIT


def test_cr2_auto_pick_respects_allowed_set_falls_to_codex_legacy():
    # grok #2 exact scenario: operator restricts to {codex, gemini}, both unauthed,
    # only pi authed. AUTOSEL must NOT pick pi (dispatch would block it as
    # not-allowed); it falls to codex legacy -> dispatch SELECTS codex, not blocked.
    reg = _registry(available={"codex", "gemini", "pi"}, authed={"pi"})
    sel = resolve_default_executor(
        _ctx(allowed_executors=("codex", "gemini")), registry=reg, env={}
    )
    assert sel.executor == "codex"
    assert sel.layer == LAYER_CODEX_LEGACY
    pi_reason = next(c.reason for c in sel.rejected if c.executor == "pi")
    assert "not_in_allowed_set" in pi_reason
    # Composition: the codex-legacy fall-through actually dispatches (no block).
    decision = resolve_dispatch_decision(action="execute", dry_run=False, default_executor=sel.executor)
    assert not decision.blocked
    assert decision.selected_executor == "codex"


def test_cr2_auto_pick_selects_an_allowed_available_executor():
    # The intended improvement: operator restricts to {gemini, grok}; gemini is the
    # only available+authed member -> AUTOSEL selects gemini (dispatch accepts it),
    # instead of the legacy blind-codex default that dispatch would then block.
    reg = _registry(available={"gemini"}, authed={"gemini"})
    sel = resolve_default_executor(
        _ctx(allowed_executors=("gemini", "grok")), registry=reg, env={}
    )
    assert sel.executor == "gemini"
    assert sel.layer == LAYER_SINGLE_AVAILABLE


def test_cr2_auto_gate_respects_disabled_and_required_capabilities():
    reg = _registry(available={"codex", "grok"}, authed={"codex", "grok"})
    # Disable codex -> only grok can pass -> single-available grok.
    sel = resolve_default_executor(_ctx(disabled_executors=("codex",)), registry=reg, env={})
    assert sel.executor == "grok"
    codex_reason = next(c.reason for c in sel.rejected if c.executor == "codex")
    assert "disabled_by_hints" in codex_reason
    # Require a capability grok lacks -> grok rejected too -> codex legacy.
    sel2 = resolve_default_executor(
        _ctx(required_capabilities=("a_capability_no_executor_has",)), registry=reg, env={}
    )
    assert sel2.layer == LAYER_CODEX_LEGACY
    rej2 = {c.executor: c.reason for c in sel2.rejected}
    # codex/grok support execute + are available, so they reach the caps check and
    # are rejected specifically for the missing required capability.
    assert "missing_required_capabilities" in rej2["codex"]
    assert "missing_required_capabilities" in rej2["grok"]


def test_cr3_dual_claude_and_codex_markers_adopt_codex_run_from():
    # grok #3: phase-loop run from codex, but the host claude-code markers leaked in
    # (no PHASE_LOOP_CHILD sentinel). Both signatures match; claude self-eliminates
    # on headless, so Layer 2 must adopt CODEX as run-from (not fall past it).
    det = detect_run_from_harness({"CLAUDECODE": "1", "CODEX_THREAD_ID": "u"})
    assert det.candidates[0] == "codex"  # session-specific ordered first
    assert "claude" in det.candidates
    reg = _registry(available={"codex", "claude"}, authed={"codex", "claude"})
    sel = resolve_default_executor(_ctx(), registry=reg, env={"CLAUDECODE": "1", "CODEX_THREAD_ID": "u"})
    assert sel.executor == "codex"
    assert sel.layer == LAYER_RUN_FROM


def test_cr5_non_timeout_probe_exception_fails_closed():
    # grok #5: any runner exception (not just TimeoutExpired) fails the gate closed.
    ea.clear_auth_cache()

    def raiser(_probe):
        raise OSError("boom")

    assert ea.auth_ok_for("codex", ("codex login status",), runner=raiser) is False


def test_launch_scrubs_and_stamps_child_env(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(launcher.subprocess, "run", fake_run)
    launcher.launch(
        ["true"],
        env={"CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli", "PATH": "/usr/bin"},
    )
    child = captured["env"]
    assert child is not None
    assert "CLAUDECODE" not in child
    assert "CLAUDE_CODE_ENTRYPOINT" not in child
    assert child[PHASE_LOOP_CHILD_ENV] == "1"
    assert child["PATH"] == "/usr/bin"
