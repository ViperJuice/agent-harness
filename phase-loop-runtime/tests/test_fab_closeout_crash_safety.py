"""FAB (Consiliency/agent-harness#191) piece 2 — closeout crash-safety via
OBJECT-GATING (CR round 6). Instead of `git commit` advancing HEAD and then
gating, a FAB-on closeout creates the candidate commit as a git OBJECT
(`git commit-tree`, no ref/index/worktree change), runs the dedicated hard gate
against it, and advances the branch ref ONLY on a PASS/decline — never on a
hard-gate BLOCK or crash. The ref advances IFF the gate passed, so a crash before
the atomic `update-ref` leaves HEAD unchanged and the candidate an UNREFERENCED
object; a retry re-reviews clean, no orphaned reachable commit. The whole
post-commit crash-safety machinery (pending record / cleared marker / phase-scope
anchor / noop re-gate) is gone.

Deliberately UNMARKED so CI runs it. Uses REAL git repos + a real bare origin;
the panel is injected only at the real spawn boundary.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

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
    write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("pkg/mod.py",))
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "add plan"], check=True)
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
    """Real governed path, no CLI spawn, disjoint pool; point the FAB honesty
    gate's fetch at the local bare remote."""
    monkeypatch.setattr(
        pi, "_default_spawn_via_provider", lambda leg, artifact, **kw: ("OK", "Reviewed.\n\nAGREE")
    )
    monkeypatch.setattr(R, "available_panel_legs", lambda *a, **k: ("codex", "gemini"))
    monkeypatch.setattr(R, "_phase_author_vendors", lambda *a, **k: ("claude",))
    orig = R._fab_closeout_producer

    def patched(repo, **kw):
        kw.setdefault("origin", "fetchsrc")
        return orig(repo, **kw)

    monkeypatch.setattr(R, "_fab_closeout_producer", patched)


def _force_fab_block(monkeypatch):
    """Force the dedicated hard gate to BLOCK (provenance still written) regardless
    of equivalence — the deterministic stand-in for 'the gate did not pass'."""
    def blocked(*a, **kw):
        artifact = fp.read_provenance(kw["repo"], kw["run_id"])
        return fp.GateStatus(
            reviewed_sha=artifact.candidate.head_sha,
            status=fp.GATE_STATUS_BLOCK,
            equivalence_verified=fp.EquivalenceVerified(result="INVALIDATED", reason="forced-block-for-test"),
        )

    monkeypatch.setattr(prod, "compose_gate_status", blocked)


def _closeout(repo, roadmap):
    return R._perform_phase_closeout(
        repo, roadmap, "CONTRACT", _snapshot(repo, roadmap),
        resolve_profile("execute"), action="execute", closeout_mode="commit", run_mode="governed",
    )


def _stage_change(repo):
    (repo / "pkg").mkdir(exist_ok=True)
    (repo / "pkg" / "mod.py").write_text("VALUE = 1\n")


# --------------------------------------------------------------------------- #
# Object-gating: the ref advances IFF the gate passed.
# --------------------------------------------------------------------------- #


def test_pass_advances_ref_to_exact_gated_sha(tmp_path, monkeypatch):
    """A FAB-on closeout that PASSES advances the branch ref to the EXACT gated
    candidate SHA, and provenance is keyed to that same SHA (gated == published)."""
    monkeypatch.setenv("PHASE_LOOP_FAB", "1")
    repo, roadmap = _setup_repo(tmp_path)
    _install_governed_panel(monkeypatch)
    _stage_change(repo)
    base = _git(repo, "rev-parse", "HEAD")

    status, event = _closeout(repo, roadmap)
    assert status == "complete", event.metadata.get("closeout", {})
    head = _git(repo, "rev-parse", "HEAD")
    assert head != base, "the ref advanced"
    run_id = f"fab-{_git(repo, 'rev-parse', 'HEAD^{tree}')}"
    assert event.metadata["closeout"].get("fab_run_id") == run_id
    artifact = fp.read_provenance(repo, run_id)
    assert artifact.candidate.head_sha == head, "provenance is keyed to the exact published SHA"
    # The owned path is committed (clean vs HEAD); the untracked `.phase-loop/`
    # run store is expected and excluded from git in real runs.
    assert _git(repo, "status", "--short", "--", "pkg/mod.py") == "", "owned path committed after the gated advance"
    assert "pkg/mod.py" in _git(repo, "show", "--name-only", "--format=", "HEAD")


