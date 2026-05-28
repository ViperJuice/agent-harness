from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.events import read_events
from phase_loop_runtime.launcher import AuthPreflightResult, LaunchResult
from phase_loop_runtime.runner import run_loop
from phase_loop_runtime.state import load_state
from phase_loop_test_utils import build_fake_automation_output, commit_fixture_paths, make_repo, write_phase_plan

GOLDEN_ROOT = Path(__file__).resolve().parent / "data" / "standalone_golden"


def test_pipeline_adapter_preserves_standalone_ledgers(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(repo, "RUNNER", roadmap)
    commit_fixture_paths(repo, "add runner plan", plan)
    monkeypatch.delenv("PHASE_LOOP_PIPELINE_MODE", raising=False)

    assert not (repo / ".pipeline").exists()
    assert not (repo / ".github" / "workflows" / "pipeline-bootstrap.yml").exists()

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

    with (
        patch("phase_loop_runtime.runner.run_auth_preflight", return_value=AuthPreflightResult(ok=True, metadata={})),
        patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch),
    ):
        run_loop(repo, roadmap, phase="RUNNER")

    state = load_state(repo)
    assert state is not None

    normalized_events = _normalize_events(read_events(repo))
    normalized_state = _normalize_state(state.to_json())

    expected_events = json.loads((GOLDEN_ROOT / "events.jsonl").read_text(encoding="utf-8"))
    expected_state = json.loads((GOLDEN_ROOT / "state.json").read_text(encoding="utf-8"))

    assert normalized_events == expected_events
    assert normalized_state == expected_state


def _normalize_events(events: list[dict]) -> list[dict]:
    normalized = []
    for event in events:
        metadata = event.get("metadata", {})
        terminal = metadata.get("terminal_summary", {})
        child = metadata.get("child_automation", {})
        transition = metadata.get("state_transition", {})
        dispatch = metadata.get("dispatch_decision", {})
        normalized.append(
            {
                "action": event.get("action"),
                "phase": event.get("phase"),
                "status": event.get("status"),
                "model": event.get("model"),
                "reasoning_effort": event.get("reasoning_effort"),
                "source": event.get("source"),
                "selected_executor": event.get("selected_executor"),
                "transition": {
                    "from": transition.get("from"),
                    "to": transition.get("to"),
                    "trigger": transition.get("trigger"),
                    "reason": transition.get("reason"),
                },
                "dispatch": {
                    "selected_executor": dispatch.get("selected_executor"),
                    "selected_via": dispatch.get("selected_via"),
                    "source": dispatch.get("source"),
                },
                "child_automation": {
                    "status": child.get("automation_status"),
                    "verification_status": child.get("automation_verification_status"),
                    "human_required": child.get("automation_human_required"),
                    "blocker_class": child.get("automation_blocker_class"),
                },
                "terminal_summary": {
                    "terminal_status": terminal.get("terminal_status"),
                    "verification_status": terminal.get("verification_status"),
                    "phase_owned_dirty": terminal.get("phase_owned_dirty"),
                    "dirty_paths": terminal.get("dirty_paths"),
                },
            }
        )
    return normalized


def _normalize_state(state: dict) -> dict:
    latest_metric = state.get("latest_metric") or {}
    return {
        "current_phase": state.get("current_phase"),
        "last_action": state.get("last_action"),
        "model": state.get("model"),
        "reasoning_effort": state.get("reasoning_effort"),
        "source": state.get("source"),
        "pipeline_mode": state.get("pipeline_mode"),
        "human_required": state.get("human_required"),
        "dirty_paths": state.get("dirty_paths"),
        "phase_owned_dirty": state.get("phase_owned_dirty"),
        "phase_owned_dirty_paths": state.get("phase_owned_dirty_paths"),
        "unowned_dirty_paths": state.get("unowned_dirty_paths"),
        "pre_existing_dirty_paths": state.get("pre_existing_dirty_paths"),
        "phases": state.get("phases"),
        "latest_metric": {
            "action": latest_metric.get("action"),
            "executor": latest_metric.get("executor"),
            "model": latest_metric.get("model"),
            "effort": latest_metric.get("effort"),
            "phase": latest_metric.get("phase"),
            "terminal_status": latest_metric.get("terminal_status"),
            "verification_status": latest_metric.get("verification_status"),
            "returncode": latest_metric.get("returncode"),
        },
        "metrics_summary": state.get("metrics_summary"),
        "work_units": state.get("work_units"),
    }
