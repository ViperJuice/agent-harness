"""FAV (issue #91, Phase 4B) -- reconcile/manual-repair guard.

A phase matching the visual-avatar-evidence detection contract (owned
avatar/browser-media surface + explicit visible-render claim) must not be
silently promoted to `complete` by `phase-loop reconcile` without satisfying
the same evidence contract the closeout validator enforces. Warn-default
still applies: this only refuses under the opt-in-to-block posture
(PHASE_LOOP_REVIEW=block); under the default warn posture the shortfall is
recorded but the promotion proceeds (autonomy-first, no human_required).
"""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from phase_loop_test_utils import commit_fixture_paths, make_repo, write_phase_plan
from phase_loop_runtime.cli import main
from phase_loop_runtime.events import read_events

VISIBLE_AVATAR_BODY = (
    "# RUNNER\n\n"
    "## Objective\n\n"
    "This phase renders a visible avatar in the browser meeting UI (synthetic media).\n\n"
    "## Lanes\n\n"
    "### SL-0 - RUNNER\n"
    "- **Owned files**: `tests/fixtures/avatar_call.html`\n"
)

GENERIC_BODY = (
    "# RUNNER\n\n## Objective\n\nGeneric backend refactor, no media surface.\n\n"
    "## Lanes\n\n### SL-0 - RUNNER\n- **Owned files**: `src/runner.py`\n"
)


def _run(argv: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


class ReconcileVisualAvatarEvidenceTest(unittest.TestCase):
    def setUp(self):
        self._review = os.environ.pop("PHASE_LOOP_REVIEW", None)

    def tearDown(self):
        if self._review is not None:
            os.environ["PHASE_LOOP_REVIEW"] = self._review
        elif "PHASE_LOOP_REVIEW" in os.environ:
            del os.environ["PHASE_LOOP_REVIEW"]

    def _args(self, repo: Path, roadmap: Path, phase: str, *extra: str) -> list[str]:
        return ["reconcile", "--repo", str(repo), "--roadmap", str(roadmap), "--phase", phase, *extra]

    def _setup(self, body: str):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        repo = make_repo(Path(td.name))
        roadmap = repo / "specs" / "phase-plans-v1.md"
        plan = write_phase_plan(repo, "RUNNER", roadmap, body=body)
        commit_fixture_paths(repo, "add runner plan", plan)
        return repo, roadmap

    # --- warn-default: missing evidence records the shortfall but doesn't block ---

    def test_matching_phase_missing_evidence_promotes_under_warn_default(self):
        repo, roadmap = self._setup(VISIBLE_AVATAR_BODY)
        code, _, stderr = _run(
            self._args(repo, roadmap, "RUNNER", "--verification-status", "passed", "--allow-dirty")
        )
        self.assertEqual(code, 0, stderr)
        event = read_events(repo)[-1]
        self.assertTrue(event["metadata"]["manual_repair"].get("visual_evidence_missing_or_blank"))

    # --- opt-in-block: missing evidence refuses the promotion ---

    def test_matching_phase_missing_evidence_blocks_on_opt_in(self):
        repo, roadmap = self._setup(VISIBLE_AVATAR_BODY)
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            code, _, stderr = _run(
                self._args(repo, roadmap, "RUNNER", "--verification-status", "passed", "--allow-dirty")
            )
        self.assertEqual(code, 2)
        self.assertIn("visual-avatar evidence", stderr)

    def test_matching_phase_valid_evidence_promotes_on_opt_in(self):
        repo, roadmap = self._setup(VISIBLE_AVATAR_BODY)
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            code, _, stderr = _run(
                self._args(
                    repo, roadmap, "RUNNER",
                    "--verification-status", "passed",
                    "--allow-dirty",
                    "--visual-evidence-path", "shots/frame.png",
                    "--visual-evidence-observed", '{"nonBlackPixels": 19200, "pixelMin": 0, "pixelMax": 255}',
                )
            )
        self.assertEqual(code, 0, stderr)
        manual_repair = read_events(repo)[-1]["metadata"]["manual_repair"]
        self.assertEqual(manual_repair["visual_evidence_path"], "shots/frame.png")
        self.assertTrue(manual_repair["visual_evidence_observed"])

    def test_matching_phase_blank_evidence_blocks_on_opt_in(self):
        repo, roadmap = self._setup(VISIBLE_AVATAR_BODY)
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            code, _, stderr = _run(
                self._args(
                    repo, roadmap, "RUNNER",
                    "--verification-status", "passed",
                    "--allow-dirty",
                    "--visual-evidence-path", "shots/frame.png",
                    # uniform gray frame -- pixelMin == pixelMax
                    "--visual-evidence-observed", '{"nonBlackPixels": 19200, "pixelMin": 243, "pixelMax": 243}',
                )
            )
        self.assertEqual(code, 2)
        self.assertIn("visual-avatar evidence", stderr)

    def test_matching_phase_typed_opt_out_promotes_on_opt_in(self):
        repo, roadmap = self._setup(VISIBLE_AVATAR_BODY)
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            code, _, stderr = _run(
                self._args(
                    repo, roadmap, "RUNNER",
                    "--verification-status", "passed",
                    "--allow-dirty",
                    "--visual-evidence-opt-out", "no_visible_media_surface",
                )
            )
        self.assertEqual(code, 0, stderr)
        manual_repair = read_events(repo)[-1]["metadata"]["manual_repair"]
        self.assertEqual(manual_repair["visual_evidence_opt_out"], "no_visible_media_surface")

    # --- non-matching phase: guard is inert ---

    def test_non_matching_phase_unaffected(self):
        repo, roadmap = self._setup(GENERIC_BODY)
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            code, _, stderr = _run(
                self._args(repo, roadmap, "RUNNER", "--verification-status", "passed", "--allow-dirty")
            )
        self.assertEqual(code, 0, stderr)
        manual_repair = read_events(repo)[-1]["metadata"]["manual_repair"]
        self.assertNotIn("visual_evidence_missing_or_blank", manual_repair)
        self.assertNotIn("visual_evidence_path", manual_repair)


if __name__ == "__main__":
    unittest.main()
