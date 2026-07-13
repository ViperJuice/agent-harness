"""Frozen convergence contracts only; runtime and broker behavior are intentionally absent."""

from phase_loop_runtime.convergence.contracts import (
    AdmissionRequest,
    AuthoritySource,
    BrokerRequest,
    BrokerTerminalEvidence,
    BrokerVerb,
    InvalidationTrigger,
    PublishCommittedBranchResult,
    ReconciliationBinding,
    ResourceIsolationDecision,
    evaluate_resource_isolation,
)
from phase_loop_runtime.convergence.provider_contracts import (
    PROVIDER_COMPLETION_CLASSIFICATIONS,
    ProviderAutomationDisposition,
    ProviderCompletionClassification,
    ProviderCompletionContract,
    TerminalOutcomeState,
    validate_terminal_transition,
)
from phase_loop_runtime.train_ledger import (
    CoordinatorEvent,
    CoordinatorEventKind,
    ConvergenceResultEnvelope,
    ConvergenceResultStatus,
    normalize_legacy_ledger_record,
)
from phase_loop_runtime.convergence.event_log import (
    RecoveredTrainState,
    default_convergence_event_log_path,
    read_convergence_events,
    record_intent,
    record_outcome,
    recover_train_state,
)
from phase_loop_runtime.convergence.reconcile import ExactStateProbes, ReconciliationVerdict, reconcile_train_state
from phase_loop_runtime.convergence.status import TrainStatusSnapshot, build_train_status, render_train_status
from phase_loop_runtime.convergence.adapters import AdapterExecutionRequest, run_claude_adapter, run_codex_adapter, run_outside_agent_adapter
from phase_loop_runtime.convergence.broker import (
    AdmissionRecord, BrokerAdmissionPolicy, BrokerClient, BrokerEnvironmentBoundary,
    BrokerEvidenceStore, BrokerExecutionResult, BrokerProviderAdapter, BrokerService,
    EvidenceRecord, GitHubBrokerAdapter, LinearizableAdmissionStore,
    publish_committed_branch_idempotency_key,
)

__all__ = [
    "AdmissionRequest",
    "AuthoritySource",
    "BrokerRequest",
    "BrokerTerminalEvidence",
    "BrokerVerb",
    "CoordinatorEvent",
    "CoordinatorEventKind",
    "ConvergenceResultEnvelope",
    "ConvergenceResultStatus",
    "InvalidationTrigger",
    "PROVIDER_COMPLETION_CLASSIFICATIONS",
    "ProviderAutomationDisposition",
    "ProviderCompletionClassification",
    "ProviderCompletionContract",
    "PublishCommittedBranchResult",
    "ReconciliationBinding",
    "ResourceIsolationDecision",
    "TerminalOutcomeState",
    "evaluate_resource_isolation",
    "normalize_legacy_ledger_record",
    "validate_terminal_transition",
    "AdapterExecutionRequest",
    "ExactStateProbes",
    "RecoveredTrainState",
    "ReconciliationVerdict",
    "TrainStatusSnapshot",
    "build_train_status",
    "default_convergence_event_log_path",
    "read_convergence_events",
    "reconcile_train_state",
    "record_intent",
    "record_outcome",
    "recover_train_state",
    "render_train_status",
    "run_claude_adapter",
    "run_codex_adapter",
    "run_outside_agent_adapter",
    "AdmissionRecord", "BrokerAdmissionPolicy", "BrokerClient", "BrokerEnvironmentBoundary",
    "BrokerEvidenceStore", "BrokerExecutionResult", "BrokerProviderAdapter", "BrokerService",
    "EvidenceRecord", "GitHubBrokerAdapter", "LinearizableAdmissionStore",
    "publish_committed_branch_idempotency_key",
]
