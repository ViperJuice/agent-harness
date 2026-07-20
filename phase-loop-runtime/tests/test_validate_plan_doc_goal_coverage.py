"""agent-harness#211 Increment 2 — validate_plan_doc.py (P) goal-ID coverage check.

When the anchored roadmap phase declares `EC-<ALIAS>-<N>` exit-criterion IDs, every
declared ID should be referenced by >=1 acceptance item and every reference should resolve
(no dangling); a plan referencing IDs against a no-ID phase is dangling; a legacy phase
with no IDs and a plan with no refs gets no finding.

check (P) uses ONLY the AUTHORITATIVE runtime parse (`phase_loop_runtime.goal_coverage` +
`roadmap_lint`) — the exact functions the goal-coverage gate calls — so the validator and
the gate can never disagree; it does NOT re-implement the parser (a divergent local parse
is worse than none for a WARN-lint). When the runtime is not importable the check is inert
but VISIBLE (an INFO line). This suite writes real files so the authoritative path runs.

Loads the GENERATED bundle validator (`phase-loop-skills/plan-phase/scripts/`), the copy
the runtime installs. Unmarked module → runs in CI.
"""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

from _dotfiles_tree import skills_bundle_present

# Loads the validator from the sibling phase-loop-skills/ bundle, which is absent in the
# standalone-from-wheel clean-room (Gate A). Guard at module level (SCRIPT is loaded at
# import time), matching test_validate_plan_doc_docs_lane.py.
if not skills_bundle_present():
    pytest.skip(
        "requires the sibling phase-loop-skills bundle (absent in the standalone-from-wheel clean-room)",
        allow_module_level=True,
    )

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "phase-loop-skills" / "plan-phase" / "scripts" / "validate_plan_doc.py"


