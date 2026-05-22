from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from phase_loop_runtime.baml_modular import parse_baml_response
from phase_loop_runtime.cli import main
from phase_loop_runtime.runtime_projection import build_runtime_projection
from phase_loop_runtime.runner import status_snapshot
from phase_loop_test_utils import make_repo


class PhaseLoopRuntimeProjectionTest(unittest.TestCase):
    def test_runtime_projection_validates_against_dotfiles_schema(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            snapshot = status_snapshot(repo, roadmap, pipeline_mode="pipeline_optional")

            payload = build_runtime_projection(repo, roadmap, snapshot=snapshot, pipeline_mode="pipeline_optional")

            self.assertEqual(payload["operating_mode"], "pipeline_optional")
            parse_baml_response("DotfilesRuntimeProjection", json.dumps(payload))

    def test_runtime_projection_redacts_host_and_secret_like_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            payload = build_runtime_projection(repo, roadmap, pipeline_mode="standalone")
            serialized = json.dumps(payload, sort_keys=True)

            for token in ("/home/", "/Users/", "/mnt/", "op://", "sk-", "AKIA", "ghp_"):
                self.assertNotIn(token, serialized)

    def test_status_runtime_projection_json_cli(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                rc = main(
                    [
                        "status",
                        "--repo",
                        str(repo),
                        "--roadmap",
                        str(roadmap),
                        "--pipeline-mode",
                        "standalone",
                        "--runtime-projection",
                        "--json",
                    ]
                )

            self.assertEqual(rc, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["operating_mode"], "standalone")
            parse_baml_response("DotfilesRuntimeProjection", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