def test_block_does_not_advance_ref(tmp_path, monkeypatch):
    """A FAB hard-gate BLOCK must NOT advance the ref — HEAD unchanged, phase
    blocked, and the candidate never becomes reachable."""
    monkeypatch.setenv("PHASE_LOOP_FAB", "1")
    repo, roadmap = _setup_repo(tmp_path)
    _install_governed_panel(monkeypatch)
    _force_fab_block(monkeypatch)
    _stage_change(repo)
    base = _git(repo, "rev-parse", "HEAD")

    status, event = _closeout(repo, roadmap)
    assert status == "blocked", f"a FAB hard-gate block must not complete, got {status}"
    assert _git(repo, "rev-parse", "HEAD") == base, "the ref was NOT advanced on a block"
    assert event.metadata["closeout"].get("closeout_action") == "review_gate_block"


def test_block_then_retry_never_completes_ungated(tmp_path, monkeypatch):
    """After a block, HEAD is unchanged and the owned paths are NOT committed, so a
    retry re-reviews from scratch (never noop_already_committed) — there is no
    orphaned reachable commit to finalize un-gated. The whole class the prior 5
    rounds chased is dissolved."""
    monkeypatch.setenv("PHASE_LOOP_FAB", "1")
    repo, roadmap = _setup_repo(tmp_path)
    _install_governed_panel(monkeypatch)
    _force_fab_block(monkeypatch)
    _stage_change(repo)
    base = _git(repo, "rev-parse", "HEAD")

    status1, _ = _closeout(repo, roadmap)
    assert status1 == "blocked"
    # Retry (the block condition persists): still blocked, still no commit, and it
    # is NEVER a noop_already_committed completion.
    status2, event2 = _closeout(repo, roadmap)
    assert status2 != "complete", f"retry must not complete an un-gated commit, got {status2}"
    assert event2.metadata["closeout"].get("closeout_action") != "noop_already_committed"
    assert _git(repo, "rev-parse", "HEAD") == base, "no commit ever reached the branch"


def test_crash_before_ref_advance_leaves_head_unchanged(tmp_path, monkeypatch):
    """Gate PASSES but the atomic ref-advance fails (a stand-in for a crash between
    the durable provenance write and update-ref): HEAD is unchanged, so the phase
    re-runs clean — nothing un-gated is reachable."""
    monkeypatch.setenv("PHASE_LOOP_FAB", "1")
    repo, roadmap = _setup_repo(tmp_path)
    _install_governed_panel(monkeypatch)
    monkeypatch.setattr(R, "_fab_advance_ref", lambda *a, **k: False)  # simulate the crash window
    _stage_change(repo)
    base = _git(repo, "rev-parse", "HEAD")

    status, _ = _closeout(repo, roadmap)
    assert status != "complete", "a failed ref-advance must not complete"
    assert _git(repo, "rev-parse", "HEAD") == base, "HEAD unchanged — no orphaned reachable commit"


def test_gated_head_noop_retry_completes(tmp_path, monkeypatch):
    """A PASS advances HEAD to the gated SHA; a later re-dispatch finds the owned
    paths already committed → noop_already_committed → complete, with NO re-gate
    (the ref only advanced because it was gated)."""
    monkeypatch.setenv("PHASE_LOOP_FAB", "1")
    repo, roadmap = _setup_repo(tmp_path)
    _install_governed_panel(monkeypatch)
    _stage_change(repo)

    status1, _ = _closeout(repo, roadmap)
    assert status1 == "complete"
    head = _git(repo, "rev-parse", "HEAD")
    # Re-dispatch: nothing staged (already committed) → noop path → complete.
    status2, event2 = _closeout(repo, roadmap)
    assert status2 == "complete"
    assert event2.metadata["closeout"].get("closeout_action") == "noop_already_committed"
    assert _git(repo, "rev-parse", "HEAD") == head


