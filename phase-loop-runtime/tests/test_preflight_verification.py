import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.discovery import (
    resolve_suite_command,
    resolve_suite_command_doc,
    validate_plan_verification_commands_for_intake,
    verification_commands_from_plan,
)
from phase_loop_runtime.events import read_events
from phase_loop_runtime.launcher import LaunchResult
from phase_loop_runtime.runner import run_loop
from phase_loop_runtime.verification_evidence import (
    detect_changed_dependency_manifests,
    resolve_install_command,
    run_verification,
    validate_verification_artifact,
)
from phase_loop_test_utils import build_fake_automation_output, commit_fixture_paths, make_repo, write_phase_plan


class PreflightVerificationTest(unittest.TestCase):
    def test_suite_command_prefers_plan_frontmatter_and_ignores_body_automation(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.write_text(
                "---\n"
                "automation:\n"
                f"  suite_command: [{sys.executable!r}, -c, 'print(\"roadmap\")']\n"
                "---\n"
                "# Roadmap\n\n"
                "### Phase 0 - Runner (RUNNER)\n",
                encoding="utf-8",
            )
            plan = write_phase_plan(
                repo,
                "RUNNER",
                roadmap,
                body=(
                    "# RUNNER\n\n"
                    "## Verification\n"
                    f"- `{sys.executable} -c \"print('verify')\"`\n\n"
                    "automation:\n"
                    "  suite_command: definitely ignored\n"
                ),
                extra_frontmatter={"automation": ""},
            )
            text = plan.read_text(encoding="utf-8")
            text = text.replace("automation: \n", f"automation:\n  suite_command: [{sys.executable!r}, -c, 'print(\"plan\")']\n")
            plan.write_text(text, encoding="utf-8")

            command = resolve_suite_command(repo, roadmap, plan)

            self.assertEqual(command, [sys.executable, "-c", 'print("plan")'])

    def test_malformed_suite_command_returns_structured_finding(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.write_text(
                "---\n"
                "automation:\n"
                "  suite_command: [python, 7]\n"
                "---\n"
                "# Roadmap\n\n"
                "### Phase 0 - Runner (RUNNER)\n",
                encoding="utf-8",
            )
            plan = write_phase_plan(repo, "RUNNER", roadmap)

            command, findings = resolve_suite_command_doc(repo, roadmap, plan)

            self.assertIsNone(command)
            self.assertEqual(findings[0].code, "malformed_suite_command")

    def test_dependency_manifest_change_resolves_install_and_failure_blocks_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            (repo / "pyproject.toml").write_text("[project]\nname = 'fixture'\n", encoding="utf-8")
            commit_fixture_paths(repo, "add pyproject", repo / "pyproject.toml")
            (repo / "pyproject.toml").write_text("[project]\nname = 'changed'\n", encoding="utf-8")

            manifests = detect_changed_dependency_manifests(repo, "HEAD")
            command = resolve_install_command(repo, manifests)
            run_dir = repo / ".phase-loop/runs/preflight"
            run_verification(
                repo,
                run_dir,
                [],
                None,
                {"triggered": True, "manifests": manifests, "install_argv": command or [], "exit_code": 9},
                5,
            )
            validation = validate_verification_artifact(run_dir / "verification.json")

            self.assertEqual(manifests, ["pyproject.toml"])
            self.assertIsNotNone(command)
            self.assertFalse(validation.ok)
            self.assertEqual(validation.exit_summary["env_refresh"], 9)

    def test_operational_evidence_is_recorded_but_not_executed(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(
                repo,
                "RUNNER",
                roadmap,
                body=(
                    "# RUNNER\n\n"
                    "## Verification\n"
                    f"- `{sys.executable} -c \"print('machine')\"`\n"
                    "- `definitely-not-executed-operational-command` evidence: operational\n"
                ),
            )

            commands, operational = verification_commands_from_plan(plan)
            run_dir = repo / ".phase-loop/runs/operational"
            run_verification(repo, run_dir, commands, None, None, 5, operational_exemptions=operational)
            payload = json.loads((run_dir / "verification.json").read_text(encoding="utf-8"))

            self.assertEqual(len(payload["commands"]), 1)
            self.assertEqual(payload["commands"][0]["exit_code"], 0)
            self.assertEqual(payload["operational_exemptions"][0]["reason"], "evidence: operational")
            self.assertNotIn("definitely-not-executed", (run_dir / "verification.log").read_text(encoding="utf-8"))

    def test_execute_launch_writes_runner_verification_metadata_before_reduction(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.write_text(
                "---\n"
                "automation:\n"
                f"  suite_command: [{sys.executable!r}, -c, 'print(\"suite\")']\n"
                "---\n"
                "# Roadmap\n\n"
                "### Phase 0 - Runner (RUNNER)\n",
                encoding="utf-8",
            )
            plan = write_phase_plan(
                repo,
                "RUNNER",
                roadmap,
                body=(
                    "# RUNNER\n\n"
                    "## Lanes\n\n"
                    "### SL-0 - Runner\n"
                    "- **Owned files**: `README.md`\n\n"
                    "## Verification\n"
                    f"- `{sys.executable} -c \"print('verify')\"`\n"
                ),
            )
            commit_fixture_paths(repo, "add plan", roadmap, plan)

            output = build_fake_automation_output(status="complete", verification_status="passed")

            with patch.dict(os.environ, {"PHASE_LOOP_VERIFY_ENFORCE": "hard"}), patch(
                "phase_loop_runtime.runner.launch_with_spec",
                return_value=LaunchResult(command=["codex", "exec"], returncode=0, output=output, executor="codex"),
            ):
                snapshot, _results = run_loop(repo, roadmap, phase="RUNNER", executor="codex")

            self.assertEqual(snapshot.phases["RUNNER"], "complete")
            event = read_events(repo)[-1]
            verification = event["metadata"]["child_automation"]["runner_verification"]
            self.assertTrue(verification["ok"])
            self.assertTrue(Path(verification["verification_artifact_path"]).exists())
            self.assertTrue(Path(verification["verification_log_path"]).exists())
            self.assertEqual(verification["verification_exit_summary"]["commands"], [0])
            self.assertEqual(verification["verification_exit_summary"]["suite"], 0)

    def test_hard_mode_missing_suite_blocks_before_execute_launch(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(
                repo,
                "RUNNER",
                roadmap,
                body="# RUNNER\n\n## Verification\n" f"- `{sys.executable} -c \"print('verify')\"`\n",
            )
            commit_fixture_paths(repo, "add plan", plan)

            with patch.dict(os.environ, {"PHASE_LOOP_VERIFY_ENFORCE": "hard"}), patch(
                "phase_loop_runtime.runner.launch_with_spec"
            ) as fake_launch:
                snapshot, results = run_loop(repo, roadmap, phase="RUNNER", executor="codex")

            fake_launch.assert_not_called()
            self.assertEqual(results, [])
            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            self.assertEqual(snapshot.blocker_class, "verification_evidence_missing")

    def test_bogus_verification_command_is_rejected_at_intake(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(
                repo,
                "RUNNER",
                roadmap,
                body="# RUNNER\n\n## Verification\n- `definitely-not-a-real-verifier`\n",
            )

            findings = validate_plan_verification_commands_for_intake(repo, plan)

            self.assertEqual(findings[0].code, "unresolved_executable")


if __name__ == "__main__":
    unittest.main()
