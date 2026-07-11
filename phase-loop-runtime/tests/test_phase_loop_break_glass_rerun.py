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
from phase_loop_runtime.profiles import resolve_profile
from phase_loop_runtime.provenance import event_provenance, snapshot_provenance
from phase_loop_runtime.reconcile import reconcile
from phase_loop_runtime.runner import _perform_phase_closeout, run_loop
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
            # A real closeout_scope_violation records the attested remainder here; the
            # break-glass rerun scopes its live-git re-derive to exactly this set.
            metadata={"closeout": {"unowned_dirty_paths": [remainder]}},
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


def test_break_glass_rerun_does_not_sweep_unrelated_live_dirt(tmp_path):
    # CR: the break-glass re-derive is scoped to the remainder the prior closeout
    # RECORDED (what the reason attests to), not repo-wide live dirt. An unrelated
    # dirty file the operator happens to have in the tree must NOT be force-committed
    # under a reason that named only the phase's remainder.
    repo, roadmap = _seed_blocked_scope_violation(tmp_path, remainder="rogue.py")
    (repo / "utils.py").write_text("unrelated operator edit\n", encoding="utf-8")

    snapshot, _results = run_loop(
        repo,
        roadmap,
        phase="CONTRACT",
        closeout_mode="commit",
        allow_unowned_reason="verified rogue.py only",
    )

    committed = _git(repo, "show", "--name-only", "--format=", "HEAD")
    assert "rogue.py" in committed
    assert "utils.py" not in committed
    assert "utils.py" in _git(repo, "status", "--short")


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
    # CR (secret-only break-glass): the sticky human-required gate must NOT be
    # downgraded to a non-human dirty_worktree_conflict — a secret-only remainder
    # keeps closeout_scope_violation + human_required so the loop cannot silently
    # leave the human gate and run automation against a secret-dirty tree.
    assert snapshot.blocker_class == "closeout_scope_violation"
    assert snapshot.human_required is True


def test_break_glass_commit_does_not_sweep_a_pre_staged_secret(tmp_path):
    # CR finding (secret leak): a pathspec-less `git commit` swept a pre-STAGED
    # unrelated `.env` into the closeout commit even though the fallback excluded it
    # from closeout_dirty_paths — silently defeating secrets-never-break-glassable.
    # The closeout must commit ONLY the accepted paths and never the operator's index.
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("owned_a.py",))
    commit_fixture_paths(repo, "add CONTRACT plan", plan)
    (repo / "owned_a.py").write_text("owned\n", encoding="utf-8")
    (repo / "rogue.py").write_text("unowned source the operator accepts\n", encoding="utf-8")
    # Operator has ALSO pre-staged an unrelated secret in the index.
    (repo / ".env").write_text("API_TOKEN=supersecret\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", ".env"], check=True)

    snapshot = StateSnapshot(
        timestamp=utc_now(), repo=str(repo), roadmap=str(roadmap),
        phases={"CONTRACT": "awaiting_phase_closeout"}, current_phase="CONTRACT",
        phase_owned_dirty=False, phase_owned_dirty_paths=(),
        dirty_paths=("owned_a.py", "rogue.py"),
        closeout_terminal_status="complete", **snapshot_provenance(roadmap),
    )
    status, event = _perform_phase_closeout(
        repo, roadmap, "CONTRACT", snapshot, resolve_profile("execute"),
        action="execute", closeout_mode="commit",
        allow_unowned_reason="operator override for rogue.py only",
    )

    committed = _git(repo, "show", "--name-only", "--format=", "HEAD")
    assert "owned_a.py" in committed and "rogue.py" in committed
    # The pre-staged secret must NOT be swept into the commit.
    assert ".env" not in committed
    # ...and it is still present (staged) in the worktree, not silently discarded.
    assert ".env" in _git(repo, "status", "--short")


