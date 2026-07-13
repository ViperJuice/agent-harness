from phase_loop_runtime.convergence.event_log import RecoveredTrainState
from phase_loop_runtime.convergence.reconcile import ExactStateProbes, reconcile_train_state
from phase_loop_runtime.convergence.contracts import InvalidationTrigger


def test_reconcile_requires_all_authority_and_reports_invalidation():
    state = RecoveredTrainState("t")
    missing = reconcile_train_state(state, ExactStateProbes())
    assert missing.blocker_reason and "git" in missing.blocker_reason
    probes = ExactStateProbes(
        git=lambda _: {"head_changed": "true"}, github=lambda _: {},
        provider=lambda _: {}, registry=lambda _: {},
    )
    verdict = reconcile_train_state(state, probes)
    assert InvalidationTrigger.EFFECTIVE_CODE_CHANGED in verdict.binding.invalidation_triggers
