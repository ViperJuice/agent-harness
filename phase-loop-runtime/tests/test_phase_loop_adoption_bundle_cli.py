import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.adoption_bundle import adoption_bundle_status, refresh_adoption_bundle
from phase_loop_runtime.cli import build_parser, main


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
HOOK = ROOT / ".githooks" / "pre-commit-adoption-bundle"


class PhaseLoopAdoptionBundleCliTest(unittest.TestCase):
    def test_parser_recognizes_status_and_refresh(self):
        parser = build_parser()

        status = parser.parse_args(["adoption-bundle", "status", "--repo", "."])
        refresh = parser.parse_args(["adoption-bundle", "refresh", "--repo", "."])

        self.assertEqual(status.command, "adoption-bundle")
        self.assertEqual(status.adoption_bundle_action, "status")
        self.assertEqual(refresh.adoption_bundle_action, "refresh")

    def test_status_json_reports_fresh_bundle_and_exit_zero(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._bundle_repo(Path(td))
            output = StringIO()

            with redirect_stdout(output):
                code = main(["adoption-bundle", "status", "--repo", str(repo), "--json"])

            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(payload["status"], "fresh")
            self.assertEqual(payload["bundle"], "docs/adoption/dotfiles-adoption-bundle.json")
            self.assertEqual(payload["stale_refs"], [])

    def test_status_reports_stale_digest_and_exit_one(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._bundle_repo(Path(td))
            baml = repo / "vendor" / "phase-loop-runtime" / "src" / "phase_loop_runtime" / "baml_src" / "emit_phase_closeout.baml"
            baml.write_text(baml.read_text(encoding="utf-8") + "\n// stale\n", encoding="utf-8")

            output = StringIO()
            with redirect_stdout(output):
                code = main(["adoption-bundle", "status", "--repo", str(repo), "--json"])

            payload = json.loads(output.getvalue())
            self.assertEqual(code, 1)
            self.assertEqual(payload["status"], "stale")
            self.assertTrue(payload["stale_refs"])

    def test_status_setup_error_exits_two(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._bundle_repo(Path(td))
            (repo / "docs" / "adoption" / "dotfiles-adoption-bundle.json").unlink()

            output = StringIO()
            with redirect_stdout(output):
                code = main(["adoption-bundle", "status", "--repo", str(repo), "--json"])

            payload = json.loads(output.getvalue())
            self.assertEqual(code, 2)
            self.assertEqual(payload["status"], "error")
            self.assertEqual(payload["bundle"], "docs/adoption/dotfiles-adoption-bundle.json")

    def test_refresh_is_idempotent_when_bundle_is_current(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._bundle_repo(Path(td))
            before = (repo / "docs" / "adoption" / "dotfiles-adoption-bundle.json").read_bytes()

            result = refresh_adoption_bundle(repo)

            self.assertFalse(result["refreshed"])
            self.assertEqual((repo / "docs" / "adoption" / "dotfiles-adoption-bundle.json").read_bytes(), before)

    def test_refresh_preserves_committed_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._bundle_repo(Path(td))
            bundle_path = repo / "docs" / "adoption" / "dotfiles-adoption-bundle.json"
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            generated_at = bundle["generated_at"]
            operating_mode = bundle["operating_mode"]
            baml = repo / "vendor" / "phase-loop-runtime" / "src" / "phase_loop_runtime" / "baml_src" / "emit_phase_closeout.baml"
            baml.write_text(baml.read_text(encoding="utf-8") + "\n// refresh\n", encoding="utf-8")

            result = refresh_adoption_bundle(repo)
            refreshed = json.loads(bundle_path.read_text(encoding="utf-8"))

            self.assertTrue(result["refreshed"])
            self.assertEqual(refreshed["generated_at"], generated_at)
            self.assertEqual(refreshed["operating_mode"], operating_mode)
            self.assertEqual(adoption_bundle_status(repo)["status"], "fresh")

    def test_refresh_json_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._bundle_repo(Path(td))
            output = StringIO()

            with redirect_stdout(output):
                code = main(["adoption-bundle", "refresh", "--repo", str(repo), "--json"])

            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(payload["status"], "fresh")
            self.assertIn("refreshed", payload)

    def test_hook_noops_without_staged_baml_changes(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._hook_repo(Path(td))

            result = subprocess.run([str(repo / ".githooks" / "pre-commit-adoption-bundle")], cwd=repo, text=True, capture_output=True)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")

    def test_hook_refreshes_and_stages_stale_bundle_for_staged_baml(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._hook_repo(Path(td))
            log = repo / "phase-loop.log"
            self._write_fake_phase_loop(repo, log)
            baml = repo / "vendor" / "phase-loop-runtime" / "src" / "phase_loop_runtime" / "baml_src" / "emit_phase_closeout.baml"
            baml.write_text("changed\n", encoding="utf-8")
            subprocess.run(["git", "add", str(baml.relative_to(repo))], cwd=repo, check=True)

            env = os.environ.copy()
            env["PATH"] = str(repo / "bin") + os.pathsep + env.get("PATH", "")
            result = subprocess.run([str(repo / ".githooks" / "pre-commit-adoption-bundle")], cwd=repo, env=env, text=True, capture_output=True)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("refreshed and staged", result.stdout)
            staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=repo, text=True, capture_output=True, check=True)
            self.assertIn("docs/adoption/dotfiles-adoption-bundle.json", staged.stdout.splitlines())
            self.assertIn("status", log.read_text(encoding="utf-8"))
            self.assertIn("refresh", log.read_text(encoding="utf-8"))

    def test_hook_exits_nonzero_when_status_setup_fails(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self._hook_repo(Path(td))
            self._write_fake_phase_loop(repo, repo / "phase-loop.log", status_code=2)
            baml = repo / "vendor" / "phase-loop-runtime" / "src" / "phase_loop_runtime" / "baml_src" / "emit_phase_closeout.baml"
            baml.write_text("changed\n", encoding="utf-8")
            subprocess.run(["git", "add", str(baml.relative_to(repo))], cwd=repo, check=True)
            env = os.environ.copy()
            env["PATH"] = str(repo / "bin") + os.pathsep + env.get("PATH", "")

            result = subprocess.run([str(repo / ".githooks" / "pre-commit-adoption-bundle")], cwd=repo, env=env, text=True, capture_output=True)

            self.assertEqual(result.returncode, 2)
            self.assertIn("status check failed", result.stderr)

    def _bundle_repo(self, tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        for relative in (
            "docs/adoption/dotfiles-adoption-bundle.json",
            "docs/dotfiles-source-authority-contract.md",
            "docs/c4/phase-loop-runtime-c4-document.md",
            "docs/tasks/dotfiles-task-catalog.md",
        ):
            destination = repo / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)
        baml_root = repo / "vendor" / "phase-loop-runtime" / "src" / "phase_loop_runtime" / "baml_src"
        shutil.copytree(ROOT / "vendor" / "phase-loop-runtime" / "src" / "phase_loop_runtime" / "baml_src", baml_root)
        refresh_adoption_bundle(repo)
        return repo

    def _hook_repo(self, tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
        (repo / ".githooks").mkdir()
        shutil.copy2(HOOK, repo / ".githooks" / "pre-commit-adoption-bundle")
        (repo / ".githooks" / "pre-commit-adoption-bundle").chmod(0o755)
        (repo / "vendor" / "phase-loop-runtime" / "src" / "phase_loop_runtime" / "baml_src").mkdir(parents=True)
        (repo / "vendor" / "phase-loop-runtime" / "src" / "phase_loop_runtime" / "baml_src" / "emit_phase_closeout.baml").write_text("initial\n", encoding="utf-8")
        (repo / "docs" / "adoption").mkdir(parents=True)
        (repo / "docs" / "adoption" / "dotfiles-adoption-bundle.json").write_text("{}\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "fixture"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
        return repo

    def _write_fake_phase_loop(self, repo: Path, log: Path, status_code: int = 1) -> None:
        bin_dir = repo / "bin"
        bin_dir.mkdir()
        script = bin_dir / "phase-loop"
        script.write_text(
            "#!/bin/sh\n"
            f"echo \"$2\" >> {log}\n"
            "if [ \"$2\" = \"status\" ]; then\n"
            f"  exit {status_code}\n"
            "fi\n"
            "printf '{\"refreshed\":true}\\n' > docs/adoption/dotfiles-adoption-bundle.json\n"
            "exit 0\n",
            encoding="utf-8",
        )
        script.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
