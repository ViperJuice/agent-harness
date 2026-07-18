"""ah#196/#223 — the Claude TUI advisor leg must clear the workspace-trust modal
(answer ``y`` once, PRE-SUBMIT, path-scoped to its scratch cwd), submit only when the
editor is quiescent, and fail CLOSED with a typed reason (never the generic 180s
``claude_tui_stalled``) when the gate can't be cleared. Post-submit output that happens
to contain the trust strings must NEVER inject a keystroke or flip the leg to blocked
(this leg reviews agent-harness PRs about this very code).

Real-subprocess PTY tests (like test_panel_tui_liveness_188 / _eof_48): a synthetic
``sh -c`` fake-``claude`` prints the captured modal / editor bursts; only the timing
constants are monkeypatched short so the flows resolve in seconds.
"""

from __future__ import annotations

import shutil
import time

import pytest

import phase_loop_runtime.panel_invoker as pi
from phase_loop_runtime.panel_invoker import _exec_claude_tui_leg, _run_claude_tui_session

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="needs POSIX sh")

_NO_TRANSCRIPT = lambda *a, **k: ""  # noqa: E731

# Fast timing so the startup state machine resolves in seconds, not the production
# 8s floor / 45s readiness deadline / 180s stall.
def _fast_timing(monkeypatch, *, submit_delay=0.5, quiescence=0.4, ready_deadline=4.0, stall=120):
    monkeypatch.setattr(pi, "_CLAUDE_TUI_SUBMIT_DELAY_S", submit_delay)
    monkeypatch.setattr(pi, "_CLAUDE_TUI_READY_QUIESCENCE_S", quiescence)
    monkeypatch.setattr(pi, "_CLAUDE_TUI_READY_DEADLINE_S", ready_deadline)
    monkeypatch.setattr(pi, "_LEG_STALL_THRESHOLD_S", stall)
    monkeypatch.setattr(pi, "_latest_claude_transcript_text", _NO_TRANSCRIPT)


# A fresh-cwd workspace-trust modal (the captured 2.1.208 shape), printed with the real
# ``$PWD`` so the path-scoped conjunction (header AND choice AND cwd token) matches.
_MODAL = (
    "printf 'Permission Required: Accessing workspace:\\n%s\\n"
    "Quick safety check: Is this a project you created or one you trust?\\n"
    "y. Yes, I trust this folder\\nn. No, exit\\nEnter y/n:' \"$PWD\"; "
)


def test_trust_modal_answered_once_then_leg_completes(tmp_path, monkeypatch):
    """The modal is answered ``y`` (recorded by the fake), the editor burst arms
    readiness, and the leg completes OK — the review prompt is NOT pasted into the
    y/n field (the reproduced bug)."""
    _fast_timing(monkeypatch)
    script = (
        _MODAL
        + "IFS= read -r ans; printf '%s' \"$ans\" > answer.txt; "
        + "printf '\\nClaude Code v2.1.208\\nWelcome back\\nmanual mode on ready now\\n'; "
        + "printf 'The staged bundle looks correct.\\n\\nAGREE\\n' > panel-claude.txt; "
        + "sleep 3"
    )
    output_file = tmp_path / "panel-claude.txt"
    rc, text, status, tail = _run_claude_tui_session(
        command=["sh", "-c", script],
        cwd=tmp_path,
        prompt="review this bundle\n",
        output_file=output_file,
        timeout_s=30,
        env={"PATH": "/usr/bin:/bin"},
        backstop_s=30,
    )
    assert status == "claude_tui_file_output", f"expected OK file verdict, got {status!r} / {text!r}"
    assert "AGREE" in text
    # The modal received exactly ``y`` — not the review prompt pasted into the y/n field.
    assert (tmp_path / "answer.txt").read_text().strip() == "y"


def test_trust_modal_never_clears_is_typed_blocked_before_stall(tmp_path, monkeypatch):
    """A modal that keeps rejecting the answer fails CLOSED as
    ``claude_tui_workspace_trust_blocked`` well before the 180s generic stall, with a
    non-empty redacted PTY tail."""
    _fast_timing(monkeypatch, ready_deadline=6.0, stall=120)
    script = (
        _MODAL
        + "IFS= read -r ans; "
        + "while :; do printf '\\nPlease answer y or n\\nEnter y/n:'; sleep 0.2; done"
    )
    start = time.monotonic()
    rc, text, status, tail = _run_claude_tui_session(
        command=["sh", "-c", script],
        cwd=tmp_path,
        prompt="review this\n",
        output_file=tmp_path / "panel-claude.txt",
        timeout_s=120,
        env={"PATH": "/usr/bin:/bin"},
        backstop_s=120,
    )
    elapsed = time.monotonic() - start
    assert status == "claude_tui_workspace_trust_blocked", f"got {status!r}"
    assert rc != 0
    assert elapsed < 60, f"typed-blocked must precede the 180s stall; took {elapsed:.1f}s"
    assert tail, "a failed leg must carry a diagnostic PTY tail"