def test_flag_on_planned_closeout_does_not_object_gate(tmp_path, monkeypatch):
    """A PLAN-DOC closeout (terminal_status="planned") never captures FAB seats and
    must take the byte-identical non-FAB `git commit` path — the object-gate
    (commit-tree/update-ref) must NOT run for it, even with the flag on."""
    monkeypatch.setenv("PHASE_LOOP_FAB", "1")
    repo, roadmap = _setup_repo(tmp_path)

    def _must_not_run(*a, **k):
        raise AssertionError("object-gate must not run for a planned closeout")

    monkeypatch.setattr(R, "_fab_object_gate_commit", _must_not_run)
    # Make the plan doc dirty — the owned path of a planned closeout.
    plan_rel = "plans/phase-plan-v1-CONTRACT.md"
    (repo / plan_rel).write_text((repo / plan_rel).read_text() + "\n<!-- planned edit -->\n")
    snapshot = StateSnapshot(
        timestamp=utc_now(), repo=str(repo), roadmap=str(roadmap),
        phases={"CONTRACT": "awaiting_phase_closeout"}, current_phase="CONTRACT",
        phase_owned_dirty=True, phase_owned_dirty_paths=(plan_rel,), dirty_paths=(plan_rel,),
        closeout_terminal_status="planned", **snapshot_provenance(roadmap),
    )
    base = _git(repo, "rev-parse", "HEAD")
    status, event = R._perform_phase_closeout(
        repo, roadmap, "CONTRACT", snapshot,
        resolve_profile("execute"), action="execute", closeout_mode="commit", run_mode="governed",
    )
    # No exception ⇒ the object-gate was not invoked. The plan commit landed via
    # the normal path, and no FAB provenance was produced for it.
    assert status == "planned", f"planned closeout should stay planned, got {status}"
    assert _git(repo, "rev-parse", "HEAD") != base and "fab_run_id" not in event.metadata.get("closeout", {})


def test_honesty_decline_falls_back_to_git_commit(tmp_path, monkeypatch):
    """CR round 7 #1: an honest honesty-gate DECLINE (here: the merge-base is
    unresolvable because the fetch remote is broken) must NOT publish the
    commit-tree object — it falls back to the normal `git commit` path (which runs
    hooks/signs) and produces NO FAB provenance for the closeout."""
    monkeypatch.setenv("PHASE_LOOP_FAB", "1")
    repo, roadmap = _setup_repo(tmp_path)
    _install_governed_panel(monkeypatch)
    # Break the honesty gate's merge-base fetch → the producer DECLINES.
    subprocess.run(["git", "-C", str(repo), "remote", "set-url", "fetchsrc", "/nonexistent/origin.git"], check=True)
    _stage_change(repo)
    base = _git(repo, "rev-parse", "HEAD")

    status, event = _closeout(repo, roadmap)
    assert status == "complete", "a declined FAB closeout still commits via the non-FAB git commit path"
    assert _git(repo, "rev-parse", "HEAD") != base, "the commit landed via git commit"
    assert "fab_run_id" not in event.metadata.get("closeout", {}), "a declined closeout produces no FAB provenance"
    run_id = f"fab-{_git(repo, 'rev-parse', 'HEAD^{tree}')}"
    with pytest.raises(fp.ProvenanceNotFound):
        fp.read_provenance(repo, run_id)


def test_retry_after_failed_advance_succeeds(tmp_path, monkeypatch):
    """CR round 7 #2 (the missing half): a failed ref-advance must leave a CLEAN
    state — a retry re-captures idempotently (no conflicting durable seat records)
    and SUCCEEDS. Attempt 1 fails the advance; attempt 2 advances."""
    monkeypatch.setenv("PHASE_LOOP_FAB", "1")
    repo, roadmap = _setup_repo(tmp_path)
    _install_governed_panel(monkeypatch)
    calls = {"n": 0}
    real_advance = R._fab_advance_ref

    def flaky_advance(*a, **k):
        calls["n"] += 1
        return False if calls["n"] == 1 else real_advance(*a, **k)

    monkeypatch.setattr(R, "_fab_advance_ref", flaky_advance)
    _stage_change(repo)
    base = _git(repo, "rev-parse", "HEAD")

    status1, _ = _closeout(repo, roadmap)
    assert status1 != "complete", "attempt 1's failed advance must not complete"
    assert _git(repo, "rev-parse", "HEAD") == base, "attempt 1 left HEAD unchanged"
    # Attempt 2: re-capture must NOT conflict on the durable ledger → advances.
    status2, event2 = _closeout(repo, roadmap)
    assert status2 == "complete", f"the retry must succeed, not permanently block (got {status2})"
    head = _git(repo, "rev-parse", "HEAD")
    assert head != base and event2.metadata["closeout"].get("fab_run_id") == f"fab-{_git(repo, 'rev-parse', 'HEAD^{tree}')}"


