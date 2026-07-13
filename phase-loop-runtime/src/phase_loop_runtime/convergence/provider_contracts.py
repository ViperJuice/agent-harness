"""Provider-completion contracts frozen before broker enforcement is introduced."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ProviderCompletionClassification(str, Enum):
    """Evidence-backed classification of a provider operation."""

    SUPPORTED = "supported"
    HUMAN_EXECUTED = "human-executed"
    UNSUPPORTED = "unsupported"


class ProviderAutomationDisposition(str, Enum):
    """Whether the future broker may automate a classified operation."""

    AUTOMATED = "automated"
    HUMAN_EXECUTED = "human-executed"
    BLOCKED = "blocked"


class TerminalOutcomeState(str, Enum):
    """Terminal-outcome state machine for a provider operation."""

    REJECTED_BEFORE_START = "rejected_before_start"
    PROVIDER_CALL_IN_FLIGHT = "provider_call_in_flight"
    EFFECT_TERMINAL_OBSERVED = "effect_terminal_observed"
    NO_EFFECT_TERMINAL_PROVEN = "no_effect_terminal_proven"
    OUTCOME_AMBIGUOUS_BLOCKED = "outcome_ambiguous_blocked"


@dataclass(frozen=True)
class ProviderCompletionContract:
    """Completion evidence required before a provider operation becomes automatable."""

    verb: str
    provider: str
    classification: ProviderCompletionClassification
    disposition: ProviderAutomationDisposition
    status_endpoint: str
    idempotency_key_supported: str
    terminal_success_evidence: str
    terminal_no_effect_evidence: str
    non_late_commit_guarantee: str
    guaranteed_processing_horizon: str
    expected_version_predicate: str
    revocation_affects_accepted: str
    stabilization_drain_interval: str

    def __post_init__(self) -> None:
        if not self.verb or not self.provider:
            raise ValueError("provider contracts require verb and provider")
        if not all(
            (
                self.status_endpoint,
                self.idempotency_key_supported,
                self.terminal_success_evidence,
                self.terminal_no_effect_evidence,
                self.non_late_commit_guarantee,
                self.guaranteed_processing_horizon,
                self.expected_version_predicate,
                self.revocation_affects_accepted,
                self.stabilization_drain_interval,
            )
        ):
            raise ValueError("provider completion evidence fields must be explicit")
        if self.classification is ProviderCompletionClassification.SUPPORTED:
            if self.disposition is not ProviderAutomationDisposition.AUTOMATED:
                raise ValueError("supported provider operations must be automatable")
            if "N/A" in {self.status_endpoint, self.terminal_success_evidence, self.terminal_no_effect_evidence}:
                raise ValueError("supported operations require terminal evidence")


# Repository-derived mutation inventory: current GitHub CLI mutation paths have no
# contractually sufficient terminal no-effect guarantee, so all remain human-executed.
AUTOMATED_PROVIDER_VERBS = frozenset(
    (verb, "github")
    for verb in ("publish", "merge", "release", "package", "publish_committed_branch")
)

PROVIDER_COMPLETION_CLASSIFICATIONS = tuple(
    ProviderCompletionContract(
        verb=verb,
        provider=provider,
        classification=ProviderCompletionClassification.HUMAN_EXECUTED,
        disposition=ProviderAutomationDisposition.HUMAN_EXECUTED,
        status_endpoint="N/A",
        idempotency_key_supported="N/A",
        terminal_success_evidence="N/A",
        terminal_no_effect_evidence="N/A",
        non_late_commit_guarantee="N/A",
        guaranteed_processing_horizon="N/A",
        expected_version_predicate="required before future dispatch",
        revocation_affects_accepted="N/A",
        stabilization_drain_interval="N/A",
    )
    for verb, provider in sorted(AUTOMATED_PROVIDER_VERBS)
)


def validate_terminal_transition(
    current: TerminalOutcomeState,
    target: TerminalOutcomeState,
    *,
    pre_linearization_proven: bool = False,
) -> bool:
    """Return whether a terminal transition preserves the fail-closed outcome rule."""
    if current is TerminalOutcomeState.PROVIDER_CALL_IN_FLIGHT:
        return target in {
            TerminalOutcomeState.EFFECT_TERMINAL_OBSERVED,
            TerminalOutcomeState.NO_EFFECT_TERMINAL_PROVEN,
            TerminalOutcomeState.OUTCOME_AMBIGUOUS_BLOCKED,
        }
    return (
        current is TerminalOutcomeState.REJECTED_BEFORE_START
        and pre_linearization_proven
        and target is TerminalOutcomeState.REJECTED_BEFORE_START
    )
