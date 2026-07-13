from phase_loop_runtime.convergence.provider_contracts import (
    AUTOMATED_PROVIDER_VERBS,
    PROVIDER_COMPLETION_CLASSIFICATIONS,
    ProviderAutomationDisposition,
    ProviderCompletionClassification,
    TerminalOutcomeState,
    validate_terminal_transition,
)


def test_repository_inventory_has_one_explicit_non_automated_classification_per_pair():
    pairs = {(item.verb, item.provider) for item in PROVIDER_COMPLETION_CLASSIFICATIONS}
    assert pairs == AUTOMATED_PROVIDER_VERBS
    assert all(item.classification is ProviderCompletionClassification.HUMAN_EXECUTED for item in PROVIDER_COMPLETION_CLASSIFICATIONS)
    assert all(item.disposition is ProviderAutomationDisposition.HUMAN_EXECUTED for item in PROVIDER_COMPLETION_CLASSIFICATIONS)
    for item in PROVIDER_COMPLETION_CLASSIFICATIONS:
        assert all(getattr(item, field) for field in (
            "status_endpoint", "idempotency_key_supported", "terminal_success_evidence",
            "terminal_no_effect_evidence", "non_late_commit_guarantee", "guaranteed_processing_horizon",
            "expected_version_predicate", "revocation_affects_accepted", "stabilization_drain_interval",
        ))


def test_in_flight_has_only_three_terminal_exits_and_no_timeout_or_override_escape():
    exits = {state for state in TerminalOutcomeState if validate_terminal_transition(TerminalOutcomeState.PROVIDER_CALL_IN_FLIGHT, state)}
    assert exits == {
        TerminalOutcomeState.EFFECT_TERMINAL_OBSERVED,
        TerminalOutcomeState.NO_EFFECT_TERMINAL_PROVEN,
        TerminalOutcomeState.OUTCOME_AMBIGUOUS_BLOCKED,
    }
    assert not validate_terminal_transition(TerminalOutcomeState.PROVIDER_CALL_IN_FLIGHT, TerminalOutcomeState.REJECTED_BEFORE_START)
    assert validate_terminal_transition(TerminalOutcomeState.REJECTED_BEFORE_START, TerminalOutcomeState.REJECTED_BEFORE_START, pre_linearization_proven=True)
