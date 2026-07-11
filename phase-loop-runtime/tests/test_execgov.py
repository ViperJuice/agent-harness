"""EXECGOV (CLEANSHIP Phase 2) — executor-governance fixes #153 + #154.

#153 has two coupled halves, both keyed on the SAME predicate — a claude
`subagent`/`agent_team` run whose sub-step is an authoring action
(`plan`/`roadmap`/`maintain-skills`, i.e. the modes' `disallowed_actions`):

  * Lane (a) — explicit-claude path: `build_claude_launch_spec` AUTO-DEGRADES the
    authoring sub-step to solo and dispatches, instead of returning the opaque
    TEAMGOV block. A residual (non-authoring) team block carries actionable
    remediation via `runner._claude_team_block_remediation`.
  * Lane (b) — AUTO path: `default_executor_resolver._gate_candidate` SKIPS claude
    for the same predicate, so run-from never seeds a claude pick the launcher would
    then block (the "inert subagent flag becomes load-bearing" trap).

The two are LAYERED, not mutually exclusive: lane (b) removes claude from the AUTO
DEFAULT SEED (the common path), but `resolve_dispatch_decision`'s fallback can still
route claude for an authoring action when the seeded executor is session-degraded —
and lane (a) is the BACKSTOP that degrades that residual case to claude-solo instead
of an opaque block. `test_auto_gate_and_launch_backstop_are_layered` pins the backstop.

#154's grok execute `--disallowed-tools` deny-list is pinned in `test_grokexec.py`.
"""
from __future__ import annotations

import dataclasses
import os
from contextlib import contextmanager
from pathlib import Path

import pytest

from phase_loop_runtime import runner
from phase_loop_runtime.capability_registry import (
    ExecutorCapabilityRecord,
    capability_registry,
)
from phase_loop_runtime.default_executor_resolver import (
    DefaultResolutionContext,
    _gate_candidate,
    resolve_default_executor,
)
from phase_loop_runtime.launcher import build_launch_request, build_launch_spec
from phase_loop_runtime.models import PhaseTeamEligibility
from phase_loop_runtime.profiles import resolve_profile_for_executor
from phase_loop_runtime.prompts import build_prompt

_ROADMAP = Path("/repo/specs/phase-plans-v1.md")
_TEAM_SAFE_ELIGIBILITY = PhaseTeamEligibility(
    allowed_execution_modes=("solo", "subagent", "agent_team"),
    default_execution_mode="solo",
    eligible_for_native_team=True,
    has_disjoint_write_lanes=True,
    has_only_read_only_lanes=False,
    unmanaged_write_risk=False,
    reason="pinned_team_safe_fixture",
)
_SOLO_ONLY_ELIGIBILITY = PhaseTeamEligibility(
    allowed_execution_modes=("solo",),
    default_execution_mode="solo",
    eligible_for_native_team=False,
    has_disjoint_write_lanes=False,
    has_only_read_only_lanes=False,
    unmanaged_write_risk=False,
    reason="pinned_solo_only_fixture",
)


@contextmanager
def _agent_view_route():
    """Pin the claude route so build_launch_spec is machine-independent (no channel/
    print env probing)."""
    saved = os.environ.get("PHASE_LOOP_CLAUDE_ROUTE")
    os.environ["PHASE_LOOP_CLAUDE_ROUTE"] = "agent_view"
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("PHASE_LOOP_CLAUDE_ROUTE", None)
        else:
            os.environ["PHASE_LOOP_CLAUDE_ROUTE"] = saved


def _claude_request(action: str, mode: str, *, eligibility=_TEAM_SAFE_ELIGIBILITY, executor="claude"):
    authoring = action in ("plan", "roadmap", "maintain-skills")
    plan = None if authoring else Path("/repo/plans/phase-plan-v1-X.md")
    selection = resolve_profile_for_executor(action=action, executor=executor)
    bundle = build_prompt(action, _ROADMAP, phase="RUNNER", plan=plan)
    return build_launch_request(
        executor=executor,
        action=action,
        repo=Path("/repo"),
        roadmap=_ROADMAP,
        phase="RUNNER",
        plan=plan,
        model_selection=selection,
        prompt_bundle=bundle,
        json_output=True,
        bypass_approvals=False,
        launch_timeout_seconds=1800,
        claude_execution_mode=mode,
        phase_team_eligibility=eligibility,
    )


