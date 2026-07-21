"""ah#252 — codex leg must not mislabel a clean review DEGRADED because its own
prose contains an ``_AUTH_SIGNATURE`` substring (e.g. "unauthorized").

Root cause (verified against a live `codex exec` run, codex-cli 0.144.6): codex
echoes BOTH the user prompt AND its own final message into its stderr
transcript (not just stdout) — e.g. running codex on a prompt asking it to
quote `"The unauthorized practice of law is prohibited."` produces "unauthorized"
on BOTH stdout and stderr. So the earlier hypothesis that codex's review body
lives only on stdout (and a stderr-only ``log_text``, mirroring gemini/grok,
would dodge this) does NOT hold for codex: its stderr transcript independently
carries the same substring. Scoping ``log_text`` to a single stream is not a
fix here.

The actual fix is in ``_classify_leg``: a conforming ``rc == 0`` review is
classified ``OK`` BEFORE the ``_AUTH_SIGNATURE`` scan ever runs. This is
stream-agnostic (it doesn't matter which stream(s) the trigger word appears
in) and stays fail-closed: a genuinely de-authed/rate-limited codex process
cannot ALSO emit a real, complete, conforming AGREE/PARTIALLY AGREE/DISAGREE,
so a leg that is not a conforming rc==0 review still falls through to the
auth-signature scan exactly as before, and a hard failure (rc != 0) is still
independently caught by the ``rc != 0`` branch.
"""

from __future__ import annotations

import phase_loop_runtime.panel_invoker as pi


