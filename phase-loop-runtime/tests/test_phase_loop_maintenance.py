import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest
from _dotfiles_tree import dotfiles_tree_present

# TESTDECOUPLE SL-1: this file reads dotfiles fleet paths (absent in the
# extracted agent-harness layout). Skip at MODULE level before any such read so
# collection does not error standalone; the marker keeps it deselected by
# `pytest -m "not dotfiles_integration"` and the conftest run-time hook.
if not dotfiles_tree_present():
    pytest.skip("requires dotfiles tree", allow_module_level=True)

pytestmark = pytest.mark.dotfiles_integration

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.events import read_events
from phase_loop_runtime.launcher import LaunchResult
from phase_loop_runtime.maintenance import MaintenanceOptions, SyncSkillsOptions, collect_reflection_inventory, sync_bridge_skills
from phase_loop_runtime.runner import run_loop
from phase_loop_runtime.skill_inventory import (
    CANONICAL_WORKFLOW_SKILLS,
    classify_skill_like_directories,
    inspect_vestigial_workflow_candidates,
    inspect_workflow_skill_inventory,
    resolve_source_skill_dir,
)
from phase_loop_test_utils import make_repo


class PhaseLoopMaintenanceTest(unittest.TestCase):
    def test_sync_skills_check_reports_missing_bridge_roots(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))

            with patch("phase_loop_runtime.skill_inventory.discover_installed_skill_roots", return_value=()):
                summary = sync_bridge_skills(repo, SyncSkillsOptions(harnesses=("codex",)))

            self.assertFalse(summary["blocked"])
            self.assertEqual(summary["bridge_skills"][0]["parity_status"], "missing_root")
            self.assertEqual(summary["workflow_sources"][0]["parity_status"], "missing_root")
            self.assertIn("vestigial_workflow_candidates", summary)
            self.assertIn("skill_classifications", summary)
            self.assertEqual(summary["changed"], [])

    def test_sync_skills_check_audits_workflow_pack_without_mutating_roots(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            install_root = Path(td) / "codex-home" / ".codex" / "skills"
            install_root.mkdir(parents=True)

            with patch("phase_loop_runtime.skill_inventory.discover_installed_skill_roots", return_value=(str(install_root),)):
                summary = sync_bridge_skills(repo, SyncSkillsOptions(harnesses=("codex", "claude")))

            self.assertFalse(summary["blocked"])
            self.assertEqual(summary["changed"], [])
            workflow_names = {
                record["harness_target"]: []
                for record in summary["workflow_sources"]
            }
            for record in summary["workflow_sources"]:
                workflow_names[record["harness_target"]].append(record["skill_name"])
            self.assertEqual(tuple(workflow_names["codex"]), CANONICAL_WORKFLOW_SKILLS["codex"])
            self.assertEqual(tuple(workflow_names["claude"]), CANONICAL_WORKFLOW_SKILLS["claude"])
            self.assertFalse(any(install_root.iterdir()))

    def test_workflow_source_resolution_uses_only_canonical_roots(self):
        repo = ROOT

        self.assertEqual(
            resolve_source_skill_dir(repo, "claude", "claude-plan-phase"),
            (repo / "claude-config" / "claude-skills" / "claude-plan-phase").resolve(),
        )
        self.assertIsNone(resolve_source_skill_dir(repo, "claude", "plan-phase"))
        self.assertIsNone(resolve_source_skill_dir(repo, "claude", "execute-phase"))
        self.assertIsNone(resolve_source_skill_dir(repo, "claude", "wsl-screenshots"))

    def test_workflow_inventory_reports_vestigial_candidates_distinctly(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            vestigial = repo / "claude-config" / "skills" / "plan-phase"
            utility = repo / "claude-config" / "skills" / "wsl-screenshots"
            canonical = repo / "claude-config" / "claude-skills" / "claude-plan-phase"
            for path in (vestigial, utility, canonical):
                path.mkdir(parents=True)
            (canonical / "SKILL.md").write_text("canonical\n", encoding="utf-8")
            (utility / "SKILL.md").write_text("utility\n", encoding="utf-8")

            self.assertEqual(resolve_source_skill_dir(repo, "claude", "claude-plan-phase"), canonical.resolve())
            self.assertIsNone(resolve_source_skill_dir(repo, "claude", "plan-phase"))
            self.assertIsNone(resolve_source_skill_dir(repo, "claude", "wsl-screenshots"))

            candidates = inspect_vestigial_workflow_candidates(repo)
            candidate_by_name = {record.candidate_name: record for record in candidates}
            self.assertTrue(candidate_by_name["plan-phase"].exists)
            self.assertEqual(candidate_by_name["plan-phase"].status, "archived-history")
            self.assertEqual(candidate_by_name["execute-phase"].status, "archived-history")

            classifications = classify_skill_like_directories(repo, ("claude",))
            by_name = {record.skill_name: record for record in classifications}
            self.assertEqual(by_name["claude-plan-phase"].classification, "canonical")
            self.assertEqual(by_name["wsl-screenshots"].classification, "legacy-utility")
            self.assertEqual(by_name["plan-phase"].classification, "archived-history")
            self.assertEqual(by_name["plan-phase"].canonical_replacement, "claude-plan-phase")

    def test_skill_classification_reports_all_cleanup_categories(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            canonical = repo / "codex-config" / "skills" / "codex-plan-phase"
            legacy = repo / "claude-config" / "skills" / "safe-edit"
            archived = repo / "claude-config" / "skills" / "execute-phase" / "reflections"
            removable = repo / "claude-config" / "skills" / "plan-phase"
            pi_role = repo / "phase-loop-pi" / "skills" / "phase-loop-repair"
            for path in (canonical, legacy, archived, removable, pi_role):
                path.mkdir(parents=True)
            for path in (canonical, legacy, removable, pi_role):
                (path / "SKILL.md").write_text("skill\n", encoding="utf-8")
            (repo / ".phase-loop" / "state.json").parent.mkdir()
            (repo / ".phase-loop" / "state.json").write_text("{}\n", encoding="utf-8")
            (repo / ".codex" / "phase-loop" / "events.jsonl").parent.mkdir(parents=True)
            (repo / ".codex" / "phase-loop" / "events.jsonl").write_text("", encoding="utf-8")

            classifications = classify_skill_like_directories(repo, ("codex", "claude"))
            by_name = {record.skill_name: record for record in classifications}

            self.assertEqual(by_name["codex-plan-phase"].classification, "canonical")
            self.assertEqual(by_name["safe-edit"].classification, "legacy-utility")
            self.assertEqual(by_name["execute-phase"].classification, "archived-history")
            self.assertEqual(by_name["plan-phase"].classification, "remove")
            self.assertEqual(by_name["phase-loop-repair"].classification, "pi-role")
            self.assertNotIn("state.json", by_name)
            self.assertNotIn("events.jsonl", by_name)

    def test_workflow_inventory_distinguishes_source_install_and_drift_statuses(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = make_repo(root / "repo-root")
            source = repo / "codex-config" / "skills" / "codex-plan-phase"
            source.mkdir(parents=True)
            (source / "SKILL.md").write_text("source\n", encoding="utf-8")
            install_root = root / "codex-home" / ".codex" / "skills"
            installed = install_root / "codex-plan-phase"
            installed.mkdir(parents=True)
            (installed / "SKILL.md").write_text("different\n", encoding="utf-8")

            with patch("phase_loop_runtime.skill_inventory.discover_installed_skill_roots", return_value=(str(install_root),)):
                records = inspect_workflow_skill_inventory(repo, ("codex",))

            by_name = {record.skill_name: record for record in records}
            self.assertEqual(by_name["codex-plan-phase"].parity_status, "drifted")
            self.assertEqual(by_name["codex-execute-phase"].parity_status, "missing_skill")

    def test_sync_skills_apply_repairs_bridge_symlink(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = make_repo(root / "repo-root")
            install_root = root / "codex-home" / ".codex" / "skills"
            target = install_root / "codex-phase-loop"

            with patch.dict("os.environ", {"HOME": str(root / "codex-home")}, clear=False):
                summary = sync_bridge_skills(repo, SyncSkillsOptions(harnesses=("codex",), apply=True))

            self.assertFalse(summary["blocked"])
            self.assertEqual(summary["bridge_skills"][0]["parity_status"], "ok")
            self.assertTrue(target.is_symlink())

    def test_sync_skills_apply_repairs_only_bridge_skills(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = make_repo(root / "repo-root")
            vestigial = repo / "claude-config" / "skills" / "plan-phase"
            vestigial.mkdir(parents=True)
            install_root = root / "claude-home" / ".claude" / "skills"

            with patch.dict("os.environ", {"HOME": str(root / "claude-home")}, clear=False):
                summary = sync_bridge_skills(repo, SyncSkillsOptions(harnesses=("claude",), apply=True))

            self.assertFalse(summary["blocked"])
            self.assertFalse((install_root / "plan-phase").exists())
            self.assertTrue((install_root / "claude-phase-loop").is_symlink())
            self.assertEqual(summary["changed"][0]["skill_name"], "claude-phase-loop")
            self.assertFalse((install_root / "claude-plan-phase").exists())
            self.assertFalse((install_root / "claude-execute-detailed").exists())

    def test_bootstrap_skips_vestigial_workflows_but_keeps_canonical_roots(self):
        bootstrap = (ROOT / "bootstrap.sh").read_text(encoding="utf-8")

        self.assertIn('"$skill_name" == "plan-phase"', bootstrap)
        self.assertIn('"$skill_name" == "execute-phase"', bootstrap)
        self.assertIn("claude-config/claude-skills", bootstrap)
        self.assertIn("codex-config/skills", bootstrap)
        self.assertIn("gemini-config/skills", bootstrap)
        self.assertIn("opencode-config/skills", bootstrap)
        self.assertIn("wsl-screenshots", bootstrap)

    def test_sync_skills_apply_refuses_active_product_loop(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            lock = repo / ".phase-loop" / "active-loop.json"
            lock.parent.mkdir(parents=True)
            lock.write_text(json.dumps({"mode": "product", "pid": os.getpid()}))

            summary = sync_bridge_skills(repo, SyncSkillsOptions(harnesses=("codex",), apply=True))

            self.assertTrue(summary["blocked"])
            self.assertEqual(summary["blocker"]["blocker_class"], "dirty_worktree_conflict")

    def test_planner_only_dry_run_inventory_excludes_archive(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            active = repo / "codex-config" / "skills" / "codex-plan-phase" / "reflections" / "repo" / "branch"
            archived = active / "archive"
            active.mkdir(parents=True)
            archived.mkdir()
            (active / "one.md").write_text("active\n")
            (archived / "two.md").write_text("archived\n")

            inventory = collect_reflection_inventory(repo)
            source_root = next(root for root in inventory["roots"] if root["root"].endswith("codex-config/skills"))
            self.assertEqual(source_root["count"], 1)

            snapshot, results = run_loop(repo, roadmap, action="maintain-skills", dry_run=True)
            command = " ".join(results[0].command)
            self.assertIn("codex-skill-improvement-planner --min-reflections 2", command)
            self.assertNotIn("codex-plan-phase", command)
            self.assertNotIn("codex-execute-phase", command)
            self.assertEqual(snapshot.last_action, "maintain-skills")
            events = read_events(repo)
            self.assertEqual(events[-1]["metadata"]["reflection_inventory"]["roots"][1]["count"], 1)
            self.assertNotIn("secret-value", json.dumps(events[-1]))

    def test_active_product_loop_refuses_maintenance(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            lock = repo / ".phase-loop" / "active-loop.json"
            lock.parent.mkdir(parents=True)
            lock.write_text(json.dumps({"mode": "product", "pid": os.getpid()}))

            snapshot, results = run_loop(repo, roadmap, action="maintain-skills", dry_run=True)
            self.assertEqual(results, [])
            self.assertTrue(snapshot.human_required)
            self.assertEqual(snapshot.blocker_class, "dirty_worktree_conflict")
            self.assertTrue((repo / ".phase-loop" / "tui-handoff.md").exists())

    def test_editor_gating_and_allowlisted_command(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            blocked, blocked_results = run_loop(
                repo,
                roadmap,
                action="maintain-skills",
                dry_run=True,
                maintenance_options=MaintenanceOptions(apply_skill_edits=True),
            )
            self.assertEqual(blocked_results, [])
            self.assertTrue(blocked.human_required)
            self.assertEqual(blocked.blocker_class, "product_decision_missing")
            self.assertIn("skill-maintenance", (repo / ".phase-loop" / "tui-handoff.md").read_text())

            plan = repo / "plans" / "skill-plan.md"
            plan.write_text("# plan\n")
            snapshot, results = run_loop(
                repo,
                roadmap,
                action="maintain-skills",
                dry_run=True,
                maintenance_options=MaintenanceOptions(
                    apply_skill_edits=True,
                    allow_skills=("codex-plan-phase",),
                    improvement_plan=plan,
                ),
            )
            command = " ".join(results[0].command)
            self.assertIn("codex-skill-editor --improvement-plan", command)
            self.assertIn("--allow-skill codex-plan-phase", command)
            self.assertFalse(snapshot.human_required)

    def test_product_run_refuses_active_skill_maintenance_loop(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            lock = repo / ".phase-loop" / "active-loop.json"
            lock.parent.mkdir(parents=True)
            lock.write_text(json.dumps({"mode": "skill-maintenance", "pid": os.getpid()}))

            snapshot, results = run_loop(repo, roadmap, phase="RUNNER", dry_run=True)
            self.assertEqual(results, [])
            self.assertTrue(snapshot.human_required)
            self.assertEqual(snapshot.blocker_class, "dirty_worktree_conflict")

    def test_failed_maintenance_launch_marks_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            def fake_launch(command, dry_run=False, log_path=None, stream_output=False, **kwargs):
                return LaunchResult(command=command, returncode=42, output="failed\n")

            with patch("phase_loop_runtime.maintenance.launch", side_effect=fake_launch):
                snapshot, results = run_loop(repo, roadmap, action="maintain-skills")

            self.assertEqual(results[0].returncode, 42)
            self.assertNotEqual(snapshot.phases.get("RUNNER"), "executing")
            events = read_events(repo)
            self.assertEqual(events[-1]["status"], "unknown")
            self.assertEqual(events[-1]["metadata"]["launch"]["returncode"], 42)
            self.assertTrue((repo / ".phase-loop" / "tui-handoff.md").exists())

    def test_maintenance_writes_launch_artifacts_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            snapshot, results = run_loop(repo, roadmap, action="maintain-skills", dry_run=True)

            self.assertEqual(snapshot.last_action, "maintain-skills")
            self.assertIsNotNone(results[0].log_path)
            self.assertTrue(Path(results[0].log_path).exists())
            events = read_events(repo)
            self.assertEqual(events[-1]["phase"], "SKILL-MAINTENANCE")
            self.assertIn("artifacts", events[-1]["metadata"])

    def test_bypass_approvals_is_explicit_in_maintenance_command(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            _snapshot, results = run_loop(repo, roadmap, action="maintain-skills", dry_run=True, bypass_approvals=True)

            command = " ".join(results[0].command)
            self.assertIn("--dangerously-bypass-approvals-and-sandbox", command)
            self.assertNotIn("--sandbox danger-full-access", command)

    def test_maintenance_remains_on_codex_helper_even_when_product_executor_exists(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            _snapshot, results = run_loop(repo, roadmap, action="maintain-skills", dry_run=True)

            command = results[0].command
            self.assertEqual(command[:2], ["codex", "exec"])
            self.assertNotIn("--phase-loop-stub", command)
            self.assertIn("codex-skill-improvement-planner", command[-1])


if __name__ == "__main__":
    unittest.main()
