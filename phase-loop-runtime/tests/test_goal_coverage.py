"""agent-harness#211: decidable goal-coverage check + EC-ID roadmap reconciliation."""
import re

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

    def test_mixed_roadmap_is_setup_error_at_runtime(self):
        # CR codex round 3: a mixed roadmap (some criteria ID'd, some bare) must fail
        # CLOSED at the runtime gate, not silently omit the bare goal.
        r = self._cov(["EC-P1-1 — a", "bare goal b"], ["EC-P1-1 — proven by t"])
        self.assertTrue(r.has_setup_errors())
        self.assertFalse(r.has_gaps())

    def test_duplicate_or_wrong_alias_id_is_setup_error_at_runtime(self):
        # CR codex round 4: the runtime gate must run the FULL reconciliation, not just
        # mixed-mode. A duplicate ID (two goals collapsing to one) or a wrong-alias ID
        # must fail closed, not silently omit a goal.
        r = self._cov(["EC-P1-1 — a", "EC-P1-1 — b"], ["EC-P1-1 — proven by t"])
        self.assertTrue(r.has_setup_errors())
        self.assertFalse(r.is_clean())
        r = self._cov(["EC-P2-1 — a", "EC-P1-2 — b"], ["EC-P2-1 — t", "EC-P1-2 — t"])
        self.assertTrue(r.has_setup_errors())

    def test_duplicate_phase_alias_is_setup_error(self):
        # CR codex round 5: a roadmap with two phases aliased P1 must fail closed at the
        # runtime gate, not silently pick the first and exclude the second's goals.
        with tempfile.TemporaryDirectory() as t:
            repo, plan = _build(Path(t), ["EC-P1-1 — a"], ["EC-P1-1 — proven by t"])
            rm = repo / "specs" / "phase-plans-v1.md"
            # append a SECOND phase also aliased P1
            rm.write_text(
                rm.read_text().replace(
                    "## Top Interface-Freeze Gates",
                    "### Phase 2 — Dup (P1)\n\n**Objective**\ny\n\n**Exit criteria**\n- [ ] EC-P1-5 — c\n\n"
                    "**Scope notes**\n1 lane\n\n**Key files**\n- `y.py`\n\n**Depends on**\n- (none)\n\n## Top Interface-Freeze Gates",
                    1,
                ),
                encoding="utf-8",
            )
            # re-pin the plan's roadmap_sha256 to the amended roadmap so the anchor passes
            new_sha = discovery.roadmap_sha256(rm)
            plan.write_text(re.sub(r"roadmap_sha256: \w+", f"roadmap_sha256: {new_sha}", plan.read_text()), encoding="utf-8")
            r = check_goal_coverage(repo, plan)
            self.assertTrue(r.has_setup_errors())
            self.assertTrue(any("duplicate_phase_alias" in d for d in r.setup_diagnostics))

    def test_dangling_ref_on_legacy_phase_is_gap(self):
        # CR codex round 3: a plan referencing a goal ID against a legacy (no-ID) phase
        # is a dangling gap, not a silent pass.
        r = self._cov(["a bare goal"], ["EC-P1-99 — proven by t"])
        self.assertTrue(r.has_gaps())
        self.assertEqual(r.dangling_refs, ("EC-P1-99",))
        self.assertFalse(r.not_applicable())

    def test_stale_sha_is_setup_error(self):
        r = self._cov(["EC-P1-1 — a"], ["EC-P1-1 — t"], break_sha=True)
        self.assertTrue(r.has_setup_errors())
        self.assertFalse(r.has_gaps())

    def test_alias_not_found_is_setup_error(self):
        r = self._cov(["EC-P1-1 — a"], ["EC-P1-1 — t"], alias="P1", plan_alias="NOPE")
        self.assertTrue(r.has_setup_errors())

    def test_checkbox_prefix_tolerant_of_missing_space_and_uppercase(self):
        # CR Fable: `- [ ]EC-P1-1` (no space) and `- [X]` must still parse the ref.
        with tempfile.TemporaryDirectory() as t:
            repo, plan = _build(Path(t), ["EC-P1-1 — a", "EC-P1-2 — b"], ["placeholder"])
            plan.write_text(
                plan.read_text().replace(
                    "- [ ] placeholder",
                    "- [ ]EC-P1-1 — proven by t1\n- [X] EC-P1-2 — proven by t2",
                ),
                encoding="utf-8",
            )
            self.assertEqual(extract_plan_goal_refs(plan), {"EC-P1-1", "EC-P1-2"})
            self.assertTrue(check_goal_coverage(repo, plan).is_clean())

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

    def test_setup_error_fails_closed_under_enforce(self):
        # CR codex/Fable: an un-auditable plan (stale roadmap_sha256) must NOT silently
        # pass the preflight gate under enforcement — it fails closed.
        from phase_loop_runtime.runner import _execute_goal_coverage_preflight

        old = os.environ.get("PHASE_LOOP_ACCEPTANCE_ENFORCE")
        try:
            with tempfile.TemporaryDirectory() as t:
                repo, plan = _build(Path(t), ["EC-P1-1 — a"], ["EC-P1-1 — proven by t"], break_sha=True)
                roadmap = repo / "specs" / "phase-plans-v1.md"
                os.environ["PHASE_LOOP_ACCEPTANCE_ENFORCE"] = "block"
                blocker = _execute_goal_coverage_preflight(repo, roadmap, plan)
                self.assertIsNotNone(blocker)  # setup error -> blocked under enforce
                self.assertFalse(blocker["human_required"])
                # warn-default: the same setup error does NOT block
                os.environ.pop("PHASE_LOOP_ACCEPTANCE_ENFORCE", None)
                self.assertIsNone(_execute_goal_coverage_preflight(repo, roadmap, plan))
        finally:
            if old is None:
                os.environ.pop("PHASE_LOOP_ACCEPTANCE_ENFORCE", None)
            else:
                os.environ["PHASE_LOOP_ACCEPTANCE_ENFORCE"] = old


