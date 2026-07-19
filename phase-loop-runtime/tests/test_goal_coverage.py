"""agent-harness#211: decidable goal-coverage check + EC-ID roadmap reconciliation."""

import os
import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime import discovery
from phase_loop_runtime.goal_coverage import check_goal_coverage, extract_plan_goal_refs
from phase_loop_runtime.roadmap_lint import _extract_phases, lint_roadmap_text


def _roadmap_text(exit_lines: list[str], alias: str = "P1") -> str:
    ec = "\n".join(f"- [ ] {c}" for c in exit_lines)
    return (
        f"# Roadmap\n\n## Context\nx\n\n## Phases\n\n### Phase 1 — Audio ({alias})\n\n"
        f"**Objective**\nx\n\n**Exit criteria**\n{ec}\n\n**Key files**\n- `x.py`\n\n"
        f"**Scope notes**\ndecompose into 2 lanes\n\n**Depends on**\n- (none)\n\n"
        f"## Top Interface-Freeze Gates\n\n## Phase Dependency DAG\n{alias}\n\n"
        f"## Execution Notes\nx\n\n## Verification\nx\n"
    )


def _build(td: Path, exit_lines, acc_lines, *, alias="P1", plan_alias=None, break_sha=False):
    rm = td / "specs" / "phase-plans-v1.md"
    rm.parent.mkdir(parents=True, exist_ok=True)
    rm.write_text(_roadmap_text(exit_lines, alias), encoding="utf-8")
    sha = "0" * 64 if break_sha else discovery.roadmap_sha256(rm)
    rel = discovery.roadmap_repo_relative_path(td, rm)
    plan = td / "plan.md"
    accb = "\n".join(f"- [ ] {a}" for a in acc_lines)
    plan.write_text(
        f'---\nphase_loop_plan_version: "1"\nphase: {plan_alias or alias}\n'
        f"roadmap: {rel}\nroadmap_sha256: {sha}\n---\n# Plan\n\n## Acceptance Criteria\n{accb}\n",
        encoding="utf-8",
    )
    return td, plan


class RoadmapECIdTest(unittest.TestCase):
    def test_parse_ids_and_api_compat(self):
        ph = _extract_phases(_roadmap_text(["EC-P1-1 — a", "EC-P1-3 — b"]))[0]
        # gap at EC-P1-2 is allowed (never-renumber)
        self.assertEqual(ph.declared_exit_criteria_ids, ["EC-P1-1", "EC-P1-3"])
        # API-compat: exit_criteria stays list[str]
        self.assertIsInstance(ph.exit_criteria, list)
        self.assertTrue(all(isinstance(c, str) for c in ph.exit_criteria))

    def test_mixed_mode_is_error(self):
        errs = [e for e in lint_roadmap_text(_roadmap_text(["EC-P1-1 — a", "bare goal"])) if e.startswith("(H)")]
        self.assertTrue(any("mixed exit-criteria" in e for e in errs))

    def test_all_ids_or_all_bare_is_clean(self):
        for lines in (["EC-P1-1 — a", "EC-P1-2 — b"], ["bare a", "bare b"]):
            self.assertEqual([e for e in lint_roadmap_text(_roadmap_text(lines)) if e.startswith("(H)")], [])

    def test_alias_mismatch_and_duplicate_are_errors(self):
        errs = [e for e in lint_roadmap_text(_roadmap_text(["EC-P2-1 — a", "EC-P1-1 — b"])) if e.startswith("(H)")]
        self.assertTrue(any("names alias 'P2'" in e for e in errs))
        errs = [e for e in lint_roadmap_text(_roadmap_text(["EC-P1-1 — a", "EC-P1-1 — b"])) if e.startswith("(H)")]
        self.assertTrue(any("duplicate goal ID" in e for e in errs))


