import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.closeout import build_phase_loop_closeout
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