def test_pre_commit_hook_declines_to_git_commit(tmp_path, monkeypatch):
    """FAB-on + a configured pre-commit hook → FAB DECLINES to the non-FAB
    `git commit` path (which RUNS the hook, incl. a blocking check-hook), and
    produces NO FAB provenance. Object-gating (commit-tree) must NOT run, since it
    would silently skip the hook."""
    monkeypatch.setenv("PHASE_LOOP_FAB", "1")
    repo, roadmap = _setup_repo(tmp_path)
    _install_governed_panel(monkeypatch)
    # A configured pre-commit hook that leaves a marker so we can prove it ran.
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\ntouch \"$(git rev-parse --show-toplevel)/HOOK_RAN\"\nexit 0\n")
    hook.chmod(0o755)
    # The object-gate must NOT be invoked when a hook is present.
    monkeypatch.setattr(R, "_fab_object_gate_commit", lambda *a, **k: (_ for _ in ()).throw(AssertionError("object-gate must not run when a pre-commit hook is present")))
    _stage_change(repo)

    status, event = _closeout(repo, roadmap)
    assert status == "complete", event.metadata.get("closeout", {})
    assert (repo / "HOOK_RAN").exists(), "the pre-commit hook must have run via the non-FAB git commit path"
    assert "fab_run_id" not in event.metadata.get("closeout", {}), "a declined closeout produces no FAB provenance"
    assert "pkg/mod.py" in _git(repo, "show", "--name-only", "--format=", "HEAD")


def test_control_detection(tmp_path, monkeypatch):
    """`_fab_pre_commit_control_active`: None → False; executable pre-commit hook
    → True; required signing → True; uncertain → fail-closed True."""
    repo, _ = _setup_repo(tmp_path)
    assert R._fab_pre_commit_control_active(repo) is False, "clean repo → object-gating allowed"
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 0\n")
    hook.chmod(0o644)  # NON-executable (git's sample posture) → not a configured hook
    assert R._fab_pre_commit_control_active(repo) is False
    hook.chmod(0o755)  # executable → a real configured hook
    assert R._fab_pre_commit_control_active(repo) is True
    hook.unlink()
    # commit-msg and prepare-commit-msg are ALSO commit-tree-bypassed controls.
    for name in ("commit-msg", "prepare-commit-msg"):
        h = repo / ".git" / "hooks" / name
        h.write_text("#!/bin/sh\nexit 0\n")
        h.chmod(0o755)
        assert R._fab_pre_commit_control_active(repo) is True, f"{name} hook must decline"
        h.unlink()
    # Signing: explicit-false → allow; true → decline; MALFORMED → fail-closed.
    subprocess.run(["git", "-C", str(repo), "config", "commit.gpgsign", "false"], check=True)
    assert R._fab_pre_commit_control_active(repo) is False
    subprocess.run(["git", "-C", str(repo), "config", "commit.gpgsign", "true"], check=True)
    assert R._fab_pre_commit_control_active(repo) is True, "required signing → decline (commit-tree can't sign)"
    subprocess.run(["git", "-C", str(repo), "config", "commit.gpgsign", "maybe"], check=True)
    assert R._fab_pre_commit_control_active(repo) is True, "malformed gpgsign → fail-closed decline"


def test_valueless_gpgsign_declines(tmp_path, monkeypatch):
    """CR round 8 / codex#6: a VALUELESS `[commit] gpgsign` key (valid git syntax
    meaning TRUE — `git config --get` returns empty for it) must be read as
    required signing → FAB declines. Raw-string parsing would fail open here."""
    repo, _ = _setup_repo(tmp_path)
    assert R._fab_pre_commit_control_active(repo) is False
    # Append a valueless key directly to .git/config.
    cfg = repo / ".git" / "config"
    cfg.write_text(cfg.read_text() + "[commit]\n\tgpgsign\n")
    assert _git(repo, "config", "--type=bool", "--get", "commit.gpgsign") == "true"  # git reads it as true
    assert R._fab_pre_commit_control_active(repo) is True, "valueless gpgsign → required signing → decline"


