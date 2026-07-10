"""CONFORM (IF-0-CONFORM-1) — the language-neutral conformance baseline.

Asserts that the Python ``AgentRuntimeProvider`` seam
(``phase_loop_runtime/agent_runtime_provider.py``) conforms to the frozen
cross-language golden ``conformance.v0.1.json``. The golden is authored in
``omniagent-plus`` from the TypeScript ``core-contracts`` source; a byte-identical
copy is vendored here under ``tests/data/conformance_golden/`` and guarded by a
committed sha256.

Scope of what each table proves against the Python baseline:

* **methodNames** — proven by set-EQUALITY against the Protocol's own members,
  resolved through the golden's ``methodNameMapping`` by EXACT string match. The
  mapping is keyed by BOTH the camelCase (``ts``) and snake_case (``py``) spellings,
  so a golden method written in either convention resolves (a snake<->camel delta
  does not bite) while an undeclared spelling (``createsession``, ``CREATE_SESSION``,
  ``create__session``) does NOT resolve and fails loud. A dropped/renamed method bites.
* **eventTypes** — a two-sided bound: every ``runtime.*`` event the
  ``HomebrewAgentRuntimeProvider`` emits is in the golden (upper bound), AND the
  known homebrew lifecycle set is emitted (lower bound, so a silently-dropped
  lifecycle event bites). Python emits a subset of the 13 contract events;
  ``runtime.tool.*`` / ``runtime.approval.*`` / ``runtime.limit`` / ``runtime.heartbeat``
  belong to a richer backend and are not fabricated here.
* **terminalStates** — two directions: every golden terminal is a declared
  ``TURN_STATES`` / ``AGENT_SESSION_STATES`` member, AND every terminal the provider
  actually reaches is blessed by the golden (the provider cannot invent one).
* **errorCategories** — a SAMPLED-path check: an observed ``RuntimeProviderError``
  category is one of the golden categories. (The full 20-category table is
  TS-sourced; Python raises only the subset it needs. This samples one raising
  path — it does not enumerate every provider error branch.)

**Checksum caveat (honest):** the sha256 guard proves the vendored copy matches its
*own* stored digest (copy integrity). It does NOT detect divergence from the
``omniagent-plus`` canonical — that repo is not present in this worktree. Cross-repo
sync is enforced upstream (the omniagent-plus ``conformance-golden-identity`` test +
PUBHARDEN's TS-vs-golden gate), not here.
"""
from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

from phase_loop_runtime.agent_runtime_provider import (
    AGENT_SESSION_STATES,
    AgentRuntimeProvider,
    CreateSessionRequest,
    HomebrewAgentRuntimeProvider,
    RuntimeProviderError,
    SendTurnRequest,
    TURN_STATES,
)

_GOLDEN_DIR = Path(__file__).parent / "data" / "conformance_golden"
_GOLDEN_PATH = _GOLDEN_DIR / "conformance.v0.1.json"
_CHECKSUM_PATH = _GOLDEN_DIR / "conformance.v0.1.json.sha256"

# The lifecycle events the homebrew provider is expected to emit across a full
# create/turn/cancel/close cycle. Pinned as a lower bound so a silent loss of a
# non-terminal lifecycle event (e.g. runtime.text.delta) is caught, not just the
# terminal turn events.
_HOMEBREW_BASELINE_EVENTS = {
    "runtime.session.created",
    "runtime.turn.started",
    "runtime.text.delta",
    "runtime.turn.completed",
    "runtime.turn.failed",
    "runtime.turn.cancelled",
    "runtime.session.closed",
}


def _load_golden() -> dict:
    return json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))


def _protocol_methods() -> set[str]:
    """The public callables the Protocol itself declares (its own class body)."""
    return {
        name
        for name, value in vars(AgentRuntimeProvider).items()
        if not name.startswith("_") and callable(value)
    }


