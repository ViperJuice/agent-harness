from phase_loop_runtime.convergence.broker.admission import LinearizableAdmissionStore
from phase_loop_runtime.convergence.contracts import AdmissionRequest

def test_admission_replays_idempotently(tmp_path):
    request = AdmissionRequest("a", 1, "f", "d", "v", "scope", "key")
    store = LinearizableAdmissionStore(tmp_path, lambda _: True)
    assert store.admit(request) == store.admit(request)
    assert len(store.replay()) == 1
