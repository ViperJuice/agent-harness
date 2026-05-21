import hashlib
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.discovery import (
    load_execution_phase_source_bundle,
    pipeline_execution_blocker,
    pipeline_execution_plan_diagnostic,
)
from phase_loop_runtime.events import append_event, read_events
from phase_loop_runtime.runner import run_loop
from phase_loop_runtime.state import load_work_unit_state
from phase_loop_test_utils import commit_fixture_paths, make_repo, provenanced_event, write_phase_plan
from test_phase_loop_pipeline_bundle import _write_bundle, _write_protected_source


class PhaseLoopPipelineFreshnessTest(unittest.TestCase):
    def test_fresh_pipeline_execution_metadata_loads_bundle(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            source = _write_protected_source(repo)
            bundle = _write_bundle(repo, protected_sha=hashlib.sha256(source.read_bytes()).hexdigest())
            plan = _write_pipeline_plan(repo, roadmap, bundle)

            self.assertIsNone(pipeline_execution_plan_diagnostic(repo, plan, phase="RUNNER", roadmap=roadmap))
            loaded = load_execution_phase_source_bundle(repo, plan, phase="RUNNER", roadmap=roadmap)

            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.sha256, hashlib.sha256(bundle.read_bytes()).hexdigest())

    def test_stale_source_bundle_sha_blocks_execution(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            source = _write_protected_source(repo)
            bundle = _write_bundle(repo, protected_sha=hashlib.sha256(source.read_bytes()).hexdigest())
            plan = _write_pipeline_plan(repo, roadmap, bundle, bundle_sha="0" * 64)

            diagnostic = pipeline_execution_plan_diagnostic(repo, plan, phase="RUNNER", roadmap=roadmap)

            self.assertIsNotNone(diagnostic)
            assert diagnostic is not None
            self.assertEqual(diagnostic.kind, "mismatched_source_bundle_sha256")
            self.assertEqual(pipeline_execution_blocker(diagnostic)["blocker_class"], "contract_bug")

    def test_sha_shaped_freshness_hash_mismatch_blocks_execution(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            _write_protected_source(repo)
            bundle = _write_bundle(repo, freshness_hash="1" * 64)
            plan = _write_pipeline_plan(repo, roadmap, bundle)

            diagnostic = pipeline_execution_plan_diagnostic(repo, plan, phase="RUNNER", roadmap=roadmap)

            self.assertIsNotNone(diagnostic)
            assert diagnostic is not None
            self.assertEqual(diagnostic.kind, "mismatched_source_bundle_sha256")

    def test_opaque_freshness_hash_is_pipeline_owned_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            _write_protected_source(repo)
            bundle = _write_bundle(repo, freshness_hash="pipeline-owned-freshness-token")
            plan = _write_pipeline_plan(repo, roadmap, bundle)

            self.assertIsNone(pipeline_execution_plan_diagnostic(repo, plan, phase="RUNNER", roadmap=roadmap))

    def test_changed_protected_source_blocks_execution(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            source = _write_protected_source(repo, content="fresh source\n")
            bundle = _write_bundle(repo, protected_sha=hashlib.sha256(source.read_bytes()).hexdigest())
            plan = _write_pipeline_plan(repo, roadmap, bundle)
            source.write_text("changed source\n", encoding="utf-8")

            diagnostic = pipeline_execution_plan_diagnostic(repo, plan, phase="RUNNER", roadmap=roadmap)

            self.assertIsNotNone(diagnostic)
            assert diagnostic is not None
            self.assertEqual(diagnostic.kind, "mismatched_protected_source_sha256")

    def test_missing_source_bundle_file_blocks_execution(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(
                repo,
                "RUNNER",
                roadmap,
                extra_frontmatter={
                    "source_bundle": ".pipeline/artifacts/missing.json",
                    "source_bundle_sha256": "0" * 64,
                    "pipeline_phase_id": "pipeline.phase.runner",
                    "pipeline_mode": "pipeline_required",
                },
            )

            diagnostic = pipeline_execution_plan_diagnostic(repo, plan, phase="RUNNER", roadmap=roadmap)

            self.assertIsNotNone(diagnostic)
            assert diagnostic is not None
            self.assertEqual(diagnostic.kind, "missing_source_bundle_file")

    def test_dfparsoak_pipeline_required_missing_bundle_blocks_before_launch(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(
                repo,
                "DFPARSOAK",
                roadmap,
                extra_frontmatter={
                    "source_bundle": ".pipeline/artifacts/dfparsoak-missing.json",
                    "source_bundle_sha256": "0" * 64,
                    "pipeline_phase_id": "pipeline.phase.dfparsoak",
                    "pipeline_mode": "pipeline_required",
                },
            )

            diagnostic = pipeline_execution_plan_diagnostic(repo, plan, phase="DFPARSOAK", roadmap=roadmap)

            self.assertIsNotNone(diagnostic)
            assert diagnostic is not None
            self.assertEqual(diagnostic.kind, "missing_source_bundle_file")

    def test_unknown_pipeline_phase_id_blocks_execution(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            _write_protected_source(repo)
            bundle = _write_bundle(repo, phase_alias="UNKNOWN")
            plan = _write_pipeline_plan(repo, roadmap, bundle, phase_id="pipeline.phase.unknown")

            diagnostic = pipeline_execution_plan_diagnostic(repo, plan, phase="RUNNER", roadmap=roadmap)

            self.assertIsNotNone(diagnostic)
            assert diagnostic is not None
            self.assertEqual(diagnostic.kind, "unknown_phase_id")

    def test_standalone_plan_compatibility(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)

            self.assertIsNone(pipeline_execution_plan_diagnostic(repo, plan, phase="RUNNER", roadmap=roadmap))

    def test_runner_blocks_stale_pipeline_phase_before_execute_launch(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            _write_protected_source(repo)
            bundle = _write_bundle(repo, freshness_hash="1" * 64)
            plan = _write_pipeline_plan(repo, roadmap, bundle)
            commit_fixture_paths(repo, "pipeline stale plan", plan, bundle)
            append_event(repo, provenanced_event(repo, roadmap, "RUNNER", "planned"))

            snapshot, results = run_loop(
                repo,
                roadmap,
                phase="RUNNER",
                dry_run=True,
                product_action_override="execute",
            )

            self.assertEqual(results, [])
            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            event = read_events(repo)[-1]
            self.assertEqual(event["blocker"]["blocker_class"], "contract_bug")
            self.assertIn("Pipeline execution freshness validation failed", event["blocker"]["blocker_summary"])
            closeout = event["metadata"]["terminal_summary"]["phase_loop_closeout"]
            self.assertEqual(closeout["terminal_status"], "stale_input")
            self.assertEqual(closeout["source_bundle"]["pipeline_mode"], "pipeline_required")
            self.assertEqual(closeout["blocker"]["blocker_class"], "contract_bug")

    def test_runner_blocks_stale_pipeline_work_unit_before_launch(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            _write_protected_source(repo)
            bundle = _write_bundle(repo, freshness_hash="1" * 64)
            plan = _write_pipeline_plan(repo, roadmap, bundle)
            commit_fixture_paths(repo, "pipeline stale plan", plan, bundle)
            append_event(repo, provenanced_event(repo, roadmap, "RUNNER", "planned"))

            snapshot, results = run_loop(
                repo,
                roadmap,
                phase="RUNNER",
                dry_run=True,
                work_unit_mode=True,
                product_action_override="execute",
            )

            self.assertEqual(results, [])
            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            self.assertFalse(load_work_unit_state(repo))

    def test_direct_output_writes_pipeline_required_prelaunch_blocker_closeout(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(
                repo,
                "RUNNER",
                roadmap,
                extra_frontmatter={
                    "source_bundle": ".pipeline/artifacts/missing.json",
                    "source_bundle_sha256": "0" * 64,
                    "pipeline_phase_id": "pipeline.phase.runner",
                    "pipeline_mode": "pipeline_required",
                },
            )
            commit_fixture_paths(repo, "pipeline missing plan", plan)
            append_event(repo, provenanced_event(repo, roadmap, "RUNNER", "planned"))
            output = repo / "closeout.json"

            snapshot, results = run_loop(
                repo,
                roadmap,
                phase="RUNNER",
                dry_run=True,
                product_action_override="execute",
                output_path=output,
            )

            self.assertEqual(results, [])
            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            closeout = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(closeout["schema"], "phase_loop_closeout.v1")
            self.assertEqual(closeout["terminal_status"], "stale_input")
            self.assertEqual(closeout["source_bundle"]["pipeline_mode"], "pipeline_required")
            self.assertEqual(closeout["source_bundle"]["sha256"], "0" * 64)
            self.assertEqual(closeout["pipeline_execution_preflight"]["diagnostic"]["kind"], "missing_source_bundle_file")


def _write_pipeline_plan(
    repo: Path,
    roadmap: Path,
    bundle: Path,
    *,
    bundle_sha: str | None = None,
    phase_id: str = "pipeline.phase.runner",
) -> Path:
    return write_phase_plan(
        repo,
        "RUNNER",
        roadmap,
        extra_frontmatter={
            "source_bundle": str(bundle.resolve().relative_to(repo.resolve())),
            "source_bundle_sha256": bundle_sha or hashlib.sha256(bundle.read_bytes()).hexdigest(),
            "pipeline_phase_id": phase_id,
            "pipeline_mode": "pipeline_required",
        },
        owned_files=("plans/phase-plan-v1-RUNNER.md",),
    )


if __name__ == "__main__":
    unittest.main()
