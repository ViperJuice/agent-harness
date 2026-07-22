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

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from phase_loop_test_utils import (
    commit_fixture_paths,
    make_repo,
    write_blank_png,
    write_phase_plan,
    write_varied_png,
)
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

# Fix 3: owned files declared as a GLOB (`src/**`) whose resolved tree contains a
# real media-render file (`src/avatar_renderer.py`). The closeout validator would
# block on the real file; reconcile must resolve the glob to the same real path.
GLOB_AVATAR_BODY = (
    "# RUNNER\n\n"
    "## Objective\n\n"
    "This phase renders a visible avatar in the browser meeting UI (synthetic media).\n\n"
    "## Lanes\n\n"
    "### SL-0 - RUNNER\n"
    "- **Owned files**: `src/**`\n"
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

    def _setup(self, body: str, owned_files: tuple[str, ...] = ("tests/fixtures/avatar_call.html",)):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        repo = make_repo(Path(td.name))
        roadmap = repo / "specs" / "phase-plans-v1.md"
        plan = write_phase_plan(repo, "RUNNER", roadmap, body=body)
        # Fix 3: the reconcile guard resolves the phase's owned globs against the
        # blocked commit's ACTUAL tree, so the declared owned media file(s) must
        # genuinely exist and be committed -- otherwise the resolved-path surface
        # is empty and the guard is (correctly) inert.
        committed = [plan]
        for rel in owned_files:
            target = repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("<html><body>avatar</body></html>\n", encoding="utf-8")
            committed.append(target)
        commit_fixture_paths(repo, "add runner plan + owned files", *committed)
        return repo, roadmap

    def _write_committed_artifact(self, repo: Path, rel: str) -> str:
        # agent-harness#91 round-2 (Fix 2): deliberately left UNCOMMITTED (every
        # caller already passes --allow-dirty). resolve_visual_evidence_artifact
        # only requires the file to EXIST inside the repo, not that it's
        # committed -- and committing it as a separate later commit would move
        # HEAD (the tests' defaulted --closeout-commit) past the commit that
        # actually touched the owned avatar/browser-media surface, which the
        # new diff-tree-of-the-closeout-commit structural check
        # (_resolve_changed_paths_at_commit) would then correctly NOT flag --
        # that's Fix 2 working as intended, but it would make this fixture
        # helper name-misleading, so the artifact stays uncommitted instead.
        #
        # agent-harness#91 round-3 (codex CR): the gate now DERIVES pixel stats
        # from the DECODED image, so this must be a REAL, varied (non-blank)
        # PNG -- a magic-header-only fake is now UNDECODABLE and fails closed
        # rather than passing on a self-reported observation.
        target = repo / rel
        write_varied_png(target)
        return rel

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
        pytest.importorskip("PIL")  # _write_committed_artifact decodes a real PNG
        repo, roadmap = self._setup(VISIBLE_AVATAR_BODY)
        # Fix 4: the artifact must actually EXIST inside the repo.
        artifact = self._write_committed_artifact(repo, "shots/frame.png")
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            code, _, stderr = _run(
                self._args(
                    repo, roadmap, "RUNNER",
                    "--verification-status", "passed",
                    "--allow-dirty",
                    "--visual-evidence-path", artifact,
                    "--visual-evidence-observed", '{"nonBlackPixels": 19200, "pixelMin": 0, "pixelMax": 255}',
                )
            )
        self.assertEqual(code, 0, stderr)
        manual_repair = read_events(repo)[-1]["metadata"]["manual_repair"]
        self.assertEqual(manual_repair["visual_evidence_path"], artifact)
        self.assertTrue(manual_repair["visual_evidence_observed"])

    def test_matching_phase_blank_evidence_blocks_on_opt_in(self):
        pytest.importorskip("PIL")
        repo, roadmap = self._setup(VISIBLE_AVATAR_BODY)
        artifact = repo / "shots" / "frame.png"
        # uniform gray frame -- a REAL, DECODABLE, but genuinely blank image.
        write_blank_png(artifact)
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            code, _, stderr = _run(
                self._args(
                    repo, roadmap, "RUNNER",
                    "--verification-status", "passed",
                    "--allow-dirty",
                    "--visual-evidence-path", "shots/frame.png",
                    "--visual-evidence-observed", '{"nonBlackPixels": 19200, "pixelMin": 243, "pixelMax": 243}',
                )
            )
        self.assertEqual(code, 2)
        self.assertIn("visual-avatar evidence", stderr)

    def test_matching_phase_blank_evidence_blocks_despite_fabricated_self_report_on_opt_in(self):
        pytest.importorskip("PIL")
        # round-3 (codex CR) core repro: a genuinely blank/uniform DECODED
        # image, paired with FABRICATED "good" self-reported numbers, must
        # still BLOCK -- the derived observation is authoritative and the
        # self-report can never override it.
        repo, roadmap = self._setup(VISIBLE_AVATAR_BODY)
        artifact = repo / "shots" / "frame.png"
        write_blank_png(artifact)
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
        self.assertEqual(code, 2)
        self.assertIn("visual-avatar evidence", stderr)

    def test_matching_phase_undecodable_artifact_fails_closed_on_opt_in(self):
        # round-3 (codex CR) core repro: a valid-header but UNDECODABLE
        # (corrupt/truncated) artifact, paired with fabricated "good"
        # self-reported numbers, must fail CLOSED -- never silently pass on
        # the self-report because derivation itself could not run.
        repo, roadmap = self._setup(VISIBLE_AVATAR_BODY)
        artifact = repo / "shots" / "frame.png"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
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
        self.assertEqual(code, 2)
        self.assertIn("visual-avatar evidence", stderr)

    def test_matching_phase_undecodable_artifact_promotes_with_recorded_finding_under_warn_default(self):
        # Requires a REAL Pillow install: distinguishing "undecodable"
        # (Pillow present, decode failed on corrupt bytes) from
        # "cannot_verify" (Pillow itself absent) needs the real import to
        # succeed and then fail on the corrupt body.
        pytest.importorskip("PIL")
        repo, roadmap = self._setup(VISIBLE_AVATAR_BODY)
        artifact = repo / "shots" / "frame.png"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
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
        # never silently clean -- the undecodable shortfall is recorded even
        # though warn-default lets the promotion proceed.
        self.assertTrue(manual_repair.get("visual_evidence_undecodable"))
        self.assertTrue(manual_repair.get("visual_evidence_missing_or_blank"))

    def test_matching_phase_decoder_unavailable_fails_closed_on_opt_in(self):
        # A decoder-unavailable environment (Pillow import raises) must fail
        # CLOSED -- never fabricate a pass because derivation could not run.
        # CORE-ONLY fail-closed smoke (agent-harness#91 round-4 CR): must
        # PASS even when Pillow is genuinely absent -- derive_visual_
        # observation raises on `from PIL import Image` itself, before ever
        # touching the artifact's bytes, so a plain placeholder file (not
        # write_varied_png, which needs a real Pillow install) is enough and
        # no importorskip("PIL") guard belongs here.
        repo, roadmap = self._setup(VISIBLE_AVATAR_BODY)
        artifact = repo / "shots" / "frame.png"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"\x89PNG\r\n\x1a\n" + b"placeholder, never decoded: decoder is what's missing")
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}), patch.dict(
            sys.modules, {"PIL": None, "PIL.Image": None}
        ):
            code, _, stderr = _run(
                self._args(
                    repo, roadmap, "RUNNER",
                    "--verification-status", "passed",
                    "--allow-dirty",
                    "--visual-evidence-path", "shots/frame.png",
                    "--visual-evidence-observed", '{"nonBlackPixels": 19200, "pixelMin": 0, "pixelMax": 255}',
                )
            )
        self.assertEqual(code, 2)
        self.assertIn("visual-avatar evidence", stderr)

    def test_matching_phase_nonexistent_artifact_blocks_on_opt_in(self):
        # Fix 4: an ASSERTED path that does not exist in the repo is rejected --
        # valid observations alone cannot promote it.
        repo, roadmap = self._setup(VISIBLE_AVATAR_BODY)
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            code, _, stderr = _run(
                self._args(
                    repo, roadmap, "RUNNER",
                    "--verification-status", "passed",
                    "--allow-dirty",
                    "--visual-evidence-path", "shots/does_not_exist.png",
                    "--visual-evidence-observed", '{"nonBlackPixels": 19200, "pixelMin": 0, "pixelMax": 255}',
                )
            )
        self.assertEqual(code, 2)
        self.assertIn("visual-avatar evidence", stderr)

    def test_matching_phase_out_of_repo_artifact_blocks_on_opt_in(self):
        # Fix 4: an absolute out-of-repo escape path is rejected.
        repo, roadmap = self._setup(VISIBLE_AVATAR_BODY)
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            code, _, stderr = _run(
                self._args(
                    repo, roadmap, "RUNNER",
                    "--verification-status", "passed",
                    "--allow-dirty",
                    "--visual-evidence-path", "/etc/hostname",
                    "--visual-evidence-observed", '{"nonBlackPixels": 19200, "pixelMin": 0, "pixelMax": 255}',
                )
            )
        self.assertEqual(code, 2)
        self.assertIn("visual-avatar evidence", stderr)

    # --- Fix 2: guard runs independent of the optional --verification-status ---

    def test_matching_phase_missing_flag_still_guarded_on_opt_in(self):
        # Omitting --verification-status must NOT bypass the gate: reconcile
        # always promotes to complete, so a matching phase is still guarded.
        repo, roadmap = self._setup(VISIBLE_AVATAR_BODY)
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            code, _, stderr = _run(
                self._args(repo, roadmap, "RUNNER", "--allow-dirty")
            )
        self.assertEqual(code, 2)
        self.assertIn("visual-avatar evidence", stderr)

    # --- Fix 3: owned GLOB resolves to a real media file at the commit ---

    def test_owned_glob_resolves_to_real_media_file_and_guards(self):
        # `src/**` owns a real `src/avatar_renderer.py`; reconcile must resolve
        # the glob to that real path (the media-render filename heuristic) and
        # guard the same way the closeout validator would on the real file.
        repo, roadmap = self._setup(GLOB_AVATAR_BODY, owned_files=("src/avatar_renderer.py",))
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            code, _, stderr = _run(
                self._args(repo, roadmap, "RUNNER", "--verification-status", "passed", "--allow-dirty")
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

    # --- Fix 2 round 2 (gemini over-block): only OWNING a pre-existing media
    # file, without CHANGING it at the closeout commit, must not block -- this
    # is what the live runner would do (it has no ownership filter at all). ---

    def test_owning_but_not_changing_media_file_is_not_blocked(self):
        # `src/**` owns `src/avatar_renderer.py`, committed in an EARLIER commit.
        # The closeout commit (HEAD, defaulted) only changes a non-media file --
        # reconcile must evaluate THAT commit's actual changed paths, not the
        # whole owned tree, so this must NOT be blocked (matches the runner).
        repo, roadmap = self._setup(GLOB_AVATAR_BODY, owned_files=("src/avatar_renderer.py",))
        util = repo / "src" / "utils.py"
        util.write_text("def helper():\n    return 1\n", encoding="utf-8")
        commit_fixture_paths(repo, "change only a non-media file", util)
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            code, _, stderr = _run(
                self._args(repo, roadmap, "RUNNER", "--verification-status", "passed", "--allow-dirty")
            )
        self.assertEqual(code, 0, stderr)
        manual_repair = read_events(repo)[-1]["metadata"]["manual_repair"]
        self.assertNotIn("visual_evidence_missing_or_blank", manual_repair)

    # --- Fix 2 round 2 (codex fail-open): resolution failures must fail CLOSED,
    # never silently read as "gate does not apply". ---

    def test_invalid_closeout_commit_fails_closed(self):
        repo, roadmap = self._setup(VISIBLE_AVATAR_BODY)
        before = len(read_events(repo))
        code, _, stderr = _run(
            self._args(
                repo, roadmap, "RUNNER",
                "--verification-status", "passed",
                "--allow-dirty",
                "--closeout-commit", "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
            )
        )
        self.assertEqual(code, 2)
        self.assertIn("closeout-commit", stderr)
        # No manual_repair event was recorded -- the phase was NOT promoted.
        self.assertEqual(len(read_events(repo)), before)

    def test_unreadable_plan_fails_closed_unconditionally(self):
        # A plan path that RESOLVES but fails to read (here: it resolves to a
        # directory) is a resolution FAILURE distinct from "no plan artifact
        # exists at all" -- it must fail closed, not silently read as "no
        # explicit claim" (which would be the same fail-open class as an
        # invalid --closeout-commit). Deliberately run under the DEFAULT
        # (warn) posture, unset here, to prove this is NOT posture-gated the
        # way a genuinely-detected missing-evidence shortfall is -- a
        # resolution failure can't evaluate the contract at all, so it always
        # refuses, warn or block.
        repo, roadmap = self._setup(VISIBLE_AVATAR_BODY)
        fake_plan_dir = repo / "plans" / "phase-plan-v1-RUNNER.md"
        with patch("phase_loop_runtime.cli.find_plan_artifact", return_value=fake_plan_dir.parent):
            code, _, stderr = _run(
                self._args(repo, roadmap, "RUNNER", "--verification-status", "passed", "--allow-dirty")
            )
        self.assertEqual(code, 2)

    # --- non-matching phase: guard is inert ---

    def test_non_matching_phase_unaffected(self):
        repo, roadmap = self._setup(GENERIC_BODY, owned_files=("src/runner.py",))
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