def _stage(tmp_path):
    review_dir = tmp_path / "review"
    review_dir.mkdir()
    (review_dir / "review-bundle.md").write_text("bundle", encoding="utf-8")
    (review_dir / "review-instructions.md").write_text("instructions", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    return review_dir, out_dir


def _run_codex(tmp_path, monkeypatch, *, stdout: str, stderr: str, returncode: int = 0):
    """Drive ``_exec_leg("codex", ...)`` through the real stdout/stderr assembly
    path (not a pre-classified mock), so the regression exercises the actual
    ``log_text = ...`` line, not just ``_classify_leg`` in isolation."""
    review_dir, out_dir = _stage(tmp_path)
    out_file = out_dir / "panel-codex.txt"

    # auth preflight (a separate `codex login status` probe) stays logged-in so
    # the leg proceeds to the real exec under test.
    monkeypatch.setattr(pi, "_leg_auth_ok", lambda *a, **k: (True, ""))

    def fake_liveness(cmd, **kwargs):
        # --output-last-message writes the final message to a file; codex ALSO
        # prints it to stdout AND echoes it (plus the prompt) into the stderr
        # transcript — reproducing the verified live-codex behavior.
        out_file.write_text(stdout, encoding="utf-8")
        return pi._LegRun(returncode, stdout, stderr)

    monkeypatch.setattr(pi, "_run_leg_with_liveness", fake_liveness)
    rc, review_text, log_text = pi._exec_leg("codex", review_dir, out_dir, timeout_s=60)
    return rc, review_text, log_text


def test_clean_review_mentioning_unauthorized_is_ok_not_degraded(tmp_path, monkeypatch):
    body = (
        '"The unauthorized practice of law is prohibited."\n\n'
        "The endpoint under review correctly enforces this via a role check.\n\n"
        "AGREE"
    )
    # transcript on stderr ALSO carries the body (verified codex behavior) —
    # this is the exact condition that used to false-positive.
    transcript = (
        "OpenAI Codex v0.144.6\n--------\nsession id: deadbeef\n--------\nuser\n"
        f"Quote: \"The unauthorized practice of law is prohibited.\"\n\ncodex\n{body}\n"
        "tokens used\n13,017\n"
    )
    rc, review_text, log_text = _run_codex(tmp_path, monkeypatch, stdout=body, stderr=transcript)
    assert pi._AUTH_SIGNATURE.search(body)
    assert pi._AUTH_SIGNATURE.search(log_text)  # confirm the trigger really is in log_text
    assert rc == 0
    assert review_text.strip() == body.strip()
    assert pi._classify_leg(rc, review_text, log_text) == "OK"


def test_clean_review_mentioning_rate_limit_exceeded_is_ok_not_degraded(tmp_path, monkeypatch):
    body = (
        "The proposed backoff correctly handles a `rate limit exceeded` response "
        "from the upstream API.\n\nDISAGREE — missing test coverage for the retry path"
    )
    rc, review_text, log_text = _run_codex(tmp_path, monkeypatch, stdout=body, stderr=body)
    assert pi._AUTH_SIGNATURE.search(log_text)
    assert pi._classify_leg(rc, review_text, log_text) == "OK"


def test_genuine_auth_failure_nonzero_exit_still_degrades(tmp_path, monkeypatch):
    # A real CLI auth banner, hard failure (non-zero exit, no review body).
    rc, review_text, log_text = _run_codex(
        tmp_path, monkeypatch,
        stdout="", stderr="error: not logged in; please run codex login",
        returncode=1,
    )
    assert not review_text.strip()
    status = pi._classify_leg(rc, review_text, log_text)
    assert status == "DEGRADED"  # fail-closed: a real auth banner is still caught


def test_genuine_auth_failure_rc_zero_empty_body_still_degrades(tmp_path, monkeypatch):
    # A de-authed CLI that exits 0 but produced no conforming review body must
    # not read as OK just because rc == 0.
    rc, review_text, log_text = _run_codex(
        tmp_path, monkeypatch,
        stdout="", stderr="401 Unauthorized: token expired",
        returncode=0,
    )
    assert pi._classify_leg(rc, review_text, log_text) == "DEGRADED"


def test_genuine_auth_failure_rc_zero_nonconforming_body_still_degrades(tmp_path, monkeypatch):
    # A de-authed CLI that exits 0 with SOME non-conforming text (no terminal
    # verdict) and an auth banner on the transcript must still fail closed.
    junk = "Session expired; unauthorized. Please re-authenticate and retry."
    rc, review_text, log_text = _run_codex(
        tmp_path, monkeypatch, stdout=junk, stderr=junk, returncode=0,
    )
    assert pi.terminal_verdict(junk) is None  # confirm it is genuinely non-conforming
    assert pi._classify_leg(rc, review_text, log_text) == "DEGRADED"


def test_classify_leg_unit_matrix_ok_before_auth_scan():
    """Direct ``_classify_leg`` unit coverage of the reorder (issue's repro shape)."""
    conforming = "1. Blocking finding: config drift.\n\nDISAGREE"
    assert pi._classify_leg(0, conforming, "...discussing the unauthorized practice of law...") == "OK"
    assert pi._classify_leg(0, conforming, "note: rate limit exceeded earlier") == "OK"
    # non-conforming body + auth phrase → still degraded (unchanged behavior).
    assert pi._classify_leg(0, "no verdict here", "unauthorized") == "DEGRADED"
    # hard failure + auth phrase → still degraded even with a conforming-looking body.
    assert pi._classify_leg(1, conforming, "unauthorized") == "DEGRADED"


def test_advisory_mode_keeps_auth_scan_first_no_fail_open():
    """ah#252 CR (codex): the early-OK bypass is REVIEW-MODE ONLY. Advisory-mode
    ``_completion_ok`` is the weak ``len>=40`` threshold, which a genuine auth banner
    clears — so advisory must keep the original auth-scan-first order and NEVER classify
    a real auth-error transcript as OK (that would fail OPEN on a non-gating board)."""
    real_banner = "401 Unauthorized: authentication token expired; please log in again."
    assert len(real_banner) >= 40  # a real banner is long enough to clear the advisory threshold
    # advisory + a genuine auth banner (any length) → DEGRADED, both as body and in log_text:
    assert pi._classify_leg(0, real_banner, real_banner, mode="advisory") == "DEGRADED"
    assert pi._classify_leg(0, "y" * 40, "rate limit exceeded", mode="advisory") == "DEGRADED"
    assert pi._classify_leg(0, "x" * 39, "rate limit exceeded", mode="advisory") == "DEGRADED"
    # review mode still gets the fix: a conforming verdict whose prose mentions auth → OK.
    assert pi._classify_leg(0, "1. finding.\n\nDISAGREE", "unauthorized", mode="review") == "OK"
