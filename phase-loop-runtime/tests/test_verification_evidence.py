import json
import sys
import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.verification_evidence import (
    append_evidence_entry,
    load_verification_artifact,
    run_verification,
    validate_verification_artifact,
    validate_verification_commands,
)


class VerificationEvidenceTest(unittest.TestCase):
    def test_all_pass_commands_write_artifact_and_log(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / ".phase-loop").mkdir()
            (repo / ".phase-loop/state.json").write_text('{"current_phase": "VC"}', encoding="utf-8")
            run_dir = repo / ".phase-loop/runs/test-run"

            result = run_verification(
                repo,
                run_dir,
                [[sys.executable, "-c", "print('first')"], [sys.executable, "-c", "print('second')"]],
                None,
                None,
                5,
            )

            artifact_path = run_dir / "verification.json"
            log_path = run_dir / "verification.log"
            self.assertTrue(artifact_path.exists())
            self.assertTrue(log_path.exists())
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 1)
            self.assertEqual(payload["run_id"], "test-run")
            self.assertEqual(payload["phase_alias"], "VC")
            self.assertEqual(payload["env_refresh"], None)
            self.assertEqual(payload["suite"], None)
            self.assertEqual(len(payload["commands"]), 2)
            self.assertEqual([item["exit_code"] for item in payload["commands"]], [0, 0])
            self.assertGreaterEqual(payload["commands"][1]["log_offset"], payload["commands"][0]["log_offset"])
            self.assertTrue(payload["started_at"])
            self.assertTrue(payload["finished_at"])
            self.assertEqual(payload["log_sha256"], result.log_sha256)
            self.assertIn("first", log_path.read_text(encoding="utf-8"))
            self.assertIn("second", log_path.read_text(encoding="utf-8"))

    def test_failing_command_is_recorded_as_data(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            run_dir = repo / ".phase-loop/runs/test-run"

            result = run_verification(repo, run_dir, [[sys.executable, "-c", "raise SystemExit(7)"]], None, None, 5)

            self.assertEqual(result.commands[0].exit_code, 7)
            payload = json.loads((run_dir / "verification.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["commands"][0]["exit_code"], 7)

    def test_validator_reports_empty_missing_executable_and_path_findings(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "exists.txt").write_text("ok", encoding="utf-8")

            findings = validate_verification_commands(
                repo,
                [
                    [],
                    ["definitely-not-a-phase-loop-command"],
                    [sys.executable, "missing/file.txt"],
                    [sys.executable, "--cwd=../outside"],
                    [sys.executable, "exists.txt"],
                ],
            )

            self.assertEqual(
                [finding.code for finding in findings],
                ["empty_argv", "unresolved_executable", "missing_path", "outside_repo_path"],
            )

    def test_suite_timeout_is_recorded_as_failed_suite_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            run_dir = repo / ".phase-loop/runs/test-run"

            result = run_verification(
                repo,
                run_dir,
                [],
                [sys.executable, "-c", "import time; time.sleep(2)"],
                None,
                0.1,
            )

            self.assertIsNotNone(result.suite)
            self.assertEqual(result.suite.exit_code, 124)
            self.assertIn("timed out", (run_dir / "verification.log").read_text(encoding="utf-8"))

    def test_load_verification_artifact_rejects_malformed_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            artifact = Path(td) / "verification.json"
            artifact.write_text('{"schema_version": 1}', encoding="utf-8")

            with self.assertRaises(ValueError):
                load_verification_artifact(artifact)

    def test_validate_verification_artifact_checks_hash_and_exit_codes(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            run_dir = repo / ".phase-loop/runs/test-run"
            run_verification(repo, run_dir, [[sys.executable, "-c", "print('ok')"]], None, None, 5)

            validation = validate_verification_artifact(run_dir / "verification.json")

            self.assertTrue(validation.ok)
            self.assertEqual(validation.code, "ok")
            self.assertEqual(validation.exit_summary["commands"], [0])

            (run_dir / "verification.log").write_text("tampered", encoding="utf-8")
            tampered = validate_verification_artifact(run_dir / "verification.json")
            self.assertFalse(tampered.ok)
            self.assertEqual(tampered.code, "log_sha256_mismatch")

    def test_validate_verification_artifact_reports_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            run_dir = repo / ".phase-loop/runs/test-run"
            run_verification(repo, run_dir, [[sys.executable, "-c", "raise SystemExit(9)"]], None, None, 5)

            validation = validate_verification_artifact(run_dir / "verification.json")

            self.assertFalse(validation.ok)
            self.assertEqual(validation.code, "nonzero_exit")
            self.assertEqual(validation.exit_summary["commands"], [9])

    def test_append_evidence_entry_preserves_existing_bytes_and_appends_json_line(self):
        with tempfile.TemporaryDirectory() as td:
            doc = Path(td) / "evidence.md"
            doc.write_bytes(b"existing evidence")

            payload = append_evidence_entry(doc, {"kind": "operator_check", "status": "passed"})

            data = doc.read_bytes()
            self.assertTrue(data.startswith(b"existing evidence\n"))
            appended = json.loads(data.splitlines()[-1])
            self.assertEqual(appended["entry"], {"kind": "operator_check", "status": "passed"})
            self.assertEqual(payload["entry"]["kind"], "operator_check")


if __name__ == "__main__":
    unittest.main()
