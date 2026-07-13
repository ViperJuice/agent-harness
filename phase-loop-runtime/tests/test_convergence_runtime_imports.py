import phase_loop_runtime.convergence as convergence
from phase_loop_runtime.convergence import FencedAdmissionFactory, RepositoryDispatchRequest, SupportedConvergenceVersions, refresh_downstream_after_merge
from phase_loop_runtime.train_runner import CoordinatorRuntime


def test_runtime_import_surface_exposes_runtime_gate():
    for name in ("default_convergence_event_log_path", "record_intent", "record_outcome", "read_convergence_events", "recover_train_state", "reconcile_train_state"):
        assert hasattr(convergence, name)


def test_convergence_runtime_exports_and_coordinator_boundary(tmp_path):
    assert FencedAdmissionFactory and RepositoryDispatchRequest and SupportedConvergenceVersions and refresh_downstream_after_merge
    runtime = CoordinatorRuntime("train", tmp_path, "train.md", "digest", "workspace", broker_client=object())
    assert runtime.train_id == "train"
