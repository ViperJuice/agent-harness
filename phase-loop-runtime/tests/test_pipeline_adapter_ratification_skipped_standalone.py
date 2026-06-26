from __future__ import annotations

import json
from unittest.mock import patch

from phase_loop_runtime.events import read_events
from phase_loop_runtime.launcher import LaunchResult
from phase_loop_runtime.runner import run_loop
from phase_loop_test_utils import commit_fixture_paths, make_repo, write_phase_plan

import pytest

# TESTDECOUPLE SL-1 (overlay-dependent): builds a skill/adoption bundle or runs the
# runtime execute path, which resolves the dotfiles skill-source / profile overlay
# (claude-config/*, codex-config/* …) absent standalone. Run-time integration: the
# conftest hook skips it when no dotfiles tree is reachable.
pytestmark = pytest.mark.dotfiles_integration


def test_runner_skips_ratification_without_pipeline_markers(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(
        repo,
        "RUNNER",
        roadmap,
        extra_frontmatter={"merge_policy": '{"on_pass": "required", "approvers": ["ops"]}'},
        body=(
            "# RUNNER\n\n"
            "## Lanes\n\n"
            "### SL-0 - Runner\n"
            "- **Owned files**: none\n"
            "- **Interfaces provided**: `IF-0-EVENT-1`\n"
        ),
    )
    commit_fixture_paths(repo, "add standalone runner plan", plan)
    monkeypatch.setenv("PHASE_LOOP_BRANCHGOV_ENABLE", "true")
    monkeypatch.delenv("PHASE_LOOP_PIPELINE_MODE", raising=False)

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

    with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
        run_loop(repo, roadmap, phase="RUNNER")

    assert not [event for event in read_events(repo) if event.get("event_type") == "ratification.passed"]
    assert not (repo / ".pipeline" / "ratification-trigger.json").exists()
