"""issue #18 — docs-freshness closeout gate (F1) and public-surface globs (F3)."""
import os
import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.closeout import build_phase_loop_closeout
from phase_loop_runtime.docs_freshness import (
    docs_freshness_evidence_backed,
    enumerate_public_docs,
    is_explicit_release_phase,
    is_release_phase,
    scan_docs_freshness,
)
from phase_loop_runtime.models import PUBLIC_SURFACE_GLOBS, public_surface_touched


def _complete_closeout(plan, repo, changed_paths, env=None):
    scan = scan_docs_freshness(repo, plan_path=plan, changed_paths=changed_paths, env=env)
    return build_phase_loop_closeout(
        phase_alias="REL",
        plan_path=plan,
        terminal_summary={"terminal_status": "complete", "verification_status": "passed"},
        automation={"status": "complete", "verification_status": "passed", "human_required": False},
        changed_paths=changed_paths,
        docs_freshness=scan,
    )


class DocsFreshnessGateTest(unittest.TestCase):
    def setUp(self):
        # The gate must work under the DEFAULT review env — if it only blocked
        # under PHASE_LOOP_REVIEW=block it would prove nothing about hard-tier.
        self._review = os.environ.pop("PHASE_LOOP_REVIEW", None)
        self._fresh = os.environ.pop("PHASE_LOOP_DOCS_FRESHNESS", None)
        self._td = tempfile.TemporaryDirectory()
        self.repo = Path(self._td.name)

    def tearDown(self):
        if self._review is not None:
            os.environ["PHASE_LOOP_REVIEW"] = self._review
        if self._fresh is not None:
            os.environ["PHASE_LOOP_DOCS_FRESHNESS"] = self._fresh
        self._td.cleanup()

    def _release_plan(self):
        plan = self.repo / "plan.md"
        plan.write_text(
            "---\nphase: REL\nphase_loop_mutation: release_dispatch\n---\n# plan\n",
            encoding="utf-8",
        )
        return plan

    def _ordinary_plan(self):
        plan = self.repo / "plan.md"
        plan.write_text("---\nphase: INT\n---\n# plan\n", encoding="utf-8")
        return plan

    def test_release_stale_readme_blocks_under_default_env(self):
        self.assertIsNone(os.environ.get("PHASE_LOOP_REVIEW"))  # default env
        plan = self._release_plan()
        (self.repo / "README.md").write_text(
            "# proj\nMessage Board SHA: recovery commit pending\n", encoding="utf-8"
        )
        c = _complete_closeout(plan, self.repo, ["CHANGELOG.md"])
        self.assertEqual(c["terminal_status"], "blocked")
        self.assertEqual(c["docs_freshness"], "blocked")
        self.assertEqual(c["blocker"]["blocker_class"], "docs_freshness_stale")
        self.assertFalse(c["blocker"].get("human_required", True))

    def test_ordinary_phase_with_stale_token_unaffected(self):
        plan = self._ordinary_plan()
        # Same stale token in a doc, but ordinary phase (no release artifacts).
        (self.repo / "README.md").write_text("recovery commit pending\n", encoding="utf-8")
        c = _complete_closeout(plan, self.repo, ["src/internal/helper.py"])
        self.assertEqual(c["terminal_status"], "complete")
        self.assertEqual(c["docs_freshness"], "skipped")

    def test_ordinary_phase_changelog_bump_with_stale_token_does_not_block(self):
        # REGRESSION (fleet-halt vector): an ORDINARY plan (no explicit release
        # frontmatter) that bumps CHANGELOG.md (artifact-glob heuristic → scan
        # runs) while a public doc still carries a stale `TBD` must NOT hard-block
        # under the DEFAULT review env. The heuristic case caps at warn-tier.
        self.assertIsNone(os.environ.get("PHASE_LOOP_REVIEW"))  # default env
        plan = self._ordinary_plan()
        (self.repo / "README.md").write_text(
            "# proj\nRelease date: TBD\n", encoding="utf-8"
        )
        c = _complete_closeout(plan, self.repo, ["CHANGELOG.md"])
        self.assertEqual(c["terminal_status"], "complete")
        self.assertNotEqual(c["docs_freshness"], "blocked")
        # The scan still ran (heuristic release) and recorded the stale token as
        # warn-tier evidence — it is downgraded from block, never suppressed.
        detail = c["docs_freshness_detail"]
        self.assertFalse(detail["explicit_release"])
        self.assertTrue(detail["is_release_phase"])
        self.assertEqual(detail["blocking_hits"], [])
        self.assertTrue(
            any(h["token"] == "TBD" and h["severity"] == "warn" for h in detail["hits"])
        )

    def test_clean_release_passes(self):
        plan = self._release_plan()
        (self.repo / "README.md").write_text("# proj\nv1.0.5 published.\n", encoding="utf-8")
        (self.repo / "CHANGELOG.md").write_text("## [1.0.5] - 2026-01-01\n- release\n", encoding="utf-8")
        c = _complete_closeout(plan, self.repo, ["CHANGELOG.md"])
        self.assertEqual(c["terminal_status"], "complete")
        self.assertEqual(c["docs_freshness"], "passed")

    def test_token_inside_inline_code_is_not_flagged(self):
        # A release doc that *documents* a stale token (always backticked) must
        # not be flagged as itself stale (dogfood: this repo's own CHANGELOG).
        plan = self._release_plan()
        (self.repo / "CHANGELOG.md").write_text(
            "scans for stale placeholders (`recovery commit pending`, `TBD`).\n",
            encoding="utf-8",
        )
        c = _complete_closeout(plan, self.repo, ["CHANGELOG.md"])
        self.assertEqual(c["terminal_status"], "complete")
        self.assertEqual(c["docs_freshness"], "passed")

    def test_bare_token_outside_code_still_blocks(self):
        plan = self._release_plan()
        (self.repo / "CHANGELOG.md").write_text(
            "Message Board SHA: recovery commit pending\n", encoding="utf-8"
        )
        c = _complete_closeout(plan, self.repo, ["CHANGELOG.md"])
        self.assertEqual(c["terminal_status"], "blocked")

    def test_freshness_ok_marker_suppresses_block(self):
        plan = self._release_plan()
        (self.repo / "README.md").write_text(
            "this is TBD <!-- freshness-ok -->\n", encoding="utf-8"
        )
        c = _complete_closeout(plan, self.repo, ["CHANGELOG.md"])
        self.assertEqual(c["terminal_status"], "complete")
        self.assertEqual(c["docs_freshness"], "passed")

    def test_warn_mode_records_but_does_not_block(self):
        plan = self._release_plan()
        (self.repo / "README.md").write_text("recovery commit pending\n", encoding="utf-8")
        c = _complete_closeout(
            plan, self.repo, ["CHANGELOG.md"], env={"PHASE_LOOP_DOCS_FRESHNESS": "warn"}
        )
        self.assertEqual(c["terminal_status"], "complete")
        self.assertEqual(c["docs_freshness"], "passed")  # warn mode never sets blocked

    def test_off_mode_skips(self):
        plan = self._release_plan()
        (self.repo / "README.md").write_text("recovery commit pending\n", encoding="utf-8")
        c = _complete_closeout(
            plan, self.repo, ["CHANGELOG.md"], env={"PHASE_LOOP_DOCS_FRESHNESS": "off"}
        )
        self.assertEqual(c["terminal_status"], "complete")
        self.assertEqual(c["docs_freshness"], "skipped")

    def test_package_readme_stale_skeleton_is_warn_not_block(self):
        # Fuzzy "skeleton" signal is warn-tier — must NOT block a release.
        plan = self._release_plan()
        pkg = self.repo / "packages" / "runtime"
        pkg.mkdir(parents=True)
        (pkg / "README.md").write_text("This runtime is a skeleton.\n", encoding="utf-8")
        c = _complete_closeout(plan, self.repo, ["CHANGELOG.md"])
        self.assertEqual(c["terminal_status"], "complete")
        self.assertEqual(c["docs_freshness"], "passed")
        # but the warn hit is recorded in the detail evidence
        detail = c["docs_freshness_detail"]
        self.assertTrue(any(h["severity"] == "warn" for h in detail["hits"]))

    def test_prose_todo_is_warn_not_block(self):
        # TODO/FIXME/XXX are common in real shipped docs — must NOT hard-block.
        plan = self._release_plan()
        (self.repo / "README.md").write_text(
            "TODO: see the contributing guide before filing.\n", encoding="utf-8"
        )
        c = _complete_closeout(plan, self.repo, ["CHANGELOG.md"])
        self.assertEqual(c["terminal_status"], "complete")
        self.assertEqual(c["docs_freshness"], "passed")
        detail = c["docs_freshness_detail"]
        self.assertTrue(any(h["severity"] == "warn" and h["token"] == "TODO" for h in detail["hits"]))

    def test_no_prescan_threaded_is_skipped(self):
        plan = self._release_plan()
        c = build_phase_loop_closeout(
            phase_alias="REL",
            plan_path=plan,
            terminal_summary={"terminal_status": "complete", "verification_status": "passed"},
            automation={"status": "complete", "verification_status": "passed", "human_required": False},
            changed_paths=["CHANGELOG.md"],
        )
        # No docs_freshness kwarg -> no key, never blocked.
        self.assertEqual(c["terminal_status"], "complete")
        self.assertNotIn("docs_freshness", c)


