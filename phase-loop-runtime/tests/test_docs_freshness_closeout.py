"""docs-freshness v4 P3 — release-aware closeout doc-delta gate + docs_freshness field.

Layer B is advisory: a release-class finding is `block` SEVERITY but stays
`warn`-EFFECTIVE under the default `PHASE_LOOP_REVIEW=warn` and never sets
`human_required`. The non-bypassable enforcement is the Layer A `docs-audit` CLI.
"""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.closeout import build_phase_loop_closeout
from phase_loop_runtime.closeout_validators import clear_closeout_validators, register_closeout_validator
from phase_loop_runtime.doc_delta_validator import doc_delta_validator

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _closeout(plan, changed_paths, terminal_extra=None):
    terminal = {"terminal_status": "complete", "verification_status": "passed"}
    terminal.update(terminal_extra or {})
    return build_phase_loop_closeout(
        phase_alias="P3",
        plan_path=plan,
        terminal_summary=terminal,
        automation={"status": "complete", "verification_status": "passed", "human_required": False},
        changed_paths=changed_paths,
    )


class ReleaseAwareCloseoutTest(unittest.TestCase):
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

    def test_release_surface_without_required_doc_blocks_freshness(self):
        c = _closeout(self.plan, ["pyproject.toml"])  # no CHANGELOG
        codes = [r.get("code") for r in c["verification"]["results"]]
        self.assertIn("release_doc_missing", codes)
        self.assertEqual(c["docs_freshness"]["status"], "blocked")

    def test_release_surface_with_required_doc_passes(self):
        c = _closeout(self.plan, ["pyproject.toml", "CHANGELOG.md"])
        codes = [r.get("code") for r in c["verification"]["results"]]
        self.assertNotIn("release_doc_missing", codes)
        self.assertEqual(c["docs_freshness"]["status"], "passed")

    def test_release_token_does_not_satisfy_release_class(self):
        # A recorded decision token does NOT rescue a release-class change (relevance binding).
        c = _closeout(self.plan, ["pyproject.toml"], {"doc_delta_decision": "no_doc_delta"})
        codes = [r.get("code") for r in c["verification"]["results"]]
        self.assertIn("release_doc_missing", codes)
        self.assertEqual(c["docs_freshness"]["status"], "blocked")

    def test_autonomous_release_block_is_warn_effective_never_human(self):
        # Default warn mode: the finding is recorded but the loop NEVER terminal-blocks
        # and NEVER sets human_required — Layer B is advisory.
        c = _closeout(self.plan, ["pyproject.toml"])
        self.assertEqual(c["terminal_status"], "complete")  # not blocked in-loop
        self.assertFalse(c["automation"].get("human_required", True))
        self.assertEqual(c["docs_freshness"]["status"], "blocked")  # but freshness reports it

    def test_block_mode_release_blocks_but_no_human(self):
        with patch.dict(os.environ, {"PHASE_LOOP_REVIEW": "block"}):
            c = _closeout(self.plan, ["pyproject.toml"])
        self.assertEqual(c["terminal_status"], "blocked")
        self.assertFalse(c["blocker"].get("human_required", True))

    def test_no_public_surface_is_skipped(self):
        c = _closeout(self.plan, ["src/internal/helper.py"])
        self.assertEqual(c["docs_freshness"]["status"], "skipped")

    def test_general_surface_with_decision_passes(self):
        c = _closeout(self.plan, ["README.md"], {"doc_delta_decision": "no_doc_delta"})
        self.assertEqual(c["docs_freshness"]["status"], "passed")


def _load_script(name: str):
    path = _REPO_ROOT / "phase-loop-skills" / name / "scripts" / "validate_plan_doc.py"
    if not path.is_file():
        return None
    mod_name = f"_vpd_{name.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod  # register so dataclasses resolve __module__
    spec.loader.exec_module(mod)
    return mod


class ValidatePlanDocUnderscopeTest(unittest.TestCase):
    def _check(self, name, lanes):
        mod = _load_script(name)
        if mod is None:
            self.skipTest(f"{name} script absent (standalone-from-wheel)")
        return mod._check_m_release_docs_underscope(lanes)

    def test_release_surface_no_docs_lane_warns(self):
        for name in ("plan-phase", "execute-phase"):
            findings = self._check(name, {"SL-1": {"owned_globs": ["pyproject.toml", "src/app.py"]}})
            self.assertTrue(any("(M) WARN" in f for f in findings), name)

    def test_release_surface_with_docs_lane_clean(self):
        for name in ("plan-phase", "execute-phase"):
            findings = self._check(
                name, {"SL-1": {"owned_globs": ["pyproject.toml"]}, "SL-2": {"owned_globs": ["CHANGELOG.md"]}}
            )
            self.assertEqual(findings, [], name)

    def test_no_release_surface_clean(self):
        for name in ("plan-phase", "execute-phase"):
            findings = self._check(name, {"SL-1": {"owned_globs": ["src/app.py"]}})
            self.assertEqual(findings, [], name)


if __name__ == "__main__":
    unittest.main()
