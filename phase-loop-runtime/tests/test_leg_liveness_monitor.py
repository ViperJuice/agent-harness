"""Behavior tests for the stall-aware leg-liveness runner (`_run_leg_with_liveness`).

These use REAL `subprocess.Popen` (no mock) with small thresholds to prove the
load-bearing behaviors on the review-gating path:
- a silent+idle leg is reclaimed on HEARTBEAT EXTINCTION (~stall_threshold), NOT at
  the wall-clock deadline;
- a CPU-active-but-silent leg is NOT false-killed by the stall timer (the secondary
  CPU heartbeat extends its life) — it dies only at the deadline backstop;
- a streaming leg survives to normal exit via its stdout heartbeat;
- stdin is fed deadlock-safely by the writer thread.

The CPU-sample cadence constant is monkeypatched below its default (5s) only where a
test needs the secondary CPU heartbeat to fire faster than a short test stall window;
production keeps 5s << 180s so CPU is sampled dozens of times per stall window.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from phase_loop_runtime import panel_invoker as pi

# Children launched by the runner use start_new_session=True (pgid == child pid), so
# group_cpu_ticks works for THEM even though this test process isn't a group leader.
# Availability is therefore a plain /proc (linux) check, not a self-pid probe.
_CPU_AVAILABLE = sys.platform.startswith("linux") and os.path.isdir("/proc")


def _run(cmd, *, deadline_s, stall_threshold_s, input_text=None):
    return pi._run_leg_with_liveness(
        cmd,
        cwd=".",
        env=os.environ,
        deadline_s=deadline_s,
        stall_threshold_s=stall_threshold_s,
        input_text=input_text,
    )


def test_silent_idle_leg_is_reclaimed_on_stall_not_deadline():
    # sleep: no stdout, no CPU. Must die ~stall_threshold, FAR before the deadline.
    t0 = time.monotonic()
    result = _run(["sleep", "600"], deadline_s=60, stall_threshold_s=2)
    elapsed = time.monotonic() - t0
    assert "[leg-liveness] stalled" in result.stderr
    assert elapsed < 15, f"took {elapsed:.1f}s — should be ~stall_threshold (2s), not the 60s deadline"


@pytest.mark.skipif(not _CPU_AVAILABLE, reason="process-group CPU sampling needs linux /proc")
def test_cpu_active_silent_leg_survives_stall_and_dies_at_deadline(monkeypatch):
    # A busy-loop emits NO stdout but burns CPU. The secondary CPU heartbeat must keep
    # resetting last_heartbeat so the stall timer never fires; it dies only at the
    # wall-clock deadline. Speed the CPU sampler so it beats the short test stall window.
    monkeypatch.setattr(pi, "_LEG_LIVENESS_CPU_SAMPLE_S", 0.3)
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        _run([sys.executable, "-c", "while True: pass"], deadline_s=4, stall_threshold_s=1)
    elapsed = time.monotonic() - t0
    # survived WELL past the 1s stall window (CPU heartbeat), died at the ~4s deadline.
    assert elapsed >= 3.5, f"died at {elapsed:.1f}s — CPU heartbeat should have carried it to the deadline"


def test_streaming_leg_survives_to_exit_via_stdout_heartbeat():
    # emits a line every 0.3s for ~3s; the stdout heartbeat must keep it alive to exit.
    result = _run(
        ["bash", "-c", "for i in $(seq 10); do echo tick; sleep 0.3; done"],
        deadline_s=30,
        stall_threshold_s=2,
    )
    assert result.returncode == 0
    assert result.stdout.count("tick") == 10
    assert "[leg-liveness] stalled" not in result.stderr


@pytest.mark.skipif(not _CPU_AVAILABLE, reason="process-group CPU sampling needs linux /proc")
def test_deadline_backstop_fires_even_when_cpu_active(monkeypatch):
    # deadline below the stall window: the wall-clock backstop must still fire (raise).
    monkeypatch.setattr(pi, "_LEG_LIVENESS_CPU_SAMPLE_S", 0.3)
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        _run([sys.executable, "-c", "while True: pass"], deadline_s=2, stall_threshold_s=30)
    assert time.monotonic() - t0 < 6


def test_stdin_is_fed_by_writer_thread_without_deadlock():
    # cat echoes stdin then exits on EOF — proves the daemon writer thread feeds stdin
    # and closes it (no deadlock, correct stdout capture).
    result = _run(["cat"], deadline_s=15, stall_threshold_s=10, input_text="hello-from-stdin\n")
    assert result.returncode == 0
    assert "hello-from-stdin" in result.stdout


def test_normal_fast_exit_returns_captured_output():
    result = _run(["bash", "-c", "echo done; exit 0"], deadline_s=15, stall_threshold_s=10)
    assert result.returncode == 0
    assert "done" in result.stdout
    assert "[leg-liveness] stalled" not in result.stderr
