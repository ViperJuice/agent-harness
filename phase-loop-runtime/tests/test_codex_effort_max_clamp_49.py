"""#49 — codex CLI reasoning-effort ceiling is 'xhigh'; the internal 'max' tier
must be emitted as 'xhigh', never the literal 'max' (which the codex CLI rejects
with "Invalid value: 'max'").

The clamp lives at the codex CLI boundary (`build_codex_command`) so codex stays
max-ELIGIBLE in the policy/tier layer — a 'max' planner request is honored, at
the codex CLI's real ceiling.
"""

from __future__ import annotations

from pathlib import Path

from phase_loop_runtime.launcher import _codex_cli_effort, build_codex_command
from phase_loop_runtime.models import ModelSelection


def _emit(cmd: list[str]) -> str:
    return next(c for c in cmd if "model_reasoning_effort" in c)


def test_codex_cli_effort_maps_max_to_xhigh():
    assert _codex_cli_effort("max") == "xhigh"


def test_codex_cli_effort_passes_through_supported_levels():
    for level in ("none", "minimal", "low", "medium", "high", "xhigh"):
        assert _codex_cli_effort(level) == level


def test_build_codex_command_emits_xhigh_for_max():
    selection = ModelSelection(profile="plan", model="gpt-5.5-codex", effort="max")
    cmd = build_codex_command(Path("/repo"), selection, "prompt")
    assert _emit(cmd) == 'model_reasoning_effort="xhigh"'
    # the invalid literal must never reach the CLI
    assert 'model_reasoning_effort="max"' not in cmd


def test_build_codex_command_passes_through_high():
    selection = ModelSelection(profile="execute", model="gpt-5.4-codex", effort="high")
    cmd = build_codex_command(Path("/repo"), selection, "prompt")
    assert _emit(cmd) == 'model_reasoning_effort="high"'