# ======================================================================
# Lane (a) — #153 authoring auto-degrade in build_claude_launch_spec.
# ======================================================================


# Only `plan`/`roadmap` are exercised here — the CLAUDE-REACHABLE authoring actions.
# `maintain-skills` is in the modes' `disallowed_actions` (so the degrade covers it
# generically, reading the set from the policy), but it is UNREACHABLE for claude in
# production: claude's `supported_actions` omits `maintain-skills`, and `run_loop`
# diverts maintenance to a codex-only `run_maintenance` path before normal dispatch.
# Testing claude+`maintain-skills` would assert an unreachable builder path, so it is
# deliberately excluded from the matrix rather than manufacturing a false-reachable case.
@pytest.mark.parametrize(
    "action,mode",
    [
        ("plan", "subagent"),
        ("plan", "agent_team"),
        ("roadmap", "subagent"),
        ("roadmap", "agent_team"),
    ],
)
def test_claude_authoring_under_team_mode_degrades_to_solo_and_dispatches(action, mode):
    # The core #153 fix: an authoring action under a team mode DEGRADES to solo and
    # DISPATCHES (available=True) — team semantics are meaningless for a single
    # authoring action — instead of an opaque TEAMGOV block. Pinned team-safe
    # eligibility proves the degrade, not eligibility, is what unblocks it.
    with _agent_view_route():
        spec = build_launch_spec(_claude_request(action, mode))
    assert spec.available is True
    assert spec.claude_execution_mode == "solo"
    assert spec.claude_team_policy.execution_mode == "solo"
    assert spec.reason is None


def test_auto_gate_and_launch_backstop_are_layered():
    # #153 layering (regression for the "mutually exclusive" over-claim): the AUTO gate
    # (lane b) only removes claude from the DEFAULT SEED. `resolve_dispatch_decision`'s
    # fallback can still route claude for an authoring action under a team mode when the
    # seeded executor is session-degraded — so claude CAN reach `build_claude_launch_spec`
    # on the AUTO path. This asserts the launch-time BACKSTOP catches exactly that: a
    # claude `subagent` authoring request (as dispatch fallback would hand it over) does
    # NOT block — it degrades to claude-solo and dispatches. Seed-gate + backstop are
    # layered, not mutually exclusive.
    with _agent_view_route():
        spec = build_launch_spec(_claude_request("plan", "subagent"))
    assert spec.available is True, "the launch backstop must degrade+dispatch, not block"
    assert spec.claude_execution_mode == "solo"


def test_claude_solo_authoring_unaffected():
    # claude + solo authoring already dispatches solo — the degrade must not disturb
    # the pre-existing solo path (it is a no-op there).
    with _agent_view_route():
        spec = build_launch_spec(_claude_request("plan", "solo"))
    assert spec.available is True
    assert spec.claude_execution_mode == "solo"


@pytest.mark.parametrize("action", ["execute", "repair", "review"])
def test_claude_nonauthoring_under_subagent_still_allowed(action):
    # execute/repair/review are ALLOWED under subagent (not in disallowed_actions), so
    # the degrade must not touch them: the mode stays subagent on a team-safe phase.
    with _agent_view_route():
        spec = build_launch_spec(_claude_request(action, "subagent"))
    assert spec.available is True
    assert spec.claude_execution_mode == "subagent"
    assert spec.claude_team_policy.execution_mode == "subagent"


def test_claude_nonauthoring_team_block_is_residual_not_degraded():
    # A non-authoring team block (execute under subagent on a SOLO-ONLY phase) is a
    # legitimate residual block — the authoring degrade must NOT convert it to solo.
    with _agent_view_route():
        spec = build_launch_spec(_claude_request("execute", "subagent", eligibility=_SOLO_ONLY_ELIGIBILITY))
    assert spec.available is False
    assert spec.claude_execution_mode == "subagent"
    assert "not team-safe" in (spec.reason or "")