class EvidenceBackedF5Test(unittest.TestCase):
    """issue #18 F5: a `passed` claim must be PROVABLE from enumerated-surface
    evidence; a bare/empty detail must not read as `passed`."""

    def setUp(self):
        self._fresh = os.environ.pop("PHASE_LOOP_DOCS_FRESHNESS", None)
        self._td = tempfile.TemporaryDirectory()
        self.repo = Path(self._td.name)

    def tearDown(self):
        if self._fresh is not None:
            os.environ["PHASE_LOOP_DOCS_FRESHNESS"] = self._fresh
        self._td.cleanup()

    def _release_plan(self):
        plan = self.repo / "plan.md"
        plan.write_text(
            "---\nphase: REL\nphase_loop_mutation: release_dispatch\n---\n# plan\n",
            encoding="utf-8",
        )
        return plan

    def test_release_phase_with_no_doc_surfaces_is_not_passed(self):
        # A release scan that runs but finds NO public-doc surfaces cannot prove
        # a pass — it must report `skipped` (no assertion), never `passed`.
        plan = self._release_plan()
        # repo has no README/CHANGELOG/etc.
        scan = scan_docs_freshness(self.repo, plan_path=plan, changed_paths=["CHANGELOG.md"])
        self.assertEqual(scan["status"], "skipped")
        self.assertEqual(scan["surfaces_scanned"], [])
        self.assertFalse(scan["evidence_backed"])

    def test_clean_release_with_surfaces_is_evidence_backed_passed(self):
        plan = self._release_plan()
        (self.repo / "README.md").write_text("# proj\nv1.0.5 published.\n", encoding="utf-8")
        scan = scan_docs_freshness(self.repo, plan_path=plan, changed_paths=["CHANGELOG.md"])
        self.assertEqual(scan["status"], "passed")
        self.assertTrue(scan["surfaces_scanned"])
        self.assertTrue(scan["evidence_backed"])

    def test_evidence_backed_helper_rejects_bare_and_blocked_details(self):
        # passed + surfaces + no blocking => backed
        self.assertTrue(
            docs_freshness_evidence_backed(
                {"status": "passed", "surfaces_scanned": ["README.md"], "blocking_hits": []}
            )
        )
        # passed but NO surfaces => not backed (the bare-detail case)
        self.assertFalse(
            docs_freshness_evidence_backed(
                {"status": "passed", "surfaces_scanned": [], "blocking_hits": []}
            )
        )
        # passed but a blocking hit slipped in => not backed
        self.assertFalse(
            docs_freshness_evidence_backed(
                {"status": "passed", "surfaces_scanned": ["README.md"], "blocking_hits": [{"path": "x"}]}
            )
        )
        # skipped / blocked / empty are never evidence-backed passes
        self.assertFalse(docs_freshness_evidence_backed({"status": "skipped", "surfaces_scanned": ["x"]}))
        self.assertFalse(docs_freshness_evidence_backed({"status": "blocked", "surfaces_scanned": ["x"]}))
        self.assertFalse(docs_freshness_evidence_backed(None))
        self.assertFalse(docs_freshness_evidence_backed({}))

    def test_ordinary_phase_still_skipped_with_evidence_flag_false(self):
        plan = self.repo / "plan.md"
        plan.write_text("---\nphase: INT\n---\n# plan\n", encoding="utf-8")
        (self.repo / "README.md").write_text("# proj\n", encoding="utf-8")
        scan = scan_docs_freshness(self.repo, plan_path=plan, changed_paths=["src/foo.py"])
        self.assertEqual(scan["status"], "skipped")
        self.assertFalse(scan["evidence_backed"])


