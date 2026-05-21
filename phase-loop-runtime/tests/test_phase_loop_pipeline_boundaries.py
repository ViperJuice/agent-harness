import hashlib
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.discovery import load_execution_phase_source_bundle, parse_plan_ownership
from phase_loop_runtime.git_ops import pipeline_authorized_write_patterns, pipeline_write_boundary_diagnostic
from phase_loop_test_utils import make_repo, write_phase_plan
from test_phase_loop_pipeline_bundle import _write_bundle, _write_protected_source


class PhaseLoopPipelineBoundariesTest(unittest.TestCase):
    def test_authorized_artifact_write_under_bundle_output_root_passes(self):
        with tempfile.TemporaryDirectory() as td:
            repo, plan, bundle = _pipeline_fixture(Path(td), owned_files=(".pipeline/artifacts/phases/pipeline.phase.runner/output.json",))

            diagnostic = pipeline_write_boundary_diagnostic(
                repo,
                [".pipeline/artifacts/phases/pipeline.phase.runner/output.json"],
                plan_ownership=parse_plan_ownership(repo, repo / "specs" / "phase-plans-v1.md", plan),
                bundle=bundle,
            )

            self.assertIsNone(diagnostic)
            self.assertIn(".pipeline/artifacts/phases/pipeline.phase.runner/**", pipeline_authorized_write_patterns(bundle))

    def test_unauthorized_pipeline_directory_write_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            repo, plan, bundle = _pipeline_fixture(Path(td), owned_files=(".pipeline/state.json",))

            diagnostic = pipeline_write_boundary_diagnostic(
                repo,
                [".pipeline/state.json"],
                plan_ownership=parse_plan_ownership(repo, repo / "specs" / "phase-plans-v1.md", plan),
                bundle=bundle,
            )

            self.assertIsNotNone(diagnostic)
            assert diagnostic is not None
            self.assertEqual(diagnostic.kind, "unauthorized_pipeline_write")
            self.assertFalse(diagnostic.human_required)

    def test_dfparsoak_allows_local_owned_docs_but_not_pipeline_state(self):
        with tempfile.TemporaryDirectory() as td:
            repo, plan, bundle = _pipeline_fixture(
                Path(td),
                owned_files=("docs/phase-loop/dfparsoak-receipt.md", ".pipeline/state.json"),
            )
            ownership = parse_plan_ownership(repo, repo / "specs" / "phase-plans-v1.md", plan)

            self.assertIsNone(
                pipeline_write_boundary_diagnostic(
                    repo,
                    ["docs/phase-loop/dfparsoak-receipt.md"],
                    plan_ownership=ownership,
                    bundle=bundle,
                )
            )
            diagnostic = pipeline_write_boundary_diagnostic(
                repo,
                [".pipeline/state.json"],
                plan_ownership=ownership,
                bundle=bundle,
            )

            self.assertIsNotNone(diagnostic)
            assert diagnostic is not None
            self.assertEqual(diagnostic.kind, "unauthorized_pipeline_write")

    def test_protected_pipeline_definition_write_blocks_without_bundle_authorization(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            definition = repo / "pipeline.definition.json"
            definition.write_text("{}\n", encoding="utf-8")
            bundle = _write_bundle(
                repo,
                protected_entries=[
                    {
                        "path": "pipeline.definition.json",
                        "category": "definition_files",
                        "sha256": hashlib.sha256(definition.read_bytes()).hexdigest(),
                        "role": "protected",
                    }
                ],
            )
            plan = _plan_for_bundle(repo, roadmap, bundle, owned_files=("pipeline.definition.json",))

            diagnostic = pipeline_write_boundary_diagnostic(
                repo,
                ["pipeline.definition.json"],
                plan_ownership=parse_plan_ownership(repo, roadmap, plan),
                bundle=load_execution_phase_source_bundle(repo, plan, phase="RUNNER", roadmap=roadmap),
            )

            self.assertIsNotNone(diagnostic)
            assert diagnostic is not None
            self.assertEqual(diagnostic.kind, "protected_pipeline_source_write")

    def test_protected_source_write_blocks_without_bundle_authorization(self):
        with tempfile.TemporaryDirectory() as td:
            repo, plan, bundle = _pipeline_fixture(Path(td), owned_files=("specs/protected-source.md",))

            diagnostic = pipeline_write_boundary_diagnostic(
                repo,
                ["specs/protected-source.md"],
                plan_ownership=parse_plan_ownership(repo, repo / "specs" / "phase-plans-v1.md", plan),
                bundle=bundle,
            )

            self.assertIsNotNone(diagnostic)
            assert diagnostic is not None
            self.assertEqual(diagnostic.kind, "protected_pipeline_source_write")

    def test_protected_portal_contract_write_blocks_without_bundle_authorization(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            contract = repo / ".pipeline" / "portal" / "contract.json"
            contract.parent.mkdir(parents=True)
            contract.write_text("{}\n", encoding="utf-8")
            bundle = _write_bundle(
                repo,
                protected_entries=[
                    {
                        "path": ".pipeline/portal/contract.json",
                        "category": "portal_contracts",
                        "sha256": hashlib.sha256(contract.read_bytes()).hexdigest(),
                        "role": "protected",
                    }
                ],
            )
            plan = _plan_for_bundle(repo, roadmap, bundle, owned_files=(".pipeline/portal/contract.json",))

            diagnostic = pipeline_write_boundary_diagnostic(
                repo,
                [".pipeline/portal/contract.json"],
                plan_ownership=parse_plan_ownership(repo, roadmap, plan),
                bundle=load_execution_phase_source_bundle(repo, plan, phase="RUNNER", roadmap=roadmap),
            )

            self.assertIsNotNone(diagnostic)
            assert diagnostic is not None
            self.assertEqual(diagnostic.kind, "protected_pipeline_source_write")

    def test_phase_plan_owned_non_pipeline_write_passes(self):
        with tempfile.TemporaryDirectory() as td:
            repo, plan, bundle = _pipeline_fixture(Path(td), owned_files=("README.md",))

            diagnostic = pipeline_write_boundary_diagnostic(
                repo,
                ["README.md"],
                plan_ownership=parse_plan_ownership(repo, repo / "specs" / "phase-plans-v1.md", plan),
                bundle=bundle,
            )

            self.assertIsNone(diagnostic)

    def test_standalone_execution_has_no_pipeline_boundary(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, owned_files=(".pipeline/state.json",))

            diagnostic = pipeline_write_boundary_diagnostic(
                repo,
                [".pipeline/state.json"],
                plan_ownership=parse_plan_ownership(repo, roadmap, plan),
                bundle=None,
            )

            self.assertIsNone(diagnostic)


def _pipeline_fixture(tmp_path: Path, *, owned_files: tuple[str, ...]):
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    source = _write_protected_source(repo)
    bundle = _write_bundle(repo, protected_sha=hashlib.sha256(source.read_bytes()).hexdigest())
    plan = _plan_for_bundle(repo, roadmap, bundle, owned_files=owned_files)
    loaded = load_execution_phase_source_bundle(repo, plan, phase="RUNNER", roadmap=roadmap)
    assert loaded is not None
    return repo, plan, loaded


def _plan_for_bundle(repo: Path, roadmap: Path, bundle: Path, *, owned_files: tuple[str, ...]) -> Path:
    return write_phase_plan(
        repo,
        "RUNNER",
        roadmap,
        extra_frontmatter={
            "source_bundle": str(bundle.resolve().relative_to(repo.resolve())),
            "source_bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
            "pipeline_phase_id": "pipeline.phase.runner",
            "pipeline_mode": "pipeline_required",
        },
        owned_files=owned_files,
    )


if __name__ == "__main__":
    unittest.main()
