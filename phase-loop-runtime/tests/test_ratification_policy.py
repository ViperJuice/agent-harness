"""REVIEWGOV W3 — parameterized ratification policy (IF-0-POLICY-1) + #80/#88.

Non-mocked: the evaluator is pure and exercised with real ``RatificationPolicy`` /
``BoardFacts`` / ``RatificationDecision`` objects; the board projection is exercised
against a REAL ``compose_review_board`` board; the #80/#88 persistence path runs the
REAL ``governed_planning_gate`` with real ``PanelResult`` legs (no ``Mock``).
"""
import unittest

from phase_loop_runtime import gate_posture
from phase_loop_runtime.advisor_board.composition import compose_review_board
from phase_loop_runtime.closeout_validators import (
    ReviewFinding,
    apply_review_findings,
    ratification_findings,
    verdict_binds_to,
)
from phase_loop_runtime.governed_review import governed_planning_gate
from phase_loop_runtime.panel_invoker import PanelLegResult, PanelResult
from phase_loop_runtime.ratification_policy import (
    CONSENSUS_MODES,
    DEFAULT_RATIFICATION_POLICIES,
    ESCALATE,
    GATES,
    PROCEED_DEGRADED,
    RATIFIED,
    BoardFacts,
    RatificationPolicy,
    board_facts_from,
    consensus_met,
    evaluate_ratification,
    shortfall_detail,
)


class SchemaValidationTest(unittest.TestCase):
    def test_valid_policy_roundtrips_to_json(self):
        p = RatificationPolicy(3, 3, "unanimous", "escalate")
        self.assertEqual(
            p.to_json(),
            {
                "required_vendors": 3,
                "required_lens_coverage": 3,
                "required_consensus": "unanimous",
                "on_shortfall": "escalate",
            },
        )

    def test_rejects_bad_field_values(self):
        with self.assertRaises(ValueError):
            RatificationPolicy(0, 1, "majority", "escalate")           # vendors < 1
        with self.assertRaises(ValueError):
            RatificationPolicy(1, 0, "majority", "escalate")           # lens < 1
        with self.assertRaises(ValueError):
            RatificationPolicy(1, 1, "plurality", "escalate")          # bad consensus
        with self.assertRaises(ValueError):
            RatificationPolicy(1, 1, "majority", "human_required")     # bad shortfall
        with self.assertRaises(ValueError):
            RatificationPolicy("3", 1, "majority", "escalate")         # non-int

    def test_default_policies_cover_every_gate(self):
        self.assertEqual(set(DEFAULT_RATIFICATION_POLICIES), set(GATES))
        for gate, pol in DEFAULT_RATIFICATION_POLICIES.items():
            self.assertIsInstance(pol, RatificationPolicy)
            self.assertIn(pol.required_consensus, CONSENSUS_MODES)
        # The merge/release gates hold (escalate); plan/design lean autonomy-first.
        self.assertEqual(DEFAULT_RATIFICATION_POLICIES["release-dispatch"].on_shortfall, "escalate")
        self.assertEqual(DEFAULT_RATIFICATION_POLICIES["pre-merge-CR"].on_shortfall, "escalate")
        self.assertEqual(DEFAULT_RATIFICATION_POLICIES["plan-ratify"].on_shortfall, "proceed_degraded")


class ConsensusTest(unittest.TestCase):
    def test_unanimous(self):
        self.assertTrue(consensus_met("unanimous", agreeing=3, reviewing=3))
        self.assertFalse(consensus_met("unanimous", agreeing=2, reviewing=3))

    def test_majority_is_strict(self):
        self.assertTrue(consensus_met("majority", agreeing=2, reviewing=3))
        self.assertFalse(consensus_met("majority", agreeing=2, reviewing=4))  # tie is not a majority
        self.assertTrue(consensus_met("majority", agreeing=3, reviewing=4))

    def test_zero_reviewers_is_no_consensus(self):
        self.assertFalse(consensus_met("unanimous", agreeing=0, reviewing=0))
        self.assertFalse(consensus_met("majority", agreeing=0, reviewing=0))


