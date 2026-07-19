import shutil
import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.classifier import classify_phase
from phase_loop_runtime.events import append_event
from phase_loop_runtime.models import LoopEvent, utc_now
from phase_loop_runtime.provenance import event_provenance
from phase_loop_runtime.reconcile import (
    _closeout_allow_unowned_attested,
    _lane_ir_override,
    reconcile,
)
from phase_loop_runtime.runtime_paths import roadmap_paths_match
from phase_loop_runtime.state import write_state
from phase_loop_test_utils import make_repo, provenanced_event, provenanced_state, write_phase_plan


def _relocated_repos(tda, tdb):
    """Repo A (source of persisted state) and repo B with byte-identical roadmap/plan
    content at a DIFFERENT absolute root. Caller copies A's `.phase-loop/` into B."""
    repo_a = make_repo(Path(tda))
    roadmap_a = repo_a / "specs" / "phase-plans-v1.md"
    write_phase_plan(repo_a, "RUNNER", roadmap_a)
    repo_b = make_repo(Path(tdb))
    roadmap_b = repo_b / "specs" / "phase-plans-v1.md"
    write_phase_plan(repo_b, "RUNNER", roadmap_b)
    return repo_a, roadmap_a, repo_b, roadmap_b


class RoadmapPathsMatchTest(unittest.TestCase):
    # ah#85(C): portable roadmap identity across a relocated repo root.
    def test_identical_absolute_paths_match_not_relocated(self):
        repo = Path("/x/repo")
        roadmap = repo / "specs" / "phase-plans-v1.md"
        self.assertEqual(roadmap_paths_match(str(repo), str(roadmap), repo, roadmap), (True, False))

    def test_relocated_same_relative_path_matches_relocated(self):
        stored_repo = Path("/home/user/code/avatar-client")
        stored_roadmap = stored_repo / "specs" / "phase-plans-v3.md"
        repo = Path("/mnt/workspace/worktrees/avatar-client-x")
        roadmap = repo / "specs" / "phase-plans-v3.md"
        self.assertEqual(roadmap_paths_match(str(stored_repo), str(stored_roadmap), repo, roadmap), (True, True))

    def test_different_relative_roadmap_does_not_match(self):
        stored_repo = Path("/a/repo")
        stored_roadmap = stored_repo / "specs" / "other-roadmap.md"
        repo = Path("/b/repo")
        roadmap = repo / "specs" / "phase-plans-v1.md"
        self.assertEqual(roadmap_paths_match(str(stored_repo), str(stored_roadmap), repo, roadmap), (False, False))

    def test_roadmap_outside_stored_repo_falls_back_to_non_match(self):
        stored_repo = Path("/a/repo")
        stored_roadmap = Path("/elsewhere/phase-plans-v1.md")  # not under stored_repo
        repo = Path("/b/repo")
        roadmap = repo / "specs" / "phase-plans-v1.md"
        self.assertEqual(roadmap_paths_match(str(stored_repo), str(stored_roadmap), repo, roadmap), (False, False))

    def test_empty_or_missing_stored_paths_do_not_match(self):
        repo = Path("/b/repo")
        roadmap = repo / "specs" / "phase-plans-v1.md"
        self.assertEqual(roadmap_paths_match(None, None, repo, roadmap), (False, False))
        self.assertEqual(roadmap_paths_match(str(repo), "", repo, roadmap), (False, False))


