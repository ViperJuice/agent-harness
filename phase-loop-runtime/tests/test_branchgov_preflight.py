from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.events import read_events
from phase_loop_runtime.launcher import AuthPreflightResult, LaunchResult
from phase_loop_runtime.pipeline_adapter.branch_ops import (
    REFUSE_ROADMAP_ORPHAN_PREFIX,
    roadmap_orphaned_by_branchgov,
)
from phase_loop_runtime.runner import run_loop
from phase_loop_test_utils import commit_fixture_paths, make_repo, write_phase_plan


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )


def _fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
    closeout = {
        "terminal_status": "complete",
        "verification_status": "passed",
        "dirty_paths": [],
        "produced_if_gates": ["IF-0-EVENT-1"],
        "next_action": None,
        "blocker_class": None,
        "blocker_summary": None,
        "human_required": None,
        "required_human_inputs": [],
    }
    return LaunchResult(command=spec.command, returncode=0, output=json.dumps(closeout), executor=spec.executor)


def _make_orphan_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Repro of issue #83: origin/main does NOT carry the roadmap; the roadmap is
    committed only on the operator's own (non-convention) feature branch.

    We pin origin/main to a base commit that PREDATES the roadmap, then commit the
    roadmap (and its phase plan) on a feature branch. The roadmap is reachable from
    the feature-branch HEAD but NOT from origin/main, so a convention-branch switch
    cut from origin/main would orphan it — the exact FileNotFoundError repro."""
    repo = make_repo(tmp_path)
    # Pin origin/main to the empty fixture base (before the roadmap exists on a
    # ref the switch would cut from). make_repo already wrote a roadmap, so reset
    # it out of the base by recommitting a base that removes it.
    roadmap = repo / "specs" / "phase-plans-v1.md"
    roadmap.unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base without roadmap")
    _git(repo, "branch", "-M", "main")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    # Operator works on their own non-convention branch and authors the roadmap
    # there (unpushed / ahead of origin/main).
    _git(repo, "checkout", "-q", "-b", "consiliency/ci/v1-restructure")
    roadmap.parent.mkdir(parents=True, exist_ok=True)
    roadmap.write_text(
        "# Roadmap\n\n"
        "### Phase 0 — Contract (CONTRACT)\n\n"
        "### Phase 1 — Access (ACCESS)\n\n"
        "### Phase 2 — Runner (RUNNER)\n"
    )
    plan = write_phase_plan(
        repo,
        "RUNNER",
        roadmap,
        body=(
            "# RUNNER\n\n## Lanes\n\n### SL-0 - Runner\n"
            "- **Owned files**: none\n- **Interfaces provided**: `IF-0-EVENT-1`\n"
        ),
    )
    commit_fixture_paths(repo, "add roadmap + runner plan (feature-branch only)", roadmap, plan)
    return repo, roadmap


def _make_governed_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Genuinely governed run: the roadmap is committed and origin/main carries
    it, so the convention-branch switch keeps it visible (behaviour-preserving)."""
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(
        repo,
        "RUNNER",
        roadmap,
        body=(
            "# RUNNER\n\n## Lanes\n\n### SL-0 - Runner\n"
            "- **Owned files**: none\n- **Interfaces provided**: `IF-0-EVENT-1`\n"
        ),
    )
    commit_fixture_paths(repo, "add runner plan", plan)
    # origin/main carries the roadmap (pushed/on-base) → no orphan.
    _git(repo, "branch", "-M", "main")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    _git(repo, "checkout", "-q", "-b", "consiliency/ci/v1-restructure")
    return repo, roadmap


# --------------------------------------------------------------------------- #
# Predicate-level unit tests (branch_ops.roadmap_orphaned_by_branchgov)
# --------------------------------------------------------------------------- #


def test_predicate_flags_standalone_orphan(tmp_path, monkeypatch):
    repo, roadmap = _make_orphan_repo(tmp_path)
    monkeypatch.setenv("PHASE_LOOP_BRANCHGOV_ENABLE", "true")
    monkeypatch.setenv("PHASE_LOOP_PIPELINE_MODE", "true")

    summary = roadmap_orphaned_by_branchgov(repo, roadmap, "v1", "main")

    assert summary is not None
    assert summary.startswith(REFUSE_ROADMAP_ORPHAN_PREFIX)
    assert "--allow-branchgov" in summary


def test_predicate_safe_for_governed_roadmap(tmp_path, monkeypatch):
    repo, roadmap = _make_governed_repo(tmp_path)
    monkeypatch.setenv("PHASE_LOOP_BRANCHGOV_ENABLE", "true")
    monkeypatch.setenv("PHASE_LOOP_PIPELINE_MODE", "true")

    assert roadmap_orphaned_by_branchgov(repo, roadmap, "v1", "main") is None


