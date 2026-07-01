"""#48 — the advisor-panel Claude TUI leg must not hang when the child CLI exits.

When the child (and its descendants) close the PTY slave, ``os.read`` hits EOF.
An EOF fd is always "readable", so without an explicit EOF branch the read loop
busy-spins to the (input-scaled, up to 30-min) deadline whenever the launched
process is a wrapper whose parent lingers after the CLI exits (``proc.poll()``
never fires). The leg must instead return a structured result promptly.
"""

from __future__ import annotations

import shutil
import time

import pytest

from phase_loop_runtime.panel_invoker import _classify_leg, _run_claude_tui_session

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="needs POSIX sh")


def test_claude_tui_leg_returns_promptly_on_pty_eof(tmp_path):
    # The child closes all three PTY fds (EOF fires immediately) but the process
    # lingers (sleep 60), so proc.poll() stays None — the exact wrapper-lingers
    # hang from #48. Deadline is 20s; the EOF path must return long before that.
    output_file = tmp_path / "panel-claude.txt"  # intentionally: no verdict written

    start = time.monotonic()
    rc, text, status = _run_claude_tui_session(
        command=["sh", "-c", "exec 0<&- 1>&- 2>&-; sleep 60"],
        cwd=tmp_path,
        prompt="review this",
        output_file=output_file,
        timeout_s=20,
        env={"PATH": "/usr/bin:/bin"},
    )
    elapsed = time.monotonic() - start

    assert elapsed < 10, (
        f"#48: TUI leg hung ~{elapsed:.1f}s toward the deadline instead of "
        f"returning promptly on PTY EOF"
    )
    assert status == "claude_tui_pty_eof_no_output", status
    # Structured, fail-closed classification — never a silent pass, never a hang.
    assert _classify_leg(rc, text, status) in {"ERROR", "EMPTY"}, (rc, status)


def test_claude_tui_eof_does_not_promote_transcript_verdict_to_ok(tmp_path, monkeypatch):
    """#48 CR: canonical output is the review FILE. On PTY EOF with no file verdict,
    a verdict scraped from the transcript is SALVAGE evidence only — it must NEVER be
    promoted to OK (that would be a race-dependent false-green in the fail-closed gate).
    """
    import phase_loop_runtime.panel_invoker as pi

    # Child closes all pty fds (EOF) and exits 0; no canonical review file is written,
    # but the transcript scrape returns a terminal verdict.
    monkeypatch.setattr(pi, "_latest_claude_transcript_text", lambda *a, **k: "DISAGREE — salvage only")
    output_file = tmp_path / "panel-claude.txt"  # never written → no file verdict

    rc, text, status = _run_claude_tui_session(
        command=["sh", "-c", "exec 0<&- 1>&- 2>&-; exit 0"],
        cwd=tmp_path,
        prompt="review this",
        output_file=output_file,
        timeout_s=20,
        env={"PATH": "/usr/bin:/bin"},
    )

    assert rc != 0, f"EOF without a canonical file verdict must be non-zero (fail-closed); got rc={rc}"
    assert status == "claude_tui_pty_eof_no_output", status
    assert _classify_leg(rc, text, status) != "OK", (
        f"a transcript-only verdict must not classify OK (false-green); "
        f"got {_classify_leg(rc, text, status)}"
    )
