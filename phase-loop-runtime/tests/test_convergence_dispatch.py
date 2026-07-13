from phase_loop_runtime.convergence.dispatch import RepositoryDispatchRequest, dispatch_ready_nodes
from phase_loop_runtime.train_ledger import ConvergenceResultEnvelope, ConvergenceResultStatus


def test_dispatches_disjoint_repositories_and_serializes_same_repository():
    decisions = []
    requests = [
        RepositoryDispatchRequest("one", "a", ("a.py",), True),
        RepositoryDispatchRequest("two", "b", ("b.py",), True),
        RepositoryDispatchRequest("three", "a", ("c.py",), True),
    ]
    result = dispatch_ready_nodes(requests, lambda item: ConvergenceResultEnvelope(ConvergenceResultStatus.COMPLETED, item.node_id), persist_decision=decisions.append)
    assert set(result) == {"one", "two", "three"}
    assert any(not item.allowed and "same-repo" in item.reason for item in decisions)
