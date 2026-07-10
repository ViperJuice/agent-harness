"""``group_cpu_ticks`` — the CPU heartbeat for the leg-liveness monitor."""
from __future__ import annotations

import os
import sys

import pytest

from phase_loop_runtime import _proc_cpu
from phase_loop_runtime._proc_cpu import group_cpu_ticks

_LINUX = sys.platform.startswith("linux") and os.path.isdir("/proc")
_needs_proc = pytest.mark.skipif(not _LINUX, reason="process-group CPU sampling needs linux /proc")


@_needs_proc
def test_own_group_has_consumed_cpu() -> None:
    assert group_cpu_ticks(os.getpgrp()) > 0


@_needs_proc
def test_monotonic_and_advances_under_load() -> None:
    pg = os.getpgrp()
    before = group_cpu_ticks(pg)
    x = 0
    for _ in range(5_000_000):
        x += 1
    after = group_cpu_ticks(pg)
    assert after >= before          # CPU never decreases (heartbeat is monotonic)


@_needs_proc
def test_unknown_group_is_zero() -> None:
    assert group_cpu_ticks(2_147_483_600) == 0


def test_missing_proc_degrades_to_zero(monkeypatch) -> None:
    # The advertised non-Linux / no-/proc degradation: os.listdir("/proc") raises OSError
    # and group_cpu_ticks returns 0 (the runner then relies on the stdout/stderr heartbeat
    # alone — still correct: a streaming leg heartbeats, a silent-and-idle one is dead).
    def _boom(path):
        raise FileNotFoundError(path)

    monkeypatch.setattr(_proc_cpu.os, "listdir", _boom)
    assert group_cpu_ticks(os.getpgrp() if _LINUX else 1) == 0