def test_no_modal_never_ready_is_editor_not_ready(tmp_path, monkeypatch):
    """A silent startup (no modal, no output) yields the DISTINCT
    ``claude_tui_editor_not_ready`` reason (not trust_blocked, not the generic stall)."""
    _fast_timing(monkeypatch, ready_deadline=3.0, stall=120)
    start = time.monotonic()
    rc, text, status, tail = _run_claude_tui_session(
        command=["sh", "-c", "sleep 60"],
        cwd=tmp_path,
        prompt="review this\n",
        output_file=tmp_path / "panel-claude.txt",
        timeout_s=120,
        env={"PATH": "/usr/bin:/bin"},
        backstop_s=120,
    )
    elapsed = time.monotonic() - start
    assert status == "claude_tui_editor_not_ready", f"got {status!r}"
    assert 3 <= elapsed < 30, f"must fire at the readiness deadline, not the stall; {elapsed:.1f}s"


def test_post_submit_trigger_text_does_not_block_or_inject(tmp_path, monkeypatch):
    """CRITICAL (self-referential): after submission, review OUTPUT containing the trust
    trigger strings ("Enter y/n:", "Please answer y or n") must NOT flip the leg to
    trust_blocked and must NOT inject a keystroke — the detector is disarmed at submit.
    The leg completes OK on its review file."""
    _fast_timing(monkeypatch)
    script = (
        # No modal: straight to an editor burst that arms readiness.
        "printf 'Claude Code v2.1.208\\nWelcome back\\nmanual mode on ready now\\n'; "
        + "sleep 2; "
        # AFTER the harness has submitted, emit output loaded with the trigger strings.
        + "printf 'The reviewed diff prints Enter y/n: and Please answer y or n verbatim.\\n"
        + "No real gate here.\\n\\nAGREE\\n' > panel-claude.txt; "
        + "sleep 3"
    )
    rc, text, status, tail = _run_claude_tui_session(
        command=["sh", "-c", script],
        cwd=tmp_path,
        prompt="review this bundle\n",
        output_file=tmp_path / "panel-claude.txt",
        timeout_s=30,
        env={"PATH": "/usr/bin:/bin"},
        backstop_s=30,
    )
    assert status == "claude_tui_file_output", f"post-submit trigger text must not block; got {status!r}"
    assert status != "claude_tui_workspace_trust_blocked"
    assert "AGREE" in text


def test_production_shaped_cwd_full_path_token_answers(tmp_path, monkeypatch):
    """CR F1/R5: production allocates ``mkdtemp('pl-panel-')/out`` — basename ``out`` is
    vacuous. The modal must be answered via the run-unique FULL path, so a production
    ``<uniq>/out`` cwd still clears the gate and completes."""
    _fast_timing(monkeypatch)
    out = tmp_path / "out"  # basename == "out", like production
    out.mkdir()
    script = (
        _MODAL
        + "IFS= read -r ans; printf '%s' \"$ans\" > answer.txt; "
        + "printf '\\nClaude Code v2.1.208\\nmanual mode on ready now\\n'; "
        + "printf 'fine.\\n\\nAGREE\\n' > panel-claude.txt; sleep 3"
    )
    rc, text, status, tail = _run_claude_tui_session(
        command=["sh", "-c", script], cwd=out, prompt="review this\n",
        output_file=out / "panel-claude.txt", timeout_s=30,
        env={"PATH": "/usr/bin:/bin"}, backstop_s=30,
    )
    assert status == "claude_tui_file_output", f"full-path token must clear the gate; got {status!r}"
    assert (out / "answer.txt").read_text().strip() == "y"


def test_wrong_dir_modal_is_not_answered(tmp_path, monkeypatch):
    """CR F1/R5 (negative): a trust modal for a DIFFERENT directory whose basename is
    also ``out`` must NOT be auto-answered (the old bare-basename token would have
    vacuously matched). No ``y`` is sent; the gate is never cleared."""
    _fast_timing(monkeypatch, ready_deadline=3.0, stall=120)
    out = tmp_path / "out"
    out.mkdir()
    # Modal for a FOREIGN path (basename "out", different full path than our cwd).
    script = (
        "printf 'Permission Required: Accessing workspace:\\n/tmp/some-other-run/out\\n"
        "y. Yes, I trust this folder\\nn. No, exit\\nEnter y/n:'; "
        "IFS= read -r ans; printf '%s' \"$ans\" > answer.txt; sleep 60"
    )
    rc, text, status, tail = _run_claude_tui_session(
        command=["sh", "-c", script], cwd=out, prompt="review this\n",
        output_file=out / "panel-claude.txt", timeout_s=120,
        env={"PATH": "/usr/bin:/bin"}, backstop_s=120,
    )
    # The gate signature was seen but our path-scoped conjunction did NOT match the
    # foreign path, so we never answered and — crucially — never armed readiness / pasted
    # the prompt into the y/n field. It fails CLOSED as trust_blocked (an uncleared gate),
    # NOT by pasting the review into a foreign modal. And no stray "y" was written.
    assert status == "claude_tui_workspace_trust_blocked", f"foreign-dir modal must fail closed; got {status!r}"
    assert not (out / "answer.txt").exists(), "a stray y was written to a foreign-dir modal"


