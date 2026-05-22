from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.cli import build_parser, main
from phase_loop_runtime.install_status import build_install_status, summarize_install_status
from phase_loop_runtime.skill_install import REQUIRED_SKILLS
from phase_loop_test_utils import make_repo


class PhaseLoopInstallStatusTest(unittest.TestCase):
    def test_install_status_reports_harness_parity_and_schema_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            skill_root = Path(td) / "codex-skills"
            for skill in REQUIRED_SKILLS:
                path = skill_root / f"codex-{skill}"
                path.mkdir(parents=True)
                (path / "SKILL.md").write_text("name: fixture\n", encoding="utf-8")

            with patch.dict("os.environ", {"PHASE_LOOP_SKILL_BUNDLE": str(skill_root)}, clear=False):
                payload = build_install_status(repo, harnesses=("codex",))

            self.assertEqual(payload["schema"], "phase-loop-install-status.v1")
            self.assertEqual(payload["summary"], "installed")
            self.assertEqual(payload["harnesses"][0]["skill_parity"], "complete")
            self.assertEqual(payload["baml_closeout_schema"]["status"], "available")
            self.assertIn(payload["dev_skills_ignore"]["gitignore_entry"], {"present", "missing"})

    def test_install_status_redacts_private_paths(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            payload = build_install_status(repo, harnesses=("codex",))
            serialized = json.dumps(payload, sort_keys=True)

            for token in ("/home/", "/Users/", "/mnt/", "op://", "sk-", "AKIA", "ghp_"):
                self.assertNotIn(token, serialized)

    def test_install_status_summary_for_missing_roots(self):
        records = ({"root_status": "missing", "skill_parity": "missing"},)
        self.assertEqual(summarize_install_status(records), "partial")
        self.assertEqual(summarize_install_status(()), "unknown")

    def test_install_status_cli_does_not_require_harness(self):
        args = build_parser().parse_args(["install", "--status", "--json"])
        self.assertTrue(args.status)
        self.assertIsNone(args.harness)

        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = main(["install", "--status", "--repo", str(repo), "--json"])

            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(stdout.getvalue())["schema"], "phase-loop-install-status.v1")


if __name__ == "__main__":
    unittest.main()
