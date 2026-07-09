import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.discovery import load_phase_source_bundle, phase_source_bundle_diagnostic
from phase_loop_test_utils import make_repo


class PhaseLoopPipelineBundleTest(unittest.TestCase):
    def test_valid_phase_source_bundle_loads_pipeline_context(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            source = _write_protected_source(repo)
            bundle_path = _write_bundle(repo, protected_sha=hashlib.sha256(source.read_bytes()).hexdigest())

            bundle = load_phase_source_bundle(repo, bundle_path, phase="RUNNER", roadmap=repo / "specs" / "phase-plans-v1.md")

            self.assertIsNotNone(bundle)
            assert bundle is not None
            self.assertEqual(bundle.phase_alias, "RUNNER")
            self.assertEqual(bundle.pipeline_mode, "pipeline_optional")
            self.assertEqual(bundle.path, ".pipeline/artifacts/phase-source-bundle.json")
            self.assertEqual(bundle.protected_sources[0].path, "specs/protected-source.md")
            self.assertEqual(bundle.protected_sources[0].role, "active_canonical_spec")
            self.assertEqual(bundle.plan_metadata().source_bundle_sha256, bundle.sha256)

    def test_governance_contracts_category_and_subtype_load_without_malformed(self):
        # PSCAT-PL: a protected source whose coarse category is the new
        # contract-owned `governance_contracts` bucket (with an optional free-form
        # producer `subtype`) must load cleanly -- malformed_source_bundle must
        # NOT fire for a category that is valid per the distributed contract enum.
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            source = _write_protected_source(repo)
            sha = hashlib.sha256(source.read_bytes()).hexdigest()
            bundle_path = _write_bundle(
                repo,
                protected_entries=[
                    {
                        "path": "specs/protected-source.md",
                        "category": "governance_contracts",
                        "subtype": "skill_manifests",
                        "sha256": sha,
                    }
                ],
            )

            diagnostic = phase_source_bundle_diagnostic(
                repo, bundle_path, phase="RUNNER", roadmap=repo / "specs" / "phase-plans-v1.md"
            )
            self.assertIsNone(diagnostic)

            bundle = load_phase_source_bundle(
                repo, bundle_path, phase="RUNNER", roadmap=repo / "specs" / "phase-plans-v1.md"
            )
            self.assertIsNotNone(bundle)
            assert bundle is not None
            self.assertEqual(bundle.protected_sources[0].category, "governance_contracts")
            self.assertEqual(bundle.protected_sources[0].subtype, "skill_manifests")

    def test_dfparsoak_optional_bundle_loads_pipeline_context(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            source = _write_protected_source(repo)
            bundle_path = _write_bundle(
                repo,
                phase_id="pipeline.phase.dfparsoak",
                phase_alias="DFPARSOAK",
                protected_sha=hashlib.sha256(source.read_bytes()).hexdigest(),
                pipeline_mode="pipeline_optional",
            )

            bundle = load_phase_source_bundle(repo, bundle_path, phase="DFPARSOAK", roadmap=repo / "specs" / "phase-plans-v1.md")

            self.assertIsNotNone(bundle)
            assert bundle is not None
            self.assertEqual(bundle.phase_alias, "DFPARSOAK")
            self.assertEqual(bundle.phase_id, "pipeline.phase.dfparsoak")
            self.assertEqual(bundle.pipeline_mode, "pipeline_optional")

    def test_missing_source_bundle_file_reports_typed_diagnostic(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))

            diagnostic = phase_source_bundle_diagnostic(repo, ".pipeline/artifacts/missing.json", phase="RUNNER")

            self.assertIsNotNone(diagnostic)
            assert diagnostic is not None
            self.assertEqual(diagnostic.kind, "missing_source_bundle_file")
            self.assertFalse(diagnostic.human_required)
            self.assertEqual(diagnostic.blocker_class, "contract_bug")

    def test_malformed_source_bundle_reports_typed_diagnostic(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            bundle_path = repo / ".pipeline" / "artifacts" / "phase-source-bundle.json"
            bundle_path.parent.mkdir(parents=True, exist_ok=True)
            bundle_path.write_text("{not-json\n", encoding="utf-8")

            diagnostic = phase_source_bundle_diagnostic(repo, bundle_path, phase="RUNNER", pipeline_mode="pipeline_required")

            self.assertIsNotNone(diagnostic)
            assert diagnostic is not None
            self.assertEqual(diagnostic.kind, "malformed_source_bundle")
            self.assertFalse(diagnostic.human_required)
            self.assertEqual(diagnostic.blocker_class, "contract_bug")

    def test_standalone_mode_without_bundle_is_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))

            self.assertIsNone(phase_source_bundle_diagnostic(repo, None, phase="RUNNER"))
            self.assertIsNone(load_phase_source_bundle(repo, None, phase="RUNNER"))

    def test_pipeline_required_without_bundle_blocks_as_missing_source_bundle(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))

            diagnostic = phase_source_bundle_diagnostic(repo, None, phase="RUNNER", pipeline_mode="pipeline_required")

            self.assertIsNotNone(diagnostic)
            assert diagnostic is not None
            self.assertEqual(diagnostic.kind, "missing_source_bundle")

    def test_bundle_hash_mismatch_reports_stale_bundle(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            _write_protected_source(repo)
            bundle_path = _write_bundle(repo, freshness_hash="0" * 64)

            diagnostic = phase_source_bundle_diagnostic(repo, bundle_path, phase="RUNNER")

            self.assertIsNotNone(diagnostic)
            assert diagnostic is not None
            self.assertEqual(diagnostic.kind, "mismatched_source_bundle_sha256")
            self.assertEqual(diagnostic.expected_sha256, "0" * 64)

    def test_unknown_phase_id_reports_typed_diagnostic(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            _write_protected_source(repo)
            bundle_path = _write_bundle(repo, phase_alias="UNKNOWN")

            diagnostic = phase_source_bundle_diagnostic(repo, bundle_path, phase="RUNNER")

            self.assertIsNotNone(diagnostic)
            assert diagnostic is not None
            self.assertEqual(diagnostic.kind, "unknown_phase_id")

    def test_missing_protected_source_entries_report_typed_diagnostic(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            bundle_path = _write_bundle(repo, protected_entries=[])

            diagnostic = phase_source_bundle_diagnostic(repo, bundle_path, phase="RUNNER")

            self.assertIsNotNone(diagnostic)
            assert diagnostic is not None
            self.assertEqual(diagnostic.kind, "missing_protected_source_entries")

    def test_stale_protected_source_hash_reports_typed_diagnostic(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            _write_protected_source(repo, content="fresh source\n")
            bundle_path = _write_bundle(repo, protected_sha="1" * 64)

            diagnostic = phase_source_bundle_diagnostic(repo, bundle_path, phase="RUNNER")

            self.assertIsNotNone(diagnostic)
            assert diagnostic is not None
            self.assertEqual(diagnostic.kind, "mismatched_protected_source_sha256")
            self.assertEqual(diagnostic.expected_sha256, "1" * 64)

    def test_missing_protected_source_file_reports_typed_diagnostic(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            bundle_path = _write_bundle(
                repo,
                protected_entries=[
                    {
                        "path": "specs/missing-protected-source.md",
                        "category": "specs",
                        "sha256": "1" * 64,
                        "role": "protected",
                    }
                ],
            )

            diagnostic = phase_source_bundle_diagnostic(repo, bundle_path, phase="RUNNER")

            self.assertIsNotNone(diagnostic)
            assert diagnostic is not None
            self.assertEqual(diagnostic.kind, "missing_protected_source_file")
            self.assertFalse(diagnostic.human_required)

    def test_adoption_sensitive_roles_are_validated_and_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            source = _write_protected_source(repo)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            roles = (
                "seed_spec",
                "predecessor_spec",
                "active_canonical_spec",
                "archived_spec",
                "managed_mirror_file",
                "unmanaged_spec_input",
                "legacy_specs_bundle",
                "root_specs_intake",
                "pipeline_specs_canonical",
                "adapter_configured_intake_root",
                "mirror_manifest",
                "archive_manifest",
            )
            bundle_path = _write_bundle(
                repo,
                protected_entries=[
                    {
                        "path": "specs/protected-source.md",
                        "category": "specs",
                        "sha256": digest,
                        "role": role,
                    }
                    for role in roles
                ],
                require_roles=True,
            )

            bundle = load_phase_source_bundle(repo, bundle_path, phase="RUNNER", roadmap=repo / "specs" / "phase-plans-v1.md")

            self.assertIsNotNone(bundle)
            assert bundle is not None
            self.assertEqual(tuple(source.role for source in bundle.protected_sources), roles)

    def test_adoption_required_bundle_without_role_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            source = _write_protected_source(repo)
            bundle_path = _write_bundle(
                repo,
                protected_entries=[
                    {
                        "path": "specs/protected-source.md",
                        "category": "specs",
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    }
                ],
                require_roles=True,
                pipeline_mode="pipeline_required",
            )

            diagnostic = phase_source_bundle_diagnostic(repo, bundle_path, phase="RUNNER", pipeline_mode="pipeline_required")

            self.assertIsNotNone(diagnostic)
            assert diagnostic is not None
            self.assertEqual(diagnostic.kind, "malformed_source_bundle")
            self.assertEqual(diagnostic.blocker_class, "contract_bug")
            self.assertFalse(diagnostic.human_required)

    def test_unknown_adoption_role_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            source = _write_protected_source(repo)
            bundle_path = _write_bundle(
                repo,
                protected_entries=[
                    {
                        "path": "specs/protected-source.md",
                        "category": "specs",
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                        "role": "mystery_spec",
                    }
                ],
                require_roles=True,
                pipeline_mode="pipeline_required",
            )

            diagnostic = phase_source_bundle_diagnostic(repo, bundle_path, phase="RUNNER", pipeline_mode="pipeline_required")

            self.assertIsNotNone(diagnostic)
            assert diagnostic is not None
            self.assertEqual(diagnostic.kind, "malformed_source_bundle")
            self.assertIn("protected source role", diagnostic.message)


def _write_protected_source(repo: Path, content: str = "protected source\n") -> Path:
    path = repo / "specs" / "protected-source.md"
    path.write_text(content, encoding="utf-8")
    return path


def _write_bundle(
    repo: Path,
    *,
    phase_id: str = "pipeline.phase.runner",
    phase_alias: str = "RUNNER",
    protected_entries: list[dict[str, object]] | None = None,
    protected_sha: str = "protected-source-sha256",
    freshness_hash: str = "pipeline-owned-freshness-token",
    pipeline_mode: str = "pipeline_optional",
    require_roles: bool = False,
) -> Path:
    path = repo / ".pipeline" / "artifacts" / "phase-source-bundle.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    entries = protected_entries
    if entries is None:
        entries = [
            {
                "path": "specs/protected-source.md",
                "category": "specs",
                "sha256": protected_sha,
                "role": "active_canonical_spec",
            }
        ]
    data = {
        "schema": "phase-source-bundle.v1",
        "phase": {
            "phase_id": phase_id,
            "phase_alias": phase_alias,
            "phase_plan_path": "plans/phase-plan-v1-RUNNER.md",
        },
        "roadmap": {
            "path": "specs/phase-plans-v1.md",
            "sha256": hashlib.sha256(roadmap.read_bytes()).hexdigest(),
        },
        "protected_sources": {
            "hash_algorithm": "sha256",
            "entries": entries,
            "requires_roles": require_roles,
        },
        "delegated_write_policy": {
            "owned_files": ["plans/phase-plan-v1-RUNNER.md"],
            "read_only_files": ["specs/protected-source.md"],
            "protected_write_mode": "explicit_phase_ownership_required",
            "typed_delegation_required": True,
        },
        "source_files": [
            {
                "path": "specs/phase-plans-v1.md",
                "sha256": hashlib.sha256(roadmap.read_bytes()).hexdigest(),
                "purpose": "roadmap",
                "protected": True,
                "read_only": True,
            }
        ],
        "artifact_target_root": ".pipeline/artifacts/phases/pipeline.phase.runner",
        "freshness": {
            "generated_at": "2026-05-11T00:00:00Z",
            "source_bundle_hash": freshness_hash,
            "status": "fresh",
        },
        "pipeline_mode": pipeline_mode,
    }
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return path


if __name__ == "__main__":
    unittest.main()