def test_non_claude_executor_unaffected_by_degrade():
    # The degrade lives on the claude path only; a non-claude executor authoring a
    # plan is unchanged (codex has no claude team policy / execution mode).
    selection = resolve_profile_for_executor(action="plan", executor="codex")
    bundle = build_prompt("plan", _ROADMAP, phase="RUNNER", plan=None)
    request = build_launch_request(
        executor="codex",
        action="plan",
        repo=Path("/repo"),
        roadmap=_ROADMAP,
        phase="RUNNER",
        plan=None,
        model_selection=selection,
        prompt_bundle=bundle,
        json_output=True,
        bypass_approvals=False,
        launch_timeout_seconds=1800,
    )
    spec = build_launch_spec(request)
    assert spec.executor == "codex"
    assert spec.available is True


# ======================================================================
# Lane (a) — #153 residual-block remediation (runner helper).
# ======================================================================


class _FakeSpec:
    def __init__(self, executor, mode, reason, available=False):
        self.executor = executor
        self.claude_execution_mode = mode
        self.reason = reason
        self.available = available


def test_remediation_enriches_claude_team_policy_block():
    spec = _FakeSpec("claude", "subagent", "Claude subagent mode is denied because the active phase plan is not team-safe: x.")
    text = runner._claude_team_block_remediation(spec, "RUNNER")
    assert text is not None
    assert "RUNNER" in text  # names the phase
    assert "--claude-execution-mode solo" in text  # names the solo escape hatch
    assert "plan the phase first" in text  # names the plan-first escape hatch


def test_runner_blocked_terminal_carries_remediation_end_to_end(tmp_path):
    # Integration: the ONLY test that exercises the runner blocked-handler
    # `next_action=_team_remediation or …` wiring (not just the helper). Runs in the
    # default AND clean-room suites — the claude skill bundle resolves by one of two
    # environment-appropriate paths, so no dotfiles overlay is ever needed:
    #   * source/dev checkout: the canonical `phase-loop-skills/` source is present at
    #     the repo root; anchor PHASE_LOOP_RUNNER_REPO_ROOT there so run_loop resolves
    #     it (without the anchor it resolves against the temp repo — no skills — and
    #     raises SkillBundleResolutionError in the launch preamble).
    #   * clean-room-from-wheel: `phase-loop-skills/` is absent, so the PACKAGED
    #     `phase_loop_runtime/skills_bundle/` fallback fires automatically (it is gated
    #     to fire only when no canonical source is present) — no anchor needed.
    # A claude subagent EXECUTE on a NON-team-safe plan is a residual (non-authoring)
    # team block; assert the emitted blocked terminal's `next_action` carries the
    # remediation, not the bare policy sentence.
    from phase_loop_test_utils import make_repo, write_phase_plan
    from phase_loop_runtime.events import read_events
    from phase_loop_runtime.runner import run_loop

    # <repo>/phase-loop-runtime/tests/ -> <repo>. Anchor ONLY when the canonical source
    # exists here (dev checkout); in a from-wheel clean-room it is absent and the
    # packaged bundle fallback resolves, so we must NOT set a bogus anchor.
    repo_root = Path(__file__).resolve().parents[2]
    canonical_source_present = (repo_root / "phase-loop-skills").is_dir()

    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    # Overlapping owned files => not eligible for a native team => residual team block.
    write_phase_plan(
        repo,
        "RUNNER",
        roadmap,
        body=(
            "# RUNNER\n\n## Lanes\n\n"
            "### SL-0 - One\n- **Owned files**: `src/*.py`\n\n"
            "### SL-1 - Two\n- **Owned files**: `src/app.py`\n"
        ),
    )
    _saved_root = os.environ.get("PHASE_LOOP_RUNNER_REPO_ROOT")
    if canonical_source_present:
        os.environ["PHASE_LOOP_RUNNER_REPO_ROOT"] = str(repo_root)
    try:
        with _agent_view_route():
            snapshot, results = run_loop(
                repo, roadmap, phase="RUNNER", executor="claude", claude_execution_mode="subagent"
            )
    finally:
        if _saved_root is None:
            os.environ.pop("PHASE_LOOP_RUNNER_REPO_ROOT", None)
        else:
            os.environ["PHASE_LOOP_RUNNER_REPO_ROOT"] = _saved_root
    assert snapshot.phases["RUNNER"] == "blocked"
    terminal = read_events(repo)[-1]["metadata"]["terminal_summary"]
    assert terminal["terminal_status"] == "blocked"
    next_action = terminal["next_action"]
    assert "RUNNER" in next_action
    assert "--claude-execution-mode solo" in next_action
    assert "plan the phase first" in next_action
    # The bare policy sentence is NOT the operator's only guidance (remediation wraps it).
    assert next_action != "Provide a valid explicit adapter configuration before retrying."


