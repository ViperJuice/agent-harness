"""#64 — advisor-panel leg auth preflight + soft-empty-turn retry.

A logged-out CLI (verified root cause) makes codex fail obliquely — an empty-turn
(`rc=0`, empty `--output-last-message`) then rate-limit errors — so the panel
silently degrades and the failure is misdiagnosed. The preflight catches a
de-authed leg at launch (DEGRADED, not a silent empty leg); the retry recovers
the genuinely-transient soft empty-turn without hammering a hard failure.
"""

from __future__ import annotations

import types

import phase_loop_runtime.panel_invoker as pi


def _stage(tmp_path):
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    (review_dir / "review-bundle.md").write_text("bundle", encoding="utf-8")
    (review_dir / "review-instructions.md").write_text("instructions", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    return review_dir, out_dir


# --- _leg_auth_ok --------------------------------------------------------------

def test_auth_ok_when_logged_in(monkeypatch):
    monkeypatch.setattr(
        pi.subprocess, "run",
        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="Logged in as x", stderr=""),
    )
    ok, detail = pi._leg_auth_ok("codex", {})
    assert ok and detail == ""


def test_auth_fails_and_classifies_degraded_when_logged_out(monkeypatch):
    monkeypatch.setattr(
        pi.subprocess, "run",
        lambda *a, **k: types.SimpleNamespace(returncode=1, stdout="", stderr="not logged in"),
    )
    ok, detail = pi._leg_auth_ok("codex", {})
    assert not ok
    assert pi._AUTH_SIGNATURE.search(detail), "detail must carry an auth signature"
    assert pi._classify_leg(1, "", detail) == "DEGRADED"  # not EMPTY, not a silent pass


def test_auth_no_probe_fails_open_without_subprocess(monkeypatch):
    called = []
    monkeypatch.setattr(pi.subprocess, "run", lambda *a, **k: called.append(1))
    ok, detail = pi._leg_auth_ok("gemini", {})  # no probe registered
    assert ok and detail == "" and not called


def test_auth_probe_missing_cli_fails_open(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError
    monkeypatch.setattr(pi.subprocess, "run", boom)
    ok, _ = pi._leg_auth_ok("codex", {})
    assert ok  # a flaky/absent probe must never block the leg


# --- _exec_leg codex path ------------------------------------------------------

def test_exec_leg_codex_blocks_when_preflight_fails(tmp_path, monkeypatch):
    review_dir, out_dir = _stage(tmp_path)

    def fake_run(cmd, **k):
        if list(cmd[:3]) == ["codex", "login", "status"]:
            return types.SimpleNamespace(returncode=1, stdout="", stderr="not logged in")
        raise AssertionError("codex exec must NOT run when the auth preflight fails")

    monkeypatch.setattr(pi.subprocess, "run", fake_run)
    rc, review, log = pi._exec_leg("codex", review_dir, out_dir, timeout_s=60)
    assert rc != 0 and not review
    assert "not logged in" in log
    assert pi._classify_leg(rc, review, log) == "DEGRADED"


def test_exec_leg_codex_retries_soft_empty_turn(tmp_path, monkeypatch):
    review_dir, out_dir = _stage(tmp_path)
    out_file = out_dir / "panel-codex.txt"
    calls = {"exec": 0}

    # auth preflight still runs via subprocess.run — keep it logged-in.
    monkeypatch.setattr(
        pi.subprocess, "run",
        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="logged in", stderr=""),
    )

    # the leg exec now goes through _run_leg_with_liveness; the codex verdict is the
    # out_file (--output-last-message), so simulate an empty first turn then a real one.
    def fake_liveness(cmd, **k):
        calls["exec"] += 1
        out_file.write_text("" if calls["exec"] == 1 else "PARTIALLY AGREE\n", encoding="utf-8")
        return pi._LegRun(0, "transcript", "")

    monkeypatch.setattr(pi, "_run_leg_with_liveness", fake_liveness)
    rc, review, log = pi._exec_leg("codex", review_dir, out_dir, timeout_s=60)
    assert calls["exec"] == 2, "a soft empty-turn (rc=0 + empty) must be retried once"
    assert review.strip() == "PARTIALLY AGREE"


def test_exec_leg_codex_does_not_retry_hard_failure(tmp_path, monkeypatch):
    review_dir, out_dir = _stage(tmp_path)
    out_file = out_dir / "panel-codex.txt"
    calls = {"exec": 0}

    monkeypatch.setattr(
        pi.subprocess, "run",
        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="logged in", stderr=""),
    )

    def fake_liveness(cmd, **k):
        calls["exec"] += 1
        out_file.write_text("", encoding="utf-8")
        return pi._LegRun(1, "", "rate limit")

    monkeypatch.setattr(pi, "_run_leg_with_liveness", fake_liveness)
    rc, review, log = pi._exec_leg("codex", review_dir, out_dir, timeout_s=60)
    assert calls["exec"] == 1, "a hard failure (rc!=0) must NOT be retried (never hammer)"
    assert rc == 1
