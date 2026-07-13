import pytest

from phase_loop_runtime.convergence.broker.evidence import BrokerEvidenceStore, EvidenceRecord
from phase_loop_runtime.convergence.provider_contracts import TerminalOutcomeState


def test_ambiguous_evidence_blocks_after_restart(tmp_path):
    store = BrokerEvidenceStore(tmp_path); store.record_intent("key")
    store.record_terminal(EvidenceRecord("key", TerminalOutcomeState.OUTCOME_AMBIGUOUS_BLOCKED, "test"))
    assert BrokerEvidenceStore(tmp_path).epoch_blocked


def test_ambiguous_is_permanent_even_via_rejected_before_start_bypass(tmp_path):
    """NIT: the store itself refuses any write over an ambiguous record.

    ``rejected_before_start`` calls ``_append`` directly (bypassing
    ``validate_terminal_transition``); the storage-layer guard must still refuse
    it so a buggy caller cannot escape the block.
    """
    store = BrokerEvidenceStore(tmp_path)
    store.record_intent("key")
    store.record_terminal(EvidenceRecord("key", TerminalOutcomeState.OUTCOME_AMBIGUOUS_BLOCKED, "amb"))

    # The bypass path is now refused at the storage layer.
    with pytest.raises(ValueError):
        store.rejected_before_start("key", "sneaky-overwrite")

    # A fresh terminal write is likewise refused.
    with pytest.raises(ValueError):
        store.record_terminal(EvidenceRecord("key", TerminalOutcomeState.EFFECT_TERMINAL_OBSERVED, "x"))

    # The block survives; the record is still ambiguous.
    replayed = BrokerEvidenceStore(tmp_path).replay()["key"]
    assert replayed.state is TerminalOutcomeState.OUTCOME_AMBIGUOUS_BLOCKED
    assert BrokerEvidenceStore(tmp_path).epoch_blocked
