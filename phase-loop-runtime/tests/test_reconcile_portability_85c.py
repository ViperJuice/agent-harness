import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from phase_loop_runtime.classifier import classify_phase
from phase_loop_runtime.events import append_event, append_payload
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


class BreakglassEmptyRepoFailClosedTest(unittest.TestCase):
    """ah#238 (fast-follow from #237/ah#85(C) round-3, Fable seat): a BREAKGLASS SL-2
    attestation event with a missing/empty `repo` or `roadmap` field must NOT be honored.
    Pre-fix, `Path(str(event.get("repo", "")))` turns an absent `repo` into `Path("")`, and
    `.resolve()` on that resolves to the CURRENT WORKING DIRECTORY — so an under-specified,
    potentially hand-edited event line would spuriously match whenever reconcile happens to
    run with CWD at the repo root. The fix rejects such events explicitly before the
    `Path(...)` construction, so the block below is unreachable through normal writers
    (`LoopEvent.repo`/`roadmap` are required `str` fields) but the gate must still fail
    closed against a hand-edited/corrupted ledger line, independent of CWD.
    """

    def _raw_attestation_payload(
        self, repo, roadmap, phase, *, event_repo, event_roadmap, reason="owner sign-off in #238", plan_path=None
    ):
        event = LoopEvent(
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
            metadata={"runner.closeout_allow_unowned_invoked": {"plan_path": plan_path, "operator_reason": reason}},
            **event_provenance(roadmap, phase),
        )
        payload = event.to_json()
        # Simulate a hand-edited/corrupted ledger line: `repo`/`roadmap` are blank rather
        # than the (normally-required) real values. `read_events` parses raw JSON, so this
        # bypasses `LoopEvent.__post_init__` entirely, matching the append-only-log-content
        # trust boundary the gate must defend.
        payload["repo"] = event_repo
        payload["roadmap"] = event_roadmap
        return payload

    def _raw_lane_ir_payload(
        self, repo, roadmap, phase, *, event_repo, event_roadmap, reason="owner sign-off in #238", plan_path=None
    ):
        event = LoopEvent(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phase=phase,
            action="lane_ir_override",
            status="planned",
            model="operator",
            reasoning_effort="manual",
            source="cli",
            override_reason=reason,
            metadata={
                "runner.lane_ir_override_invoked": {
                    "plan_path": plan_path,
                    "operator_reason": reason,
                    "diagnostic_kinds_overridden": ["unowned_file"],
                }
            },
            **event_provenance(roadmap, phase),
        )
        payload = event.to_json()
        payload["repo"] = event_repo
        payload["roadmap"] = event_roadmap
        return payload

    def test_closeout_allow_unowned_empty_repo_field_fails_closed_at_repo_root_cwd(self):
        # The exact fail-open shape named in ah#238: `repo` absent/empty, `roadmap` present
        # and CORRECT, CWD == the actual repo root (the common case for reconcile). Pre-fix,
        # `Path("").resolve()` == CWD == repo.resolve() → spurious match.
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            payload = self._raw_attestation_payload(
                repo, roadmap, "RUNNER", event_repo="", event_roadmap=str(roadmap)
            )
            append_payload(repo, payload, roadmap=roadmap)

            cwd = os.getcwd()
            try:
                os.chdir(repo)
                self.assertFalse(_closeout_allow_unowned_attested(repo, roadmap, "RUNNER"))
            finally:
                os.chdir(cwd)

    def test_closeout_allow_unowned_missing_roadmap_field_fails_closed(self):
        # Symmetric case: `roadmap` absent/empty, `repo` present and correct.
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            payload = self._raw_attestation_payload(
                repo, roadmap, "RUNNER", event_repo=str(repo), event_roadmap=""
            )
            append_payload(repo, payload, roadmap=roadmap)

            cwd = os.getcwd()
            try:
                os.chdir(repo)
                self.assertFalse(_closeout_allow_unowned_attested(repo, roadmap, "RUNNER"))
            finally:
                os.chdir(cwd)

    def test_closeout_allow_unowned_empty_repo_field_fails_closed_regardless_of_cwd(self):
        # CWD-independence: fails closed even from a THIRD, unrelated directory (not the
        # repo root, not empty-string-adjacent). Guards against a fix that only special-cases
        # the repo-root CWD instead of rejecting the malformed event outright.
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as other:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            payload = self._raw_attestation_payload(
                repo, roadmap, "RUNNER", event_repo="", event_roadmap=str(roadmap)
            )
            append_payload(repo, payload, roadmap=roadmap)

            cwd = os.getcwd()
            try:
                os.chdir(other)
                self.assertFalse(_closeout_allow_unowned_attested(repo, roadmap, "RUNNER"))
            finally:
                os.chdir(cwd)

    def test_lane_ir_override_empty_repo_field_fails_closed_at_repo_root_cwd(self):
        # Mirrors the closeout_allow_unowned case for the second BREAKGLASS SL-2 gate.
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            plan = repo / "plans" / "phase-plan-v1-RUNNER.md"
            payload = self._raw_lane_ir_payload(
                repo, roadmap, "RUNNER", event_repo="", event_roadmap=str(roadmap)
            )
            append_payload(repo, payload, roadmap=roadmap)

            cwd = os.getcwd()
            try:
                os.chdir(repo)
                self.assertEqual(_lane_ir_override(repo, roadmap, "RUNNER", plan), ())
            finally:
                os.chdir(cwd)

    def test_lane_ir_override_empty_roadmap_field_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            plan = repo / "plans" / "phase-plan-v1-RUNNER.md"
            payload = self._raw_lane_ir_payload(
                repo, roadmap, "RUNNER", event_repo=str(repo), event_roadmap=""
            )
            append_payload(repo, payload, roadmap=roadmap)

            cwd = os.getcwd()
            try:
                os.chdir(repo)
                self.assertEqual(_lane_ir_override(repo, roadmap, "RUNNER", plan), ())
            finally:
                os.chdir(cwd)

    # --- codex CR follow-up (ah#238): non-empty RELATIVE repo/roadmap fields ---
    #
    # The falsy-only guard (`not event.get("repo")`) rejects an absent/empty field but lets a
    # non-empty RELATIVE path through unchanged. `Path(".").resolve()` is exactly as
    # CWD-dependent as `Path("").resolve()` — both resolve against the current working
    # directory rather than the site the attestation was actually granted at. codex's
    # read-only probe confirmed both gates fail OPEN on `repo="."` at the exact branch HEAD
    # that introduced the falsy-only guard. These tests pin the fail-closed fix: any event
    # whose `repo` or `roadmap` is not an ABSOLUTE path string must be rejected, independent
    # of the falsy check.

    def test_closeout_allow_unowned_relative_repo_dot_fails_closed_at_repo_root_cwd(self):
        # The exact shape codex's probe used: repo="." (non-empty, relative), roadmap relative
        # too, CWD == the actual repo root. Pre-fix, Path(".").resolve() == CWD == repo.resolve()
        # → spurious match (codex observed True here on branch HEAD).
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            relative_roadmap = str(roadmap.relative_to(repo))
            payload = self._raw_attestation_payload(
                repo, roadmap, "RUNNER", event_repo=".", event_roadmap=relative_roadmap
            )
            append_payload(repo, payload, roadmap=roadmap)

            cwd = os.getcwd()
            try:
                os.chdir(repo)
                self.assertFalse(_closeout_allow_unowned_attested(repo, roadmap, "RUNNER"))
            finally:
                os.chdir(cwd)

    def test_closeout_allow_unowned_relative_roadmap_with_absolute_repo_fails_closed(self):
        # Symmetric case: repo is absolute and correct, but roadmap is a RELATIVE path that
        # happens to resolve (CWD-dependently) to the correct absolute roadmap when CWD == repo
        # root. Must still fail closed regardless of which field carries the relative path.
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            relative_roadmap = str(roadmap.relative_to(repo))
            payload = self._raw_attestation_payload(
                repo, roadmap, "RUNNER", event_repo=str(repo), event_roadmap=relative_roadmap
            )
            append_payload(repo, payload, roadmap=roadmap)

            cwd = os.getcwd()
            try:
                os.chdir(repo)
                self.assertFalse(_closeout_allow_unowned_attested(repo, roadmap, "RUNNER"))
            finally:
                os.chdir(cwd)

    def test_lane_ir_override_relative_repo_dot_fails_closed_at_repo_root_cwd(self):
        # Mirrors the closeout_allow_unowned relative-`repo="."` case for the second BREAKGLASS
        # SL-2 gate. codex's probe observed `('unowned_file',)` (a live override) here pre-fix.
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            plan = repo / "plans" / "phase-plan-v1-RUNNER.md"
            relative_roadmap = str(roadmap.relative_to(repo))
            payload = self._raw_lane_ir_payload(
                repo, roadmap, "RUNNER", event_repo=".", event_roadmap=relative_roadmap
            )
            append_payload(repo, payload, roadmap=roadmap)

            cwd = os.getcwd()
            try:
                os.chdir(repo)
                self.assertEqual(_lane_ir_override(repo, roadmap, "RUNNER", plan), ())
            finally:
                os.chdir(cwd)

    def test_lane_ir_override_relative_roadmap_with_absolute_repo_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            plan = repo / "plans" / "phase-plan-v1-RUNNER.md"
            relative_roadmap = str(roadmap.relative_to(repo))
            payload = self._raw_lane_ir_payload(
                repo, roadmap, "RUNNER", event_repo=str(repo), event_roadmap=relative_roadmap
            )
            append_payload(repo, payload, roadmap=roadmap)

            cwd = os.getcwd()
            try:
                os.chdir(repo)
                self.assertEqual(_lane_ir_override(repo, roadmap, "RUNNER", plan), ())
            finally:
                os.chdir(cwd)

    # --- codex CR follow-up round 3 (ah#238): expanduser() applied BEFORE the absoluteness
    # check let a RELATIVE "~/repo" path expand to an absolute path under the current $HOME
    # and spuriously pass, and let "~nonexistent-user/..." raise an uncaught RuntimeError
    # (the expanduser() call sat outside the try/except). The fix checks is_absolute() on the
    # RAW string with no expanduser() at all. These tests pin: (1) a relative "repo" alone
    # (with a correct absolute roadmap) fails closed, proving repo-rejection independently of
    # roadmap; (2) a tilde "repo" is rejected as relative, not silently honored via $HOME;
    # (3) a tilde path for a nonexistent user fails closed WITHOUT crashing reconciliation.

    def test_closeout_allow_unowned_relative_repo_dot_with_absolute_roadmap_fails_closed(self):
        # Isolates repo-rejection: roadmap is absolute AND correct, only `repo` is relative.
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            payload = self._raw_attestation_payload(
                repo, roadmap, "RUNNER", event_repo=".", event_roadmap=str(roadmap)
            )
            append_payload(repo, payload, roadmap=roadmap)

            cwd = os.getcwd()
            try:
                os.chdir(repo)
                self.assertFalse(_closeout_allow_unowned_attested(repo, roadmap, "RUNNER"))
            finally:
                os.chdir(cwd)

    def test_lane_ir_override_relative_repo_dot_with_absolute_roadmap_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            plan = repo / "plans" / "phase-plan-v1-RUNNER.md"
            payload = self._raw_lane_ir_payload(
                repo, roadmap, "RUNNER", event_repo=".", event_roadmap=str(roadmap)
            )
            append_payload(repo, payload, roadmap=roadmap)

            cwd = os.getcwd()
            try:
                os.chdir(repo)
                self.assertEqual(_lane_ir_override(repo, roadmap, "RUNNER", plan), ())
            finally:
                os.chdir(cwd)

    def test_closeout_allow_unowned_tilde_repo_not_honored_via_home(self):
        # `repo="~/repo"` is a RELATIVE path string (leading "~"), even though it happens to
        # expand to the real repo root under this $HOME. Pre-fix, expanduser() ran before the
        # absoluteness check, so this event spuriously matched — rebinding the authorization
        # through $HOME. Must fail closed.
        with tempfile.TemporaryDirectory() as home_td:
            home_dir = Path(home_td)
            repo = make_repo(home_dir)
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            payload = self._raw_attestation_payload(
                repo, roadmap, "RUNNER", event_repo="~/repo", event_roadmap=str(roadmap)
            )
            append_payload(repo, payload, roadmap=roadmap)

            cwd = os.getcwd()
            try:
                os.chdir(repo)
                with mock.patch.dict(os.environ, {"HOME": str(home_dir)}):
                    self.assertFalse(_closeout_allow_unowned_attested(repo, roadmap, "RUNNER"))
            finally:
                os.chdir(cwd)

    def test_lane_ir_override_tilde_repo_not_honored_via_home(self):
        with tempfile.TemporaryDirectory() as home_td:
            home_dir = Path(home_td)
            repo = make_repo(home_dir)
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            plan = repo / "plans" / "phase-plan-v1-RUNNER.md"
            payload = self._raw_lane_ir_payload(
                repo, roadmap, "RUNNER", event_repo="~/repo", event_roadmap=str(roadmap)
            )
            append_payload(repo, payload, roadmap=roadmap)

            cwd = os.getcwd()
            try:
                os.chdir(repo)
                with mock.patch.dict(os.environ, {"HOME": str(home_dir)}):
                    self.assertEqual(_lane_ir_override(repo, roadmap, "RUNNER", plan), ())
            finally:
                os.chdir(cwd)

    def test_closeout_allow_unowned_tilde_nonexistent_user_fails_closed_without_raising(self):
        # `Path("~nonexistent-user-xyz/repo").expanduser()` raises RuntimeError (no such user).
        # Pre-fix, that call sat OUTSIDE the try/except in the absoluteness guard, so this event
        # crashed reconciliation instead of being rejected. Must fail closed without raising.
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            payload = self._raw_attestation_payload(
                repo,
                roadmap,
                "RUNNER",
                event_repo="~nonexistent-user-xyz/repo",
                event_roadmap=str(roadmap),
            )
            append_payload(repo, payload, roadmap=roadmap)

            cwd = os.getcwd()
            try:
                os.chdir(repo)
                self.assertFalse(_closeout_allow_unowned_attested(repo, roadmap, "RUNNER"))
            finally:
                os.chdir(cwd)

    def test_lane_ir_override_tilde_nonexistent_user_fails_closed_without_raising(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            plan = repo / "plans" / "phase-plan-v1-RUNNER.md"
            payload = self._raw_lane_ir_payload(
                repo,
                roadmap,
                "RUNNER",
                event_repo="~nonexistent-user-xyz/repo",
                event_roadmap=str(roadmap),
            )
            append_payload(repo, payload, roadmap=roadmap)

            cwd = os.getcwd()
            try:
                os.chdir(repo)
                self.assertEqual(_lane_ir_override(repo, roadmap, "RUNNER", plan), ())
            finally:
                os.chdir(cwd)

    # ah#238 (codex CR round-3, comprehensive hardening): `Path(...).expanduser().resolve()`
    # raises ValueError("embedded null byte") for a string containing a null byte (e.g.
    # `plan_path: " "` / `"\x00"`). Pre-fix, the `plan_path` guard in both gates caught only
    # `(OSError, RuntimeError)`, so a `plan_path` with an embedded null byte propagated the
    # ValueError and CRASHED reconciliation instead of safely skipping the event. Everything
    # else about the event (repo/roadmap/phase/sha/operator_reason) matches, isolating the
    # crash to the plan_path parsing specifically.

    def test_closeout_allow_unowned_plan_path_embedded_null_byte_fails_closed_without_raising(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            payload = self._raw_attestation_payload(
                repo,
                roadmap,
                "RUNNER",
                event_repo=str(repo),
                event_roadmap=str(roadmap),
                plan_path="plan\x00.md",
            )
            append_payload(repo, payload, roadmap=roadmap)

            cwd = os.getcwd()
            try:
                os.chdir(repo)
                self.assertFalse(_closeout_allow_unowned_attested(repo, roadmap, "RUNNER"))
            finally:
                os.chdir(cwd)

    def test_lane_ir_override_plan_path_embedded_null_byte_fails_closed_without_raising(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            plan = repo / "plans" / "phase-plan-v1-RUNNER.md"
            payload = self._raw_lane_ir_payload(
                repo,
                roadmap,
                "RUNNER",
                event_repo=str(repo),
                event_roadmap=str(roadmap),
                plan_path="plan\x00.md",
            )
            append_payload(repo, payload, roadmap=roadmap)

            cwd = os.getcwd()
            try:
                os.chdir(repo)
                self.assertEqual(_lane_ir_override(repo, roadmap, "RUNNER", plan), ())
            finally:
                os.chdir(cwd)

    # Same crash class in the earlier `repo`/`roadmap` guard: confirm a null byte there (not
    # just an empty/relative/tilde value) also fails closed without raising, now that the
    # guard's except clause covers ValueError explicitly.

    def test_closeout_allow_unowned_repo_embedded_null_byte_fails_closed_without_raising(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            payload = self._raw_attestation_payload(
                repo,
                roadmap,
                "RUNNER",
                event_repo=str(repo) + "\x00",
                event_roadmap=str(roadmap),
            )
            append_payload(repo, payload, roadmap=roadmap)

            cwd = os.getcwd()
            try:
                os.chdir(repo)
                self.assertFalse(_closeout_allow_unowned_attested(repo, roadmap, "RUNNER"))
            finally:
                os.chdir(cwd)

    def test_lane_ir_override_roadmap_embedded_null_byte_fails_closed_without_raising(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            plan = repo / "plans" / "phase-plan-v1-RUNNER.md"
            payload = self._raw_lane_ir_payload(
                repo,
                roadmap,
                "RUNNER",
                event_repo=str(repo),
                event_roadmap=str(roadmap) + "\x00",
            )
            append_payload(repo, payload, roadmap=roadmap)

            cwd = os.getcwd()
            try:
                os.chdir(repo)
                self.assertEqual(_lane_ir_override(repo, roadmap, "RUNNER", plan), ())
            finally:
                os.chdir(cwd)

    # --- codex CR follow-up (ah#238, uniformity round): the `repo`/`roadmap` fields got the
    # raw-string is_absolute() rejection above, but the event's `plan_path` did NOT — it went
    # straight to `expanduser().resolve()`. A relative `plan_path` (e.g. "plans/x.md") is
    # honored whenever reconcile's CWD happens to be the repo root and rejected elsewhere
    # (the exact CWD-dependent acceptance already closed for repo/roadmap), and a `~/x.md`
    # plan_path rebinds through the current user's $HOME. These tests pin the fix: plan_path
    # now gets the SAME raw-string is_absolute() rejection, CWD-independently, in both gates.
    # A control test confirms a real ABSOLUTE plan_path is still honored (no regression).

    def test_closeout_allow_unowned_absolute_plan_path_honored(self):
        # Control: a real absolute plan_path matching the discovered plan artifact is still
        # honored post-fix (no regression from the new is_absolute() check).
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            plan = repo / "plans" / "phase-plan-v1-RUNNER.md"
            payload = self._raw_attestation_payload(
                repo,
                roadmap,
                "RUNNER",
                event_repo=str(repo),
                event_roadmap=str(roadmap),
                plan_path=str(plan),
            )
            append_payload(repo, payload, roadmap=roadmap)

            cwd = os.getcwd()
            try:
                os.chdir(repo)
                self.assertTrue(_closeout_allow_unowned_attested(repo, roadmap, "RUNNER"))
            finally:
                os.chdir(cwd)

    def test_lane_ir_override_absolute_plan_path_honored(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            plan = repo / "plans" / "phase-plan-v1-RUNNER.md"
            payload = self._raw_lane_ir_payload(
                repo,
                roadmap,
                "RUNNER",
                event_repo=str(repo),
                event_roadmap=str(roadmap),
                plan_path=str(plan),
            )
            append_payload(repo, payload, roadmap=roadmap)

            cwd = os.getcwd()
            try:
                os.chdir(repo)
                self.assertEqual(_lane_ir_override(repo, roadmap, "RUNNER", plan), ("unowned_file",))
            finally:
                os.chdir(cwd)

    def test_closeout_allow_unowned_relative_plan_path_fails_closed_at_repo_root_cwd(self):
        # The exact fail-open shape named in the codex follow-up: `plan_path` non-empty and
        # RELATIVE, repo/roadmap absolute and correct, CWD == the actual repo root (the common
        # case for reconcile). Pre-fix, `Path("plans/...").resolve()` == the correct absolute
        # plan path when CWD == repo root → spurious match.
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            payload = self._raw_attestation_payload(
                repo,
                roadmap,
                "RUNNER",
                event_repo=str(repo),
                event_roadmap=str(roadmap),
                plan_path="plans/phase-plan-v1-RUNNER.md",
            )
            append_payload(repo, payload, roadmap=roadmap)

            cwd = os.getcwd()
            try:
                os.chdir(repo)
                self.assertFalse(_closeout_allow_unowned_attested(repo, roadmap, "RUNNER"))
            finally:
                os.chdir(cwd)

    def test_closeout_allow_unowned_relative_plan_path_fails_closed_regardless_of_cwd(self):
        # CWD-independence: fails closed even from a THIRD, unrelated directory.
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as other:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            payload = self._raw_attestation_payload(
                repo,
                roadmap,
                "RUNNER",
                event_repo=str(repo),
                event_roadmap=str(roadmap),
                plan_path="plans/phase-plan-v1-RUNNER.md",
            )
            append_payload(repo, payload, roadmap=roadmap)

            cwd = os.getcwd()
            try:
                os.chdir(other)
                self.assertFalse(_closeout_allow_unowned_attested(repo, roadmap, "RUNNER"))
            finally:
                os.chdir(cwd)

    def test_lane_ir_override_relative_plan_path_fails_closed_at_repo_root_cwd(self):
        # Mirrors the closeout_allow_unowned relative-plan_path case for the second BREAKGLASS
        # SL-2 gate.
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            plan = repo / "plans" / "phase-plan-v1-RUNNER.md"
            payload = self._raw_lane_ir_payload(
                repo,
                roadmap,
                "RUNNER",
                event_repo=str(repo),
                event_roadmap=str(roadmap),
                plan_path="plans/phase-plan-v1-RUNNER.md",
            )
            append_payload(repo, payload, roadmap=roadmap)

            cwd = os.getcwd()
            try:
                os.chdir(repo)
                self.assertEqual(_lane_ir_override(repo, roadmap, "RUNNER", plan), ())
            finally:
                os.chdir(cwd)

    def test_lane_ir_override_relative_plan_path_fails_closed_regardless_of_cwd(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as other:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            plan = repo / "plans" / "phase-plan-v1-RUNNER.md"
            payload = self._raw_lane_ir_payload(
                repo,
                roadmap,
                "RUNNER",
                event_repo=str(repo),
                event_roadmap=str(roadmap),
                plan_path="plans/phase-plan-v1-RUNNER.md",
            )
            append_payload(repo, payload, roadmap=roadmap)

            cwd = os.getcwd()
            try:
                os.chdir(other)
                self.assertEqual(_lane_ir_override(repo, roadmap, "RUNNER", plan), ())
            finally:
                os.chdir(cwd)

    def test_closeout_allow_unowned_tilde_plan_path_not_honored_via_home(self):
        # `plan_path="~/repo/plans/..."` is a RELATIVE path string (leading "~"), even though it
        # happens to expand to the real plan path under this $HOME. Must fail closed, not
        # rebind the authorization through $HOME.
        with tempfile.TemporaryDirectory() as home_td:
            home_dir = Path(home_td)
            repo = make_repo(home_dir)
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            tilde_plan = "~/" + str((repo / "plans" / "phase-plan-v1-RUNNER.md").relative_to(home_dir))
            payload = self._raw_attestation_payload(
                repo,
                roadmap,
                "RUNNER",
                event_repo=str(repo),
                event_roadmap=str(roadmap),
                plan_path=tilde_plan,
            )
            append_payload(repo, payload, roadmap=roadmap)

            cwd = os.getcwd()
            try:
                os.chdir(repo)
                with mock.patch.dict(os.environ, {"HOME": str(home_dir)}):
                    self.assertFalse(_closeout_allow_unowned_attested(repo, roadmap, "RUNNER"))
            finally:
                os.chdir(cwd)

    def test_lane_ir_override_tilde_plan_path_not_honored_via_home(self):
        with tempfile.TemporaryDirectory() as home_td:
            home_dir = Path(home_td)
            repo = make_repo(home_dir)
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            plan = repo / "plans" / "phase-plan-v1-RUNNER.md"
            tilde_plan = "~/" + str(plan.relative_to(home_dir))
            payload = self._raw_lane_ir_payload(
                repo,
                roadmap,
                "RUNNER",
                event_repo=str(repo),
                event_roadmap=str(roadmap),
                plan_path=tilde_plan,
            )
            append_payload(repo, payload, roadmap=roadmap)

            cwd = os.getcwd()
            try:
                os.chdir(repo)
                with mock.patch.dict(os.environ, {"HOME": str(home_dir)}):
                    self.assertEqual(_lane_ir_override(repo, roadmap, "RUNNER", plan), ())
            finally:
                os.chdir(cwd)


class RoadmapPathsMatchNullByteTest(unittest.TestCase):
    """ah#238 (comprehensive hardening follow-up): ``roadmap_paths_match`` is called from the
    MAIN ``reconcile()`` event loop for EVERY event's ``repo``/``roadmap`` fields, not just the
    BREAKGLASS SL-2 gates. Pre-fix, its ``expanduser().resolve()`` calls caught only ``OSError``,
    so an embedded null byte in a stored repo/roadmap string raised an uncaught ``ValueError``
    and crashed reconciliation for ANY event, breakglass or not. Must fail closed to
    ``(False, False)`` without raising.
    """

    def test_stored_roadmap_embedded_null_byte_fails_closed_without_raising(self):
        stored_repo = "/a/repo"
        stored_roadmap = "/a/repo/specs/phase-plans\x00-v1.md"
        repo = Path("/b/repo")
        roadmap = repo / "specs" / "phase-plans-v1.md"
        self.assertEqual(roadmap_paths_match(stored_repo, stored_roadmap, repo, roadmap), (False, False))

    def test_stored_repo_embedded_null_byte_fails_closed_without_raising(self):
        stored_repo = "/a/re\x00po"
        stored_roadmap = "/a/repo/specs/phase-plans-v1.md"
        repo = Path("/b/repo")
        roadmap = repo / "specs" / "phase-plans-v1.md"
        self.assertEqual(roadmap_paths_match(stored_repo, stored_roadmap, repo, roadmap), (False, False))


class RoadmapPathsMatchNonStrStoredRoadmapTest(unittest.TestCase):
    """gemini CR (fix/breakglass-empty-repo-failclosed-238): the fast-path try/except
    guarding ``Path(stored_roadmap_str).expanduser().resolve()`` caught only
    ``(OSError, ValueError, RuntimeError)``, omitting ``TypeError`` even though the
    later portable-path except in the same function already includes it. A non-str/
    non-PathLike ``stored_roadmap`` (e.g. a nested object from malformed ledger JSON)
    reaching ``Path(...)`` with that type would raise an uncaught ``TypeError``. Added
    ``TypeError`` to the fast-path except for parity. Must fail closed to
    ``(False, False)`` without raising for non-str stored_roadmap values.
    """

    def test_stored_roadmap_none_fails_closed_without_raising(self):
        stored_repo = "/a/repo"
        stored_roadmap = None
        repo = Path("/b/repo")
        roadmap = repo / "specs" / "phase-plans-v1.md"
        self.assertEqual(roadmap_paths_match(stored_repo, stored_roadmap, repo, roadmap), (False, False))

    def test_stored_roadmap_non_str_object_fails_closed_without_raising(self):
        stored_repo = "/a/repo"
        stored_roadmap = {"nested": "object"}
        repo = Path("/b/repo")
        roadmap = repo / "specs" / "phase-plans-v1.md"
        self.assertEqual(roadmap_paths_match(stored_repo, stored_roadmap, repo, roadmap), (False, False))


if __name__ == "__main__":
    unittest.main()