def _assert_methods_conform(golden: dict) -> None:
    """Set-equality of the golden method table (resolved via the mapping table, by
    EXACT string match) against the Python Protocol members. Raises AssertionError
    on drift. The mapping is keyed by both the camelCase and snake_case spellings,
    so a naming-convention delta resolves but an undeclared spelling does not."""
    pairs = golden["methodNameMapping"]
    ts_names = [pair["ts"] for pair in pairs]
    py_names = [pair["py"] for pair in pairs]
    actual_py = _protocol_methods()

    # Mapping integrity: one-to-one, no duplicates on either side.
    if len(ts_names) != len(set(ts_names)):
        raise AssertionError(f"duplicate ts name in methodNameMapping: {ts_names}")
    if len(py_names) != len(set(py_names)):
        raise AssertionError(f"duplicate py name in methodNameMapping: {py_names}")

    # The mapping's py-side must be exactly the Protocol's method set — the mapping
    # covers every protocol method once and invents none.
    if set(py_names) != actual_py:
        raise AssertionError(
            f"mapping py-names {sorted(py_names)} != protocol methods {sorted(actual_py)}"
        )

    method_names = golden["methodNames"]
    if len(method_names) != len(set(method_names)):
        raise AssertionError(f"duplicate entry in methodNames: {method_names}")

    # Resolve each golden method name by EXACT lookup (either the camelCase or the
    # snake_case spelling is an exact key — a naming-convention delta resolves; an
    # undeclared spelling does not). Then require the resolved set to equal the
    # Protocol exactly, so methodNames covers every method and adds none.
    resolver: dict[str, str] = {}
    for pair in pairs:
        resolver[pair["ts"]] = pair["py"]
        resolver[pair["py"]] = pair["py"]
    expected_py: set[str] = set()
    for name in method_names:
        resolved = resolver.get(name)  # EXACT match only — no normalization
        if resolved is None:
            raise AssertionError(
                f"golden method {name!r} is not an exact ts/py spelling in methodNameMapping"
            )
        expected_py.add(resolved)

    if expected_py != actual_py:
        raise AssertionError(
            f"method-name drift: golden(resolved)={sorted(expected_py)} "
            f"!= protocol={sorted(actual_py)}"
        )


def _emitted_event_types() -> set[str]:
    """Every runtime.* event the homebrew provider emits across a full cycle:
    session.created, turn.started, text.delta, turn.completed, turn.failed,
    turn.cancelled, session.closed."""
    emitted: set[str] = set()

    def _drain(provider: HomebrewAgentRuntimeProvider, session_id: str) -> None:
        for event in provider.read_history(session_id).events:
            emitted.add(event.type)

    # OK turn -> session.created, turn.started, text.delta, turn.completed, session.closed
    ok = HomebrewAgentRuntimeProvider(
        spawn=lambda request, register_process=None: ("OK", "done")
    )
    s = ok.create_session(
        CreateSessionRequest(target_harness="fake", idempotency_key="k1", title="ok")
    )
    ok.send_turn(SendTurnRequest(session_id=s.id, idempotency_key="t1", message="hi"))
    ok.close_session(s.id)
    _drain(ok, s.id)

    # ERROR turn -> turn.failed
    bad = HomebrewAgentRuntimeProvider(
        spawn=lambda request, register_process=None: ("ERROR", "boom")
    )
    s2 = bad.create_session(
        CreateSessionRequest(target_harness="fake", idempotency_key="k2", title="bad")
    )
    bad.send_turn(SendTurnRequest(session_id=s2.id, idempotency_key="t2", message="hi"))
    _drain(bad, s2.id)

    # Cancel a running turn -> turn.cancelled. NOTE: the synchronous spawn completes the
    # turn before send_turn returns, so this test INJECTS a running state on the private
    # record to drive cancel_turn's cancelled-terminal path (injected-state cancellation
    # coverage — not a state the synchronous homebrew profile reaches on its own).
    prov = HomebrewAgentRuntimeProvider(
        spawn=lambda request, register_process=None: ("OK", "x")
    )
    s3 = prov.create_session(
        CreateSessionRequest(target_harness="fake", idempotency_key="k3", title="cancel")
    )
    handle = prov.send_turn(
        SendTurnRequest(session_id=s3.id, idempotency_key="t3", message="hi")
    )
    record = prov._record(s3.id)  # type: ignore[attr-defined]
    record.turns[handle.turn_id].state = "running"
    record.info.active_turn_id = handle.turn_id
    prov.cancel_turn(record.turns[handle.turn_id])
    _drain(prov, s3.id)

    return emitted