class GoalCoverageCloseoutTest(unittest.TestCase):
    """CR codex round 2: the closeout re-check must fail closed under enforce on a gap,
    a setup error, AND an audit exception — never a silent pass."""

    def _run_closeout(self, ex, ac, enforce, *, is_complete=True, patch_exc=False, **kw):
        from unittest.mock import patch as mpatch

        from phase_loop_runtime.runner import _goal_coverage_closeout_outcome

        old = os.environ.get("PHASE_LOOP_ACCEPTANCE_ENFORCE")
        try:
            if enforce is None:
                os.environ.pop("PHASE_LOOP_ACCEPTANCE_ENFORCE", None)
            else:
                os.environ["PHASE_LOOP_ACCEPTANCE_ENFORCE"] = enforce
            with tempfile.TemporaryDirectory() as t:
                repo, plan = _build(Path(t), ex, ac, **kw)
                roadmap = repo / "specs" / "phase-plans-v1.md"
                if patch_exc:
                    with mpatch("phase_loop_runtime.goal_coverage.check_goal_coverage", side_effect=RuntimeError("boom")):
                        return _goal_coverage_closeout_outcome(repo, roadmap, plan, is_complete)
                return _goal_coverage_closeout_outcome(repo, roadmap, plan, is_complete)
        finally:
            if old is None:
                os.environ.pop("PHASE_LOOP_ACCEPTANCE_ENFORCE", None)
            else:
                os.environ["PHASE_LOOP_ACCEPTANCE_ENFORCE"] = old

    def test_gap_blocks_under_enforce_warns_default(self):
        ev, blk = self._run_closeout(["EC-P1-1 — a", "EC-P1-2 — b"], ["EC-P1-1 — t"], "block")
        self.assertIsNotNone(blk)
        self.assertFalse(blk["human_required"])
        ev, blk = self._run_closeout(["EC-P1-1 — a", "EC-P1-2 — b"], ["EC-P1-1 — t"], None)
        self.assertIsNone(blk)  # warn-default
        self.assertIsNotNone(ev)  # but evidence recorded

    def test_setup_error_blocks_under_enforce(self):
        ev, blk = self._run_closeout(["EC-P1-1 — a"], ["EC-P1-1 — t"], "block", break_sha=True)
        self.assertIsNotNone(blk)

    def test_exception_fails_closed_under_enforce(self):
        ev, blk = self._run_closeout(["EC-P1-1 — a"], ["EC-P1-1 — t"], "block", patch_exc=True)
        self.assertIsNotNone(blk)
        ev, blk = self._run_closeout(["EC-P1-1 — a"], ["EC-P1-1 — t"], None, patch_exc=True)
        self.assertIsNone(blk)  # warn-default: exception does not block

    def test_clean_records_evidence_no_block(self):
        ev, blk = self._run_closeout(["EC-P1-1 — a"], ["EC-P1-1 — proven by t"], "block")
        self.assertIsNone(blk)
        self.assertIsNotNone(ev)

    def test_legacy_no_ids_no_evidence_no_block(self):
        ev, blk = self._run_closeout(["a bare goal"], ["did work"], "block")
        self.assertIsNone(blk)
        self.assertIsNone(ev)  # not_applicable -> no evidence, no gate

    def test_incomplete_phase_not_gated(self):
        # a non-complete phase is not gated even with a gap under enforce.
        ev, blk = self._run_closeout(["EC-P1-1 — a", "EC-P1-2 — b"], ["EC-P1-1 — t"], "block", is_complete=False)
        self.assertIsNone(blk)


if __name__ == "__main__":
    unittest.main()