class AttachCloseoutIntegrationTest(unittest.TestCase):
    """The real runner path (`_attach_phase_loop_closeout`) discards a closeout
    whose `phase_loop_closeout_diagnostic` is non-None, returning the original
    `complete`. This proves the new root fields don't trip that discard and the
    block survives end-to-end at the call site the incident hit."""

    def setUp(self):
        self._review = os.environ.pop("PHASE_LOOP_REVIEW", None)
        self._fresh = os.environ.pop("PHASE_LOOP_DOCS_FRESHNESS", None)
        self._td = tempfile.TemporaryDirectory()
        self.repo = Path(self._td.name)

    def tearDown(self):
        if self._review is not None:
            os.environ["PHASE_LOOP_REVIEW"] = self._review
        if self._fresh is not None:
            os.environ["PHASE_LOOP_DOCS_FRESHNESS"] = self._fresh
        self._td.cleanup()

    def test_blocked_closeout_survives_attach(self):
        from phase_loop_runtime import runner as runner_mod

        plan = self.repo / "plan.md"
        plan.write_text(
            "---\nphase: REL\nphase_loop_mutation: release_dispatch\n---\n# plan\n",
            encoding="utf-8",
        )
        (self.repo / "README.md").write_text(
            "Message Board SHA: recovery commit pending\n", encoding="utf-8"
        )
        roadmap = self.repo / "roadmap.md"
        roadmap.write_text("# roadmap\n", encoding="utf-8")
        terminal_summary = {
            "terminal_status": "complete",
            "verification_status": "passed",
            "automation_status": "complete",
            "automation_human_required": False,
        }
        out = runner_mod._attach_phase_loop_closeout(
            repo=self.repo,
            roadmap=roadmap,
            plan=plan,
            phase="REL",
            terminal_summary=terminal_summary,
            automation={"status": "complete", "verification_status": "passed", "human_required": False},
            changed_paths=("CHANGELOG.md",),
        )
        closeout = out.get("phase_loop_closeout")
        self.assertIsNotNone(closeout, "closeout was discarded by the diagnostic gate")
        self.assertEqual(closeout["terminal_status"], "blocked")
        self.assertEqual(closeout["docs_freshness"], "blocked")
        self.assertEqual(closeout["blocker"]["blocker_class"], "docs_freshness_stale")


