"""FAB (Consiliency/agent-harness#191) piece 2 — closeout CRASH-SAFETY across
retries (CR round-2 blocker). A FAB hard-block (or a post-commit producer
exception) leaves the commit in place; on a SUBSEQUENT closeout attempt the
`noop_already_committed` branch must NOT finalize that un-gated commit as
complete — it must RE-GATE the exact committed tree and only complete on an
affirmative FAB pass, else stay blocked (the merge-time re-gate is inert in
piece 2, so this is the only backstop).

Deliberately UNMARKED so CI runs it. Uses REAL git repos + a real bare origin;
the panel is injected only at the real spawn boundary.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from phase_loop_runtime import fab_gate as fg
from phase_loop_runtime import fab_producer as prod
from phase_loop_runtime import fab_provenance as fp
from phase_loop_runtime import panel_invoker as pi
from phase_loop_runtime import runner as R
from phase_loop_runtime.models import StateSnapshot, utc_now
from phase_loop_runtime.profiles import resolve_profile
from phase_loop_runtime.provenance import snapshot_provenance
from phase_loop_test_utils import make_repo, write_phase_plan


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _setup_repo(tmp_path: Path):
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("pkg/mod.py",))
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "add plan"], check=True)
    # Two-remote convention: origin github-shaped (identity), fetchsrc bare (fetch).
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    base = _git(repo, "rev-parse", "HEAD")
    for args in (
        ("remote", "add", "origin", "git@github.com:testorg/testrepo.git"),
        ("remote", "add", "fetchsrc", str(origin)),
        ("push", "-q", "fetchsrc", "HEAD:refs/heads/main"),
        ("update-ref", "refs/remotes/origin/main", base),
        ("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"),
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True)
    return repo, roadmap


def _snapshot(repo: Path, roadmap: Path) -> StateSnapshot:
    return StateSnapshot(
        timestamp=utc_now(),
        repo=str(repo),
        roadmap=str(roadmap),
        phases={"CONTRACT": "awaiting_phase_closeout"},
        current_phase="CONTRACT",
        phase_owned_dirty=True,
        phase_owned_dirty_paths=("pkg/mod.py",),
        dirty_paths=("pkg/mod.py",),
        closeout_terminal_status="complete",
        **snapshot_provenance(roadmap),
    )


def _install_governed_panel(monkeypatch):
    """Real governed path, no CLI spawn, disjoint pool."""
    monkeypatch.setattr(
        pi, "_default_spawn_via_provider", lambda leg, artifact, **kw: ("OK", "Reviewed.\n\nAGREE")
    )
    monkeypatch.setattr(R, "available_panel_legs", lambda *a, **k: ("codex", "gemini"))
    monkeypatch.setattr(R, "_phase_author_vendors", lambda *a, **k: ("claude",))
    # The producer fetches from "origin" in production; point it at the bare remote.
    orig = R._fab_closeout_producer

    def patched(repo, **kw):
        kw.setdefault("origin", "fetchsrc")
        return orig(repo, **kw)

    monkeypatch.setattr(R, "_fab_closeout_producer", patched)


def _force_fab_block(monkeypatch):
    """Force the FAB hard gate to BLOCK (provenance still written) regardless of
    equivalence — the deterministic stand-in for 'the gate did not pass'."""
    def blocked(*a, **kw):
        run_id = kw["run_id"]
        artifact = fp.read_provenance(kw["repo"], run_id)
        return fp.GateStatus(
            reviewed_sha=artifact.candidate.head_sha,
            status=fp.GATE_STATUS_BLOCK,
            equivalence_verified=fp.EquivalenceVerified(result="INVALIDATED", reason="forced-block-for-test"),
        )

    monkeypatch.setattr(prod, "compose_gate_status", blocked)


def test_blocked_fab_commit_is_not_noop_completed_on_retry(tmp_path, monkeypatch):
    """Attempt 1 hard-blocks (FAB gate) but the commit is made; attempt 2 (resume,
    nothing staged) must NOT finalize it as noop_already_committed — it must
    re-gate and stay blocked."""
    monkeypatch.setenv("PHASE_LOOP_FAB", "1")
    repo, roadmap = _setup_repo(tmp_path)
    _install_governed_panel(monkeypatch)
    _force_fab_block(monkeypatch)

    (repo / "pkg").mkdir()
    (repo / "pkg" / "mod.py").write_text("VALUE = 1\n")
    head_before = _git(repo, "rev-parse", "HEAD")

    # Attempt 1: governed review passes, commit made, FAB hard gate BLOCKS.
    status1, event1 = R._perform_phase_closeout(
        repo, roadmap, "CONTRACT", _snapshot(repo, roadmap),
        resolve_profile("execute"), action="execute", closeout_mode="commit", run_mode="governed",
    )
    assert status1 == "blocked", f"attempt 1 must block on the FAB hard gate, got {status1}"
    committed = _git(repo, "rev-parse", "HEAD")
    assert committed != head_before, "attempt 1 made the commit before blocking"
    # The blocked commit's tree keys the FAB run; NO cleared marker was written.
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    run_id = f"fab-{tree}"
    assert prod.is_fab_scoped(repo, run_id), "attempt 1 was FAB-scoped (capture ran)"
    assert not prod.is_closeout_cleared(repo, run_id), "a blocked closeout must NOT be cleared"

    # Attempt 2 (resume): the file is already committed → nothing staged → the
    # noop_already_committed branch. It MUST re-gate and stay blocked, never
    # finalize the un-gated commit as complete.
    status2, event2 = R._perform_phase_closeout(
        repo, roadmap, "CONTRACT", _snapshot(repo, roadmap),
        resolve_profile("execute"), action="execute", closeout_mode="commit", run_mode="governed",
    )
    assert status2 != "complete", (
        "RETRY FAIL-OPEN: a FAB-blocked commit was finalized as complete without re-gating "
        f"(status={status2}, action={event2.metadata.get('closeout', {}).get('closeout_action')})"
    )
    assert _git(repo, "rev-parse", "HEAD") == committed, "no new commit on the blocked retry"


def test_crashed_producer_is_not_noop_completed_on_retry(tmp_path, monkeypatch):
    """A post-commit producer EXCEPTION (crash between commit and gate-pass) must
    also leave a fail-closed state: attempt 2 re-gates, and while the crash
    condition persists it stays blocked (never noop-completes)."""
    monkeypatch.setenv("PHASE_LOOP_FAB", "1")
    repo, roadmap = _setup_repo(tmp_path)
    _install_governed_panel(monkeypatch)

    def boom(*a, **kw):
        raise RuntimeError("simulated crash after commit, before gate-pass")

    monkeypatch.setattr(prod, "compose_gate_status", boom)

    (repo / "pkg").mkdir()
    (repo / "pkg" / "mod.py").write_text("VALUE = 1\n")

    status1, _ = R._perform_phase_closeout(
        repo, roadmap, "CONTRACT", _snapshot(repo, roadmap),
        resolve_profile("execute"), action="execute", closeout_mode="commit", run_mode="governed",
    )
    assert status1 == "blocked", f"a post-commit crash must fail closed, got {status1}"
    committed = _git(repo, "rev-parse", "HEAD")
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    assert not prod.is_closeout_cleared(repo, f"fab-{tree}")

    status2, event2 = R._perform_phase_closeout(
        repo, roadmap, "CONTRACT", _snapshot(repo, roadmap),
        resolve_profile("execute"), action="execute", closeout_mode="commit", run_mode="governed",
    )
    assert status2 != "complete", "a crashed FAB commit must not noop-complete on retry"
    assert _git(repo, "rev-parse", "HEAD") == committed


def test_flag_off_noop_retry_is_byte_neutral(tmp_path, monkeypatch):
    """Byte-neutral: with PHASE_LOOP_FAB OFF, a resume that hits the noop branch
    completes exactly as before — no re-gate, no FAB import reached."""
    monkeypatch.delenv("PHASE_LOOP_FAB", raising=False)
    repo, roadmap = _setup_repo(tmp_path)

    (repo / "pkg").mkdir()
    (repo / "pkg" / "mod.py").write_text("VALUE = 1\n")
    # Attempt 1 (autonomous, flag off): plain commit.
    status1, _ = R._perform_phase_closeout(
        repo, roadmap, "CONTRACT", _snapshot(repo, roadmap),
        resolve_profile("execute"), action="execute", closeout_mode="commit",
    )
    assert status1 == "complete"
    committed = _git(repo, "rev-parse", "HEAD")
    # Attempt 2 (resume, nothing staged): noop_already_committed → complete.
    status2, event2 = R._perform_phase_closeout(
        repo, roadmap, "CONTRACT", _snapshot(repo, roadmap),
        resolve_profile("execute"), action="execute", closeout_mode="commit",
    )
    assert status2 == "complete"
    assert event2.metadata["closeout"]["closeout_action"] == "noop_already_committed"
    assert _git(repo, "rev-parse", "HEAD") == committed


# --------------------------------------------------------------------------- #
# Decision-table unit tests for the noop re-gate helper (the subtle cells).
# --------------------------------------------------------------------------- #


def _capture_and_commit(repo: Path) -> str:
    """Capture a FAB review (real panel legs) + commit a staged change, WITHOUT
    writing the cleared marker — the state a blocked/crashed attempt-1 leaves."""
    from phase_loop_runtime.governed_bundle import staged_index_diff
    from phase_loop_runtime.panel_invoker import PanelLegResult, PanelResult

    (repo / "pkg").mkdir(exist_ok=True)
    (repo / "pkg" / "mod.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "pkg/mod.py"], check=True)
    tree = _git(repo, "write-tree")
    run_id = f"fab-{tree}"
    panel = PanelResult(legs=(
        PanelLegResult(leg="codex", status="OK", text="Reviewed.\n\nAGREE", seat_key="codex:x:high"),
        PanelLegResult(leg="gemini", status="OK", text="Reviewed.\n\nAGREE", seat_key="gemini:y:high"),
    ))
    prod.capture_review_at_invocation(
        repo, run_id, panel, epoch=1, reviewed_bundle_text="bundle",
        reviewed_diff_text=staged_index_diff(repo, ["pkg/mod.py"]), closeout_dirty_paths=("pkg/mod.py",),
    )
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "c1"], check=True)
    return run_id


def test_noop_regate_not_fab_scoped_is_safe(tmp_path, monkeypatch):
    """A plain (non-FAB-captured) commit → the re-gate helper returns None (safe
    to complete)."""
    monkeypatch.setenv("PHASE_LOOP_FAB", "1")
    repo, _ = _setup_repo(tmp_path)
    (repo / "pkg").mkdir()
    (repo / "pkg" / "mod.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "pkg/mod.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "plain"], check=True)
    assert R._fab_noop_regate_block(repo, committed_head=_git(repo, "rev-parse", "HEAD"), metadata={"closeout": {}}) is None


def test_noop_regate_cleared_marker_short_circuits(tmp_path, monkeypatch):
    """FAB-scoped + a cleared marker → None WITHOUT re-gating (trust the marker;
    do not false-block a legitimately-passed commit on resume)."""
    monkeypatch.setenv("PHASE_LOOP_FAB", "1")
    repo, _ = _setup_repo(tmp_path)
    run_id = _capture_and_commit(repo)
    prod.mark_closeout_cleared(repo, run_id)
    # Even if the re-gate WOULD crash, the marker short-circuits it.
    monkeypatch.setattr(prod, "compose_gate_status", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("must not be called")))
    assert R._fab_noop_regate_block(repo, committed_head=_git(repo, "rev-parse", "HEAD"), metadata={"closeout": {}}) is None


def test_noop_regate_decline_fails_closed(tmp_path, monkeypatch):
    """THE SUBTLE CELL: FAB-scoped + NO marker + re-gate DECLINES (e.g. the
    merge-base can't be resolved offline) → BLOCK, never complete. A decline is
    "cannot establish FAB honesty now", which for a known-not-cleared commit is
    fail-closed territory, not the non-FAB fallback."""
    monkeypatch.setenv("PHASE_LOOP_FAB", "1")
    repo, _ = _setup_repo(tmp_path)
    run_id = _capture_and_commit(repo)
    assert not prod.is_closeout_cleared(repo, run_id)
    # Break origin fetch so the honesty gate's merge-base is unresolvable →
    # finalize_and_gate DECLINES (wrote_provenance=False), never an affirmative pass.
    subprocess.run(["git", "-C", str(repo), "remote", "set-url", "fetchsrc", "/nonexistent/origin.git"], check=True)
    monkeypatch.setattr(R, "_fab_closeout_producer", lambda repo, **kw: (
        prod.finalize_and_gate(repo, kw["fab_run_id"], epoch=1, reviewed_base_sha=kw["reviewed_base_sha"],
                               reviewed_tree=kw["reviewed_tree"], committed_head_sha=kw["committed_head"],
                               closeout_dirty_paths=kw["closeout_dirty_paths"], base_ref_name="main", origin="fetchsrc")
    ))
    meta = {"closeout": {}}
    result = R._fab_noop_regate_block(repo, committed_head=_git(repo, "rev-parse", "HEAD"), metadata=meta)
    assert result is not None, "a re-gate DECLINE on a known-not-cleared commit must fail closed, not complete"
    assert result[0] == "blocked"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
