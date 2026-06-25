import subprocess
import tempfile
import unittest
import json
import os
import hashlib
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.events import append_event
from phase_loop_runtime.events import read_events
from phase_loop_runtime.closeout import build_phase_loop_closeout, phase_loop_closeout_diagnostic
from phase_loop_runtime.launcher import LaunchResult
from phase_loop_runtime.models import (
    DelegationBudget,
    DelegationRequest,
    HarnessLaneAssignment,
    LoopEvent,
    PHASE_STATUSES,
    PipelinePlanMetadata,
    PhaseSourceBundle,
    PipelineProtectedSource,
    StateSnapshot,
    utc_now,
)
from phase_loop_runtime.observability import read_work_unit_metrics
from phase_loop_runtime.prompts import build_prompt
from phase_loop_runtime.provenance import event_provenance
from phase_loop_runtime.runtime_paths import phase_loop_stop_file
from phase_loop_runtime.runner import _build_repair_context, _classify_dirty_paths, _detect_dirty_renames, _write_deterministic_closeout, launch_delegated_child, launch_harness_lane_work_unit, run_loop, status_snapshot
from phase_loop_runtime.runner import _ensure_pipeline_branch_before_dispatch
from phase_loop_runtime.state import load_work_unit_state, write_state
from phase_loop_runtime.state_degradation import load_degradation
from phase_loop_smoke_utils import (
    claude_team_live_smoke_enabled,
    enabled_live_smoke_executors,
    make_live_team_fixture,
    make_mixed_harness_live_fixture,
    run_live_smoke,
)
from phase_loop_test_utils import (
    FAKE_EXECUTORS,
    assert_metadata_only_evidence_refs,
    build_fake_automation_output,
    build_fake_delegation_request,
    commit_fixture_paths,
    make_fake_launch_result,
    make_code_index_blocker_fixture,
    make_greenfield_closeout_fixture,
    make_message_board_fixture,
    make_regenesis_amendment_fixture,
    make_repo,
    provenanced_event,
    provenanced_state,
    validate_fake_executor_matrix,
    validate_dffakesmoke_fake_smoke_matrix,
    write_phase_plan,
)
from test_phase_loop_pipeline_bundle import _write_bundle, _write_protected_source


def _migration_wave_body() -> str:
    return (
        "# MIGRATELOOP\n\n"
        "## Lane Index & Dependencies\n\n"
        "- SL-0 - Producer; Depends on: (none); Blocks: SL-1; Parallel-safe: yes\n"
        "- SL-1 - Reducer; Depends on: SL-0; Blocks: (none); Parallel-safe: no\n\n"
        "## Lanes\n\n"
        "### SL-0 - Producer\n"
        "- **Owned files**: `producer.py`\n"
        "- **Interfaces provided**: `producer.out`\n"
        "- **Interfaces consumed**: none\n"
        "- **Parallel-safe**: yes\n\n"
        "### SL-1 - Reducer\n"
        "- **Owned files**: none\n"
        "- **Interfaces provided**: `done.out`\n"
        "- **Interfaces consumed**: `producer.out`\n"
        "- **Parallel-safe**: no\n"
    )