class EvaluatorTest(unittest.TestCase):
    def test_full_board_ratifies(self):
        pol = DEFAULT_RATIFICATION_POLICIES["pre-merge-CR"]
        facts = BoardFacts(distinct_vendors=3, lens_coverage=3, agreeing=3, reviewing=3)
        d = evaluate_ratification(pol, facts, gate="pre-merge-CR")
        self.assertEqual(d.status, RATIFIED)
        self.assertTrue(d.satisfied)
        self.assertEqual(d.shortfalls, ())
        self.assertFalse(d.blocks)

    def test_vendor_shortfall_escalates_under_escalate_policy(self):
        pol = DEFAULT_RATIFICATION_POLICIES["release-dispatch"]  # on_shortfall=escalate
        facts = BoardFacts(distinct_vendors=1, lens_coverage=3, agreeing=1, reviewing=1)
        d = evaluate_ratification(pol, facts, gate="release-dispatch")
        self.assertEqual(d.status, ESCALATE)
        self.assertIn("vendors", d.shortfalls)
        self.assertTrue(d.blocks)

    def test_shortfall_proceeds_degraded_under_proceed_policy(self):
        # A 1-subscription operator on plan-ratify: vendor+lens short, but the dial
        # is proceed_degraded, so it proceeds WITH an audit record (never blocks,
        # never human_required).
        pol = DEFAULT_RATIFICATION_POLICIES["plan-ratify"]
        facts = BoardFacts(distinct_vendors=1, lens_coverage=1, agreeing=1, reviewing=1)
        d = evaluate_ratification(pol, facts, gate="plan-ratify")
        self.assertEqual(d.status, PROCEED_DEGRADED)
        self.assertFalse(d.blocks)
        self.assertFalse(d.satisfied)
        self.assertEqual(set(d.shortfalls), {"vendors", "lens_coverage"})

    def test_seated_but_silent_board_fails_closed(self):
        # Regression (CR, convergent codex+grok finding): a fully SEATED 3-vendor
        # board whose legs mostly dropped (only 1 usable review — the normal
        # contention condition) must NOT ratify a 3-vendor gate. The vendor quorum
        # binds to USABLE reviewers, not seated ones, so this escalates (fail-closed)
        # rather than fail-open on a single reviewer.
        pol = DEFAULT_RATIFICATION_POLICIES["pre-merge-CR"]  # required_vendors=3, escalate
        facts = BoardFacts(distinct_vendors=3, lens_coverage=3, agreeing=1, reviewing=1)
        d = evaluate_ratification(pol, facts, gate="pre-merge-CR")
        self.assertEqual(d.status, ESCALATE)
        self.assertIn("vendors", d.shortfalls)
        self.assertTrue(d.blocks)
        # The detail names the effective (usable) count, not just the seated count.
        self.assertIn("usable reviewer vendors 1", shortfall_detail(d))
        # A genuinely full 3-vendor board (3 usable reviews) still ratifies.
        full = BoardFacts(distinct_vendors=3, lens_coverage=3, agreeing=3, reviewing=3)
        self.assertEqual(evaluate_ratification(pol, full, gate="pre-merge-CR").status, RATIFIED)

    def test_consensus_shortfall_detected(self):
        pol = RatificationPolicy(1, 1, "unanimous", "escalate")
        facts = BoardFacts(distinct_vendors=1, lens_coverage=1, agreeing=1, reviewing=2)
        d = evaluate_ratification(pol, facts, gate="pre-merge-CR")
        self.assertEqual(d.shortfalls, ("consensus",))
        self.assertEqual(d.status, ESCALATE)

    def test_audit_record_is_durable_and_never_human_required(self):
        pol = DEFAULT_RATIFICATION_POLICIES["plan-ratify"]
        facts = BoardFacts(distinct_vendors=1, lens_coverage=1, agreeing=1, reviewing=1, reviewed_sha="abc123")
        audit = evaluate_ratification(pol, facts, gate="plan-ratify").to_audit()
        self.assertEqual(audit["kind"], "ratification_decision")
        self.assertEqual(audit["gate"], "plan-ratify")
        self.assertEqual(audit["facts"]["reviewed_sha"], "abc123")
        self.assertNotIn("human_required", audit)


class BoardProjectionTest(unittest.TestCase):
    def test_facts_from_full_real_board(self):
        # All four vendors up (simulated availability) → 4 distinct vendors, 4 lenses.
        board = compose_review_board(is_available=lambda _v: True)
        facts = board_facts_from(board, agreeing=4, reviewing=4, reviewed_sha="sha")
        self.assertEqual(facts.distinct_vendors, 4)
        self.assertEqual(facts.lens_coverage, 4)
        self.assertEqual(facts.reviewed_sha, "sha")

    def test_facts_from_single_vendor_board_is_degraded(self):
        # One vendor up → backfilled to 4 lens-varied seats on ONE vendor: lens
        # coverage stays high but distinct vendors is 1 (correlated blind spot).
        board = compose_review_board(is_available=lambda v: v == "grok")
        facts = board_facts_from(board, agreeing=4, reviewing=4)
        self.assertEqual(facts.distinct_vendors, 1)
        self.assertGreaterEqual(facts.lens_coverage, 3)
        d = evaluate_ratification(DEFAULT_RATIFICATION_POLICIES["release-dispatch"], facts, gate="release-dispatch")
        self.assertIn("vendors", d.shortfalls)  # a lens-varied 1-vendor board is NOT independent


