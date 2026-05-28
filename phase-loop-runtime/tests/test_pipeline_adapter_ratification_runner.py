from __future__ import annotations

import json
from unittest.mock import patch

from phase_loop_runtime.events import read_events
from phase_loop_runtime.launcher import AuthPreflightResult, LaunchResult
from phase_loop_runtime.runner import run_loop
from phase_loop_test_utils import commit_fixture_paths, make_repo, write_phase_plan


def test_runner_emits_ratification_when_enabled_pipeline_reaches_gate(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    (repo / ".pipeline").mkdir()
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(
        repo,
        "RUNNER",
        roadmap,
        extra_frontmatter={
            "merge_policy": '{"on_pass": "required", "approvers": ["ops"]}',
            "ratification_gate": "complete",
        },
        body=(
            "# RUNNER\n\n"
            "## Lanes\n\n"
            "### SL-0 - Runner\n"
            "- **Owned files**: none\n"
            "- **Interfaces provided**: `IF-0-EVENT-1`\n"
        ),
    )
    commit_fixture_paths(repo, "add pipeline runner plan", plan)
    monkeypatch.setenv("PHASE_LOOP_BRANCHGOV_ENABLE", "true")

    def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
        closeout = {
            "terminal_status": "complete",
            "verification_status": "passed",
            "dirty_paths": [],
            "produced_if_gates": ["IF-0-EVENT-1"],
            "next_action": None,
            "blocker_class": None,
            "blocker_summary": None,
            "human_required": None,
            "required_human_inputs": [],
        }
        return LaunchResult(command=spec.command, returncode=0, output=json.dumps(closeout), executor=spec.executor)

    with (
        patch("phase_loop_runtime.runner.run_auth_preflight", return_value=AuthPreflightResult(ok=True, metadata={})),
        patch("phase_loop_runtime.runner._ensure_pipeline_branch_before_dispatch", return_value=None),
        patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch),
    ):
        run_loop(repo, roadmap, phase="RUNNER")

    events = [event for event in read_events(repo) if event.get("event_type") == "ratification.passed"]
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload == json.loads((repo / ".pipeline" / "ratification-trigger.json").read_text(encoding="utf-8"))
    assert payload["merge_policy"] == {"on_pass": "required", "approvers": ["ops"]}
    assert payload["audit"]["terminal_status"] == "complete"
    assert payload["audit"]["produced_if_gates"] == ["IF-0-EVENT-1"]
