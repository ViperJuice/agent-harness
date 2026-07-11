"""RUNCORE lane (d) — an explicit ``--phase`` is honored on the concurrent
scheduler (coordinator-waves) path.

The serial selector ``_select_ready_phase(repo, roadmap, classifications, phase)``
already honors an explicit phase; the concurrent coordinator-waves selector
``_select_parallel_dispatch_phase`` did not accept a ``phase`` argument at all, so it
could only pick by wave order and a fully-blocked earlier wave halted the loop.

Reachability note (CR): today ``coordinator_waves`` is populated only when ``phase``
is ``None`` (its derivation is gated on ``phase is None``), so through ``run_loop``
this selector is never called with an explicit phase — the explicit-phase case is
served by ``_select_ready_phase``. Threading ``phase`` here is therefore a
**defensive consistency** guarantee (the helper honors an explicit phase, bounded to
the wave structure, if that invariant ever changes), not a fix for a currently
reachable production defect. These tests pin the helper's contract directly.
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
