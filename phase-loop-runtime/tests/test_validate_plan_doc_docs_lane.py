"""rigor-v1 P2 — validate_plan_doc.py terminal docs-lane WARN check."""
import importlib.util
import sys
import unittest
from pathlib import Path

import pytest

from _dotfiles_tree import skills_bundle_present

# TESTDECOUPLE (#9): loads a script from the sibling phase-loop-skills/ bundle, absent in the
# standalone-from-wheel clean-room. Guard at module level (the script is loaded at import time).
if not skills_bundle_present():
    pytest.skip(
        "requires the sibling phase-loop-skills bundle (absent in the standalone-from-wheel clean-room)",
        allow_module_level=True,
    )

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "phase-loop-skills" / "plan-phase" / "scripts" / "validate_plan_doc.py"


def _load():
    spec = importlib.util.spec_from_file_location("validate_plan_doc_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve annotations via sys.modules
    spec.loader.exec_module(mod)
    return mod


class DocsLaneCheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def test_warns_when_no_docs_lane(self):
        src = "## Lanes\n\n### SL-1 — Core logic\n\n### SL-2 — Wiring\n"
        findings = self.mod._check_j_docs_lane(src)
        self.assertEqual(len(findings), 1)
        self.assertIn("WARN", findings[0])
        self.assertIn("docs", findings[0].lower())

    def test_clean_with_named_docs_lane(self):
        src = "## Lanes\n\n### SL-1 — Core logic\n\n### SL-7 — Documentation sweep\n"
        self.assertEqual(self.mod._check_j_docs_lane(src), [])

    def test_docker_lane_is_not_a_docs_lane(self):
        # "Docker" contains "doc" but is not a docs lane (review fix).
        src = "## Lanes\n\n### SL-1 — Docker image build\n\n### SL-2 — Wiring\n"
        self.assertEqual(len(self.mod._check_j_docs_lane(src)), 1)

    def test_clean_with_sl_docs_marker(self):
        src = "## Lanes\n\nThe terminal SL-docs lane depends on all others.\n"
        self.assertEqual(self.mod._check_j_docs_lane(src), [])

    def test_warn_does_not_set_error_exit(self):
        # A WARN finding must not be classified as an error (autonomy-first).
        findings = self.mod._check_j_docs_lane("### SL-1 — Core\n")
        errors = [f for f in findings if "WARN" not in f]
        self.assertEqual(errors, [])


class AcceptanceTestableCheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def test_prose_criterion_warns(self):
        src = "## Acceptance Criteria\n\n- [ ] Users can log in successfully\n"
        findings = self.mod._check_k_acceptance_testable(src)
        self.assertEqual(len(findings), 1)
        self.assertIn("WARN", findings[0])

    def test_command_backed_criterion_is_clean(self):
        src = "## Acceptance Criteria\n\n- [ ] `pytest tests/test_x.py` passes\n"
        self.assertEqual(self.mod._check_k_acceptance_testable(src), [])

    def test_http_assertion_is_clean(self):
        src = "## Acceptance Criteria\n\n- [ ] POST /api/auth returns 200 for a registered user\n"
        self.assertEqual(self.mod._check_k_acceptance_testable(src), [])

    def test_english_get_with_number_still_warns(self):
        # Lowercase "get" + a number is prose, not an HTTP assertion (review fix).
        src = "## Acceptance Criteria\n\n- [ ] User can get back to the dashboard within 200 ms\n"
        self.assertEqual(len(self.mod._check_k_acceptance_testable(src)), 1)

    def test_and_or_slash_prose_still_warns(self):
        src = "## Acceptance Criteria\n\n- [ ] Login and/or signup works\n"
        self.assertEqual(len(self.mod._check_k_acceptance_testable(src)), 1)

    def test_all_findings_are_warnings(self):
        src = "## Acceptance Criteria\n\n- [ ] It works well\n- [ ] It is robust\n"
        findings = self.mod._check_k_acceptance_testable(src)
        self.assertTrue(findings)
        self.assertEqual([f for f in findings if "WARN" not in f], [])


class UiVisualVerificationCheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def test_ui_change_without_browser_step_warns(self):
        src = "### SL-1\nOwned files: `src/components/Button.tsx`\n\n## Verification\n```\npytest tests/\n```\n"
        findings = self.mod._check_l_ui_visual_verification(src)
        self.assertEqual(len(findings), 1)
        self.assertIn("WARN", findings[0])

    def test_ui_change_with_browser_step_is_clean(self):
        src = "Owned: `app/page.tsx`\n\n## Verification\n```\nplaywright test e2e/\n```\n"
        self.assertEqual(self.mod._check_l_ui_visual_verification(src), [])

    def test_non_ui_plan_is_clean(self):
        src = "Owned: `src/runner.py`\n\n## Verification\n```\npytest tests/\n```\n"
        self.assertEqual(self.mod._check_l_ui_visual_verification(src), [])


if __name__ == "__main__":
    unittest.main()