class ReconcileRepoRelocationTest(unittest.TestCase):
    def test_reconcile_preserves_status_after_repo_relocation(self):
        # ah#85(C) symptom #5: state written under repo root A, then `.phase-loop/` replayed
        # from a DIFFERENT root B (moved/renamed/copied worktree). The persisted "complete"
        # status must survive (only the snapshot-application path can produce it) and exactly one
        # `repo_relocated` portability warning must be emitted — instead of all-unplanned.
        # Hermetic (reconcile is read-side; no skill bundle needed) and UNMARKED so CI runs it.
        with tempfile.TemporaryDirectory() as tda, tempfile.TemporaryDirectory() as tdb:
            repo_a = make_repo(Path(tda))
            roadmap_a = repo_a / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo_a, "RUNNER", roadmap_a)
            # Persist a completed RUNNER with correct content provenance, absolute A paths.
            write_state(repo_a, provenanced_state(repo_a, roadmap_a, {"RUNNER": "complete"}))

            # Repo B: byte-identical roadmap/plan content (matching SHAs), different absolute root.
            repo_b = make_repo(Path(tdb))
            roadmap_b = repo_b / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo_b, "RUNNER", roadmap_b)
            # Relocate: copy A's `.phase-loop/` (state.json still carries A's absolute paths) into B.
            shutil.copytree(repo_a / ".phase-loop", repo_b / ".phase-loop")

            snapshot = reconcile(repo_b, roadmap_b, read_only=True)

            # Fails on pre-fix main (absolute-equality gate skips the snapshot block → not complete).
            self.assertEqual(snapshot.phases.get("RUNNER"), "complete")
            reasons = [w.get("reason") for w in snapshot.ledger_warnings]
            self.assertIn("repo_relocated", reasons)
            self.assertEqual(reasons.count("repo_relocated"), 1)

    def test_same_root_reconcile_emits_no_relocation_warning(self):
        # Guard against a false-positive relocation warning on the normal same-root path.
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            write_state(repo, provenanced_state(repo, roadmap, {"RUNNER": "complete"}))

            snapshot = reconcile(repo, roadmap, read_only=True)

            self.assertEqual(snapshot.phases.get("RUNNER"), "complete")
            self.assertNotIn("repo_relocated", [w.get("reason") for w in snapshot.ledger_warnings])

    def test_events_only_relocation_emits_single_warning(self):
        # ah#85(C) round-2: an events-only reconcile (no state.json in the copied `.phase-loop/`)
        # must still emit exactly one `repo_relocated` warning and apply the relocated status.
        with tempfile.TemporaryDirectory() as tda, tempfile.TemporaryDirectory() as tdb:
            repo_a, roadmap_a, repo_b, roadmap_b = _relocated_repos(tda, tdb)
            append_event(repo_a, provenanced_event(repo_a, roadmap_a, "RUNNER", "complete", action="execute"))
            # Copy ONLY events (no state.json was written) to root B.
            shutil.copytree(repo_a / ".phase-loop", repo_b / ".phase-loop")

            snapshot = reconcile(repo_b, roadmap_b, read_only=True)

            self.assertEqual(snapshot.phases.get("RUNNER"), "complete")
            reasons = [w.get("reason") for w in snapshot.ledger_warnings]
            self.assertEqual(reasons.count("repo_relocated"), 1)


class ClassifierRelocationTest(unittest.TestCase):
    def test_classify_phase_preserves_status_after_relocation(self):
        # ah#85(C) round-2: classify_phase's own roadmap gate must be portable too (the reconcile
        # test does not exercise classify_phase). Fails if classifier.py's gate is abs-only.
        with tempfile.TemporaryDirectory() as tda, tempfile.TemporaryDirectory() as tdb:
            repo_a, roadmap_a, repo_b, roadmap_b = _relocated_repos(tda, tdb)
            write_state(repo_a, provenanced_state(repo_a, roadmap_a, {"RUNNER": "complete"}))
            shutil.copytree(repo_a / ".phase-loop", repo_b / ".phase-loop")

            self.assertEqual(classify_phase(repo_b, roadmap_b, "RUNNER"), "complete")

    def test_classify_phase_drifted_phase_content_not_trusted_after_relocation(self):
        # Negative: relocation must NOT override the content-SHA backstop. If the relocated repo's
        # RUNNER phase content differs (phase_sha drift), the persisted `complete` is not trusted.
        with tempfile.TemporaryDirectory() as tda, tempfile.TemporaryDirectory() as tdb:
            repo_a, roadmap_a, repo_b, roadmap_b = _relocated_repos(tda, tdb)
            write_state(repo_a, provenanced_state(repo_a, roadmap_a, {"RUNNER": "complete"}))
            shutil.copytree(repo_a / ".phase-loop", repo_b / ".phase-loop")
            # Drift RUNNER's phase content at B (alias still parses; phase_sha256 changes).
            roadmap_b.write_text(
                roadmap_b.read_text().replace("Runner (RUNNER)", "Runner Rewired (RUNNER)")
            )

            self.assertNotEqual(classify_phase(repo_b, roadmap_b, "RUNNER"), "complete")


