from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from phase_loop_test_utils import ROOT, make_repo

from phase_loop_runtime.discovery import repo_identity
from phase_loop_runtime.migrate_handoffs import _quiesced, migrate_handoffs


BIN = "phase-loop"
SKILL_FILES = tuple(
    ROOT / base / f"{harness}-{skill}" / "SKILL.md"
    for harness, base in (
        ("claude", "claude-config/claude-skills"),
        ("codex", "codex-config/skills"),
        ("gemini", "gemini-config/skills"),
        ("opencode", "opencode-config/skills"),
    )
    for skill in (
        "execute-phase",
        "plan-phase",
        "plan-detailed",
        "phase-roadmap-builder",
        "skill-editor",
        "skill-improvement-planner",
    )
)


def load_handoff_resolver():
    path = ROOT / "shared" / "phase-loop" / "handoff_path.py"
    spec = importlib.util.spec_from_file_location("handoff_path", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.resolve_handoff_path


def write_state(repo: Path, phases: dict[str, str]) -> None:
    loop = repo / ".phase-loop"
    loop.mkdir()
    (loop / "state.json").write_text(json.dumps({"phases": phases}), encoding="utf-8")


def write_event(repo: Path, action: str) -> None:
    event = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "action": action}
    path = repo / ".phase-loop" / "events.jsonl"
    path.parent.mkdir(exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")


def write_legacy_handoff(home: Path, repo: Path, skill: str) -> Path:
    identity = repo_identity(repo)
    handoff_dir = home / ".codex" / "skills" / skill / "handoffs" / identity.repo_hash / identity.branch_slug
    handoff_dir.mkdir(parents=True)
    content = (
        "---\n"
        f"from: {skill}\n"
        f"repo: {identity.repo_hash}\n"
        f"repo_root: {repo}\n"
        f"branch: {identity.branch}\n"
        f"branch_slug: {identity.branch_slug}\n"
        f"commit: {identity.commit}\n"
        f"artifact: {repo / 'plans' / 'phase-plan-v1-RUNNER.md'}\n"
        "---\n\n"
        "automation:\n"
        "  status: complete\n"
    )
    latest = handoff_dir / "latest.md"
    latest.write_text(content, encoding="utf-8")
    sibling = handoff_dir / "run-fixture.md"
    sibling.write_text(content, encoding="utf-8")
    return latest


class PhaseLoopHandoffMigrationTest(unittest.TestCase):
    def test_resolve_handoff_path_is_pure_repo_local_path(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            resolve_handoff_path = load_handoff_resolver()

            path = resolve_handoff_path(repo, "codex-plan-phase")

            self.assertEqual(path, repo.resolve() / ".dev-skills" / "handoffs" / "codex-plan-phase" / "latest.md")
            self.assertFalse((repo / ".dev-skills").exists())

    def test_quiesced_accepts_terminal_state(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            write_state(repo, {"HANDOFFS": "complete", "EXTRACT": "unplanned"})

            self.assertTrue(_quiesced(repo))

    def test_quiesced_rejects_non_terminal_recent_event_and_lock(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            write_state(repo, {"HANDOFFS": "planned"})
            self.assertFalse(_quiesced(repo))

            (repo / ".phase-loop" / "state.json").write_text(json.dumps({"phases": {"HANDOFFS": "complete"}}), encoding="utf-8")
            write_event(repo, "run")
            self.assertFalse(_quiesced(repo))

            (repo / ".phase-loop" / "events.jsonl").write_text("", encoding="utf-8")
            (repo / ".phase-loop" / "migration.lock").write_text("", encoding="utf-8")
            self.assertFalse(_quiesced(repo))

    def test_quiesced_allows_recent_manual_repair_event(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            write_state(repo, {"HANDOFFS": "complete"})
            write_event(repo, "manual_repair")

            self.assertTrue(_quiesced(repo))

    def test_dry_run_reports_matching_current_repo_without_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            repo = make_repo(tmp / "repo")
            home = tmp / "home"
            latest = write_legacy_handoff(home, repo, "codex-plan-phase")

            records = migrate_handoffs(repo, home=home)

            self.assertEqual(len(records), 2)
            self.assertTrue(all(record.status == "dry_run" for record in records))
            self.assertTrue(latest.exists())
            self.assertFalse((repo / ".dev-skills").exists())

    def test_apply_moves_matching_handoffs_and_second_apply_is_noop(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            repo = make_repo(tmp / "repo")
            write_state(repo, {"HANDOFFS": "complete"})
            home = tmp / "home"
            write_legacy_handoff(home, repo, "codex-plan-phase")

            first = migrate_handoffs(repo, apply=True, home=home)
            second = migrate_handoffs(repo, apply=True, home=home)

            target = repo / ".dev-skills" / "handoffs" / "codex-plan-phase" / "latest.md"
            self.assertTrue(target.exists())
            self.assertTrue(all(record.status == "migrated" for record in first))
            self.assertEqual(second, ())

    def test_apply_refuses_when_not_quiesced(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            repo = make_repo(tmp / "repo")
            write_state(repo, {"HANDOFFS": "executing"})
            home = tmp / "home"
            write_legacy_handoff(home, repo, "codex-plan-phase")

            records = migrate_handoffs(repo, apply=True, home=home)

            self.assertEqual(records[0].action, "blocked")
            self.assertIn("not_quiesced", records[0].status)
            self.assertFalse((repo / ".dev-skills").exists())

    def test_malformed_and_other_repo_handoffs_are_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            repo = make_repo(tmp / "repo")
            other = make_repo(tmp / "other")
            home = tmp / "home"
            write_legacy_handoff(home, other, "codex-plan-phase")
            malformed = home / ".codex" / "skills" / "codex-execute-phase" / "handoffs" / "bad" / "main"
            malformed.mkdir(parents=True)
            (malformed / "latest.md").write_text("secret-value\n", encoding="utf-8")

            records = migrate_handoffs(repo, home=home)

            self.assertTrue(all(record.status == "other_repo_or_malformed" for record in records))
            self.assertNotIn("secret-value", "\n".join(str(record) for record in records))

    def test_cli_accepts_migrate_handoffs_help_and_json_dry_run(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td) / "repo")
            env = dict(os.environ)
            env["HOME"] = str(Path(td) / "home")
            help_result = subprocess.run([str(BIN), "migrate-handoffs", "--help"], text=True, capture_output=True, check=True)
            dry_run = subprocess.run(
                [str(BIN), "migrate-handoffs", "--repo", str(repo), "--dry-run", "--json"],
                text=True,
                capture_output=True,
                env=env,
                check=True,
            )

            self.assertIn("--apply", help_result.stdout)
            self.assertEqual(json.loads(dry_run.stdout), [])

    def test_skill_text_uses_repo_local_handoffs_and_no_legacy_write_roots(self):
        forbidden = (
            "Handoff: `~/",
            "Latest handoff pointer: `~/",
            "HANDOFF_DIR=\"$HOME/",
            "HANDOFF_PATH=\"$HOME/",
        )
        for path in SKILL_FILES:
            with self.subTest(path=path):
                text = path.read_text(encoding="utf-8")
                self.assertTrue(".dev-skills/handoffs" in text or "resolve_handoff_path" in text)
                for needle in forbidden:
                    self.assertNotIn(needle, text)


if __name__ == "__main__":
    unittest.main()