def test_helper_inert_without_pipeline_mode(tmp_path, monkeypatch):
    # Branchgov-active gating lives at the CALLER (the runner helper) now, not in
    # the predicate (CR #5 removed the redundant self-recheck). Not in
    # pipeline-mode → the helper must short-circuit and never refuse.
    from phase_loop_runtime.runner import _branchgov_orphan_blocker_before_dispatch

    repo, roadmap = _make_orphan_repo(tmp_path)
    monkeypatch.setenv("PHASE_LOOP_BRANCHGOV_ENABLE", "true")
    monkeypatch.delenv("PHASE_LOOP_PIPELINE_MODE", raising=False)

    assert _branchgov_orphan_blocker_before_dispatch(repo, roadmap) is None


# --------------------------------------------------------------------------- #
# Runner-level tests (the clean blocker vs. the crash)
# --------------------------------------------------------------------------- #


def test_runner_refuses_standalone_orphan_with_clean_blocker(tmp_path, monkeypatch):
    # Issue #83: without the guard the runner would switch + crash FileNotFoundError.
    # With the guard (env UNSET default → branchgov active, NOT explicitly overridden)
    # it emits a clean branch_sync_conflict blocker, human_required True.
    repo, roadmap = _make_orphan_repo(tmp_path)
    monkeypatch.delenv("PHASE_LOOP_BRANCHGOV_ENABLE", raising=False)  # default-on, NOT explicit
    monkeypatch.setenv("PHASE_LOOP_PIPELINE_MODE", "true")

    with (
        patch("phase_loop_runtime.runner.run_auth_preflight", return_value=AuthPreflightResult(ok=True, metadata={})),
        patch("phase_loop_runtime.runner.launch_with_spec", side_effect=_fake_launch) as launched,
    ):
        run_loop(repo, roadmap, phase="RUNNER")

    # The executor must NOT have been launched (we refused before dispatch).
    assert not launched.called, "expected refusal BEFORE launching the executor"

    events = read_events(repo)
    blocked = [e for e in events if e.get("status") == "blocked"]
    assert blocked, "expected a blocked closeout, not a crash"
    blocker = blocked[-1].get("blocker", {})
    assert blocker.get("blocker_class") == "branch_sync_conflict"
    assert blocker.get("human_required") is True
    assert REFUSE_ROADMAP_ORPHAN_PREFIX in (blocker.get("blocker_summary") or "")

    # The working tree must NOT have switched away from the operator's branch.
    current = _git(repo, "branch", "--show-current").stdout.strip()
    assert current == "consiliency/ci/v1-restructure"
    # The roadmap is still present (not orphaned).
    assert roadmap.is_file()

    switched = [e for e in events if e.get("action") == "coordinator.branch_switched"]
    assert not switched, "no switch should occur when we refuse"


def test_runner_helper_refuses_orphan_without_override(tmp_path, monkeypatch):
    # Claim A (no override): the runner helper that consults the override returns
    # a clean branch_sync_conflict blocker (human_required True) on a genuine
    # orphan when branchgov is default-on but NOT explicitly opted into.
    from phase_loop_runtime.runner import _branchgov_orphan_blocker_before_dispatch

    repo, roadmap = _make_orphan_repo(tmp_path)
    monkeypatch.delenv("PHASE_LOOP_BRANCHGOV_ENABLE", raising=False)  # default-on, NOT explicit
    monkeypatch.setenv("PHASE_LOOP_PIPELINE_MODE", "true")

    blocker = _branchgov_orphan_blocker_before_dispatch(repo, roadmap)

    assert blocker is not None
    assert blocker["blocker_class"] == "branch_sync_conflict"
    assert blocker["human_required"] is True
    assert REFUSE_ROADMAP_ORPHAN_PREFIX in blocker["blocker_summary"]


def test_runner_helper_override_bypasses_refusal(tmp_path, monkeypatch):
    # Claim A (override): --allow-branchgov / explicit PHASE_LOOP_BRANCHGOV_ENABLE=true
    # makes the SAME orphan stop refusing — the helper returns None so the existing
    # switch + #44 branch_switched event proceed (the switch + event preservation
    # itself is covered by test_phase_loop_branch_divergence_event.py and the
    # governed run below; here we isolate the override discriminator).
    from phase_loop_runtime.runner import _branchgov_orphan_blocker_before_dispatch

    repo, roadmap = _make_orphan_repo(tmp_path)
    monkeypatch.setenv("PHASE_LOOP_BRANCHGOV_ENABLE", "true")  # explicit opt-in
    monkeypatch.setenv("PHASE_LOOP_PIPELINE_MODE", "true")

    assert _branchgov_orphan_blocker_before_dispatch(repo, roadmap) is None


