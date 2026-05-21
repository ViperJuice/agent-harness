import json
import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_test_utils import ROOT, make_code_index_blocker_fixture, make_greenfield_closeout_fixture, make_repo
from phase_loop_test_utils import provenanced_event, provenanced_state
from phase_loop_test_utils import write_phase_plan
from test_phase_loop_pipeline_bundle import _write_bundle, _write_protected_source
from phase_loop_runtime.cli import build_parser, main
from phase_loop_runtime.events import append_event
from phase_loop_runtime.launcher import LaunchResult
from phase_loop_runtime.state import write_state
from phase_loop_runtime.state_degradation import load_degradation, record_degradation
from phase_loop_smoke_utils import append_manual_import_event, isolated_codex_home, write_skill_handoff


BIN = shutil.which("phase-loop") or "phase-loop"
CODEX_ALIAS_BIN = shutil.which("codex-phase-loop") or "codex-phase-loop"


class PhaseLoopCliTest(unittest.TestCase):
    def test_neutral_and_codex_alias_wrappers_share_public_help(self):
        neutral_help = subprocess.run([str(BIN), "--help"], text=True, capture_output=True, check=True)
        alias_help = subprocess.run([str(CODEX_ALIAS_BIN), "--help"], text=True, capture_output=True, check=True)

        for output in (neutral_help.stdout, alias_help.stdout):
            self.assertIn("Neutral phase-loop runner", output)
            self.assertIn("codex-phase-loop remains a Codex bridge alias", output)
            self.assertIn("version", output)
            self.assertIn("execute", output)

    def test_version_flag_and_command_report_same_public_version(self):
        flag = subprocess.run([str(BIN), "--version"], text=True, capture_output=True, check=True)
        command = subprocess.run([str(BIN), "version"], text=True, capture_output=True, check=True)
        alias_flag = subprocess.run([str(CODEX_ALIAS_BIN), "--version"], text=True, capture_output=True, check=True)

        self.assertEqual(flag.stdout, command.stdout)
        self.assertEqual(flag.stdout, alias_flag.stdout)
        self.assertRegex(flag.stdout, r"^phase-loop \d+\.\d+\.\d+")

    def test_execute_help_documents_direct_bridge_flags(self):
        result = subprocess.run([str(BIN), "execute", "--help"], text=True, capture_output=True, check=True)

        self.assertIn("phase", result.stdout)
        self.assertIn("--bundle", result.stdout)
        self.assertIn("--output", result.stdout)
        self.assertIn("--mode", result.stdout)

    def test_lane_scheduler_mode_accepts_explicit_off_for_compatibility(self):
        args = build_parser().parse_args(["--lane-scheduler", "off", "run"])
        default_args = build_parser().parse_args(["run"])

        self.assertEqual(args.lane_scheduler_mode, "off")
        self.assertIsNone(default_args.lane_scheduler_mode)

    def test_reset_capability_is_limited_to_dispatch_commands(self):
        parser = build_parser()
        for command in ("run", "resume", "dry-run"):
            self.assertTrue(parser.parse_args([command, "--reset-capability"]).reset_capability)
        with self.assertRaises(SystemExit):
            parser.parse_args(["status", "--reset-capability"])

    def test_rotation_flags_are_limited_to_dispatch_commands(self):
        parser = build_parser()
        for command in ("run", "resume", "dry-run"):
            args = parser.parse_args(
                [
                    command,
                    "--rotate-executors",
                    "codex,claude",
                    "--rotation-mode",
                    "work_unit",
                    "--rotation-on-policy-pin",
                    "fallback-next",
                ]
            )
            self.assertEqual(args.rotate_executors, "codex,claude")
            self.assertEqual(args.rotation_mode, "work_unit")
            self.assertEqual(args.rotation_on_policy_pin, "fallback-next")
        for command in ("status", "handoff", "archive-state", "monitor", "execute", "reconcile", "maintain-skills", "sync-skills"):
            with self.subTest(command=command), self.assertRaises(SystemExit):
                parser.parse_args([command, "--rotate-executors", "codex"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["run", "--rotation-mode", "lane"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["run", "--rotation-on-policy-pin", "consume"])

    def test_run_reset_capability_clears_degradation_before_run_loop(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            record_degradation(repo, "claude", "account_or_billing_setup", "RUNNER", "Claude auth missing", 300)

            def fake_run_loop(**kwargs):
                self.assertEqual(load_degradation(repo), {})
                self.assertEqual(kwargs["rotate_executors"], "codex,claude")
                self.assertEqual(kwargs["rotation_mode"], "phase")
                self.assertEqual(kwargs["rotation_on_policy_pin"], "skip")
                return provenanced_state(repo, roadmap, {"RUNNER": "unplanned"}), []

            with patch("phase_loop_runtime.cli.run_loop", side_effect=fake_run_loop), patch("phase_loop_runtime.cli.render_status", return_value="status"):
                self.assertEqual(
                    main(
                        [
                            "run",
                            "--repo",
                            str(repo),
                            "--roadmap",
                            str(roadmap),
                            "--reset-capability",
                            "--rotate-executors",
                            "codex,claude",
                            "--max-phases",
                            "0",
                        ]
                    ),
                    0,
                )

    def test_help_status_json_and_dry_run_aliases(self):
        help_result = subprocess.run([str(BIN), "--help"], text=True, capture_output=True, check=True)
        self.assertIn("maintain-skills", help_result.stdout)
        self.assertIn("sync-skills", help_result.stdout)
        self.assertIn("monitor", help_result.stdout)
        self.assertIn("--observe", help_result.stdout)
        self.assertIn("--no-observe", help_result.stdout)
        self.assertIn("--stream-output", help_result.stdout)
        self.assertIn("--bypass-approvals", help_result.stdout)
        self.assertIn("--executor", help_result.stdout)
        self.assertIn("--command-name", help_result.stdout)
        self.assertIn("--command-template", help_result.stdout)
        self.assertIn("--heartbeat-interval-seconds", help_result.stdout)
        self.assertIn("--no-heartbeat", help_result.stdout)
        self.assertIn("--closeout-mode", help_result.stdout)
        self.assertIn("handoff", help_result.stdout)
        maintenance_help = subprocess.run([str(BIN), "maintain-skills", "--help"], text=True, capture_output=True, check=True)
        self.assertIn("--min-reflections", maintenance_help.stdout)
        self.assertIn("--apply-skill-edits", maintenance_help.stdout)
        self.assertIn("--allow-skill", maintenance_help.stdout)
        self.assertIn("--improvement-plan", maintenance_help.stdout)
        sync_help = subprocess.run([str(BIN), "sync-skills", "--help"], text=True, capture_output=True, check=True)
        self.assertIn("--harness", sync_help.stdout)
        self.assertIn("--apply", sync_help.stdout)

        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            status = subprocess.run([str(BIN), "status", "--repo", str(repo), "--roadmap", str(roadmap), "--json"], text=True, capture_output=True, check=True)
            self.assertEqual(json.loads(status.stdout)["phases"]["RUNNER"], "unplanned")
            self.assertIn("git_topology", json.loads(status.stdout))
            self.assertTrue((repo / ".phase-loop" / "tui-handoff.md").exists())

            dry_run = subprocess.run([str(BIN), "--repo", str(repo), "--roadmap", str(roadmap), "--dry-run"], text=True, capture_output=True, check=True)
            self.assertIn("Executor: codex", dry_run.stdout)
            self.assertIn("Injection mode: prompt_only", dry_run.stdout)
            self.assertIn("Expected skill pack: codex-plan-phase", dry_run.stdout)
            self.assertIn("codex exec", dry_run.stdout)
            self.assertNotIn("--output-last-message", dry_run.stdout)
            self.assertTrue((repo / ".phase-loop" / "events.jsonl").exists())

            subcommand = subprocess.run([str(BIN), "dry-run", "--repo", str(repo), "--roadmap", str(roadmap)], text=True, capture_output=True, check=True)
            self.assertIn("Phase statuses", subcommand.stdout)

            handoff = subprocess.run([str(BIN), "handoff", "--repo", str(repo), "--roadmap", str(roadmap)], text=True, capture_output=True, check=True)
            self.assertIn("Machine Sources", handoff.stdout)
            self.assertIn("Current Status", handoff.stdout)
            self.assertIn("Resume Command", handoff.stdout)
            self.assertIn("Phase statuses", subcommand.stdout)

            handoff_json = subprocess.run([str(BIN), "handoff", "--repo", str(repo), "--roadmap", str(roadmap), "--json"], text=True, capture_output=True, check=True)
            self.assertTrue(json.loads(handoff_json.stdout)["tui_handoff_exists"])

            monitor = subprocess.run([str(BIN), "monitor", "--repo", str(repo), "--roadmap", str(roadmap), "--once", "--json"], text=True, capture_output=True, check=True)
            monitor_data = json.loads(monitor.stdout)
            self.assertIn("monitor_status", monitor_data)
            self.assertEqual(monitor_data["monitor_status"]["current_phase"], "CONTRACT")

            alias_help = subprocess.run([str(CODEX_ALIAS_BIN), "--help"], text=True, capture_output=True, check=True)
            self.assertIn("maintain-skills", alias_help.stdout)

    def test_sync_skills_json_reports_bridge_parity_without_roadmap(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            result = subprocess.run(
                [str(BIN), "sync-skills", "--repo", str(repo), "--harness", "codex", "--json"],
                text=True,
                capture_output=True,
                check=True,
            )
            data = json.loads(result.stdout)
            self.assertEqual(data["bridge_skills"][0]["harness_target"], "codex")
            self.assertIn("parity_status", data["bridge_skills"][0])
            self.assertIn("workflow_sources", data)
            self.assertIn("vestigial_workflow_candidates", data)
            self.assertIn("skill_classifications", data)
            self.assertEqual(data["workflow_sources"][0]["harness_target"], "codex")

    def test_sync_skills_text_labels_bridge_workflow_and_vestigial_records(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            (repo / "claude-config" / "skills" / "plan-phase").mkdir(parents=True)
            (repo / "claude-config" / "skills" / "execute-phase").mkdir(parents=True)
            (repo / "claude-config" / "skills" / "execute-phase" / "SKILL.md").write_text("legacy\n", encoding="utf-8")

            result = subprocess.run(
                [str(BIN), "sync-skills", "--repo", str(repo), "--harness", "claude"],
                text=True,
                capture_output=True,
                check=True,
                env={**os.environ, "HOME": str(Path(td) / "empty-home")},
            )

            self.assertIn("Bridge parity:", result.stdout)
            self.assertIn("Workflow source:", result.stdout)
            self.assertIn("Vestigial workflow:", result.stdout)
            self.assertIn("Skill classification:", result.stdout)
            self.assertIn("missing_root", result.stdout)
            self.assertIn("archived-history", result.stdout)
            self.assertIn("remove", result.stdout)

    def test_run_dry_run_explicit_phase_and_maintain_skills(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            result = subprocess.run([str(BIN), "run", "--repo", str(repo), "--roadmap", str(roadmap), "--phase", "RUNNER", "--dry-run"], text=True, capture_output=True, check=True)
            self.assertIn("RUNNER", result.stdout)
            maintain = subprocess.run([str(BIN), "maintain-skills", "--repo", str(repo), "--roadmap", str(roadmap), "--dry-run"], text=True, capture_output=True, check=True)
            self.assertIn("model_reasoning_effort", maintain.stdout)

    def test_non_codex_executor_reports_claude_live_dry_run_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            result = subprocess.run(
                [str(BIN), "run", "--repo", str(repo), "--roadmap", str(roadmap), "--phase", "RUNNER", "--dry-run", "--executor", "claude"],
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("Executor: claude", result.stdout)
            self.assertIn("Injection mode: context_file", result.stdout)
            self.assertIn("Expected skill pack:", result.stdout)
            self.assertIn("claude-phase-roadmap-builder", result.stdout)
            self.assertIn("claude-phase-loop", result.stdout)
            self.assertIn("claude -p --verbose --output-format stream-json", result.stdout)

    def test_non_codex_executor_reports_opencode_live_dry_run_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            result = subprocess.run(
                [
                    str(BIN),
                    "run",
                    "--repo",
                    str(repo),
                    "--roadmap",
                    str(roadmap),
                    "--phase",
                    "RUNNER",
                    "--dry-run",
                    "--executor",
                    "opencode",
                    "--bypass-approvals",
                ],
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("Executor: opencode", result.stdout)
            self.assertIn("Injection mode: context_file", result.stdout)
            self.assertIn("Expected skill pack: opencode-plan-phase", result.stdout)
            self.assertIn("opencode run", result.stdout)

    def test_command_executor_reports_blocked_without_adapter_inputs(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            result = subprocess.run(
                [str(BIN), "run", "--repo", str(repo), "--roadmap", str(roadmap), "--phase", "RUNNER", "--dry-run", "--executor", "command"],
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 5)
            self.assertIn("blocked", result.stdout.lower())
            self.assertIn("explicit adapter inputs", result.stdout)

    def test_command_executor_reports_dry_run_metadata_with_template(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            result = subprocess.run(
                [
                    str(BIN),
                    "run",
                    "--repo",
                    str(repo),
                    "--roadmap",
                    str(roadmap),
                    "--phase",
                    "RUNNER",
                    "--dry-run",
                    "--executor",
                    "command",
                    "--command-name",
                    "wrapped-cli",
                    "--command-template",
                    "wrapped-cli --cwd {cwd} --roadmap {roadmap} --phase {phase} --context-file {context_file}",
                ],
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("Executor: command", result.stdout)
            self.assertIn("Injection mode: context_file", result.stdout)
            self.assertIn("wrapped-cli --cwd", result.stdout)

    def test_closeout_mode_is_passed_to_run_loop(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            calls = []

            def fake_run_loop(**kwargs):
                calls.append(kwargs)
                return provenanced_state(repo, roadmap, {"RUNNER": "planned"}), []

            with patch("phase_loop_runtime.cli.run_loop", side_effect=fake_run_loop), patch("phase_loop_runtime.cli.render_status", return_value="status"):
                self.assertEqual(main(["run", "--repo", str(repo), "--roadmap", str(roadmap), "--closeout-mode", "push"]), 0)
                self.assertEqual(main(["dry-run", "--repo", str(repo), "--roadmap", str(roadmap), "--closeout-mode", "commit"]), 0)

            self.assertEqual(calls[0]["closeout_mode"], "push")
            self.assertEqual(calls[1]["closeout_mode"], "commit")

    def test_execute_direct_invocation_writes_closeout_with_supplied_bundle(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            source = _write_protected_source(repo)
            bundle = _write_bundle(repo, protected_sha=hashlib.sha256(source.read_bytes()).hexdigest())
            append_event(repo, provenanced_event(repo, roadmap, "RUNNER", "planned"))
            output = repo / "bridge-closeout.json"

            result = subprocess.run(
                [
                    str(BIN),
                    "execute",
                    "RUNNER",
                    "--repo",
                    str(repo),
                    "--roadmap",
                    str(roadmap),
                    "--bundle",
                    str(bundle),
                    "--output",
                    str(output),
                    "--mode",
                    "execute",
                    "--dry-run",
                    "--json",
                ],
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertEqual(json.loads(result.stdout)["current_phase"], "RUNNER")
            closeout = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(closeout["schema"], "phase_loop_closeout.v1")
            self.assertEqual(closeout["phase"], "RUNNER")
            self.assertEqual(closeout["source_bundle"]["sha256"], hashlib.sha256(bundle.read_bytes()).hexdigest())
            self.assertEqual(closeout["source_bundle"]["phase_id"], "pipeline.phase.runner")
            self.assertEqual(closeout["artifacts"]["plan_path"], str(plan))

    def test_execute_direct_invocation_invalid_mode_writes_typed_closeout(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            output = repo / "bridge-closeout.json"

            result = subprocess.run(
                [
                    str(BIN),
                    "execute",
                    "RUNNER",
                    "--repo",
                    str(repo),
                    "--roadmap",
                    str(roadmap),
                    "--output",
                    str(output),
                    "--mode",
                    "inspect",
                    "--json",
                ],
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 1)
            closeout = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(closeout["terminal_status"], "blocked")
            self.assertEqual(closeout["automation"]["blocker_class"], "contract_bug")
            self.assertIn("does not support --mode", closeout["automation"]["blocker_summary"])

    def test_execute_direct_invocation_pipeline_required_without_bundle_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            output = repo / "bridge-closeout.json"

            result = subprocess.run(
                [
                    str(BIN),
                    "execute",
                    "RUNNER",
                    "--repo",
                    str(repo),
                    "--roadmap",
                    str(roadmap),
                    "--output",
                    str(output),
                    "--pipeline-mode",
                    "pipeline_required",
                    "--json",
                ],
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 1)
            closeout = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(closeout["terminal_status"], "stale_input")
            self.assertEqual(closeout["automation"]["next_command"], "none - missing_source_bundle")

    def test_execute_direct_invocation_pipeline_required_malformed_bundle_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            bundle = repo / ".pipeline" / "artifacts" / "phase-source-bundle.json"
            bundle.parent.mkdir(parents=True, exist_ok=True)
            bundle.write_text("not-json\n", encoding="utf-8")
            output = repo / "bridge-closeout.json"

            result = subprocess.run(
                [
                    str(BIN),
                    "execute",
                    "RUNNER",
                    "--repo",
                    str(repo),
                    "--roadmap",
                    str(roadmap),
                    "--bundle",
                    str(bundle),
                    "--output",
                    str(output),
                    "--pipeline-mode",
                    "pipeline_required",
                    "--json",
                ],
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 1)
            closeout = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(closeout["terminal_status"], "stale_input")
            self.assertEqual(closeout["automation"]["next_command"], "none - malformed_source_bundle")
            self.assertFalse(closeout["automation"]["human_required"])

    def test_run_writes_observability_artifacts_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            result = subprocess.run([str(BIN), "run", "--repo", str(repo), "--roadmap", str(roadmap), "--phase", "RUNNER", "--dry-run"], text=True, capture_output=True, check=True)

            self.assertIn("Log:", result.stdout)
            runs = sorted((repo / ".phase-loop" / "runs").glob("*/"))
            self.assertEqual(len(runs), 1)
            self.assertTrue((runs[0] / "launch.json").exists())
            self.assertTrue((runs[0] / "output.log").exists())
            self.assertTrue((runs[0] / "heartbeat.json").exists())
            self.assertTrue((runs[0] / "terminal-summary.json").exists())
            self.assertTrue((repo / ".phase-loop" / "metrics.jsonl").exists())
            status = subprocess.run(
                [str(BIN), "status", "--repo", str(repo), "--roadmap", str(roadmap)],
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertIn("Metrics:", status.stdout)

    def test_no_observe_suppresses_launch_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            result = subprocess.run(
                [str(BIN), "run", "--repo", str(repo), "--roadmap", str(roadmap), "--phase", "RUNNER", "--dry-run", "--no-observe"],
                text=True,
                capture_output=True,
                check=True,
            )

            self.assertNotIn("Log:", result.stdout)
            self.assertFalse((repo / ".phase-loop" / "runs").exists())

    def test_run_returns_nonzero_on_failed_child(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            def fake_run_loop(**kwargs):
                return kwargs["roadmap"], [LaunchResult(command=["codex", "exec"], returncode=42)]

            with patch("phase_loop_runtime.cli.run_loop", side_effect=fake_run_loop), patch("phase_loop_runtime.cli.render_status", return_value="status"):
                self.assertEqual(main(["run", "--repo", str(repo), "--roadmap", str(roadmap)]), 1)

    def test_run_returns_5_on_blocked_terminal_status(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            def fake_run_loop(**kwargs):
                from dataclasses import replace as _replace
                snapshot = provenanced_state(repo, roadmap, {"RUNNER": "blocked"})
                snapshot = _replace(snapshot, terminal_summary={"terminal_status": "blocked"})
                return snapshot, []

            with patch("phase_loop_runtime.cli.run_loop", side_effect=fake_run_loop), patch("phase_loop_runtime.cli.render_status", return_value="status"):
                self.assertEqual(main(["run", "--repo", str(repo), "--roadmap", str(roadmap)]), 5)

    def test_run_returns_5_when_snapshot_carries_blocker_class(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            def fake_run_loop(**kwargs):
                from dataclasses import replace as _replace
                snapshot = provenanced_state(repo, roadmap, {"RUNNER": "blocked"})
                snapshot = _replace(snapshot, blocker_class="contract_bug", blocker_summary="malformed plan")
                return snapshot, []

            with patch("phase_loop_runtime.cli.run_loop", side_effect=fake_run_loop), patch("phase_loop_runtime.cli.render_status", return_value="status"):
                self.assertEqual(main(["run", "--repo", str(repo), "--roadmap", str(roadmap)]), 5)

    def test_run_returns_1_when_failed_child_outranks_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            def fake_run_loop(**kwargs):
                from dataclasses import replace as _replace
                snapshot = provenanced_state(repo, roadmap, {"RUNNER": "blocked"})
                snapshot = _replace(snapshot, terminal_summary={"terminal_status": "blocked"})
                return snapshot, [LaunchResult(command=["codex", "exec"], returncode=42)]

            with patch("phase_loop_runtime.cli.run_loop", side_effect=fake_run_loop), patch("phase_loop_runtime.cli.render_status", return_value="status"):
                self.assertEqual(main(["run", "--repo", str(repo), "--roadmap", str(roadmap)]), 1)

    def test_reconcile_synthesizes_manual_repair_event_and_marks_phase_complete(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            result = subprocess.run(
                [str(BIN), "reconcile", "--repo", str(repo), "--roadmap", str(roadmap), "--phase", "RUNNER", "--repair-summary", "fixture recovery"],
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0)
            from phase_loop_runtime.events import read_events
            events = read_events(repo)
            self.assertGreaterEqual(len(events), 1)
            last = events[-1]
            self.assertEqual(last["action"], "manual_repair")
            self.assertEqual(last["status"], "complete")
            self.assertEqual(last["phase"], "RUNNER")
            self.assertTrue(last["metadata"]["manual_repair"]["clears_blocker"])
            self.assertEqual(last["metadata"]["manual_repair"]["closeout_policy"], "commit")
            self.assertEqual(last["metadata"]["manual_repair"]["repair_summary"], "fixture recovery")
            self.assertEqual(last["source"], "reconcile")
            self.assertTrue(last["metadata"]["manual_repair"]["closeout_commit"])

    def test_reconcile_refuses_dirty_tree(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            (repo / "dirty.txt").write_text("uncommitted\n")

            result = subprocess.run(
                [str(BIN), "reconcile", "--repo", str(repo), "--roadmap", str(roadmap), "--phase", "RUNNER"],
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("working tree is dirty", result.stderr)

    def test_reconcile_allow_dirty_overrides_refusal(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            (repo / "dirty.txt").write_text("uncommitted\n")

            result = subprocess.run(
                [str(BIN), "reconcile", "--repo", str(repo), "--roadmap", str(roadmap), "--phase", "RUNNER", "--allow-dirty"],
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0)

    def test_reconcile_rejects_unknown_phase(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            result = subprocess.run(
                [str(BIN), "reconcile", "--repo", str(repo), "--roadmap", str(roadmap), "--phase", "NONEXISTENT"],
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("not found in roadmap", result.stderr)

    def test_run_returns_0_on_clean_complete(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            def fake_run_loop(**kwargs):
                from dataclasses import replace as _replace
                snapshot = provenanced_state(repo, roadmap, {"RUNNER": "complete"})
                snapshot = _replace(snapshot, terminal_summary={"terminal_status": "complete"})
                return snapshot, []

            with patch("phase_loop_runtime.cli.run_loop", side_effect=fake_run_loop), patch("phase_loop_runtime.cli.render_status", return_value="status"):
                self.assertEqual(main(["run", "--repo", str(repo), "--roadmap", str(roadmap)]), 0)

    def test_state_json_reports_hidden_ledger_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_state(repo, provenanced_state(repo, roadmap, {"RUNNER": "planned"}))
            append_event(repo, provenanced_event(repo, roadmap, "RUNNER", "planned"))

            result = subprocess.run([str(BIN), "state", "--repo", str(repo), "--roadmap", str(roadmap), "--json"], text=True, capture_output=True, check=True)
            data = json.loads(result.stdout)
            self.assertEqual(data["event_count"], 1)
            self.assertEqual(data["legacy_count"], 0)
            self.assertIn("roadmap_sha256", data)
            self.assertIn("state_path", data)
            self.assertIn("git_topology", data)
            self.assertIn("monitor_status", data)
            self.assertIn("runs_path", data)
            self.assertIn("stop_file", data)
            self.assertIn("tui_handoff_path", data)
            self.assertNotIn("secret-value", result.stdout)

    def test_monitor_notify_command_receives_blocked_payload(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td) / "repo-root")
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(repo, provenanced_event(repo, roadmap, "RUNNER", "blocked", action="execute"))
            payload = Path(td) / "payload.json"
            command = f"python3 -c \"import sys, pathlib; pathlib.Path({str(payload)!r}).write_text(sys.stdin.read())\""

            result = subprocess.run(
                [
                    str(BIN),
                    "monitor",
                    "--repo",
                    str(repo),
                    "--roadmap",
                    str(roadmap),
                    "--once",
                    "--json",
                    "--notify-command",
                    command,
                    "--notify-on",
                    "blocked",
                ],
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 1)
            data = json.loads(result.stdout)
            self.assertEqual(data["monitor_status"]["event_kind"], "blocked")
            self.assertTrue(payload.exists())
            notified = json.loads(payload.read_text())
            self.assertEqual(notified["event_kind"], "blocked")
            self.assertEqual(notified["current_phase"], "RUNNER")
            self.assertNotIn("secret-value", payload.read_text())

    def test_handoff_and_state_json_surface_regress_fixture_states(self):
        with tempfile.TemporaryDirectory() as td:
            greenfield = make_greenfield_closeout_fixture(Path(td) / "greenfield")
            append_event(
                greenfield.repo,
                provenanced_event(greenfield.repo, greenfield.roadmap, greenfield.execute_phase, "awaiting_phase_closeout", action="execute"),
            )

            handoff = subprocess.run(
                [str(BIN), "handoff", "--repo", str(greenfield.repo), "--roadmap", str(greenfield.roadmap), "--json"],
                text=True,
                capture_output=True,
                check=True,
            )
            handoff_data = json.loads(handoff.stdout)
            self.assertTrue(handoff_data["tui_handoff_exists"])
            handoff_text = Path(handoff_data["tui_handoff_path"]).read_text(encoding="utf-8")
            self.assertIn(f"Current phase: {greenfield.execute_phase}", handoff_text)
            self.assertIn("Current status: awaiting_phase_closeout", handoff_text)

            code_index = make_code_index_blocker_fixture(Path(td) / "code-index")
            append_event(
                code_index.repo,
                provenanced_event(code_index.repo, code_index.roadmap, code_index.execute_phase, "blocked", action="plan"),
            )
            state = subprocess.run(
                [str(BIN), "state", "--repo", str(code_index.repo), "--roadmap", str(code_index.roadmap), "--json"],
                text=True,
                capture_output=True,
                check=True,
            )
            state_data = json.loads(state.stdout)
            self.assertEqual(state_data["current_phase"], code_index.execute_phase)
            self.assertEqual(state_data["phases"][code_index.execute_phase], "blocked")
            self.assertNotIn("roadmap_mismatch", json.dumps(state_data))

    def test_state_json_surfaces_reentry_metadata_without_top_level_breakage(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = make_repo(root)
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            with isolated_codex_home(root) as codex_home:
                write_skill_handoff(codex_home, repo, "gemini-execute-phase", "RUNNER", "complete", plan)
                append_manual_import_event(
                    repo,
                    roadmap,
                    "RUNNER",
                    "complete",
                    harness="claude",
                    skill="claude-execute-phase",
                    artifact=plan,
                )
                state = subprocess.run(
                    [str(BIN), "state", "--repo", str(repo), "--roadmap", str(roadmap), "--json"],
                    text=True,
                    capture_output=True,
                    check=True,
                )
            data = json.loads(state.stdout)
            self.assertIn("monitor_status", data)
            self.assertEqual(data["monitor_status"]["trusted_workflow_handoff"]["originating_harness"], "gemini")
            self.assertEqual(data["monitor_status"]["latest_manual_import"]["originating_harness"], "claude")

    def test_archive_state_moves_runtime_files(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_state(repo, provenanced_state(repo, roadmap, {"RUNNER": "planned"}))
            append_event(repo, provenanced_event(repo, roadmap, "RUNNER", "planned"))
            handoff = repo / ".phase-loop" / "tui-handoff.md"
            handoff.write_text("# handoff\n")

            result = subprocess.run([str(BIN), "archive-state", "--repo", str(repo), "--reason", "fixture", "--json"], text=True, capture_output=True, check=True)
            data = json.loads(result.stdout)
            self.assertTrue(data["archived"])
            self.assertEqual(len(data["moved"]), 3)
            archive_path = Path(data["archive_path"])
            self.assertTrue((archive_path / "archive.json").exists())
            self.assertFalse((repo / ".phase-loop" / "state.json").exists())

    def test_execute_shim_accepts_pipeline_flags(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            bundle = repo / "phase-source-bundle.v1.json"
            bundle.write_text("{}")
            output = Path(td) / "closeout.json"

            result = subprocess.run(
                [
                    str(BIN),
                    "execute",
                    "RUNNER",
                    "--repo", str(repo),
                    "--roadmap", str(roadmap),
                    "--bundle", str(bundle),
                    "--output", str(output),
                    "--mode", "execute",
                    "--dry-run"
                ],
                text=True,
                capture_output=True
            )

            self.assertNotEqual(result.returncode, 2, f"Argparse rejection: {result.stderr}")
            self.assertIn("RUNNER", result.stdout + result.stderr)

    def test_execute_shim_rejects_missing_output(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            
            result = subprocess.run(
                [
                    str(BIN),
                    "execute",
                    "RUNNER",
                    "--repo", str(repo),
                    "--roadmap", str(roadmap),
                    "--mode", "execute",
                    "--dry-run"
                ],
                text=True,
                capture_output=True
            )
            
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("output", (result.stdout + result.stderr).lower())

    def test_execute_writes_closeout_json(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            output = Path(td) / "closeout.json"

            subprocess.run(
                [
                    str(BIN),
                    "execute",
                    "RUNNER",
                    "--repo", str(repo),
                    "--roadmap", str(roadmap),
                    "--output", str(output),
                    "--mode", "execute",
                    "--dry-run"
                ],
                text=True,
                capture_output=True,
                check=True
            )

            self.assertTrue(output.exists())
            data = json.loads(output.read_text())
            self.assertEqual(data["phase"], "RUNNER")
            self.assertEqual(data["schema"], "phase_loop_closeout.v1")
            self.assertIn("automation", data)
            self.assertIn("artifacts", data)

    def test_execute_handles_invalid_output_path(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            output = Path(td) / "missing_subdir" / "closeout.json"

            subprocess.run(
                [
                    str(BIN),
                    "execute",
                    "RUNNER",
                    "--repo", str(repo),
                    "--roadmap", str(roadmap),
                    "--output", str(output),
                    "--mode", "execute",
                    "--dry-run"
                ],
                text=True,
                capture_output=True,
                check=True
            )

            self.assertTrue(output.exists())
            self.assertTrue(output.parent.exists())

    def test_execute_fails_closed_on_invalid_phase(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            output = Path(td) / "closeout.json"

            subprocess.run(
                [
                    str(BIN),
                    "execute",
                    "NON_EXISTENT_PHASE",
                    "--repo", str(repo),
                    "--roadmap", str(roadmap),
                    "--output", str(output),
                    "--mode", "execute",
                    "--dry-run"
                ],
                text=True,
                capture_output=True
            )

            data = json.loads(output.read_text())
            self.assertEqual(data["phase"], "NON_EXISTENT_PHASE")
            self.assertEqual(data["terminal_status"], "blocked")


if __name__ == "__main__":
    unittest.main()
