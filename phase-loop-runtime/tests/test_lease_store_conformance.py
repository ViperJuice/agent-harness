"""Vector-driven conformance tests for CS-0.10c against the vendored
`consiliency_contract` (>=0.2.0) lease/coordination conformance vectors.

Two disjoint shapes ship under the contract's `lease-*`/`coordination-*`
vector IDs:

* A `consiliency.coordination_scenario.v1` scenario (`now`, `requested_mode`,
  `backend`, `atomic_backend`, an ordered `events` stream, and -- on the
  `coordination-*` guardrail vectors -- advisory `messages`) with an
  `expected.current_lease` / `expected.effective_mode` to assert against the
  PURE projection core (`phase_loop_runtime.lease_store.project` /
  `resolve_effective_mode`). `messages`, when present, are never read by the
  projection -- feeding a `coordination-*-does-not-*` vector's `events` alone
  and getting the unchanged view back IS the guardrail proof (no separate
  code path exists for a message to reach `project()`).
* `lease-event-carries-lease` -- a single `consiliency.lease_event.v1`
  document (an acquire event nesting a full lease snapshot), asserted
  against the wire schema instead (`lease_event_validator()`), since it
  exercises schema cross-referencing, not the projection.
"""
from __future__ import annotations

import unittest

from consiliency_contract import list_vectors, load_vector

from phase_loop_runtime.lease_store import lease_event_validator, project, resolve_effective_mode

_SCENARIO_VECTOR_IDS = tuple(
    name[: -len(".json")]
    for name in list_vectors()
    if (name.startswith("lease-") or name.startswith("coordination-")) and name != "lease-event-carries-lease.json"
)


class LeaseScenarioConformanceTest(unittest.TestCase):
    def test_every_scenario_vector_present(self):
        # Pins the vector count so a contract bump that adds/removes a
        # lease/coordination vector fails loud here instead of silently
        # under-covering.
        self.assertEqual(
            set(_SCENARIO_VECTOR_IDS),
            {
                "lease-acquire",
                "lease-renew",
                "lease-release",
                "lease-expire",
                "lease-expiry-boundary",
                "lease-hard-degrades-to-soft",
                "lease-hard-mode-atomic",
                "coordination-announce-intent-does-not-lease",
                "coordination-message-does-not-mutate-lease",
                "coordination-done-does-not-release-lease",
                "coordination-handoff-does-not-transfer-holder",
            },
        )

    def test_scenario_vectors_project_to_expected_view(self):
        for vector_id in _SCENARIO_VECTOR_IDS:
            with self.subTest(vector=vector_id):
                vector = load_vector(vector_id)
                scenario = vector["input"]
                expected = vector["expected"]
                events = scenario["events"]
                now = scenario["now"]
                atomic_backend = bool(scenario.get("atomic_backend", False))

                # Every event in these fixtures targets the same lease_id.
                lease_ids = {event["lease_id"] for event in events}
                self.assertEqual(len(lease_ids), 1, f"{vector_id}: fixture assumption violated")
                lease_id = next(iter(lease_ids))

                view = project(events, lease_id, now, atomic_backend=atomic_backend)
                self.assertEqual(view, expected["current_lease"], vector_id)

                effective_mode = resolve_effective_mode(scenario["requested_mode"], atomic_backend)
                self.assertEqual(effective_mode, expected["effective_mode"], vector_id)

                # changed_by_message is always False: messages (when present)
                # were never passed to project() at all.
                self.assertFalse(expected.get("changed_by_message", False) is True)

    def test_coordination_messages_never_reach_the_projection(self):
        """Structural guardrail proof: re-run each coordination-*-does-not-*
        vector's events with its `messages` field present in the raw fixture
        but never forwarded to project() (the function has no parameter for
        it) -- the view is identical to the message-free projection."""
        for vector_id in _SCENARIO_VECTOR_IDS:
            if not vector_id.startswith("coordination-"):
                continue
            with self.subTest(vector=vector_id):
                vector = load_vector(vector_id)
                scenario = vector["input"]
                self.assertIn("messages", scenario, f"{vector_id}: expected a guardrail vector with messages")
                events = scenario["events"]
                lease_id = events[0]["lease_id"]
                with_messages_present_in_fixture = project(
                    events, lease_id, scenario["now"], atomic_backend=bool(scenario.get("atomic_backend", False))
                )
                self.assertEqual(with_messages_present_in_fixture, vector["expected"]["current_lease"])


class LeaseEventCarriesLeaseConformanceTest(unittest.TestCase):
    def test_acquire_event_validates_against_the_wire_schema(self):
        vector = load_vector("lease-event-carries-lease")
        lease_event_validator().validate(vector["input"])


if __name__ == "__main__":
    unittest.main()