def test_durability_fsyncs_run_dir_and_parent(tmp_path, monkeypatch):
    """CR round 8 / codex#5: the durability sweep must fsync not just the files but
    the run directory AND its parent (else a crash can lose the run dir's own
    directory entry while preserving the advanced ref)."""
    monkeypatch.setenv("PHASE_LOOP_FAB", "1")
    repo, roadmap = _setup_repo(tmp_path)
    _install_governed_panel(monkeypatch)
    _stage_change(repo)
    status, event = _closeout(repo, roadmap)
    assert status == "complete"
    run_id = event.metadata["closeout"]["fab_run_id"]
    run_dir = fp.provenance_dir_for_run(repo, run_id)
    fsynced: list = []
    monkeypatch.setattr(fp, "_fsync_path", lambda path, flags: fsynced.append(str(path)))
    fp.fsync_run_store_durable(repo, run_id)
    assert str(run_dir) in fsynced, "the run directory itself must be fsynced"
    assert str(run_dir.parent) in fsynced, "the run dir's PARENT (runs root) must be fsynced (codex#5)"
    # Material snapshot files are covered by the file sweep.
    assert any(fp.MATERIAL_SNAPSHOT_DIRNAME in p for p in fsynced), "material snapshot files fsynced"


def test_concurrent_switch_records_gated_sha_not_ambient_head(tmp_path, monkeypatch):
    """CR round 8 / codex#4: if HEAD is switched away from the gated branch during
    the advance seam, the closeout must RECORD the GATED candidate SHA (the ref it
    advanced), never ambient HEAD."""
    monkeypatch.setenv("PHASE_LOOP_FAB", "1")
    repo, roadmap = _setup_repo(tmp_path)
    _install_governed_panel(monkeypatch)
    base = _git(repo, "rev-parse", "HEAD")
    branch_ref = "refs/heads/" + _git(repo, "symbolic-ref", "--short", "HEAD")
    real_advance = R._fab_advance_ref

    def advance_then_detach(repo_, new_sha, *, ref, expected_old):
        ok = real_advance(repo_, new_sha, ref=ref, expected_old=expected_old)
        # Simulate a concurrent branch switch mid-closeout: detach HEAD to base.
        subprocess.run(["git", "-C", str(repo_), "checkout", "-q", "--detach", expected_old], check=True)
        return ok

    monkeypatch.setattr(R, "_fab_advance_ref", advance_then_detach)
    _stage_change(repo)

    status, event = _closeout(repo, roadmap)
    assert status == "complete"
    recorded = event.metadata["closeout"]["closeout_commit"]
    assert recorded != base, "must not record the ambient (detached) HEAD base"
    assert recorded == _git(repo, "rev-parse", branch_ref), "recorded SHA is the gated candidate on the advanced branch"


def test_flag_off_is_byte_neutral(tmp_path, monkeypatch):
    """Byte-neutral: with PHASE_LOOP_FAB OFF, the closeout uses the normal
    `git commit` path and completes; a resume hits the unchanged noop path."""
    monkeypatch.delenv("PHASE_LOOP_FAB", raising=False)
    repo, roadmap = _setup_repo(tmp_path)
    _stage_change(repo)

    status1, _ = R._perform_phase_closeout(
        repo, roadmap, "CONTRACT", _snapshot(repo, roadmap),
        resolve_profile("execute"), action="execute", closeout_mode="commit",
    )
    assert status1 == "complete"
    committed = _git(repo, "rev-parse", "HEAD")
    status2, event2 = R._perform_phase_closeout(
        repo, roadmap, "CONTRACT", _snapshot(repo, roadmap),
        resolve_profile("execute"), action="execute", closeout_mode="commit",
    )
    assert status2 == "complete"
    assert event2.metadata["closeout"]["closeout_action"] == "noop_already_committed"
    assert _git(repo, "rev-parse", "HEAD") == committed


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