class ReleaseDetectionTest(unittest.TestCase):
    def test_explicit_mutation_signal(self):
        self.assertTrue(
            is_release_phase(plan_frontmatter={"phase_loop_mutation": "release_dispatch"})
        )

    def test_release_artifact_heuristic(self):
        self.assertTrue(is_release_phase(changed_paths=["packages/x/package.json"]))
        self.assertTrue(is_release_phase(changed_paths=["CHANGELOG.md"]))

    def test_ordinary_changed_paths_not_release(self):
        self.assertFalse(is_release_phase(changed_paths=["src/foo.py", "tests/test_foo.py"]))

    def test_explicit_release_predicate_requires_frontmatter(self):
        # The artifact-glob heuristic is NOT an explicit release — only
        # frontmatter is. This is the signal that gates the hard block.
        self.assertTrue(
            is_explicit_release_phase({"phase_loop_mutation": "release_dispatch"})
        )
        self.assertTrue(is_explicit_release_phase({"phase_type": "package"}))
        self.assertFalse(is_explicit_release_phase({"phase": "INT"}))
        self.assertFalse(is_explicit_release_phase(None))
        # Heuristic match is a release *shape* but not an *explicit* release.
        self.assertTrue(is_release_phase(changed_paths=["CHANGELOG.md"]))
        self.assertFalse(is_explicit_release_phase({}))


class EnumerationTest(unittest.TestCase):
    def test_enumerate_path_keyed_not_diff_keyed(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "README.md").write_text("x\n", encoding="utf-8")
            pkg = repo / "packages" / "a"
            pkg.mkdir(parents=True)
            (pkg / "README.md").write_text("y\n", encoding="utf-8")
            (repo / "CHANGELOG.md").write_text("z\n", encoding="utf-8")
            # vendored dir must be skipped
            nm = repo / "node_modules" / "dep"
            nm.mkdir(parents=True)
            (nm / "README.md").write_text("noise\n", encoding="utf-8")
            surfaces = enumerate_public_docs(repo)
            self.assertIn("README.md", surfaces)
            self.assertIn("packages/a/README.md", surfaces)
            self.assertIn("CHANGELOG.md", surfaces)
            self.assertNotIn("node_modules/dep/README.md", surfaces)


class PublicSurfaceGlobsF3Test(unittest.TestCase):
    def test_package_readme_now_public_surface(self):
        self.assertTrue(public_surface_touched(["packages/runtime/README.md"]))

    def test_top_level_readme_still_public(self):
        self.assertTrue(public_surface_touched(["README.md"]))

    def test_changelog_variants_public(self):
        self.assertTrue(public_surface_touched(["CHANGELOG.md"]))
        self.assertTrue(public_surface_touched(["CHANGELOG.rst"]))
        self.assertTrue(public_surface_touched(["RELEASE_NOTES.md"]))

    def test_internal_path_not_public(self):
        self.assertFalse(public_surface_touched(["src/internal/helper.py"]))

    def test_globs_reused_consistently(self):
        # F3 globs are present in the canonical tuple.
        self.assertIn("**/README.md", PUBLIC_SURFACE_GLOBS)


if __name__ == "__main__":
    unittest.main()
