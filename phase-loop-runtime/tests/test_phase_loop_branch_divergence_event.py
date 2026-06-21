from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.events import read_events
from phase_loop_runtime.launcher import AuthPreflightResult, LaunchResult
from phase_loop_runtime.runner import run_loop
from phase_loop_test_utils import commit_fixture_paths, make_repo, write_phase_plan


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )


def test_runner_emits_branch_switched_event_on_divergence(tmp_path, monkeypatch):
    # #44 end-to-end: operator on a NON-convention branch (the issue's repro), with
    # branch governance active. The runner switches to consiliency/pipeline/<v> and
    # must emit a coordinator.branch_switched event instead of switching silently.
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(
        repo,
        "RUNNER",
        roadmap,
        body=(
            "# RUNNER\n\n## Lanes\n\n### SL-0 - Runner\n"
            "- **Owned files**: none\n- **Interfaces provided**: `IF-0-EVENT-1`\n"
        ),
    )
    commit_fixture_paths(repo, "add runner plan", plan)
    # origin/main carries the roadmap so it survives the switch to the convention branch.
    _git(repo, "branch", "-M", "main")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    _git(repo, "checkout", "-q", "-b", "consiliency/ci/v1-restructure")

    monkeypatch.setenv("PHASE_LOOP_BRANCHGOV_ENABLE", "true")
    monkeypatch.setenv("PHASE_LOOP_PIPELINE_MODE", "true")

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
        patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch),
    ):
        run_loop(repo, roadmap, phase="RUNNER")

    switched = [e for e in read_events(repo) if e.get("action") == "coordinator.branch_switched"]
    assert switched, "expected a coordinator.branch_switched event on divergence"
    md = switched[0].get("metadata", {}).get("coordinator", {})
    assert md.get("original_branch") == "consiliency/ci/v1-restructure"
    assert md.get("target_branch") == "consiliency/pipeline/v1"
    assert md.get("branch_action") == "create"
    assert md.get("diverged") is True


def test_runner_no_branch_switched_event_when_already_on_convention(tmp_path, monkeypatch):
    # Negative: already on the convention branch → no divergence, no event.
    repo = make_repo(tmp_path)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    plan = write_phase_plan(
        repo,
        "RUNNER",
        roadmap,
        body=(
            "# RUNNER\n\n## Lanes\n\n### SL-0 - Runner\n"
            "- **Owned files**: none\n- **Interfaces provided**: `IF-0-EVENT-1`\n"
        ),
    )
    commit_fixture_paths(repo, "add runner plan", plan)
    _git(repo, "branch", "-M", "main")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    _git(repo, "checkout", "-q", "-b", "consiliency/pipeline/v1")

    monkeypatch.setenv("PHASE_LOOP_BRANCHGOV_ENABLE", "true")
    monkeypatch.setenv("PHASE_LOOP_PIPELINE_MODE", "true")

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
        patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch),
    ):
        run_loop(repo, roadmap, phase="RUNNER")

    switched = [e for e in read_events(repo) if e.get("action") == "coordinator.branch_switched"]
    assert not switched, "no branch_switched event expected when already on the convention branch"
