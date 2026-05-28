"""Reconcile auto-clears cached blockers whose preconditions resolved.

state.json (or the latest event) can hold a cached blocker whose
precondition was already resolved out-of-band -- the operator manually
created the pipeline branch, resolved the merge, etc. Without an active
self-clearing pass, reconcile echoed the stale blocker forever and the
runner refused to advance.

This file covers the regenesis v37 cases:

- ``branch_sync_conflict`` from the BranchGov default-branch refusal
  variant clears when we are on a non-default branch.
- ``branch_sync_conflict`` from that variant does NOT clear when we are
  still on the default branch.
- ``branch_sync_conflict`` from other variants (e.g.
  ``base_ref_unavailable`` in release_guard) does NOT auto-clear.
- ``merge_conflict`` clears when no unmerged paths remain.
- Human-action classes (``admin_approval``, ``missing_secret``,
  ``product_decision_missing``) do NOT auto-clear.
- ``human_required=True`` blockers NEVER auto-clear, regardless of
  class.

``dirty_worktree_conflict`` is intentionally NOT covered here: the
runner emits a richer ``repair_precondition_cleared`` state_transition
for that class on its own. See
``test_phase_loop_repair_skipped_when_blocker_cleared``.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.events import append_event
from phase_loop_runtime.models import LoopEvent, StateSnapshot, utc_now
from phase_loop_runtime.provenance import event_provenance, snapshot_provenance
from phase_loop_runtime.reconcile import reconcile
from phase_loop_runtime.state import write_state
from phase_loop_test_utils import commit_fixture_paths, make_repo, write_phase_plan


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _seed_blocked_snapshot(
    repo: Path,
    roadmap: Path,
    phase: str,
    blocker_class: str,
    *,
    human_required: bool = False,
    blocker_summary: str = "Cached stale blocker for reconcile self-clear test.",
) -> None:
    """Persist state.json + emit a blocked event so reconcile sees a
    cached blocker for the phase.
    """
    snapshot = StateSnapshot(
        timestamp=utc_now(),
        repo=str(repo),
        roadmap=str(roadmap),
        phases={phase.upper(): "blocked"},
        current_phase=phase.upper(),
        last_action="reconcile",
        human_required=human_required,
        blocker_class=blocker_class,
        blocker_summary=blocker_summary,
        required_human_inputs=(),
        access_attempts=(),
        **snapshot_provenance(roadmap),
    )
    write_state(repo, snapshot)
    append_event(
        repo,
        LoopEvent(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phase=phase.upper(),
            action="execute",
            status="blocked",
            model="gpt-5.4",
            reasoning_effort="medium",
            source="fixture",
            blocker={
                "human_required": human_required,
                "blocker_class": blocker_class,
                "blocker_summary": blocker_summary,
                "required_human_inputs": (),
                "access_attempts": (),
            },
            **event_provenance(roadmap, phase),
        ),
    )


_BRANCHGOV_REFUSAL_SUMMARY = (
    "Refusing git commit on default branch main while pipeline branch governance is enabled."
)


class ReconcileBlockerSelfClearTest(unittest.TestCase):
    def test_branchgov_refusal_clears_when_on_pipeline_branch(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("README.md",))
            commit_fixture_paths(repo, "add CONTRACT plan", plan)
            # Pretend "main" is the default branch via origin/HEAD.
            _git(repo, "branch", "-M", "main")
            _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
            _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
            # Move off main onto a pipeline branch -- precondition for the
            # cached blocker (refusing commit on default branch) is gone.
            _git(repo, "checkout", "-b", "pipeline/v1-CONTRACT")
            _seed_blocked_snapshot(
                repo,
                roadmap,
                "CONTRACT",
                "branch_sync_conflict",
                blocker_summary=_BRANCHGOV_REFUSAL_SUMMARY,
            )
            snapshot = reconcile(repo, roadmap)
            self.assertIsNone(snapshot.blocker_class)
            self.assertNotEqual(snapshot.phases["CONTRACT"], "blocked")
            self.assertTrue(
                any(
                    w.get("reason") == "blocker_precondition_self_cleared"
                    for w in snapshot.ledger_warnings
                ),
                f"expected self-clear ledger warning, got {snapshot.ledger_warnings!r}",
            )

    def test_branchgov_refusal_does_not_clear_when_still_on_default(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("README.md",))
            commit_fixture_paths(repo, "add CONTRACT plan", plan)
            _git(repo, "branch", "-M", "main")
            _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
            _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
            _seed_blocked_snapshot(
                repo,
                roadmap,
                "CONTRACT",
                "branch_sync_conflict",
                blocker_summary=_BRANCHGOV_REFUSAL_SUMMARY,
            )
            snapshot = reconcile(repo, roadmap)
            self.assertEqual(snapshot.blocker_class, "branch_sync_conflict")
            self.assertEqual(snapshot.phases["CONTRACT"], "blocked")

    def test_branch_sync_conflict_does_not_clear_for_non_branchgov_variants(self):
        # release_guard emits ``branch_sync_conflict`` with a
        # ``base_ref_unavailable`` reason. That variant must NOT
        # auto-clear -- the precondition (origin base ref missing) is
        # not satisfied by simply being on a non-default branch.
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("README.md",))
            commit_fixture_paths(repo, "add CONTRACT plan", plan)
            _git(repo, "branch", "-M", "main")
            _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
            _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
            _git(repo, "checkout", "-b", "pipeline/v1-CONTRACT")
            _seed_blocked_snapshot(
                repo,
                roadmap,
                "CONTRACT",
                "branch_sync_conflict",
                blocker_summary="release_dispatch base ref origin/main is unavailable.",
            )
            snapshot = reconcile(repo, roadmap)
            self.assertEqual(snapshot.blocker_class, "branch_sync_conflict")
            self.assertEqual(snapshot.phases["CONTRACT"], "blocked")

    def test_merge_conflict_clears_when_no_unmerged_paths(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("README.md",))
            commit_fixture_paths(repo, "add CONTRACT plan", plan)
            _seed_blocked_snapshot(repo, roadmap, "CONTRACT", "merge_conflict")
            # No merge in progress -> no unmerged paths in `git status`.
            snapshot = reconcile(repo, roadmap)
            self.assertIsNone(snapshot.blocker_class)
            self.assertNotEqual(snapshot.phases["CONTRACT"], "blocked")

    def test_human_required_blocker_does_not_self_clear_even_when_clearable_class(self):
        # human_required=True is an operator override that means
        # "do not second-guess this". Even if the class is one we would
        # normally clear (branch_sync_conflict on a pipeline branch),
        # reconcile must leave it cached.
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("README.md",))
            commit_fixture_paths(repo, "add CONTRACT plan", plan)
            _git(repo, "branch", "-M", "main")
            _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
            _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
            _git(repo, "checkout", "-b", "pipeline/v1-CONTRACT")
            _seed_blocked_snapshot(
                repo,
                roadmap,
                "CONTRACT",
                "branch_sync_conflict",
                human_required=True,
                blocker_summary=_BRANCHGOV_REFUSAL_SUMMARY,
            )
            snapshot = reconcile(repo, roadmap)
            self.assertEqual(snapshot.blocker_class, "branch_sync_conflict")
            self.assertTrue(snapshot.human_required)
            self.assertEqual(snapshot.phases["CONTRACT"], "blocked")

    def test_admin_approval_does_not_self_clear(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("README.md",))
            commit_fixture_paths(repo, "add CONTRACT plan", plan)
            _seed_blocked_snapshot(
                repo,
                roadmap,
                "CONTRACT",
                "admin_approval",
                human_required=True,
                blocker_summary="Needs operator approval before proceeding.",
            )
            # Worktree is clean, no merge, etc. -- but admin_approval is a
            # human-action class; reconcile must leave it cached.
            snapshot = reconcile(repo, roadmap)
            self.assertEqual(snapshot.blocker_class, "admin_approval")
            self.assertTrue(snapshot.human_required)
            self.assertEqual(snapshot.phases["CONTRACT"], "blocked")

    def test_missing_secret_does_not_self_clear(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("README.md",))
            commit_fixture_paths(repo, "add CONTRACT plan", plan)
            _seed_blocked_snapshot(
                repo,
                roadmap,
                "CONTRACT",
                "missing_secret",
                human_required=True,
                blocker_summary="Need credentials before proceeding.",
            )
            snapshot = reconcile(repo, roadmap)
            self.assertEqual(snapshot.blocker_class, "missing_secret")
            self.assertEqual(snapshot.phases["CONTRACT"], "blocked")


if __name__ == "__main__":
    unittest.main()