def test_noop_finalize_ignores_unrelated_staged_file(tmp_path):
    # CR: the closeout commit is path-scoped, so the "nothing staged" no-op check
    # (issue #6: verified work already on the base branch) must be scoped to the
    # closeout paths too. An unrelated staged file must NOT divert a valid
    # already-committed closeout into the commit branch (where the scoped commit
    # would fail "nothing to commit" and spuriously block).
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("owned_a.py",))
    (repo / "owned_a.py").write_text("verified work already committed out-of-band\n", encoding="utf-8")
    commit_fixture_paths(repo, "add plan + owned work", plan, repo / "owned_a.py")
    # An unrelated file is staged in the operator's index.
    (repo / "utils.py").write_text("unrelated staged edit\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "utils.py"], check=True)
    head_before = _git(repo, "rev-parse", "HEAD").strip()

    snapshot = StateSnapshot(
        timestamp=utc_now(), repo=str(repo), roadmap=str(roadmap),
        phases={"CONTRACT": "awaiting_phase_closeout"}, current_phase="CONTRACT",
        phase_owned_dirty=True, phase_owned_dirty_paths=("owned_a.py",),
        dirty_paths=("owned_a.py",),
        closeout_terminal_status="complete", **snapshot_provenance(roadmap),
    )
    status, event = _perform_phase_closeout(
        repo, roadmap, "CONTRACT", snapshot, resolve_profile("execute"),
        action="execute", closeout_mode="commit",
    )

    assert status == "complete", (status, event.blocker)
    assert event.metadata["closeout"]["closeout_action"] == "noop_already_committed"
    # No new commit, and the unrelated file was not committed; its worktree content is
    # preserved (index-isolation unstages it, so it now shows as untracked).
    assert _git(repo, "rev-parse", "HEAD").strip() == head_before
    assert "utils.py" not in _git(repo, "show", "--name-only", "--format=", "HEAD")
    assert "utils.py" in _git(repo, "status", "--short")
    assert (repo / "utils.py").read_text(encoding="utf-8") == "unrelated staged edit\n"


def test_closeout_commits_reviewed_staged_bytes_not_worktree(tmp_path, monkeypatch):
    # CR (reviewed == committed): the closeout commits the STAGED index the governed
    # panel reviewed, not the current working tree. If the worktree for an owned path
    # changes AFTER staging/review (a TOCTOU), the reviewed bytes must still be what
    # lands — a path-scoped `git commit -- <path>` would instead re-read the worktree.
    import phase_loop_runtime.runner as runner_mod

    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("owned_a.py",))
    commit_fixture_paths(repo, "add CONTRACT plan", plan)
    (repo / "owned_a.py").write_text("REVIEWED bytes\n", encoding="utf-8")

    def tamper_then_pass(*_args, **_kwargs):
        # Simulate a worktree change landing during the review window.
        (repo / "owned_a.py").write_text("UNREVIEWED tampered bytes\n", encoding="utf-8")
        return None  # no governed block

    monkeypatch.setattr(runner_mod, "_governed_premerge_review", tamper_then_pass)

    snapshot = StateSnapshot(
        timestamp=utc_now(), repo=str(repo), roadmap=str(roadmap),
        phases={"CONTRACT": "awaiting_phase_closeout"}, current_phase="CONTRACT",
        phase_owned_dirty=True, phase_owned_dirty_paths=("owned_a.py",),
        dirty_paths=("owned_a.py",),
        closeout_terminal_status="complete", **snapshot_provenance(roadmap),
    )
    status, _event = _perform_phase_closeout(
        repo, roadmap, "CONTRACT", snapshot, resolve_profile("execute"),
        action="execute", closeout_mode="commit",
    )

    assert status == "complete"
    committed_bytes = _git(repo, "show", "HEAD:owned_a.py")
    assert committed_bytes == "REVIEWED bytes\n"
    assert "UNREVIEWED" not in committed_bytes


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