def _observed_terminal_states() -> tuple[set[str], set[str]]:
    """Run the homebrew provider to its terminal states and report the turn-handle
    terminal states and session terminal states it reaches:
      - turn:    completed (OK), failed (ERROR), cancelled (injected-state cancel path)
      - session: closed (after close_session)
    The homebrew profile never reaches turn `timed_out` or session `failed`, so the
    observed set is a strict subset of the golden — exactly the conformance direction
    we assert (the provider must not reach a terminal the golden does not bless)."""
    turn_terminals: set[str] = set()
    session_terminals: set[str] = set()

    ok = HomebrewAgentRuntimeProvider(
        spawn=lambda request, register_process=None: ("OK", "done")
    )
    s = ok.create_session(
        CreateSessionRequest(target_harness="fake", idempotency_key="k1", title="ok")
    )
    turn_terminals.add(
        ok.send_turn(SendTurnRequest(session_id=s.id, idempotency_key="t1", message="hi")).state
    )
    ok.close_session(s.id)
    session_terminals.add(ok.get_session_info(s.id).state)

    bad = HomebrewAgentRuntimeProvider(
        spawn=lambda request, register_process=None: ("ERROR", "boom")
    )
    s2 = bad.create_session(
        CreateSessionRequest(target_harness="fake", idempotency_key="k2", title="bad")
    )
    turn_terminals.add(
        bad.send_turn(SendTurnRequest(session_id=s2.id, idempotency_key="t2", message="hi")).state
    )

    prov = HomebrewAgentRuntimeProvider(
        spawn=lambda request, register_process=None: ("OK", "x")
    )
    s3 = prov.create_session(
        CreateSessionRequest(target_harness="fake", idempotency_key="k3", title="cancel")
    )
    handle = prov.send_turn(
        SendTurnRequest(session_id=s3.id, idempotency_key="t3", message="hi")
    )
    record = prov._record(s3.id)  # type: ignore[attr-defined]
    record.turns[handle.turn_id].state = "running"
    record.info.active_turn_id = handle.turn_id
    turn_terminals.add(prov.cancel_turn(record.turns[handle.turn_id]).state)

    return turn_terminals, session_terminals


def _assert_events_conform(golden: dict) -> None:
    """Two-sided event bound. Raises AssertionError on drift.
      - upper bound: every emitted event type is in the golden table;
      - lower bound: the known homebrew lifecycle set is actually emitted."""
    golden_events = set(golden["eventTypes"])
    emitted = _emitted_event_types()
    extra = emitted - golden_events
    if extra:
        raise AssertionError(
            f"emitted event(s) absent from golden: {sorted(extra)} "
            f"(golden={sorted(golden_events)})"
        )
    missing_baseline = _HOMEBREW_BASELINE_EVENTS - emitted
    if missing_baseline:
        raise AssertionError(
            f"homebrew stopped emitting baseline lifecycle event(s): {sorted(missing_baseline)}"
        )


def _assert_terminals_conform(golden: dict) -> None:
    """Two-direction terminal-state conformance. Raises AssertionError on drift.
      - Direction 1: every golden terminal is a declared Python state constant;
      - Direction 2: every terminal the provider reaches is blessed by the golden."""
    golden_turn = set(golden["terminalStates"]["turn"])
    golden_session = set(golden["terminalStates"]["session"])

    for state in golden_turn:
        if state not in TURN_STATES:
            raise AssertionError(f"turn-terminal {state!r} missing from TURN_STATES")
    for state in golden_session:
        if state not in AGENT_SESSION_STATES:
            raise AssertionError(f"session-terminal {state!r} missing from AGENT_SESSION_STATES")

    observed_turn, observed_session = _observed_terminal_states()
    if not observed_turn.issubset(golden_turn):
        raise AssertionError(
            f"provider reached turn-terminal(s) not in golden: {sorted(observed_turn - golden_turn)}"
        )
    if not observed_session.issubset(golden_session):
        raise AssertionError(
            f"provider reached session-terminal(s) not in golden: "
            f"{sorted(observed_session - golden_session)}"
        )


class ChecksumGuardTest(unittest.TestCase):
    def test_vendored_golden_matches_committed_checksum(self):
        expected = _CHECKSUM_PATH.read_text(encoding="utf-8").split()[0]
        actual = hashlib.sha256(_GOLDEN_PATH.read_bytes()).hexdigest()
        self.assertEqual(actual, expected, "vendored golden does not match its sha256")


