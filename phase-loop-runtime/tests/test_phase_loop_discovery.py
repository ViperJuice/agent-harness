import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.events import append_event
from phase_loop_runtime.discovery import (
    WORKFLOW_EXECUTE_SKILLS,
    classify_phase_team_eligibility,
    dispatch_hints_for_action,
    expanded_dirty_ownership_matches,
    execution_policy_dispatch_hints,
    execution_policy_for_action,
    execution_policy_for_lane,
    find_plan_artifact,
    handoff_matches_roadmap,
    latest_skill_handoff,
    latest_workflow_handoff,
    parse_automation_status,
    parse_dispatch_hints,
    parse_execution_policy,
    parse_pipeline_plan_metadata,
    parse_plan_ownership,
    parse_roadmap_phases,
    pipeline_plan_metadata_diagnostic,
    plan_artifact_diagnostic,
    previous_phase_owned_dirty_paths,
    plan_is_stale,
    repo_identity,
    roadmap_closeout_evidence_audit_enabled,
    roadmap_fingerprint,
    select_roadmap,
)
from phase_loop_runtime.models import LoopEvent, StateSnapshot, utc_now
from phase_loop_runtime.plan_manifest import DotfilesPlanEntry, DotfilesPlanRef, append_entry
from phase_loop_runtime.provenance import event_provenance
from phase_loop_runtime.state import write_state
from phase_loop_test_utils import make_repo, write_phase_plan
from test_phase_loop_pipeline_bundle import _write_bundle, _write_protected_source

import pytest

# TESTDECOUPLE SL-1 (overlay-dependent): builds a skill/adoption bundle or runs the
# runtime execute path, which resolves the dotfiles skill-source / profile overlay
# (claude-config/*, codex-config/* …) absent standalone. Run-time integration: the
# conftest hook skips it when no dotfiles tree is reachable.
pytestmark = pytest.mark.dotfiles_integration


