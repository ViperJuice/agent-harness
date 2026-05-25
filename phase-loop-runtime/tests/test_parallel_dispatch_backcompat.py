from unittest.mock import patch

from phase_loop_runtime.events import read_events
from phase_loop_runtime.launcher import LaunchResult
from phase_loop_runtime.runner import run_loop
from phase_loop_runtime.state import load_state
from phase_loop_test_utils import build_fake_automation_output, commit_fixture_paths, make_repo, write_phase_plan


def test_non_parallel_dispatch_emits_no_coordinator_events(tmp_path):
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "RUNNER", roadmap)
    commit_fixture_paths(repo, "add runner plan", plan)

    def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
        return LaunchResult(
            command=spec.command,
            returncode=0,
            output=build_fake_automation_output(
                status="complete",
                verification_status="passed",
                artifact=str(plan),
                artifact_state="tracked",
            ),
            executor=spec.executor,
            log_path=str(log_path) if log_path else None,
        )

    with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
        snapshot, results = run_loop(repo, roadmap, phase="RUNNER")

    assert len(results) == 1
    assert snapshot.phases["RUNNER"] == "complete"
    assert load_state(repo) is not None
    assert not [event for event in read_events(repo) if str(event.get("action", "")).startswith("coordinator.")]
