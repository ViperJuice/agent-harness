"""W4 (IF-0-UNATTEND-1) + #145 typed operator approval.

W4 lets an N-vendor consensus quorum substitute for the EXISTING
``release_guard`` human merge/tag grant in an ``unattended`` run, writing a
durable audit record; the ``on_shortfall`` dial from IF-0-POLICY-1 handles a
1-subscription operator. #145 delivers a typed, metadata-only operator approval
record with fail-closed target coverage and secret rejection."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from phase_loop_runtime.ratification_policy import (
    BoardFacts,
    RatificationPolicy,
    evaluate_ratification,
)
from phase_loop_runtime.release_guard import (
    ATTENDED_RUN_MODE,
    UNATTENDED_RUN_MODE,
    OperatorApproval,
    OperatorApprovalError,
    ReleaseDispatchBlocker,
    evaluate_unattended_release,
    operator_approval_from,
)


def _blocker() -> ReleaseDispatchBlocker:
    return ReleaseDispatchBlocker(
        blocker_class="branch_sync_conflict",
        blocker_summary="needs a human merge/tag grant",
        required_human_inputs=("merge the release branch",),
        metadata={"guard": "release_dispatch"},
    )


# A full cross-vendor board that satisfies the strict release-dispatch defaults.
_STRICT = RatificationPolicy(3, 3, "unanimous", "escalate")
_DEGRADED_DIAL = RatificationPolicy(3, 3, "unanimous", "proceed_degraded")


class W4UnattendedGrantTest(unittest.TestCase):
    def test_attended_mode_returns_none(self):
        # Attended runs keep the existing human_required path unchanged.
        facts = BoardFacts(3, 3, 3, 3, reviewed_sha="deadbeef")
        self.assertIsNone(
            evaluate_unattended_release(
                _blocker(), policy=_STRICT, facts=facts, run_mode=ATTENDED_RUN_MODE
            )
        )
        # Default run mode is attended.
        self.assertIsNone(
            evaluate_unattended_release(_blocker(), policy=_STRICT, facts=facts)
        )

    def test_consensus_grants_and_proceeds(self):
        facts = BoardFacts(3, 3, 3, 3, reviewed_sha="abc123def456")
        grant = evaluate_unattended_release(
            _blocker(), policy=_STRICT, facts=facts, run_mode=UNATTENDED_RUN_MODE
        )
        self.assertIsNotNone(grant)
        self.assertTrue(grant.granted)
        self.assertEqual(grant.outcome, "consensus_granted")
        # A granted decision proceeds — no blocker emitted.
        self.assertIsNone(grant.to_blocker())
        audit = grant.to_audit()
        self.assertEqual(audit["kind"], "unattended_release_grant")
        self.assertEqual(audit["substitutes_for"], "branch_sync_conflict")
        self.assertEqual(audit["reviewed_sha"], "abc123def456")
        # The frozen POLICY decision shape is embedded verbatim (no drift).
        expected = evaluate_ratification(_STRICT, facts, gate="release-dispatch").to_audit()
        self.assertEqual(audit["ratification"], expected)

    def test_shortfall_escalates_to_non_human_hold(self):
        # Only one vendor / one lens / no unanimity → shortfall under the strict dial.
        facts = BoardFacts(1, 1, 1, 3, reviewed_sha="cafe")
        grant = evaluate_unattended_release(
            _blocker(), policy=_STRICT, facts=facts, run_mode=UNATTENDED_RUN_MODE
        )
        self.assertFalse(grant.granted)
        self.assertEqual(grant.outcome, "escalated")
        blocker = grant.to_blocker()
        self.assertIsNotNone(blocker)
        # NON-human hold: autonomy-first, never a new human_required gate.
        self.assertFalse(blocker["human_required"])
        self.assertEqual(blocker["blocker_class"], "review_gate_block")
        self.assertIn("escalated", blocker["blocker_summary"])

    def test_seated_but_silent_board_fails_closed(self):
        # IF-0-POLICY-1 tightening: quorum binds to USABLE reviewers
        # (effective_vendors = min(distinct_vendors, reviewing)). A board that
        # SEATS 3 vendors but only 1 leg actually reviewed (2 dropped silently
        # under contention) must NOT let consensus stand in for the human grant —
        # otherwise W4 would auto-approve a release on phantom reviews. Escalate.
        facts = BoardFacts(distinct_vendors=3, lens_coverage=3, agreeing=1, reviewing=1)
        grant = evaluate_unattended_release(
            _blocker(), policy=_STRICT, facts=facts, run_mode=UNATTENDED_RUN_MODE
        )
        self.assertFalse(grant.granted)
        self.assertEqual(grant.outcome, "escalated")
        self.assertIn("vendors", grant.to_audit()["ratification"]["shortfalls"])

    def test_proceed_degraded_dial_grants_with_paper_trail(self):
        # Same shortfall, but the 1-subscription dial proceeds with an audit record.
        facts = BoardFacts(1, 1, 1, 3, reviewed_sha="f00d")
        grant = evaluate_unattended_release(
            _blocker(), policy=_DEGRADED_DIAL, facts=facts, run_mode=UNATTENDED_RUN_MODE
        )
        self.assertTrue(grant.granted)
        self.assertEqual(grant.outcome, "proceed_degraded")
        self.assertIsNone(grant.to_blocker())
        # The paper trail records that a degraded board was knowingly accepted.
        self.assertFalse(grant.to_audit()["ratification"]["satisfied"])
        self.assertEqual(grant.to_audit()["reviewed_sha"], "f00d")


class OperatorApprovalTest(unittest.TestCase):
    def _payload(self, **over):
        base = {
            "approved_targets": ["pkg:consiliency", "deploy:prod"],
            "approved_at": "2026-07-11T00:00:00Z",
            "source": "codex-task-123",
            "watch_owner": "operator@example.com",
            "roadmap": "specs/phase-plans-v10.md",
            "phase": "UNATTEND",
            "run_id": "run-7",
        }
        base.update(over)
        return base

    def test_roundtrip_and_metadata_is_secret_free(self):
        approval = operator_approval_from(self._payload())
        self.assertIsInstance(approval, OperatorApproval)
        meta = approval.to_metadata()
        self.assertEqual(meta["kind"], "operator_approval")
        self.assertEqual(meta["approved_targets"], ["pkg:consiliency", "deploy:prod"])
        self.assertNotIn("secret", " ".join(map(str, meta.keys())).lower())

    def test_covers_is_fail_closed(self):
        approval = operator_approval_from(self._payload())
        self.assertTrue(approval.covers(["pkg:consiliency"]))
        self.assertTrue(approval.covers(["pkg:consiliency", "deploy:prod"]))
        # An unapproved target fails the whole check.
        self.assertFalse(approval.covers(["pkg:consiliency", "deploy:staging"]))
        # An empty request is not vacuously approved.
        self.assertFalse(approval.covers([]))

    def test_secret_bearing_key_rejected(self):
        for bad_key in ("api_token", "GITHUB_SECRET", "aws_credential", "auth_header"):
            with self.assertRaises(OperatorApprovalError):
                operator_approval_from(self._payload(**{bad_key: "x"}))

    def test_empty_or_string_targets_rejected(self):
        with self.assertRaises(OperatorApprovalError):
            operator_approval_from(self._payload(approved_targets=[]))
        with self.assertRaises(OperatorApprovalError):
            operator_approval_from(self._payload(approved_targets="pkg:consiliency"))

    def test_non_mapping_rejected(self):
        with self.assertRaises(OperatorApprovalError):
            operator_approval_from(["not", "a", "mapping"])  # type: ignore[arg-type]

    def test_nested_container_value_rejected(self):
        # A nested map/list must not slip a secret past the top-level key scan and
        # get stringified into the metadata (CR: agy finding).
        with self.assertRaises(OperatorApprovalError):
            operator_approval_from(self._payload(source={"api_token": "sk-leak"}))
        with self.assertRaises(OperatorApprovalError):
            operator_approval_from(self._payload(watch_owner=["nested", "list"]))

    def test_non_iterable_targets_raise_typed_error(self):
        # A non-iterable approved_targets must raise the typed error, not TypeError.
        with self.assertRaises(OperatorApprovalError):
            operator_approval_from(self._payload(approved_targets=5))

    def test_non_string_target_element_rejected(self):
        # A non-str target element (e.g. a nested map) must not be str()'d into an
        # approved target and leaked via to_metadata() (CR: codex finding).
        with self.assertRaises(OperatorApprovalError):
            operator_approval_from(self._payload(approved_targets=[{"api_token": "sk-leak"}]))
        with self.assertRaises(OperatorApprovalError):
            operator_approval_from(self._payload(approved_targets=["pkg:ok", 42]))


if __name__ == "__main__":
    unittest.main()
