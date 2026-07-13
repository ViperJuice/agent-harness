from phase_loop_runtime.convergence.broker.credsep import BrokerEnvironmentBoundary, build_non_force_branch_ref

def test_only_broker_role_receives_mutation_credentials():
    env = {"GH_TOKEN": "value", "NORMAL": "yes"}
    assert "GH_TOKEN" in BrokerEnvironmentBoundary().environment_for("broker", env)
    assert "GH_TOKEN" not in BrokerEnvironmentBoundary().environment_for("worker", env)
    assert build_non_force_branch_ref("feature/broker") == "refs/heads/feature/broker"