class GoalCoverageTest(unittest.TestCase):
    def _cov(self, ex, ac, **kw):
        with tempfile.TemporaryDirectory() as t:
            repo, plan = _build(Path(t), ex, ac, **kw)
            return check_goal_coverage(repo, plan)

    def test_all_referenced_clean(self):
        r = self._cov(["EC-P1-1 — a", "EC-P1-3 — b"], ["EC-P1-1 — proven by t1", "EC-P1-3 — proven by t2"])
        self.assertTrue(r.is_clean())
        self.assertTrue(r.applicable)

    def test_dropped_goal_is_gap(self):
        r = self._cov(["EC-P1-1 — a", "EC-P1-3 — b"], ["EC-P1-1 — proven by t1"])
        self.assertTrue(r.has_gaps())
        self.assertEqual(r.unreferenced_ids, ("EC-P1-3",))

    def test_one_to_many_item_references_two_ids(self):
        r = self._cov(["EC-P1-1 — a", "EC-P1-2 — b"], ["EC-P1-1, EC-P1-2 — proven by t_both"])
        self.assertTrue(r.is_clean())

    def test_prose_mention_does_not_count(self):
        r = self._cov(["EC-P1-1 — a"], ["NOTE: EC-P1-1 was deferred to a later phase"])
        self.assertTrue(r.has_gaps())
        self.assertEqual(r.unreferenced_ids, ("EC-P1-1",))

    def test_dangling_ref_is_gap(self):
        r = self._cov(["EC-P1-1 — a"], ["EC-P1-1 — ok", "EC-P1-9 — proven by t"])
        self.assertTrue(r.has_gaps())
        self.assertEqual(r.dangling_refs, ("EC-P1-9",))

    def test_legacy_no_ids_is_not_applicable(self):
        r = self._cov(["a bare goal"], ["did the work"])
        self.assertTrue(r.not_applicable())
        self.assertFalse(r.has_gaps())

    def test_stale_sha_is_setup_error(self):
        r = self._cov(["EC-P1-1 — a"], ["EC-P1-1 — t"], break_sha=True)
        self.assertTrue(r.has_setup_errors())
        self.assertFalse(r.has_gaps())

    def test_alias_not_found_is_setup_error(self):
        r = self._cov(["EC-P1-1 — a"], ["EC-P1-1 — t"], alias="P1", plan_alias="NOPE")
        self.assertTrue(r.has_setup_errors())

    def test_extract_refs_only_from_acceptance_section(self):
        with tempfile.TemporaryDirectory() as t:
            repo, plan = _build(Path(t), ["EC-P1-1 — a"], ["EC-P1-1 — ok"])
            # a prose mention of EC-P1-2 OUTSIDE the acceptance section must not count
            plan.write_text(plan.read_text() + "\n## Notes\nWe considered EC-P1-2 but skipped it.\n", encoding="utf-8")
            self.assertEqual(extract_plan_goal_refs(plan), {"EC-P1-1"})

    def test_closeout_mutation_window_caught(self):
        # preflight passes on a plan that references the goal; then the reference is
        # DELETED mid-execution; a re-check on the mutated plan gaps (what the closeout
        # re-check does).
        with tempfile.TemporaryDirectory() as t:
            repo, plan = _build(Path(t), ["EC-P1-1 — a"], ["EC-P1-1 — proven by t"])
            self.assertTrue(check_goal_coverage(repo, plan).is_clean())
            plan.write_text(plan.read_text().replace("- [ ] EC-P1-1 — proven by t", "- [ ] did some work"), encoding="utf-8")
            self.assertTrue(check_goal_coverage(repo, plan).has_gaps())


class GoalCoveragePreflightTest(unittest.TestCase):
    def _blocker(self, enforce):
        from phase_loop_runtime.runner import _execute_goal_coverage_preflight

        with tempfile.TemporaryDirectory() as t:
            repo, plan = _build(Path(t), ["EC-P1-1 — a", "EC-P1-2 — b"], ["EC-P1-1 — proven by t"])
            roadmap = repo / "specs" / "phase-plans-v1.md"
            old = os.environ.get("PHASE_LOOP_ACCEPTANCE_ENFORCE")
            try:
                if enforce is None:
                    os.environ.pop("PHASE_LOOP_ACCEPTANCE_ENFORCE", None)
                else:
                    os.environ["PHASE_LOOP_ACCEPTANCE_ENFORCE"] = enforce
                return _execute_goal_coverage_preflight(repo, roadmap, plan)
            finally:
                if old is None:
                    os.environ.pop("PHASE_LOOP_ACCEPTANCE_ENFORCE", None)
                else:
                    os.environ["PHASE_LOOP_ACCEPTANCE_ENFORCE"] = old

    def test_warn_default_does_not_block(self):
        self.assertIsNone(self._blocker(None))

    def test_enforce_block_blocks_without_human_required(self):
        blocker = self._blocker("block")
        self.assertIsNotNone(blocker)
        self.assertFalse(blocker["human_required"])
        self.assertEqual(blocker["blocker_class"], "contract_bug")


if __name__ == "__main__":
    unittest.main()
