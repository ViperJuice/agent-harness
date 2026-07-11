"""Shared LaunchSpec-golden case builder (EXECREG IF-0-EXECREG-1 acceptance spine).

Builds a normalized full-``LaunchSpec`` snapshot for every pre-existing executor
with fully pinned inputs + env, so the snapshot is reproducible across machines.
The snapshot is captured on the pre-refactor base commit and asserted byte-stable
after ``build_launch_spec`` is rewritten to delegate to ``record.build_command``.

Normalization: the ``prompt_bundle`` / ``injection_metadata`` sub-objects are pass
-through (computed in the shared preamble, identical for every branch and sensitive
to the skill/dotfiles environment) and are dropped from the snapshot. Everything the
per-executor branch actually constructs on the ``LaunchSpec`` is kept.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from phase_loop_runtime.launcher import build_launch_request, build_launch_spec
from phase_loop_runtime.models import (
    ClaudeTeamPolicy,
    CommandAdapterConfig,
    PhaseTeamEligibility,
)
from phase_loop_runtime.profiles import resolve_profile_for_executor
from phase_loop_runtime.prompts import build_prompt

_REPO = Path("/repo")
_ROADMAP = Path("/repo/specs/phase-plans-v1.md")
_PLAN = Path("/repo/plans/phase-plan-v1-ADAPTER.md")
_PHASE = "ADAPTER"

# Fields dropped from the snapshot: pass-through preamble output (env/skill-bundle
# sensitive), not constructed by the per-executor branch under refactor.
_DROP_FIELDS = ("prompt_bundle", "injection_metadata")


@contextmanager
def _pinned_env() -> Iterator[None]:
    """Pin every env var the claude route / CI detection reads, so the snapshot is
    machine-independent. Uses the deterministic agent_view route (no sidecar/session
    env dependence)."""
    keys = [
        "PHASE_LOOP_CLAUDE_ROUTE",
        "PHASE_LOOP_CHANNEL_SESSION_ID",
        "PHASE_LOOP_CLAUDE_CHANNEL_SESSION_ID",
        "PHASE_LOOP_CLAUDE_CHANNEL_SIDECAR_URL",
        "CI",
        "GITHUB_ACTIONS",
    ]
    saved = {k: os.environ.get(k) for k in keys}
    try:
        for k in keys:
            os.environ.pop(k, None)
        os.environ["PHASE_LOOP_CLAUDE_ROUTE"] = "agent_view"
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _base_request(executor: str, **kwargs: Any):
    selection = resolve_profile_for_executor(action="execute", executor=executor)
    bundle = build_prompt("execute", _ROADMAP, phase=_PHASE, plan=_PLAN)
    return build_launch_request(
        executor=executor,
        action="execute",
        repo=_REPO,
        roadmap=_ROADMAP,
        phase=_PHASE,
        plan=_PLAN,
        model_selection=selection,
        prompt_bundle=bundle,
        json_output=True,
        bypass_approvals=False,
        launch_timeout_seconds=1800,
        **kwargs,
    )


def _pinned_claude_eligibility() -> PhaseTeamEligibility:
    # Explicit eligibility bypasses the filesystem classify_phase_team_eligibility read.
    return PhaseTeamEligibility(
        allowed_execution_modes=("solo",),
        default_execution_mode="solo",
        eligible_for_native_team=False,
        has_disjoint_write_lanes=False,
        has_only_read_only_lanes=False,
        unmanaged_write_risk=False,
        reason="pinned_golden_fixture",
    )


def _command_adapter() -> CommandAdapterConfig:
    return CommandAdapterConfig(
        name="golden-adapter",
        template="run --context {context_file} --cwd {cwd}",
        supported_actions=("execute",),
        delivery_mode="context_file",
    )


def _normalize(spec_json: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in spec_json.items() if k not in _DROP_FIELDS}
    return out


def build_cases() -> dict[str, dict[str, Any]]:
    """Return {case_name: normalized LaunchSpec snapshot} for every pre-existing
    executor. Env is pinned for the duration."""
    cases: dict[str, dict[str, Any]] = {}
    with _pinned_env():
        # Simple executors: pure functions of (request, capability).
        for executor in ("codex", "gemini", "opencode", "pi", "manual"):
            spec = build_launch_spec(_base_request(executor))
            cases[executor] = _normalize(spec.to_json())

        # command executor needs an explicit adapter config.
        spec = build_launch_spec(_base_request("command", command_adapter=_command_adapter()))
        cases["command"] = _normalize(spec.to_json())

        # claude: pin route (agent_view) + explicit policy/eligibility to avoid fs reads.
        claude_request = _base_request(
            "claude",
            claude_execution_mode="solo",
            phase_team_eligibility=_pinned_claude_eligibility(),
        )
        spec = build_launch_spec(claude_request)
        cases["claude_agent_view_solo"] = _normalize(spec.to_json())
    return cases