class BreakglassRelocationTest(unittest.TestCase):
    def _attestation_event(self, repo, roadmap, phase, reason="owner sign-off in #123"):
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
            metadata={"runner.closeout_allow_unowned_invoked": {"plan_path": None, "operator_reason": reason}},
            **event_provenance(roadmap, phase),
        )

    _ROADMAP_TEXT = (
        "# Roadmap\n\n"
        "### Phase 0 — Contract (CONTRACT)\n\n"
        "### Phase 1 — Access (ACCESS)\n\n"
        "### Phase 2 — Runner (RUNNER)\n"
    )

    def _lane_ir_event(self, repo, roadmap, phase):
        return LoopEvent(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phase=phase,
            action="lane_ir_override",
            status="planned",
            model="operator",
            reasoning_effort="manual",
            source="cli",
            override_reason="owner sign-off in #123",
            metadata={
                "runner.lane_ir_override_invoked": {
                    "plan_path": None,
                    "operator_reason": "owner sign-off in #123",
                    "diagnostic_kinds_overridden": ["unowned_file"],
                }
            },
            **event_provenance(roadmap, phase),
        )

    def test_breakglass_attestation_does_not_relocate(self):
        # ah#85(C) round-2: operator SL-2 attestations are bound to the repo root they were granted
        # in; a relocated `.phase-loop/` must NOT transfer them (fail-closed to the original path).
        with tempfile.TemporaryDirectory() as tda, tempfile.TemporaryDirectory() as tdb:
            repo_a, roadmap_a, repo_b, roadmap_b = _relocated_repos(tda, tdb)
            append_event(repo_a, self._attestation_event(repo_a, roadmap_a, "RUNNER"))

            # Control: honored at the original root.
            self.assertTrue(_closeout_allow_unowned_attested(repo_a, roadmap_a, "RUNNER"))
            # Relocated: NOT honored (fail-closed), even though roadmap content is identical.
            shutil.copytree(repo_a / ".phase-loop", repo_b / ".phase-loop")
            self.assertFalse(_closeout_allow_unowned_attested(repo_b, roadmap_b, "RUNNER"))

    def test_closeout_allow_unowned_shared_external_roadmap_fails_closed_across_roots(self):
        # ah#85(C) round-3 (codex): the gate binds to the repo ROOT, not just the roadmap path.
        # With a SHARED EXTERNAL roadmap (identical absolute path for two repos), an attestation
        # granted under root A must NOT be honored under root B. Isolates the repo-binding: the
        # roadmap path is byte-identical across both scenarios, only the granting repo differs.
        with tempfile.TemporaryDirectory() as tdext, tempfile.TemporaryDirectory() as tdb, tempfile.TemporaryDirectory() as tdc, tempfile.TemporaryDirectory() as tda:
            external_roadmap = Path(tdext) / "shared-roadmap.md"
            external_roadmap.write_text(self._ROADMAP_TEXT)
            other_root = Path(tda)

            repo_b = make_repo(Path(tdb))
            append_event(repo_b, self._attestation_event(other_root, external_roadmap, "RUNNER"))
            # Attestation granted under `other_root` but checked from repo_b → fail closed.
            self.assertFalse(_closeout_allow_unowned_attested(repo_b, external_roadmap, "RUNNER"))

            # Control: same event granted under repo_c's OWN root (same external roadmap) IS honored.
            repo_c = make_repo(Path(tdc))
            append_event(repo_c, self._attestation_event(repo_c, external_roadmap, "RUNNER"))
            self.assertTrue(_closeout_allow_unowned_attested(repo_c, external_roadmap, "RUNNER"))

    def test_lane_ir_override_shared_external_roadmap_fails_closed_across_roots(self):
        # ah#85(C) round-3 (codex): same repo-root binding for the second SL-2 gate.
        with tempfile.TemporaryDirectory() as tdext, tempfile.TemporaryDirectory() as tdb, tempfile.TemporaryDirectory() as tdc, tempfile.TemporaryDirectory() as tda:
            external_roadmap = Path(tdext) / "shared-roadmap.md"
            external_roadmap.write_text(self._ROADMAP_TEXT)
            other_root = Path(tda)

            repo_b = make_repo(Path(tdb))
            plan_b = repo_b / "plans" / "phase-plan-v1-RUNNER.md"
            append_event(repo_b, self._lane_ir_event(other_root, external_roadmap, "RUNNER"))
            # Granted under `other_root`, checked from repo_b → no override kinds (fail closed).
            self.assertEqual(_lane_ir_override(repo_b, external_roadmap, "RUNNER", plan_b), ())

            # Control: granted under repo_c's own root → override kinds honored.
            repo_c = make_repo(Path(tdc))
            plan_c = repo_c / "plans" / "phase-plan-v1-RUNNER.md"
            append_event(repo_c, self._lane_ir_event(repo_c, external_roadmap, "RUNNER"))
            self.assertEqual(_lane_ir_override(repo_c, external_roadmap, "RUNNER", plan_c), ("unowned_file",))


if __name__ == "__main__":
    unittest.main()