def test_runner_governed_roadmap_unaffected(tmp_path, monkeypatch):
    # Behaviour preservation: a pushed/on-base roadmap still switches with #44's
    # event and no refusal — even under the default-on (env unset) posture.
    repo, roadmap = _make_governed_repo(tmp_path)
    monkeypatch.delenv("PHASE_LOOP_BRANCHGOV_ENABLE", raising=False)  # default-on
    monkeypatch.setenv("PHASE_LOOP_PIPELINE_MODE", "true")

    with (
        patch("phase_loop_runtime.runner.run_auth_preflight", return_value=AuthPreflightResult(ok=True, metadata={})),
        patch("phase_loop_runtime.runner.launch_with_spec", side_effect=_fake_launch),
    ):
        run_loop(repo, roadmap, phase="RUNNER")

    events = read_events(repo)
    switched = [e for e in events if e.get("action") == "coordinator.branch_switched"]
    assert switched, "governed run must still switch + emit branch_switched"

    orphan_blocked = [
        e
        for e in events
        if REFUSE_ROADMAP_ORPHAN_PREFIX in ((e.get("blocker") or {}).get("blocker_summary") or "")
    ]
    assert not orphan_blocked, "governed run must NOT be refused"

    current = _git(repo, "branch", "--show-current").stdout.strip()
    assert current == "consiliency/pipeline/v1"


# --------------------------------------------------------------------------- #
# Blocker #1 (CR): the preflight must evaluate the FETCHED base — a stale local
# origin/main must not false-positive-refuse a genuinely-pushed roadmap.
# --------------------------------------------------------------------------- #


def _make_pushed_roadmap_with_stale_origin(tmp_path: Path) -> tuple[Path, Path]:
    """A real `origin` remote that HAS the roadmap on main, but the local
    `refs/remotes/origin/main` is REWOUND behind it (stale). Pre-fetch the
    predicate would wrongly think the roadmap isn't on the base; after the
    runner's fetch it sees the pushed roadmap and stays safe."""
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", "-q", str(remote))

    repo = make_repo(tmp_path)  # has specs/phase-plans-v1.md in the base commit
    roadmap = repo / "specs" / "phase-plans-v1.md"
    _git(repo, "branch", "-M", "main")
    _git(repo, "remote", "add", "origin", str(remote))

    # 1) Push a BASE that predates the roadmap (remove it, commit, push) and
    #    capture that base SHA — this is what the local remote-tracking ref will
    #    be rewound to (stale).
    roadmap.unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base without roadmap")
    _git(repo, "push", "-q", "origin", "main")
    stale_base = _git(repo, "rev-parse", "HEAD").stdout.strip()

    # 2) Author + commit the roadmap (and plan) and PUSH them to the real remote
    #    main — the roadmap IS genuinely on origin/main now.
    roadmap.parent.mkdir(parents=True, exist_ok=True)
    roadmap.write_text(
        "# Roadmap\n\n"
        "### Phase 0 — Contract (CONTRACT)\n\n"
        "### Phase 1 — Access (ACCESS)\n\n"
        "### Phase 2 — Runner (RUNNER)\n"
    )
    plan = write_phase_plan(
        repo,
        "RUNNER",
        roadmap,
        body=(
            "# RUNNER\n\n## Lanes\n\n### SL-0 - Runner\n"
            "- **Owned files**: none\n- **Interfaces provided**: `IF-0-EVENT-1`\n"
        ),
    )
    commit_fixture_paths(repo, "add roadmap + runner plan", roadmap, plan)
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")

    # 3) REWIND the LOCAL remote-tracking ref to the pre-roadmap base so it is
    #    stale: pre-fetch the roadmap looks orphaned; a real fetch sees it pushed.
    _git(repo, "update-ref", "refs/remotes/origin/main", stale_base)
    # Operator works on their own non-convention branch (HEAD has the roadmap).
    _git(repo, "checkout", "-q", "-b", "consiliency/ci/v1-restructure")
    return repo, roadmap