class PostureBridgeTest(unittest.TestCase):
    def test_default_resolution(self):
        self.assertEqual(
            gate_posture.resolve_ratification_policy("pre-merge-CR"),
            DEFAULT_RATIFICATION_POLICIES["pre-merge-CR"],
        )

    def test_partial_manifest_override(self):
        manifest = {"ratification_policy_overrides": {"pre-merge-CR": {"on_shortfall": "proceed_degraded"}}}
        pol = gate_posture.resolve_ratification_policy("pre-merge-CR", manifest=manifest)
        self.assertEqual(pol.on_shortfall, "proceed_degraded")
        # Other fields untouched (partial patch).
        self.assertEqual(pol.required_vendors, DEFAULT_RATIFICATION_POLICIES["pre-merge-CR"].required_vendors)

    def test_malformed_override_falls_back_to_default(self):
        manifest = {"ratification_policy_overrides": {"pre-merge-CR": {"on_shortfall": "human_required"}}}
        pol = gate_posture.resolve_ratification_policy("pre-merge-CR", manifest=manifest)
        self.assertEqual(pol, DEFAULT_RATIFICATION_POLICIES["pre-merge-CR"])

    def test_unknown_gate_uses_conservative_default(self):
        pol = gate_posture.resolve_ratification_policy("no-such-gate")
        self.assertEqual(pol, DEFAULT_RATIFICATION_POLICIES["release-dispatch"])


class RatificationFindingsTest(unittest.TestCase):
    def test_escalate_becomes_non_human_review_gate_block(self):
        pol = DEFAULT_RATIFICATION_POLICIES["release-dispatch"]
        facts = BoardFacts(distinct_vendors=1, lens_coverage=1, agreeing=1, reviewing=1, reviewed_sha="deadbeef")
        d = evaluate_ratification(pol, facts, gate="release-dispatch")
        findings = ratification_findings(d)
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f.severity, "block")
        self.assertEqual(f.blocker_class, "review_gate_block")
        self.assertEqual(f.body, shortfall_detail(d))
        self.assertEqual(f.reviewed_sha, "deadbeef")
        # apply_review_findings never asks for a human (autonomy-first invariant).
        applied = apply_review_findings(findings=list(findings), terminal={}, automation={}, blocker={})
        self.assertIs(applied["automation"]["human_required"], False)
        self.assertIs(applied["blocker"]["human_required"], False)

    def test_proceed_degraded_is_a_recorded_warn(self):
        pol = DEFAULT_RATIFICATION_POLICIES["plan-ratify"]
        facts = BoardFacts(distinct_vendors=1, lens_coverage=1, agreeing=1, reviewing=1)
        findings = ratification_findings(evaluate_ratification(pol, facts, gate="plan-ratify"))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "warn")
        self.assertIsNone(findings[0].blocker_class)

    def test_ratified_emits_no_finding(self):
        pol = DEFAULT_RATIFICATION_POLICIES["pre-merge-CR"]
        facts = BoardFacts(distinct_vendors=3, lens_coverage=3, agreeing=3, reviewing=3)
        self.assertEqual(ratification_findings(evaluate_ratification(pol, facts, gate="pre-merge-CR")), ())


class ShaBindingTest(unittest.TestCase):
    """#88 — the verdict binds to the exact reviewed commit."""

    def test_binds_only_on_exact_match(self):
        f = ReviewFinding(code="panel_block", reason="…", reviewed_sha="abc")
        self.assertTrue(verdict_binds_to(f, "abc"))
        self.assertFalse(verdict_binds_to(f, "def"))

    def test_unbound_finding_never_binds(self):
        f = ReviewFinding(code="panel_block", reason="…")  # no reviewed_sha
        self.assertFalse(verdict_binds_to(f, "abc"))

    def test_unknown_head_never_binds(self):
        f = ReviewFinding(code="panel_block", reason="…", reviewed_sha="abc")
        self.assertFalse(verdict_binds_to(f, None))


class FindingBodyPersistenceTest(unittest.TestCase):
    """#80 — the block finding carries the ACTUAL panel review text + reviewed SHA,
    exercised through the REAL governed gate with real (non-mock) panel legs."""

    def test_block_finding_persists_leg_text_and_sha(self):
        blocking_text = (
            "The migration drops the users table without a backup step.\n"
            "This is unrecoverable in production.\nDISAGREE"
        )
        # A real panel: one usable disjoint leg that DISAGREEs (blocks).
        panel = PanelResult(legs=(PanelLegResult(leg="gemini", status="OK", text=blocking_text),))
        result = governed_planning_gate(
            artifact="ART",
            author_vendors=["codex"],          # codex authored → gemini is disjoint
            run_mode="governed",
            available_legs=["gemini"],
            invoke=lambda *a, **k: panel,       # real object, not a Mock
            reviewed_sha="c0ffee",
        )
        self.assertTrue(result.ran)
        self.assertFalse(result.promoted)       # blocked
        block = next(f for f in result.findings if f.severity == "block")
        self.assertEqual(block.code, "panel_block")
        # The generic reason is unchanged, but the ACTUAL review text is now carried…
        self.assertEqual(block.reason, "panel leg gemini raised a blocking concern")
        self.assertEqual(block.body, blocking_text)
        self.assertEqual(block.reviewed_sha, "c0ffee")
        # …and it survives serialization to the durable artifact.
        self.assertEqual(block.to_json()["body"], blocking_text)
        self.assertEqual(block.to_json()["reviewed_sha"], "c0ffee")
        # And the verdict binds only to the reviewed head.
        self.assertTrue(verdict_binds_to(block, "c0ffee"))
        self.assertFalse(verdict_binds_to(block, "beefcafe"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
