from phase_loop_runtime.convergence.broker.verbs import publish_committed_branch_idempotency_key

def test_publish_key_binds_the_canonical_triple():
    assert publish_committed_branch_idempotency_key("r", "b", "h") != publish_committed_branch_idempotency_key("r", "b", "other")
