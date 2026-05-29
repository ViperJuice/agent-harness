import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.cli import build_parser, main
from phase_loop_runtime.events_migration import MigrationError, migrate_ledger
from phase_loop_runtime.provenance import phase_sha256, roadmap_sha256
from phase_loop_runtime.reconcile import reconcile
from phase_loop_test_utils import make_repo, write_phase_plan


DATA = Path(__file__).resolve().parent / "data"


class PhaseLoopEventsMigrationTest(unittest.TestCase):
    def test_migrate_events_help_documents_supported_flags(self):
        output = io.StringIO()

        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            build_parser().parse_args(["migrate-events", "--help"])

        self.assertEqual(raised.exception.code, 0)
        help_text = output.getvalue()
        self.assertIn("--repo", help_text)
        self.assertIn("--dry-run", help_text)
        self.assertIn("--backup-suffix", help_text)

    def test_cli_dry_run_outputs_summary_without_writing(self):
        with tempfile.TemporaryDirectory() as td:
            repo, _roadmap = self._fixture(Path(td))
            event_path = self._seed_fixture(repo, "legacy_def4_pre_writer_namespacing.jsonl")
            before = event_path.read_text(encoding="utf-8")
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                code = main(["migrate-events", "--repo", str(repo), "--dry-run"])

            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue()), {"would_migrate": 1, "already_migrated": 0})
            self.assertEqual(event_path.read_text(encoding="utf-8"), before)
            self.assertFalse(event_path.with_name("events.jsonl.bak-before-def4-migrate").exists())

    def test_apply_creates_backup_rewrites_legacy_only_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            repo, _roadmap = self._fixture(Path(td))
            event_path = self._seed_fixture(repo, "legacy_def4_pre_writer_namespacing.jsonl")
            self._append_event(repo, {"timestamp": "2026-05-29T01:00:01Z", "action": "run", "metadata": {"note": "runner event"}})
            before = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]

            result = migrate_ledger(repo, dry_run=False, backup_suffix=".bak-before-def4-migrate")

            self.assertEqual(result.to_json()["migrated"], 1)
            self.assertEqual(result.to_json()["already_migrated"], 0)
            backup = event_path.with_name("events.jsonl.bak-before-def4-migrate")
            self.assertTrue(backup.exists())
            self.assertEqual(backup.read_text(encoding="utf-8"), "\n".join(json.dumps(event, separators=(",", ":")) for event in before) + "\n")
            after = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(after[0]["action"], "executor.closeout")
            self.assertEqual({k: v for k, v in after[0].items() if k != "action"}, {k: v for k, v in before[0].items() if k != "action"})
            self.assertEqual(after[1], before[1])

            second = migrate_ledger(repo, dry_run=False, backup_suffix=".bak-before-def4-migrate")

            self.assertEqual(second.to_json()["migrated"], 0)
            self.assertEqual(second.to_json()["already_migrated"], 1)
            self.assertEqual(backup.read_text(encoding="utf-8"), "\n".join(json.dumps(event, separators=(",", ":")) for event in before) + "\n")

    def test_existing_backup_refuses_without_writing(self):
        with tempfile.TemporaryDirectory() as td:
            repo, _roadmap = self._fixture(Path(td))
            event_path = self._seed_fixture(repo, "legacy_def4_pre_writer_namespacing.jsonl")
            before = event_path.read_text(encoding="utf-8")
            backup = event_path.with_name("events.jsonl.keep")
            backup.write_text("keep me", encoding="utf-8")

            with self.assertRaisesRegex(MigrationError, "backup path already exists"):
                migrate_ledger(repo, dry_run=False, backup_suffix=".keep")

            self.assertEqual(event_path.read_text(encoding="utf-8"), before)
            self.assertEqual(backup.read_text(encoding="utf-8"), "keep me")

    def test_malformed_line_aborts_before_backup_or_write(self):
        with tempfile.TemporaryDirectory() as td:
            repo, _roadmap = self._fixture(Path(td))
            event_path = repo / ".phase-loop" / "events.jsonl"
            event_path.parent.mkdir(exist_ok=True)
            before = '{"action":"run","metadata":{"executor_closeout_event":{}}}\nnot-json\n'
            event_path.write_text(before, encoding="utf-8")

            with self.assertRaisesRegex(MigrationError, "line 2"):
                migrate_ledger(repo, dry_run=False, backup_suffix=".bak-before-def4-migrate")

            self.assertEqual(event_path.read_text(encoding="utf-8"), before)
            self.assertFalse(event_path.with_name("events.jsonl.bak-before-def4-migrate").exists())

    def test_reconcile_snapshot_is_equivalent_before_and_after_migration(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = self._fixture(Path(td))
            self._seed_fixture(repo, "legacy_def4_pre_writer_namespacing.jsonl")
            before = reconcile(repo, roadmap)

            migrate_ledger(repo, dry_run=False, backup_suffix=".bak-before-def4-migrate")
            after = reconcile(repo, roadmap)

            self.assertEqual(after.phases["RW"], before.phases["RW"])
            self.assertEqual(after.dirty_paths, before.dirty_paths)
            self.assertEqual(after.phase_owned_dirty_paths, before.phase_owned_dirty_paths)
            self.assertEqual(after.unowned_dirty_paths, before.unowned_dirty_paths)
            self.assertEqual(after.terminal_summary, before.terminal_summary)

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

    def _seed_fixture(self, repo: Path, name: str) -> Path:
        event_path = repo / ".phase-loop" / "events.jsonl"
        event_path.parent.mkdir(exist_ok=True)
        event_path.write_text(DATA.joinpath(name).read_text(encoding="utf-8"), encoding="utf-8")
        return event_path

    def _append_event(self, repo: Path, payload: dict) -> None:
        event_path = repo / ".phase-loop" / "events.jsonl"
        base = {
            "repo": str(repo),
            "roadmap": str(repo / "specs" / "phase-plans-v35.md"),
            "phase": "RW",
            "status": "planned",
            "source": "fixture",
            "schema_version": 2,
            "roadmap_sha256": roadmap_sha256(repo / "specs" / "phase-plans-v35.md"),
            "phase_sha256": phase_sha256(repo / "specs" / "phase-plans-v35.md", "RW"),
        }
        with event_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({**base, **payload}, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    unittest.main()
