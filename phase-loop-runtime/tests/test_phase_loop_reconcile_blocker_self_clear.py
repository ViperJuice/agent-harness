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
from phase_loop_runtime.reconcile import (
    _clean_verified_dirty_closeout_recovery_supersedes_blocker,
    reconcile,
)
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


class CloseoutAllowUnownedAttestationTest(unittest.TestCase):
    """BREAKGLASS SL-2 (reconcile parity): a recorded ``closeout_allow_unowned``
    attestation lifts the ``unowned_dirty_paths`` bail in
    ``_clean_verified_dirty_closeout_recovery_supersedes_blocker`` (reconcile.py
    ~812) — the NON-human verified-dirty-closeout-recovery path. The live
    human-required ``closeout_scope_violation`` is broken through by SL-1's
    rerun, not by reconcile. Secrets are never break-glassable, and a stale
    attestation (sha-drifted / wrong phase / empty reason) does not authorize.
    """

    def _recovery_event(self, repo, roadmap, phase, *, unowned_dirty_paths, provenance=None):
        return LoopEvent(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phase=phase,
            action="execute",
            status="blocked",
            model="gpt-5.4",
            reasoning_effort="medium",
            source="fixture",
            metadata={
                "completion_dirty_worktree": {
                    "reason": "verified_dirty_closeout_recovery",
                    "terminal_status": "complete",
                    "dirty_paths": ["owned.txt"],
                    "phase_owned_dirty_paths": ["owned.txt"],
                    "unowned_dirty_paths": list(unowned_dirty_paths),
                    "pre_existing_dirty_paths": [],
                    "phase_owned_dirty": True,
                },
                "closeout": {
                    "closeout_mode": "commit",
                    "closeout_action": "manual",
                    "verification_status": "passed",
                    "closeout_commit": "abc123",
                },
                "terminal_summary": {
                    "terminal_status": "complete",
                    "terminal_blocker": None,
                    "verification_status": "passed",
                    "dirty_paths": ["owned.txt"],
                    "phase_owned_dirty": True,
                    "phase_owned_dirty_paths": ["owned.txt"],
                    "unowned_dirty_paths": list(unowned_dirty_paths),
                    "pre_existing_dirty_paths": [],
                },
            },
            **(provenance or event_provenance(roadmap, phase)),
        )

    def _attestation_event(self, repo, roadmap, phase, *, reason="owner sign-off in #123", provenance=None):
        return LoopEvent(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phase=phase,
            action="closeout_allow_unowned",
            status="planned",
            model="operator",
            reasoning_effort="manual",
            source="cli",
            override_reason=reason,
            metadata={
                "runner.closeout_allow_unowned_invoked": {
                    "plan_path": None,
                    "operator_reason": reason,
                }
            },
            **(provenance or event_provenance(roadmap, phase)),
        )

    def _shas(self, roadmap, phase):
        prov = event_provenance(roadmap, phase)
        return prov["roadmap_sha256"], {phase: prov["phase_sha256"]}

    def test_attestation_lifts_unowned_bail(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("README.md",))
            commit_fixture_paths(repo, "add CONTRACT plan", plan)
            append_event(repo, self._recovery_event(repo, roadmap, "CONTRACT", unowned_dirty_paths=["rogue.py"]))
            roadmap_sha, phase_sha = self._shas(roadmap, "CONTRACT")

            # Without an attestation, the bail fires (no recovery).
            self.assertIsNone(
                _clean_verified_dirty_closeout_recovery_supersedes_blocker(
                    repo, roadmap, "CONTRACT", roadmap_sha, phase_sha
                )
            )

            # With a matching attestation, the bail is lifted and recovery proceeds.
            append_event(repo, self._attestation_event(repo, roadmap, "CONTRACT"))
            recovered = _clean_verified_dirty_closeout_recovery_supersedes_blocker(
                repo, roadmap, "CONTRACT", roadmap_sha, phase_sha
            )
            self.assertIsNotNone(recovered)
            self.assertEqual(recovered["closeout_summary"]["closeout_commit"], "abc123")

    def test_secrets_never_recover_even_with_attestation(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("README.md",))
            commit_fixture_paths(repo, "add CONTRACT plan", plan)
            append_event(repo, self._recovery_event(repo, roadmap, "CONTRACT", unowned_dirty_paths=[".env"]))
            append_event(repo, self._attestation_event(repo, roadmap, "CONTRACT"))
            roadmap_sha, phase_sha = self._shas(roadmap, "CONTRACT")

            # secrets are never break-glassable; the attestation does not recover.
            self.assertIsNone(
                _clean_verified_dirty_closeout_recovery_supersedes_blocker(
                    repo, roadmap, "CONTRACT", roadmap_sha, phase_sha
                )
            )

    def test_stale_attestation_does_not_authorize(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("README.md",))
            commit_fixture_paths(repo, "add CONTRACT plan", plan)
            append_event(repo, self._recovery_event(repo, roadmap, "CONTRACT", unowned_dirty_paths=["rogue.py"]))
            roadmap_sha, phase_sha = self._shas(roadmap, "CONTRACT")

            # (a) empty operator reason -> no authorization.
            append_event(repo, self._attestation_event(repo, roadmap, "CONTRACT", reason="   "))
            self.assertIsNone(
                _clean_verified_dirty_closeout_recovery_supersedes_blocker(
                    repo, roadmap, "CONTRACT", roadmap_sha, phase_sha
                )
            )

            # (b) sha-drifted attestation (content changed since attestation) -> stale,
            # does not authorize a later closeout.
            stale_prov = {**event_provenance(roadmap, "CONTRACT"), "phase_sha256": "stale" + "0" * 60}
            append_event(
                repo,
                self._attestation_event(repo, roadmap, "CONTRACT", provenance=stale_prov),
            )
            self.assertIsNone(
                _clean_verified_dirty_closeout_recovery_supersedes_blocker(
                    repo, roadmap, "CONTRACT", roadmap_sha, phase_sha
                )
            )

    def test_attestation_recovers_end_to_end_via_reconcile(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("README.md",))
            commit_fixture_paths(repo, "add CONTRACT plan", plan)
            # A prior NON-human supersedable blocker so the recovery path is reached.
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="CONTRACT",
                    action="execute",
                    status="blocked",
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="fixture",
                    blocker={
                        "human_required": False,
                        "blocker_class": "dirty_worktree_conflict",
                        "blocker_summary": "Phase reported verified dirty closeout but left dirty paths.",
                        "required_human_inputs": (),
                    },
                    **event_provenance(roadmap, "CONTRACT"),
                ),
            )
            append_event(repo, self._recovery_event(repo, roadmap, "CONTRACT", unowned_dirty_paths=["rogue.py"]))
            # Without attestation: stays blocked.
            self.assertEqual(reconcile(repo, roadmap).phases["CONTRACT"], "blocked")
            # With attestation: recovers to complete.
            append_event(repo, self._attestation_event(repo, roadmap, "CONTRACT"))
            snapshot = reconcile(repo, roadmap)
            self.assertEqual(snapshot.phases["CONTRACT"], "complete")
            self.assertIsNone(snapshot.blocker_class)


if __name__ == "__main__":
    unittest.main()
