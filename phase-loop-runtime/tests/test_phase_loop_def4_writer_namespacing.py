import json
import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.provenance import phase_sha256, roadmap_sha256
from phase_loop_runtime.reconcile import _normalize_legacy_executor_closeout_action, reconcile
from phase_loop_test_utils import make_repo, write_phase_plan


DATA = Path(__file__).resolve().parent / "data"


class PhaseLoopDef4WriterNamespacingTest(unittest.TestCase):
    def test_legacy_executor_closeout_action_normalizes_without_mutating_event(self):
        event = {
            "phase": "RW",
            "action": "run",
            "metadata": {"executor_closeout_event": {"source_status": "complete"}},
        }

        normalized, changed = _normalize_legacy_executor_closeout_action(event)

        self.assertTrue(changed)
        self.assertEqual(normalized["action"], "executor.closeout")
        self.assertEqual(event["action"], "run")

    def test_legacy_executor_closeout_reconciles_with_single_warning(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = self._fixture(Path(td))
            event = self._raw_event(repo, roadmap, action="run", status="complete")
            event["metadata"] = {"executor_closeout_event": {"source_status": "complete"}}
            self._append_raw_event(repo, event)
            second = {**event, "timestamp": "2026-05-29T01:00:01Z"}
            self._append_raw_event(repo, second)

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.phases["RW"], "complete")
            warnings = [
                warning
                for warning in snapshot.ledger_warnings
                if warning["reason"] == "legacy_executor_closeout_action_normalized"
            ]
            self.assertEqual(len(warnings), 1)
            self.assertEqual(warnings[0]["action"], "run")

    def test_legacy_byte_identical_executor_closeout_duplicates_dedup_after_normalization(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = self._fixture(Path(td))
            event = self._raw_event(repo, roadmap, action="run", status="blocked")
            event["metadata"] = {
                "executor_closeout_event": {
                    "source_status": "blocked",
                    "verification_status": "blocked",
                    "produced_if_gates": ["IF-0-RW-1"],
                    "dirty_paths": ["phase-owned.log"],
                }
            }

            self._append_raw_event(repo, event)
            self._append_raw_event(repo, dict(event))

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.phases["RW"], "blocked")
            self.assertEqual(len(snapshot.ledger_duplicates_skipped), 1)
            self.assertEqual(snapshot.ledger_duplicates_skipped[0]["action"], "executor.closeout")

    def test_pre_writer_namespacing_fixture_reconciles_like_namespaced_fixture(self):
        legacy = self._snapshot_from_fixture("legacy_def4_pre_writer_namespacing.jsonl")
        namespaced = self._snapshot_from_fixture("def4_executor_closeout_namespaced.jsonl")

        self.assertEqual(legacy.phases["RW"], namespaced.phases["RW"])
        self.assertEqual(legacy.dirty_paths, namespaced.dirty_paths)
        self.assertEqual(legacy.phase_owned_dirty_paths, namespaced.phase_owned_dirty_paths)
        self.assertEqual(legacy.unowned_dirty_paths, namespaced.unowned_dirty_paths)
        self.assertEqual(legacy.terminal_summary, namespaced.terminal_summary)

    def _snapshot_from_fixture(self, name: str):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = self._fixture(Path(td))
            for raw in DATA.joinpath(name).read_text(encoding="utf-8").splitlines():
                event = json.loads(raw)
                event.update(
                    {
                        "repo": str(repo),
                        "roadmap": str(roadmap),
                        "roadmap_sha256": roadmap_sha256(roadmap),
                        "phase_sha256": phase_sha256(roadmap, str(event["phase"]).upper()),
                    }
                )
                self._append_raw_event(repo, event)
            return reconcile(repo, roadmap)

    def _fixture(self, tmp_path: Path) -> tuple[Path, Path]:
        repo = make_repo(tmp_path)
        roadmap = repo / "specs" / "phase-plans-v35.md"
        roadmap.write_text("# v35\n\n### Phase 1 - RUNNERWRITE (RW)\n", encoding="utf-8")
        write_phase_plan(
            repo,
            "RW",
            roadmap,
            body=(
                "# RW\n\n"
                "## Interface Freeze Gates\n\n"
                "- [ ] IF-0-RW-1\n\n"
                "## Lanes\n\n"
                "### SL-0 - Runner write\n"
                "- **Owned files**: `runner.py`\n"
                "- **Interfaces provided**: `IF-0-RW-1`\n"
            ),
        )
        return repo, roadmap

    def _raw_event(self, repo: Path, roadmap: Path, *, action: str, status: str) -> dict[str, object]:
        return {
            "timestamp": "2026-05-29T01:00:00Z",
            "repo": str(repo),
            "roadmap": str(roadmap),
            "phase": "RW",
            "action": action,
            "status": status,
            "source": "fixture",
            "schema_version": 2,
            "roadmap_sha256": roadmap_sha256(roadmap),
            "phase_sha256": phase_sha256(roadmap, "RW"),
        }

    def _append_raw_event(self, repo: Path, payload: dict[str, object]) -> None:
        path = repo / ".phase-loop" / "events.jsonl"
        path.parent.mkdir(exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")


if __name__ == "__main__":
    unittest.main()