class ConformanceGoldenTest(unittest.TestCase):
    def setUp(self):
        self.golden = _load_golden()

    def test_schema_and_shape(self):
        self.assertEqual(self.golden["schema"], "conformance.v0.1")
        self.assertEqual(len(self.golden["methodNames"]), 8)
        self.assertEqual(len(self.golden["eventTypes"]), 13)
        self.assertEqual(len(self.golden["errorCategories"]), 20)

    def test_method_names_conform(self):
        _assert_methods_conform(self.golden)

    def test_homebrew_satisfies_protocol(self):
        provider = HomebrewAgentRuntimeProvider(
            spawn=lambda request, register_process=None: ("OK", "hi")
        )
        self.assertIsInstance(provider, AgentRuntimeProvider)

    def test_event_types_conform(self):
        _assert_events_conform(self.golden)

    def test_terminal_states_conform(self):
        _assert_terminals_conform(self.golden)
        # sanity: the three homebrew turn terminals are observed (completed/failed
        # organically; cancelled via the injected-state cancel path).
        observed_turn, _ = _observed_terminal_states()
        self.assertEqual(observed_turn, {"completed", "failed", "cancelled"})

    def test_error_categories_conform(self):
        provider = HomebrewAgentRuntimeProvider(
            spawn=lambda request, register_process=None: ("OK", "hi")
        )
        with self.assertRaises(RuntimeProviderError) as ctx:
            provider.get_session_info("does-not-exist")
        self.assertIn(ctx.exception.category, set(self.golden["errorCategories"]))


class MutationBitesTest(unittest.TestCase):
    """The golden actually discriminates real drift from a naming-convention delta.
    Each mutation runs against the SAME conformance helper the real tests use, so a
    mutation test cannot pass by re-deriving logic the production check doesn't run."""

    def setUp(self):
        self.golden = _load_golden()

    def test_mutation_bites_real_event_drift(self):
        # Flip an event string the homebrew provider actually emits -> event check FAILS.
        mutated = copy.deepcopy(self.golden)
        idx = mutated["eventTypes"].index("runtime.turn.completed")
        mutated["eventTypes"][idx] = "runtime.turn.MUTATED"
        with self.assertRaises(AssertionError):
            _assert_events_conform(mutated)

    def test_mutation_bites_terminal_state_drift(self):
        # Drop a terminal turn state the provider reaches -> terminal conformance FAILS.
        mutated = copy.deepcopy(self.golden)
        mutated["terminalStates"]["turn"] = [
            s for s in mutated["terminalStates"]["turn"] if s != "completed"
        ]
        with self.assertRaises(AssertionError):
            _assert_terminals_conform(mutated)

    def test_mutation_bites_real_method_drift(self):
        # Drop a real method (and its mapping) from the golden -> method check FAILS.
        mutated = copy.deepcopy(self.golden)
        mutated["methodNames"] = [n for n in mutated["methodNames"] if n != "sendTurn"]
        mutated["methodNameMapping"] = [
            p for p in mutated["methodNameMapping"] if p["ts"] != "sendTurn"
        ]
        with self.assertRaises(AssertionError):
            _assert_methods_conform(mutated)

    def test_naming_convention_delta_does_not_bite(self):
        # Rewrite a golden method name from camelCase to its snake_case spelling (a pure
        # convention delta, still an exact mapping key) -> methods still conform.
        mutated = copy.deepcopy(self.golden)
        idx = mutated["methodNames"].index("createSession")
        mutated["methodNames"][idx] = "create_session"
        _assert_methods_conform(mutated)  # must NOT raise

    def test_undeclared_spelling_bites(self):
        # An undeclared spelling (not an exact ts/py mapping key) must fail loud — the
        # mapping pins correspondence, it does not accept arbitrary normalizations.
        for bogus in ("createsession", "CREATE_SESSION", "create__session"):
            mutated = copy.deepcopy(self.golden)
            idx = mutated["methodNames"].index("createSession")
            mutated["methodNames"][idx] = bogus
            with self.assertRaises(AssertionError):
                _assert_methods_conform(mutated)


if __name__ == "__main__":
    unittest.main()
