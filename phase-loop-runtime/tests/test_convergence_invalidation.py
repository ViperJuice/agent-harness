from phase_loop_runtime.convergence.reconcile import ExactStateProbes, reconcile_before_action
from phase_loop_runtime.convergence.event_log import RecoveredTrainState


def test_action_reconciliation_invalidates_approval_and_verification():
    probes = ExactStateProbes(git=lambda _: {"head_changed": "true"}, github=lambda _: {}, provider=lambda _: {}, registry=lambda _: {})
    result = reconcile_before_action(RecoveredTrainState("train", verification_valid=True, approval_valid=True), probes, "publish")
    assert not result.admitted
    assert not result.verification_valid and not result.approval_valid
