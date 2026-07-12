import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.events import append_event
from phase_loop_runtime.models import LoopEvent, StateSnapshot, utc_now
from phase_loop_runtime.plan_manifest import DotfilesPlanEntry, DotfilesPlanRef, append_entry, read_manifest
from phase_loop_runtime.provenance import event_provenance
from phase_loop_runtime.reconcile import _clean_verified_dirty_closeout_recovery_supersedes_blocker, reconcile
from phase_loop_runtime.state import write_state
from phase_loop_smoke_utils import make_two_phase_repo
from phase_loop_test_utils import (
    make_message_board_fixture,
    make_regenesis_amendment_fixture,
    make_repo,
    provenanced_event,
    provenanced_state,
    write_phase_plan,
)


class PhaseLoopReconcileTest(unittest.TestCase):
    def test_events_update_reconciled_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            (repo / "plans" / "phase-plan-v1-RUNNER.md").write_text("# RUNNER\n")
            append_event(repo, provenanced_event(repo, roadmap, "RUNNER", "complete", action="execute"))
            snapshot = reconcile(repo, roadmap)
            self.assertEqual(snapshot.phases["RUNNER"], "complete")

    def test_event_only_dry_run_terminal_summary_is_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "ALPHA", roadmap)
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="ALPHA",
                    action="run",
                    status="planned",
                    model="gpt-5.6-sol",
                    reasoning_effort="medium",
                    source="fixture",
                    metadata={
                        "launch": {"dry_run": True},
                        "terminal_summary": {
                            "terminal_status": "dry_run",
                            "verification_status": "not_run",
                            "dirty_paths": [],
                        },
                        "dry_run_only": True,
                    },
                    **event_provenance(roadmap, "ALPHA"),
                ),
            )

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.phases["ALPHA"], "planned")
            self.assertIsNone(snapshot.closeout_terminal_status)
            self.assertIsNone(snapshot.terminal_summary)
            warning = snapshot.ledger_warnings[-1]
            self.assertEqual(warning["reason"], "event_only_status")
            self.assertEqual(warning["value"], "dry_run")
            self.assertEqual(warning["status"], "dry_run")
            self.assertEqual(warning["raw_event_summary"]["status"], "planned")

    def test_non_event_only_invalid_terminal_summary_is_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "ALPHA", roadmap)
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="ALPHA",
                    action="run",
                    status="planned",
                    model="gpt-5.6-sol",
                    reasoning_effort="medium",
                    source="fixture",
                    metadata={
                        "terminal_summary": {
                            "terminal_status": "not_a_phase_status",
                            "verification_status": "not_run",
                            "dirty_paths": [],
                        },
                    },
                    **event_provenance(roadmap, "ALPHA"),
                ),
            )

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.closeout_terminal_status, "not_a_phase_status")
            self.assertEqual(snapshot.terminal_summary["terminal_status"], "not_a_phase_status")
            self.assertFalse(any(warning["reason"] == "event_only_status" for warning in snapshot.ledger_warnings))

    def test_complete_event_with_stale_blocker_does_not_keep_global_blocker(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            stale_blocker = {
                "human_required": False,
                "blocker_class": "dirty_worktree_conflict",
                "blocker_summary": "stale dirty blocker",
                "required_human_inputs": (),
                "access_attempts": (),
            }
            append_event(repo, provenanced_event(repo, roadmap, "CONTRACT", "blocked", action="execute"))
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="CONTRACT",
                    action="repair",
                    status="complete",
                    model="gpt-5.6-terra",
                    reasoning_effort="medium",
                    source="fixture",
                    blocker=stale_blocker,
                    **event_provenance(roadmap, "CONTRACT"),
                ),
            )

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.phases["CONTRACT"], "complete")
            self.assertFalse(snapshot.human_required)
            self.assertIsNone(snapshot.blocker_class)
            self.assertIsNone(snapshot.blocker_summary)

    def test_state_transition_event_clears_stale_dirty_blocker_idempotently(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "CONTRACT", roadmap)
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
                        "human_required": False,
                        "blocker_class": "dirty_worktree_conflict",
                        "blocker_summary": "stale dirty blocker",
                        "required_human_inputs": (),
                        "access_attempts": (),
                    },
                    **event_provenance(roadmap, "CONTRACT"),
                ),
            )
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="CONTRACT",
                    action="state_transition",
                    status="planned",
                    model="gpt-5.6-terra",
                    reasoning_effort="medium",
                    source="fixture",
                    metadata={
                        "state_transition": {
                            "from": "blocked",
                            "to": "planned",
                            "reason": "repair_precondition_cleared",
                            "trigger": "live_dirty_worktree_check",
                        }
                    },
                    **event_provenance(roadmap, "CONTRACT"),
                ),
            )

            first = reconcile(repo, roadmap)
            second = reconcile(repo, roadmap)

            self.assertEqual(first.phases["CONTRACT"], "planned")
            self.assertEqual(second.phases["CONTRACT"], "planned")
            self.assertFalse(first.human_required)
            self.assertFalse(second.human_required)
            self.assertIsNone(first.blocker_class)
            self.assertIsNone(second.blocker_class)
            self.assertEqual(first.current_phase, second.current_phase)
            self.assertEqual(first.ledger_warnings, second.ledger_warnings)

    def test_placeholder_none_blocker_class_is_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
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
                        "human_required": False,
                        "blocker_class": "<frozen blocker class or none>",
                        "blocker_summary": "<short actionable summary or none>",
                        "required_human_inputs": (),
                        "access_attempts": (),
                    },
                    **event_provenance(roadmap, "CONTRACT"),
                ),
            )

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.phases["CONTRACT"], "blocked")
            self.assertFalse(snapshot.human_required)
            self.assertIsNone(snapshot.blocker_class)
            self.assertIsNone(snapshot.blocker_summary)

    def test_pipeline_stale_input_blocker_remains_trusted_non_human(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="RUNNER",
                    action="execute",
                    status="blocked",
                    model="gpt-5.6-terra",
                    reasoning_effort="medium",
                    source="fixture",
                    blocker={
                        "human_required": False,
                        "blocker_class": "contract_bug",
                        "blocker_summary": "Pipeline execution freshness validation failed: mismatched_source_bundle_sha256",
                        "required_human_inputs": (),
                        "access_attempts": (),
                    },
                    metadata={
                        "pipeline_execution_preflight": {
                            "status": "blocked",
                            "diagnostic": {"kind": "mismatched_source_bundle_sha256"},
                        }
                    },
                    **event_provenance(roadmap, "RUNNER"),
                ),
            )

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            self.assertFalse(snapshot.human_required)
            self.assertEqual(snapshot.blocker_class, "contract_bug")
            self.assertIn("Pipeline execution freshness", snapshot.blocker_summary)

    def test_operator_auth_blocker_class_is_normalized(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="CONTRACT",
                    action="execute",
                    status="blocked",
                    model="claude-opus-4-8",
                    reasoning_effort="medium",
                    source="fixture",
                    blocker={
                        "human_required": True,
                        "blocker_class": "operator_auth_required",
                        "blocker_summary": "Interactive WorkOS login required.",
                        "required_human_inputs": ("tester_safe_workos_account",),
                        "access_attempts": (),
                    },
                    **event_provenance(roadmap, "CONTRACT"),
                ),
            )

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.phases["CONTRACT"], "blocked")
            self.assertTrue(snapshot.human_required)
            self.assertEqual(snapshot.blocker_class, "account_or_billing_setup")
            self.assertEqual(snapshot.blocker_summary, "Interactive WorkOS login required.")

    def test_external_setup_blocker_class_is_normalized(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="CONTRACT",
                    action="execute",
                    status="blocked",
                    model="claude-opus-4-8",
                    reasoning_effort="medium",
                    source="fixture",
                    blocker={
                        "human_required": True,
                        "blocker_class": "blocked_by_external_setup",
                        "blocker_summary": "Interactive AWS SSO login required.",
                        "required_human_inputs": ("aws_sso_login",),
                        "access_attempts": (),
                    },
                    **event_provenance(roadmap, "CONTRACT"),
                ),
            )

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.phases["CONTRACT"], "blocked")
            self.assertTrue(snapshot.human_required)
            self.assertEqual(snapshot.blocker_class, "admin_approval")
            self.assertEqual(snapshot.blocker_summary, "Interactive AWS SSO login required.")

    def test_implementation_blocker_class_is_normalized(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="CONTRACT",
                    action="execute",
                    status="blocked",
                    model="claude-opus-4-8",
                    reasoning_effort="medium",
                    source="fixture",
                    blocker={
                        "human_required": False,
                        "blocker_class": "blocked_by_implementation",
                        "blocker_summary": "Deployed route returned the wrong denial envelope.",
                        "required_human_inputs": (),
                        "access_attempts": (),
                    },
                    **event_provenance(roadmap, "CONTRACT"),
                ),
            )

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.phases["CONTRACT"], "blocked")
            self.assertFalse(snapshot.human_required)
            self.assertEqual(snapshot.blocker_class, "repeated_verification_failure")
            self.assertEqual(snapshot.blocker_summary, "Deployed route returned the wrong denial envelope.")

    def test_planned_state_requires_current_plan_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            stale = provenanced_state(repo, roadmap, {"RUNNER": "planned"})
            write_state(repo, stale)

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.phases["RUNNER"], "unplanned")

    def test_planned_event_requires_current_plan_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(repo, provenanced_event(repo, roadmap, "RUNNER", "planned", action="dry-run"))

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.phases["RUNNER"], "unplanned")

    def test_reconcile_auto_imports_regex_only_phase_plans(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            (repo / "plans" / "manifest.json").write_text('{"schema_version": 1, "plans": []}\n', encoding="utf-8")

            snapshot = reconcile(repo, roadmap)
            manifest = read_manifest(repo)

            self.assertEqual(snapshot.phases["RUNNER"], "planned")
            self.assertTrue(any(entry.slug == "v1-RUNNER" and entry.status == "imported" for entry in manifest.plans))

    def test_reconcile_marks_missing_manifest_phase_plan_orphaned(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            missing_plan = repo / "plans" / "phase-plan-v1-RUNNER.md"
            append_entry(
                repo,
                DotfilesPlanEntry(
                    slug="v1-RUNNER",
                    file="plans/phase-plan-v1-RUNNER.md",
                    type="phase",
                    status="committed",
                    created_at="2026-05-30T00:00:00Z",
                    updated_at="2026-05-30T00:00:00Z",
                    owner_skill="codex-plan-phase",
                    roadmap_ref=DotfilesPlanRef(
                        slug=roadmap.stem,
                        file="specs/phase-plans-v1.md",
                        type="phase",
                        status="committed",
                    ),
                    phase_alias="RUNNER",
                    if_gates_produced=("IF-0-RUNNER-1",),
                    lanes=("SL-0",),
                ),
            )
            self.assertFalse(missing_plan.exists())

            snapshot = reconcile(repo, roadmap)
            manifest = read_manifest(repo)
            entry = next(entry for entry in manifest.plans if entry.slug == "v1-RUNNER")

            self.assertEqual(snapshot.phases["RUNNER"], "unplanned")
            self.assertEqual(entry.status, "orphaned")
            self.assertTrue(any(warning["reason"] == "manifest_plan_file_missing" for warning in snapshot.ledger_warnings))

    def test_reconcile_escape_hatch_skips_manifest_import_and_orphaning(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)

            with unittest.mock.patch.dict("os.environ", {"PHASE_LOOP_MANIFEST_DISABLED": "1"}):
                snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.phases["RUNNER"], "planned")
            self.assertFalse(read_manifest(repo).plans)

    def test_stale_executing_state_becomes_unknown_in_dirty_repo(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            (repo / "README.md").write_text("dirty\n")
            write_state(repo, provenanced_state(repo, roadmap, {"RUNNER": "executing"}))
            snapshot = reconcile(repo, roadmap)
            self.assertEqual(snapshot.phases["RUNNER"], "unknown")

    def test_downstream_roadmap_edit_preserves_matching_complete_phase(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(repo, provenanced_event(repo, roadmap, "ALPHA", "complete", action="execute"))
            roadmap.write_text(roadmap.read_text().replace("### Phase 1 - Beta (BETA)", "### Phase 1 - Beta Revised (BETA)"))
            snapshot = reconcile(repo, roadmap)
            self.assertEqual(snapshot.phases["ALPHA"], "complete")
            self.assertEqual(snapshot.phases["BETA"], "unplanned")

    def test_downstream_roadmap_edit_invalidates_planned_phase(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(repo, provenanced_event(repo, roadmap, "ALPHA", "planned"))
            roadmap.write_text(roadmap.read_text().replace("### Phase 1 - Beta (BETA)", "### Phase 1 - Beta Revised (BETA)"))
            snapshot = reconcile(repo, roadmap)
            self.assertEqual(snapshot.phases["ALPHA"], "unplanned")
            self.assertEqual(snapshot.ledger_warnings[-1]["reason"], "roadmap_mismatch")

    def test_phase_block_edit_invalidates_complete_phase(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(repo, provenanced_event(repo, roadmap, "ALPHA", "complete", action="execute"))
            roadmap.write_text(roadmap.read_text().replace("### Phase 0 - Alpha (ALPHA)", "### Phase 0 - Alpha Changed (ALPHA)"))
            snapshot = reconcile(repo, roadmap)
            self.assertEqual(snapshot.phases["ALPHA"], "unplanned")
            self.assertEqual(snapshot.ledger_warnings[-1]["reason"], "phase_mismatch")
            # #85: the invalidation is (correctly) kept, but the drift is now flagged
            # as a repairable gold_record_amendment so status can tell it apart from a
            # genuinely-never-planned phase.
            amendment_warnings = [
                w for w in snapshot.ledger_warnings if w.get("diagnostic_class") == "gold_record_amendment"
            ]
            self.assertTrue(amendment_warnings)
            self.assertTrue(all(w.get("repairable") for w in amendment_warnings))
            self.assertTrue(all(w["phase"] == "ALPHA" for w in amendment_warnings))
            self.assertTrue(all("amendment_drift" in w for w in amendment_warnings))

    def test_amendment_drift_marker_only_on_completed_phase_not_genuinely_unplanned(self):
        # #85 two-direction guard: a completed phase whose OWN block was amended in
        # flight is flagged repairable (gold_record_amendment); a phase that was
        # genuinely never planned gets NO such marker — that asymmetry is what lets
        # status distinguish "amendment changed hashes" from "genuinely unplanned".
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            # ALPHA is completed; BETA is never planned (no state, no event).
            write_state(repo, provenanced_state(repo, roadmap, {"ALPHA": "complete", "BETA": "unplanned"}))
            append_event(repo, provenanced_event(repo, roadmap, "ALPHA", "complete", action="execute"))
            roadmap.write_text(roadmap.read_text().replace("### Phase 0 - Alpha (ALPHA)", "### Phase 0 - Alpha Changed (ALPHA)"))
            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.phases["ALPHA"], "unplanned")
            self.assertEqual(snapshot.phases["BETA"], "unplanned")
            marked = {w["phase"] for w in snapshot.ledger_warnings if w.get("diagnostic_class") == "gold_record_amendment"}
            self.assertIn("ALPHA", marked)
            self.assertNotIn("BETA", marked)

    def test_newer_untrusted_blocked_event_prevents_false_roadmap_complete(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(repo, provenanced_event(repo, roadmap, "ALPHA", "complete", action="execute"))
            append_event(repo, provenanced_event(repo, roadmap, "BETA", "complete", action="execute"))
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="BETA",
                    action="execute",
                    status="blocked",
                    model="gpt-5.6-terra",
                    reasoning_effort="medium",
                    source="fixture",
                    schema_version=2,
                    roadmap_sha256="future-roadmap",
                    phase_sha256="future-phase",
                ),
            )

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.phases["ALPHA"], "complete")
            self.assertEqual(snapshot.phases["BETA"], "unknown")
            self.assertEqual(snapshot.current_phase, "BETA")
            self.assertTrue(any(warning["reason"] == "newer_untrusted_terminal_event" for warning in snapshot.ledger_warnings))

    def test_legacy_state_and_events_are_warned_not_trusted(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_state(repo, StateSnapshot(timestamp=utc_now(), repo=str(repo), roadmap=str(roadmap), phases={"RUNNER": "complete"}))
            append_event(repo, LoopEvent(timestamp=utc_now(), repo=str(repo), roadmap=str(roadmap), phase="RUNNER", action="execute", status="complete", model="gpt-5.6-terra", reasoning_effort="medium", source="default"))
            snapshot = reconcile(repo, roadmap)
            self.assertEqual(snapshot.phases["RUNNER"], "unplanned")
            self.assertTrue(any(warning["reason"] == "legacy" for warning in snapshot.ledger_warnings))

    def test_fresh_unplanned_state_does_not_warn(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "CONTRACT", roadmap)
            write_state(repo, provenanced_state(repo, roadmap, {"CONTRACT": "planned", "ACCESS": "unplanned", "RUNNER": "unplanned"}))
            snapshot = reconcile(repo, roadmap)
            self.assertEqual(snapshot.phases["CONTRACT"], "planned")
            self.assertEqual(snapshot.ledger_warnings, ())

    def test_later_trusted_event_suppresses_superseded_stale_warning(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = make_message_board_fixture(Path(td))
            repo = fixture.repo
            roadmap = fixture.roadmap
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase=fixture.execute_phase,
                    action="execute",
                    status="blocked",
                    model="gpt-5.6-terra",
                    reasoning_effort="medium",
                    source="fixture",
                    blocker={
                        "human_required": False,
                        "blocker_class": "dirty_worktree_conflict",
                        "blocker_summary": "message_board stale blocker",
                        "required_human_inputs": (),
                    },
                    roadmap_sha256="stale-roadmap",
                    phase_sha256="stale-phase",
                ),
            )
            append_event(repo, provenanced_event(repo, roadmap, fixture.execute_phase, "complete", action="execute"))
            snapshot = reconcile(repo, roadmap)
            self.assertEqual(snapshot.phases[fixture.execute_phase], "complete")
            self.assertEqual(snapshot.ledger_warnings, ())

    def test_trusted_amendment_event_suppresses_stale_downstream_warning_for_same_phase(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = make_regenesis_amendment_fixture(Path(td))
            repo = fixture.repo
            roadmap = fixture.roadmap
            append_event(repo, provenanced_event(repo, roadmap, fixture.stale_phase, "planned", action="plan"))
            roadmap.write_text(
                "# Roadmap\n\n"
                "### Phase 0 - Affordance Verification (AFFVERIFY)\n\n"
                "### Phase 1 - Mobile Shell (MOBSHELL)\n\n"
                "### Phase 2 - Visual Fidelity (VISUAL)\n"
            )
            write_phase_plan(repo, fixture.stale_phase, roadmap)
            append_event(repo, provenanced_event(repo, roadmap, fixture.stale_phase, "planned", action="plan"))

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.phases["AFFVERIFY"], "unplanned")
            self.assertEqual(snapshot.phases[fixture.next_phase], "unplanned")
            self.assertEqual(snapshot.phases[fixture.stale_phase], "planned")
            self.assertEqual(snapshot.ledger_warnings, ())

    def test_executed_phase_remains_current_for_repair(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(repo, provenanced_event(repo, roadmap, "ALPHA", "executed", action="execute"))
            snapshot = reconcile(repo, roadmap)
            self.assertEqual(snapshot.current_phase, "ALPHA")

    def test_missing_plan_after_planning_blocker_is_trusted_until_repaired(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="CONTRACT",
                    action="run",
                    status="blocked",
                    model="gpt-5.6-terra",
                    reasoning_effort="medium",
                    source="fixture",
                    blocker={
                        "human_required": False,
                        "blocker_class": "repeated_verification_failure",
                        "blocker_summary": "Planning turn for CONTRACT exited successfully but did not create a current phase plan artifact.",
                        "required_human_inputs": (),
                    },
                    metadata={
                        "missing_plan_after_planning": {
                            "reason": "planning_launch_missing_current_plan_artifact",
                            "expected_phase": "CONTRACT",
                        },
                        "terminal_summary": {
                            "terminal_status": "blocked",
                            "terminal_blocker": {
                                "human_required": False,
                                "blocker_class": "repeated_verification_failure",
                                "blocker_summary": "Planning artifact missing.",
                                "required_human_inputs": (),
                            },
                            "verification_status": "blocked",
                            "next_action": "Rerun planning after inspection.",
                            "dirty_paths": [],
                            "phase_owned_dirty": False,
                        },
                    },
                    **event_provenance(roadmap, "CONTRACT"),
                ),
            )

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.current_phase, "CONTRACT")
            self.assertEqual(snapshot.phases["CONTRACT"], "blocked")
            self.assertFalse(snapshot.human_required)
            self.assertEqual(snapshot.blocker_class, "repeated_verification_failure")

    def test_closeout_phase_remains_current_ahead_of_earlier_unplanned_work(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(repo, provenanced_event(repo, roadmap, "BETA", "awaiting_phase_closeout", action="execute"))

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.phases["ALPHA"], "unplanned")
            self.assertEqual(snapshot.phases["BETA"], "awaiting_phase_closeout")
            self.assertEqual(snapshot.current_phase, "BETA")

    def test_closeout_commit_event_completes_previously_executed_phase(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(repo, provenanced_event(repo, roadmap, "ALPHA", "awaiting_phase_closeout", action="execute"))
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="ALPHA",
                    action="execute",
                    status="executed",
                    model="gpt-5.6-terra",
                    reasoning_effort="medium",
                    source="fixture",
                    metadata={
                        "closeout": {
                            "closeout_mode": "push",
                            "closeout_action": "push",
                            "closeout_commit": "abc123",
                            "closeout_push_ref": "origin refs/heads/main",
                            "verification_status": "passed",
                        }
                    },
                    **event_provenance(roadmap, "ALPHA"),
                ),
            )

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.phases["ALPHA"], "complete")
            self.assertEqual(snapshot.closeout_summary["closeout_commit"], "abc123")

    def test_trusted_dirty_summary_is_exposed_for_current_phase(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="BETA",
                    action="execute",
                    status="awaiting_phase_closeout",
                    model="gpt-5.6-terra",
                    reasoning_effort="medium",
                    source="fixture",
                    metadata={
                        "incomplete_execute_dirty_worktree": {
                            "reason": "execute_status_without_completion_with_dirty_worktree",
                            "terminal_status": "executed",
                            "dirty_paths": ["README.md"],
                            "phase_owned_dirty_paths": ["README.md"],
                            "unowned_dirty_paths": [],
                            "pre_existing_dirty_paths": [],
                            "phase_owned_dirty": True,
                        }
                    },
                    **event_provenance(roadmap, "BETA"),
                ),
            )

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.current_phase, "BETA")
            self.assertEqual(snapshot.dirty_paths, ("README.md",))
            self.assertEqual(snapshot.phase_owned_dirty_paths, ("README.md",))
            self.assertTrue(snapshot.phase_owned_dirty)

    def test_previous_phase_owned_paths_round_trip_from_dirty_summary(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="BETA",
                    action="execute",
                    status="awaiting_phase_closeout",
                    model="gpt-5.6-terra",
                    reasoning_effort="medium",
                    source="fixture",
                    metadata={
                        "incomplete_execute_dirty_worktree": {
                            "reason": "execute_status_without_completion_with_dirty_worktree",
                            "terminal_status": "executed",
                            "dirty_paths": ["README.md"],
                            "phase_owned_dirty_paths": [],
                            "previous_phase_owned_paths": ["README.md"],
                            "unowned_dirty_paths": [],
                            "pre_existing_dirty_paths": [],
                            "phase_owned_dirty": True,
                        },
                        "terminal_summary": {
                            "terminal_status": "executed",
                            "verification_status": "not_run",
                            "dirty_paths": ["README.md"],
                            "phase_owned_dirty": True,
                            "phase_owned_dirty_paths": [],
                            "previous_phase_owned_paths": ["README.md"],
                            "unowned_dirty_paths": [],
                            "pre_existing_dirty_paths": [],
                        },
                    },
                    **event_provenance(roadmap, "BETA"),
                ),
            )

            first = reconcile(repo, roadmap)
            second = reconcile(repo, roadmap)

            self.assertEqual(first.previous_phase_owned_paths, ("README.md",))
            self.assertEqual(second.previous_phase_owned_paths, ("README.md",))
            self.assertEqual(first.pre_existing_dirty_paths, ())

    def test_completed_upstream_terminal_summary_is_not_current_phase_summary(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="ALPHA",
                    action="execute",
                    status="complete",
                    model="gpt-5.6-terra",
                    reasoning_effort="medium",
                    source="fixture",
                    metadata={
                        "terminal_summary": {
                            "terminal_status": "complete",
                            "terminal_blocker": None,
                            "verification_status": "passed",
                            "next_action": "Plan BETA next.",
                            "dirty_paths": [],
                            "phase_owned_dirty": False,
                            "artifact_paths": {"terminal": "runs/x/terminal-summary.json"},
                        }
                    },
                    **event_provenance(roadmap, "ALPHA"),
                ),
            )

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.current_phase, "BETA")
            self.assertIsNone(snapshot.terminal_summary)

    def test_automation_only_blocked_event_is_trusted_and_not_downgraded_by_parent_planned_event(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            plan = write_phase_plan(repo, "ALPHA", roadmap)
            events_path = repo / ".phase-loop" / "events.jsonl"
            events_path.parent.mkdir(parents=True)
            events_path.write_text(
                (
                    '{"timestamp":"2026-04-24T00:00:00Z","source":"manual","automation":'
                    '{"status":"blocked","artifact":"'
                    + str(plan)
                    + '","human_required":true,"blocker_class":"dirty_worktree_conflict",'
                    '"blocker_summary":"Clean worktree required.","required_human_inputs":["clean worktree"]}}\n'
                ),
                encoding="utf-8",
            )
            append_event(repo, provenanced_event(repo, roadmap, "ALPHA", "planned", action="run"))
            snapshot = reconcile(repo, roadmap)
            self.assertEqual(snapshot.phases["ALPHA"], "blocked")
            self.assertTrue(snapshot.human_required)
            self.assertEqual(snapshot.blocker_class, "dirty_worktree_conflict")

    def test_verified_dirty_closeout_recovery_completes_stale_nonhuman_blocker(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="ALPHA",
                    action="execute",
                    status="blocked",
                    model="gpt-5.6-terra",
                    reasoning_effort="medium",
                    source="fixture",
                    blocker={
                        "human_required": False,
                        "blocker_class": "dirty_worktree_conflict",
                        "blocker_summary": "Phase reported verified dirty closeout but left dirty paths that are not closeout-safe.",
                        "required_human_inputs": (),
                    },
                    **event_provenance(roadmap, "ALPHA"),
                ),
            )
            append_event(repo, self._verified_dirty_recovery_event(repo, roadmap))

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.phases["ALPHA"], "complete")
            self.assertFalse(snapshot.human_required)
            self.assertIsNone(snapshot.blocker_class)
            self.assertEqual(snapshot.closeout_summary["closeout_commit"], "abc123")
            self.assertTrue(
                any(
                    warning["reason"] == "clean_verified_dirty_closeout_recovery_superseded_nonhuman_blocker"
                    for warning in snapshot.ledger_warnings
                )
            )

    def test_verified_dirty_closeout_recovery_helper_requires_passed_verification(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(repo, self._verified_dirty_recovery_event(repo, roadmap, verification_status="failed"))

            self.assertIsNone(
                _clean_verified_dirty_closeout_recovery_supersedes_blocker(
                    repo,
                    roadmap,
                    "ALPHA",
                    event_provenance(roadmap, "ALPHA")["roadmap_sha256"],
                    {"ALPHA": event_provenance(roadmap, "ALPHA")["phase_sha256"]},
                )
            )

    def test_verified_dirty_closeout_recovery_helper_requires_closeout_commit(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(repo, self._verified_dirty_recovery_event(repo, roadmap, closeout_commit=None))

            self.assertIsNone(
                _clean_verified_dirty_closeout_recovery_supersedes_blocker(
                    repo,
                    roadmap,
                    "ALPHA",
                    event_provenance(roadmap, "ALPHA")["roadmap_sha256"],
                    {"ALPHA": event_provenance(roadmap, "ALPHA")["phase_sha256"]},
                )
            )

    def test_verified_dirty_closeout_recovery_helper_requires_matching_reason(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(repo, self._verified_dirty_recovery_event(repo, roadmap, reason="manual_repair"))

            self.assertIsNone(
                _clean_verified_dirty_closeout_recovery_supersedes_blocker(
                    repo,
                    roadmap,
                    "ALPHA",
                    event_provenance(roadmap, "ALPHA")["roadmap_sha256"],
                    {"ALPHA": event_provenance(roadmap, "ALPHA")["phase_sha256"]},
                )
            )

    def test_verified_dirty_closeout_recovery_helper_requires_empty_unowned_paths(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(repo, self._verified_dirty_recovery_event(repo, roadmap, unowned_dirty_paths=["unexpected.txt"]))

            self.assertIsNone(
                _clean_verified_dirty_closeout_recovery_supersedes_blocker(
                    repo,
                    roadmap,
                    "ALPHA",
                    event_provenance(roadmap, "ALPHA")["roadmap_sha256"],
                    {"ALPHA": event_provenance(roadmap, "ALPHA")["phase_sha256"]},
                )
            )

    def test_verified_dirty_closeout_recovery_does_not_clear_human_blocker(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="ALPHA",
                    action="execute",
                    status="blocked",
                    model="gpt-5.6-terra",
                    reasoning_effort="medium",
                    source="fixture",
                    blocker={
                        "human_required": True,
                        "blocker_class": "admin_approval",
                        "blocker_summary": "Approval required.",
                        "required_human_inputs": ("approval",),
                    },
                    **event_provenance(roadmap, "ALPHA"),
                ),
            )
            append_event(repo, self._verified_dirty_recovery_event(repo, roadmap))

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.phases["ALPHA"], "blocked")
            self.assertTrue(snapshot.human_required)
            self.assertEqual(snapshot.blocker_class, "admin_approval")

    def test_verified_dirty_closeout_recovery_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(repo, self._verified_dirty_recovery_event(repo, roadmap))

            first = reconcile(repo, roadmap)
            second = reconcile(repo, roadmap)

            self.assertEqual(first.phases["ALPHA"], "complete")
            self.assertEqual(second.phases["ALPHA"], "complete")

    def _verified_dirty_recovery_event(
        self,
        repo: Path,
        roadmap: Path,
        *,
        verification_status: str = "passed",
        closeout_commit: str | None = "abc123",
        reason: str = "verified_dirty_closeout_recovery",
        unowned_dirty_paths: list[str] | None = None,
    ) -> LoopEvent:
        closeout = {
            "closeout_mode": "commit",
            "closeout_action": "manual",
            "verification_status": "passed",
        }
        if closeout_commit is not None:
            closeout["closeout_commit"] = closeout_commit
        return LoopEvent(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phase="ALPHA",
            action="execute",
            status="blocked",
            model="gpt-5.6-terra",
            reasoning_effort="medium",
            source="fixture",
            metadata={
                "completion_dirty_worktree": {
                    "reason": reason,
                    "terminal_status": "complete",
                    "dirty_paths": ["owned.txt"],
                    "phase_owned_dirty_paths": ["owned.txt"],
                    "unowned_dirty_paths": unowned_dirty_paths or [],
                    "pre_existing_dirty_paths": [],
                    "phase_owned_dirty": True,
                },
                "closeout": closeout,
                "terminal_summary": {
                    "terminal_status": "complete",
                    "terminal_blocker": None,
                    "verification_status": verification_status,
                    "dirty_paths": ["owned.txt"],
                    "phase_owned_dirty": True,
                    "phase_owned_dirty_paths": ["owned.txt"],
                    "unowned_dirty_paths": unowned_dirty_paths or [],
                    "pre_existing_dirty_paths": [],
                },
            },
            **event_provenance(roadmap, "ALPHA"),
        )

    def test_planned_event_with_none_blocker_sentinel_is_not_a_blocker(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            events_path = repo / ".phase-loop" / "events.jsonl"
            events_path.parent.mkdir(parents=True)
            event = {
                "timestamp": utc_now(),
                "repo": str(repo),
                "roadmap": str(roadmap),
                "phase": "ALPHA",
                "action": "manual",
                "status": "planned",
                "model": "gpt-5.6-terra",
                "reasoning_effort": "medium",
                "source": "manual",
                "human_required": False,
                "blocker_class": "none",
                "blocker_summary": "none",
                "required_human_inputs": [],
                **event_provenance(roadmap, "ALPHA"),
            }
            events_path.write_text(json.dumps(event) + "\n", encoding="utf-8")
            write_phase_plan(repo, "ALPHA", roadmap)

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.phases["ALPHA"], "planned")
            self.assertFalse(snapshot.human_required)
            self.assertIsNone(snapshot.blocker_class)
            self.assertIsNone(snapshot.blocker_summary)

    def test_manual_repair_planned_event_can_clear_blocked_phase(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "ALPHA", roadmap)
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="ALPHA",
                    action="execute",
                    status="blocked",
                    model="gpt-5.6-terra",
                    reasoning_effort="medium",
                    source="fixture",
                    metadata={
                        "terminal_summary": {
                            "terminal_status": "blocked",
                            "verification_status": "blocked",
                            "next_action": "Repair the stale planning blocker.",
                            "dirty_paths": [],
                            "phase_owned_dirty": False,
                        }
                    },
                    **event_provenance(roadmap, "ALPHA"),
                ),
            )
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="ALPHA",
                    action="manual_repair",
                    status="planned",
                    model="gpt-5.6-terra",
                    reasoning_effort="medium",
                    source="manual",
                    metadata={"manual_repair": {"clears_blocker": True}},
                    **event_provenance(roadmap, "ALPHA"),
                ),
            )

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.phases["ALPHA"], "planned")
            self.assertFalse(snapshot.human_required)
            self.assertIsNone(snapshot.blocker_class)
            self.assertIsNone(snapshot.terminal_summary)

    def test_clean_planned_artifact_supersedes_mismatched_nonhuman_blocker_repair(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(
                repo,
                "ALPHA",
                roadmap,
                body=(
                    "# ALPHA\n\n"
                    "## Lanes\n\n"
                    "### SL-0 - ALPHA\n"
                    "- **Owned files**: none\n\n"
                    "```yaml\n"
                    "automation:\n"
                    "  status: planned\n"
                    "  next_skill: codex-execute-phase\n"
                    "  next_command: codex-execute-phase plans/phase-plan-v1-ALPHA.md\n"
                    "  human_required: false\n"
                    "  blocker_class: none\n"
                    "  blocker_summary: none\n"
                    "  required_human_inputs: []\n"
                    "  verification_status: not_run\n"
                    "```\n"
                ),
            )
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add alpha plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="ALPHA",
                    action="run",
                    status="blocked",
                    model="gpt-5.6-terra",
                    reasoning_effort="medium",
                    source="fixture",
                    blocker={
                        "human_required": False,
                        "blocker_class": "repeated_verification_failure",
                        "blocker_summary": "stale child closeout blocker",
                        "required_human_inputs": (),
                    },
                    **event_provenance(roadmap, "ALPHA"),
                ),
            )
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="ALPHA",
                    action="manual_repair",
                    status="planned",
                    model="manual",
                    reasoning_effort="none",
                    source="manual_repair",
                    metadata={"manual_repair": {"clears_blocker": True}},
                    roadmap_sha256=event_provenance(roadmap, "ALPHA")["roadmap_sha256"],
                    phase_sha256="stale-plan-derived-sha",
                ),
            )

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.phases["ALPHA"], "planned")
            self.assertFalse(snapshot.human_required)
            self.assertIsNone(snapshot.blocker_class)
            self.assertIsNone(snapshot.terminal_summary)
            self.assertTrue(any(warning["reason"] == "clean_plan_superseded_nonhuman_blocker" for warning in snapshot.ledger_warnings))

    def test_manual_repair_complete_clears_superseded_blocked_terminal_summary(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(repo, provenanced_event(repo, roadmap, "ALPHA", "complete", action="execute"))
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="BETA",
                    action="execute",
                    status="blocked",
                    model="gpt-5.6-terra",
                    reasoning_effort="medium",
                    source="fixture",
                    metadata={
                        "terminal_summary": {
                            "phase": "BETA",
                            "terminal_status": "blocked",
                            "verification_status": "blocked",
                            "next_action": "Repair the recorded blocker before rerunning the loop.",
                            "dirty_paths": [],
                            "phase_owned_dirty": False,
                        }
                    },
                    **event_provenance(roadmap, "BETA"),
                ),
            )
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="BETA",
                    action="manual_repair",
                    status="complete",
                    model="gpt-5.6-terra",
                    reasoning_effort="medium",
                    source="operator",
                    metadata={"manual_repair": {"clears_blocker": True}},
                    **event_provenance(roadmap, "BETA"),
                ),
            )

            snapshot = reconcile(repo, roadmap)

            self.assertIsNone(snapshot.current_phase)
            self.assertEqual(snapshot.phases["BETA"], "complete")
            self.assertIsNone(snapshot.terminal_summary)
            self.assertFalse(snapshot.human_required)
            self.assertIsNone(snapshot.blocker_class)

    def test_clean_manual_repair_complete_supersedes_later_missing_closeout_blocker(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(repo, provenanced_event(repo, roadmap, "ALPHA", "complete", action="execute"))
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="BETA",
                    action="execute",
                    status="blocked",
                    model="gpt-5.6-terra",
                    reasoning_effort="medium",
                    source="fixture",
                    blocker={
                        "human_required": False,
                        "blocker_class": "repeated_verification_failure",
                        "blocker_summary": "Codex live launch for BETA exited successfully but did not emit a valid shared automation closeout.",
                        "required_human_inputs": (),
                        "access_attempts": (),
                    },
                    metadata={
                        "terminal_summary": {
                            "phase": "BETA",
                            "terminal_status": "blocked",
                            "verification_status": "blocked",
                            "dirty_paths": [],
                        }
                    },
                    **event_provenance(roadmap, "BETA"),
                ),
            )
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="BETA",
                    action="manual_repair",
                    status="complete",
                    model="manual",
                    reasoning_effort="none",
                    source="manual_repair",
                    metadata={
                        "manual_repair": {
                            "clears_blocker": True,
                            "closeout_action": "commit",
                            "closeout_commit": "abc123",
                            "verification_status": "passed",
                        },
                        "terminal_summary": {
                            "phase": "BETA",
                            "terminal_status": "complete",
                            "verification_status": "passed",
                            "dirty_paths": [],
                        },
                    },
                    **event_provenance(roadmap, "BETA"),
                ),
            )
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="BETA",
                    action="run",
                    status="blocked",
                    model="gpt-5.6-terra",
                    reasoning_effort="medium",
                    source="fixture",
                    blocker={
                        "human_required": False,
                        "blocker_class": "repeated_verification_failure",
                        "blocker_summary": "Codex live launch for BETA exited successfully but did not emit a valid shared automation closeout.",
                        "required_human_inputs": (),
                        "access_attempts": (),
                    },
                    metadata={
                        "terminal_summary": {
                            "phase": "BETA",
                            "terminal_status": "blocked",
                            "verification_status": "blocked",
                            "dirty_paths": [],
                        }
                    },
                    **event_provenance(roadmap, "BETA"),
                ),
            )

            snapshot = reconcile(repo, roadmap)

            self.assertIsNone(snapshot.current_phase)
            self.assertEqual(snapshot.phases["BETA"], "complete")
            self.assertFalse(snapshot.human_required)
            self.assertIsNone(snapshot.blocker_class)
            self.assertEqual(snapshot.closeout_summary["closeout_commit"], "abc123")
            self.assertTrue(
                any(
                    warning["reason"] == "clean_manual_repair_superseded_nonhuman_blocker"
                    for warning in snapshot.ledger_warnings
                )
            )

    def test_clean_manual_repair_complete_supersedes_later_dirty_closeout_blocker(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(repo, provenanced_event(repo, roadmap, "ALPHA", "complete", action="execute"))
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="BETA",
                    action="manual_repair",
                    status="complete",
                    model="manual",
                    reasoning_effort="none",
                    source="manual_repair",
                    metadata={
                        "manual_repair": {
                            "clears_blocker": True,
                            "closeout_action": "commit",
                            "closeout_commit": "abc123",
                            "verification_status": "passed",
                        },
                    },
                    **event_provenance(roadmap, "BETA"),
                ),
            )
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="BETA",
                    action="run",
                    status="blocked",
                    model="gpt-5.6-terra",
                    reasoning_effort="medium",
                    source="fixture",
                    blocker={
                        "human_required": False,
                        "blocker_class": "dirty_worktree_conflict",
                        "blocker_summary": (
                            "Phase reported complete but left dirty paths that are not closeout-safe. "
                            "(ownership evidence failed closed: overlapping_write_ownership:SL-3)"
                        ),
                        "required_human_inputs": (),
                        "access_attempts": (),
                    },
                    metadata={
                        "terminal_summary": {
                            "phase": "BETA",
                            "terminal_status": "blocked",
                            "verification_status": "blocked",
                            "dirty_paths": [],
                        }
                    },
                    **event_provenance(roadmap, "BETA"),
                ),
            )

            snapshot = reconcile(repo, roadmap)

            self.assertIsNone(snapshot.current_phase)
            self.assertEqual(snapshot.phases["BETA"], "complete")
            self.assertFalse(snapshot.human_required)
            self.assertIsNone(snapshot.blocker_class)
            self.assertEqual(snapshot.closeout_summary["closeout_commit"], "abc123")

    def test_clean_manual_repair_complete_supersedes_later_legacy_metadata_blocker(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(repo, provenanced_event(repo, roadmap, "ALPHA", "complete", action="execute"))
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="BETA",
                    action="manual_repair",
                    status="complete",
                    model="manual",
                    reasoning_effort="none",
                    source="manual_repair",
                    metadata={
                        "manual_repair": {
                            "clears_blocker": True,
                            "closeout_action": "commit",
                            "closeout_commit": "abc123",
                            "verification_status": "passed",
                        },
                    },
                    **event_provenance(roadmap, "BETA"),
                ),
            )
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="BETA",
                    action="run",
                    status="blocked",
                    model="claude-opus-4-8",
                    reasoning_effort="medium",
                    source="fixture",
                    blocker={
                        "human_required": False,
                        "blocker_class": "repeated_verification_failure",
                        "blocker_summary": "Legacy Claude closeout reported a blocked outcome without the shared blocker metadata.",
                        "required_human_inputs": (),
                        "access_attempts": (),
                    },
                    **event_provenance(roadmap, "BETA"),
                ),
            )

            snapshot = reconcile(repo, roadmap)

            self.assertIsNone(snapshot.current_phase)
            self.assertEqual(snapshot.phases["BETA"], "complete")
            self.assertFalse(snapshot.human_required)
            self.assertIsNone(snapshot.blocker_class)
            self.assertEqual(snapshot.closeout_summary["closeout_commit"], "abc123")

    def test_normal_planned_event_does_not_clear_blocked_phase(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(repo, provenanced_event(repo, roadmap, "ALPHA", "blocked", action="execute"))
            append_event(repo, provenanced_event(repo, roadmap, "ALPHA", "planned", action="manual"))

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.phases["ALPHA"], "blocked")

    def test_automation_event_with_none_blocker_sentinel_is_not_a_blocker(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "ALPHA", roadmap)
            events_path = repo / ".phase-loop" / "events.jsonl"
            events_path.parent.mkdir(parents=True)
            event = {
                "timestamp": utc_now(),
                "source": "manual",
                "automation": {
                    "status": "planned",
                    "artifact": str(plan),
                    "human_required": False,
                    "blocker_class": "none",
                    "blocker_summary": "none",
                    "required_human_inputs": [],
                },
            }
            events_path.write_text(json.dumps(event) + "\n", encoding="utf-8")

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.phases["ALPHA"], "planned")
            self.assertFalse(snapshot.human_required)
            self.assertIsNone(snapshot.blocker_class)
            self.assertIsNone(snapshot.blocker_summary)

    def test_later_trusted_blocked_event_clears_stale_blocker(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            plan = write_phase_plan(repo, "ALPHA", roadmap)
            events_path = repo / ".phase-loop" / "events.jsonl"
            events_path.parent.mkdir(parents=True)
            events_path.write_text(
                (
                    '{"timestamp":"2026-04-24T00:00:00Z","source":"manual","automation":'
                    '{"status":"blocked","artifact":"'
                    + str(plan)
                    + '","human_required":true,"blocker_class":"dirty_worktree_conflict",'
                    '"blocker_summary":"Clean worktree required.","required_human_inputs":["clean worktree"]}}\n'
                ),
                encoding="utf-8",
            )
            append_event(repo, provenanced_event(repo, roadmap, "ALPHA", "blocked", action="run"))
            snapshot = reconcile(repo, roadmap)
            self.assertEqual(snapshot.phases["ALPHA"], "blocked")
            self.assertFalse(snapshot.human_required)
            self.assertIsNone(snapshot.blocker_class)

    def test_parent_executed_event_preserves_child_human_blocker(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            blocker = {
                "human_required": True,
                "blocker_class": "dirty_worktree_conflict",
                "blocker_summary": "Release dispatch requires a clean worktree.",
                "required_human_inputs": ("commit or clear release changes",),
            }
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="ALPHA",
                    action="manual",
                    status="executed",
                    model="gpt-5.6-terra",
                    reasoning_effort="medium",
                    source="manual",
                    blocker=blocker,
                    **event_provenance(roadmap, "ALPHA"),
                ),
            )
            append_event(repo, provenanced_event(repo, roadmap, "ALPHA", "executed", action="run"))

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.phases["ALPHA"], "executed")
            self.assertTrue(snapshot.human_required)
            self.assertEqual(snapshot.blocker_class, "dirty_worktree_conflict")
            self.assertEqual(snapshot.required_human_inputs, ("commit or clear release changes",))

    def test_blocked_plan_automation_supplies_current_blocker_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            write_phase_plan(
                repo,
                "ALPHA",
                roadmap,
                body=(
                    "```yaml\n"
                    "automation:\n"
                    "  status: blocked\n"
                    "  human_required: true\n"
                    "  blocker_class: branch_sync_conflict\n"
                    "  blocker_summary: local main is ahead of origin/main\n"
                    "  required_human_inputs:\n"
                    "    - align main with origin/main\n"
                    "```\n"
                ),
            )
            snapshot = reconcile(repo, roadmap)
            self.assertEqual(snapshot.phases["ALPHA"], "blocked")
            self.assertTrue(snapshot.human_required)
            self.assertEqual(snapshot.blocker_class, "branch_sync_conflict")
            self.assertEqual(snapshot.required_human_inputs, ("align main with origin/main",))

    def test_blocked_phase_takes_current_priority_over_earlier_unplanned_phase(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="BETA",
                    action="execute",
                    status="blocked",
                    model="gpt-5.6-terra",
                    reasoning_effort="medium",
                    source="fixture",
                    blocker={
                        "human_required": True,
                        "blocker_class": "dirty_worktree_conflict",
                        "blocker_summary": "Clean worktree required.",
                        "required_human_inputs": ("clean worktree",),
                    },
                    **event_provenance(roadmap, "BETA"),
                ),
            )

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.phases["ALPHA"], "unplanned")
            self.assertEqual(snapshot.phases["BETA"], "blocked")
            self.assertEqual(snapshot.current_phase, "BETA")
            self.assertTrue(snapshot.human_required)

    def test_blocked_plan_metadata_is_preserved_without_human_required(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            write_phase_plan(
                repo,
                "ALPHA",
                roadmap,
                body=(
                    "```yaml\n"
                    "automation:\n"
                    "  status: blocked\n"
                    "  human_required: false\n"
                    "  blocker_class: dirty_worktree_conflict\n"
                    "  blocker_summary: plan repair required before execution\n"
                    "  required_human_inputs: []\n"
                    "```\n"
                ),
            )
            snapshot = reconcile(repo, roadmap)
            self.assertEqual(snapshot.phases["ALPHA"], "blocked")
            self.assertFalse(snapshot.human_required)
            self.assertEqual(snapshot.blocker_class, "dirty_worktree_conflict")
            self.assertEqual(snapshot.blocker_summary, "plan repair required before execution")

    def test_phase_reopen_event_clears_prior_terminal_summary(self):
        """phase_reopen is explicit operator intent to discard a stuck terminal
        state. The reducer must clear the prior terminal_summary so the next
        `phase-loop run` re-dispatches instead of replaying the stale closeout.

        Before this was made explicit, the same effect happened incidentally
        via the planned-status branch — but a future refactor of the elif
        ordering could silently break the recoverable-blocker recovery path.
        """
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            # 1. An executor reports a blocked closeout carrying a real terminal_summary.
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="RUNNER",
                    action="run",
                    status="blocked",
                    model="gpt-5.6-sol",
                    reasoning_effort="medium",
                    source="fixture",
                    metadata={
                        "terminal_summary": {
                            "terminal_status": "blocked",
                            "verification_status": "blocked",
                            "dirty_paths": ["src/foo.py"],
                            "blocker_class": "missing_secret",
                        },
                    },
                    **event_provenance(roadmap, "RUNNER"),
                ),
            )
            pre_snapshot = reconcile(repo, roadmap)
            self.assertEqual(pre_snapshot.phases["RUNNER"], "blocked")
            self.assertIsNotNone(pre_snapshot.terminal_summary)

            # 2. Operator runs `phase-loop reopen --phase RUNNER --reason "SSO refreshed"`.
            # That synthesizes a planned phase_reopen event.
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="RUNNER",
                    action="phase_reopen",
                    status="planned",
                    model=None,
                    reasoning_effort=None,
                    source="fixture",
                    metadata={
                        "phase_reopen": {
                            "reason": "SSO refreshed",
                            "prior_status": "blocked",
                            "prior_closeout_commit": "abc123",
                            "reopen_commit": "def456",
                        },
                    },
                    **event_provenance(roadmap, "RUNNER"),
                ),
            )

            post_snapshot = reconcile(repo, roadmap)

            self.assertEqual(post_snapshot.phases["RUNNER"], "planned")
            # The explicit phase_reopen branch in _event_clears_terminal_summary
            # must clear the stale blocked-event terminal_summary.
            self.assertIsNone(
                post_snapshot.terminal_summary,
                msg=(
                    "phase_reopen must clear prior terminal_summary so the next "
                    f"`phase-loop run` re-dispatches; got {post_snapshot.terminal_summary!r}"
                ),
            )


if __name__ == "__main__":
    unittest.main()
