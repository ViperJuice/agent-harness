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
