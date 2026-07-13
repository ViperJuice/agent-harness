from phase_loop_runtime.convergence.contracts import AdmissionRequest
from phase_loop_runtime.train_roadmap import parse_train_roadmap
from phase_loop_runtime.train_runner import CoordinatorRuntime, run_train


def test_runtime_without_broker_blocks_before_any_train_action(tmp_path):
    runtime = CoordinatorRuntime("train", tmp_path, "train.md", "digest", "workspace")
    result = run_train(None, tmp_path / "ledger", resolve_workspace=lambda _: tmp_path, coordinator_runtime=runtime)
    assert result["status"] == "blocked"


# --- Blocker 1: broker wired into run_train → publish_fn --------------------
_TRAIN_1NODE_MD = """\
# Release Train: broker-wire-test

## Nodes

### Node: repo-a / specs/plan-a.md

**Depends on:** (none)
**Channel:** (none)
"""


def _capturing_publish(captured):
    def _publish(workspace, owned_paths, *, draft, **kw):
        captured.append(kw)
        return {"status": "published", "branch": f"feat/{workspace.name}", "head_sha": "s", "pr_url": "u"}
    return _publish


def _run_kwargs(tmp_path, captured):
    roadmap = parse_train_roadmap(_TRAIN_1NODE_MD)
    ws_map = {n.node_id: tmp_path / n.repo for n in roadmap.nodes}
    return roadmap, dict(
        ledger_path=tmp_path / "ledger",
        resolve_workspace=lambda n: ws_map[n.node_id],
        _run_loop=lambda *a, **kw: (None, []),
        _publish=_capturing_publish(captured),
        _set_upstream_ref_fn=lambda *a, **kw: [],
        _preflight_fn=lambda nodes, resolve_workspace: [],
        _pr_is_open=lambda ws, br: False,
    )


def test_broker_and_admission_threaded_into_publish_when_runtime_present(tmp_path):
    captured = []
    roadmap, kwargs = _run_kwargs(tmp_path, captured)
    sentinel_admission = AdmissionRequest("a", 1, "f", "d", "v", "scope", "canned-key")
    broker = object()
    runtime = CoordinatorRuntime("train", tmp_path, "train.md", "digest", "workspace", broker_client=broker)

    run_train(
        roadmap,
        kwargs.pop("ledger_path"),
        coordinator_runtime=runtime,
        _admission_fn=lambda *a, **kw: sentinel_admission,
        **kwargs,
    )

    assert captured, "publish must be called"
    assert all(kw.get("broker_client") is broker for kw in captured)
    assert all(kw.get("admission") is sentinel_admission for kw in captured)


def test_publish_receives_no_broker_kwargs_without_runtime(tmp_path):
    """Backward-compat: legacy callers (no coordinator_runtime) publish as before."""
    captured = []
    roadmap, kwargs = _run_kwargs(tmp_path, captured)

    run_train(roadmap, kwargs.pop("ledger_path"), **kwargs)

    assert captured, "publish must be called"
    assert all("broker_client" not in kw and "admission" not in kw for kw in captured)