class PhaseLoopRunnerTest(unittest.TestCase):
    def test_pipeline_branch_governance_uses_explicit_base_ref(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            (repo / ".pipeline").mkdir()
            roadmap = repo / "specs" / "phase-plans-v44.md"
            roadmap.parent.mkdir(parents=True, exist_ok=True)
            roadmap.write_text("# Roadmap\n", encoding="utf-8")

            with (
                patch.dict(os.environ, {"PHASE_LOOP_BASE_REF": "origin/consiliency/pipeline/v42"}),
                patch("phase_loop_runtime.pipeline_adapter.branch_ops.ensure_pipeline_branch", return_value="consiliency/pipeline/v44") as fake_ensure,
            ):
                blocker, _decision = _ensure_pipeline_branch_before_dispatch(repo, roadmap)

            self.assertIsNone(blocker)
            fake_ensure.assert_called_once_with(
                repo,
                "v44",
                "main",
                base_ref="origin/consiliency/pipeline/v42",
                base_already_fetched=False,
            )

    def test_pipeline_branch_governance_uses_base_version_for_suffix_roadmap(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            (repo / ".pipeline").mkdir()
            roadmap = repo / "specs" / "phase-plans-v44-claude-primary-harness-integration.md"
            roadmap.parent.mkdir(parents=True, exist_ok=True)
            roadmap.write_text("# Roadmap\n", encoding="utf-8")

            with patch(
                "phase_loop_runtime.pipeline_adapter.branch_ops.ensure_pipeline_branch",
                return_value="consiliency/pipeline/v44",
            ) as fake_ensure:
                blocker, _decision = _ensure_pipeline_branch_before_dispatch(repo, roadmap)

            self.assertIsNone(blocker)
            fake_ensure.assert_called_once_with(
                repo,
                "v44",
                "main",
                base_ref=None,
                base_already_fetched=False,
            )

    def test_pipeline_branch_governance_uses_current_pipeline_upstream_base(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            (repo / ".pipeline").mkdir()
            roadmap = repo / "specs" / "phase-plans-v44-claude-primary-harness-integration.md"
            roadmap.parent.mkdir(parents=True, exist_ok=True)
            roadmap.write_text("# Roadmap\n", encoding="utf-8")
            subprocess.run(["git", "remote", "add", "origin", str(repo)], cwd=repo, check=True)
            subprocess.run(["git", "update-ref", "refs/remotes/origin/main", "HEAD"], cwd=repo, check=True)
            subprocess.run(
                ["git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"],
                cwd=repo,
                check=True,
            )
            subprocess.run(["git", "checkout", "-b", "consiliency/pipeline/v44"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(
                ["git", "update-ref", "refs/remotes/origin/consiliency/pipeline/v44", "HEAD"],
                cwd=repo,
                check=True,
            )
            subprocess.run(
                ["git", "config", "branch.consiliency/pipeline/v44.remote", "origin"],
                cwd=repo,
                check=True,
            )
            subprocess.run(
                ["git", "config", "branch.consiliency/pipeline/v44.merge", "refs/heads/consiliency/pipeline/v44"],
                cwd=repo,
                check=True,
            )

            with patch(
                "phase_loop_runtime.pipeline_adapter.branch_ops.ensure_pipeline_branch",
                return_value="consiliency/pipeline/v44",
            ) as fake_ensure:
                blocker, _decision = _ensure_pipeline_branch_before_dispatch(repo, roadmap)

            self.assertIsNone(blocker)
            fake_ensure.assert_called_once_with(
                repo,
                "v44",
                "main",
                base_ref="origin/consiliency/pipeline/v44",
                base_already_fetched=False,
            )

    def test_repair_context_and_prompt_include_previous_phase_owned_paths(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, owned_files=("README.md",))
            snapshot = StateSnapshot(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phases={"RUNNER": "blocked"},
                current_phase="RUNNER",
                dirty_paths=("README.md",),
                phase_owned_dirty_paths=(),
                previous_phase_owned_paths=("README.md",),
                unowned_dirty_paths=(),
                pre_existing_dirty_paths=(),
                phase_owned_dirty=True,
                terminal_summary={
                    "phase": "RUNNER",
                    "terminal_status": "blocked",
                    "verification_status": "blocked",
                    "next_action": "Repair previous execution.",
                    "dirty_paths": ["README.md"],
                    "previous_phase_owned_paths": ["README.md"],
                },
            )

            context, missing = _build_repair_context(repo, "RUNNER", plan, snapshot)
            self.assertEqual(missing, [])
            self.assertEqual(context["previous_phase_owned_paths"], ["README.md"])
            prompt = build_prompt(
                "repair",
                roadmap,
                phase="RUNNER",
                plan=plan,
                blocker_summary="dirty output from previous attempt",
                repair_context=context,
            ).render_prompt()

            self.assertIn("previous_phase_owned_paths=README.md", prompt)
            self.assertIn("Continue or restart the previous execute attempt", prompt)
            self.assertIn("do not treat these as unrelated dirty files", prompt)

    def test_closeout_commit_uses_previous_phase_owned_paths_as_continuation(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, owned_files=("README.md",))
            commit_fixture_paths(repo, "add runner plan", plan)
            (repo / "README.md").write_text("previous execute output\n", encoding="utf-8")
            snapshot = replace(
                provenanced_state(repo, roadmap, {"RUNNER": "awaiting_phase_closeout"}),
                current_phase="RUNNER",
                dirty_paths=("README.md",),
                phase_owned_dirty_paths=(),
                previous_phase_owned_paths=("README.md",),
                phase_owned_dirty=True,
                closeout_terminal_status="executed",
            )
            write_state(repo, snapshot)

            result, launches = run_loop(repo, roadmap, phase="RUNNER", closeout_mode="commit")

            self.assertEqual(launches, [])
            self.assertEqual(result.phases["RUNNER"], "complete")
            self.assertEqual(
                subprocess.check_output(["git", "-C", str(repo), "log", "-1", "--format=%s"], text=True).strip(),
                "phase-loop continuation: RUNNER",
            )
            self.assertEqual(subprocess.check_output(["git", "-C", str(repo), "status", "--short"], text=True).strip(), "")

    def _latest_event_with_metadata(self, events: list[dict], key: str) -> dict:
        for event in reversed(events):
            if key in event.get("metadata", {}):
                return event
        raise AssertionError(f"expected event metadata key {key}")

    def _latest_event_with_action(self, events: list[dict], action: str) -> dict:
        for event in reversed(events):
            if event.get("action") == action:
                return event
        raise AssertionError(f"expected event action {action}")

    def test_standalone_harness_closeout_includes_advisory_impact_hints(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(
                repo,
                "RUNNER",
                roadmap,
                owned_files=("docs/phase-loop/contract-map.md", "specs/phase-plans-v1.md"),
            )
            assignment = HarnessLaneAssignment(
                phase="RUNNER",
                lane_id="SL-0",
                work_unit_kind="lane_execute",
                owned_files=("docs/phase-loop/contract-map.md", "specs/phase-plans-v1.md"),
            )

            outcome = launch_harness_lane_work_unit(repo=repo, roadmap=roadmap, plan=plan, assignment=assignment, dry_run=True)

            closeout = outcome["terminal_summary"]["phase_loop_closeout"]
            impact = closeout["source_truth_impact"]
            self.assertEqual(closeout["source_bundle"], {"pipeline_mode": "standalone"})
            self.assertTrue(impact["canonical_refresh_recommended"])
            self.assertIn("docs_source_truth_touched", impact["canonical_refresh_reason_codes"])
            self.assertIn("unmanaged_specs_touched", impact["canonical_refresh_reason_codes"])
            self.assertIn("contract_refs_touched", impact["canonical_refresh_reason_codes"])
            categories = {item["path"]: item["category"] for item in impact["changed_path_boundaries"]}
            self.assertEqual(categories["specs/phase-plans-v1.md"], "unmanaged_spec")

    def test_pipeline_required_harness_closeout_includes_advisory_impact_hints(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            _write_protected_source(repo)
            roadmap_sha = hashlib.sha256(roadmap.read_bytes()).hexdigest()
            bundle_path = _write_bundle(
                repo,
                protected_entries=[
                    {
                        "path": "specs/phase-plans-v1.md",
                        "category": "specs",
                        "sha256": roadmap_sha,
                        "role": "managed_mirror_file",
                    }
                ],
                pipeline_mode="pipeline_required",
            )
            bundle_sha = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
            plan = write_phase_plan(
                repo,
                "RUNNER",
                roadmap,
                extra_frontmatter={
                    "source_bundle": ".pipeline/artifacts/phase-source-bundle.json",
                    "source_bundle_sha256": bundle_sha,
                    "pipeline_phase_id": "pipeline.phase.runner",
                    "pipeline_mode": "pipeline_required",
                },
                owned_files=("specs/phase-plans-v1.md",),
            )
            assignment = HarnessLaneAssignment(
                phase="RUNNER",
                lane_id="SL-0",
                work_unit_kind="lane_execute",
                owned_files=("specs/phase-plans-v1.md",),
            )

            outcome = launch_harness_lane_work_unit(repo=repo, roadmap=roadmap, plan=plan, assignment=assignment, dry_run=True)

            closeout = outcome["terminal_summary"]["phase_loop_closeout"]
            impact = closeout["source_truth_impact"]
            self.assertEqual(closeout["source_bundle"]["pipeline_mode"], "pipeline_required")
            self.assertTrue(impact["canonical_refresh_recommended"])
            self.assertIn("managed_mirror_specs_touched", impact["canonical_refresh_reason_codes"])
            self.assertEqual(impact["changed_path_boundaries"][0]["category"], "managed_root_mirror_spec")

    def test_standalone_non_default_spec_roots_are_advisory_only_when_explicit(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)

            unconfigured = build_phase_loop_closeout(
                phase_alias="RUNNER",
                plan_path=plan,
                terminal_summary={"terminal_status": "complete", "verification_status": "passed"},
                automation={"status": "complete", "verification_status": "passed", "human_required": False},
                changed_paths=("Specs/proposed-runner.md",),
            )

            unconfigured_impact = unconfigured["source_truth_impact"]
            self.assertEqual(unconfigured["source_bundle"], {"pipeline_mode": "standalone"})
            self.assertFalse(unconfigured_impact["canonical_refresh_recommended"])
            self.assertEqual(unconfigured_impact["changed_path_boundaries"][0]["category"], "unknown")

            explicit_context = PhaseSourceBundle(
                path=".pipeline/artifacts/phase-source-bundle.json",
                sha256="a" * 64,
                phase_id="pipeline.phase.runner",
                phase_alias="RUNNER",
                phase_plan_path="plans/phase-plan-v1-RUNNER.md",
                roadmap_path="specs/phase-plans-v1.md",
                roadmap_sha256="b" * 64,
                protected_sources=(
                    PipelineProtectedSource(
                        path="Specs/proposed-runner.md",
                        category="specs",
                        sha256="c" * 64,
                        role="adapter_configured_intake_root",
                    ),
                ),
                pipeline_mode="standalone",
            )
            explicit = build_phase_loop_closeout(
                phase_alias="RUNNER",
                plan_path=plan,
                source_bundle=explicit_context,
                terminal_summary={"terminal_status": "complete", "verification_status": "passed"},
                automation={"status": "complete", "verification_status": "passed", "human_required": False},
                changed_paths=("Specs/proposed-runner.md",),
            )

            explicit_impact = explicit["source_truth_impact"]
            self.assertTrue(explicit_impact["canonical_refresh_recommended"])
            self.assertIn("unmanaged_specs_touched", explicit_impact["canonical_refresh_reason_codes"])
            self.assertEqual(explicit_impact["changed_path_boundaries"][0]["category"], "unmanaged_spec")
            self.assertEqual(explicit["source_bundle"]["pipeline_mode"], "standalone")

    def test_event_metadata_extracts_advisory_impact_summary(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            closeout = build_phase_loop_closeout(
                phase_alias="RUNNER",
                plan_path=plan,
                terminal_summary={"terminal_status": "complete", "verification_status": "passed"},
                automation={"status": "complete", "verification_status": "passed", "human_required": False},
                changed_paths=("docs/phase-loop/contract-map.md",),
            )

            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="RUNNER",
                    action="execute",
                    status="complete",
                    model="gpt-5.5",
                    reasoning_effort="medium",
                    source="fixture",
                    metadata={"phase_loop_closeout": closeout},
                ),
            )

            metadata = read_events(repo)[-1]["metadata"]
            self.assertTrue(metadata["canonical_refresh_recommended"])
            self.assertIn("docs_source_truth_touched", metadata["canonical_refresh_reason_codes"])
            self.assertEqual(metadata["phase_loop_closeout"], closeout)
            self.assertEqual(metadata["changed_path_boundaries"][0]["category"], "docs")
            self.assertIn("docs", metadata["changed_path_categories"])

    def test_deterministic_closeout_output_carries_impact_hints_for_phase_owned_dirty_paths(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, owned_files=("README.md",))
            commit_fixture_paths(repo, "add runner plan", plan)
            (repo / "README.md").write_text("fixture changed\n", encoding="utf-8")
            output = repo / "closeout.json"

            _write_deterministic_closeout(repo, roadmap, status_snapshot(repo, roadmap), output, override_phase="RUNNER")

            closeout = json.loads(output.read_text(encoding="utf-8"))
            impact = closeout["source_truth_impact"]
            self.assertEqual(closeout["artifacts"]["changed_paths"], ["README.md"])
            self.assertTrue(impact["canonical_refresh_recommended"])
            self.assertIn("docs_source_truth_touched", impact["canonical_refresh_reason_codes"])

    def test_dfparsoak_closeout_reduction_outcomes_and_redaction_boundary(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "DFPARSOAK", roadmap, owned_files=("docs/phase-loop/dfparsoak-substrate-soak.md",))

            scenarios = (
                (
                    "complete",
                    {"terminal_status": "complete", "verification_status": "passed"},
                    {},
                    "complete",
                ),
                (
                    "failed verification",
                    {"terminal_status": "blocked", "verification_status": "failed"},
                    {"blocker_class": "repeated_verification_failure", "blocker_summary": "DFPARSOAK verification failed."},
                    "failed_verification",
                ),
                (
                    "human blocker",
                    {"terminal_status": "blocked", "verification_status": "blocked"},
                    {"human_required": True, "blocker_class": "admin_approval", "blocker_summary": "Operator approval required."},
                    "human_required",
                ),
                (
                    "stale input",
                    {"terminal_status": "blocked", "verification_status": "blocked"},
                    {"blocker_class": "contract_bug", "blocker_summary": "source_bundle receipt digest changed."},
                    "stale_input",
                ),
                (
                    "dirty worktree blocker",
                    {"terminal_status": "blocked", "verification_status": "blocked", "dirty_paths": ["docs/phase-loop/dfparsoak-substrate-soak.md"]},
                    {"blocker_class": "dirty_worktree_conflict", "blocker_summary": "Unowned generated output remained after DFPARSOAK."},
                    "blocked",
                ),
            )
            for label, terminal_summary, blocker, expected in scenarios:
                with self.subTest(label=label):
                    payload = build_phase_loop_closeout(
                        phase_alias="DFPARSOAK",
                        plan_path=plan,
                        plan_metadata=PipelinePlanMetadata(
                            source_bundle="none",
                            source_bundle_sha256="6" * 64,
                            pipeline_phase_id="dotfiles-v13-DFPARSOAK",
                            pipeline_mode="standalone",
                        ),
                        terminal_summary=terminal_summary,
                        automation={
                            "status": "complete" if expected == "complete" else "blocked",
                            "next_skill": "none",
                            "next_command": "none",
                            "next_model_hint": "none",
                            "human_required": bool(blocker.get("human_required", False)),
                            "blocker_class": blocker.get("blocker_class"),
                            "blocker_summary": blocker.get("blocker_summary"),
                            "required_human_inputs": [],
                            "verification_status": terminal_summary["verification_status"],
                            "artifact": str(plan),
                            "artifact_state": "tracked",
                        },
                        blocker=blocker,
                        changed_paths=("docs/phase-loop/dfparsoak-substrate-soak.md",),
                        evidence_refs=(
                            {
                                "path": ".phase-loop/runs/dfparsoak-wave-001/terminal-summary.json",
                                "sha256": "3" * 64,
                            },
                        ),
                    )

                    self.assertIsNone(phase_loop_closeout_diagnostic(payload))
                    self.assertEqual(payload["terminal_status"], expected)
                    self.assertEqual(payload["assignment"] if "assignment" in payload else None, None)
                    assert_metadata_only_evidence_refs(self, payload["artifacts"]["evidence_refs"])

            malformed = {"schema": "phase_loop_closeout.v1", "phase": "DFPARSOAK"}
            diagnostic = phase_loop_closeout_diagnostic(malformed)
            self.assertIsNotNone(diagnostic)
            assert diagnostic is not None
            self.assertEqual(diagnostic["kind"], "malformed_closeout")

            with self.assertRaises(AssertionError):
                assert_metadata_only_evidence_refs(self, ("raw-log:provider-payload",))

    def test_fake_executor_matrix_contract_self_check(self):
        validate_fake_executor_matrix()
        validate_dffakesmoke_fake_smoke_matrix()
        matrix_path = Path(__file__).resolve().parent / "fixtures" / "phase_loop_dfparsoak" / "matrix.json"
        payload = json.loads(matrix_path.read_text(encoding="utf-8"))
        lanes = payload["lanes"]
        self.assertEqual(payload["schema"], "dfparsoak_soak_matrix.v1")
        self.assertGreaterEqual(len(lanes), 3)
        self.assertEqual({lane["worktree_assignment"]["isolation_mode"] for lane in lanes}, {"git_worktree"})
        self.assertEqual(len({lane["worktree_assignment"]["worktree_path"] for lane in lanes}), len(lanes))
        self.assertTrue({"pi", "claude", "codex", "gemini"}.issubset({lane["harness_route"] for lane in lanes}))
        for key in ("overlap_rejection", "stale_base_rejection", "redacted_evidence_refs"):
            self.assertTrue(payload["expectations"][key])
        serialized = json.dumps(payload).lower()
        self.assertNotIn("raw transcript", serialized)
        self.assertNotIn("provider payload", serialized)
        self.assertNotIn("credential value", serialized)
        self.assertNotIn("local env value", serialized)

    def test_delegated_child_denial_records_typed_blocked_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("allowed/*",))
            request = DelegationRequest(
                request_id="req-denied",
                product_action="review",
                target_executor="codex",
                reason="Review an unowned file",
                owned_files=("secret.txt",),
                expected_output="Review notes",
                budget=DelegationBudget(max_seconds=30),
            )

            outcome = launch_delegated_child(
                repo=repo,
                roadmap=roadmap,
                parent_phase="CONTRACT",
                parent_action="execute",
                plan=plan,
                request=request,
                dry_run=True,
            )

            self.assertEqual(outcome["decision"]["status"], "denied")
            self.assertEqual(outcome["decision"]["reason_code"], "owned_files_out_of_bounds")
            self.assertEqual(outcome["terminal_summary"]["terminal_status"], "blocked")
            self.assertTrue(outcome["terminal_summary"]["metric_id"])
            self.assertEqual(outcome["launch_metadata"]["delegation_request"]["request_id"], "req-denied")

    def test_delegated_child_approved_dry_run_records_lineage(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("notes.md",))
            request = DelegationRequest(
                request_id="req-approved",
                product_action="review",
                target_executor="codex",
                reason="Need a second harness review.",
                owned_files=("notes.md",),
                expected_output="Review findings",
                budget=DelegationBudget(max_seconds=60, max_tokens=500),
            )

            outcome = launch_delegated_child(
                repo=repo,
                roadmap=roadmap,
                parent_phase="CONTRACT",
                parent_action="execute",
                plan=plan,
                request=request,
                parent_run_id="run-parent-1",
                dry_run=True,
            )

            self.assertEqual(outcome["decision"]["status"], "approved")
            self.assertTrue(outcome["result"]["dry_run"])
            self.assertEqual(outcome["launch_metadata"]["parent_child"]["parent_run_id"], "run-parent-1")
            self.assertEqual(outcome["launch_metadata"]["parent_child"]["child_executor"], "codex")
            self.assertEqual(outcome["launch_metadata"]["parent_child"]["child_worktree_root"], str(repo.resolve()))
            self.assertEqual(outcome["launch_metadata"]["parent_child"]["child_closeout_result"]["status"], "planned")
            self.assertEqual(outcome["terminal_summary"]["terminal_status"], "planned")
            self.assertTrue(outcome["terminal_summary"]["metric_id"])

    def test_fake_review_delegation_requests_stay_typed_across_executors(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("notes.md",))

            for executor in ("codex", "claude"):
                with self.subTest(executor=executor):
                    request = build_fake_delegation_request(
                        request_id=f"req-{executor}",
                        target_executor=executor,
                    )
                    outcome = launch_delegated_child(
                        repo=repo,
                        roadmap=roadmap,
                        parent_phase="CONTRACT",
                        parent_action="execute",
                        parent_executor="codex",
                        plan=plan,
                        request=request,
                        parent_run_id="run-parent-fake",
                        dry_run=True,
                    )

                    self.assertEqual(outcome["decision"]["status"], "approved")
                    self.assertEqual(outcome["launch_metadata"]["delegation_request"]["request_id"], f"req-{executor}")
                    self.assertEqual(outcome["launch_metadata"]["delegation_request"]["product_action"], "review")
                    self.assertEqual(outcome["launch_metadata"]["delegation_request"]["target_executor"], executor)
                    self.assertEqual(outcome["launch_metadata"]["delegation_request"]["priority"], "high")
                    self.assertEqual(outcome["terminal_summary"]["terminal_status"], "planned")

    def test_status_and_run_dry_run_write_state_without_advancing_phase(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            self.assertEqual(status_snapshot(repo, roadmap).phases["RUNNER"], "unplanned")
            snapshot, results = run_loop(repo, roadmap, phase="RUNNER", dry_run=True)
            self.assertEqual(snapshot.phases["RUNNER"], "unplanned")
            self.assertTrue(results[0].dry_run)
            self.assertTrue(results[0].context_sha256)
            self.assertTrue((repo / ".phase-loop" / "state.json").exists())
            self.assertTrue((repo / ".phase-loop" / "tui-handoff.md").exists())
            events = read_events(repo)
            self.assertEqual(events[-1]["status"], "unplanned")
            self.assertEqual(events[-1]["metadata"]["terminal_summary"]["terminal_status"], "dry_run")
            self.assertTrue(events[-1]["metadata"]["dry_run_only"])
            self.assertTrue(events[-1]["metadata"]["launch"]["dry_run"])
            self.assertNotIn("dry_run", PHASE_STATUSES)
            metric_id = events[-1]["metadata"]["terminal_summary"]["metric_id"]
            self.assertEqual(events[-1]["metadata"]["launch"]["metric_id"], metric_id)
            self.assertEqual(read_work_unit_metrics(repo)[-1]["metric_id"], metric_id)

    def test_claude_dry_run_keeps_executor_model_alias_with_explicit_profile(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            snapshot, results = run_loop(
                repo,
                roadmap,
                phase="CONTRACT",
                executor="claude",
                dry_run=True,
                action="run",
                model_profile="roadmap",
                product_action_override="roadmap",
            )

            self.assertEqual(snapshot.phases["CONTRACT"], "unplanned")
            self.assertEqual(len(results), 1)
            self.assertTrue(results[0].dry_run)
            self.assertEqual(results[0].selected_model, "claude-opus-4-8")
            self.assertIn("--model", results[0].command)
            self.assertIn("claude-opus-4-8", results[0].command)

    def test_status_snapshot_uses_reconciled_blocked_events(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(
                repo,
                provenanced_event(repo, roadmap, "CONTRACT", "blocked", action="execute"),
            )
            snapshot = status_snapshot(repo, roadmap)
            self.assertEqual(snapshot.phases["CONTRACT"], "blocked")
            self.assertEqual(snapshot.current_phase, "CONTRACT")

    def test_dry_run_planned_without_plan_artifact_replans(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            run_loop(repo, roadmap, phase="RUNNER", dry_run=True)
            _snapshot, results = run_loop(repo, roadmap, phase="RUNNER", dry_run=True)
            command_text = " ".join(results[0].command)
            self.assertIn("codex-plan-phase", command_text)
            self.assertNotIn("None", command_text)

    def test_source_bundle_path_threads_into_planning_prompt(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            _write_protected_source(repo)
            bundle_path = _write_bundle(repo)
            roadmap = repo / "specs" / "phase-plans-v1.md"

            _snapshot, results = run_loop(repo, roadmap, phase="RUNNER", dry_run=True, source_bundle_path=bundle_path)

            command_text = " ".join(results[0].command)
            self.assertIn("Pipeline planning source bundle", command_text)
            self.assertIn("source_bundle_sha256", command_text)
            self.assertIn("pipeline_phase_id", command_text)
            self.assertIn(".pipeline/artifacts/phase-source-bundle.json", command_text)

    def test_source_bundle_environment_fallback_threads_into_planning_prompt(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            _write_protected_source(repo)
            bundle_path = _write_bundle(repo)
            roadmap = repo / "specs" / "phase-plans-v1.md"

            with patch.dict(os.environ, {"PHASE_LOOP_SOURCE_BUNDLE": str(bundle_path)}):
                _snapshot, results = run_loop(repo, roadmap, phase="RUNNER", dry_run=True)

            self.assertIn("Pipeline planning source bundle", " ".join(results[0].command))

    def test_required_missing_source_bundle_blocks_before_child_planning_launch(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            snapshot, results = run_loop(repo, roadmap, phase="RUNNER", dry_run=True, pipeline_mode="pipeline_required")

            self.assertEqual(results, [])
            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            event = read_events(repo)[-1]
            self.assertEqual(event["blocker"]["blocker_class"], "contract_bug")
            self.assertIn("missing_source_bundle", event["blocker"]["blocker_summary"])

    def test_old_plan_without_lane_sections_stays_on_coarse_default(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, body="# RUNNER\n\nLegacy coarse plan body.\n")
            commit_fixture_paths(repo, "legacy plan fixture", plan)

            _snapshot, results = run_loop(repo, roadmap, phase="RUNNER", dry_run=True)

            self.assertEqual(len(results), 1)
            self.assertIn("codex-execute-phase", " ".join(results[0].command))
            self.assertFalse(load_work_unit_state(repo))

    def test_concurrent_lane_scheduler_launches_work_unit_before_child_execution(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, body=_migration_wave_body())
            commit_fixture_paths(repo, "wave fixture", plan)

            snapshot, results = run_loop(
                repo,
                roadmap,
                phase="RUNNER",
                dry_run=True,
                lane_scheduler_mode="concurrent",
            )
            events = read_events(repo)

            self.assertEqual(results, [])
            self.assertEqual(snapshot.phases["RUNNER"], "executing")
            self.assertEqual(tuple(load_work_unit_state(repo)), ("RUNNER.lane_execute.SL-0.1",))
            self.assertEqual(events[-1]["metadata"]["lane_scheduler"]["decision"]["status"], "ready")

    def test_reruns_reconciliation_after_phase_steers_roadmap(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            commands: list[list[str]] = []

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                commands.append(spec.command)
                if len(commands) == 1:
                    roadmap.write_text(
                        "# Roadmap\n\n"
                        "### Phase 0 - Contract (CONTRACT)\n\n"
                        "### Phase 2 - Runner (RUNNER)\n"
                    )
                    subprocess.run(["git", "add", str(roadmap.relative_to(repo))], cwd=repo, check=True)
                    subprocess.run(["git", "commit", "-m", "steer roadmap"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
                    append_event(
                        repo,
                        LoopEvent(
                            timestamp=utc_now(),
                            repo=str(repo),
                            roadmap=str(roadmap),
                            phase="CONTRACT",
                            action="execute",
                            status="complete",
                            model="gpt-5.4",
                            reasoning_effort="medium",
                            source="fixture",
                            **event_provenance(roadmap, "CONTRACT"),
                        ),
                    )
                return LaunchResult(command=spec.command, returncode=0)

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                snapshot, results = run_loop(repo, roadmap, max_phases=2)

            self.assertEqual(len(results), 2)
            self.assertIn("codex-plan-phase", " ".join(commands[1]))
            self.assertIn("RUNNER", " ".join(commands[1]))
            self.assertNotIn("ACCESS", snapshot.phases)
            self.assertEqual(snapshot.phases["CONTRACT"], "complete")

    def test_single_phase_run_hands_off_to_inserted_downstream_phase_after_amendment(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = make_regenesis_amendment_fixture(Path(td))
            repo = fixture.repo
            roadmap = fixture.roadmap

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                roadmap.write_text(
                    "# Roadmap\n\n"
                    "### Phase 0 - Affordance Verification (AFFVERIFY)\n\n"
                    "### Phase 1 - Mobile Shell (MOBSHELL)\n\n"
                    "### Phase 2 - Visual Fidelity (VISUAL)\n"
                )
                subprocess.run(["git", "add", str(roadmap.relative_to(repo))], cwd=repo, check=True)
                subprocess.run(["git", "commit", "-m", "steer roadmap downstream"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
                append_event(
                    repo,
                    provenanced_event(repo, roadmap, "AFFVERIFY", "complete", action="execute"),
                )
                return LaunchResult(command=spec.command, returncode=0)

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                snapshot, results = run_loop(repo, roadmap, phase=fixture.execute_phase, max_phases=1)

            self.assertEqual(len(results), 1)
            self.assertEqual(snapshot.phases[fixture.execute_phase], "complete")
            self.assertEqual(snapshot.phases["MOBSHELL"], "unplanned")
            self.assertEqual(snapshot.phases["VISUAL"], "unplanned")
            self.assertEqual(snapshot.current_phase, fixture.next_phase)
            self.assertIn(f"Current phase: {fixture.next_phase}", (repo / ".phase-loop" / "tui-handoff.md").read_text())

    def test_runner_event_metadata_carries_taskledger_summary_for_claude_team_launches(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(
                repo,
                "RUNNER",
                roadmap,
                body=(
                    "# RUNNER\n\n"
                    "## Lanes\n\n"
                    "### SL-0 - One\n"
                    "- **Owned files**: `src/one.py`\n\n"
                    "### SL-1 - Two\n"
                    "- **Owned files**: `src/two.py`\n"
                ),
            )

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                return make_fake_launch_result(
                    spec,
                    "execute",
                    executor="claude",
                    log_path=str(log_path) if log_path is not None else None,
                )

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                run_loop(repo, roadmap, phase="RUNNER", executor="claude")

            event = read_events(repo)[-1]
            self.assertIn("task_ledger", event["metadata"])
            self.assertIn("task_snapshot_path", event["metadata"]["task_ledger"])
            launch_metadata = json.loads(Path(event["metadata"]["artifacts"]["metadata"]).read_text(encoding="utf-8"))
            self.assertEqual(launch_metadata["task_ledger_runtime"]["terminal_status"], "executed")
            self.assertTrue(launch_metadata["task_ledger_runtime"]["superseded"])

    def test_message_board_fixture_clears_stale_blocker_with_later_trusted_event(self):
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
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="fixture",
                    blocker={
                        "human_required": False,
                        "blocker_class": "dirty_worktree_conflict",
                        "blocker_summary": "Stale message_board blocker awaiting later trusted status.",
                        "required_human_inputs": (),
                    },
                    roadmap_sha256="stale-roadmap",
                    phase_sha256="stale-phase",
                ),
            )

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                append_event(repo, provenanced_event(repo, roadmap, fixture.execute_phase, "complete", action="execute"))
                return LaunchResult(command=spec.command, returncode=0)

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                snapshot, results = run_loop(repo, roadmap, phase=fixture.execute_phase, max_phases=1)

            self.assertEqual(len(results), 1)
            self.assertEqual(snapshot.phases[fixture.execute_phase], "complete")
            self.assertEqual(snapshot.current_phase, fixture.next_phase)
            self.assertFalse(snapshot.human_required)
            self.assertEqual(snapshot.ledger_warnings, ())

    def test_successful_child_launch_with_untrusted_terminal_event_and_dirty_tree_awaits_closeout(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("evidence.md",))

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                roadmap.write_text(
                    "# Roadmap\n\n"
                    "### Phase 0 - Contract (CONTRACT)\n\n"
                    "### Phase 3 - Later (LATER)\n"
                    "\nGAREL-style downstream steering.\n"
                )
                append_event(
                    repo,
                    LoopEvent(
                        timestamp=utc_now(),
                        repo=str(repo),
                        roadmap=str(roadmap),
                        phase="CONTRACT",
                        action="execute",
                        status="complete",
                        model="gpt-5.4",
                        reasoning_effort="medium",
                        source="fixture",
                        schema_version=2,
                        roadmap_sha256="stale-roadmap",
                        phase_sha256="stale-phase",
                    ),
                )
                (repo / "evidence.md").write_text("child wrote evidence\n")
                return LaunchResult(command=spec.command, returncode=0)

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                snapshot, _results = run_loop(repo, roadmap, phase="CONTRACT")

            self.assertEqual(snapshot.current_phase, "CONTRACT")
            self.assertEqual(snapshot.phases["CONTRACT"], "awaiting_phase_closeout")
            self.assertFalse(snapshot.human_required)
            event = read_events(repo)[-1]
            self.assertEqual(event["status"], "awaiting_phase_closeout")
            self.assertIsNone(event.get("blocker"))
            self.assertEqual(event["metadata"]["terminal_summary"]["terminal_status"], "executed")
            self.assertEqual(event["metadata"]["terminal_summary"]["verification_status"], "not_run")
            self.assertTrue(Path(event["metadata"]["artifacts"]["terminal"]).exists())
            self.assertEqual(
                event["metadata"]["incomplete_execute_dirty_worktree"]["reason"],
                "execute_status_without_completion_with_dirty_worktree",
            )
            self.assertEqual(event["metadata"]["incomplete_execute_dirty_worktree"]["terminal_status"], "executed")
            self.assertIn("evidence.md", event["metadata"]["incomplete_execute_dirty_worktree"]["dirty_paths"])
            self.assertIn("CONTRACT: awaiting_phase_closeout", (repo / ".phase-loop" / "tui-handoff.md").read_text())

    def test_execute_launch_that_falls_back_to_planned_with_dirty_tree_awaits_closeout(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("evidence.md",))
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                (repo / "evidence.md").write_text("phase output\n")
                return LaunchResult(command=spec.command, returncode=0)

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                snapshot, _results = run_loop(repo, roadmap, phase="CONTRACT")

            self.assertEqual(snapshot.phases["CONTRACT"], "awaiting_phase_closeout")
            self.assertFalse(snapshot.human_required)
            event = read_events(repo)[-1]
            self.assertEqual(event["status"], "awaiting_phase_closeout")
            self.assertIsNone(event.get("blocker"))
            self.assertEqual(event["metadata"]["terminal_summary"]["terminal_status"], "executed")
            self.assertEqual(event["metadata"]["incomplete_execute_dirty_worktree"]["terminal_status"], "executed")
            self.assertIn("evidence.md", event["metadata"]["incomplete_execute_dirty_worktree"]["dirty_paths"])

    def test_failed_launch_marks_unknown_and_stops(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "CONTRACT", roadmap)
            commands: list[list[str]] = []

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                commands.append(spec.command)
                return LaunchResult(command=spec.command, returncode=42, output="failed\n")

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                snapshot, results = run_loop(repo, roadmap, max_phases=2)

            self.assertEqual(len(results), 1)
            self.assertEqual(len(commands), 1)
            self.assertEqual(snapshot.current_phase, "CONTRACT")
            self.assertEqual(snapshot.phases["CONTRACT"], "unknown")
            events = read_events(repo)
            self.assertEqual(events[-1]["status"], "unknown")
            self.assertEqual(events[-1]["metadata"]["launch"]["returncode"], 42)
            self.assertEqual(events[-1]["metadata"]["terminal_summary"]["verification_status"], "failed")
            self.assertIn("CONTRACT: unknown", (repo / ".phase-loop" / "tui-handoff.md").read_text())

    def test_fake_missing_closeout_matrix_blocks_equivalently_across_executors(self):
        for executor in tuple(item for item in FAKE_EXECUTORS if item != "codex"):
            with self.subTest(executor=executor):
                with tempfile.TemporaryDirectory() as td:
                    repo = make_repo(Path(td))
                    roadmap = repo / "specs" / "phase-plans-v1.md"
                    plan = write_phase_plan(repo, "RUNNER", roadmap)
                    subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True, stdout=subprocess.DEVNULL)
                    subprocess.run(["git", "commit", "-m", "add fake missing-closeout plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
                    kwargs = {"executor": executor}
                    if executor == "command":
                        kwargs.update(
                            {
                                "command_adapter_name": "wrapped-cli",
                                "command_template": "wrapped-cli --cwd {cwd} --plan {plan} --context-file {context_file}",
                            }
                        )
                    with patch(
                        "phase_loop_runtime.runner.run_auth_preflight",
                        return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
                    ), patch(
                        "phase_loop_runtime.runner.launch_with_spec",
                        return_value=LaunchResult(command=[executor, "run"], returncode=0, output='{"result":"no automation here"}', executor=executor),
                    ):
                        snapshot, results = run_loop(repo, roadmap, phase="RUNNER", **kwargs)

                    self.assertEqual(len(results), 1)
                    self.assertEqual(snapshot.phases["RUNNER"], "blocked")
                    event = read_events(repo)[-1]
                    self.assertEqual(event["blocker"]["blocker_class"], "repeated_verification_failure")
                    self.assertEqual(event["metadata"]["terminal_summary"]["terminal_status"], "blocked")

    def test_fake_output_failure_matrix_blocks_equivalently_across_executors(self):
        scenarios = ("malformed_output", "zero_byte_output", "timeout", "orphan_cleanup")
        for executor in tuple(item for item in FAKE_EXECUTORS if item != "codex"):
            for scenario_name in scenarios:
                with self.subTest(executor=executor, scenario=scenario_name):
                    with tempfile.TemporaryDirectory() as td:
                        repo = make_repo(Path(td))
                        roadmap = repo / "specs" / "phase-plans-v1.md"
                        plan = write_phase_plan(repo, "RUNNER", roadmap)
                        subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True, stdout=subprocess.DEVNULL)
                        subprocess.run(["git", "commit", "-m", "add fake output-failure plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
                        kwargs = {"executor": executor}
                        if executor == "command":
                            kwargs.update(
                                {
                                    "command_adapter_name": "wrapped-cli",
                                    "command_template": "wrapped-cli --cwd {cwd} --plan {plan} --context-file {context_file}",
                                }
                            )

                        def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **_kwargs):
                            return make_fake_launch_result(spec, scenario_name, executor=executor, log_path=str(log_path) if log_path else None)

                        with patch(
                            "phase_loop_runtime.runner.run_auth_preflight",
                            return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
                        ), patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                            snapshot, results = run_loop(repo, roadmap, phase="RUNNER", **kwargs)

                        self.assertEqual(len(results), 1)
                        self.assertEqual(snapshot.phases["RUNNER"], "blocked")
                        event = read_events(repo)[-1]
                        self.assertEqual(event["blocker"]["blocker_class"], "repeated_verification_failure")
                        self.assertEqual(event["metadata"]["terminal_summary"]["terminal_status"], "blocked")

    def test_fake_verified_dirty_closeout_matrix_reduces_to_awaiting_closeout(self):
        for executor in FAKE_EXECUTORS:
            with self.subTest(executor=executor):
                with tempfile.TemporaryDirectory() as td:
                    repo = make_repo(Path(td))
                    roadmap = repo / "specs" / "phase-plans-v1.md"
                    plan = write_phase_plan(repo, "RUNNER", roadmap, owned_files=("docs/status.md",))
                    subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True, stdout=subprocess.DEVNULL)
                    subprocess.run(["git", "commit", "-m", "add fake closeout plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
                    kwargs = {"executor": executor}
                    if executor == "command":
                        kwargs.update(
                            {
                                "command_adapter_name": "wrapped-cli",
                                "command_template": "wrapped-cli --cwd {cwd} --plan {plan} --context-file {context_file}",
                            }
                        )

                    def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **_kwargs):
                        status_file = repo / "docs" / "status.md"
                        status_file.parent.mkdir(parents=True, exist_ok=True)
                        status_file.write_text(f"{executor} fake closeout output\n", encoding="utf-8")
                        return make_fake_launch_result(
                            spec,
                            "verified_dirty_closeout",
                            executor=executor,
                            log_path=str(log_path) if log_path else None,
                        )

                    with patch(
                        "phase_loop_runtime.runner.run_auth_preflight",
                        return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
                    ), patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                        snapshot, results = run_loop(repo, roadmap, phase="RUNNER", **kwargs)

                    self.assertEqual(len(results), 1)
                    self.assertEqual(snapshot.phases["RUNNER"], "awaiting_phase_closeout")
                    self.assertFalse(snapshot.human_required)
                    event = read_events(repo)[-1]
                    self.assertEqual(event["status"], "awaiting_phase_closeout")
                    dirty_summary = event["metadata"].get("completion_dirty_worktree") or event["metadata"].get(
                        "incomplete_execute_dirty_worktree"
                    )
                    self.assertIsNotNone(dirty_summary)
                    self.assertIn(dirty_summary["terminal_status"], {"complete", "executed"})

    def test_operator_stop_file_halts_before_next_phase(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            stop = phase_loop_stop_file(repo)
            stop.parent.mkdir(parents=True, exist_ok=True)
            stop.write_text("pause for review\n")

            with patch("phase_loop_runtime.runner.launch_with_spec") as fake_launch:
                snapshot, results = run_loop(repo, roadmap, max_phases=2)

            fake_launch.assert_not_called()
            self.assertEqual(results, [])
            self.assertEqual(snapshot.current_phase, "CONTRACT")
            events = read_events(repo)
            self.assertEqual(events[-1]["action"], "operator_halt")
            self.assertTrue(events[-1]["metadata"]["operator_halt"])
            self.assertTrue((repo / ".phase-loop" / "tui-handoff.md").exists())

    def test_run_writes_launch_artifacts_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            snapshot, results = run_loop(repo, roadmap, phase="RUNNER", dry_run=True)

            self.assertEqual(snapshot.phases["RUNNER"], "unplanned")
            self.assertIsNotNone(results[0].log_path)
            self.assertEqual(results[0].executor, "codex")
            self.assertEqual(results[0].injection_mode, "prompt_only")
            self.assertEqual(results[0].expected_skill_pack, ("codex-plan-phase",))
            log_path = Path(results[0].log_path)
            self.assertTrue((log_path.parent / "launch.json").exists())
            self.assertTrue((log_path.parent / "heartbeat.json").exists())
            self.assertTrue((log_path.parent / "terminal-summary.json").exists())
            self.assertTrue(log_path.exists())
            launch_metadata = json.loads((log_path.parent / "launch.json").read_text(encoding="utf-8"))
            self.assertEqual(launch_metadata["live_proof_gate"], "none")
            self.assertEqual(launch_metadata["promotion_status"], "live")
            self.assertEqual(launch_metadata["output_capture_format"], "json_stream")
            self.assertEqual(launch_metadata["terminal_summary_artifact"], "terminal-summary.json")
            events = read_events(repo)
            self.assertIn("artifacts", events[-1]["metadata"])
            self.assertIn("heartbeat", events[-1]["metadata"]["artifacts"])
            self.assertIn("terminal", events[-1]["metadata"]["artifacts"])
            self.assertEqual(events[-1]["metadata"]["launch"]["executor"], "codex")
            self.assertEqual(events[-1]["metadata"]["launch"]["injection_mode"], "prompt_only")
            self.assertTrue(events[-1]["metadata"]["launch"]["context_sha256"])
            self.assertEqual(events[-1]["metadata"]["launch_spec"]["executor"], "codex")
            self.assertEqual(events[-1]["metadata"]["launch_request"]["executor"], "codex")

    def test_live_claude_executor_blocks_on_failed_auth_preflight(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add runner plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

            with patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type(
                    "Preflight",
                    (),
                    {
                        "ok": False,
                        "blocker_class": "account_or_billing_setup",
                        "blocker_summary": "Claude auth missing",
                        "metadata": {"probes": []},
                        "suggested_ttl_seconds": 300,
                        "demoted_to": "proof_gated",
                    },
                )(),
            ), patch("phase_loop_runtime.runner.launch_with_spec") as fake_launch:
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER", executor="claude")

            fake_launch.assert_not_called()
            self.assertEqual(results, [])
            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            events = read_events(repo)
            self.assertEqual(events[-1]["status"], "blocked")
            self.assertEqual(events[-1]["blocker"]["blocker_class"], "account_or_billing_setup")
            self.assertEqual(events[-1]["blocker"]["suggested_ttl_seconds"], 300)
            self.assertEqual(events[-1]["blocker"]["demoted_to"], "proof_gated")
            self.assertEqual(events[-1]["metadata"]["terminal_summary"]["verification_status"], "blocked")
            degradation = load_degradation(repo)["claude"]
            self.assertEqual(degradation.reason, "account_or_billing_setup")
            self.assertEqual(degradation.source_phase, "RUNNER")
            self.assertEqual(degradation.ttl_seconds, 300)
            self.assertEqual(degradation.demoted_to, "proof_gated")

    def test_live_claude_executor_blocks_when_closeout_is_missing(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add runner plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

            with patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
            ), patch(
                "phase_loop_runtime.runner.launch_with_spec",
                return_value=LaunchResult(command=["claude", "-p"], returncode=0, output='{"result":"no automation here"}', executor="claude"),
            ):
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER", executor="claude")

            self.assertEqual(len(results), 1)
            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            event = read_events(repo)[-1]
            self.assertEqual(event["blocker"]["blocker_class"], "repeated_verification_failure")
            self.assertIn("did not emit a valid shared automation closeout", event["blocker"]["blocker_summary"])

    def test_live_codex_missing_closeout_with_staged_phase_owned_output_commits(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, owned_files=("README.md",))
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add runner plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                (repo / "README.md").write_text("phase output\n")
                subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
                return LaunchResult(
                    command=spec.command,
                    returncode=0,
                    output='{"result":"no automation here"}',
                    log_path=str(log_path) if log_path else None,
                    executor="codex",
                )

            with patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
            ), patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER", executor="codex", closeout_mode="commit")

            self.assertEqual(len(results), 1)
            self.assertEqual(snapshot.phases["RUNNER"], "complete")
            events = read_events(repo)
            dirty_event = self._latest_event_with_metadata(events, "incomplete_execute_dirty_worktree")
            self.assertEqual(dirty_event["status"], "awaiting_phase_closeout")
            self.assertEqual(dirty_event["metadata"]["incomplete_execute_dirty_worktree"]["phase_owned_dirty"], True)
            self.assertEqual(events[-1]["status"], "complete")
            self.assertEqual(events[-1]["metadata"]["closeout"]["closeout_action"], "commit")
            self.assertEqual(subprocess.check_output(["git", "-C", str(repo), "status", "--short"], text=True).strip(), "")

    def test_repair_launch_trusts_manual_repair_event_without_closeout_footer(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, owned_files=("README.md",))
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add runner plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="RUNNER",
                    action="execute",
                    status="blocked",
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="fixture",
                    blocker={
                        "human_required": False,
                        "blocker_class": "repeated_verification_failure",
                        "blocker_summary": "missing closeout footer",
                        "required_human_inputs": (),
                        "access_attempts": (),
                    },
                    metadata={
                        "terminal_summary": {
                            "terminal_status": "blocked",
                            "verification_status": "blocked",
                            "dirty_paths": [],
                        }
                    },
                    **event_provenance(roadmap, "RUNNER"),
                ),
            )

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                append_event(
                    repo,
                    LoopEvent(
                        timestamp=utc_now(),
                        repo=str(repo),
                        roadmap=str(roadmap),
                        phase="RUNNER",
                        action="manual_repair",
                        status="complete",
                        model="manual",
                        reasoning_effort="none",
                        source="manual_repair",
                        metadata={
                            "manual_repair": {
                                "clears_blocker": True,
                                "verification_status": "passed",
                            },
                            "terminal_summary": {
                                "terminal_status": "complete",
                                "verification_status": "passed",
                                "dirty_paths": [],
                            },
                        },
                        **event_provenance(roadmap, "RUNNER"),
                    ),
                )
                return LaunchResult(
                    command=spec.command,
                    returncode=0,
                    output='{"result":"manual repair event already written"}',
                    log_path=str(log_path) if log_path else None,
                    executor="codex",
                )

            with patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
            ), patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER", executor="codex", closeout_mode="commit")

            self.assertEqual(len(results), 1)
            self.assertEqual(snapshot.phases["RUNNER"], "complete")
            events = read_events(repo)
            self.assertEqual(self._latest_event_with_action(events, "manual_repair")["action"], "manual_repair")
            self.assertEqual(events[-1]["status"], "complete")
            self.assertNotIn("blocker", events[-1])

    def test_planning_launch_uses_plan_artifact_automation_when_stdout_closeout_missing(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                plan_path = repo / "plans" / "phase-plan-v1-RUNNER.md"
                write_phase_plan(
                    repo,
                    "RUNNER",
                    roadmap,
                    body=(
                        "# RUNNER\n\n"
                        "## Lanes\n\n"
                        "### SL-0 - RUNNER\n"
                        "- **Owned files**: none\n\n"
                        "## Automation Handoff\n\n"
                        "```yaml\n"
                        "automation:\n"
                        "  status: planned\n"
                        "  next_skill: codex-execute-phase\n"
                        "  next_command: codex-execute-phase plans/phase-plan-v1-RUNNER.md\n"
                        "  human_required: false\n"
                        "  blocker_class: none\n"
                        "  blocker_summary: none\n"
                        "  required_human_inputs: []\n"
                        "  verification_status: not_run\n"
                        f"  artifact: {plan_path}\n"
                        "  artifact_state: staged\n"
                        "```\n"
                    ),
                )
                return LaunchResult(command=["codex", "exec"], returncode=0, output='{"result":"plan written"}', executor="codex")

            with patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
            ), patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER", executor="codex", closeout_mode="commit")

            self.assertEqual(len(results), 1)
            self.assertEqual(snapshot.phases["RUNNER"], "planned")
            self.assertFalse(snapshot.human_required)
            events = read_events(repo)
            self.assertNotIn("blocker", events[-1])
            self.assertEqual(events[-1]["metadata"]["closeout"]["closeout_action"], "commit")

    def test_planning_launch_blocked_by_stale_dirty_closeout_commits_valid_plan(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            output = build_fake_automation_output(
                status="blocked",
                human_required=False,
                blocker_class="dirty_worktree_conflict",
                blocker_summary="stale upstream dirty closeout",
                verification_status="blocked",
            )

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                write_phase_plan(repo, "RUNNER", roadmap)
                return LaunchResult(command=spec.command, returncode=0, output=output, executor="codex")

            with patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
            ), patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER", executor="codex", closeout_mode="commit")

            self.assertEqual(len(results), 1)
            self.assertEqual(snapshot.phases["RUNNER"], "planned")
            self.assertFalse(snapshot.human_required)
            events = read_events(repo)
            dirty_event = self._latest_event_with_metadata(events, "plan_dirty_worktree")
            self.assertEqual(dirty_event["status"], "awaiting_phase_closeout")
            self.assertEqual(dirty_event["metadata"]["child_automation"]["automation_status"], "blocked")
            self.assertEqual(dirty_event["metadata"]["plan_dirty_worktree"]["phase_owned_dirty"], True)
            self.assertEqual(events[-1]["status"], "planned")
            self.assertEqual(events[-1]["metadata"]["closeout"]["closeout_action"], "commit")
            self.assertEqual(subprocess.check_output(["git", "-C", str(repo), "status", "--short"], text=True).strip(), "")

    def test_live_claude_executor_blocks_when_automation_closeout_is_malformed(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add runner plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

            with patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
            ), patch(
                "phase_loop_runtime.runner.launch_with_spec",
                return_value=LaunchResult(
                    command=["claude", "-p"],
                    returncode=0,
                    output='{"result":"automation:\\n  status: executed\\n  next_skill: none\\n"}',
                    executor="claude",
                ),
            ):
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER", executor="claude")

            self.assertEqual(len(results), 1)
            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            event = read_events(repo)[-1]
            self.assertEqual(event["blocker"]["blocker_class"], "repeated_verification_failure")
            self.assertIn("malformed shared automation closeout", event["blocker"]["blocker_summary"])

    def test_gemini_repair_completion_closeout_commits_inherited_phase_owned_dirty(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, owned_files=("README.md",))
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add runner plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            (repo / "README.md").write_text("phase output from failed execute\n", encoding="utf-8")
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="RUNNER",
                    action="execute",
                    status="blocked",
                    model="auto",
                    reasoning_effort="medium",
                    source="fixture",
                    blocker={
                        "human_required": False,
                        "blocker_class": "repeated_verification_failure",
                        "blocker_summary": "Gemini live launch for RUNNER emitted a malformed shared automation closeout.",
                        "required_human_inputs": (),
                        "access_attempts": (),
                    },
                    metadata={
                        "terminal_summary": {
                            "terminal_status": "blocked",
                            "verification_status": "blocked",
                            "next_action": "Repair malformed Gemini closeout.",
                            "dirty_paths": ["README.md"],
                            "phase_owned_dirty": False,
                            "phase_owned_dirty_paths": ["README.md"],
                            "unowned_dirty_paths": [],
                            "pre_existing_dirty_paths": ["README.md"],
                            "artifact_paths": {},
                        }
                    },
                    **event_provenance(roadmap, "RUNNER"),
                ),
            )

            def fake_repair(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                return LaunchResult(
                    command=spec.command,
                    returncode=0,
                    output=(
                        'Warning: 256-color support not detected.\n'
                        '{"session_id":"abc","response":"automation:\\n'
                        "  status: complete\\n"
                        "  next_skill: none\\n"
                        "  next_command: none\\n"
                        "  human_required: false\\n"
                        "  blocker_class: none\\n"
                        "  blocker_summary: none\\n"
                        "  required_human_inputs: []\\n"
                        "  verification_status: passed\"}"
                    ),
                    executor="gemini",
                    log_path=str(log_path) if log_path else None,
                )

            with patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
            ), patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_repair):
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER", executor="gemini", closeout_mode="commit")

            self.assertEqual(len(results), 1)
            self.assertEqual(snapshot.phases["RUNNER"], "complete")
            events = read_events(repo)
            dirty_event = self._latest_event_with_metadata(events, "completion_dirty_worktree")
            self.assertEqual(dirty_event["status"], "awaiting_phase_closeout")
            self.assertIsNone(dirty_event.get("blocker"))
            self.assertEqual(dirty_event["metadata"]["child_automation"]["automation_status"], "complete")
            self.assertEqual(dirty_event["metadata"]["completion_dirty_worktree"]["phase_owned_dirty"], True)
            self.assertEqual(dirty_event["metadata"]["completion_dirty_worktree"]["pre_existing_dirty_paths"], [])
            self.assertEqual(events[-1]["status"], "complete")
            self.assertEqual(events[-1]["metadata"]["closeout"]["closeout_action"], "commit")
            self.assertEqual(subprocess.check_output(["git", "-C", str(repo), "status", "--short"], text=True).strip(), "")

    def test_invalid_automation_status_blocks_instead_of_crashing(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add runner plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            output = build_fake_automation_output(status="<complete|executed|blocked|unknown>")

            with patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
            ), patch(
                "phase_loop_runtime.runner.launch_with_spec",
                return_value=LaunchResult(command=["codex", "exec"], returncode=0, output=output, executor="codex"),
            ):
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER", executor="codex")

            self.assertEqual(len(results), 1)
            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            event = read_events(repo)[-1]
            self.assertEqual(event["status"], "blocked")
            self.assertEqual(event["blocker"]["blocker_class"], "repeated_verification_failure")
            self.assertIn("invalid shared automation status", event["blocker"]["blocker_summary"])
            self.assertEqual(event["metadata"]["child_automation"]["automation_status"], "<complete|executed|blocked|unknown>")

    def test_delegated_automation_status_launches_typed_child(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, owned_files=("notes.md",))
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add runner plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            delegated_output = """delegation_request:
  request_id: runner-execute-codex
  product_action: execute
  target_executor: codex
  reason: Use Codex for this execution slice.
  owned_files:
    - notes.md
  expected_output: Complete the phase implementation.
  priority: normal
  budget:
    notes: metadata only
automation:
  status: delegated
  next_skill: codex-execute-phase
  next_command: codex-execute-phase plans/phase-plan-v1-RUNNER.md
  human_required: false
  blocker_class: none
  blocker_summary: none
  required_human_inputs: []
  verification_status: not_run
"""
            child_output = build_fake_automation_output(status="complete", verification_status="passed")

            with patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
            ), patch("phase_loop_runtime.runner.launch_with_spec") as fake_launch:
                fake_launch.side_effect = (
                    LaunchResult(command=["claude", "-p"], returncode=0, output=delegated_output, executor="claude"),
                    LaunchResult(command=["codex", "exec"], returncode=0, output=child_output, executor="codex"),
                )
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER", executor="claude")

            self.assertEqual(len(results), 1)
            self.assertEqual(fake_launch.call_count, 2)
            self.assertEqual(snapshot.phases["RUNNER"], "complete")
            event = read_events(repo)[-1]
            self.assertEqual(event["status"], "complete")
            delegated_child = event["metadata"]["child_automation"]["delegated_child"]
            self.assertEqual(delegated_child["decision"]["status"], "approved")
            self.assertEqual(
                delegated_child["launch_metadata"]["parent_child"]["child_closeout_result"]["selected_executor"],
                "codex",
            )

    def test_live_claude_executor_normalizes_legacy_skill_closeout(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            with patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
            ), patch("phase_loop_runtime.runner.launch_with_spec") as fake_launch:
                def fake_legacy_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                    write_phase_plan(repo, "RUNNER", roadmap)
                    if log_path is not None:
                        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
                        Path(log_path).write_text("legacy claude closeout\n", encoding="utf-8")
                    return LaunchResult(
                        command=["claude", "-p"],
                        returncode=0,
                        output=(
                            '{"result":"```yaml\\nautomation:\\n'
                            "  skill: claude-plan-phase\\n"
                            "  phase: runner\\n"
                            "  phase_id: RUNNER\\n"
                            "  verification_status: passed\\n"
                            "  next_phase: RUNNER - execution ready\\n"
                            "  next_command: /claude-execute-phase runner\\n"
                            '```"}'
                        ),
                        executor="claude",
                        log_path=str(log_path) if log_path else None,
                        process_pid=12345,
                        started_at=utc_now(),
                        finished_at=utc_now(),
                    )

                fake_launch.side_effect = fake_legacy_launch
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER", executor="claude")

            self.assertEqual(len(results), 1)
            self.assertEqual(snapshot.phases["RUNNER"], "planned")
            event = read_events(repo)[-1]
            self.assertEqual(event["status"], "planned")
            self.assertEqual(event["metadata"]["child_automation"]["automation_status"], "planned")
            self.assertEqual(event["metadata"]["child_automation"]["automation_next_skill"], "claude-execute-phase")

    def test_failed_live_claude_launch_with_trusted_closeout_is_reduced(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add runner plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

            output = (
                '{"type":"result","result":"automation:\\n  status: completed\\n'
                "  next_skill: claude-phase-roadmap-builder\\n"
                "  next_command: claude-phase-roadmap-builder specs/phase-plans-v1.md\\n"
                "  human_required: false\\n"
                "  blocker_class: none\\n"
                "  blocker_summary: none\\n"
                "  required_human_inputs: []\\n"
                '  verification_status: pass"}'
            )

            with patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
            ), patch("phase_loop_runtime.runner.launch_with_spec") as fake_launch:
                fake_launch.return_value = LaunchResult(
                    command=["claude", "-p"],
                    returncode=-15,
                    output=output,
                    executor="claude",
                )
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER", executor="claude")

            self.assertEqual(len(results), 1)
            self.assertEqual(snapshot.phases["RUNNER"], "complete")
            event = read_events(repo)[-1]
            self.assertEqual(event["status"], "complete")
            self.assertEqual(event["metadata"]["child_automation"]["automation_status"], "complete")
            self.assertEqual(
                event["metadata"]["launch"]["returncode"],
                0,
            )
            self.assertEqual(
                event["metadata"]["child_automation"]["original_returncode"],
                -15,
            )

    def test_repair_planned_closeout_clears_stale_blocker_and_commits_phase_plan(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, owned_files=("README.md",))
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="RUNNER",
                    action="run",
                    status="blocked",
                    model="claude-opus-4-8",
                    reasoning_effort="high",
                    source="fixture",
                    blocker={
                        "human_required": False,
                        "blocker_class": "repeated_verification_failure",
                        "blocker_summary": "stale blocker",
                        "required_human_inputs": (),
                        "access_attempts": (),
                    },
                    metadata={
                        "terminal_summary": {
                            "terminal_status": "complete",
                            "terminal_blocker": {
                                "human_required": False,
                                "blocker_class": "repeated_verification_failure",
                                "blocker_summary": "stale blocker",
                                "required_human_inputs": (),
                                "access_attempts": (),
                            },
                            "verification_status": "blocked",
                            "next_action": "stale blocker",
                            "dirty_paths": [str(plan.relative_to(repo))],
                            "phase_owned_dirty": True,
                            "phase_owned_dirty_paths": [str(plan.relative_to(repo))],
                            "unowned_dirty_paths": [],
                            "pre_existing_dirty_paths": [],
                            "artifact_paths": {},
                        }
                    },
                    **event_provenance(roadmap, "RUNNER"),
                ),
            )

            def fake_repair(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                plan.write_text(plan.read_text(encoding="utf-8") + "\n<!-- refreshed -->\n", encoding="utf-8")
                return LaunchResult(
                    command=["claude", "-p"],
                    returncode=0,
                    output=(
                        '{"result":"automation:\\n'
                        "  status: success\\n"
                        "  next_skill: claude-execute-phase\\n"
                        "  next_command: claude-execute-phase /repo/plans/phase-plan-v1-RUNNER.md RUNNER\\n"
                        "  human_required: false\\n"
                        "  blocker_class: none\\n"
                        "  blocker_summary: none\\n"
                        "  required_human_inputs: []\\n"
                        "  verification_status: evidence_checked\\n"
                        '"}'
                    ),
                    executor="claude",
                    log_path=str(log_path) if log_path else None,
                )

            with patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
            ), patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_repair):
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER", executor="claude", closeout_mode="commit")

            self.assertEqual(len(results), 1)
            self.assertEqual(snapshot.phases["RUNNER"], "planned")
            events = read_events(repo)
            dirty_event = self._latest_event_with_metadata(events, "plan_dirty_worktree")
            self.assertEqual(dirty_event["status"], "awaiting_phase_closeout")
            self.assertIsNone(dirty_event.get("blocker"))
            self.assertNotIn("terminal_blocker", dirty_event["metadata"]["terminal_summary"])
            self.assertEqual(dirty_event["metadata"]["terminal_summary"]["verification_status"], "not_run")
            self.assertEqual(events[-1]["status"], "planned")
            self.assertEqual(events[-1]["metadata"]["closeout"]["closeout_action"], "commit")
            self.assertEqual(subprocess.check_output(["git", "-C", str(repo), "status", "--short"], text=True).strip(), "")

    def test_live_claude_executor_blocks_when_log_is_zero_byte_and_output_is_empty(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)

            with patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
            ), patch("phase_loop_runtime.runner.launch_with_spec") as fake_launch:
                def fake_zero_byte_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                    if log_path is not None:
                        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
                        Path(log_path).write_text("", encoding="utf-8")
                    return LaunchResult(
                        command=["claude", "-p"],
                        returncode=0,
                        output="",
                        executor="claude",
                        log_path=str(log_path) if log_path else None,
                        process_pid=12345,
                        started_at=utc_now(),
                        finished_at=utc_now(),
                    )

                fake_launch.side_effect = fake_zero_byte_launch
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER", executor="claude")

            self.assertEqual(len(results), 1)
            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            event = read_events(repo)[-1]
            self.assertEqual(event["blocker"]["blocker_class"], "repeated_verification_failure")
            self.assertIn("zero-byte durable output log", event["blocker"]["blocker_summary"])

    def test_live_gemini_executor_blocks_when_closeout_is_missing(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add runner plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

            with patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
            ), patch(
                "phase_loop_runtime.runner.launch_with_spec",
                return_value=LaunchResult(command=["gemini", "-p"], returncode=0, output='{"response":"no automation here"}', executor="gemini"),
            ):
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER", executor="gemini")

            self.assertEqual(len(results), 1)
            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            event = read_events(repo)[-1]
            self.assertEqual(event["blocker"]["blocker_class"], "repeated_verification_failure")
            self.assertIn("Gemini live launch", event["blocker"]["blocker_summary"])

    def test_live_gemini_executor_reduces_auth_failure_to_typed_blocker(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)

            with patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
            ), patch(
                "phase_loop_runtime.runner.launch_with_spec",
                return_value=LaunchResult(
                    command=["gemini", "-p"],
                    returncode=55,
                    output="Gemini CLI auth error: please log in to continue.",
                    executor="gemini",
                ),
            ):
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER", executor="gemini")

            self.assertEqual(len(results), 1)
            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            event = read_events(repo)[-1]
            self.assertEqual(event["status"], "blocked")
            self.assertEqual(event["blocker"]["blocker_class"], "account_or_billing_setup")
            self.assertEqual(event["metadata"]["terminal_summary"]["terminal_status"], "blocked")

    def test_live_codex_executor_reduces_auth_failure_to_typed_blocker(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)

            with patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
            ), patch(
                "phase_loop_runtime.runner.launch_with_spec",
                return_value=LaunchResult(
                    command=["codex", "exec"],
                    returncode=55,
                    output="subscription required for this account\n",
                    executor="codex",
                ),
            ):
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER", executor="codex")

            self.assertEqual(len(results), 1)
            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            event = read_events(repo)[-1]
            self.assertEqual(event["status"], "blocked")
            self.assertEqual(event["blocker"]["blocker_class"], "account_or_billing_setup")
            self.assertIn("Codex live launch", event["blocker"]["blocker_summary"])
            self.assertEqual(event["metadata"]["terminal_summary"]["terminal_status"], "blocked")

    def test_live_codex_executor_parses_json_closeout_blocker(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            output = (
                '{"type":"item.completed","item":{"type":"agent_message","text":"automation:\\n'
                "  status: planned\\n"
                "  next_skill: codex-execute-phase\\n"
                "  next_command: codex-execute-phase /repo/plans/phase-plan-v1-RUNNER.md\\n"
                "  human_required: false\\n"
                "  blocker_class: none\\n"
                "  blocker_summary: none\\n"
                "  required_human_inputs: []\\n"
                "  verification_status: not_run\\n"
                '"}}\n'
                '{"type":"item.completed","item":{"type":"agent_message","text":"automation:\\n'
                "  status: blocked\\n"
                "  next_skill: none\\n"
                "  next_command: none\\n"
                "  human_required: false\\n"
                "  blocker_class: none\\n"
                "  blocker_summary: SG-0 failed.\\n"
                "  required_human_inputs: []\\n"
                "  verification_status: blocked\\n"
                '"}}\n'
            )

            with patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
            ), patch(
                "phase_loop_runtime.runner.launch_with_spec",
                return_value=LaunchResult(command=["codex", "exec"], returncode=0, output=output, executor="codex"),
            ):
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER", executor="codex")

            self.assertEqual(len(results), 1)
            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            event = read_events(repo)[-1]
            self.assertEqual(event["status"], "blocked")
            self.assertEqual(event["blocker"]["blocker_class"], "repeated_verification_failure")
            self.assertEqual(event["blocker"]["blocker_summary"], "SG-0 failed.")
            self.assertEqual(event["metadata"]["terminal_summary"]["terminal_status"], "blocked")
            self.assertEqual(event["metadata"]["terminal_summary"]["verification_status"], "blocked")

    def test_live_codex_verified_dirty_closeout_commits_phase_owned_output(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, owned_files=("README.md",))
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            output = (
                '{"type":"item.completed","item":{"type":"agent_message","text":"automation:\\n'
                "  status: blocked\\n"
                "  next_skill: none\\n"
                "  next_command: none - preserve verified output\\n"
                "  human_required: false\\n"
                "  blocker_class: dirty_worktree_conflict\\n"
                "  blocker_summary: phase-owned changes passed verification but remain dirty\\n"
                "  required_human_inputs: []\\n"
                "  verification_status: passed\\n"
                '"}}\n'
            )

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                (repo / "README.md").write_text("verified output\n", encoding="utf-8")
                return LaunchResult(command=spec.command, returncode=0, output=output, executor="codex", log_path=str(log_path) if log_path else None)

            with patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
            ), patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER", executor="codex", closeout_mode="commit")

            self.assertEqual(len(results), 1)
            self.assertEqual(snapshot.phases["RUNNER"], "complete")
            events = read_events(repo)
            dirty_event = self._latest_event_with_metadata(events, "completion_dirty_worktree")
            self.assertEqual(dirty_event["status"], "awaiting_phase_closeout")
            self.assertEqual(dirty_event["metadata"]["completion_dirty_worktree"]["phase_owned_dirty"], True)
            self.assertEqual(events[-1]["status"], "complete")
            self.assertEqual(events[-1]["metadata"]["closeout"]["closeout_action"], "commit")
            self.assertEqual(subprocess.check_output(["git", "-C", str(repo), "status", "--short"], text=True).strip(), "")

    def test_verified_dirty_recovery_uses_launch_plan_after_downstream_roadmap_amendment(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, owned_files=("README.md",))
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            roadmap.write_text(
                "# Roadmap\n\n"
                "### Phase 0 - Contract (CONTRACT)\n\n"
                "### Phase 1 - Access (ACCESS)\n\n"
                "### Phase 2 - Runner (RUNNER)\n\n"
                "### Phase 3 - Downstream (DOWNSTREAM)\n"
            )
            (repo / "README.md").write_text("verified output\n", encoding="utf-8")
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="RUNNER",
                    action="run",
                    status="blocked",
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="fixture",
                    blocker={
                        "human_required": False,
                        "blocker_class": "repeated_verification_failure",
                        "blocker_summary": "old runner missed the automation closeout",
                        "required_human_inputs": (),
                        "access_attempts": (),
                    },
                    metadata={
                        "launch_request": {"plan": str(plan)},
                        "child_automation": {
                            "automation_status": "blocked",
                            "automation_human_required": "false",
                            "automation_blocker_class": "dirty_worktree_conflict",
                            "automation_blocker_summary": "verified dirty output remains",
                            "automation_verification_status": "passed",
                            "automation_required_human_inputs": [],
                        },
                        "terminal_summary": {
                            "terminal_status": "blocked",
                            "verification_status": "blocked",
                            "dirty_paths": [],
                        },
                    },
                    **event_provenance(roadmap, "RUNNER"),
                ),
            )

            with patch("phase_loop_runtime.runner.launch_with_spec") as fake_launch:
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER", closeout_mode="commit")

            fake_launch.assert_not_called()
            self.assertEqual(results, [])
            self.assertEqual(snapshot.phases["RUNNER"], "complete")
            events = read_events(repo)
            recovery_event = next(
                event for event in events if event.get("metadata", {}).get("verified_dirty_closeout_recovery")
            )
            self.assertEqual(recovery_event["status"], "awaiting_phase_closeout")
            self.assertEqual(recovery_event["metadata"]["verified_dirty_closeout_recovery"]["plan_source"], "latest_launch")
            self.assertIn("specs/phase-plans-v1.md", recovery_event["metadata"]["completion_dirty_worktree"]["phase_owned_dirty_paths"])
            self.assertIn("README.md", recovery_event["metadata"]["completion_dirty_worktree"]["phase_owned_dirty_paths"])
            self.assertEqual(events[-1]["status"], "complete")
            self.assertEqual(subprocess.check_output(["git", "-C", str(repo), "status", "--short"], text=True).strip(), "")

    def test_verified_dirty_recovery_fails_closed_when_launch_plan_does_not_own_dirty_paths(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, owned_files=("docs/owned.md",))
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            roadmap.write_text(
                "# Roadmap\n\n"
                "### Phase 0 - Contract (CONTRACT)\n\n"
                "### Phase 1 - Access (ACCESS)\n\n"
                "### Phase 2 - Runner (RUNNER)\n\n"
                "### Phase 3 - Downstream (DOWNSTREAM)\n"
            )
            (repo / "README.md").write_text("unowned verified output\n", encoding="utf-8")
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="RUNNER",
                    action="run",
                    status="blocked",
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="fixture",
                    blocker={
                        "human_required": False,
                        "blocker_class": "repeated_verification_failure",
                        "blocker_summary": "old runner missed the automation closeout",
                        "required_human_inputs": (),
                        "access_attempts": (),
                    },
                    metadata={
                        "launch_request": {"plan": str(plan)},
                        "child_automation": {
                            "automation_status": "blocked",
                            "automation_human_required": "false",
                            "automation_blocker_class": "dirty_worktree_conflict",
                            "automation_blocker_summary": "verified dirty output remains",
                            "automation_verification_status": "passed",
                            "automation_required_human_inputs": [],
                        },
                    },
                    **event_provenance(roadmap, "RUNNER"),
                ),
            )

            with patch("phase_loop_runtime.runner.launch_with_spec") as fake_launch:
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER", closeout_mode="commit")

            fake_launch.assert_not_called()
            self.assertEqual(results, [])
            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            self.assertTrue(snapshot.human_required)
            self.assertEqual(snapshot.blocker_class, "dirty_worktree_conflict")
            event = read_events(repo)[-1]
            self.assertEqual(event["status"], "blocked")
            self.assertEqual(event["metadata"]["verified_dirty_closeout_recovery"]["plan_source"], "latest_launch")
            self.assertEqual(event["metadata"]["completion_dirty_worktree"]["unowned_dirty_paths"], ["README.md"])

    def test_live_claude_executor_reduces_usage_failure_to_typed_blocker(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)

            with patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
            ), patch(
                "phase_loop_runtime.runner.launch_with_spec",
                return_value=LaunchResult(
                    command=["claude", "-p"],
                    returncode=55,
                    output="You're out of extra usage. Add more at claude.ai/settings/usage and keep going.",
                    executor="claude",
                ),
            ):
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER", executor="claude")

            self.assertEqual(len(results), 1)
            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            event = read_events(repo)[-1]
            self.assertEqual(event["status"], "blocked")
            self.assertEqual(event["blocker"]["blocker_class"], "account_or_billing_setup")
            self.assertIn("Claude live launch", event["blocker"]["blocker_summary"])
            self.assertEqual(event["metadata"]["terminal_summary"]["terminal_status"], "blocked")

    def test_live_opencode_executor_blocks_when_closeout_is_missing(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add runner plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

            with patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
            ), patch(
                "phase_loop_runtime.runner.launch_with_spec",
                return_value=LaunchResult(command=["opencode", "run"], returncode=0, output='{"content":"no automation here"}', executor="opencode"),
            ):
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER", executor="opencode")

            self.assertEqual(len(results), 1)
            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            event = read_events(repo)[-1]
            self.assertEqual(event["blocker"]["blocker_class"], "repeated_verification_failure")
            self.assertIn("Opencode live launch", event["blocker"]["blocker_summary"])

    def test_live_opencode_executor_reduces_auth_failure_to_typed_blocker(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)

            with patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
            ), patch(
                "phase_loop_runtime.runner.launch_with_spec",
                return_value=LaunchResult(
                    command=["opencode", "run"],
                    returncode=55,
                    output="subscription required for this account\n",
                    executor="opencode",
                ),
            ):
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER", executor="opencode")

            self.assertEqual(len(results), 1)
            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            event = read_events(repo)[-1]
            self.assertEqual(event["status"], "blocked")
            self.assertEqual(event["blocker"]["blocker_class"], "account_or_billing_setup")
            self.assertEqual(event["metadata"]["terminal_summary"]["terminal_status"], "blocked")

    def test_dry_run_plan_hints_can_select_non_default_executor(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.write_text(
                roadmap.read_text()
                + "\n## Dispatch Hints\n"
                + "- plan preferred executors: `claude`\n"
                + "- plan fallback executors: `codex`\n"
            )

            snapshot, results = run_loop(repo, roadmap, phase="RUNNER", dry_run=True)

            self.assertEqual(snapshot.phases["RUNNER"], "unplanned")
            self.assertEqual(results[0].executor, "claude")
            event = read_events(repo)[-1]
            self.assertEqual(event["metadata"]["dispatch_decision"]["selected_executor"], "claude")
            self.assertEqual(event["metadata"]["dispatch_decision"]["source"], "roadmap")

    def test_live_run_honors_claude_preference_when_live_adapter_is_available(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.write_text(
                roadmap.read_text()
                + "\n## Dispatch Hints\n"
                + "- execute preferred executors: `claude`\n"
                + "- execute fallback executors: `codex`\n"
            )
            write_phase_plan(repo, "RUNNER", roadmap)

            with patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
            ), patch(
                "phase_loop_runtime.runner.launch_with_spec",
                return_value=LaunchResult(
                    command=["claude", "-p"],
                    returncode=0,
                    output='{"result":"automation:\\n  status: executed\\n  next_skill: none\\n  next_command: none\\n  human_required: false\\n  blocker_class: none\\n  blocker_summary: none\\n  required_human_inputs: []\\n  verification_status: not_run"}',
                    executor="claude",
                ),
            ) as fake_launch:
                _snapshot, results = run_loop(repo, roadmap, phase="RUNNER")

            self.assertEqual(len(results), 1)
            fake_launch.assert_called_once()
            self.assertEqual(fake_launch.call_args.args[0].executor, "claude")
            event = read_events(repo)[-1]
            self.assertFalse(event["metadata"]["dispatch_decision"]["fallback_applied"])
            self.assertEqual(event["metadata"]["dispatch_decision"]["selected_executor"], "claude")

    def test_command_executor_without_adapter_inputs_blocks_even_in_dry_run(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)

            with patch("phase_loop_runtime.runner.launch_with_spec") as fake_launch:
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER", dry_run=True, executor="command")

            fake_launch.assert_not_called()
            self.assertEqual(results, [])
            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            event = read_events(repo)[-1]
            self.assertEqual(event["status"], "blocked")
            self.assertIn("explicit adapter inputs", event["blocker"]["blocker_summary"])

    def test_command_executor_dry_run_writes_launch_artifacts_with_template_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add command adapter plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

            snapshot, results = run_loop(
                repo,
                roadmap,
                phase="RUNNER",
                dry_run=True,
                executor="command",
                command_adapter_name="wrapped-cli",
                command_template="wrapped-cli --cwd {cwd} --plan {plan} --context-file {context_file}",
            )

            self.assertEqual(snapshot.phases["RUNNER"], "planned")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].executor, "command")
            launch_metadata = json.loads((Path(results[0].log_path).parent / "launch.json").read_text(encoding="utf-8"))
            self.assertEqual(launch_metadata["command_adapter_name"], "wrapped-cli")
            self.assertEqual(launch_metadata["wrapped_cwd"], str(repo))
            self.assertIn("{context_file}", launch_metadata["command_template"])

    def test_observe_can_be_disabled_for_launch_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            _snapshot, results = run_loop(repo, roadmap, phase="RUNNER", dry_run=True, observe=False)

            self.assertIsNone(results[0].log_path)
            self.assertFalse((repo / ".phase-loop" / "runs").exists())

    def test_successful_planning_launch_preserves_planned_status(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                write_phase_plan(repo, "CONTRACT", roadmap)
                return LaunchResult(command=spec.command, returncode=0)

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                snapshot, _results = run_loop(repo, roadmap, phase="CONTRACT")

            self.assertEqual(snapshot.phases["CONTRACT"], "planned")
            self.assertEqual(read_events(repo)[-1]["status"], "planned")

    def test_planning_launch_without_current_plan_artifact_blocks_even_with_stale_executed_event(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(repo, provenanced_event(repo, roadmap, "CONTRACT", "executed", action="run"))

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                return LaunchResult(command=spec.command, returncode=0)

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                snapshot, _results = run_loop(repo, roadmap, phase="CONTRACT")

            self.assertEqual(snapshot.current_phase, "CONTRACT")
            self.assertEqual(snapshot.phases["CONTRACT"], "blocked")
            self.assertFalse(snapshot.human_required)
            self.assertEqual(snapshot.blocker_class, "repeated_verification_failure")
            event = read_events(repo)[-1]
            self.assertEqual(event["status"], "blocked")
            self.assertEqual(event["blocker"]["blocker_class"], "repeated_verification_failure")
            self.assertEqual(
                event["metadata"]["missing_plan_after_planning"]["reason"],
                "planning_launch_missing_current_plan_artifact",
            )

    def test_planning_launch_with_mismatched_plan_artifact_reports_diagnostic(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                plan = write_phase_plan(repo, "CONTRACT", roadmap)
                plan.write_text(
                    plan.read_text(encoding="utf-8").replace(
                        "roadmap: specs/phase-plans-v1.md",
                        "roadmap: phase-plans-v1.md",
                    ),
                    encoding="utf-8",
                )
                return LaunchResult(command=spec.command, returncode=0)

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                snapshot, _results = run_loop(repo, roadmap, phase="CONTRACT")

            self.assertEqual(snapshot.phases["CONTRACT"], "blocked")
            self.assertEqual(snapshot.blocker_class, "repeated_verification_failure")
            self.assertIn("does not match the current roadmap", snapshot.blocker_summary or "")
            event = read_events(repo)[-1]
            diagnostics = event["metadata"]["missing_plan_after_planning"]["invalid_plan_artifacts"]
            self.assertEqual(diagnostics[0]["diagnostic"], "mismatched_roadmap_path")

    def test_executed_phase_relaunches_execute_not_plan(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "CONTRACT", roadmap)
            append_event(repo, provenanced_event(repo, roadmap, "CONTRACT", "executed", action="execute"))

            snapshot, results = run_loop(repo, roadmap, phase="CONTRACT", dry_run=True)

            self.assertEqual(snapshot.current_phase, "CONTRACT")
            self.assertIn("codex-execute-phase", " ".join(results[0].command))

    def test_release_dispatch_dirty_release_file_blocks_before_launch(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "CONTRACT", roadmap, extra_frontmatter={"phase_loop_mutation": "release_dispatch"})
            (repo / "pyproject.toml").write_text("[project]\nname = 'fixture'\n")

            with patch("phase_loop_runtime.runner.launch_with_spec") as fake_launch:
                snapshot, results = run_loop(repo, roadmap, phase="CONTRACT")

            fake_launch.assert_not_called()
            self.assertEqual(results, [])
            self.assertEqual(snapshot.current_phase, "CONTRACT")
            self.assertEqual(snapshot.phases["CONTRACT"], "blocked")
            self.assertTrue(snapshot.human_required)
            self.assertEqual(snapshot.blocker_class, "dirty_worktree_conflict")
            event = read_events(repo)[-1]
            self.assertEqual(event["metadata"]["reason"], "dirty_release_affecting_paths")
            self.assertIn("pyproject.toml", event["metadata"]["dirty_paths"])

    def test_release_dispatch_clean_tree_launches_child(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "CONTRACT", roadmap, extra_frontmatter={"phase_loop_mutation": "release_dispatch"})

            with patch("phase_loop_runtime.runner.launch_with_spec", return_value=LaunchResult(command=["codex", "exec"], returncode=0)) as fake_launch:
                _snapshot, results = run_loop(repo, roadmap, phase="CONTRACT")

            fake_launch.assert_called_once()
            self.assertEqual(len(results), 1)

    def test_release_dispatch_missing_origin_base_blocks_when_origin_exists(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = make_repo(root)
            remote = root / "origin.git"
            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True, stdout=subprocess.DEVNULL)
            subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=repo, check=True)
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "CONTRACT", roadmap, extra_frontmatter={"phase_loop_mutation": "release_dispatch"})

            with patch("phase_loop_runtime.runner.launch_with_spec") as fake_launch:
                snapshot, results = run_loop(repo, roadmap, phase="CONTRACT")

            fake_launch.assert_not_called()
            self.assertEqual(results, [])
            self.assertEqual(snapshot.phases["CONTRACT"], "blocked")
            self.assertEqual(snapshot.blocker_class, "branch_sync_conflict")
            self.assertEqual(read_events(repo)[-1]["metadata"]["reason"], "base_ref_unavailable")

    def test_blocked_phase_does_not_relaunch_by_default(self):
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
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="fixture",
                    blocker={
                        "human_required": True,
                        "blocker_class": "dirty_worktree_conflict",
                        "blocker_summary": "Clean worktree required.",
                        "required_human_inputs": ("clean worktree",),
                    },
                    **event_provenance(roadmap, "CONTRACT"),
                ),
            )

            with patch("phase_loop_runtime.runner.launch_with_spec") as fake_launch:
                snapshot, results = run_loop(repo, roadmap)

            fake_launch.assert_not_called()
            self.assertEqual(results, [])
            self.assertEqual(snapshot.current_phase, "CONTRACT")
            self.assertEqual(snapshot.phases["CONTRACT"], "blocked")
            self.assertTrue(snapshot.human_required)

    def test_non_human_blocked_phase_launches_repair(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("README.md",))
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add repair plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="CONTRACT",
                    action="plan",
                    status="blocked",
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="fixture",
                    blocker={
                        "human_required": False,
                        "blocker_summary": "Split release preparation from release dispatch.",
                    },
                    metadata={
                        "artifacts": {
                            "log": str(repo / ".phase-loop" / "runs" / "x" / "output.log"),
                            "terminal": str(repo / ".phase-loop" / "runs" / "x" / "terminal-summary.json"),
                            "metadata": str(repo / ".phase-loop" / "runs" / "x" / "launch.json"),
                        },
                        "terminal_summary": {
                            "terminal_status": "executed",
                            "verification_status": "blocked",
                            "next_action": "Repair the mixed release scope.",
                            "dirty_paths": ["README.md"],
                            "phase_owned_dirty": True,
                            "phase_owned_dirty_paths": ["README.md"],
                            "unowned_dirty_paths": [],
                            "pre_existing_dirty_paths": [],
                        },
                    },
                    **event_provenance(roadmap, "CONTRACT"),
                ),
            )
            commands: list[list[str]] = []

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                commands.append(spec.prompt_bundle.render_prompt())
                return LaunchResult(command=spec.command, returncode=0)

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                snapshot, results = run_loop(repo, roadmap, max_phases=1)

            self.assertEqual(len(results), 1)
            prompt = commands[0]
            self.assertIn("Repair the non-human phase-loop blocker for CONTRACT", prompt)
            self.assertIn("Split release preparation from release dispatch.", prompt)
            self.assertIn("Repair checklist:", prompt)
            self.assertIn("phase-loop handoff", prompt)
            self.assertIn("phase-loop status", prompt)
            self.assertIn("dirty_paths=README.md", prompt)
            self.assertEqual(snapshot.current_phase, "CONTRACT")
            self.assertEqual(snapshot.phases["CONTRACT"], "blocked")
            self.assertFalse(snapshot.human_required)

    def test_repeated_repair_failure_pivots_to_configured_fallback_executor(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("README.md",))
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add repair plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            blocker = {
                "human_required": False,
                "blocker_class": "repeated_verification_failure",
                "blocker_summary": "Gemini repair emitted the same malformed closeout.",
                "required_human_inputs": (),
                "access_attempts": (),
            }
            for _ in range(2):
                append_event(
                    repo,
                    LoopEvent(
                        timestamp=utc_now(),
                        repo=str(repo),
                        roadmap=str(roadmap),
                        phase="CONTRACT",
                        action="run",
                        status="blocked",
                        model="auto",
                        reasoning_effort="medium",
                        source="fixture",
                        blocker=blocker,
                        metadata={
                            "launch_request": {
                                "executor": "gemini",
                                "action": "repair",
                                "repo": str(repo),
                                "roadmap": str(roadmap),
                                "phase": "CONTRACT",
                                "plan": str(plan),
                            },
                            "terminal_summary": {
                                "terminal_status": "blocked",
                                "verification_status": "blocked",
                                "next_action": "Retry repair with another executor.",
                                "dirty_paths": [],
                            },
                        },
                        **event_provenance(roadmap, "CONTRACT"),
                    ),
                )
            launched: list[str] = []

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                launched.append(spec.executor)
                return LaunchResult(
                    command=spec.command,
                    returncode=0,
                    output=build_fake_automation_output(status="complete", verification_status="passed"),
                    executor=spec.executor,
                )

            with patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type("Preflight", (), {"ok": True, "metadata": {"probes": []}})(),
            ), patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                snapshot, results = run_loop(
                    repo,
                    roadmap,
                    phase="CONTRACT",
                    executor="gemini",
                    fallback_executors=("claude",),
                )

            self.assertEqual(len(results), 1)
            self.assertEqual(launched, ["claude"])
            self.assertEqual(snapshot.phases["CONTRACT"], "complete")
            event = read_events(repo)[-1]
            self.assertEqual(event["metadata"]["repair_loop_guard"]["status"], "pivoted")
            self.assertEqual(event["metadata"]["repair_loop_guard"]["from_executor"], "gemini")
            self.assertEqual(event["metadata"]["repair_loop_guard"]["to_executor"], "claude")

    def test_repeated_repair_failure_blocks_without_configured_fallback_executor(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("README.md",))
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add repair plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            blocker = {
                "human_required": False,
                "blocker_class": "repeated_verification_failure",
                "blocker_summary": "Gemini repair emitted the same malformed closeout.",
                "required_human_inputs": (),
                "access_attempts": (),
            }
            for _ in range(2):
                append_event(
                    repo,
                    LoopEvent(
                        timestamp=utc_now(),
                        repo=str(repo),
                        roadmap=str(roadmap),
                        phase="CONTRACT",
                        action="run",
                        status="blocked",
                        model="auto",
                        reasoning_effort="medium",
                        source="fixture",
                        blocker=blocker,
                        metadata={
                            "launch_request": {
                                "executor": "gemini",
                                "action": "repair",
                                "repo": str(repo),
                                "roadmap": str(roadmap),
                                "phase": "CONTRACT",
                                "plan": str(plan),
                            },
                            "terminal_summary": {
                                "terminal_status": "blocked",
                                "verification_status": "blocked",
                                "next_action": "Retry repair with another executor.",
                                "dirty_paths": [],
                            },
                        },
                        **event_provenance(roadmap, "CONTRACT"),
                    ),
                )

            with patch("phase_loop_runtime.runner.launch_with_spec") as fake_launch:
                snapshot, results = run_loop(repo, roadmap, phase="CONTRACT", executor="gemini")

            fake_launch.assert_not_called()
            self.assertEqual(results, [])
            self.assertEqual(snapshot.phases["CONTRACT"], "blocked")
            event = read_events(repo)[-1]
            self.assertEqual(event["metadata"]["repair_loop_guard"]["status"], "blocked")
            self.assertIn("no fallback executor was configured", event["blocker"]["blocker_summary"])

    def test_non_human_blocked_phase_without_trusted_repair_context_stays_blocked(self):
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
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="fixture",
                    blocker={
                        "human_required": False,
                        "blocker_summary": "Repair evidence is incomplete.",
                    },
                    **event_provenance(roadmap, "CONTRACT"),
                ),
            )

            with patch("phase_loop_runtime.runner.launch_with_spec") as fake_launch:
                snapshot, results = run_loop(repo, roadmap, max_phases=1)

            fake_launch.assert_not_called()
            self.assertEqual(results, [])
            self.assertEqual(snapshot.current_phase, "CONTRACT")
            self.assertEqual(snapshot.phases["CONTRACT"], "blocked")
            self.assertFalse(snapshot.human_required)
            self.assertIn("trusted repair context is incomplete", snapshot.blocker_summary)
            event = read_events(repo)[-1]
            self.assertEqual(event["metadata"]["repair_launch"]["reason"], "missing_trusted_repair_context")
            self.assertEqual(event["metadata"]["repair_launch"]["missing"], ["terminal_summary", "phase_plan"])

    def test_product_decision_blocker_does_not_launch_repair(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = make_code_index_blocker_fixture(Path(td))
            repo = fixture.repo
            roadmap = fixture.roadmap
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase=fixture.execute_phase,
                    action="plan",
                    status="blocked",
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="fixture",
                    blocker={
                        "human_required": True,
                        "blocker_class": "product_decision_missing",
                        "blocker_summary": "code_index requires an explicit product decision.",
                        "required_human_inputs": ("choose the product behavior",),
                    },
                    **event_provenance(roadmap, fixture.execute_phase),
                ),
            )

            with patch("phase_loop_runtime.runner.launch_with_spec") as fake_launch:
                snapshot, results = run_loop(repo, roadmap, max_phases=1)

            fake_launch.assert_not_called()
            self.assertEqual(results, [])
            self.assertEqual(snapshot.current_phase, fixture.execute_phase)
            self.assertEqual(snapshot.phases[fixture.execute_phase], "blocked")
            self.assertTrue(snapshot.human_required)
            self.assertEqual(snapshot.blocker_class, "product_decision_missing")

    def test_greenfield_fixture_complete_status_with_dirty_worktree_becomes_awaiting_phase_closeout(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = make_greenfield_closeout_fixture(Path(td))
            repo = fixture.repo
            roadmap = fixture.roadmap

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                report = repo / "artifacts" / "enforce-report.json"
                report.parent.mkdir(exist_ok=True)
                report.write_text("{\"status\":\"passed\"}\n")
                append_event(repo, provenanced_event(repo, roadmap, fixture.execute_phase, "complete", action="execute"))
                return LaunchResult(command=spec.command, returncode=0)

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                snapshot, _results = run_loop(repo, roadmap, phase=fixture.execute_phase)

            self.assertEqual(snapshot.phases[fixture.execute_phase], "awaiting_phase_closeout")
            self.assertFalse(snapshot.human_required)
            self.assertIsNone(snapshot.blocker_class)
            event = read_events(repo)[-1]
            self.assertEqual(event["status"], "awaiting_phase_closeout")
            self.assertIsNone(event.get("blocker"))
            self.assertEqual(event["metadata"]["completion_dirty_worktree"]["reason"], "complete_status_with_dirty_worktree")
            self.assertEqual(event["metadata"]["completion_dirty_worktree"]["terminal_status"], "complete")
            self.assertIn("artifacts/enforce-report.json", event["metadata"]["completion_dirty_worktree"]["dirty_paths"])

    def test_complete_status_with_preexisting_dirty_worktree_requires_human(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("README.md",))
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            (repo / "README.md").write_text("pre-existing user work\n")

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                (repo / "README.md").write_text("child touched pre-existing dirty work\n")
                append_event(repo, provenanced_event(repo, roadmap, "CONTRACT", "complete", action="execute"))
                return LaunchResult(command=spec.command, returncode=0)

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                snapshot, _results = run_loop(repo, roadmap, phase="CONTRACT")

            self.assertEqual(snapshot.phases["CONTRACT"], "blocked")
            self.assertTrue(snapshot.human_required)
            self.assertEqual(snapshot.blocker_class, "dirty_worktree_conflict")
            event = read_events(repo)[-1]
            self.assertTrue(event["blocker"]["human_required"])
            self.assertIn("README.md", event["blocker"]["required_human_inputs"][0])

    def test_unowned_dirty_output_fails_closed_to_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("docs/owned.md",))
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                (repo / "README.md").write_text("unowned dirty output\n")
                append_event(repo, provenanced_event(repo, roadmap, "CONTRACT", "complete", action="execute"))
                return LaunchResult(command=spec.command, returncode=0)

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                snapshot, _results = run_loop(repo, roadmap, phase="CONTRACT")

            self.assertEqual(snapshot.phases["CONTRACT"], "blocked")
            self.assertTrue(snapshot.human_required)
            self.assertEqual(snapshot.unowned_dirty_paths, ("README.md",))
            event = read_events(repo)[-1]
            self.assertEqual(event["metadata"]["completion_dirty_worktree"]["unowned_dirty_paths"], ["README.md"])
            self.assertFalse(event["metadata"]["completion_dirty_worktree"]["phase_owned_dirty"])

    def test_malformed_plan_ownership_fails_closed_to_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(
                repo,
                "CONTRACT",
                roadmap,
                body=(
                    "# CONTRACT\n\n"
                    "## Lanes\n\n"
                    "### SL-0 - Broken\n"
                    "- **Owned files**: README.md\n"
                ),
            )
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add malformed plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                (repo / "README.md").write_text("dirty output\n")
                append_event(repo, provenanced_event(repo, roadmap, "CONTRACT", "complete", action="execute"))
                return LaunchResult(command=spec.command, returncode=0)

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                snapshot, _results = run_loop(repo, roadmap, phase="CONTRACT")

            self.assertEqual(snapshot.phases["CONTRACT"], "blocked")
            self.assertFalse(snapshot.human_required)
            event = read_events(repo)[-1]
            self.assertIn("ownership evidence failed closed", event["blocker"]["blocker_summary"])
            self.assertIn("malformed_owned_files:### SL-0 - Broken", event["metadata"]["completion_dirty_worktree"]["ownership_errors"])

    def test_control_only_plan_dirty_can_closeout_even_with_incomplete_lane_contract(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                plan = write_phase_plan(
                    repo,
                    "CONTRACT",
                    roadmap,
                    body="# CONTRACT\n\n## Notes\n\nNo lane sections yet.\n",
                )
                return LaunchResult(
                    command=spec.command,
                    returncode=0,
                    output=build_fake_automation_output(
                        status="planned",
                        next_skill="codex-execute-phase",
                        next_command=f"codex-execute-phase {plan.relative_to(repo)}",
                        verification_status="passed",
                        artifact=str(plan.relative_to(repo)),
                    ),
                    log_path=str(log_path) if log_path else None,
                )

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                snapshot, _results = run_loop(repo, roadmap, phase="CONTRACT", closeout_mode="commit")

            self.assertEqual(snapshot.phases["CONTRACT"], "planned")
            self.assertEqual(subprocess.check_output(["git", "-C", str(repo), "status", "--short"], text=True).strip(), "")
            events = read_events(repo)
            dirty_event = self._latest_event_with_metadata(events, "plan_dirty_worktree")
            self.assertEqual(dirty_event["status"], "awaiting_phase_closeout")
            self.assertEqual(dirty_event["metadata"]["plan_dirty_worktree"]["phase_owned_dirty"], True)
            self.assertEqual(dirty_event["metadata"]["plan_dirty_worktree"]["ownership_errors"], [])

    def test_live_mixed_harness_plan_execute_matrix(self):
        executors = enabled_live_smoke_executors()
        if "codex" not in executors or len(executors) < 2:
            self.skipTest("enable PHASE_LOOP_ENABLE_CODEX_LIVE_TEST=1 and one non-Codex live harness flag to run mixed live smokes")

        mixed_pairs = [("codex", executor) for executor in executors if executor != "codex"]
        mixed_pairs.extend((executor, "codex") for executor in executors if executor != "codex")

        for planner, executee in mixed_pairs:
            with self.subTest(planner=planner, executee=executee):
                with tempfile.TemporaryDirectory() as td:
                    fixture = make_mixed_harness_live_fixture(Path(td))

                    plan_result = run_live_smoke(fixture.repo, fixture.roadmap, fixture.execute_phase, planner)
                    self.assertEqual(
                        plan_result.returncode,
                        0,
                        msg=f"{planner} planning stdout:\n{plan_result.stdout}\n\nstderr:\n{plan_result.stderr}",
                    )
                    self.assertTrue((fixture.repo / "plans" / "phase-plan-v1-DOCS.md").exists())
                    plan_event = read_events(fixture.repo)[-1]
                    self.assertEqual(plan_event["metadata"]["launch"]["executor"], planner)

                    execute_result = run_live_smoke(fixture.repo, fixture.roadmap, fixture.execute_phase, executee)
                    self.assertEqual(
                        execute_result.returncode,
                        0,
                        msg=f"{executee} execute stdout:\n{execute_result.stdout}\n\nstderr:\n{execute_result.stderr}",
                    )
                    events = read_events(fixture.repo)
                    self.assertEqual(events[-1]["metadata"]["launch"]["executor"], executee)
                    self.assertTrue((fixture.repo / ".phase-loop" / "runs").exists())
                    self.assertTrue((fixture.repo / ".phase-loop" / "tui-handoff.md").exists())

    def test_delegated_child_auth_preflight_blocks_before_launch(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("notes.md",))
            request = build_fake_delegation_request(
                request_id="req-auth-block",
                target_executor="claude",
                product_action="review",
            )

            with patch(
                "phase_loop_runtime.runner.run_auth_preflight",
                return_value=type(
                    "Preflight",
                    (),
                    {
                        "ok": False,
                        "blocker_class": "account_or_billing_setup",
                        "blocker_summary": "Claude auth missing",
                        "metadata": {"executor": "claude", "probes": []},
                        "suggested_ttl_seconds": 300,
                        "demoted_to": "proof_gated",
                    },
                )(),
            ), patch("phase_loop_runtime.runner.launch_with_spec") as fake_launch:
                outcome = launch_delegated_child(
                    repo=repo,
                    roadmap=roadmap,
                    parent_phase="CONTRACT",
                    parent_action="execute",
                    parent_executor="codex",
                    plan=plan,
                    request=request,
                )

            fake_launch.assert_not_called()
            self.assertEqual(outcome["terminal_summary"]["terminal_status"], "blocked")
            self.assertEqual(outcome["terminal_summary"]["verification_status"], "blocked")
            self.assertEqual(outcome["launch_metadata"]["parent_child"]["child_closeout_result"]["status"], "blocked")
            degradation = load_degradation(repo)["claude"]
            self.assertEqual(degradation.source_phase, "CONTRACT")
            self.assertEqual(degradation.ttl_seconds, 300)
            self.assertEqual(degradation.demoted_to, "proof_gated")

    def test_live_runner_brokered_delegation_matrix(self):
        executors = enabled_live_smoke_executors()
        if "claude" not in executors:
            self.skipTest("enable PHASE_LOOP_ENABLE_CLAUDE_LIVE_TEST=1 to run runner-brokered delegation proof")

        pairs = []
        if "codex" in executors:
            pairs.append(("codex", "claude"))
            pairs.append(("claude", "codex"))
        pairs.append(("claude", "claude"))

        for parent_executor, child_executor in pairs:
            with self.subTest(parent_executor=parent_executor, child_executor=child_executor):
                with tempfile.TemporaryDirectory() as td:
                    repo = make_repo(Path(td))
                    roadmap = repo / "specs" / "phase-plans-v1.md"
                    plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("README.md",))
                    subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True, stdout=subprocess.DEVNULL)
                    subprocess.run(["git", "commit", "-m", "add live delegation fixture"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
                    request = build_fake_delegation_request(
                        request_id=f"req-{parent_executor}-to-{child_executor}",
                        target_executor=child_executor,
                        product_action="review",
                        owned_files=("README.md",),
                        expected_output="Shared review closeout",
                    )

                    outcome = launch_delegated_child(
                        repo=repo,
                        roadmap=roadmap,
                        parent_phase="CONTRACT",
                        parent_action="execute",
                        parent_executor=parent_executor,
                        parent_run_id="live-parent",
                        plan=plan,
                        request=request,
                        bypass_approvals=True,
                    )

                    self.assertEqual(outcome["decision"]["status"], "approved")
                    self.assertEqual(outcome["launch_metadata"]["delegation_request"]["target_executor"], child_executor)
                    self.assertEqual(outcome["launch_metadata"]["parent_child"]["parent_executor"], parent_executor)
                    self.assertEqual(outcome["launch_metadata"]["parent_child"]["child_executor"], child_executor)
                    self.assertTrue(Path(outcome["artifacts"]["metadata"]).exists())
                    self.assertTrue(Path(outcome["artifacts"]["terminal"]).exists())
                    self.assertIn(
                        outcome["launch_metadata"]["parent_child"]["child_closeout_result"]["status"],
                        {"complete", "executed", "planned", "awaiting_phase_closeout"},
                    )

    @unittest.skipUnless(claude_team_live_smoke_enabled(), "set PHASE_LOOP_ENABLE_CLAUDE_LIVE_TEST=1 and PHASE_LOOP_ENABLE_CLAUDE_TEAM_LIVE_TEST=1 to run Claude native-team proof")
    def test_live_claude_agent_team_launch_records_task_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = make_live_team_fixture(Path(td))
            snapshot, results = run_loop(
                repo=fixture.repo,
                roadmap=fixture.roadmap,
                phase=fixture.execute_phase,
                executor="claude",
                max_phases=1,
                action="run",
                model_profile="execute",
                product_action_override="execute",
                claude_execution_mode="agent_team",
                bypass_approvals=True,
            )

            self.assertEqual(len(results), 1)
            launch_dir = max((fixture.repo / ".phase-loop" / "runs").iterdir(), key=lambda item: item.name)
            self.assertTrue((launch_dir / "task-snapshot.json").exists())
            self.assertIn(snapshot.phases[fixture.execute_phase], {"complete", "executed", "planned"})

    def test_awaiting_phase_closeout_does_not_relaunch_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(repo, provenanced_event(repo, roadmap, "CONTRACT", "awaiting_phase_closeout", action="execute"))

            with patch("phase_loop_runtime.runner.launch_with_spec") as fake_launch:
                snapshot, results = run_loop(repo, roadmap)

            fake_launch.assert_not_called()
            self.assertEqual(results, [])
            self.assertEqual(snapshot.current_phase, "CONTRACT")
            self.assertEqual(snapshot.phases["CONTRACT"], "awaiting_phase_closeout")

    def test_commit_closeout_stages_phase_owned_output_and_appends_terminal_status(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("README.md",))
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            (repo / "README.md").write_text("phase output\n")
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="CONTRACT",
                    action="execute",
                    status="awaiting_phase_closeout",
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="fixture",
                    metadata={
                        "completion_dirty_worktree": {
                            "reason": "complete_status_with_dirty_worktree",
                            "terminal_status": "complete",
                            "dirty_paths": ["README.md"],
                            "phase_owned_dirty_paths": ["README.md"],
                            "unowned_dirty_paths": [],
                            "pre_existing_dirty_paths": [],
                            "phase_owned_dirty": True,
                        }
                    },
                    **event_provenance(roadmap, "CONTRACT"),
                ),
            )

            snapshot, results = run_loop(repo, roadmap, closeout_mode="commit")

            self.assertEqual(results, [])
            self.assertEqual(snapshot.phases["CONTRACT"], "complete")
            event = read_events(repo)[-1]
            self.assertEqual(event["status"], "complete")
            self.assertEqual(event["metadata"]["closeout"]["closeout_mode"], "commit")
            self.assertEqual(event["metadata"]["closeout"]["closeout_action"], "commit")
            self.assertIn("closeout_commit", event["metadata"]["closeout"])
            self.assertEqual(subprocess.check_output(["git", "-C", str(repo), "status", "--short"], text=True).strip(), "")

    def test_commit_closeout_promotes_executed_terminal_status_to_complete_when_clean(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("README.md",))
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            (repo / "README.md").write_text("phase output\n")
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="CONTRACT",
                    action="execute",
                    status="awaiting_phase_closeout",
                    model="gpt-5.4",
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
                    **event_provenance(roadmap, "CONTRACT"),
                ),
            )

            snapshot, results = run_loop(repo, roadmap, closeout_mode="commit")

            self.assertEqual(results, [])
            self.assertEqual(snapshot.phases["CONTRACT"], "complete")
            event = read_events(repo)[-1]
            self.assertEqual(event["status"], "complete")
            self.assertEqual(event["metadata"]["closeout"]["closeout_action"], "commit")
            self.assertEqual(event["metadata"]["closeout"]["verification_status"], "passed")
            self.assertEqual(subprocess.check_output(["git", "-C", str(repo), "status", "--short"], text=True).strip(), "")

    def test_commit_closeout_runs_immediately_after_execute_leaves_phase_owned_dirty(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("README.md",))
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                (repo / "README.md").write_text("phase output\n")
                return LaunchResult(command=spec.command, returncode=0, output="done", log_path=str(log_path) if log_path else None)

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                snapshot, results = run_loop(repo, roadmap, phase="CONTRACT", closeout_mode="commit")

            self.assertEqual(len(results), 1)
            self.assertEqual(snapshot.phases["CONTRACT"], "complete")
            events = read_events(repo)
            dirty_event = self._latest_event_with_metadata(events, "incomplete_execute_dirty_worktree")
            self.assertEqual(dirty_event["status"], "awaiting_phase_closeout")
            self.assertEqual(events[-1]["status"], "complete")
            self.assertEqual(events[-1]["metadata"]["closeout"]["closeout_action"], "commit")
            self.assertEqual(subprocess.check_output(["git", "-C", str(repo), "status", "--short"], text=True).strip(), "")

    def test_commit_closeout_keeps_planning_artifact_status_planned(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("README.md",))
                return LaunchResult(
                    command=spec.command,
                    returncode=0,
                    output=build_fake_automation_output(
                        status="complete",
                        verification_status="passed",
                        artifact=str(plan.relative_to(repo)),
                        artifact_state="modified",
                    ),
                    log_path=str(log_path) if log_path else None,
                )

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                snapshot, results = run_loop(repo, roadmap, phase="CONTRACT", closeout_mode="commit")

            self.assertEqual(len(results), 1)
            self.assertEqual(snapshot.phases["CONTRACT"], "planned")
            events = read_events(repo)
            dirty_event = self._latest_event_with_metadata(events, "plan_dirty_worktree")
            self.assertEqual(dirty_event["status"], "awaiting_phase_closeout")
            self.assertEqual(dirty_event["metadata"]["plan_dirty_worktree"]["terminal_status"], "planned")
            self.assertEqual(events[-1]["status"], "planned")
            self.assertEqual(events[-1]["metadata"]["closeout"]["closeout_action"], "commit")
            self.assertEqual(events[-1]["metadata"]["closeout"]["verification_status"], "not_run")
            self.assertEqual(subprocess.check_output(["git", "-C", str(repo), "status", "--short"], text=True).strip(), "")

    def test_push_closeout_records_refusal_without_remote_mutation_when_guard_rejects(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("README.md",))
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            (repo / "README.md").write_text("phase output\n")
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="CONTRACT",
                    action="execute",
                    status="awaiting_phase_closeout",
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="fixture",
                    metadata={
                        "completion_dirty_worktree": {
                            "reason": "complete_status_with_dirty_worktree",
                            "terminal_status": "complete",
                            "dirty_paths": ["README.md"],
                            "phase_owned_dirty_paths": ["README.md"],
                            "unowned_dirty_paths": [],
                            "pre_existing_dirty_paths": [],
                            "phase_owned_dirty": True,
                        }
                    },
                    **event_provenance(roadmap, "CONTRACT"),
                ),
            )

            with patch("phase_loop_runtime.runner.resolve_closeout_push_target", return_value={"allowed": False, "push_ref": "refs/heads/main", "refusal_reason": "behind_upstream"}):
                snapshot, results = run_loop(repo, roadmap, closeout_mode="push")

            self.assertEqual(results, [])
            self.assertEqual(snapshot.phases["CONTRACT"], "complete")
            event = read_events(repo)[-1]
            self.assertEqual(event["metadata"]["closeout"]["closeout_mode"], "push")
            self.assertEqual(event["metadata"]["closeout"]["closeout_action"], "push_refused")
            self.assertEqual(event["metadata"]["closeout"]["closeout_refusal_reason"], "behind_upstream")

    def test_detect_dirty_renames_pairs_filesystem_move_by_blob_hash(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            services_dir = repo / "services"
            services_dir.mkdir()
            src = services_dir / "foo.test.ts"
            src.write_text("export const foo = 1;\n", encoding="utf-8")
            subprocess.run(["git", "add", str(src.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "add fixture file"], cwd=repo, check=True)

            tests_dir = repo / "tests"
            tests_dir.mkdir()
            dst = tests_dir / "foo.test.ts"
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            src.unlink()

            renames = _detect_dirty_renames(repo)
            self.assertEqual(renames.get("services/foo.test.ts"), "tests/foo.test.ts")

    def test_detect_dirty_renames_captures_git_reported_renames(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            services_dir = repo / "services"
            services_dir.mkdir()
            src = services_dir / "foo.test.ts"
            src.write_text("export const foo = 2;\n", encoding="utf-8")
            subprocess.run(["git", "add", str(src.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "add fixture file"], cwd=repo, check=True)

            tests_dir = repo / "tests"
            tests_dir.mkdir()
            subprocess.run(
                ["git", "mv", "services/foo.test.ts", "tests/foo.test.ts"],
                cwd=repo,
                check=True,
            )

            renames = _detect_dirty_renames(repo)
            self.assertEqual(renames.get("services/foo.test.ts"), "tests/foo.test.ts")

    def test_detect_dirty_renames_skips_content_modified_move(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            services_dir = repo / "services"
            services_dir.mkdir()
            src = services_dir / "foo.test.ts"
            src.write_text("export const foo = 3;\n", encoding="utf-8")
            subprocess.run(["git", "add", str(src.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "add fixture file"], cwd=repo, check=True)

            tests_dir = repo / "tests"
            tests_dir.mkdir()
            dst = tests_dir / "foo.test.ts"
            dst.write_text("export const foo = 999;  // rewrote\n", encoding="utf-8")
            src.unlink()

            renames = _detect_dirty_renames(repo)
            self.assertNotIn("services/foo.test.ts", renames)

    def test_classify_dirty_paths_promotes_rename_source_when_destination_owned(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            services_dir = repo / "services"
            services_dir.mkdir()
            src = services_dir / "foo.test.ts"
            src.write_text("export const foo = 4;\n", encoding="utf-8")
            subprocess.run(["git", "add", str(src.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "add fixture file"], cwd=repo, check=True)

            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(
                repo,
                "RUNNER",
                roadmap,
                owned_files=("tests/foo.test.ts",),
            )
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "add plan"], cwd=repo, check=True)

            tests_dir = repo / "tests"
            tests_dir.mkdir()
            dst = tests_dir / "foo.test.ts"
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            src.unlink()

            summary = _classify_dirty_paths(
                repo,
                roadmap,
                plan,
                pre_launch_dirty_paths=[],
                post_launch_dirty_paths=["services/foo.test.ts", "tests/foo.test.ts"],
            )
            self.assertIn("services/foo.test.ts", summary["phase_owned_dirty_paths"])
            self.assertIn("tests/foo.test.ts", summary["phase_owned_dirty_paths"])
            self.assertEqual(summary["unowned_dirty_paths"], [])
            self.assertTrue(summary["phase_owned_dirty"])
            self.assertIn("services/foo.test.ts", summary["rename_sources_promoted"])

    def test_classify_dirty_paths_does_not_promote_when_destination_not_owned(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            services_dir = repo / "services"
            services_dir.mkdir()
            src = services_dir / "foo.test.ts"
            src.write_text("export const foo = 5;\n", encoding="utf-8")
            subprocess.run(["git", "add", str(src.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "add fixture file"], cwd=repo, check=True)

            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(
                repo,
                "RUNNER",
                roadmap,
                owned_files=("docs/intentionally-unrelated.md",),
            )
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "add plan"], cwd=repo, check=True)

            elsewhere_dir = repo / "elsewhere"
            elsewhere_dir.mkdir()
            dst = elsewhere_dir / "foo.test.ts"
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            src.unlink()

            summary = _classify_dirty_paths(
                repo,
                roadmap,
                plan,
                pre_launch_dirty_paths=[],
                post_launch_dirty_paths=["services/foo.test.ts", "elsewhere/foo.test.ts"],
            )
            self.assertNotIn("services/foo.test.ts", summary["phase_owned_dirty_paths"])
            self.assertEqual(summary["rename_sources_promoted"], [])

    def test_run_loop_surfaces_malformed_execution_policy_as_contract_bug(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.write_text(
                roadmap.read_text(encoding="utf-8")
                + "\n## Execution Policy\n"
                + "- work-unit defaults:\n"
                + "  - effort: high\n"
                + "  - model: codex\n",
                encoding="utf-8",
            )

            snapshot, results = run_loop(repo, roadmap, phase="RUNNER", dry_run=True)

            self.assertFalse(any(r.failed for r in results))
            event = read_events(repo)[-1]
            self.assertEqual(event["status"], "blocked")
            self.assertEqual(event["blocker"]["blocker_class"], "contract_bug")
            self.assertIn("malformed Execution Policy line", event["blocker"]["blocker_summary"])
            self.assertIn(str(roadmap), event["blocker"]["blocker_summary"])
            self.assertEqual(
                event["metadata"]["execution_policy_parse_error"]["path"], str(roadmap)
            )


if __name__ == "__main__":
    unittest.main()
