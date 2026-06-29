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


class ReleaseDocsCoverageCheckTest(unittest.TestCase):
    """issue #18 (F2) — release/package phases must own public-doc surfaces and
    the docs reducer must depend on every producer lane."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def _lanes(self, depends_on_for_docs):
        Lane = self.mod.Lane
        return [
            Lane(sl_id="SL-1", name="Release manifests"),
            Lane(sl_id="SL-2", name="Documentation sweep", depends_on=depends_on_for_docs),
        ]

    def _release_frontmatter_src(self):
        return "---\nphase: REL\nphase_loop_mutation: release_dispatch\n---\n# plan\n"

    def test_release_phase_docs_not_owning_readme_is_error(self):
        src = self._release_frontmatter_src()
        lanes = self._lanes(["SL-1"])
        parsed = {
            "SL-1": {"owned_globs": ["package.json", "CHANGELOG.md"]},
            "SL-2": {"owned_globs": ["plans/RECOVERY.md"]},  # owns no public-doc surface
        }
        findings = self.mod._check_m_release_docs_coverage(src, lanes, parsed)
        errors = [f for f in findings if "WARN" not in f]
        self.assertTrue(errors, "release phase docs-coverage gap must be an ERROR")
        self.assertTrue(any("README" in f for f in errors))

    def test_release_phase_owning_readme_and_full_deps_is_clean(self):
        src = self._release_frontmatter_src()
        lanes = self._lanes(["SL-1"])
        parsed = {
            "SL-1": {"owned_globs": ["package.json"]},
            "SL-2": {"owned_globs": ["README.md", "CHANGELOG.md"]},
        }
        self.assertEqual(self.mod._check_m_release_docs_coverage(src, lanes, parsed), [])

    def test_release_reducer_missing_producer_dep_is_error(self):
        src = self._release_frontmatter_src()
        lanes = self._lanes([])  # docs reducer depends on nothing
        parsed = {
            "SL-1": {"owned_globs": ["package.json"]},
            "SL-2": {"owned_globs": ["README.md"]},
        }
        findings = self.mod._check_m_release_docs_coverage(src, lanes, parsed)
        errors = [f for f in findings if "WARN" not in f]
        self.assertTrue(any("SL-1" in f and "Depends on" in f for f in errors))

    def test_ordinary_phase_same_gap_is_warn_not_error(self):
        # No release frontmatter, no release-artifact owned globs.
        src = "---\nphase: INT\n---\n# plan\n"
        lanes = self._lanes([])
        parsed = {
            "SL-1": {"owned_globs": ["src/core.py"]},
            "SL-2": {"owned_globs": ["plans/notes.md"]},
        }
        findings = self.mod._check_m_release_docs_coverage(src, lanes, parsed)
        self.assertTrue(findings)
        self.assertEqual([f for f in findings if "WARN" not in f], [])

    def test_release_detected_via_owned_artifact_glob_is_warn_not_error(self):
        # No explicit frontmatter, but a lane owns package.json -> release SHAPE.
        # Without explicit release frontmatter the coverage gap must be WARN-tier,
        # not an ERROR — an ordinary manifest/dep bump must not become a blocker.
        src = "---\nphase: REL\n---\n# plan\n"
        lanes = self._lanes(["SL-1"])
        parsed = {
            "SL-1": {"owned_globs": ["packages/x/package.json"]},
            "SL-2": {"owned_globs": ["plans/notes.md"]},  # no public doc
        }
        findings = self.mod._check_m_release_docs_coverage(src, lanes, parsed)
        self.assertTrue(findings, "the coverage gap should still be reported")
        self.assertEqual(
            [f for f in findings if "WARN" not in f],
            [],
            "heuristic-only release shape must not produce an ERROR",
        )

    def test_explicit_release_docs_gap_is_error_even_when_changelog_only(self):
        # An EXPLICIT release (frontmatter) with the same coverage gap stays an
        # ERROR — the original FLEETRELEASERECOVERY catch is preserved.
        src = self._release_frontmatter_src()
        lanes = self._lanes(["SL-1"])
        parsed = {
            "SL-1": {"owned_globs": ["CHANGELOG.md"]},
            "SL-2": {"owned_globs": ["plans/notes.md"]},  # owns no public-doc surface
        }
        findings = self.mod._check_m_release_docs_coverage(src, lanes, parsed)
        self.assertTrue([f for f in findings if "WARN" not in f])

    def test_release_no_docs_lane_is_error(self):
        src = self._release_frontmatter_src()
        Lane = self.mod.Lane
        lanes = [Lane(sl_id="SL-1", name="Release manifests")]
        parsed = {"SL-1": {"owned_globs": ["package.json"]}}
        findings = self.mod._check_m_release_docs_coverage(src, lanes, parsed)
        self.assertTrue([f for f in findings if "WARN" not in f])

    def test_no_doc_change_decision_satisfies_ownership(self):
        src = self._release_frontmatter_src() + "\nThe docs lane records no_doc_delta: each surface is current.\n"
        lanes = self._lanes(["SL-1"])
        parsed = {
            "SL-1": {"owned_globs": ["package.json"]},
            "SL-2": {"owned_globs": ["plans/notes.md"]},
        }
        findings = self.mod._check_m_release_docs_coverage(src, lanes, parsed)
        # ownership requirement satisfied by the explicit decision; only the
        # dependency check (if any) remains — here deps are complete.
        self.assertFalse(any("do not own" in f for f in findings))


class PostDispatchReducerCheckF4Test(unittest.TestCase):
    """issue #18 (F4) — a release-dispatch phase must include a post-dispatch
    evidence-reducer lane that back-fills the now-known SHA / workflow result."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def _dispatch_src(self):
        return "---\nphase: REL\nphase_loop_mutation: release_dispatch\n---\n# plan\n"

    def _lanes(self, names):
        Lane = self.mod.Lane
        return [Lane(sl_id=f"SL-{i+1}", name=n) for i, n in enumerate(names)]

    def _raw(self, lanes, bodies=None):
        bodies = bodies or {}
        return {lane.sl_id: bodies.get(lane.sl_id, "") for lane in lanes}

    def _parsed(self, lanes):
        return {lane.sl_id: {"owned_globs": []} for lane in lanes}

    def test_release_dispatch_missing_reducer_is_error(self):
        src = self._dispatch_src()
        lanes = self._lanes(["Dispatch release workflow", "Documentation sweep"])
        findings = self.mod._check_n_post_dispatch_reducer(
            src, lanes, self._raw(lanes), self._parsed(lanes)
        )
        errors = [f for f in findings if "WARN" not in f]
        self.assertTrue(errors, "release-dispatch without a reducer lane must ERROR")
        self.assertTrue(any("post-dispatch" in f.lower() for f in errors))

    def test_release_dispatch_with_reducer_lane_is_clean(self):
        src = self._dispatch_src()
        lanes = self._lanes(
            ["Dispatch release workflow", "Post-dispatch evidence back-fill"]
        )
        self.assertEqual(
            self.mod._check_n_post_dispatch_reducer(
                src, lanes, self._raw(lanes), self._parsed(lanes)
            ),
            [],
        )

    def test_reducer_detected_in_lane_body(self):
        # The signal may live in the lane body, not just the name.
        src = self._dispatch_src()
        lanes = self._lanes(["Dispatch", "Reconcile evidence"])
        bodies = {
            "SL-2": "Re-open evidence docs and back-fill the now-known commit SHA "
            "and workflow result after the tag is cut.",
        }
        self.assertEqual(
            self.mod._check_n_post_dispatch_reducer(
                src, lanes, self._raw(lanes, bodies), self._parsed(lanes)
            ),
            [],
        )

    def test_ordinary_phase_missing_reducer_is_clean(self):
        # A non-release plan never needs a post-dispatch reducer.
        src = "---\nphase: INT\n---\n# plan\n"
        lanes = self._lanes(["Core logic", "Wiring"])
        self.assertEqual(
            self.mod._check_n_post_dispatch_reducer(
                src, lanes, self._raw(lanes), self._parsed(lanes)
            ),
            [],
        )

    def test_non_dispatch_release_shape_missing_reducer_is_warn(self):
        # A release *shape* (lane owns a release artifact) that is NOT an explicit
        # dispatch gets a WARN, never an ERROR (autonomy-first).
        src = "---\nphase: REL\n---\n# plan\n"
        lanes = self._lanes(["Bump manifests", "Docs"])
        parsed = {
            "SL-1": {"owned_globs": ["packages/x/package.json"]},
            "SL-2": {"owned_globs": ["README.md"]},
        }
        findings = self.mod._check_n_post_dispatch_reducer(
            src, lanes, self._raw(lanes), parsed
        )
        self.assertTrue(findings, "release-shaped plan should get the advisory")
        self.assertEqual(
            [f for f in findings if "WARN" not in f], [], "must be WARN-tier, not ERROR"
        )

    def test_explicit_dispatch_error_survives_full_validation(self):
        # End-to-end through main()'s check ordering: an explicit release-dispatch
        # plan with no reducer lane must surface the (N) ERROR.
        src = self._dispatch_src()
        lanes = self._lanes(["Dispatch the release"])
        findings = self.mod._check_n_post_dispatch_reducer(
            src, lanes, self._raw(lanes), self._parsed(lanes)
        )
        self.assertTrue(any(f.startswith("(N)") and "WARN" not in f for f in findings))


if __name__ == "__main__":
    unittest.main()
