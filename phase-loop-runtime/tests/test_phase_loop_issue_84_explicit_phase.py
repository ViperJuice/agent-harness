"""RUNCORE lane (e) / #84 — regression guard: an explicit ``--phase`` on the
serial path dispatches the requested independent phase, not a repair of a blocked
sibling.

agent-harness#84 (filed against phase-loop 0.1.11) reported that
``phase-loop --phase ROOM … run`` launched another ``SEAL`` repair instead of
dispatching the explicitly requested, independent, already-planned ``ROOM`` while
``SEAL`` was blocked (non-human ``repeated_verification_failure``).

Investigation finding (see ``plans/decision-issue-84-*.md``): on current ``main``
the *serial* selector ``_select_ready_phase`` already honors an explicit
``--phase`` (``if phase: return phase.upper()``), so the full dispatch launches
``ROOM`` execute — the selection-level symptom does not reproduce. The adjacent
*concurrent* coordinator-waves selector DID drop the explicit phase; that is fixed
in lane (d) (``_select_parallel_dispatch_phase``). This test pins the serial-path
behavior against regression by driving the exact #84 scenario and asserting the
dispatched (phase, action) is ``(ROOM, execute)``, never a ``SEAL`` repair.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.models import LoopEvent, StateSnapshot, utc_now
from phase_loop_runtime.provenance import event_provenance, snapshot_provenance
from phase_loop_runtime.events import append_event
from phase_loop_runtime.state import write_state
from phase_loop_runtime.runner import run_loop
from phase_loop_test_utils import commit_fixture_paths, make_repo, write_named_roadmap, write_phase_plan


class _Dispatched(Exception):
    def __init__(self, phase, action):
        self.phase = phase
        self.action = action


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True).stdout


def _seed_seal_blocked_room_planned(tmp_path: Path):
    repo = make_repo(tmp_path)
    roadmap = write_named_roadmap(
        repo, (("DEPLOY", "Deploy"), ("SEAL", "Seal"), ("ROOM", "Room")), version="gap-v1"
    )
    seal_plan = write_phase_plan(repo, "SEAL", roadmap, owned_files=("seal.py",))
    room_plan = write_phase_plan(repo, "ROOM", roadmap, owned_files=("room.py",))
    commit_fixture_paths(repo, "add roadmap + plans", roadmap, seal_plan, room_plan)
    # SEAL blocked (non-human) with staged evidence still in the tree.
    (repo / "seal.py").write_text("staged seal evidence\n", encoding="utf-8")
    _git(repo, "add", "seal.py")
    append_event(
        repo,
        LoopEvent(
            timestamp=utc_now(), repo=str(repo), roadmap=str(roadmap), phase="SEAL",
            action="execute", status="blocked", model="m", reasoning_effort="medium", source="fixture",
            blocker={
                "human_required": False,
                "blocker_class": "repeated_verification_failure",
                "blocker_summary": "live staging verification failure",
                "required_human_inputs": (),
                "access_attempts": (),
            },
            **event_provenance(roadmap, "SEAL"),
        ),
    )
    append_event(
        repo,
        LoopEvent(
            timestamp=utc_now(), repo=str(repo), roadmap=str(roadmap), phase="ROOM",
            action="plan", status="planned", model="m", reasoning_effort="medium", source="fixture",
            **event_provenance(roadmap, "ROOM"),
        ),
    )
    write_state(
        repo,
        StateSnapshot(
            timestamp=utc_now(), repo=str(repo), roadmap=str(roadmap),
            phases={"DEPLOY": "complete", "SEAL": "blocked", "ROOM": "planned"},
            current_phase="SEAL", **snapshot_provenance(roadmap),
        ),
    )
    return repo, roadmap


def test_explicit_room_dispatches_room_execute_not_seal_repair(tmp_path):
    repo, roadmap = _seed_seal_blocked_room_planned(tmp_path)

    def fake_build_prompt(launch_action, **kwargs):
        raise _Dispatched(kwargs.get("phase"), launch_action)

    with patch("phase_loop_runtime.runner.build_prompt", side_effect=fake_build_prompt):
        try:
            run_loop(
                repo,
                roadmap,
                phase="ROOM",
                max_phases=1,
                full_phase=True,
                closeout_mode="manual",
                bypass_approvals=True,
                allow_cross_phase_dirty_reason=(
                    "SEAL is blocked with staged evidence; ROOM is independent and already planned."
                ),
            )
        except _Dispatched as dispatched:
            assert dispatched.phase == "ROOM", dispatched.phase
            assert dispatched.action == "execute", dispatched.action
        else:
            raise AssertionError("expected a dispatch of ROOM; none occurred")
