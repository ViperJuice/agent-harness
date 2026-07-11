"""RUNCORE lane (c) / #59 — a valid planned repair closeout clears the stale
non-human blocker instead of looping repair.

Repro of agent-harness#59: a phase hit a non-human ``contract_bug`` blocker; a
bounded repair child narrowed the plan and emitted a valid closeout
(``terminal_status=planned``, ``verification_status=not_run``, ``dirty_paths=[]``,
no blocker, ``human_required=null``) leaving the tree clean. The parent runner did
not clear the stale blocked state and relaunched the same repair path, creating a
repair loop.

``repair_precondition_for_snapshot`` previously cleared ONLY
``dirty_worktree_conflict``; every other non-human blocker returned
``repair_required`` and re-dispatched repair. The fix extends the clearable set to
the planned-repair-closeout case — but conditioned on the repair child's own
evidence (a valid ``planned``/``not_run``/clean closeout with no blocker), NOT on
``blocker_class`` alone, so a legitimately un-repaired ``contract_bug`` still
requires repair.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from phase_loop_runtime.events import append_event
from phase_loop_runtime.models import LoopEvent, StateSnapshot, utc_now
from phase_loop_runtime.provenance import event_provenance, snapshot_provenance
from phase_loop_runtime.runner import repair_precondition_for_snapshot
from phase_loop_test_utils import commit_fixture_paths, make_repo, write_phase_plan


def _blocked_snapshot(repo: Path, roadmap: Path, *, blocker_class: str, human_required: bool = False):
    return StateSnapshot(
        timestamp=utc_now(),
        repo=str(repo),
        roadmap=str(roadmap),
        phases={"CONTRACT": "blocked"},
        current_phase="CONTRACT",
        human_required=human_required,
        blocker_class=blocker_class,
        blocker_summary="secret-shape deny check scanned pre-existing unowned fixtures.",
        **snapshot_provenance(roadmap),
    )


def _append_child_automation(
    repo: Path,
    roadmap: Path,
    *,
    status: str,
    verification_status: str,
    blocker_class=None,
    action: str = "repair",
    child_status: str = "planned",
):
    append_event(
        repo,
        LoopEvent(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phase="CONTRACT",
            action=action,
            status=child_status,
            model="gpt-5.6-terra",
            reasoning_effort="medium",
            source="fixture",
            metadata={
                "child_automation": {
                    "automation_status": status,
                    "automation_verification_status": verification_status,
                    "automation_human_required": "false",
                    "automation_blocker_class": blocker_class,
                    "automation_blocker_summary": None,
                    "dirty_paths": [],
                }
            },
            **event_provenance(roadmap, "CONTRACT"),
        ),
    )


def _setup(tmp_path: Path):
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("src/session.py",))
    commit_fixture_paths(repo, "add CONTRACT plan + repaired source", plan)
    return repo, roadmap, plan


def test_planned_repair_closeout_clears_contract_bug(tmp_path):
    repo, roadmap, plan = _setup(tmp_path)
    # The repair child reshaped the plan and emitted a valid planned/not_run closeout;
    # the tree is clean (the repair commit already landed).
    _append_child_automation(repo, roadmap, status="planned", verification_status="not_run")
    snapshot = _blocked_snapshot(repo, roadmap, blocker_class="contract_bug")
    result = repair_precondition_for_snapshot(repo, roadmap, "CONTRACT", plan, snapshot)
    assert result["status"] == "cleared", result


def test_contract_bug_without_planned_evidence_stays_repair_required(tmp_path):
    # No repair-child planned closeout evidence -> the blocker is NOT cleared blindly
    # by blocker_class + a clean tree; a genuine contract_bug still needs repair.
    repo, roadmap, plan = _setup(tmp_path)
    snapshot = _blocked_snapshot(repo, roadmap, blocker_class="contract_bug")
    result = repair_precondition_for_snapshot(repo, roadmap, "CONTRACT", plan, snapshot)
    assert result["status"] == "repair_required", result


def test_planned_repair_evidence_but_dirty_tree_not_cleared(tmp_path):
    # A planned closeout but an uncommitted dirty remainder -> not a clean repair;
    # the precondition does not clear (the work is not actually finalized).
    repo, roadmap, plan = _setup(tmp_path)
    _append_child_automation(repo, roadmap, status="planned", verification_status="not_run")
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "session.py").write_text("uncommitted repair remnant\n", encoding="utf-8")
    snapshot = _blocked_snapshot(repo, roadmap, blocker_class="contract_bug")
    result = repair_precondition_for_snapshot(repo, roadmap, "CONTRACT", plan, snapshot)
    assert result["status"] != "cleared", result


def test_later_blocked_child_supersedes_planned_evidence(tmp_path):
    # A planned closeout followed by a LATER blocked repair child (e.g. the repair
    # was interrupted) -> the latest evidence is not a clean planned closeout, so
    # the precondition does not clear.
    repo, roadmap, plan = _setup(tmp_path)
    _append_child_automation(repo, roadmap, status="planned", verification_status="not_run")
    _append_child_automation(
        repo, roadmap, status="blocked", verification_status="blocked",
        blocker_class="repeated_verification_failure", child_status="blocked",
    )
    snapshot = _blocked_snapshot(repo, roadmap, blocker_class="contract_bug")
    result = repair_precondition_for_snapshot(repo, roadmap, "CONTRACT", plan, snapshot)
    assert result["status"] != "cleared", result


def _append_plain_blocked(repo: Path, roadmap: Path, *, blocker_class="repeated_verification_failure"):
    # A runner-emitted blocker carries NO child_automation metadata (unlike a child
    # closeout). It must still supersede an earlier planned child.
    append_event(
        repo,
        LoopEvent(
            timestamp=utc_now(), repo=str(repo), roadmap=str(roadmap), phase="CONTRACT",
            action="execute", status="blocked", model="gpt-5.6-terra",
            reasoning_effort="medium", source="fixture",
            blocker={
                "human_required": False, "blocker_class": blocker_class,
                "blocker_summary": "fresh live failure after the repair",
                "required_human_inputs": (), "access_attempts": (),
            },
            **event_provenance(roadmap, "CONTRACT"),
        ),
    )


def test_later_plain_blocked_event_without_child_automation_supersedes_planned(tmp_path):
    # CR finding: the reader must not skip past a later blocked event that carries no
    # child_automation and then clear on an earlier planned child.
    repo, roadmap, plan = _setup(tmp_path)
    _append_child_automation(repo, roadmap, status="planned", verification_status="not_run")
    _append_plain_blocked(repo, roadmap)
    snapshot = _blocked_snapshot(repo, roadmap, blocker_class="repeated_verification_failure")
    result = repair_precondition_for_snapshot(repo, roadmap, "CONTRACT", plan, snapshot)
    assert result["status"] != "cleared", result


def test_planned_child_missing_verification_status_does_not_clear(tmp_path):
    # CR finding: a planned child whose verification_status is absent is not the #59
    # evidence and must not clear.
    repo, roadmap, plan = _setup(tmp_path)
    append_event(
        repo,
        LoopEvent(
            timestamp=utc_now(), repo=str(repo), roadmap=str(roadmap), phase="CONTRACT",
            action="repair", status="planned", model="gpt-5.6-terra",
            reasoning_effort="medium", source="fixture",
            metadata={"child_automation": {
                "automation_status": "planned",
                # verification_status intentionally omitted
                "automation_human_required": "false",
                "automation_blocker_class": None,
                "automation_blocker_summary": None,
                "dirty_paths": [],
            }},
            **event_provenance(roadmap, "CONTRACT"),
        ),
    )
    snapshot = _blocked_snapshot(repo, roadmap, blocker_class="contract_bug")
    result = repair_precondition_for_snapshot(repo, roadmap, "CONTRACT", plan, snapshot)
    assert result["status"] != "cleared", result


def test_blocked_event_carrying_planned_child_payload_does_not_clear(tmp_path):
    # CR: a single launch event can be blocked parent-side (e.g. missing produced
    # gates / a governed block) while STILL carrying a child_automation whose
    # automation_status == "planned". The block is authoritative — a planned child
    # payload on a BLOCKED event must not be read as a repaired/planned closeout.
    repo, roadmap, plan = _setup(tmp_path)
    append_event(
        repo,
        LoopEvent(
            timestamp=utc_now(), repo=str(repo), roadmap=str(roadmap), phase="CONTRACT",
            action="execute", status="blocked", model="gpt-5.6-terra",
            reasoning_effort="medium", source="fixture",
            blocker={
                "human_required": False, "blocker_class": "contract_bug",
                "blocker_summary": "parent blocked despite a planned child report",
                "required_human_inputs": (), "access_attempts": (),
            },
            metadata={"child_automation": {
                "automation_status": "planned",
                "automation_verification_status": "not_run",
                "automation_human_required": "false",
                "automation_blocker_class": None,
                "automation_blocker_summary": None,
                "dirty_paths": [],
            }},
            **event_provenance(roadmap, "CONTRACT"),
        ),
    )
    snapshot = _blocked_snapshot(repo, roadmap, blocker_class="contract_bug")
    result = repair_precondition_for_snapshot(repo, roadmap, "CONTRACT", plan, snapshot)
    assert result["status"] != "cleared", result


def test_planned_child_with_null_human_required_still_clears(tmp_path):
    # #59 real-payload guard: the repair child emits `human_required: null`
    # (parsed to the string "null"); the event ledger strips genuine None values, so
    # a clean closeout may legitimately omit the blocker fields entirely. Presence of
    # the load-bearing signals (planned / not_run / empty dirty_paths) is enough — a
    # null/absent human_required must NOT block the clear (else #59 stays broken).
    repo, roadmap, plan = _setup(tmp_path)
    append_event(
        repo,
        LoopEvent(
            timestamp=utc_now(), repo=str(repo), roadmap=str(roadmap), phase="CONTRACT",
            action="repair", status="planned", model="gpt-5.6-terra",
            reasoning_effort="medium", source="fixture",
            metadata={"child_automation": {
                "automation_status": "planned",
                "automation_verification_status": "not_run",
                "automation_human_required": "null",
                # blocker_class / blocker_summary legitimately absent (null-stripped)
                "dirty_paths": [],
            }},
            **event_provenance(roadmap, "CONTRACT"),
        ),
    )
    snapshot = _blocked_snapshot(repo, roadmap, blocker_class="contract_bug")
    result = repair_precondition_for_snapshot(repo, roadmap, "CONTRACT", plan, snapshot)
    assert result["status"] == "cleared", result


def test_human_required_contract_bug_still_sticky(tmp_path):
    # A human-required blocker is never cleared by the planned-repair path.
    repo, roadmap, plan = _setup(tmp_path)
    _append_child_automation(repo, roadmap, status="planned", verification_status="not_run")
    snapshot = _blocked_snapshot(repo, roadmap, blocker_class="contract_bug", human_required=True)
    result = repair_precondition_for_snapshot(repo, roadmap, "CONTRACT", plan, snapshot)
    assert result["status"] == "sticky", result
