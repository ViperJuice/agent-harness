import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from phase_loop_test_utils import commit_fixture_paths, make_repo, write_phase_plan
from phase_loop_runtime.cli import main
from phase_loop_runtime.events import read_events
from phase_loop_runtime.verification_evidence import run_verification


def _run(argv: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


class ReconcileVerificationLogTest(unittest.TestCase):
    def _args(self, repo: Path, roadmap: Path, phase: str, *extra: str) -> list[str]:
        return ["reconcile", "--repo", str(repo), "--roadmap", str(roadmap), "--phase", phase, *extra]

    def test_rg_passed_reconcile_requires_verification_log(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            code, _, stderr = _run(self._args(repo, roadmap, "RG", "--verification-status", "passed"))

            self.assertEqual(code, 2)
            self.assertIn("missing_verification_log", stderr)

    def test_plan_declaring_rg_contract_requires_verification_log(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, body="# RUNNER\n\n**Interfaces provided**: IF-0-RG-1\n")
            commit_fixture_paths(repo, "add runner plan", plan)

            code, _, stderr = _run(self._args(repo, roadmap, "RUNNER", "--verification-status", "passed"))

            self.assertEqual(code, 2)
            self.assertIn("missing_verification_log", stderr)

    def test_valid_verification_json_records_redacted_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            run_dir = repo / ".phase-loop/runs/test-run"
            run_verification(repo, run_dir, [[sys.executable, "-c", "print('secret-looking log text')"]], None, None, 5)

            code, _, stderr = _run(
                self._args(
                    repo,
                    roadmap,
                    "RUNNER",
                    "--verification-status",
                    "passed",
                    "--verification-log",
                    str(run_dir / "verification.json"),
                    "--allow-dirty",
                )
            )

            self.assertEqual(code, 0, stderr)
            evidence = read_events(repo)[-1]["metadata"]["manual_repair"]["verification_evidence"]
            self.assertEqual(evidence["code"], "ok")
            self.assertEqual(evidence["exit_summary"]["commands"], [0])
            self.assertNotIn("secret-looking log text", str(evidence))

    def test_valid_verification_log_path_resolves_to_sibling_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            run_dir = repo / ".phase-loop/runs/test-run"
            run_verification(repo, run_dir, [[sys.executable, "-c", "print('ok')"]], None, None, 5)

            code, _, stderr = _run(
                self._args(
                    repo,
                    roadmap,
                    "RUNNER",
                    "--verification-status",
                    "passed",
                    "--verification-log",
                    str(run_dir / "verification.log"),
                    "--allow-dirty",
                )
            )

            self.assertEqual(code, 0, stderr)
            evidence = read_events(repo)[-1]["metadata"]["manual_repair"]["verification_evidence"]
            self.assertEqual(evidence["code"], "ok")
            self.assertEqual(Path(evidence["artifact_path"]).name, "verification.json")

    def test_reconcile_rejects_nonzero_or_tampered_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            nonzero_dir = repo / ".phase-loop/runs/nonzero"
            run_verification(repo, nonzero_dir, [[sys.executable, "-c", "raise SystemExit(9)"]], None, None, 5)

            code, _, stderr = _run(
                self._args(
                    repo,
                    roadmap,
                    "RUNNER",
                    "--verification-status",
                    "passed",
                    "--verification-log",
                    str(nonzero_dir / "verification.json"),
                    "--allow-dirty",
                )
            )
            self.assertEqual(code, 2)
            self.assertIn("nonzero_exit", stderr)

            tampered_dir = repo / ".phase-loop/runs/tampered"
            run_verification(repo, tampered_dir, [[sys.executable, "-c", "print('ok')"]], None, None, 5)
            (tampered_dir / "verification.log").write_text("tampered", encoding="utf-8")

            code, _, stderr = _run(
                self._args(
                    repo,
                    roadmap,
                    "RUNNER",
                    "--verification-status",
                    "passed",
                    "--verification-log",
                    str(tampered_dir / "verification.json"),
                    "--allow-dirty",
                )
            )
            self.assertEqual(code, 2)
            self.assertIn("log_sha256_mismatch", stderr)


if __name__ == "__main__":
    unittest.main()
