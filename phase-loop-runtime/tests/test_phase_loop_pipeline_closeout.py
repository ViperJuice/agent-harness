import unittest
import os
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.closeout import build_phase_loop_closeout, phase_loop_closeout_diagnostic
from phase_loop_runtime.models import (
    CHANGED_PATH_CATEGORIES,
    PhaseSourceBundle,
    PipelinePlanMetadata,
    PipelineProtectedSource,
    WorkUnitCloseout,
    WorkUnitIdentity,
)
from phase_loop_runtime.redaction import build_source_truth_impact, classify_changed_path, metadata_redaction_diagnostic
from phase_loop_test_utils import make_repo, write_phase_plan

DEPRECATED_V5_ROOT_FIELDS = {
    "status",
    "next_skill",
    "next_command",
    "verification_status",
    "artifact",
    "artifact_state",
}

class TestPhaseLoopPipelineCloseout(unittest.TestCase):
    FIXTURE_DIR = "vendor/phase-loop-runtime/tests/fixtures/phase_loop_pipeline_bridge"
    VALID_FIXTURES = [
        "complete.json",
        "blocked.json",
        "stale_input.json",
        "failed_verification.json",
        "human_required.json",
        "dfparsoak_complete.json",
        "dfparsoak_blocked.json",
        "dfparsoak_failed_verification.json",
        "dfparsoak_stale_input.json",
        "dfparsoak_human_required.json",
        "dfbundlecloseout_complete.json",
        "dfbundlecloseout_blocked.json",
        "dfbundlecloseout_failed_verification.json",
        "dfbundlecloseout_human_required.json",
        "dfbundlecloseout_stale_bundle.json",
        "dfbundlecloseout_standalone.json",
        "dfdriftsignal_complete.json",
        "dfdriftsignal_blocked.json",
        "dfdriftsignal_standalone_advisory.json",
        "dfdriftsignal_pipeline_required_advisory.json",
        "dfdriftsignal_canonical_refresh_recommended.json",
        "dfadopthints_standalone_unmanaged_spec.json",
        "dfadopthints_pipeline_required_adoption_roles.json",
        "dfadopthints_canonical_refresh_recommended.json",
        "dfadoptbridge_adoption_complete.json",
        "dfadoptbridge_blocked_adoption_metadata.json",
        "dfadoptbridge_stale_source_bundle.json",
        "dfadoptbridge_stale_mirror_manifest.json",
        "dfadoptbridge_unmanaged_spec_input.json",
        "dfadoptbridge_archive_manifest_touched.json",
        "dfadoptbridge_standalone_non_adoption.json",
    ]

    def _load_fixture(self, name):
        path = os.path.join(self.FIXTURE_DIR, name)
        with open(path, 'r') as f:
            return json.load(f)

    def _valid_fixture_names(self):
        return self.VALID_FIXTURES

    def test_changed_path_classification_boundaries_are_deterministic(self):
        cases = {
            "vendor/phase-loop-runtime/src/phase_loop_runtime/runner.py": "code",
            "vendor/phase-loop-runtime/tests/test_phase_loop_runner.py": "tests",
            "vendor/phase-loop-runtime/tests/fixtures/phase_loop_pipeline_bridge/dfdriftsignal_complete.json": "tests",
            "docs/phase-loop/runbook.md": "docs",
            "README.md": "docs",
            "specs/phase-plans-v14.md": "unmanaged_spec",
            ".pipeline/specs/active/phase-plans-v15.md": "active_canonical_spec",
            ".pipeline/specs/archive/phase-plans-v14.md": "archived_spec",
            ".pipeline/specs/mirror-manifest.json": "mirror_manifest",
            ".pipeline/specs/archive-manifest.json": "archive_manifest",
            ".pipeline/sources/phase-source-bundle.json": "pipeline_sources",
            "packages/pipeline-schema/src/pipeline.definition.json": "pipeline_sources",
            "portal/contracts/phase-loop-closeout.json": "portal_contract_refs",
            "greenfield/authority/reduction-contract.md": "greenfield_authority_refs",
            "scratch/output.txt": "unknown",
        }
        for path, expected in cases.items():
            self.assertEqual(classify_changed_path(path), expected)

    def test_source_truth_impact_recommends_canonical_refresh_for_sensitive_paths(self):
        impact = build_source_truth_impact(
            [
                "vendor/phase-loop-runtime/src/phase_loop_runtime/runner.py",
                "vendor/phase-loop-runtime/tests/test_phase_loop_runner.py",
                "docs/phase-loop/contract-map.md",
                "specs/phase-plans-v14.md",
                "docs/phase-loop/adoption-contract.md",
                ".pipeline/specs/active/phase-plans-v15.md",
                ".pipeline/specs/mirror-manifest.json",
                ".pipeline/specs/archive-manifest.json",
                ".pipeline/sources/phase-source-bundle.json",
                "portal/contracts/phase-loop-closeout.json",
                "greenfield/authority/reduction-contract.md",
            ]
        ).to_json()

        self.assertTrue(impact["canonical_refresh_recommended"])
        self.assertIn("docs_source_truth_touched", impact["canonical_refresh_reason_codes"])
        self.assertIn("unmanaged_specs_touched", impact["canonical_refresh_reason_codes"])
        self.assertIn("adoption_contracts_touched", impact["canonical_refresh_reason_codes"])
        self.assertIn("active_specs_touched", impact["canonical_refresh_reason_codes"])
        self.assertIn("mirror_manifests_touched", impact["canonical_refresh_reason_codes"])
        self.assertIn("archive_manifests_touched", impact["canonical_refresh_reason_codes"])
        self.assertIn("contract_refs_touched", impact["canonical_refresh_reason_codes"])
        self.assertIn("pipeline_sources_touched", impact["canonical_refresh_reason_codes"])
        self.assertIn("portal_contract_refs_touched", impact["canonical_refresh_reason_codes"])
        self.assertIn("greenfield_authority_refs_touched", impact["canonical_refresh_reason_codes"])
        self.assertEqual(impact["redaction_posture"], "metadata_only")
        self.assertTrue({item["category"] for item in impact["changed_path_boundaries"]}.issubset(set(CHANGED_PATH_CATEGORIES)))

    def test_validated_protected_source_roles_refine_root_spec_classification(self):
        impact = build_source_truth_impact(
            [
                "specs/managed.md",
                "specs/archive.md",
                "specs/unmanaged.md",
            ],
            {
                "specs/managed.md": "managed_mirror_file",
                "specs/archive.md": "archived_spec",
            },
        ).to_json()

        categories = {item["path"]: item["category"] for item in impact["changed_path_boundaries"]}
        self.assertEqual(categories["specs/managed.md"], "managed_root_mirror_spec")
        self.assertEqual(categories["specs/archive.md"], "archived_spec")
        self.assertEqual(categories["specs/unmanaged.md"], "unmanaged_spec")
        self.assertTrue(impact["canonical_refresh_recommended"])
        self.assertIn("managed_mirror_specs_touched", impact["canonical_refresh_reason_codes"])
        self.assertIn("archived_specs_touched", impact["canonical_refresh_reason_codes"])
        self.assertIn("unmanaged_specs_touched", impact["canonical_refresh_reason_codes"])

    def test_source_truth_impact_does_not_recommend_refresh_for_code_or_tests_only(self):
        impact = build_source_truth_impact(
            ["vendor/phase-loop-runtime/src/phase_loop_runtime/runner.py", "vendor/phase-loop-runtime/tests/test_phase_loop_runner.py"]
        ).to_json()

        self.assertFalse(impact["canonical_refresh_recommended"])
        self.assertEqual(impact["canonical_refresh_reason_codes"], [])

    def test_metadata_redaction_rejects_forbidden_closeout_content(self):
        cases = (
            {"evidence_refs": [{"summary": "diff --git a/file b/file"}]},
            {"evidence_refs": [{"summary": "raw spec body from source"}]},
            {"evidence_refs": [{"summary": "raw transcript from provider"}]},
            {"evidence_refs": [{"token": "api_key=sk_test_secret_value"}]},
            {"evidence_refs": [{"path": "/home/alice/private/evidence.txt"}]},
            {"evidence_refs": [{"summary": "raw provider payload"}]},
            {"evidence_refs": [{"summary": "credential payload"}]},
            {"evidence_refs": [{"summary": "local env value"}]},
            {"evidence_refs": [{"summary": "private evidence bytes"}]},
        )
        for payload in cases:
            diagnostic = metadata_redaction_diagnostic(payload)
            self.assertIsNotNone(diagnostic)
            assert diagnostic is not None
            self.assertEqual(diagnostic["kind"], "malformed_closeout")

    def test_schema_version(self):
        """Verify that all fixtures declare the correct schema version."""
        for fixture_name in self.VALID_FIXTURES:
            data = self._load_fixture(fixture_name)
            self.assertEqual(data.get("schema"), "phase_loop_closeout.v1", f"Fixture {fixture_name} has wrong schema")

    def test_upstream_receipt_fixture_is_not_native_closeout_json(self):
        upstreams = self._load_fixture("dfparsoak_upstream_receipts.json")

        self.assertIsInstance(upstreams, list)
        for upstream in upstreams:
            self.assertIn("phase_alias", upstream)
            self.assertIn("path", upstream)
            self.assertIn("sha256", upstream)

    def test_required_top_level_fields(self):
        """Verify that required top-level fields are present."""
        required_fields = ["schema", "phase", "terminal_status", "automation", "artifacts", "verification"]
        for fixture_name in self._valid_fixture_names():
            data = self._load_fixture(fixture_name)
            for field in required_fields:
                self.assertIn(field, data, f"Fixture {fixture_name} missing required field: {field}")

    def test_automation_object_schema(self):
        """Verify that the automation object follows the v1 schema."""
        required_automation_fields = [
            "status", "next_skill", "next_command", "next_model_hint",
            "next_effort_hint", "human_required", "blocker_class",
            "blocker_summary", "required_human_inputs", "verification_status",
            "artifact", "artifact_state"
        ]
        for fixture_name in self._valid_fixture_names():
            data = self._load_fixture(fixture_name)
            automation = data.get("automation", {})
            for field in required_automation_fields:
                self.assertIn(field, automation, f"Fixture {fixture_name} automation missing field: {field}")

    def test_source_bundle_reference(self):
        """Verify that fixtures include source bundle metadata when appropriate."""
        for fixture_name in self._valid_fixture_names():
            data = self._load_fixture(fixture_name)
            self.assertIn("source_bundle", data, f"Fixture {fixture_name} missing source_bundle")
            sb = data["source_bundle"]
            self.assertIn("pipeline_mode", sb)
            if sb["pipeline_mode"] == "pipeline_required":
                self.assertIn("path", sb)
                self.assertIn("sha256", sb)
                self.assertIn("phase_id", sb)

    def test_standalone_closeout_omits_pipeline_only_identity_without_failing(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "DFBUNDLECLOSEOUT", roadmap)
            closeout = build_phase_loop_closeout(
                phase_alias="DFBUNDLECLOSEOUT",
                plan_path=plan,
                terminal_summary={
                    "terminal_status": "complete",
                    "verification_status": "passed",
                    "artifact_paths": {"terminal": ".phase-loop/runs/local/terminal-summary.json"},
                    "evidence_refs": ({"path": "vendor/phase-loop-runtime/tests/fixtures/phase_loop_pipeline_bridge/dfbundlecloseout_standalone.json", "sha256": "a" * 64},),
                    "verification_commands": ({"command": "python3 -m unittest test_phase_loop_pipeline_closeout", "status": "passed"},),
                },
                automation={"status": "complete", "verification_status": "passed", "human_required": False},
                changed_paths=("vendor/phase-loop-runtime/src/phase_loop_runtime/closeout.py",),
            )

            self.assertIsNone(phase_loop_closeout_diagnostic(closeout))
            self.assertTrue({"automation", "artifacts", "verification", "blocker", "source_bundle", "source_truth_impact"}.issubset(closeout))
            self.assertTrue(DEPRECATED_V5_ROOT_FIELDS.isdisjoint(closeout))
            self.assertEqual(closeout["source_bundle"], {"pipeline_mode": "standalone"})
            self.assertNotIn("source_bundle_sha256", closeout)
            self.assertEqual(closeout["artifacts"]["changed_paths"], ["vendor/phase-loop-runtime/src/phase_loop_runtime/closeout.py"])
            self.assertEqual(closeout["source_truth_impact"]["changed_path_boundaries"][0]["category"], "code")
            self.assertFalse(closeout["source_truth_impact"]["canonical_refresh_recommended"])
            self.assertEqual(closeout["artifacts"]["evidence_refs"][0]["path"], "vendor/phase-loop-runtime/tests/fixtures/phase_loop_pipeline_bridge/dfbundlecloseout_standalone.json")
            self.assertEqual(closeout["verification"]["commands"], ["python3 -m unittest test_phase_loop_pipeline_closeout"])

    def test_pipeline_required_closeout_requires_bundle_identity(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "DFBUNDLECLOSEOUT", roadmap)
            closeout = build_phase_loop_closeout(
                phase_alias="DFBUNDLECLOSEOUT",
                plan_path=plan,
                plan_metadata=PipelinePlanMetadata(
                    source_bundle=".pipeline/artifacts/phase-source-bundle.json",
                    source_bundle_sha256=None,
                    pipeline_phase_id="pipeline.phase.dfbundlecloseout",
                    pipeline_mode="pipeline_required",
                ),
                terminal_summary={"terminal_status": "blocked", "verification_status": "blocked"},
                automation={"status": "blocked", "verification_status": "blocked", "human_required": False, "blocker_class": "contract_bug"},
            )

            diagnostic = phase_loop_closeout_diagnostic(closeout)
            self.assertIsNotNone(diagnostic)
            assert diagnostic is not None
            self.assertEqual(diagnostic["kind"], "missing_source_bundle_sha256")

    def test_closeout_echoes_adoption_role_metadata_only(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "DFADOPTMODE", roadmap)
            source_bundle = PhaseSourceBundle(
                path=".pipeline/artifacts/phase-source-bundle.json",
                sha256="a" * 64,
                phase_id="pipeline.phase.dfadoptmode",
                phase_alias="DFADOPTMODE",
                phase_plan_path="plans/phase-plan-v1-DFADOPTMODE.md",
                roadmap_path="specs/phase-plans-v1.md",
                roadmap_sha256="b" * 64,
                protected_sources=(
                    PipelineProtectedSource(
                        path=".pipeline/specs/active.md",
                        category="specs",
                        sha256="c" * 64,
                        role="active_canonical_spec",
                    ),
                    PipelineProtectedSource(
                        path="specs/active.md",
                        category="specs",
                        sha256="d" * 64,
                        role="managed_mirror_file",
                    ),
                    PipelineProtectedSource(
                        path=".pipeline/specs/archive-manifest.json",
                        category="definition_files",
                        sha256="e" * 64,
                        role="archive_manifest",
                    ),
                ),
                artifact_target_root=".pipeline/artifacts/phases/pipeline.phase.dfadoptmode",
                freshness={"status": "fresh"},
                pipeline_mode="pipeline_required",
            )

            closeout = build_phase_loop_closeout(
                phase_alias="DFADOPTMODE",
                plan_path=plan,
                source_bundle=source_bundle,
                terminal_summary={"terminal_status": "complete", "verification_status": "passed"},
                automation={"status": "complete", "verification_status": "passed", "human_required": False},
            )

            self.assertIsNone(phase_loop_closeout_diagnostic(closeout))
            self.assertEqual(
                [item["role"] for item in closeout["source_bundle"]["protected_sources"]],
                ["active_canonical_spec", "managed_mirror_file", "archive_manifest"],
            )
            self.assertNotIn("raw_spec_bodies", json.dumps(closeout))
            self.assertNotIn("raw_diffs", json.dumps(closeout))

            malformed = json.loads(json.dumps(closeout))
            malformed["source_bundle"]["protected_sources"][0]["role"] = "unknown_role"
            diagnostic = phase_loop_closeout_diagnostic(malformed)
            self.assertIsNotNone(diagnostic)
            assert diagnostic is not None
            self.assertEqual(diagnostic["kind"], "malformed_closeout")

    def test_closeout_uses_source_bundle_roles_for_adoption_sensitive_paths(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "DFADOPTHINTS", roadmap)
            source_bundle = PhaseSourceBundle(
                path=".pipeline/artifacts/phase-source-bundle.json",
                sha256="a" * 64,
                phase_id="pipeline.phase.dfadopthints",
                phase_alias="DFADOPTHINTS",
                phase_plan_path="plans/phase-plan-v1-DFADOPTHINTS.md",
                roadmap_path="specs/phase-plans-v1.md",
                roadmap_sha256="b" * 64,
                protected_sources=(
                    PipelineProtectedSource(
                        path="specs/managed.md",
                        category="specs",
                        sha256="c" * 64,
                        role="managed_mirror_file",
                    ),
                    PipelineProtectedSource(
                        path="specs/archive.md",
                        category="specs",
                        sha256="d" * 64,
                        role="archived_spec",
                    ),
                ),
                pipeline_mode="pipeline_required",
            )

            closeout = build_phase_loop_closeout(
                phase_alias="DFADOPTHINTS",
                plan_path=plan,
                source_bundle=source_bundle,
                terminal_summary={"terminal_status": "complete", "verification_status": "passed"},
                automation={"status": "complete", "verification_status": "passed", "human_required": False},
                changed_paths=("specs/managed.md", "specs/archive.md", "specs/new.md"),
            )

            self.assertIsNone(phase_loop_closeout_diagnostic(closeout))
            categories = {
                item["path"]: item["category"]
                for item in closeout["source_truth_impact"]["changed_path_boundaries"]
            }
            self.assertEqual(categories["specs/managed.md"], "managed_root_mirror_spec")
            self.assertEqual(categories["specs/archive.md"], "archived_spec")
            self.assertEqual(categories["specs/new.md"], "unmanaged_spec")

    def test_closeout_uses_explicit_intake_roles_for_non_default_spec_roots(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "DFADOPTSOAK", roadmap)
            source_bundle = PhaseSourceBundle(
                path=".pipeline/artifacts/phase-source-bundle.json",
                sha256="a" * 64,
                phase_id="pipeline.phase.dfadoptsoak",
                phase_alias="DFADOPTSOAK",
                phase_plan_path="plans/phase-plan-v1-DFADOPTSOAK.md",
                roadmap_path="specs/phase-plans-v1.md",
                roadmap_sha256="b" * 64,
                protected_sources=(
                    PipelineProtectedSource(
                        path="Specs/legacy-intake.md",
                        category="specs",
                        sha256="c" * 64,
                        role="adapter_configured_intake_root",
                    ),
                    PipelineProtectedSource(
                        path="legacy-specs/bundle.md",
                        category="specs",
                        sha256="d" * 64,
                        role="legacy_specs_bundle",
                    ),
                ),
                pipeline_mode="pipeline_required",
            )

            closeout = build_phase_loop_closeout(
                phase_alias="DFADOPTSOAK",
                plan_path=plan,
                source_bundle=source_bundle,
                terminal_summary={"terminal_status": "complete", "verification_status": "passed"},
                automation={"status": "complete", "verification_status": "passed", "human_required": False},
                changed_paths=("Specs/legacy-intake.md", "legacy-specs/bundle.md"),
            )

            self.assertIsNone(phase_loop_closeout_diagnostic(closeout))
            categories = {
                item["path"]: item["category"]
                for item in closeout["source_truth_impact"]["changed_path_boundaries"]
            }
            self.assertEqual(categories["Specs/legacy-intake.md"], "unmanaged_spec")
            self.assertEqual(categories["legacy-specs/bundle.md"], "unmanaged_spec")
            self.assertIn("unmanaged_specs_touched", closeout["source_truth_impact"]["canonical_refresh_reason_codes"])
            self.assertEqual(
                [item["role"] for item in closeout["source_bundle"]["protected_sources"]],
                ["adapter_configured_intake_root", "legacy_specs_bundle"],
            )

    def test_native_v1_closeout_rejects_deprecated_root_aliases(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "DFBUNDLECLOSEOUT", roadmap)
            closeout = build_phase_loop_closeout(
                phase_alias="DFBUNDLECLOSEOUT",
                plan_path=plan,
                terminal_summary={"terminal_status": "complete", "verification_status": "passed"},
                automation={"status": "complete", "verification_status": "passed", "human_required": False},
            )

            for field in DEPRECATED_V5_ROOT_FIELDS:
                payload = dict(closeout)
                payload[field] = "deprecated"
                diagnostic = phase_loop_closeout_diagnostic(payload)
                self.assertIsNotNone(diagnostic, field)
                assert diagnostic is not None
                self.assertEqual(diagnostic["kind"], "malformed_closeout")
                self.assertIn(field, diagnostic["message"])

    def test_malformed_closeout_rejects_raw_private_evidence_tokens(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "DFBUNDLECLOSEOUT", roadmap)
            closeout = build_phase_loop_closeout(
                phase_alias="DFBUNDLECLOSEOUT",
                plan_path=plan,
                terminal_summary={
                    "terminal_status": "complete",
                    "verification_status": "passed",
                    "evidence_refs": ({"path": "raw transcript should not appear", "sha256": "b" * 64},),
                },
                automation={"status": "complete", "verification_status": "passed"},
            )

            diagnostic = phase_loop_closeout_diagnostic(closeout)
            self.assertIsNotNone(diagnostic)
            assert diagnostic is not None
            self.assertEqual(diagnostic["kind"], "malformed_closeout")

    def test_dfparsoak_closeout_builder_threads_lane_route_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "DFPARSOAK", roadmap)
            closeout = build_phase_loop_closeout(
                phase_alias="DFPARSOAK",
                plan_path=plan,
                terminal_summary={"terminal_status": "complete", "verification_status": "passed"},
                automation={"status": "complete", "verification_status": "passed", "human_required": False},
                work_unit_closeout=WorkUnitCloseout(
                    identity=WorkUnitIdentity(phase="DFPARSOAK", kind="lane_execute", lane_id="SL-2", attempt=1),
                    status="complete",
                    terminal_summary={
                        "worktree_isolation_mode": "git_worktree",
                        "base_sha": "b" * 40,
                        "harness_route": "codex",
                        "model": "gpt-5.5",
                        "effort": "high",
                        "policy_source": "phase-plan",
                        "fallback_reason": "codex_cli_fallback",
                    },
                    wave_id="wave-dfparsoak-001",
                    worktree_path="<WORKTREE-PATH-REDACTED>",
                    changed_paths=("docs/phase-loop/dfparsoak-receipt.md",),
                    verification_status="passed",
                    evidence_refs=({"path": ".phase-loop/runs/dfparsoak/terminal-summary.json", "sha256": "a" * 64},),
                ),
            )

            self.assertEqual(closeout["lane"]["lane_id"], "SL-2")
            self.assertEqual(closeout["lane"]["wave_id"], "wave-dfparsoak-001")
            self.assertEqual(closeout["lane"]["worktree_isolation_mode"], "git_worktree")
            self.assertEqual(closeout["lane"]["base_sha"], "b" * 40)
            self.assertEqual(closeout["lane"]["harness_route"], "codex")
            self.assertEqual(closeout["lane"]["work_unit_kind"], "lane_execute")
            self.assertEqual(closeout["lane"]["model"], "gpt-5.5")
            self.assertEqual(closeout["lane"]["effort"], "high")
            self.assertEqual(closeout["lane"]["policy_source"], "phase-plan")
            self.assertEqual(closeout["lane"]["fallback_reason"], "codex_cli_fallback")

if __name__ == "__main__":
    unittest.main()
