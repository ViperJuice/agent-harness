"""The sole provider-agnostic mutation boundary."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from phase_loop_runtime.convergence.contracts import BrokerRequest, BrokerTerminalEvidence, PublishCommittedBranchResult
from phase_loop_runtime.convergence.provider_contracts import PROVIDER_COMPLETION_CLASSIFICATIONS, ProviderCompletionClassification, TerminalOutcomeState
from .evidence import BrokerEvidenceStore, EvidenceRecord


class BrokerProviderAdapter(Protocol):
    def execute(self, request: BrokerRequest) -> tuple[PublishCommittedBranchResult | None, BrokerTerminalEvidence]: ...
class BrokerClient(Protocol):
    def execute(self, request: BrokerRequest) -> "BrokerExecutionResult": ...

@dataclass(frozen=True)
class BrokerExecutionResult:
    accepted: bool
    evidence: BrokerTerminalEvidence
    publish_result: PublishCommittedBranchResult | None = None
    reason: str = ""

def publish_committed_branch_idempotency_key(repo: str, branch: str, head_sha: str) -> str:
    return hashlib.sha256(f"{repo}\0{branch}\0{head_sha}".encode()).hexdigest()

class BrokerService:
    def __init__(self, admission_store, evidence_store: BrokerEvidenceStore, adapter: BrokerProviderAdapter, contracts=PROVIDER_COMPLETION_CLASSIFICATIONS) -> None:
        self.admission_store, self.evidence_store, self.adapter, self.contracts = admission_store, evidence_store, adapter, contracts
    def execute(self, request: BrokerRequest) -> BrokerExecutionResult:
        key = request.admission.idempotency_key
        current = self.evidence_store.replay().get(key)
        if current and current.state is not TerminalOutcomeState.PROVIDER_CALL_IN_FLIGHT:
            return BrokerExecutionResult(current.state is TerminalOutcomeState.EFFECT_TERMINAL_OBSERVED, BrokerTerminalEvidence(key, current.state.value, current.evidence_reference))
        contract = next((c for c in self.contracts if c.verb == request.verb.value and c.provider == "github"), None)
        if contract is None or contract.classification is not ProviderCompletionClassification.SUPPORTED:
            evidence = self.evidence_store.rejected_before_start(key, "provider-classification")
            return BrokerExecutionResult(False, BrokerTerminalEvidence(key, evidence.state.value, evidence.evidence_reference), reason="provider_not_supported")
        if self.evidence_store.epoch_blocked: raise PermissionError("epoch permanently blocked")
        self.admission_store.admit(request); self.evidence_store.record_intent(key)
        try:
            result, evidence = self.adapter.execute(request)
            state = TerminalOutcomeState(evidence.terminal_state)
            recorded = self.evidence_store.record_terminal(EvidenceRecord(key, state, evidence.evidence_reference))
            return BrokerExecutionResult(state is TerminalOutcomeState.EFFECT_TERMINAL_OBSERVED, BrokerTerminalEvidence(key, recorded.state.value, recorded.evidence_reference), result)
        except Exception:
            recorded = self.evidence_store.record_terminal(EvidenceRecord(key, TerminalOutcomeState.OUTCOME_AMBIGUOUS_BLOCKED, "adapter-exception"))
            return BrokerExecutionResult(False, BrokerTerminalEvidence(key, recorded.state.value, recorded.evidence_reference), reason="outcome_ambiguous")
