"""Process-group CPU sampling for the leg-liveness monitor.

A panel leg is launched with ``start_new_session=True``, so its process-group id
(pgid) equals the leader pid and every descendant it spawns inherits that pgid.
``group_cpu_ticks(leader_pid)`` therefore sums CPU (utime+stime) across the whole
leg — leader and all children — which is the heartbeat signal for a leg that is
*thinking silently* (codex/grok in print mode emit no incremental stdout, then
write their ``--output-last-message`` file at the end; a thinking process burns
CPU, a wedged one does not).

Linux-only via ``/proc``. On any other platform (or a missing ``/proc``) it
returns 0, so the runner degrades to a stdout-only heartbeat — still correct: a
streaming leg heartbeats on stdout, and a silent-AND-idle leg is genuinely dead.
"""
from __future__ import annotations

import os


def _pgrp_and_ticks(pid: int) -> tuple[int, int]:
    """Return ``(process_group_id, utime+stime)`` for ``pid`` from /proc/<pid>/stat.

    The ``comm`` field (2nd) is wrapped in parens and may itself contain spaces or
    parens, so parse the fixed fields AFTER the last ``)``: from there field 3
    (state) is index 0, so pgrp (field 5) = index 2, utime (14) = index 11, and
    stime (15) = index 12.
    """
    with open(f"/proc/{pid}/stat", encoding="ascii", errors="replace") as fh:
        data = fh.read()
    after = data[data.rfind(")") + 2 :].split()
    pgrp = int(after[2])
    return pgrp, int(after[11]) + int(after[12])


def group_cpu_ticks(leader_pid: int) -> int:
    """Sum CPU ticks (utime+stime) across every process in ``leader_pid``'s group.

    Returns 0 when ``/proc`` is unavailable (non-Linux) or nothing matches — the
    caller treats 0-delta as "no CPU heartbeat", which combined with the stdout
    heartbeat still catches a genuinely wedged leg and never false-kills a
    streaming one.
    """
    total = 0
    try:
        entries = os.listdir("/proc")
    except OSError:
        return 0
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            pgrp, ticks = _pgrp_and_ticks(int(entry))
        except (OSError, ValueError, IndexError):
            continue  # process exited mid-scan, or an unreadable/odd stat line
        if pgrp == leader_pid:
            total += ticks
    return total