class PhaseLoopDiscoveryTest(unittest.TestCase):
    def test_previous_phase_owned_dirty_paths_uses_latest_same_phase_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            self._append_dirty_event(repo, roadmap, "RUNNER", ["old.md"])
            self._append_dirty_event(repo, roadmap, "OTHER", ["other.md"])
            self._append_dirty_event(repo, roadmap, "RUNNER", ["latest.md"])

            self.assertEqual(previous_phase_owned_dirty_paths(repo, "RUNNER"), ("latest.md",))

    def test_previous_phase_owned_dirty_paths_uses_terminal_summary_fallback(self):
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
                    metadata={
                        "terminal_summary": {
                            "dirty_paths": ["README.md"],
                            "phase_owned_dirty_paths": ["README.md"],
                        }
                    },
                    **event_provenance(roadmap, "RUNNER"),
                ),
            )

            self.assertEqual(previous_phase_owned_dirty_paths(repo, "RUNNER"), ("README.md",))

    def test_previous_phase_owned_dirty_paths_ignores_malformed_fields(self):
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
                    metadata={
                        "completion_dirty_worktree": {
                            "dirty_paths": ["README.md"],
                            "phase_owned_dirty_paths": "README.md",
                        }
                    },
                    **event_provenance(roadmap, "RUNNER"),
                ),
            )

            self.assertEqual(previous_phase_owned_dirty_paths(repo, "RUNNER"), ())

    def _append_dirty_event(self, repo: Path, roadmap: Path, phase: str, paths: list[str]) -> None:
        append_event(
            repo,
            LoopEvent(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phase=phase,
                action="execute",
                status="awaiting_phase_closeout",
                model="gpt-5.6-terra",
                reasoning_effort="medium",
                source="fixture",
                metadata={
                    "completion_dirty_worktree": {
                        "dirty_paths": paths,
                        "phase_owned_dirty_paths": paths,
                        "unowned_dirty_paths": [],
                        "pre_existing_dirty_paths": [],
                    }
                },
                **event_provenance(roadmap, phase),
            ),
        )

    def test_explicit_roadmap_selection_and_phase_aliases(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = select_roadmap(repo, "specs/phase-plans-v1.md")
            self.assertEqual(parse_roadmap_phases(roadmap), ["CONTRACT", "ACCESS", "RUNNER"])

    def test_plan_prompt_prefers_manifest_artifact_for_suffix_roadmap(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v44-claude-primary-harness-integration.md"
            roadmap.write_text(
                "# Roadmap\n\n"
                "### Phase 1 - Claude Contract Alignment (DFCHCONTRACT)\n",
                encoding="utf-8",
            )
            roadmap_hash = roadmap_fingerprint(roadmap)
            plan = repo / "plans" / "phase-plan-v44-DFCHCONTRACT.md"
            plan.write_text(
                "---\n"
                "phase_loop_plan_version: 1\n"
                "phase: DFCHCONTRACT\n"
                "roadmap: specs/phase-plans-v44-claude-primary-harness-integration.md\n"
                f"roadmap_sha256: {roadmap_hash}\n"
                "---\n"
                "# DFCHCONTRACT\n",
                encoding="utf-8",
            )
            append_entry(
                repo,
                DotfilesPlanEntry(
                    slug="v44-DFCHCONTRACT",
                    file="plans/phase-plan-v44-DFCHCONTRACT.md",
                    type="phase",
                    status="imported",
                    created_at="2026-06-20T00:00:00Z",
                    updated_at="2026-06-20T00:00:00Z",
                    owner_skill="codex-plan-phase",
                    roadmap_ref=DotfilesPlanRef(
                        slug="phase-plans-v44-claude-primary-harness-integration",
                        file="specs/phase-plans-v44-claude-primary-harness-integration.md",
                        type="phase",
                        status="imported",
                    ),
                    phase_alias="DFCHCONTRACT",
                ),
            )

            from phase_loop_runtime.prompts import _expected_plan_artifact_path, build_prompt

            prompt_path = _expected_plan_artifact_path(roadmap, "DFCHCONTRACT")
            prompt = build_prompt("plan", roadmap, phase="DFCHCONTRACT")

            self.assertEqual(prompt_path, "plans/phase-plan-v44-DFCHCONTRACT.md")
            self.assertIn("Write it exactly to `plans/phase-plan-v44-DFCHCONTRACT.md`", prompt.body)

    def test_parse_automation_status_prefers_last_bounded_block(self):
        text = (
            "Earlier file content:\n"
            "automation:\n"
            "  status: planned\n"
            "  next_skill: codex-execute-phase\n"
            "  next_command: codex-execute-phase plans/phase-plan-v1-RUNNER.md\n"
            "  human_required: false\n"
            "  blocker_class: none\n"
            "  blocker_summary: none\n"
            "  required_human_inputs: []\n"
            "  verification_status: not_run\n"
            "\nFinal closeout:\n"
            "automation:\n"
            "  status: blocked\n"
            "  next_skill: none\n"
            "  next_command: none\n"
            "  human_required: false\n"
            "  blocker_class: repeated_verification_failure\n"
            "  blocker_summary: SG-0 failed.\n"
            "  required_human_inputs: []\n"
            "  verification_status: blocked\n"
            "\nmodel_provenance:\n"
            "  model_profile: execute\n"
        )

        parsed = parse_automation_status(text)

        self.assertEqual(parsed["automation_status"], "blocked")
        self.assertEqual(parsed["automation_next_skill"], "none")
        self.assertEqual(parsed["automation_blocker_summary"], "SG-0 failed.")
        self.assertEqual(parsed["automation_verification_status"], "blocked")

    def test_phase_aliases_allow_amendment_suffixes(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v2.md"
            roadmap.write_text(
                "### Phase 1 - Smoke Truth (SMOKETRUTH)\n\n"
                "### Phase 2 - Portal RLS Fix (PORTALRLSFIX) *(amendment, 2026-04-28)*\n\n"
                "### Phase 3 - Smoke Fix (SMOKEFIX)\n"
            )
            self.assertEqual(parse_roadmap_phases(roadmap), ["SMOKETRUTH", "PORTALRLSFIX", "SMOKEFIX"])

    def test_phase_aliases_allow_branch_phase_numbers(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v2.md"
            roadmap.write_text(
                "### Phase 1 - Contract Freeze (P1)\n\n"
                "### Phase 2A - Backend Branch (P2A)\n\n"
                "### Phase 2B - Frontend Branch (P2B)\n\n"
                "### Phase 4.0 - Orientation (P40)\n\n"
                "### Phase 6A - Parallel Branch (P6A, parallel after P1)\n"
            )
            self.assertEqual(parse_roadmap_phases(roadmap), ["P1", "P2A", "P2B", "P40", "P6A"])

    def test_roadmap_closeout_evidence_audit_enabled_from_frontmatter(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v2.md"
            roadmap.write_text(
                "---\n"
                "closeout_evidence_audit: true\n"
                "---\n"
                "# Roadmap\n\n"
                "## Context\n\n"
                "### Phase 0 - Contract (CONTRACT)\n"
            )

            self.assertTrue(roadmap_closeout_evidence_audit_enabled(roadmap))

    def test_roadmap_closeout_evidence_audit_enabled_from_first_h2_block(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v2.md"
            roadmap.write_text(
                "# Roadmap\n\n"
                "## Metadata\n\n"
                "- closeout_evidence_audit: true\n\n"
                "## Context\n\n"
                "### Phase 0 - Contract (CONTRACT)\n"
            )

            self.assertTrue(roadmap_closeout_evidence_audit_enabled(roadmap))

    def test_roadmap_closeout_evidence_audit_absent_defaults_false(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v2.md"
            roadmap.write_text(
                "# Roadmap\n\n"
                "## Context\n\n"
                "General context.\n\n"
                "## Metadata\n\n"
                "- closeout_evidence_audit: true\n\n"
                "### Phase 0 - Contract (CONTRACT)\n"
            )

            self.assertFalse(roadmap_closeout_evidence_audit_enabled(roadmap))

    def test_single_roadmap_selection_and_multiple_roadmap_ambiguity(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            newer = repo / "specs" / "phase-plans-v2.md"
            newer.write_text("### Phase 0 — Docs (DOCS)\n")
            with self.assertRaisesRegex(RuntimeError, "ambiguous roadmap selection"):
                select_roadmap(repo)
            newer.unlink()
            self.assertEqual(select_roadmap(repo).name, "phase-plans-v1.md")

    def test_active_state_selects_roadmap(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            newer = repo / "specs" / "phase-plans-v2.md"
            newer.write_text("### Phase 0 — Docs (DOCS)\n")
            write_state(repo, StateSnapshot(timestamp=utc_now(), repo=str(repo), roadmap=str(roadmap), phases={}))
            self.assertEqual(select_roadmap(repo), roadmap.resolve())

    def test_plan_artifact_detection(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            plan = write_phase_plan(repo, "RUNNER", repo / "specs" / "phase-plans-v1.md")
            self.assertEqual(find_plan_artifact(repo, "runner"), plan.resolve())

    def test_find_plan_artifact_prefers_manifest_only_entry(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = repo / "plans" / "custom-runner-plan.md"
            plan.write_text(
                "---\n"
                "phase_loop_plan_version: 1\n"
                "phase: RUNNER\n"
                "roadmap: specs/phase-plans-v1.md\n"
                f"roadmap_sha256: {roadmap_fingerprint(roadmap)}\n"
                "---\n"
                "# RUNNER\n",
                encoding="utf-8",
            )
            append_entry(repo, self._phase_manifest_entry(plan, roadmap, "RUNNER"))

            self.assertEqual(find_plan_artifact(repo, "RUNNER", roadmap=roadmap), plan.resolve())

    def test_find_plan_artifact_prefers_manifest_on_conflict(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            regex_plan = write_phase_plan(repo, "RUNNER", roadmap)
            manifest_plan = repo / "plans" / "manifest-runner-plan.md"
            manifest_plan.write_text(regex_plan.read_text(encoding="utf-8"), encoding="utf-8")
            append_entry(repo, self._phase_manifest_entry(manifest_plan, roadmap, "RUNNER"))

            self.assertEqual(find_plan_artifact(repo, "RUNNER", roadmap=roadmap), manifest_plan.resolve())

    def test_manifest_escape_hatch_uses_regex_only_behavior(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            regex_plan = write_phase_plan(repo, "RUNNER", roadmap)
            manifest_plan = repo / "plans" / "manifest-runner-plan.md"
            manifest_plan.write_text(regex_plan.read_text(encoding="utf-8"), encoding="utf-8")
            append_entry(repo, self._phase_manifest_entry(manifest_plan, roadmap, "RUNNER"))

            with patch.dict(os.environ, {"PHASE_LOOP_MANIFEST_DISABLED": "1"}):
                self.assertEqual(find_plan_artifact(repo, "RUNNER", roadmap=roadmap), regex_plan.resolve())

    def test_plan_metadata_controls_staleness(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            self.assertFalse(plan_is_stale(plan, roadmap))
            self.assertEqual(find_plan_artifact(repo, "runner", roadmap=roadmap), plan.resolve())

            plan.write_text(plan.read_text().replace(roadmap_fingerprint(roadmap), "0" * 64))
            os.utime(plan, (100, 100))
            os.utime(roadmap, (50, 50))
            self.assertTrue(plan_is_stale(plan, roadmap))
            self.assertIsNone(find_plan_artifact(repo, "runner", roadmap=roadmap))

    def _phase_manifest_entry(self, plan: Path, roadmap: Path, phase: str) -> DotfilesPlanEntry:
        return DotfilesPlanEntry(
            slug=f"manifest-{phase.lower()}",
            file=plan.relative_to(plan.parents[1]).as_posix(),
            type="phase",
            status="committed",
            created_at="2026-05-30T00:00:00Z",
            updated_at="2026-05-30T00:00:00Z",
            owner_skill="codex-plan-phase",
            roadmap_ref=DotfilesPlanRef(
                slug=roadmap.stem,
                file=roadmap.relative_to(plan.parents[1]).as_posix(),
                type="phase",
                status="committed",
            ),
            phase_alias=phase,
            if_gates_produced=("IF-0-PH-1",),
            lanes=("SL-0",),
        )

    def test_plan_artifact_detection_skips_stale_same_alias_candidates(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            old_roadmap = repo / "specs" / "phase-plans-v1.md"
            stale_plan = write_phase_plan(repo, "RUNNER", old_roadmap)
            current_roadmap = repo / "specs" / "phase-plans-v2.md"
            current_roadmap.write_text("# Roadmap\n\n### Phase 0 - Runner (RUNNER)\n")
            current_plan = repo / "plans" / "phase-plan-v2-RUNNER.md"
            current_plan.write_text(
                "---\n"
                "phase_loop_plan_version: 1\n"
                "phase: RUNNER\n"
                "roadmap: specs/phase-plans-v2.md\n"
                f"roadmap_sha256: {roadmap_fingerprint(current_roadmap)}\n"
                "---\n"
                "# RUNNER\n"
            )

            self.assertTrue(plan_is_stale(stale_plan, current_roadmap))
            self.assertEqual(find_plan_artifact(repo, "runner", roadmap=current_roadmap), current_plan.resolve())

    def test_metadata_free_plan_is_stale_for_roadmap_execution(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = repo / "plans" / "phase-plan-v1-RUNNER.md"
            plan.write_text("# RUNNER\n")
            self.assertEqual(find_plan_artifact(repo, "runner"), plan.resolve())
            self.assertTrue(plan_is_stale(plan, roadmap))
            self.assertIsNone(find_plan_artifact(repo, "runner", roadmap=roadmap))

    def test_standalone_plan_without_pipeline_metadata_remains_ready(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)

            metadata = parse_pipeline_plan_metadata(plan)

            self.assertTrue(metadata.empty)
            self.assertIsNone(pipeline_plan_metadata_diagnostic(repo, plan))
            self.assertIsNone(plan_artifact_diagnostic(repo, plan, roadmap, "RUNNER"))
            self.assertFalse(plan_is_stale(plan, roadmap))

    def test_pipeline_metadata_round_trips_from_plan_frontmatter(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            _write_protected_source(repo)
            bundle = _write_bundle(repo)
            bundle_hash = hashlib.sha256(bundle.read_bytes()).hexdigest()
            plan = write_phase_plan(
                repo,
                "RUNNER",
                roadmap,
                extra_frontmatter={
                    "source_bundle": ".pipeline/artifacts/phase-source-bundle.json",
                    "source_bundle_sha256": bundle_hash,
                    "pipeline_phase_id": "pipeline.phase.runner",
                    "pipeline_mode": "pipeline_required",
                },
            )

            metadata = parse_pipeline_plan_metadata(plan)

            self.assertEqual(metadata.source_bundle, ".pipeline/artifacts/phase-source-bundle.json")
            self.assertEqual(metadata.source_bundle_sha256, bundle_hash)
            self.assertEqual(metadata.pipeline_phase_id, "pipeline.phase.runner")
            self.assertTrue(metadata.required)
            self.assertIsNone(pipeline_plan_metadata_diagnostic(repo, plan))
            self.assertIsNone(plan_artifact_diagnostic(repo, plan, roadmap, "RUNNER"))

    def test_pipeline_required_metadata_reports_missing_bundle_fields(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, extra_frontmatter={"pipeline_mode": "pipeline_required"})

            diagnostic = pipeline_plan_metadata_diagnostic(repo, plan)

            self.assertIsNotNone(diagnostic)
            self.assertEqual(diagnostic.kind, "missing_source_bundle")
            self.assertEqual(plan_artifact_diagnostic(repo, plan, roadmap, "RUNNER"), "pipeline_metadata:missing_source_bundle")

    def test_pipeline_required_metadata_reports_missing_bundle_hash(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(
                repo,
                "RUNNER",
                roadmap,
                extra_frontmatter={
                    "source_bundle": ".pipeline/artifacts/phase-source-bundle.json",
                    "pipeline_mode": "pipeline_required",
                },
            )

            diagnostic = pipeline_plan_metadata_diagnostic(repo, plan)

            self.assertIsNotNone(diagnostic)
            self.assertEqual(diagnostic.kind, "missing_source_bundle_sha256")

    def test_pipeline_required_metadata_reports_stale_bundle_hash(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            bundle = repo / ".pipeline" / "artifacts" / "phase-source-bundle.json"
            bundle.parent.mkdir(parents=True)
            bundle.write_text('{"schema":"phase-source-bundle.v1","version":1}', encoding="utf-8")
            plan = write_phase_plan(
                repo,
                "RUNNER",
                roadmap,
                extra_frontmatter={
                    "source_bundle": ".pipeline/artifacts/phase-source-bundle.json",
                    "source_bundle_sha256": "0" * 64,
                    "pipeline_mode": "pipeline_required",
                },
            )

            diagnostic = pipeline_plan_metadata_diagnostic(repo, plan)

            self.assertIsNotNone(diagnostic)
            self.assertEqual(diagnostic.kind, "mismatched_source_bundle_sha256")
            self.assertEqual(diagnostic.expected_sha256, "0" * 64)
            self.assertEqual(diagnostic.actual_sha256, hashlib.sha256(bundle.read_bytes()).hexdigest())

    def test_pipeline_required_metadata_reports_incomplete_source_bundle(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            bundle = _write_bundle(repo, protected_entries=[])
            plan = write_phase_plan(
                repo,
                "RUNNER",
                roadmap,
                extra_frontmatter={
                    "source_bundle": ".pipeline/artifacts/phase-source-bundle.json",
                    "source_bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
                    "pipeline_mode": "pipeline_required",
                },
            )

            diagnostic = pipeline_plan_metadata_diagnostic(repo, plan)

            self.assertIsNotNone(diagnostic)
            assert diagnostic is not None
            self.assertEqual(diagnostic.kind, "missing_protected_source_entries")
            self.assertEqual(plan_artifact_diagnostic(repo, plan, roadmap, "RUNNER"), "pipeline_metadata:missing_protected_source_entries")

    def test_pipeline_required_metadata_reports_unknown_bundle_phase(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            _write_protected_source(repo)
            bundle = _write_bundle(repo, phase_alias="UNKNOWN")
            plan = write_phase_plan(
                repo,
                "RUNNER",
                roadmap,
                extra_frontmatter={
                    "source_bundle": ".pipeline/artifacts/phase-source-bundle.json",
                    "source_bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
                    "pipeline_mode": "pipeline_required",
                },
            )

            diagnostic = pipeline_plan_metadata_diagnostic(repo, plan)

            self.assertIsNotNone(diagnostic)
            assert diagnostic is not None
            self.assertEqual(diagnostic.kind, "unknown_phase_id")

    def test_pipeline_metadata_reports_unknown_mode_as_typed_diagnostic(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, extra_frontmatter={"pipeline_mode": "ambient"})

            diagnostic = pipeline_plan_metadata_diagnostic(repo, plan)

            self.assertIsNotNone(diagnostic)
            self.assertEqual(diagnostic.kind, "invalid_pipeline_mode")

    def test_latest_skill_handoff_validates_identity_and_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = make_repo(root)
            identity = repo_identity(repo)
            plan = repo / "plans" / "phase-plan-v1-RUNNER.md"
            plan.write_text("# RUNNER\n")
            handoff = (
                root
                / "home"
                / ".codex"
                / "skills"
                / "codex-execute-phase"
                / "handoffs"
                / identity.repo_hash
                / identity.branch_slug
                / "latest.md"
            )
            handoff.parent.mkdir(parents=True)
            handoff.write_text(
                "---\n"
                "from: codex-execute-phase\n"
                f"repo: {identity.repo_hash}\n"
                f"repo_root: {repo}\n"
                f"branch: {identity.branch}\n"
                f"branch_slug: {identity.branch_slug}\n"
                f"commit: {identity.commit}\n"
                f"artifact: {plan}\n"
                "---\n"
                "automation:\n"
                "  status: complete\n"
            )
            with patch("phase_loop_runtime.discovery.Path.home", return_value=root / "home"):
                self.assertEqual(latest_skill_handoff(identity, "codex-execute-phase")["automation_status"], "complete")

            handoff.write_text(handoff.read_text().replace(f"branch_slug: {identity.branch_slug}", "branch_slug: other"))
            with patch("phase_loop_runtime.discovery.Path.home", return_value=root / "home"):
                self.assertIsNone(latest_skill_handoff(identity, "codex-execute-phase"))

    def test_latest_workflow_handoff_discovers_supported_non_codex_roots(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = make_repo(root)
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            identity = repo_identity(repo)
            handoff_dir = (
                root / "home" / ".codex" / "skills" / "gemini-execute-phase" / "handoffs" / identity.repo_hash / identity.branch_slug
            )
            handoff_dir.mkdir(parents=True)
            (handoff_dir / "latest.md").write_text(
                "---\n"
                "from: gemini-execute-phase\n"
                "timestamp: 2026-04-26T00:00:00Z\n"
                f"repo: {identity.repo_hash}\n"
                f"repo_root: {repo}\n"
                f"branch: {identity.branch}\n"
                f"branch_slug: {identity.branch_slug}\n"
                f"commit: {identity.commit}\n"
                f"artifact: {plan}\n"
                "---\n"
                "automation:\n"
                "  status: complete\n"
            )
            with patch("phase_loop_runtime.discovery.Path.home", return_value=root / "home"):
                handoff = latest_workflow_handoff(identity, repo, roadmap, WORKFLOW_EXECUTE_SKILLS)
            self.assertEqual(handoff["automation_status"], "complete")
            self.assertEqual(handoff["workflow_skill"], "gemini-execute-phase")
            self.assertEqual(handoff["originating_harness"], "gemini")

    def test_stale_downstream_plan_and_handoff_do_not_match_amended_roadmap(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = make_repo(root)
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.write_text(
                "# Roadmap\n\n"
                "### Phase 0 - Affordance Verification (AFFVERIFY)\n\n"
                "### Phase 1 - Visual Fidelity (VISUAL)\n"
            )
            plan = write_phase_plan(repo, "VISUAL", roadmap)
            identity = repo_identity(repo)
            handoff = (
                root
                / "home"
                / ".codex"
                / "skills"
                / "codex-execute-phase"
                / "handoffs"
                / identity.repo_hash
                / identity.branch_slug
                / "latest.md"
            )
            handoff.parent.mkdir(parents=True)
            handoff.write_text(
                "---\n"
                "from: codex-execute-phase\n"
                f"repo: {identity.repo_hash}\n"
                f"repo_root: {repo}\n"
                f"branch: {identity.branch}\n"
                f"branch_slug: {identity.branch_slug}\n"
                f"commit: {identity.commit}\n"
                f"artifact: {plan}\n"
                "---\n"
                "automation:\n"
                "  status: complete\n"
            )
            roadmap.write_text(
                "# Roadmap\n\n"
                "### Phase 0 - Affordance Verification (AFFVERIFY)\n\n"
                "### Phase 1 - Mobile Shell (MOBSHELL)\n\n"
                "### Phase 2 - Visual Fidelity (VISUAL)\n"
            )

            self.assertIsNone(find_plan_artifact(repo, "VISUAL", roadmap=roadmap))
            with patch("phase_loop_runtime.discovery.Path.home", return_value=root / "home"):
                latest = latest_skill_handoff(identity, "codex-execute-phase")
            self.assertIsNotNone(latest)
            self.assertFalse(handoff_matches_roadmap(repo, "VISUAL", roadmap, latest))

    def test_parse_plan_ownership_reads_literals_globs_and_none(self):
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
                    "### SL-0 - Parser\n"
                    "- **Owned files**: `vendor/phase-loop-runtime/src/phase_loop_runtime/discovery.py`, `vendor/phase-loop-runtime/tests/test_phase_loop_discovery.py`\n\n"
                    "### SL-1 - Globs\n"
                    "- **Owned files**: `tests/fixtures/*.json`\n\n"
                    "### SL-1b - Directory\n"
                    "- **Owned files**: `src/migrations/versions/`\n\n"
                    "### SL-3 - Literal Route Segment\n"
                    "- **Owned files**: `apps/portal/src/app/pipelines/jobs/[jobId]/__tests__/BootstrapProgress.test.tsx`\n\n"
                    "### SL-2 - Reducer\n"
                    "- **Owned files**: none (read-only lane)\n"
                    "### SL-4 - Parenthesized None\n"
                    "- **Owned files**: (none)\n"
                ),
            )

            ownership = parse_plan_ownership(repo, roadmap, plan)

            self.assertTrue(ownership.valid)
            self.assertIn("vendor/phase-loop-runtime/src/phase_loop_runtime/discovery.py", ownership.owned_patterns)
            self.assertIn("tests/fixtures/*.json", ownership.owned_patterns)
            self.assertTrue(ownership.matches("tests/fixtures/sample.json"))
            self.assertTrue(ownership.matches("src/migrations/versions/20260508000000_add_table.py"))
            self.assertTrue(
                ownership.matches("apps/portal/src/app/pipelines/jobs/[jobId]/__tests__/BootstrapProgress.test.tsx")
            )
            self.assertTrue(ownership.matches("specs/phase-plans-v1.md"))
            self.assertTrue(ownership.matches("plans/phase-plan-v1-RUNNER.md"))

    def test_dirty_output_ownership_expands_to_paired_tests_but_strict_matches_stays_false(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, owned_files=("apps/portal/src/lib/feature.ts",))

            ownership = parse_plan_ownership(repo, roadmap, plan)

            self.assertFalse(ownership.matches("apps/portal/src/lib/__tests__/feature.test.ts"))
            self.assertFalse(ownership.matches("apps/portal/src/lib/__tests__/feature.spec.ts"))
            self.assertTrue(expanded_dirty_ownership_matches(ownership, "apps/portal/src/lib/__tests__/feature.test.ts"))
            self.assertTrue(expanded_dirty_ownership_matches(ownership, "apps/portal/src/lib/__tests__/feature.spec.ts"))
            self.assertTrue(ownership.matches_dirty_output("apps/portal/src/lib/__tests__/feature.test.ts"))
            self.assertFalse(expanded_dirty_ownership_matches(ownership, "apps/portal/src/lib/__tests__/other.test.ts"))
            self.assertFalse(expanded_dirty_ownership_matches(ownership, "apps/portal/src/other/__tests__/feature.test.ts"))

    def test_dirty_output_ownership_expands_to_paired_fixtures(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, owned_files=("apps/portal/src/lib/feature.ts",))

            ownership = parse_plan_ownership(repo, roadmap, plan)

            self.assertFalse(ownership.matches("apps/portal/src/lib/__fixtures__/feature.json"))
            self.assertTrue(expanded_dirty_ownership_matches(ownership, "apps/portal/src/lib/__fixtures__/feature.json"))
            self.assertFalse(expanded_dirty_ownership_matches(ownership, "apps/portal/src/lib/__fixtures__/other.json"))

    def test_dirty_output_ownership_expands_vendor_src_to_module_tests(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(
                repo,
                "RUNNER",
                roadmap,
                owned_files=("vendor/sample-runtime/src/sample_runtime/runner.py",),
            )

            ownership = parse_plan_ownership(repo, roadmap, plan)

            self.assertFalse(ownership.matches("vendor/sample-runtime/tests/test_runner.py"))
            self.assertTrue(expanded_dirty_ownership_matches(ownership, "vendor/sample-runtime/tests/test_runner.py"))
            self.assertFalse(expanded_dirty_ownership_matches(ownership, "vendor/other-runtime/tests/test_runner.py"))
            self.assertFalse(expanded_dirty_ownership_matches(ownership, "vendor/sample-runtime/tests/runner_test.py"))
            self.assertFalse(expanded_dirty_ownership_matches(ownership, "tests/test_runner.py"))

    def test_dirty_output_ownership_expands_vendor_src_directory_to_module_tests(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, owned_files=("vendor/sample-runtime/src/",))

            ownership = parse_plan_ownership(repo, roadmap, plan)

            self.assertTrue(expanded_dirty_ownership_matches(ownership, "vendor/sample-runtime/tests/test_runner.py"))

    def test_parse_plan_ownership_reads_wrapped_owned_files(self):
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
                    "### SL-0 - Wrapped\n"
                    "- **Owned files**: `vendor/phase-loop-runtime/src/phase_loop_runtime/injection.py`,\n"
                    "  `vendor/phase-loop-runtime/src/phase_loop_runtime/runtime_paths.py`,\n"
                    "  `vendor/phase-loop-runtime/tests/test_phase_loop_injection.py`\n"
                    "- **Interfaces provided**: `bundle_contract`\n"
                ),
            )

            ownership = parse_plan_ownership(repo, roadmap, plan)

            self.assertTrue(ownership.valid)
            self.assertIn("vendor/phase-loop-runtime/src/phase_loop_runtime/injection.py", ownership.owned_patterns)
            self.assertIn("vendor/phase-loop-runtime/src/phase_loop_runtime/runtime_paths.py", ownership.owned_patterns)
            self.assertIn("vendor/phase-loop-runtime/tests/test_phase_loop_injection.py", ownership.owned_patterns)

    def test_parse_plan_ownership_reads_alias_numbered_lane_headings(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v2.md"
            roadmap.write_text("# Roadmap\n\n### Phase 0 - Smoke Fix (SMOKEFIX)\n", encoding="utf-8")
            plan = write_phase_plan(
                repo,
                "SMOKEFIX",
                roadmap,
                body=(
                    "# SMOKEFIX\n\n"
                    "## Lanes\n\n"
                    "### SF-0 - Re-verify\n"
                    "- **Owned files**: `scripts/client-smoke/inbox-delivery.ts`\n\n"
                    "### SF-1 - Inventory\n"
                    "- **Owned files**: `specs/smoke-failure-inventory.md`\n"
                ),
            )

            ownership = parse_plan_ownership(repo, roadmap, plan)

            self.assertTrue(ownership.valid)
            self.assertIn("scripts/client-smoke/inbox-delivery.ts", ownership.owned_patterns)
            self.assertIn("specs/smoke-failure-inventory.md", ownership.owned_patterns)

    def test_parse_plan_ownership_fails_closed_for_missing_or_malformed_owned_files(self):
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
                    "### SL-0 - Missing\n"
                    "- **Scope**: missing owned files\n\n"
                    "### SL-1 - Malformed\n"
                    "- **Owned files**: vendor/phase-loop-runtime/tests/test_phase_loop_discovery.py\n"
                ),
            )

            ownership = parse_plan_ownership(repo, roadmap, plan)

            self.assertFalse(ownership.valid)
            self.assertIn("missing_owned_files:### SL-0 - Missing", ownership.errors)
            self.assertIn("malformed_owned_files:### SL-1 - Malformed", ownership.errors)

    def test_parse_dispatch_hints_supports_default_and_action_specific_rules(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.write_text(
                roadmap.read_text()
                + "\n## Dispatch Hints\n"
                + "- preferred executors: `codex`\n"
                + "- allowed executors: `codex`, `claude`\n"
                + "- execute preferred executors: `claude`\n"
                + "- execute fallback executors: `codex`\n"
                + "- execute required capabilities: `dry_run`, `structured_output`\n"
            )

            parsed = parse_dispatch_hints(roadmap, kind="roadmap")
            execute_hints = dispatch_hints_for_action(parsed, "execute")
            review_hints = dispatch_hints_for_action(parsed, "review")

            self.assertEqual(parsed["default"].preferred_executors, ("codex",))
            self.assertEqual(execute_hints.preferred_executors, ("claude",))
            self.assertEqual(execute_hints.fallback_executors, ("codex",))
            self.assertEqual(execute_hints.required_capabilities, ("dry_run", "structured_output"))
            self.assertEqual(review_hints.preferred_executors, ("codex",))
            self.assertEqual(review_hints.allowed_executors, ("codex", "claude"))

    def test_parse_execution_policy_supports_default_action_and_lane_rules(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.write_text(
                roadmap.read_text(encoding="utf-8")
                + "\n## Execution Policy\n"
                + "- work-unit defaults: work-unit=`lane_execute`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`\n"
                + "- execute: executor=`claude`, model=`claude-opus-4-8`, effort=`high`, reason=`prefer Claude for this phase`\n"
                + "- SL-2: executor=`codex`, effort=`xhigh`, work-unit=`phase_reducer`\n",
                encoding="utf-8",
            )

            parsed = parse_execution_policy(roadmap, kind="roadmap")
            execute_rule = execution_policy_for_action(parsed, "execute")
            lane_rule = execution_policy_for_lane(parsed, "execute", "SL-2")
            hints = execution_policy_dispatch_hints(execute_rule)

            self.assertEqual(execute_rule.executor, "claude")
            self.assertEqual(execute_rule.work_unit_kind, "lane_execute")
            self.assertEqual(lane_rule.work_unit_kind, "phase_reducer")
            self.assertEqual(hints.preferred_executors, ("claude",))
            self.assertEqual(execute_rule.unsupported_policy_behavior, "inherit_default")
            self.assertTrue(execute_rule.inherit_default)
            self.assertEqual(lane_rule.executor, "codex")
            self.assertEqual(lane_rule.effort, "xhigh")
            self.assertEqual(lane_rule.work_unit_kind, "phase_reducer")
            self.assertEqual(hints.preferred_executors, ("claude",))
            self.assertEqual(hints.source, "roadmap:execute-policy")

    def test_parse_execution_policy_rejects_reduce_and_verify_selectors(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            for selector in ("reduce", "verify"):
                with self.subTest(selector=selector):
                    roadmap.write_text(
                        "# Roadmap\n\n"
                        "### Phase 1 - Runner (RUNNER)\n\n"
                        "## Execution Policy\n"
                        f"- {selector}: executor=`codex`, effort=`high`, work-unit=`phase_verify`\n",
                        encoding="utf-8",
                    )
                    document = parse_execution_policy(roadmap, kind="roadmap")
                    self.assertEqual(document.rules, ())
                    self.assertIsNotNone(document.parse_error)
                    self.assertIn("invalid execution policy selector", document.parse_error.detail)
                    self.assertEqual(document.parse_error.path, str(roadmap))

    def test_parse_execution_policy_allows_absent_sections_and_fails_closed_for_bad_literals(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            self.assertTrue(parse_execution_policy(roadmap, kind="roadmap").is_empty())

            roadmap.write_text(roadmap.read_text(encoding="utf-8") + "\n## Execution Policy\n- execute: effort=`ultra`\n", encoding="utf-8")
            document = parse_execution_policy(roadmap, kind="roadmap")
            self.assertEqual(document.rules, ())
            self.assertIsNotNone(document.parse_error)
            self.assertEqual(document.parse_error.path, str(roadmap))
            self.assertGreaterEqual(document.parse_error.line_number, 1)
            self.assertIn("execute: effort=`ultra`", document.parse_error.raw_line)

    def test_parse_execution_policy_returns_parse_error_on_malformed_assignment_line(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.write_text(
                "# Roadmap\n\n"
                "### Phase 1 - Runner (RUNNER)\n\n"
                "## Execution Policy\n"
                "- work-unit defaults:\n"
                "  - effort: high\n"
                "  - model: codex\n",
                encoding="utf-8",
            )
            document = parse_execution_policy(roadmap, kind="roadmap")
            self.assertEqual(document.rules, ())
            self.assertIsNotNone(document.parse_error)
            self.assertEqual(document.parse_error.path, str(roadmap))
            self.assertIn("malformed execution policy line", document.parse_error.detail)

    def test_parse_execution_policy_fails_closed_on_partial_malformed_section(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.write_text(
                "# Roadmap\n\n"
                "### Phase 1 - Runner (RUNNER)\n\n"
                "## Execution Policy\n"
                "- execute: executor=`codex`, model=`gpt-5.6-sol`, effort=`high`\n"
                "- repair: effort=`ultra`\n"
                "- review: executor=`claude`, model=`claude-opus-4-8`, effort=`high`\n",
                encoding="utf-8",
            )
            document = parse_execution_policy(roadmap, kind="roadmap")
            self.assertEqual(document.rules, ())
            self.assertIsNotNone(document.parse_error)
            self.assertIn("repair: effort=`ultra`", document.parse_error.raw_line)

    def test_classify_phase_team_eligibility_allows_disjoint_write_lanes(self):
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

            eligibility = classify_phase_team_eligibility(repo, roadmap, plan)

            self.assertTrue(eligibility.eligible_for_native_team)
            self.assertTrue(eligibility.has_disjoint_write_lanes)
            self.assertEqual(eligibility.allowed_execution_modes, ("solo", "subagent", "agent_team"))
            self.assertEqual(eligibility.reason, "disjoint_write_lanes")

    def test_classify_phase_team_eligibility_allows_read_only_lanes(self):
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
                    "### SL-0 - Review\n"
                    "- **Owned files**: none (read-only review lane)\n\n"
                    "### SL-1 - Reducer\n"
                    "- **Owned files**: none (read-only reducer lane)\n"
                ),
            )

            eligibility = classify_phase_team_eligibility(repo, roadmap, plan)

            self.assertTrue(eligibility.eligible_for_native_team)
            self.assertTrue(eligibility.has_only_read_only_lanes)
            self.assertEqual(eligibility.reason, "read_only_lanes_only")

    def test_classify_phase_team_eligibility_fails_closed_for_overlapping_write_lanes(self):
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
                    "- **Owned files**: `src/*.py`\n\n"
                    "### SL-1 - Two\n"
                    "- **Owned files**: `src/app.py`\n"
                ),
            )

            eligibility = classify_phase_team_eligibility(repo, roadmap, plan)

            self.assertFalse(eligibility.eligible_for_native_team)
            self.assertTrue(eligibility.unmanaged_write_risk)
            self.assertEqual(eligibility.allowed_execution_modes, ("solo",))
            self.assertIn("overlap:### SL-0 - One<->### SL-1 - Two", eligibility.invalid_reasons)


if __name__ == "__main__":
    unittest.main()
