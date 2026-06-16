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


PARTIAL_PLAN = """# PARTIAL

## Lanes

### SL-0 - Owned
- **Owned files**: `owned_a.py`, `owned_b.py`
- **Interfaces provided**: a
- **Interfaces consumed**: none
"""


def test_closeout_partial_classify_commits_owned_subset_and_blocks_on_unowned_remainder(tmp_path):
    # OWNFIX #36-item1: reproduced from the real ai-stack-v2 INVENTORY run, where the
    # executor emitted empty phase_owned_dirty_paths and one of N dirty paths
    # (a test the plan under-enumerated) was unowned. The old all-or-nothing fallback
    # blocked ALL verified-owned paths. The fix: auto-classify and commit the owned
    # subset, then surface the genuinely-unowned remainder via closeout_scope_violation
    # (human_required) so an autonomous loop stops cleanly instead of stranding work.
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "PARTIAL", roadmap, body=PARTIAL_PLAN)
    commit_fixture_paths(repo, "add PARTIAL plan", plan)
    (repo / "owned_a.py").write_text("a\n", encoding="utf-8")
    (repo / "owned_b.py").write_text("b\n", encoding="utf-8")
    (repo / "stray_test.py").write_text("not declared by the plan\n", encoding="utf-8")
    head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()

    snapshot = StateSnapshot(
        timestamp=utc_now(),
        repo=str(repo),
        roadmap=str(roadmap),
        phases={"PARTIAL": "awaiting_phase_closeout"},
        current_phase="PARTIAL",
        phase_owned_dirty=False,
        phase_owned_dirty_paths=(),
        dirty_paths=("owned_a.py", "owned_b.py", "stray_test.py"),
        closeout_terminal_status="complete",
        **snapshot_provenance(roadmap),
    )

    status, event = _perform_phase_closeout(
        repo, roadmap, "PARTIAL", snapshot, resolve_profile("execute"),
        action="execute", closeout_mode="commit",
    )

    # Owned subset was committed (verified work preserved, no manual intervention).
    head_after = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert head_after != head_before, "expected the owned subset to be committed"
    assert event.metadata["closeout"].get("closeout_dirty_paths_autoclassified") == ["owned_a.py", "owned_b.py"]
    committed = _git(repo, "show", "--stat", "--name-only", "--format=", "HEAD").stdout
    assert "owned_a.py" in committed and "owned_b.py" in committed
    assert "stray_test.py" not in committed
    # The genuinely-unowned remainder is surfaced loudly, not stranded.
    assert status == "blocked"
    assert event.blocker["blocker_class"] == "closeout_scope_violation"
    assert event.blocker["human_required"] is True
    assert "stray_test.py" in event.blocker["blocker_summary"]
    assert event.metadata["closeout"]["unowned_dirty_paths"] == ["stray_test.py"]
    # Verification passed; the block is scope, not verification.
    assert event.metadata["closeout"]["verification_status"] == "passed"
    # stray_test.py is still dirty (left for the operator to declare / break-glass).
    assert "stray_test.py" in _git(repo, "status", "--short").stdout


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
