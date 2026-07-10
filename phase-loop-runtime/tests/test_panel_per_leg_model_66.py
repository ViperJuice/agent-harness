"""#66 — per-leg model override for the advisor panel.

The panel hardcoded each leg's model (claude via `CLAUDE_IMPLEMENTER_MODEL`, codex
`gpt-5.6-sol`, gemini `Gemini 3.1 Pro (High)`), so running e.g. the Claude leg on
`claude-fable-5` required an in-process monkeypatch. `invoke_panel(..., models={...})`
now overrides any subset per leg; unset legs use `DEFAULT_LEG_MODELS`.
"""

from __future__ import annotations

import types
from unittest.mock import patch

import phase_loop_runtime.panel_invoker as pi


def test_default_leg_models_exposed():
    # The claude review leg defaults to Fable, DECOUPLED from the implementer model
    # (claude-sonnet-5): pre-merge review runs on Fable, the implementer stays Sonnet.
    assert pi.DEFAULT_LEG_MODELS["claude"] == "claude-fable-5"
    assert pi.DEFAULT_LEG_MODELS["claude"] != pi.CLAUDE_IMPLEMENTER_MODEL
    assert pi.DEFAULT_LEG_MODELS["codex"] == "gpt-5.6-sol"
    assert "Gemini" in pi.DEFAULT_LEG_MODELS["gemini"]


def test_claude_tui_command_model_override(tmp_path):
    cmd = pi._claude_tui_command(tmp_path, tmp_path, "claude-fable-5")
    assert cmd[cmd.index("--model") + 1] == "claude-fable-5"


def test_claude_tui_command_defaults_when_unset(tmp_path):
    cmd = pi._claude_tui_command(tmp_path, tmp_path)
    # Unset → the panel default (Fable), not the implementer model.
    assert cmd[cmd.index("--model") + 1] == pi.DEFAULT_LEG_MODELS["claude"] == "claude-fable-5"


def _stage(tmp_path):
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    (review_dir / "review-bundle.md").write_text("q", encoding="utf-8")
    (review_dir / "review-instructions.md").write_text("i", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    return review_dir, out_dir


def test_exec_leg_codex_uses_model_override(tmp_path, monkeypatch):
    review_dir, out_dir = _stage(tmp_path)
    captured = {}

    # The auth preflight still uses subprocess.run — keep it logged-in. The leg exec
    # now goes through _run_leg_with_liveness (the codex verdict is the out_file).
    monkeypatch.setattr(
        pi.subprocess, "run",
        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="logged in", stderr=""),
    )

    def fake_liveness(cmd, **k):
        captured["cmd"] = list(cmd)
        (out_dir / "panel-codex.txt").write_text("AGREE", encoding="utf-8")
        return pi._LegRun(0, "", "")

    monkeypatch.setattr(pi, "_run_leg_with_liveness", fake_liveness)
    pi._exec_leg("codex", review_dir, out_dir, 60, "q", model="gpt-5.6-terra-codex")
    assert captured["cmd"][captured["cmd"].index("--model") + 1] == "gpt-5.6-terra-codex"


def test_exec_leg_gemini_uses_model_override(tmp_path, monkeypatch):
    review_dir, out_dir = _stage(tmp_path)
    captured = {}

    def fake_liveness(cmd, **k):
        captured["cmd"] = list(cmd)
        return pi._LegRun(0, "AGREE", "")  # gemini reads its verdict from stdout

    monkeypatch.setattr(pi, "_run_leg_with_liveness", fake_liveness)
    pi._exec_leg("gemini", review_dir, out_dir, 60, "q", model="Gemini 3.5 Flash (High)")
    assert captured["cmd"][captured["cmd"].index("--model") + 1] == "Gemini 3.5 Flash (High)"


def test_invoke_panel_threads_per_leg_model():
    with patch.object(pi, "_default_spawn", return_value=("OK", "x\nAGREE")) as ds:
        pi.invoke_panel("b", ("claude",), models={"claude": "claude-fable-5"})
    ds.assert_called_once_with("claude", "b", repo_dir=None, mode="review", model="claude-fable-5")


def test_invoke_panel_unset_leg_gets_none_and_falls_back():
    with patch.object(pi, "_default_spawn", return_value=("OK", "x\nAGREE")) as ds:
        pi.invoke_panel("b", ("codex",), models={"claude": "claude-fable-5"})  # codex unset
    # codex leg gets model=None → _exec_leg falls back to its default
    ds.assert_called_once_with("codex", "b", repo_dir=None, mode="review", model=None)
