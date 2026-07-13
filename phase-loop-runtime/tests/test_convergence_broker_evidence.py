from phase_loop_runtime.convergence.broker.evidence import BrokerEvidenceStore, EvidenceRecord
from phase_loop_runtime.convergence.provider_contracts import TerminalOutcomeState

def test_ambiguous_evidence_blocks_after_restart(tmp_path):
    store = BrokerEvidenceStore(tmp_path); intent = store.record_intent("key")
    store.record_terminal(EvidenceRecord("key", TerminalOutcomeState.OUTCOME_AMBIGUOUS_BLOCKED, "test"))
    assert BrokerEvidenceStore(tmp_path).epoch_blocked
