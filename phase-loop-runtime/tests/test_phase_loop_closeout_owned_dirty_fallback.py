"""Regression tests for the closeout auto-classification fallback.

Regenesis v37 hit a repeating failure where codex's EmitPhaseCloseout
left `phase_owned_dirty_paths` empty even though `dirty_paths` contained
files that plainly matched the active plan's owned-files glob. The
runner blocked closeout with ``missing_phase_owned_dirty_paths`` and the
operator had to manually commit on every phase.

The fix: when the dirty path list is non-empty AND every dirty path is
owned by the active plan, auto-classify them as phase-owned and proceed
to commit. If any dirty path is NOT owned by the plan, the blocker must
still be emitted.
"""

from __future__ import annotations

import subprocess

from phase_loop_runtime.models import StateSnapshot, utc_now
from phase_loop_runtime.profiles import resolve_profile
from phase_loop_runtime.provenance import snapshot_provenance
from phase_loop_runtime.runner import _perform_phase_closeout
from phase_loop_test_utils import commit_fixture_paths, make_repo, write_phase_plan


def _git(repo, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_closeout_autoclassifies_when_phase_owned_dirty_paths_empty_but_dirty_paths_match_plan(tmp_path):
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("README.md",))
    commit_fixture_paths(repo, "add CONTRACT plan", plan)
    (repo / "README.md").write_text("phase output written by execute\n", encoding="utf-8")
    head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()

    # Simulate codex emitting empty phase_owned_dirty_paths despite valid
    # dirty_paths and the file plainly matching README.md owned-glob.
    snapshot = StateSnapshot(
        timestamp=utc_now(),
        repo=str(repo),
        roadmap=str(roadmap),
        phases={"CONTRACT": "awaiting_phase_closeout"},
        current_phase="CONTRACT",
        phase_owned_dirty=False,
        phase_owned_dirty_paths=(),
        dirty_paths=("README.md",),
        closeout_terminal_status="complete",
        **snapshot_provenance(roadmap),
    )

    status, event = _perform_phase_closeout(
        repo,
        roadmap,
        "CONTRACT",
        snapshot,
        resolve_profile("execute"),
        action="execute",
        closeout_mode="commit",
    )

    assert event.blocker is None, f"expected no blocker, got {event.blocker!r}"
    assert status == "complete", f"expected complete, got {status}"
    assert event.metadata["closeout"]["closeout_action"] == "commit"
    head_after = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert head_after != head_before, "expected a new commit"
    # The reclassified paths should be recorded for audit.
    assert event.metadata["closeout"].get("closeout_dirty_paths_autoclassified") == ["README.md"]


def test_closeout_still_blocks_when_dirty_paths_are_not_plan_owned(tmp_path):
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("README.md",))
    commit_fixture_paths(repo, "add CONTRACT plan", plan)
    # Touch a path that the plan does NOT own.
    foreign = repo / "src" / "foreign.txt"
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_text("not owned by CONTRACT\n", encoding="utf-8")
    head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()

    snapshot = StateSnapshot(
        timestamp=utc_now(),
        repo=str(repo),
        roadmap=str(roadmap),
        phases={"CONTRACT": "awaiting_phase_closeout"},
        current_phase="CONTRACT",
        phase_owned_dirty=False,
        phase_owned_dirty_paths=(),
        dirty_paths=("src/foreign.txt",),
        closeout_terminal_status="complete",
        **snapshot_provenance(roadmap),
    )

    status, event = _perform_phase_closeout(
        repo,
        roadmap,
        "CONTRACT",
        snapshot,
        resolve_profile("execute"),
        action="execute",
        closeout_mode="commit",
    )

    assert status == "blocked"
    assert event.blocker is not None
    assert event.blocker["blocker_class"] == "dirty_worktree_conflict"
    assert event.metadata["closeout"]["closeout_refusal_reason"] == "missing_phase_owned_dirty_paths"
    # No commit should have happened.
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == head_before
