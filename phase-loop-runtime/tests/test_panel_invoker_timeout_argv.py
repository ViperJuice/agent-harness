"""#36 — input-scaled leg timeout + argv assertions for ``panel_invoker._exec_leg``.

The previously-fixed 600s timeout silently timed out large-artifact frontier reviews,
degrading the panel to fewer legs (the failure that stayed hidden through the cross-repo
work because every test stubs the spawn boundary and none asserted the command / timeout).
These tests pin the input-scaling and the exact command construction (read-only sandbox +
``--output-last-message`` for codex; ``--add-dir`` + scaled ``--print-timeout`` for gemini).
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from phase_loop_runtime import panel_invoker as pi


def test_leg_timeout_scales_with_review_size():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        # empty → base floor
        assert pi._leg_timeout_for(d) == pi._LEG_TIMEOUT_BASE_S
        # ~50 KB artifact → base + 50 * per-KB (below the cap), clearing codex-xhigh ~900s
        (d / "big.txt").write_text("x" * (50 * 1024))
        scaled = pi._leg_timeout_for(d)
        assert scaled == pi._LEG_TIMEOUT_BASE_S + 50 * pi._LEG_TIMEOUT_PER_KB_S
        assert scaled > pi._LEG_TIMEOUT_BASE_S
        assert scaled >= 900


def test_leg_timeout_is_capped():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "huge.txt").write_text("x" * (4 * 1024 * 1024))  # 4 MB → well over cap
        assert pi._leg_timeout_for(d) == pi._LEG_TIMEOUT_MAX_S


def _capture_run(monkeypatch, stdout: str = ""):
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["timeout"] = kwargs.get("timeout")
        captured["input"] = kwargs.get("input")
        captured["stdin"] = kwargs.get("stdin")

        class _R:
            returncode = 0

        r = _R()
        r.stdout = stdout
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    return captured


def test_codex_leg_argv_is_read_only_with_output_last_message(monkeypatch):
    captured = _capture_run(monkeypatch)
    with tempfile.TemporaryDirectory() as rd, tempfile.TemporaryDirectory() as od:
        pi._exec_leg("codex", Path(rd), Path(od))
        expected_timeout = pi._leg_timeout_for(Path(rd))
    cmd = captured["cmd"]
    assert cmd[:2] == ["codex", "exec"]
    assert "--sandbox" in cmd and cmd[cmd.index("--sandbox") + 1] == "read-only"
    assert "--output-last-message" in cmd
    # never the executor default that build_codex_command emits
    assert "danger-full-access" not in cmd
    assert "--dangerously-bypass-approvals-and-sandbox" not in cmd
    assert captured["timeout"] == expected_timeout


def test_grok_leg_argv_is_headless_plain_with_reasoning_effort(monkeypatch):
    captured = _capture_run(monkeypatch, stdout="AGREE")
    with tempfile.TemporaryDirectory() as rd, tempfile.TemporaryDirectory() as od:
        rdp = Path(rd)
        pi._exec_leg("grok", rdp, Path(od))  # effort-absent → grok's max reasoning
        expected_timeout = pi._leg_timeout_for(rdp)
    cmd = captured["cmd"]
    assert cmd[0] == "grok"
    assert "-p" in cmd  # single-turn headless prompt
    # plain headless output (stdout IS the review; no --output-last-message file)
    assert cmd[cmd.index("--output-format") + 1] == "plain"
    # runs the grok-4.5 default model at max reasoning
    assert cmd[cmd.index("-m") + 1] == "grok-4.5"
    assert cmd[cmd.index("--reasoning-effort") + 1] == "max"
    # web search / tools stay ON — never disabled (matches codex/gemini convention)
    assert "--disable-web-search" not in cmd
    # grok is a SLOW leg: same +60s hard-kill grace as gemini, never a short timeout
    assert captured["timeout"] == expected_timeout + 60


def test_grok_leg_renders_seat_effort_through_the_map(monkeypatch):
    captured = _capture_run(monkeypatch, stdout="AGREE")
    with tempfile.TemporaryDirectory() as rd, tempfile.TemporaryDirectory() as od:
        # a board seat's canonical effort reaches the CLI as --reasoning-effort <token>.
        pi._exec_leg("grok", Path(rd), Path(od), effort="high", model="grok-4.5")
    cmd = captured["cmd"]
    assert cmd[cmd.index("--reasoning-effort") + 1] == "high"


def test_gemini_leg_argv_uses_add_dir_and_scaled_print_timeout(monkeypatch):
    captured = _capture_run(monkeypatch, stdout="AGREE")
    with tempfile.TemporaryDirectory() as rd, tempfile.TemporaryDirectory() as od:
        rdp = Path(rd)
        (rdp / "artifact.py").write_text("x" * (30 * 1024))
        pi._exec_leg("gemini", rdp, Path(od))
        expected_timeout = pi._leg_timeout_for(rdp)
    cmd = captured["cmd"]
    assert cmd[0] == "agy"
    assert "--add-dir" in cmd
    assert "--print-timeout" in cmd
    assert cmd[cmd.index("--print-timeout") + 1] == f"{expected_timeout}s"
    assert captured["timeout"] == expected_timeout + 60


def test_gemini_leg_passes_prompt_inline_on_argv_not_stdin(monkeypatch):
    """Regression: ``agy -p -`` IGNORES stdin and runs an EMPTY prompt (it prints its
    "How can I help you today?" greeting), so the gemini leg silently returned a
    non-review on every run. The prompt MUST be the inline ``-p`` argv value, and the
    leg MUST NOT feed stdin. Mirrors the grok leg's inline-prompt convention."""
    captured = _capture_run(monkeypatch, stdout="AGREE")
    with tempfile.TemporaryDirectory() as rd, tempfile.TemporaryDirectory() as od:
        rdp = Path(rd)
        (rdp / "artifact.py").write_text("some code to review")
        pi._exec_leg("gemini", rdp, Path(od), artifact="REVIEW THIS ARTIFACT", mode="review")
    cmd = captured["cmd"]
    # the arg right after -p is the composed leg prompt (the staged-bundle pointer),
    # never the stdin sentinel "-" that made agy run an empty prompt.
    prompt_arg = cmd[cmd.index("-p") + 1]
    assert prompt_arg != "-"
    assert "review-bundle.md" in prompt_arg  # the real staged-bundle pointer prompt
    # and nothing is fed on stdin (feeding stdin was the empty-prompt bug)
    assert captured["input"] is None
    assert captured["stdin"] is subprocess.DEVNULL
