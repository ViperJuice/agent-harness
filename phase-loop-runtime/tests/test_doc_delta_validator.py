"""rigor-v1 P2 — doc-delta closeout validator."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.closeout import build_phase_loop_closeout
from phase_loop_runtime.closeout_validators import clear_closeout_validators, register_closeout_validator
from phase_loop_runtime.doc_delta_validator import doc_delta_validator


def _closeout(plan, changed_paths, terminal_extra=None):
    terminal = {"terminal_status": "complete", "verification_status": "passed"}
    terminal.update(terminal_extra or {})
    return build_phase_loop_closeout(
        phase_alias="P2",
        plan_path=plan,
        terminal_summary=terminal,
        automation={"status": "complete", "verification_status": "passed", "human_required": False},
        changed_paths=changed_paths,
    )


class DocDeltaValidatorTest(unittest.TestCase):
    def setUp(self):
        clear_closeout_validators()
        register_closeout_validator(doc_delta_validator)
        self._td = tempfile.TemporaryDirectory()
        self.plan = Path(self._td.name) / "plan.md"
        self.plan.write_text("# plan\n", encoding="utf-8")
        self._review = os.environ.pop("PHASE_LOOP_REVIEW", None)

    def tearDown(self):
        clear_closeout_validators()
        if self._review is not None:
            os.environ["PHASE_LOOP_REVIEW"] = self._review
        self._td.cleanup()

    def test_non_public_change_no_finding(self):
        c = _closeout(self.plan, ["src/internal/helper.py"])
        self.assertEqual(c["terminal_status"], "complete")
        self.assertFalse(c["verification"]["results"])

    def test_public_change_without_decision_records_finding_but_warns(self):
        c = _closeout(self.plan, ["phase_loop_runtime/cli.py"])
        self.assertEqual(c["terminal_status"], "complete")  # warn default never blocks
        codes = [r.get("code") for r in c["verification"]["results"]]
        self.assertIn("doc_delta_undecided", codes)

    def test_public_change_with_recorded_decision_is_clean(self):
        c = _closeout(self.plan, ["README.md"], {"doc_delta_decision": "no_doc_delta"})
        self.assertEqual(c["terminal_status"], "complete")
        self.assertFalse(c["verification"]["results"])

    def test_public_change_blocks_in_block_mode(self):
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            c = _closeout(self.plan, ["phase_loop_runtime/cli.py"])
        self.assertEqual(c["terminal_status"], "blocked")
        self.assertEqual(c["blocker"]["blocker_class"], "review_gate_block")
        self.assertFalse(c["blocker"].get("human_required", True))

    def test_block_mode_with_decision_is_clean(self):
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            c = _closeout(self.plan, ["README.md"], {"doc_delta_decision": "docs_updated"})
        self.assertEqual(c["terminal_status"], "complete")


class DocDeltaCorroborationF5Test(unittest.TestCase):
    """issue #18 F5: a self-attested `no_doc_delta` on a release phase is
    corroborated against the path-keyed docs-freshness scan evidence."""

    def setUp(self):
        clear_closeout_validators()
        register_closeout_validator(doc_delta_validator)
        self._td = tempfile.TemporaryDirectory()
        self.plan = Path(self._td.name) / "plan.md"
        self.plan.write_text("# plan\n", encoding="utf-8")
        self._review = os.environ.pop("PHASE_LOOP_REVIEW", None)

    def tearDown(self):
        clear_closeout_validators()
        if self._review is not None:
            os.environ["PHASE_LOOP_REVIEW"] = self._review
        self._td.cleanup()

    def _closeout_with_scan(self, changed_paths, decision, scan):
        return build_phase_loop_closeout(
            phase_alias="REL",
            plan_path=self.plan,
            terminal_summary={
                "terminal_status": "complete",
                "verification_status": "passed",
                "doc_delta_decision": decision,
            },
            automation={"status": "complete", "verification_status": "passed", "human_required": False},
            changed_paths=changed_paths,
            docs_freshness=scan,
        )

    @staticmethod
    def _scan(status, *, is_release, surfaces, blocking=()):
        return {
            "status": status,
            "mode": "hard",
            "is_release_phase": is_release,
            "explicit_release": is_release,
            "surfaces_scanned": list(surfaces),
            "hits": [],
            "blocking_hits": list(blocking),
            "evidence_backed": status == "passed" and bool(surfaces) and not blocking,
        }

    def test_release_no_doc_delta_corroborated_by_passed_scan_is_clean(self):
        # `no_doc_delta` + a freshness scan that enumerated surfaces and passed =
        # the claim is provable. No finding.
        scan = self._scan("passed", is_release=True, surfaces=["README.md", "CHANGELOG.md"])
        c = self._closeout_with_scan(["README.md"], "no_doc_delta", scan)
        self.assertEqual(c["terminal_status"], "complete")
        codes = [r.get("code") for r in c["verification"]["results"]]
        self.assertNotIn("doc_delta_uncorroborated", codes)

    def test_release_no_doc_delta_uncorroborated_empty_scan_is_warn(self):
        # `no_doc_delta` but the scan enumerated NO surfaces (bare detail) — the
        # claim is unverified. Downgraded to a recorded WARN, never a block.
        scan = self._scan("skipped", is_release=True, surfaces=[])
        c = self._closeout_with_scan(["README.md"], "no_doc_delta", scan)
        self.assertEqual(c["terminal_status"], "complete")  # warn never blocks
        results = c["verification"]["results"]
        warn = next(r for r in results if r.get("code") == "doc_delta_uncorroborated")
        self.assertEqual(warn["severity"], "warn")

    def test_uncorroborated_no_doc_delta_does_not_block_even_in_block_mode(self):
        # The corroboration downgrade is autonomy-first: even under
        # PHASE_LOOP_REVIEW=block it records a warn, never halts the fleet.
        scan = self._scan("skipped", is_release=True, surfaces=[])
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            c = self._closeout_with_scan(["README.md"], "no_doc_delta", scan)
        self.assertEqual(c["terminal_status"], "complete")

    def test_ordinary_phase_no_doc_delta_is_clean(self):
        # Non-release phase: a self-attested no_doc_delta for an internal change
        # is fine; the freshness scan reports skipped there anyway. No finding.
        scan = self._scan("skipped", is_release=False, surfaces=[])
        c = self._closeout_with_scan(["README.md"], "no_doc_delta", scan)
        self.assertEqual(c["terminal_status"], "complete")
        codes = [r.get("code") for r in c["verification"]["results"]]
        self.assertNotIn("doc_delta_uncorroborated", codes)

    def test_no_scan_threaded_is_satisfied(self):
        # Unwired scan (docs_freshness=None) => no corroboration available =>
        # the literal still satisfies, as before F5. Never newly-fail.
        c = build_phase_loop_closeout(
            phase_alias="REL",
            plan_path=self.plan,
            terminal_summary={
                "terminal_status": "complete",
                "verification_status": "passed",
                "doc_delta_decision": "no_doc_delta",
            },
            automation={"status": "complete", "verification_status": "passed", "human_required": False},
            changed_paths=["README.md"],
        )
        self.assertEqual(c["terminal_status"], "complete")
        codes = [r.get("code") for r in c["verification"]["results"]]
        self.assertNotIn("doc_delta_uncorroborated", codes)

    def test_release_docs_updated_decision_passes_through(self):
        # Only `no_doc_delta` is corroborated; `docs_updated` evidences a real
        # change and is never downgraded, even with an empty scan.
        scan = self._scan("skipped", is_release=True, surfaces=[])
        c = self._closeout_with_scan(["README.md"], "docs_updated", scan)
        self.assertEqual(c["terminal_status"], "complete")
        codes = [r.get("code") for r in c["verification"]["results"]]
        self.assertNotIn("doc_delta_uncorroborated", codes)


if __name__ == "__main__":
    unittest.main()
