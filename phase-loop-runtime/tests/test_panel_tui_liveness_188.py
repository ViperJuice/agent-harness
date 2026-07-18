"""#188 — the Claude TUI (PTY) advisor leg must be reclaimed on reviewer-heartbeat
extinction, NOT hang on cosmetic animation and NOT rely on a fixed short timeout.

Regression: after ViperJuice/agent-harness#185 the Fable panel leg runs headless
through a self-allocated PTY (``_run_claude_tui_session`` → ``pty.openpty``). The
pre-#188 monitor reset its stall clock on ANY PTY byte and on ANY CPU advance. But
the Claude TUI repaints an animated "✻ Herding… (Ns · esc to interrupt)" status
line ~1x/sec while blocked in ``ep_poll``, and a Node CLI trickles libuv/GC CPU
while blocked — so a genuinely-wedged leg (no output, ~2s CPU) hung ~17 min and no
timeout fired. #188 makes liveness reviewer-progress-based: cosmetic repaints and
incidental CPU no longer count; genuine novel output / file / transcript growth do.

These are real-subprocess PTY tests (like ``test_panel_tui_eof_48``): no mocking of
the read loop, only a monkeypatched-short stall window so the wedge is caught in
seconds instead of the production 180s.
"""

from __future__ import annotations

import shutil
import time

import pytest

import phase_loop_runtime.panel_invoker as pi
from phase_loop_runtime.panel_invoker import _classify_leg, _run_claude_tui_session

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="needs POSIX sh")

# A hermetic empty transcript: tmp cwds have no ~/.claude project, but pin it so the
# transcript-growth heartbeat can never spuriously reset the clock in these tests.
_NO_TRANSCRIPT = lambda *a, **k: ""  # noqa: E731


# A wedged TUI: repaints the SAME animated status line each ~0.1s (only the elapsed
# digit changes → de-animates to a single seen line), burns a CPU trickle each
# iteration, never writes the review file, never exits. This is the "~2s CPU / no
# output / animating forever" signature of the #188 repro.
_WEDGE_SCRIPT = (
    "i=0; while :; do "
    "  i=$((i+1)); "
    "  s=0; while [ $s -lt 3000 ]; do s=$((s+1)); done; "  # CPU trickle
    r'  printf "\r\033[2K* Herding... (%ss . esc to interrupt)" "$i"; '
    "  sleep 0.1; "
    "done"
)

# A slow-but-progressing TUI: emits a NOVEL de-animated line each ~0.6s (distinct
# words, not just a changing digit) with NO file output, then exits. Each novel line
# is a genuine reviewer heartbeat; the leg must survive many stall windows.
_PROGRESS_SCRIPT = (
    "for w in alpha bravo charlie delta echo foxtrot golf hotel india juliet; do "
    r'  printf "reviewing the %s section of the staged bundle now\n" "$w"; '
    "  sleep 0.6; "
    "done"
)


def test_wedged_pty_leg_is_reclaimed_on_heartbeat_extinction(tmp_path, monkeypatch):
    """(a) A silent-hung PTY leg that keeps ANIMATING (cosmetic repaints) and trickles
    CPU is detected and terminated within the stall window — NOT at the wall-clock
    backstop, NOT never. The generous backstop (120s) proves the kill is heartbeat
    reclaim, not a fixed timeout."""
    monkeypatch.setattr(pi, "_LEG_STALL_THRESHOLD_S", 3)
    monkeypatch.setattr(pi, "_CLAUDE_TUI_SUBMIT_DELAY_S", 999)  # never submit; irrelevant here
    monkeypatch.setattr(pi, "_latest_claude_transcript_text", _NO_TRANSCRIPT)
    output_file = tmp_path / "panel-claude.txt"  # never written

    start = time.monotonic()
    rc, text, status, _pty_tail = _run_claude_tui_session(
        command=["sh", "-c", _WEDGE_SCRIPT],
        cwd=tmp_path,
        prompt="review this",
        output_file=output_file,
        timeout_s=120,
        env={"PATH": "/usr/bin:/bin"},
        backstop_s=120,  # generous: a fixed-timeout kill would need this to be short
    )
    elapsed = time.monotonic() - start

    assert status == "claude_tui_stalled", (
        f"wedged animating TUI must be reclaimed as stalled; got {status!r} "
        f"(pre-#188: cosmetic repaints reset the heartbeat forever)"
    )
    assert 3 <= elapsed < 30, (
        f"reclaim took {elapsed:.1f}s — must be ~stall_threshold (3s), not the 120s "
        f"backstop (would prove a fixed timeout) and not never"
    )
    assert rc != 0, f"a reclaimed wedge must be fail-closed non-zero; got rc={rc}"


