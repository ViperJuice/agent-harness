"""RUNCORE lane (d) — an explicit ``--phase`` is honored on the concurrent
scheduler (coordinator-waves) path.

The serial selector ``_select_ready_phase(repo, roadmap, classifications, phase)``
already honors an explicit phase, but the concurrent coordinator-waves selector
``_select_parallel_dispatch_phase(coordinator_waves, classifications)`` silently
dropped it — wave order picked the phase instead, and a fully-blocked earlier wave
halted the loop even when the operator asked for a ready independent phase in a
later wave.
"""

from __future__ import annotations

from phase_loop_runtime.runner import _select_parallel_dispatch_phase

WAVES = (("SEAL",), ("ROOM", "AVAIL"))
CLASSIFICATIONS = {"SEAL": "blocked", "ROOM": "planned", "AVAIL": "unplanned"}


def test_explicit_phase_is_honored_over_wave_order():
    # Without the explicit phase, the first wave is fully blocked so the selector
    # halts (returns None) — the pre-fix behavior that stranded a ready ROOM.
    assert _select_parallel_dispatch_phase(WAVES, CLASSIFICATIONS) is None
    # With the explicit phase, the ready independent ROOM is dispatched.
    assert _select_parallel_dispatch_phase(WAVES, CLASSIFICATIONS, "ROOM") == "ROOM"


def test_explicit_phase_is_uppercased():
    assert _select_parallel_dispatch_phase(WAVES, CLASSIFICATIONS, "room") == "ROOM"


def test_explicit_phase_not_in_any_wave_selects_nothing():
    assert _select_parallel_dispatch_phase(WAVES, CLASSIFICATIONS, "NOPE") is None


def test_no_explicit_phase_preserves_wave_selection():
    # Backcompat: the default (no explicit phase) selection is unchanged.
    waves = (("ROOM", "AVAIL"),)
    classifications = {"ROOM": "planned", "AVAIL": "unplanned"}
    assert _select_parallel_dispatch_phase(waves, classifications) == "ROOM"
    assert _select_parallel_dispatch_phase(waves, classifications, None) == "ROOM"
