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
import subprocess
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
from phase_loop_runtime.cli import _reconcile_visual_evidence_guard, main
from phase_loop_runtime.events import append_event, read_events
from phase_loop_runtime.models import LoopEvent, utc_now
from phase_loop_runtime.observability import build_terminal_summary

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

    def _declare(self, repo: Path, roadmap: Path, phase: str, declared: bool) -> None:
        # FAV #272: reconcile reads visual_render_declared from the phase's
        # own PERSISTED event history (metadata.terminal_summary), not from a
        # CLI flag and not re-derived from the git diff -- so tests simulate
        # "the executor declared this at closeout time" by appending a real
        # event carrying the field.
        #
        # round-8 CR (codex+gemini Finding 3b): this MUST drive the
        # declaration through the REAL production serializer chain
        # (observability.build_terminal_summary -> apply_child_terminal_
        # summary_overlay -> models.visual_evidence_terminal_fields), the
        # exact path the live runner uses to build metadata.terminal_summary
        # -- NOT a hand-injected ledger dict that bypasses it. The prior
        # version of this helper injected {"visual_render_declared": declared}
        # directly into the event, which happened to make an explicit
        # ``False`` "work" even while the real serializer was silently
        # STRIPPING every explicit False (truthy-only persistence) -- a
        # hand-injected dict can never catch that class of bug because it
        # never exercises the code path that has it.
        terminal_summary = build_terminal_summary(
            terminal_status="blocked",
            terminal_blocker=None,
            verification_status="blocked",
            next_action="",
            child_baml_closeout={"visual_render_declared": declared},
        )
        append_event(
            repo,
            LoopEvent(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phase=phase,
                action="execute",
                status="blocked",
                model="test",
                reasoning_effort="test",
                source="test",
                metadata={"terminal_summary": terminal_summary},
            ),
        )

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
        self._declare(repo, roadmap, "RUNNER", True)
        code, _, stderr = _run(
            self._args(repo, roadmap, "RUNNER", "--verification-status", "passed", "--allow-dirty")
        )
        self.assertEqual(code, 0, stderr)
        event = read_events(repo)[-1]
        self.assertTrue(event["metadata"]["manual_repair"].get("visual_evidence_missing_or_blank"))

    # --- opt-in-block: missing evidence refuses the promotion ---

    def test_matching_phase_missing_evidence_blocks_on_opt_in(self):
        repo, roadmap = self._setup(VISIBLE_AVATAR_BODY)
        self._declare(repo, roadmap, "RUNNER", True)
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            code, _, stderr = _run(
                self._args(repo, roadmap, "RUNNER", "--verification-status", "passed", "--allow-dirty")
            )
        self.assertEqual(code, 2)
        self.assertIn("visual-avatar evidence", stderr)

    def test_matching_phase_valid_evidence_promotes_on_opt_in(self):
        pytest.importorskip("PIL")  # _write_committed_artifact decodes a real PNG
        repo, roadmap = self._setup(VISIBLE_AVATAR_BODY)
        self._declare(repo, roadmap, "RUNNER", True)
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
        self._declare(repo, roadmap, "RUNNER", True)
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
        self._declare(repo, roadmap, "RUNNER", True)
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
        self._declare(repo, roadmap, "RUNNER", True)
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
        self._declare(repo, roadmap, "RUNNER", True)
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

    def test_matching_phase_decoder_unavailable_promotes_silently_under_warn_default(self):
        # round-7 CR: decoder-ABSENT (Pillow not installed) under warn-default
        # must be SILENT here too, exactly like the closeout validator -- no
        # visual_evidence_cannot_verify / visual_evidence_missing_or_blank
        # finding recorded at all, not merely non-blocking. A standard install
        # without the optional `visual` extra must not get spammed on the
        # reconcile path purely because an optional dependency isn't
        # installed. CORE-ONLY fail-closed smoke, mirrors the opt-in test
        # below: Pillow import itself raises, before ever touching the
        # artifact's bytes, so a plain placeholder file is enough.
        repo, roadmap = self._setup(VISIBLE_AVATAR_BODY)
        self._declare(repo, roadmap, "RUNNER", True)
        artifact = repo / "shots" / "frame.png"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"\x89PNG\r\n\x1a\n" + b"placeholder, never decoded: decoder is what's missing")
        with patch.dict(sys.modules, {"PIL": None, "PIL.Image": None}):
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
        self.assertNotIn("visual_evidence_cannot_verify", manual_repair)
        self.assertNotIn("visual_evidence_missing_or_blank", manual_repair)

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
        self._declare(repo, roadmap, "RUNNER", True)
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
        self._declare(repo, roadmap, "RUNNER", True)
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
        self._declare(repo, roadmap, "RUNNER", True)
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
        self._declare(repo, roadmap, "RUNNER", True)
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            code, _, stderr = _run(
                self._args(repo, roadmap, "RUNNER", "--allow-dirty")
            )
        self.assertEqual(code, 2)
        self.assertIn("visual-avatar evidence", stderr)

    # --- Fix 3: owned GLOB resolves to a real media file at the commit ---

    def test_owned_glob_resolves_to_real_media_file_and_guards(self):
        # `src/**` owns a real `src/avatar_renderer.py`; the phase DECLARED a
        # visible render, so reconcile guards on missing evidence the same
        # way the closeout validator would.
        repo, roadmap = self._setup(GLOB_AVATAR_BODY, owned_files=("src/avatar_renderer.py",))
        self._declare(repo, roadmap, "RUNNER", True)
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            code, _, stderr = _run(
                self._args(repo, roadmap, "RUNNER", "--verification-status", "passed", "--allow-dirty")
            )
        self.assertEqual(code, 2)
        self.assertIn("visual-avatar evidence", stderr)

    def test_owned_glob_real_media_file_without_declaration_raises_advisory_only(self):
        # FAV #272 discriminating case: the SAME owned-glob-resolved real
        # media file that used to trigger a block under the retired
        # heuristic contract must NEVER block without a declaration, even
        # under opt-in `block` -- it only raises the non-blocking advisory.
        repo, roadmap = self._setup(GLOB_AVATAR_BODY, owned_files=("src/avatar_renderer.py",))
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            code, _, stderr = _run(
                self._args(repo, roadmap, "RUNNER", "--verification-status", "passed", "--allow-dirty")
            )
        self.assertEqual(code, 0, stderr)
        manual_repair = read_events(repo)[-1]["metadata"]["manual_repair"]
        self.assertTrue(manual_repair.get("visual_render_undeclared_surface"))
        self.assertNotIn("visual_evidence_missing_or_blank", manual_repair)

    def test_matching_phase_typed_opt_out_promotes_on_opt_in(self):
        repo, roadmap = self._setup(VISIBLE_AVATAR_BODY)
        self._declare(repo, roadmap, "RUNNER", True)
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
        # The closeout commit (HEAD, defaulted) only changes a non-media file.
        # FAV #272: the BLOCK decision no longer depends on changed paths at
        # all (declared-only), so this is never blocked regardless of which
        # files this commit touched -- there is no declaration here, so it
        # promotes cleanly (GLOB_AVATAR_BODY's plan text still carries an
        # explicit claim, which independently raises the non-blocking
        # advisory via the OR's claim axis -- that is expected and does not
        # affect the block decision, which is what this test guards).
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
        self._declare(repo, roadmap, "RUNNER", True)
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

    def test_unreadable_plan_is_advisory_only_never_fails_closed(self):
        # FAV #272: plan text now feeds ONLY the non-blocking advisory (the
        # BLOCK decision is declared-only and never reads plan text at all).
        # A plan path that RESOLVES but fails to read (here: it resolves to a
        # directory) therefore just means no CLAIM signal can be computed for
        # the advisory -- it must NOT fail closed the way it used to when
        # plan text fed the block contract directly. The owned .html surface
        # in VISIBLE_AVATAR_BODY still makes the STRUCTURAL half of the
        # advisory's OR fire independent of the plan read failure, so the
        # advisory is still recorded -- just never a block/refusal.
        repo, roadmap = self._setup(VISIBLE_AVATAR_BODY)
        fake_plan_dir = repo / "plans" / "phase-plan-v1-RUNNER.md"
        with patch("phase_loop_runtime.cli.find_plan_artifact", return_value=fake_plan_dir.parent):
            code, _, stderr = _run(
                self._args(repo, roadmap, "RUNNER", "--verification-status", "passed", "--allow-dirty")
            )
        self.assertEqual(code, 0, stderr)
        manual_repair = read_events(repo)[-1]["metadata"]["manual_repair"]
        self.assertTrue(manual_repair.get("visual_render_undeclared_surface"))

    # --- Fix 2 (agent-harness#91 round-6 CR): a MERGE closeout commit must
    # still be evaluated -- a bare `git diff-tree --root <merge>` (no
    # explicit parent) returns ZERO paths for an ordinary clean merge (the
    # combined diff suppresses every non-conflicting path), which was
    # silently read as "genuinely no files changed" -> gate bypassed
    # (fail-open). The fix diffs against the TRUE first parent (the two-tree
    # `git diff-tree <commit>^1 <commit>` form) -- not `-m --first-parent`,
    # which was tried first but empirically still emits the UNION of every
    # parent's diff. ---

    def test_merge_commit_with_media_file_still_blocks_on_opt_in(self):
        # A merge commit whose merged content brings in the owned
        # avatar/browser-media surface (via a feature branch merged with
        # --no-ff) must still be evaluated -- the gate must BLOCK on missing
        # evidence exactly as it would for a direct, non-merge commit that
        # touched the same file. Under the pre-fix bug, the merge commit's
        # diff-tree returned no paths at all, so `avatar_media_surface_
        # touched` saw an empty structural surface and the guard was
        # (wrongly) inert -- promoting the phase with code 0.
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        repo = make_repo(Path(td.name))
        roadmap = repo / "specs" / "phase-plans-v1.md"
        base_branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
        ).stdout.strip()
        plan = write_phase_plan(repo, "RUNNER", roadmap, body=VISIBLE_AVATAR_BODY)
        commit_fixture_paths(repo, "add runner plan", plan)

        # Feature branch: adds the owned avatar/browser-media surface.
        subprocess.run(["git", "checkout", "-q", "-b", "feature/avatar-media"], cwd=repo, check=True)
        media = repo / "tests" / "fixtures" / "avatar_call.html"
        media.parent.mkdir(parents=True, exist_ok=True)
        media.write_text("<html><body>avatar</body></html>\n", encoding="utf-8")
        commit_fixture_paths(repo, "add avatar media fixture", media)

        # Back on the base branch: an unrelated change, then merge the
        # feature branch with --no-ff to produce a real 2-parent merge commit
        # whose diff-tree --root (no explicit parent) would show ZERO paths.
        subprocess.run(["git", "checkout", "-q", base_branch], cwd=repo, check=True)
        unrelated = repo / "UNRELATED.md"
        unrelated.write_text("unrelated change\n", encoding="utf-8")
        commit_fixture_paths(repo, "unrelated base-branch change", unrelated)
        subprocess.run(
            ["git", "merge", "--no-ff", "-q", "feature/avatar-media", "-m", "merge avatar media feature"],
            cwd=repo,
            check=True,
        )
        merge_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
        ).stdout.strip()
        # Sanity: a bare (no -m --first-parent) diff-tree of this merge
        # commit genuinely returns no paths -- proves the scenario reproduces
        # the pre-fix bug shape, not just an artifact of this repo layout.
        bare_diff = subprocess.run(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", merge_sha],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.assertEqual(bare_diff, "", "test scenario must reproduce the bare diff-tree empty-paths bug")

        # Discriminating check: the resolver must report the TRUE first-
        # parent diff, not the union of every parent's diff. `UNRELATED.md`
        # was committed directly on the base branch (the merge's FIRST
        # parent) BEFORE the merge, so it is already present in that parent
        # and must NOT appear as a "changed path" of the merge commit itself
        # -- only `avatar_call.html` (brought in from the feature branch)
        # genuinely changed relative to the first parent. A union-of-all-
        # parents diff (e.g. `git diff-tree -m --first-parent`, which
        # empirically still emits every parent's diff, not just parent #1's)
        # would wrongly include `UNRELATED.md` here.
        from phase_loop_runtime.cli import _resolve_changed_paths_at_commit

        resolved_paths = _resolve_changed_paths_at_commit(repo, merge_sha)
        self.assertIn("tests/fixtures/avatar_call.html", resolved_paths)
        self.assertNotIn("UNRELATED.md", resolved_paths)

        # FAV #272: the block decision is declared-only -- declare the phase
        # so this still exercises the missing-evidence block, on top of the
        # merge-commit diff-tree-resolution fix proven above.
        self._declare(repo, roadmap, "RUNNER", True)
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            code, _, stderr = _run(
                self._args(
                    repo, roadmap, "RUNNER",
                    "--verification-status", "passed",
                    "--allow-dirty",
                    "--closeout-commit", merge_sha,
                )
            )
        self.assertEqual(code, 2)
        self.assertIn("visual-avatar evidence", stderr)

    # --- FAV #272: the persisted declared bool is the ONLY block trigger ---

    def test_persisted_declared_true_missing_evidence_blocks_on_opt_in(self):
        repo, roadmap = self._setup(VISIBLE_AVATAR_BODY)
        self._declare(repo, roadmap, "RUNNER", True)
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            code, _, stderr = _run(
                self._args(repo, roadmap, "RUNNER", "--verification-status", "passed", "--allow-dirty")
            )
        self.assertEqual(code, 2)
        self.assertIn("visual-avatar evidence", stderr)

    def test_persisted_declared_false_never_blocks_even_with_matching_paths(self):
        # Same phase/changed-paths shape (owned .html + explicit claim) as
        # the test above -- the ONLY difference is the persisted declaration
        # is explicitly False. Must never block, even under opt-in `block`.
        repo, roadmap = self._setup(VISIBLE_AVATAR_BODY)
        self._declare(repo, roadmap, "RUNNER", False)
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            code, _, stderr = _run(
                self._args(repo, roadmap, "RUNNER", "--verification-status", "passed", "--allow-dirty")
            )
        self.assertEqual(code, 0, stderr)
        manual_repair = read_events(repo)[-1]["metadata"]["manual_repair"]
        self.assertNotIn("visual_evidence_missing_or_blank", manual_repair)
        self.assertTrue(manual_repair.get("visual_render_undeclared_surface"))

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

    # --- Finding 2 (round-8 CR, codex+gemini): a changed-paths resolution
    # failure must never block an UNDECLARED phase -- the declared read must
    # happen FIRST, and changed_paths must never appear on any ok=False
    # path. cli.py's `main()` pre-validates --closeout-commit before the
    # guard ever runs (rejects an unresolvable commit at the CLI layer with
    # its own exit code 2), so the guard's OWN fail-open/closed behavior on
    # a resolution failure is only reachable by calling it directly. Pre-fix
    # repro: this exact call returned
    # ``(False, {"visual_evidence_resolution_failed": True,
    # "visual_evidence_missing_or_blank": True})`` -- an UNDECLARED phase
    # was refused purely because ``changed_paths`` (which feeds nothing but
    # the non-blocking advisory) could not be resolved. ---

    def test_undeclared_phase_changed_paths_resolution_failure_is_not_blocked(self):
        repo, roadmap = self._setup(GENERIC_BODY, owned_files=("src/runner.py",))
        # No declaration recorded for this phase at all, and no structural/
        # claim advisory signal either (GENERIC_BODY), so a clean (True,
        # None) is the only correct outcome once the resolution failure is
        # advisory-only.
        ok, fields = _reconcile_visual_evidence_guard(
            repo=repo,
            roadmap=roadmap,
            phase="RUNNER",
            closeout_commit="deadbeef" * 5,  # well-formed SHA shape, resolves to nothing
            visual_evidence_path=None,
            visual_evidence_observed_raw=None,
            visual_evidence_opt_out=None,
        )
        self.assertEqual((ok, fields), (True, None))

    # --- Finding 3a (round-8 CR, codex): the persisted-declaration ledger
    # scan is scoped by ROADMAP identity, not just the phase alias string --
    # a PRIOR roadmap's same-named phase must never supply the current
    # roadmap's declaration. ---

    def test_prior_roadmap_same_alias_true_does_not_block_current_undeclared_phase(self):
        repo, roadmap = self._setup(VISIBLE_AVATAR_BODY)
        prior_roadmap = repo / "specs" / "retired-phase-plans-v0.md"
        # A DIFFERENT roadmap document (different path/identity) that
        # happens to share the same phase alias "RUNNER", declared true.
        self._declare(repo, prior_roadmap, "RUNNER", True)
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            code, _, stderr = _run(
                self._args(repo, roadmap, "RUNNER", "--verification-status", "passed", "--allow-dirty")
            )
        self.assertEqual(code, 0, stderr)
        manual_repair = read_events(repo)[-1]["metadata"]["manual_repair"]
        self.assertNotIn("visual_evidence_missing_or_blank", manual_repair)
        # The structural/claim advisory can still fire independently for
        # THIS (undeclared, per the current roadmap) phase -- expected and
        # orthogonal to the block decision this test guards.
        self.assertTrue(manual_repair.get("visual_render_undeclared_surface"))

    # --- Finding 3b (round-8 CR, codex+gemini): a later explicit False
    # retracts an earlier True -- no "sticky-True" latch. Drives BOTH
    # declarations through the real production serializer (see _declare),
    # so this would have caught the truthy-only-strip bug the hand-injected
    # gemini repro test could not. ---

    def test_declared_true_then_false_sequence_ends_not_blocked(self):
        repo, roadmap = self._setup(VISIBLE_AVATAR_BODY)
        self._declare(repo, roadmap, "RUNNER", True)
        self._declare(repo, roadmap, "RUNNER", False)
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            code, _, stderr = _run(
                self._args(repo, roadmap, "RUNNER", "--verification-status", "passed", "--allow-dirty")
            )
        self.assertEqual(code, 0, stderr)
        manual_repair = read_events(repo)[-1]["metadata"]["manual_repair"]
        self.assertNotIn("visual_evidence_missing_or_blank", manual_repair)
        self.assertTrue(manual_repair.get("visual_render_undeclared_surface"))


if __name__ == "__main__":
    unittest.main()