def test_modal_answered_but_editor_never_ready_is_editor_not_ready(tmp_path, monkeypatch):
    """CR F2/R6: a modal that IS answered but whose editor never reaches readiness is
    ``claude_tui_editor_not_ready`` (an editor-readiness failure), NOT the misleading
    ``claude_tui_workspace_trust_blocked``. (The prompt line is newline-terminated so the
    cooked-mode echo of ``y`` produces no post-answer novel content — isolating the
    "answered but no editor output" state, which in the real TUI is otherwise masked by
    the redraw that follows the answer.)"""
    _fast_timing(monkeypatch, ready_deadline=3.0, stall=120)
    modal_nl = (
        "printf 'Permission Required: Accessing workspace:\\n%s\\n"
        "y. Yes, I trust this folder\\nn. No, exit\\nEnter y/n:\\n' \"$PWD\"; "
    )
    script = modal_nl + "IFS= read -r ans; printf '%s' \"$ans\" > answer.txt; sleep 60"
    rc, text, status, tail = _run_claude_tui_session(
        command=["sh", "-c", script], cwd=tmp_path, prompt="review this\n",
        output_file=tmp_path / "panel-claude.txt", timeout_s=120,
        env={"PATH": "/usr/bin:/bin"}, backstop_s=120,
    )
    assert (tmp_path / "answer.txt").read_text().strip() == "y", "the modal should have been answered"
    assert status == "claude_tui_editor_not_ready", f"answered-but-unready must be editor_not_ready; got {status!r}"


def test_non_typed_failure_also_carries_pty_tail(tmp_path, monkeypatch):
    """CR F3/R3: the redacted tail must reach the leg result for EVERY non-OK failure,
    not only the typed-degraded trio (a missing-canonical / eof / timeout too)."""
    monkeypatch.setattr(pi, "_run_claude_tui_session",
                        lambda **kw: (1, "", "claude_tui_missing_canonical_output", "diag tail Z"))
    monkeypatch.setattr(pi, "_claude_code_support_status", lambda: (True, "supported"))
    monkeypatch.setattr(pi, "_under_claude_code", lambda env=None: False)
    review_dir = tmp_path / "review"
    out_dir = tmp_path / "out"
    review_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True)
    status, text = _exec_claude_tui_leg(review_dir, out_dir, 30, "bundle", env={})
    assert status != "OK"
    assert "[pty-tail] diag tail Z" in text, "non-typed failure dropped the diagnostic tail"


def test_sanitized_pty_tail_redacts_strips_and_keeps_end(tmp_path):
    """The evidence tail strips ANSI + control bytes, is bounded, and keeps the END of
    the buffer (where the modal / reject context lives)."""
    raw = b"\x1b[2Kstart-of-buffer\x00\x07\x1b]0;title\x07 " + b"filler " * 40 + b"THE_END_MARKER"
    out = pi._sanitized_pty_tail(raw, max_chars=90)
    assert len(out) <= 90
    assert "THE_END_MARKER" in out  # keeps the END, not the head
    assert "\x1b" not in out and "\x00" not in out and "\x07" not in out


def test_sanitized_pty_tail_redacts_before_truncation(tmp_path):
    """R4 / codex: redact the WHOLE text THEN keep the tail — a secret whose KEY sits
    before the tail window must still be scrubbed (slice-then-redact would leak it)."""
    # key "password=" at offset 60 (OUTSIDE the last-80 window); value runs into the tail.
    raw = b"a" * 60 + b"password=" + b"S" * 110
    out = pi._sanitized_pty_tail(raw, max_chars=80)
    assert "S" * 20 not in out, "value leaked — tail was sliced BEFORE redaction"
    assert "<redacted>" in out


def _degraded_mapping(monkeypatch, tmp_path, marker, tail):
    monkeypatch.setattr(pi, "_run_claude_tui_session", lambda **kw: (1, "", marker, tail))
    monkeypatch.setattr(pi, "_claude_code_support_status", lambda: (True, "supported"))
    monkeypatch.setattr(pi, "_under_claude_code", lambda env=None: False)
    review_dir = tmp_path / "review"
    out_dir = tmp_path / "out"
    review_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True)
    return _exec_claude_tui_leg(review_dir, out_dir, 30, "bundle", env={})


def test_typed_reasons_map_to_degraded_with_pty_tail(tmp_path, monkeypatch):
    """Both new typed reasons surface as DEGRADED with a named cause AND the redacted
    tail folded into the propagating ``text`` (reaches PanelLegResult.text)."""
    status, text = _degraded_mapping(
        monkeypatch, tmp_path / "a", "claude_tui_workspace_trust_blocked", "redacted tail A"
    )
    assert status == "DEGRADED"
    assert "workspace-trust gate not cleared" in text
    assert "[pty-tail] redacted tail A" in text

    status, text = _degraded_mapping(
        monkeypatch, tmp_path / "b", "claude_tui_editor_not_ready", "redacted tail B"
    )
    assert status == "DEGRADED"
    assert "editor never reached prompt-ready state" in text
    assert "[pty-tail] redacted tail B" in text