def _load():
    spec = importlib.util.spec_from_file_location("validate_plan_doc_gc_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _roadmap(exit_items: str, heading: str = "### Phase 1 — Foo (P1)") -> str:
    return (
        "# Proj — Phase Plan v1\n\n## Phases\n\n"
        f"{heading}\n\n"
        "**Objective**\nDo the foo.\n\n"
        "**Exit criteria**\n"
        f"{exit_items}\n\n"
        "**Produces**\n- IF-0-P1-1\n\n"
        "## Verification\n- [ ] EC-P1-9 — a stray checkbox in a later section\n"
    )


_EC_EXIT = "- [ ] EC-P1-1 — `pytest a` passes\n- [ ] EC-P1-2 — `pytest b` passes"
_LEGACY_EXIT = "- [ ] `pytest a` passes"


def _plan(acceptance: str) -> str:
    return (
        "---\n"
        "phase_loop_plan_version: 1\n"
        "phase: P1\n"
        "roadmap: specs/phase-plans-v1.md\n"
        "roadmap_sha256: deadbeef\n"
        "---\n\n"
        "## Acceptance Criteria\n"
        f"{acceptance}\n"
    )


class GoalCoverageCheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def _run(self, roadmap_text: str, acceptance: str):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "specs").mkdir()
            (repo / "plans").mkdir()
            (repo / "specs" / "phase-plans-v1.md").write_text(roadmap_text, encoding="utf-8")
            src = _plan(acceptance)
            plan_path = repo / "plans" / "phase-plan-v1-p1.md"
            plan_path.write_text(src, encoding="utf-8")
            return self.mod._check_p_goal_id_coverage(src, plan_path, repo)

    def test_all_declared_ids_referenced_is_clean(self):
        findings = self._run(_roadmap(_EC_EXIT), "- [ ] EC-P1-1 — proven by `a`\n- [ ] EC-P1-2 — proven by `b`")
        self.assertEqual(findings, [])

    def test_one_item_may_reference_several_ids(self):
        # 1:many — a single proving command discharges two goals (runtime supports this).
        findings = self._run(_roadmap(_EC_EXIT), "- [ ] EC-P1-1, EC-P1-2 — proven by `pytest ab`")
        self.assertEqual(findings, [])

    def test_unreferenced_declared_id_warns(self):
        findings = self._run(_roadmap(_EC_EXIT), "- [ ] EC-P1-1 — proven by `a`")  # EC-P1-2 dropped
        self.assertEqual(len(findings), 1)
        self.assertIn("(P) WARN", findings[0])
        self.assertIn("EC-P1-2", findings[0])
        self.assertIn("not referenced", findings[0])

    def test_dangling_reference_warns(self):
        findings = self._run(
            _roadmap(_EC_EXIT),
            "- [ ] EC-P1-1 — proven by `a`\n- [ ] EC-P1-2 — proven by `b`\n- [ ] EC-P1-7 — proven by `c`",
        )
        self.assertEqual(len(findings), 1)
        self.assertIn("dangling", findings[0])
        self.assertIn("EC-P1-7", findings[0])

    def test_comma_suffixed_heading_still_finds_declared_ids(self):
        # Runtime-accepted heading form `(P1, owner team)` must not be treated as legacy.
        findings = self._run(
            _roadmap(_EC_EXIT, heading="### Phase 1 — Foo (P1, owner team)"),
            "- [ ] EC-P1-1 — proven by `a`",  # EC-P1-2 dropped -> must still warn
        )
        self.assertEqual(len(findings), 1)
        self.assertIn("EC-P1-2", findings[0])

    def test_stray_ec_checkbox_in_later_section_not_counted_as_declared(self):
        # The `## Verification` section's EC-P1-9 checkbox must NOT inflate `declared`
        # (phase body ends at the next `## ` heading).
        findings = self._run(_roadmap(_EC_EXIT), "- [ ] EC-P1-1 — proven by `a`\n- [ ] EC-P1-2 — proven by `b`")
        self.assertEqual(findings, [])  # EC-P1-9 is not a declared goal of P1

    def test_legacy_phase_no_refs_no_finding(self):
        findings = self._run(_roadmap(_LEGACY_EXIT), "- [ ] `pytest a` passes")
        self.assertEqual(findings, [])

    def test_legacy_phase_with_ec_refs_is_dangling(self):
        # A plan that references goal IDs against a phase declaring NONE is a dangling
        # contract bug (mirrors the runtime), not a silent pass.
        findings = self._run(_roadmap(_LEGACY_EXIT), "- [ ] EC-P1-1 — proven by `a`")
        self.assertEqual(len(findings), 1)
        self.assertIn("dangling", findings[0])
        self.assertIn("EC-P1-1", findings[0])

    def test_missing_roadmap_is_inert(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "plans").mkdir()
            src = _plan("- [ ] EC-P1-1 — proven by `a`")
            plan_path = repo / "plans" / "phase-plan-v1-p1.md"
            plan_path.write_text(src, encoding="utf-8")
            self.assertEqual(self.mod._check_p_goal_id_coverage(src, plan_path, repo), [])

    def test_phase_body_with_subheading_still_finds_declared_ids(self):
        # codex CR probe: a `### ` subheading INSIDE the phase (before Exit criteria) must
        # not truncate the phase body — the runtime parses the IDs, so check (P) must too
        # (it uses the runtime parse, no divergent local scan that would break on `### `).
        roadmap = (
            "# Proj — Phase Plan v1\n\n## Phases\n\n"
            "### Phase 1 — Foo (P1)\n\n"
            "**Objective**\nDo the foo.\n\n"
            "### Notes\nsome notes inside the phase.\n\n"
            "**Exit criteria**\n"
            "- [ ] EC-P1-1 — `a`\n- [ ] EC-P1-2 — `b`\n\n"
            "## Verification\n- [ ] EC-P1-9 — a stray checkbox in a later section\n"
        )
        # both declared IDs referenced -> clean; drop one -> the unreferenced one warns.
        self.assertEqual(self._run(roadmap, "- [ ] EC-P1-1 — proven by `a`\n- [ ] EC-P1-2 — proven by `b`"), [])
        findings = self._run(roadmap, "- [ ] EC-P1-1 — proven by `a`")
        self.assertEqual(len(findings), 1)
        self.assertIn("EC-P1-2", findings[0])

    def test_duplicate_phase_alias_is_un_auditable(self):
        # Two phases with the same alias — the runtime gate fails closed (duplicate_phase_alias);
        # check (P) must not compute a plausible coverage verdict, it flags un-auditable.
        roadmap = (
            "# Proj — Phase Plan v1\n\n## Phases\n\n"
            "### Phase 1 — Foo (P1)\n\n**Objective**\no.\n\n**Exit criteria**\n- [ ] EC-P1-1 — `a`\n\n"
            "### Phase 2 — Bar (P1)\n\n**Objective**\no.\n\n**Exit criteria**\n- [ ] EC-P1-2 — `b`\n"
        )
        findings = self._run(roadmap, "- [ ] EC-P1-1 — proven by `a`")
        self.assertEqual(len(findings), 1)
        self.assertIn("un-auditable", findings[0])
        self.assertIn("(P) WARN", findings[0])


if __name__ == "__main__":
    unittest.main()
