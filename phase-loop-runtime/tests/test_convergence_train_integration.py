from phase_loop_runtime.train_runner import CoordinatorRuntime, run_train


def test_runtime_without_broker_blocks_before_any_train_action(tmp_path):
    runtime = CoordinatorRuntime("train", tmp_path, "train.md", "digest", "workspace")
    result = run_train(None, tmp_path / "ledger", resolve_workspace=lambda _: tmp_path, coordinator_runtime=runtime)
    assert result["status"] == "blocked"