def test_remediation_none_for_solo_and_non_claude_and_route_blocks():
    # solo claude block: no team remediation.
    assert runner._claude_team_block_remediation(_FakeSpec("claude", "solo", "some solo reason"), "P") is None
    # non-claude block: no team remediation.
    assert runner._claude_team_block_remediation(_FakeSpec("codex", "subagent", "codex reason"), "P") is None
    # claude team-mode but a ROUTE/channel block (not a team-policy denial): untouched.
    route_reason = "Claude Channel route requires PHASE_LOOP_CHANNEL_SESSION_ID or ..."
    assert runner._claude_team_block_remediation(_FakeSpec("claude", "agent_team", route_reason), "P") is None


# ======================================================================
# Lane (b) — #153 AUTO-gate coupling in _gate_candidate / resolver.
# ======================================================================


def _headless_registry(headless_claude: bool = True) -> dict[str, ExecutorCapabilityRecord]:
    """Real registry with is_available/auth_ok bound True for claude+codex, and
    claude's headless_launchable overridable — so a test can isolate the mode gate as
    the SOLE reason claude is skipped (in production claude is tty-only, so the gate
    sits in front of the headless check as defense-in-depth)."""
    out: dict[str, ExecutorCapabilityRecord] = {}
    for name, rec in capability_registry().items():
        base = rec
        if name == "claude" and headless_claude:
            # Replace the dataclass field FIRST — bind_runtime attaches the probes as
            # instance attrs that `dataclasses.replace` would otherwise drop.
            base = dataclasses.replace(rec, headless_launchable=True)
        out[name] = base.bind_runtime(
            is_available=(lambda n=name: n in {"claude", "codex"}),
            auth_ok=(lambda n=name: n in {"claude", "codex"}),
        )
    return out


def test_gate_skips_claude_for_authoring_action_under_subagent():
    # With claude made headless, the ONLY reason it is skipped is the authoring+team
    # gate — proving the coupling, not the pre-existing tty rejection.
    reg = _headless_registry()
    reason = _gate_candidate(
        "claude",
        reg["claude"],
        ctx=DefaultResolutionContext(action="plan", claude_execution_mode="subagent"),
    )
    assert reason == "claude_authoring_disallowed_under_subagent"


@pytest.mark.parametrize("mode", ["subagent", "agent_team"])
def test_resolver_run_from_claude_skips_authoring_and_falls_through(mode):
    # Under Claude Code (run-from claude) with a headless claude, an authoring action
    # under a team mode must NOT select claude — it falls through to codex.
    reg = _headless_registry()
    sel = resolve_default_executor(
        DefaultResolutionContext(action="plan", claude_execution_mode=mode),
        registry=reg,
        env={"CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli"},
    )
    assert sel.executor != "claude"
    rejected = [c.reason for c in sel.rejected if c.executor == "claude"]
    assert any(f"claude_authoring_disallowed_under_{mode}" in r for r in rejected)


def test_gate_does_not_skip_claude_for_execute_under_subagent():
    # execute is allowed under subagent — the gate must NOT skip claude for it (with a
    # headless claude the gate returns None → claude passes).
    reg = _headless_registry()
    reason = _gate_candidate(
        "claude",
        reg["claude"],
        ctx=DefaultResolutionContext(action="execute", claude_execution_mode="subagent"),
    )
    assert reason is None


def test_gate_does_not_skip_claude_for_authoring_under_solo():
    # solo authoring is fine — the gate must not skip claude when mode is solo/None.
    reg = _headless_registry()
    for mode in (None, "solo"):
        reason = _gate_candidate(
            "claude",
            reg["claude"],
            ctx=DefaultResolutionContext(action="plan", claude_execution_mode=mode),
        )
        assert reason is None, f"mode={mode!r} must not trip the authoring gate"


def test_gate_default_mode_none_is_backward_compatible():
    # Default ctx (no claude_execution_mode) must not change any existing behavior:
    # claude is skipped for the pre-existing reason (tty-only), never the new gate.
    reg = capability_registry()
    reason = _gate_candidate("claude", reg["claude"], ctx=DefaultResolutionContext(action="plan"))
    assert reason == "requires_controlling_terminal"