def test_wedged_stall_marker_classifies_degraded(tmp_path, monkeypatch):
    """(a cont.) The reclaimed stall is surfaced as a TYPED leg. ``_exec_claude_tui_leg``
    maps the ``claude_tui_stalled`` marker to DEGRADED (never a silent OK / bare ERROR),
    so the panel summary names the reclaim while the completed seats are preserved. This
    exercises the REAL wiring (not a re-implementation of the mapping)."""
    review_dir = tmp_path / "review"
    out_dir = tmp_path / "out"
    review_dir.mkdir()
    out_dir.mkdir()
    # Run the leg RIGHT HERE (headless self-PTY path): not under Claude Code, CLI supported.
    monkeypatch.setattr(pi, "_under_claude_code", lambda env=None: False)
    monkeypatch.setattr(pi, "_claude_code_support_status", lambda: (True, ""))
    monkeypatch.setattr(pi, "_subscription_env", lambda: {"PATH": "/usr/bin:/bin"})
    # The session reclaims a genuinely-wedged leg: non-zero rc, no verdict, stall marker.
    monkeypatch.setattr(pi, "_run_claude_tui_session", lambda **kwargs: (1, "", "claude_tui_stalled", ""))

    status, _text = pi._exec_claude_tui_leg(review_dir, out_dir, 120, "bundle")

    assert status == "DEGRADED", (
        f"a heartbeat-reclaimed wedge must be surfaced as DEGRADED (typed stalled leg), "
        f"not a bare ERROR/silent OK; got {status!r}"
    )
    assert status in pi.LEG_STATUSES


def test_slow_but_progressing_pty_leg_is_not_killed(tmp_path, monkeypatch):
    """(b) A leg that emits NOVEL de-animated terminal text every interval (genuine
    reviewer heartbeat) survives many stall windows and is NOT false-killed — even with
    no file output yet and the same short stall threshold that reclaims the wedge."""
    monkeypatch.setattr(pi, "_LEG_STALL_THRESHOLD_S", 2)
    monkeypatch.setattr(pi, "_CLAUDE_TUI_SUBMIT_DELAY_S", 999)  # isolate the PTY-progress signal
    monkeypatch.setattr(pi, "_latest_claude_transcript_text", _NO_TRANSCRIPT)
    output_file = tmp_path / "panel-claude.txt"  # intentionally never written by the script

    start = time.monotonic()
    rc, text, status, _pty_tail = _run_claude_tui_session(
        command=["sh", "-c", _PROGRESS_SCRIPT],
        cwd=tmp_path,
        prompt="review this",
        output_file=output_file,
        timeout_s=120,
        env={"PATH": "/usr/bin:/bin"},
        backstop_s=120,
    )
    elapsed = time.monotonic() - start

    assert status != "claude_tui_stalled", (
        f"a leg emitting novel output every 0.6s must NOT be reclaimed as stalled "
        f"(stall window was 2s); got {status!r} after {elapsed:.1f}s"
    )
    # 10 novel lines × 0.6s ≈ 6s, far past 2× the 2s stall window — proof it lived on
    # its reviewer heartbeat, then exited on its own (no canonical file → typed miss).
    assert elapsed > 4, f"leg exited/was reclaimed too early ({elapsed:.1f}s) — progress heartbeat failed"
    # It lived on its reviewer heartbeat and then ended on ITS OWN terms (the script
    # exits and closes the PTY → structured EOF / missing-canonical result), never a
    # liveness reclaim. The exact terminal detail depends on exit-vs-poll ordering.
    assert status in {"claude_tui_pty_eof_no_output", "claude_tui_missing_canonical_output"}, status


def test_novel_line_split_across_read_boundaries_is_detected_whole():
    """(b cont., #188 CR) A novel review line delivered in TWO halves across two
    ``os.read`` boundaries must register as ONE progress event once the line completes
    — the carry-buffer holds the trailing partial line so novelty is evaluated on the
    WHOLE line, not on fragments (which could each collide with a seen/too-short form
    and silently drop the heartbeat)."""
    seen: set[str] = set()
    carry = bytearray()

    # First read delivers the line WITHOUT a terminator: no complete line yet, so
    # nothing is evaluated and no fragment pollutes `seen`.
    complete1 = pi._tui_take_complete_lines(carry, b"reviewing the acceptance crit")
    assert complete1 == b"", "an unterminated partial line must not be emitted"
    assert not pi._tui_chunk_has_novel_content(complete1, seen)
    assert seen == set(), "no fragment should be recorded before the line completes"

    # Second read completes the line (adds the tail + newline): now the WHOLE line is
    # evaluated and counts as novel progress.
    complete2 = pi._tui_take_complete_lines(carry, b"eria section now\n")
    assert pi._tui_chunk_has_novel_content(complete2, seen), (
        "the reassembled whole line must be detected as novel progress"
    )
    # The recorded token is the whole normalized line, not either half.
    assert "reviewing the acceptance criteria section now" in seen
    assert carry == bytearray(), "no residue after a terminated line"


def test_no_fixed_short_timeout_is_injected_when_caller_did_not_request_one(tmp_path):
    """(c) With no caller-supplied per-leg timeout, the runtime does NOT inject a fixed
    model timeout: the hard deadline is raised to the _MAX backstop and the real kill is
    heartbeat reclaim (tested above). An explicit override is still honored as-is."""
    (tmp_path / "review-bundle.md").write_text("small bundle", encoding="utf-8")

    ref_default, deadline_default = pi._leg_deadline_from(None, tmp_path)
    # No fixed short timeout: the backstop is the generous _MAX (>= 1800s), not the base.
    assert deadline_default >= pi._MAX_LEG_TIMEOUT_S
    assert deadline_default > pi._LEG_TIMEOUT_BASE_S

    # An EXPLICIT caller override is honored verbatim (still bounded — the contract path).
    ref_explicit, deadline_explicit = pi._leg_deadline_from(300, tmp_path)
    assert (ref_explicit, deadline_explicit) == (300, 300)