def test_predicate_false_positive_without_fetch_then_safe_after(tmp_path, monkeypatch):
    repo, roadmap = _make_pushed_roadmap_with_stale_origin(tmp_path)
    monkeypatch.setenv("PHASE_LOOP_BRANCHGOV_ENABLE", "true")
    monkeypatch.setenv("PHASE_LOOP_PIPELINE_MODE", "true")

    # Pre-fetch (base_already_fetched=True forces evaluation of the STALE local
    # ref): the predicate FALSE-POSITIVES — the roadmap looks orphaned.
    stale = roadmap_orphaned_by_branchgov(
        repo, roadmap, "v1", "main", base_already_fetched=True
    )
    assert stale is not None, "stale local origin/main should look orphaned pre-fetch"

    # After fetching (the default path the runner takes), the predicate sees the
    # pushed roadmap on the real base and is SAFE — no false-positive refusal.
    safe = roadmap_orphaned_by_branchgov(repo, roadmap, "v1", "main")
    assert safe is None, "after fetch the pushed roadmap must not be refused"


def test_runner_pushed_roadmap_with_stale_origin_switches_no_refusal(tmp_path, monkeypatch):
    # End-to-end: the runner fetches once, sees the pushed roadmap, switches with
    # #44's event, and never false-positive-refuses.
    repo, roadmap = _make_pushed_roadmap_with_stale_origin(tmp_path)
    monkeypatch.delenv("PHASE_LOOP_BRANCHGOV_ENABLE", raising=False)  # default-on, NOT explicit
    monkeypatch.setenv("PHASE_LOOP_PIPELINE_MODE", "true")

    with (
        patch("phase_loop_runtime.runner.run_auth_preflight", return_value=AuthPreflightResult(ok=True, metadata={})),
        patch("phase_loop_runtime.runner.launch_with_spec", side_effect=_fake_launch),
    ):
        run_loop(repo, roadmap, phase="RUNNER")

    events = read_events(repo)
    orphan_blocked = [
        e
        for e in events
        if REFUSE_ROADMAP_ORPHAN_PREFIX in ((e.get("blocker") or {}).get("blocker_summary") or "")
    ]
    assert not orphan_blocked, "pushed roadmap (stale local ref) must not be refused after fetch"
    switched = [e for e in events if e.get("action") == "coordinator.branch_switched"]
    assert switched, "pushed roadmap must still switch + emit branch_switched"
    assert _git(repo, "branch", "--show-current").stdout.strip() == "consiliency/pipeline/v1"


# --------------------------------------------------------------------------- #
# Blocker #3 (CR): --allow-branchgov over a GENUINE orphan must fail cleanly
# (post-switch branch_sync_conflict), NOT crash FileNotFoundError.
# --------------------------------------------------------------------------- #


def test_runner_override_on_genuine_orphan_fails_cleanly_not_crash(tmp_path, monkeypatch):
    from phase_loop_runtime.runner import ROADMAP_ORPHAN_AFTER_SWITCH_PREFIX

    repo, roadmap = _make_orphan_repo(tmp_path)
    monkeypatch.setenv("PHASE_LOOP_BRANCHGOV_ENABLE", "true")  # explicit opt-in (the escape hatch)
    monkeypatch.setenv("PHASE_LOOP_PIPELINE_MODE", "true")

    with (
        patch("phase_loop_runtime.runner.run_auth_preflight", return_value=AuthPreflightResult(ok=True, metadata={})),
        patch("phase_loop_runtime.runner.launch_with_spec", side_effect=_fake_launch) as launched,
    ):
        # Must NOT raise FileNotFoundError (the original #83 crash).
        run_loop(repo, roadmap, phase="RUNNER")

    # The override switched (so #44's branch_switched event was still emitted)...
    events = read_events(repo)
    assert [e for e in events if e.get("action") == "coordinator.branch_switched"]
    # ...the roadmap genuinely vanished on the convention branch, so the guard
    # RESTORED the working tree to the operator's original branch (no stranding,
    # roadmap visible again).
    assert _git(repo, "branch", "--show-current").stdout.strip() == "consiliency/ci/v1-restructure"
    assert roadmap.is_file()
    # ...but the run then failed CLEANLY with a post-switch branch_sync_conflict,
    # not a crash, and the executor was never launched into a missing roadmap.
    assert not launched.called, "executor must not launch after the roadmap vanished"
    blocked = [
        e
        for e in events
        if (e.get("blocker") or {}).get("blocker_class") == "branch_sync_conflict"
        and ROADMAP_ORPHAN_AFTER_SWITCH_PREFIX in ((e.get("blocker") or {}).get("blocker_summary") or "")
    ]
    assert blocked, "expected a clean post-switch branch_sync_conflict blocker"
    assert blocked[-1]["blocker"]["human_required"] is True
