import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.closeout import build_phase_loop_closeout, phase_loop_closeout_diagnostic
from phase_loop_runtime.verification_evidence import run_verification


class CloseoutVerificationGateTest(unittest.TestCase):
    def test_artifact_backed_pass_is_accepted_and_records_agent_report(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            plan = self._plan(repo)
            run_dir = repo / ".phase-loop/runs/test-run"
            run_verification(repo, run_dir, [[sys.executable, "-c", "print('ok')"]], None, None, 5)

            closeout = self._closeout(plan, run_dir)

            self.assertEqual(closeout["terminal_status"], "complete")
            self.assertEqual(closeout["verification"]["status"], "passed")
            self.assertEqual(closeout["verification"]["agent_reported_verification_status"], "passed")
            self.assertEqual(closeout["verification"]["results"][0]["code"], "ok")

    def test_missing_artifact_blocks_passed_closeout_in_hard_mode(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            plan = self._plan(repo)
            run_dir = repo / ".phase-loop/runs/test-run"
            run_dir.mkdir(parents=True)

            closeout = self._closeout(plan, run_dir)

            self.assertEqual(closeout["terminal_status"], "blocked")
            self.assertEqual(closeout["verification"]["status"], "blocked")
            self.assertEqual(closeout["blocker"]["blocker_class"], "verification_evidence_missing")
            self.assertEqual(closeout["verification"]["results"][0]["code"], "malformed_artifact")

    def test_rg_passed_closeout_without_artifact_path_blocks_in_hard_mode(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            plan = self._plan(repo)

            closeout = build_phase_loop_closeout(
                phase_alias="RG",
                plan_path=plan,
                terminal_summary={"terminal_status": "complete", "verification_status": "passed"},
                automation={"status": "complete", "verification_status": "passed", "human_required": False},
            )

            self.assertEqual(closeout["terminal_status"], "blocked")
            self.assertEqual(closeout["verification"]["status"], "blocked")
            self.assertEqual(closeout["blocker"]["blocker_class"], "verification_evidence_missing")
            self.assertEqual(closeout["verification"]["results"][0]["code"], "missing_verification_artifact")

    def test_legacy_non_rg_passed_closeout_without_artifact_path_remains_compatible(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            plan = repo / "plans/phase-plan-v1-LEGACY.md"
            plan.parent.mkdir(parents=True, exist_ok=True)
            plan.write_text("# LEGACY\n", encoding="utf-8")

            closeout = build_phase_loop_closeout(
                phase_alias="LEGACY",
                plan_path=plan,
                terminal_summary={"terminal_status": "complete", "verification_status": "passed"},
                automation={"status": "complete", "verification_status": "passed", "human_required": False},
            )

            self.assertEqual(closeout["terminal_status"], "complete")
            self.assertEqual(closeout["verification"]["status"], "passed")
            self.assertEqual(closeout["verification"]["results"], [])

    def test_declared_rg_contract_requires_artifact_path_for_non_rg_phase(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            plan = repo / "plans/phase-plan-v1-AUDIT.md"
            plan.parent.mkdir(parents=True, exist_ok=True)
            plan.write_text("**Interfaces provided**: IF-0-RG-1\n", encoding="utf-8")

            closeout = build_phase_loop_closeout(
                phase_alias="AUDIT",
                plan_path=plan,
                terminal_summary={"terminal_status": "complete", "verification_status": "passed"},
                automation={"status": "complete", "verification_status": "passed", "human_required": False},
            )

            self.assertEqual(closeout["terminal_status"], "blocked")
            self.assertEqual(closeout["verification"]["results"][0]["code"], "missing_verification_artifact")

    def test_warn_mode_records_warning_without_blocking_closeout(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            plan = self._plan(repo)
            run_dir = repo / ".phase-loop/runs/test-run"
            run_dir.mkdir(parents=True)

            with patch.dict(os.environ, {"PHASE_LOOP_VERIFY_ENFORCE": "warn"}):
                closeout = self._closeout(plan, run_dir)

            self.assertEqual(closeout["terminal_status"], "complete")
            self.assertEqual(closeout["verification"]["status"], "passed")
            self.assertEqual(closeout["verification"]["results"][0]["enforcement"], "warn")
            self.assertIn("warning", closeout["verification"]["results"][0])

    def test_nonzero_artifact_blocks_passed_closeout(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            plan = self._plan(repo)
            run_dir = repo / ".phase-loop/runs/test-run"
            run_verification(repo, run_dir, [[sys.executable, "-c", "raise SystemExit(7)"]], None, None, 5)

            closeout = self._closeout(plan, run_dir)

            self.assertEqual(closeout["verification"]["status"], "blocked")
            self.assertEqual(closeout["verification"]["results"][0]["code"], "nonzero_exit")

    def test_malformed_artifact_blocks_passed_closeout(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            plan = self._plan(repo)
            run_dir = repo / ".phase-loop/runs/test-run"
            run_dir.mkdir(parents=True)
            (run_dir / "verification.json").write_text('{"schema_version": 1}', encoding="utf-8")
            (run_dir / "verification.log").write_text("log", encoding="utf-8")

            closeout = self._closeout(plan, run_dir)

            self.assertEqual(closeout["verification"]["status"], "blocked")
            self.assertEqual(closeout["verification"]["results"][0]["code"], "malformed_artifact")

    def test_tampered_log_hash_blocks_passed_closeout(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            plan = self._plan(repo)
            run_dir = repo / ".phase-loop/runs/test-run"
            run_verification(repo, run_dir, [[sys.executable, "-c", "print('ok')"]], None, None, 5)
            (run_dir / "verification.log").write_text("tampered", encoding="utf-8")

            closeout = self._closeout(plan, run_dir)

            self.assertEqual(closeout["verification"]["status"], "blocked")
            self.assertEqual(closeout["verification"]["results"][0]["code"], "log_sha256_mismatch")

    def test_closeout_diagnostic_with_secret_is_redacted_to_metadata_only(self):
        # agent-harness#243: a failing stage that dumps a secret-shaped value into
        # verification.log must NOT surface that secret through the persisted closeout record.
        # The diagnostic is redacted to metadata-only when it enters the record.
        import json as _json

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            plan = self._plan(repo)
            run_dir = repo / ".phase-loop/runs/test-run"
            run_verification(
                repo,
                run_dir,
                [[sys.executable, "-c",
                  "import sys; print(\"api_key='AKIAIOSFODNN7EXAMPLEKEY'\"); sys.exit(1)"]],
                None,
                None,
                5,
            )

            closeout = self._closeout(plan, run_dir)

            self.assertEqual(closeout["verification"]["status"], "blocked")  # nonzero still blocks
            result = closeout["verification"]["results"][0]
            self.assertEqual(result["code"], "nonzero_exit")
            diag = result["diagnostics"][0]
            self.assertTrue(diag["redacted"])
            self.assertEqual(diag["diagnostic_status"], "redacted")
            self.assertNotIn("raw_tail", diag)
            # The secret must not appear anywhere in the serialized closeout record.
            self.assertNotIn("AKIAIOSFODNN7EXAMPLEKEY", _json.dumps(closeout))

    def test_closeout_diagnostic_with_double_quoted_secret_is_redacted_to_metadata_only(self):
        # agent-harness#243 CR (defect 1): a DOUBLE-quoted secret must be redacted too. The
        # pre-fix matcher ran against a json.dumps(...) blob, which backslash-escapes an
        # embedded double quote and broke the secret_like_value pattern (single-quoted secrets
        # were unaffected -- see test_closeout_diagnostic_with_secret_is_redacted_to_metadata_only
        # above -- which is why this case slipped through).
        import json as _json

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            plan = self._plan(repo)
            run_dir = repo / ".phase-loop/runs/test-run"
            run_verification(
                repo,
                run_dir,
                [[sys.executable, "-c",
                  'import sys; print("api_key=\\"AKIAIOSFODNN7EXAMPLEKEY\\""); sys.exit(1)']],
                None,
                None,
                5,
            )

            closeout = self._closeout(plan, run_dir)

            self.assertEqual(closeout["verification"]["status"], "blocked")  # nonzero still blocks
            result = closeout["verification"]["results"][0]
            self.assertEqual(result["code"], "nonzero_exit")
            diag = result["diagnostics"][0]
            self.assertTrue(diag["redacted"])
            self.assertEqual(diag["diagnostic_status"], "redacted")
            self.assertNotIn("raw_tail", diag)
            # The secret must not appear anywhere in the serialized closeout record.
            self.assertNotIn("AKIAIOSFODNN7EXAMPLEKEY", _json.dumps(closeout))

    def test_closeout_diagnostic_with_split_argv_secret_is_redacted_to_metadata_only(self):
        # agent-harness#243 CR (round 2, cross-vendor, defect 3): a secret split across TWO
        # adjacent argv elements (a real subprocess invocation shape -- e.g. `tool --token
        # SECRET` -- rather than a single pre-joined string) must be caught end-to-end through
        # the closeout path, not just at the redaction-helper level. Before the fix, examining
        # each argv leaf in isolation never saw the "--token" flag and the secret value
        # contiguous, so `secret_like_value` never matched and the raw secret argv leaked
        # straight into the persisted closeout record.
        import json as _json

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            plan = self._plan(repo)
            run_dir = repo / ".phase-loop/runs/test-run"
            run_verification(
                repo,
                run_dir,
                [[sys.executable, "-c", "import sys; sys.exit(1)", "--token", "AKIAIOSFODNN7EXAMPLEKEY"]],
                None,
                None,
                5,
            )

            closeout = self._closeout(plan, run_dir)

            self.assertEqual(closeout["verification"]["status"], "blocked")  # nonzero still blocks
            result = closeout["verification"]["results"][0]
            self.assertEqual(result["code"], "nonzero_exit")
            diag = result["diagnostics"][0]
            self.assertTrue(diag["redacted"])
            self.assertEqual(diag["diagnostic_status"], "redacted")
            self.assertNotIn("argv", diag)
            # The secret must not appear anywhere in the serialized closeout record.
            self.assertNotIn("AKIAIOSFODNN7EXAMPLEKEY", _json.dumps(closeout))

    def test_closeout_diagnostic_with_json_struct_secret_is_redacted_to_metadata_only(self):
        # agent-harness#243 CR round 4 (codex + Fable): a failing command that PRINTS ordinary
        # JSON credentials (e.g. ``print(json.dumps({"api_key": "SECRET"}))``) is captured
        # verbatim into raw_tail as literal JSON text. The closing quote on the JSON key sits
        # between the keyword and the ``:`` separator, breaking the keyword->separator->value
        # adjacency the matcher required -- so this JSON-formatted secret bypassed BOTH the
        # redaction path and the fatal metadata gate end-to-end through the real closeout path.
        import json as _json

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            plan = self._plan(repo)
            run_dir = repo / ".phase-loop/runs/test-run"
            run_verification(
                repo,
                run_dir,
                [[sys.executable, "-c",
                  "import json, sys; print(json.dumps({'api_key': 'AKIAIOSFODNN7EXAMPLEKEY'})); sys.exit(1)"]],
                None,
                None,
                5,
            )

            closeout = self._closeout(plan, run_dir)

            self.assertEqual(closeout["verification"]["status"], "blocked")  # nonzero still blocks
            result = closeout["verification"]["results"][0]
            self.assertEqual(result["code"], "nonzero_exit")
            diag = result["diagnostics"][0]
            self.assertTrue(diag["redacted"])
            self.assertEqual(diag["diagnostic_status"], "redacted")
            self.assertNotIn("raw_tail", diag)
            # The secret must not appear anywhere in the serialized closeout record.
            self.assertNotIn("AKIAIOSFODNN7EXAMPLEKEY", _json.dumps(closeout))

    def test_closeout_diagnostic_with_nested_json_struct_secret_is_redacted_to_metadata_only(self):
        # agent-harness#243 CR round 4: the same JSON-struct blind spot, one level deeper --
        # a secret nested inside another JSON object, printed verbatim by a failing command.
        import json as _json

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            plan = self._plan(repo)
            run_dir = repo / ".phase-loop/runs/test-run"
            run_verification(
                repo,
                run_dir,
                [[sys.executable, "-c",
                  "import json, sys; print(json.dumps({'outer': {'token': 'AKIAIOSFODNN7EXAMPLEKEY'}})); sys.exit(1)"]],
                None,
                None,
                5,
            )

            closeout = self._closeout(plan, run_dir)

            self.assertEqual(closeout["verification"]["status"], "blocked")
            result = closeout["verification"]["results"][0]
            self.assertEqual(result["code"], "nonzero_exit")
            diag = result["diagnostics"][0]
            self.assertTrue(diag["redacted"])
            self.assertNotIn("raw_tail", diag)
            self.assertNotIn("AKIAIOSFODNN7EXAMPLEKEY", _json.dumps(closeout))

    def test_closeout_diagnostic_with_json_struct_password_is_redacted_to_metadata_only(self):
        # agent-harness#243 CR round 4: same JSON-struct blind spot with the "password" keyword.
        import json as _json

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            plan = self._plan(repo)
            run_dir = repo / ".phase-loop/runs/test-run"
            run_verification(
                repo,
                run_dir,
                [[sys.executable, "-c",
                  "import json, sys; print(json.dumps({'password': 'AKIAIOSFODNN7EXAMPLEKEY'})); sys.exit(1)"]],
                None,
                None,
                5,
            )

            closeout = self._closeout(plan, run_dir)

            self.assertEqual(closeout["verification"]["status"], "blocked")
            result = closeout["verification"]["results"][0]
            self.assertEqual(result["code"], "nonzero_exit")
            diag = result["diagnostics"][0]
            self.assertTrue(diag["redacted"])
            self.assertNotIn("raw_tail", diag)
            self.assertNotIn("AKIAIOSFODNN7EXAMPLEKEY", _json.dumps(closeout))

    def test_closeout_with_benign_prose_blocker_summary_is_not_malformed(self):
        # agent-harness#243 CR (cross-vendor codex, REGRESSION): the round that widened
        # secret_like_value's separator to accept bare whitespace made the FATAL
        # metadata_redaction_diagnostic gate (invoked here via phase_loop_closeout_diagnostic)
        # reject a legitimate human-authored blocker_summary that happens to contain a secret
        # keyword followed by whitespace and 12+ alnum chars -- e.g. "review the token
        # configuration" or "the password authentication documentation" -- turning an
        # ordinary blocked closeout into malformed_closeout and preventing
        # persistence/reconciliation. MUST FAIL at HEAD bec790f (diagnostic is non-None) and
        # pass once the separator is strict again.
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            plan = self._plan(repo)
            run_dir = repo / ".phase-loop/runs/test-run"
            run_verification(repo, run_dir, [[sys.executable, "-c", "print('ok')"]], None, None, 5)

            closeout = build_phase_loop_closeout(
                phase_alias="RG",
                plan_path=plan,
                terminal_summary={
                    "terminal_status": "blocked",
                    "verification_status": "passed",
                    "artifact_paths": {"root": str(run_dir)},
                },
                automation={
                    "status": "blocked",
                    "verification_status": "passed",
                    "human_required": True,
                    "blocker_class": "admin_approval",
                    "blocker_summary": (
                        "Next action: review the token configuration and the password "
                        "authentication documentation before re-running the suite; see the "
                        "secret management guide for the rotation policy."
                    ),
                },
            )

            self.assertEqual(closeout["terminal_status"], "human_required")
            self.assertIn("token configuration", closeout["blocker"]["blocker_summary"])
            diagnostic = phase_loop_closeout_diagnostic(closeout)
            self.assertIsNone(diagnostic, diagnostic)

    def test_closeout_force_all_redaction_suppresses_benign_tail(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            plan = self._plan(repo)
            run_dir = repo / ".phase-loop/runs/test-run"
            run_verification(
                repo, run_dir,
                [[sys.executable, "-c", "print('benign failing output'); raise SystemExit(1)"]],
                None, None, 5,
            )
            with patch.dict(os.environ, {"PHASE_LOOP_VERIFY_REDACT_DIAGNOSTICS": "all"}):
                closeout = self._closeout(plan, run_dir)
            diag = closeout["verification"]["results"][0]["diagnostics"][0]
            self.assertTrue(diag["redacted"])
            self.assertEqual(diag["redaction_reason"], "operator_forced")

    def _plan(self, repo: Path) -> Path:
        plan = repo / "plans/phase-plan-v1-RG.md"
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text("# RG\n", encoding="utf-8")
        return plan

    def _closeout(self, plan: Path, run_dir: Path) -> dict:
        return build_phase_loop_closeout(
            phase_alias="RG",
            plan_path=plan,
            terminal_summary={
                "terminal_status": "complete",
                "verification_status": "passed",
                "artifact_paths": {"root": str(run_dir)},
            },
            automation={"status": "complete", "verification_status": "passed", "human_required": False},
        )


if __name__ == "__main__":
    unittest.main()
