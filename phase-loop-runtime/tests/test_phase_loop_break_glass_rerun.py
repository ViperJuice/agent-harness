"""RUNCORE lane (b) / #71 — ``--closeout-allow-unowned`` breaks through a sticky
human-required ``closeout_scope_violation`` on rerun.

Repro of agent-harness#71: after a partial closeout commits the phase-owned
subset and blocks with ``blocker_class=closeout_scope_violation``,
``human_required=true`` over a live unowned remainder, an operator rerun with a
non-empty ``--closeout-allow-unowned <reason>`` recorded the attestation event but
did **not** recover — the dispatch closure short-circuited at
``if snapshot.human_required: return break`` before closeout could consume
``allow_unowned_reason``.

The protocol (see the ``CloseoutAllowUnownedAttestationTest`` docstring in
``test_phase_loop_reconcile_blocker_self_clear``) is that the *live* human-required
``closeout_scope_violation`` is broken through by **this rerun (SL-1)**, not by
reconcile. Secrets are never break-glassable, and other human-required blockers
(``missing_secret`` etc.) still short-circuit.

Two coupled edits land together (atomicity — an interleaved state is unsafe):
  1. the human-required short-circuit routes into ``_perform_phase_closeout`` with
     the reason when the blocker is break-glassable; and
  2. the closeout fallback re-derives the live-git remainder when the reconciled
     blocked snapshot carries no dirty summary (the blocking closeout event records
     none), so the remainder is actually force-committed under the reason.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from phase_loop_runtime.events import append_event
from phase_loop_runtime.models import LoopEvent, StateSnapshot, utc_now
from phase_loop_runtime.provenance import event_provenance, snapshot_provenance
from phase_loop_runtime.reconcile import reconcile
from phase_loop_runtime.runner import run_loop
from phase_loop_runtime.state import write_state
from phase_loop_test_utils import commit_fixture_paths, make_repo, write_phase_plan

def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout


def _seed_blocked_scope_violation(
    tmp_path: Path,
    *,
    remainder: str,
    blocker_class: str = "closeout_scope_violation",
):
    """A phase blocked human-required after a partial closeout, with ``remainder``
    left live-dirty as the unowned remainder (as the real bug left the renderer
    files). Mirrors ``_seed_blocked_snapshot`` — the blocked event carries no
    dirty summary, so the rerun's reconciled snapshot has empty ``dirty_paths``.
    """
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("owned_a.py",))
    # The phase-owned subset was already committed by the first closeout.
    (repo / "owned_a.py").write_text("committed by first closeout\n", encoding="utf-8")
    commit_fixture_paths(repo, "add CONTRACT plan + owned subset", plan, repo / "owned_a.py")
    # The unowned remainder is still live-dirty in the worktree.
    rem = repo / remainder
    rem.parent.mkdir(parents=True, exist_ok=True)
    rem.write_text("verified but outside plan ownership\n", encoding="utf-8")

    summary = (
        f"committed phase-owned paths; 1 verified dirty path outside plan owned files: {remainder}"
    )
    write_state(
        repo,
        StateSnapshot(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phases={"CONTRACT": "blocked"},
            current_phase="CONTRACT",
            last_action="reconcile",
            human_required=True,
            blocker_class=blocker_class,
            blocker_summary=summary,
            **snapshot_provenance(roadmap),
        ),
    )
    append_event(
        repo,
        LoopEvent(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phase="CONTRACT",
            action="execute",
            status="blocked",
            model="gpt-5.6-terra",
            reasoning_effort="medium",
            source="fixture",
            blocker={
                "human_required": True,
                "blocker_class": blocker_class,
                "blocker_summary": summary,
                "required_human_inputs": (),
                "access_attempts": (),
            },
            **event_provenance(roadmap, "CONTRACT"),
        ),
    )
    return repo, roadmap


def test_reconcile_preserves_the_sticky_human_required_block(tmp_path):
    # Precondition the fix depends on: reconcile keeps the human-required
    # closeout_scope_violation cached (human_required blockers never auto-clear),
    # AND the reconciled snapshot has no dirty summary (so the fallback re-derive
    # is load-bearing, not incidental).
    repo, roadmap = _seed_blocked_scope_violation(tmp_path, remainder="rogue.py")
    snap = reconcile(repo, roadmap)
    assert snap.phases["CONTRACT"] == "blocked"
    assert snap.human_required is True
    assert snap.blocker_class == "closeout_scope_violation"
    assert snap.dirty_paths == ()  # blocking event carried no completion_dirty_worktree


def test_break_glass_rerun_force_commits_and_clears(tmp_path):
    repo, roadmap = _seed_blocked_scope_violation(tmp_path, remainder="rogue.py")
    head_before = _git(repo, "rev-parse", "HEAD").strip()

    snapshot, _results = run_loop(
        repo,
        roadmap,
        phase="CONTRACT",
        closeout_mode="commit",
        allow_unowned_reason="CONTRACT SL-2 ownership omission: rogue.py verified for policy wiring",
    )

    # The remainder was force-committed under the audited reason and the phase cleared.
    assert snapshot.phases["CONTRACT"] in {"complete", "awaiting_phase_closeout"}
    assert snapshot.phases["CONTRACT"] != "blocked"
    assert _git(repo, "rev-parse", "HEAD").strip() != head_before
    assert "rogue.py" in _git(repo, "show", "--name-only", "--format=", "HEAD")
    assert "rogue.py" not in _git(repo, "status", "--short")


def test_break_glass_rerun_without_reason_still_short_circuits(tmp_path):
    # No reason -> the human-required block is NOT broken through (unchanged behavior).
    repo, roadmap = _seed_blocked_scope_violation(tmp_path, remainder="rogue.py")
    head_before = _git(repo, "rev-parse", "HEAD").strip()

    snapshot, _results = run_loop(repo, roadmap, phase="CONTRACT", closeout_mode="commit")

    assert snapshot.phases["CONTRACT"] == "blocked"
    assert _git(repo, "rev-parse", "HEAD").strip() == head_before
    assert "rogue.py" in _git(repo, "status", "--short")


def test_break_glass_rerun_never_commits_secret_even_with_reason(tmp_path):
    # secrets are NEVER break-glassable: the phase stays blocked, the secret uncommitted.
    repo, roadmap = _seed_blocked_scope_violation(tmp_path, remainder=".env")
    head_before = _git(repo, "rev-parse", "HEAD").strip()

    snapshot, _results = run_loop(
        repo,
        roadmap,
        phase="CONTRACT",
        closeout_mode="commit",
        allow_unowned_reason="operator override attempt for the secret",
    )

    assert snapshot.phases["CONTRACT"] == "blocked"
    assert _git(repo, "rev-parse", "HEAD").strip() == head_before
    assert ".env" in _git(repo, "status", "--short")


def test_break_glass_reason_does_not_break_through_non_closeout_blocker(tmp_path):
    # A non-break-glassable human-required blocker (missing_secret) still short-circuits
    # even with a reason — break-through is scoped to closeout_scope_violation.
    repo, roadmap = _seed_blocked_scope_violation(
        tmp_path, remainder="rogue.py", blocker_class="missing_secret"
    )
    head_before = _git(repo, "rev-parse", "HEAD").strip()

    snapshot, _results = run_loop(
        repo,
        roadmap,
        phase="CONTRACT",
        closeout_mode="commit",
        allow_unowned_reason="does not apply to missing_secret",
    )

    assert snapshot.phases["CONTRACT"] == "blocked"
    assert snapshot.blocker_class == "missing_secret"
    assert _git(repo, "rev-parse", "HEAD").strip() == head_before
