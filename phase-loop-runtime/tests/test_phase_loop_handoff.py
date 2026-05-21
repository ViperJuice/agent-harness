import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.events import append_event
from phase_loop_runtime.handoff import tui_handoff_path, write_tui_handoff
from phase_loop_runtime.models import DelegationBudget, DelegationRequest, LoopEvent, StateSnapshot, utc_now
from phase_loop_runtime.observability import run_artifacts, write_run_heartbeat
from phase_loop_runtime.launcher import build_launch_request, build_launch_spec
from phase_loop_runtime.profiles import resolve_profile
from phase_loop_runtime.provenance import event_provenance, snapshot_provenance
from phase_loop_runtime.prompts import build_prompt
from phase_loop_runtime.runner import launch_delegated_child
from phase_loop_runtime.state import write_work_unit_state
from phase_loop_runtime.models import WorkUnitIdentity, WorkUnitState
from phase_loop_smoke_utils import append_manual_import_event, isolated_codex_home, write_skill_handoff
from phase_loop_test_utils import make_completed_roadmap_fixture, make_regenesis_amendment_fixture, make_repo, write_phase_plan


class PhaseLoopHandoffTest(unittest.TestCase):
    def test_handoff_includes_delegation_lineage(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("notes.md",))
            request = DelegationRequest(
                request_id="req-handoff",
                product_action="review",
                target_executor="codex",
                reason="Need a bounded review handoff.",
                owned_files=("notes.md",),
                expected_output="Review findings",
                budget=DelegationBudget(max_seconds=60),
            )
            launch_delegated_child(
                repo=repo,
                roadmap=roadmap,
                parent_phase="CONTRACT",
                parent_action="execute",
                plan=plan,
                request=request,
                parent_run_id="run-parent-2",
                dry_run=True,
            )
            snapshot = StateSnapshot(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phases={"CONTRACT": "planned"},
                current_phase="CONTRACT",
                **snapshot_provenance(roadmap),
            )

            text = write_tui_handoff(repo, roadmap, snapshot, action="status").read_text(encoding="utf-8")

            self.assertIn("## Delegation Lineage", text)
            self.assertIn("req-handoff", text)
            self.assertIn("run-parent-2", text)
            self.assertIn("Review findings", text)

    def test_blocked_dirty_worktree_handoff_links_machine_state_and_required_action(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            snapshot = StateSnapshot(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phases={"CONTRACT": "complete", "ACCESS": "blocked", "RUNNER": "unplanned"},
                current_phase="ACCESS",
                last_action="run",
                human_required=True,
                blocker_class="dirty_worktree_conflict",
                blocker_summary="Clear or isolate the dirty worktree before rerunning dispatch.",
                required_human_inputs=("clean worktree",),
                dirty_paths=("README.md", "notes.txt"),
                phase_owned_dirty_paths=("README.md",),
                unowned_dirty_paths=("notes.txt",),
                pre_existing_dirty_paths=("notes.txt",),
                **snapshot_provenance(roadmap),
            )

            path = write_tui_handoff(repo, roadmap, snapshot, action="run")
            text = path.read_text(encoding="utf-8")

            self.assertEqual(path, tui_handoff_path(repo))
            self.assertIn("# Phase Loop TUI Handoff", text)
            self.assertIn("State JSON: `.phase-loop/state.json`", text)
            self.assertIn("Event ledger: `.phase-loop/events.jsonl`", text)
            self.assertIn("## Monitor Command", text)
            self.assertIn("phase-loop monitor", text)
            self.assertIn("* ACCESS: blocked", text)
            self.assertIn("dirty worktree", text.lower())
            self.assertIn("git status --short --branch", text)
            self.assertIn("git fetch origin main --tags --prune", text)
            self.assertIn("clean worktree", text)
            self.assertIn("Dirty Path Classification", text)
            self.assertIn("phase-owned paths: `README.md`", text)
            self.assertIn("unowned paths: `notes.txt`", text)
            self.assertNotIn("secret-value", text)

    def test_handoff_distinguishes_phase_and_work_unit_status(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_work_unit_state(
                repo,
                WorkUnitState(
                    identity=WorkUnitIdentity(phase="RUNNER", kind="lane_execute", lane_id="SL-0", attempt=1),
                    status="awaiting-closeout",
                ),
                roadmap=roadmap,
            )
            snapshot = StateSnapshot(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phases={"RUNNER": "executing"},
                current_phase="RUNNER",
                latest_work_unit={
                    "work_unit_id": "RUNNER.lane_execute.SL-0.1",
                    "identity": {"phase": "RUNNER", "kind": "lane_execute", "lane_id": "SL-0", "attempt": 1},
                    "status": "awaiting-closeout",
                },
                **snapshot_provenance(roadmap),
            )

            text = write_tui_handoff(repo, roadmap, snapshot, action="status").read_text(encoding="utf-8")

            self.assertIn("## Latest Work Unit", text)
            self.assertIn("phase_status: `RUNNER`", text)
            self.assertIn("work_unit_status: `awaiting-closeout`", text)

    def test_blocked_branch_sync_handoff_names_upstream_alignment(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            snapshot = StateSnapshot(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phases={"CONTRACT": "blocked"},
                current_phase="CONTRACT",
                human_required=True,
                blocker_class="branch_sync_conflict",
                blocker_summary="local main is ahead of origin/main",
                required_human_inputs=("align main with origin/main",),
                **snapshot_provenance(roadmap),
            )

            text = write_tui_handoff(repo, roadmap, snapshot, action="run").read_text(encoding="utf-8")

            self.assertIn("branch_sync_conflict", text)
            self.assertIn("local-only commit", text)
            self.assertIn("git rev-parse HEAD origin/main", text)

    def test_handoff_includes_git_topology_for_pr_branch_recovery(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            snapshot = StateSnapshot(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phases={"CONTRACT": "blocked"},
                current_phase="CONTRACT",
                human_required=True,
                blocker_class="branch_sync_conflict",
                blocker_summary="local main is ahead of origin/main",
                required_human_inputs=("align main with origin/main",),
                git_topology={
                    "available": True,
                    "branch": "ga-hardening",
                    "head": "abc123",
                    "base_ref": "origin/main",
                    "ahead_of_base": 2,
                    "behind_base": 0,
                    "target_push_ref": "refs/heads/ga-hardening",
                    "pr_url": "https://github.com/example/repo/pull/123",
                    "pr_review_decision": "REVIEW_REQUIRED",
                    "pr_mergeable": "MERGEABLE",
                    "matching_remote_ref": "origin/codex/ga-hardening",
                    "status_short_branch": "## ga-hardening...origin/main [ahead 2]",
                },
                **snapshot_provenance(roadmap),
            )

            text = write_tui_handoff(repo, roadmap, snapshot, action="status").read_text(encoding="utf-8")

            self.assertIn("## Git Topology", text)
            self.assertIn("Approve and merge PR https://github.com/example/repo/pull/123", text)
            self.assertIn("branch: `ga-hardening`", text)
            self.assertIn("base: `origin/main` (ahead 2, behind 0)", text)
            self.assertIn("target push ref: `refs/heads/ga-hardening`", text)
            self.assertIn("pull request: https://github.com/example/repo/pull/123", text)
            self.assertIn("PR review decision: `REVIEW_REQUIRED`", text)
            self.assertIn("matching remote ref: `origin/codex/ga-hardening`", text)

    def test_handoff_includes_latest_observed_run_artifacts_from_event_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            artifacts = run_artifacts(repo, "ACCESS", "plan", 1, ["codex", "exec"])
            (artifacts["metadata"]).write_text(
                json.dumps(
                    {
                        "skill_bundle_id": "codex:plan:codex-plan-phase",
                        "skill_bundle_sha256": "abc123",
                        "harness_target": "codex",
                        "injection_mode": "prompt_only",
                        "fallback_mode": None,
                        "expected_skill_pack": ["codex-plan-phase"],
                        "recommended_installed_roots": ["~/.codex/skills"],
                        "installed_skill_roots": [],
                        "installed_skill_warnings": [
                            "codex:codex-phase-loop: installed bridge root missing; autonomous injected bundle remains authoritative, but local reentry should run sync-skills --apply"
                        ],
                        "bridge_skill_inventory": [
                            {
                                "harness_target": "codex",
                                "skill_name": "codex-phase-loop",
                                "source_dir": "/repo/codex-config/skills/codex-phase-loop",
                                "recommended_installed_roots": ["~/.codex/skills"],
                                "installed_skill_roots": [],
                                "installed_path": None,
                                "parity_status": "missing_root",
                                "repair_target": "/home/test/.codex/skills/codex-phase-loop",
                            }
                        ],
                        "plugin_bundle_artifacts": {
                            "plugin_dir": "/repo/.phase-loop/runs/x/claude-bundle/plugin",
                            "settings_path": "/repo/.phase-loop/runs/x/claude-bundle/settings.json",
                            "agents_path": "/repo/.phase-loop/runs/x/claude-bundle/agents.json",
                            "mcp_config_path": "/repo/.phase-loop/runs/x/claude-bundle/mcp.json",
                            "artifact_names": ["plugin-dir", "settings", "agents", "mcp-config"],
                        },
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            write_run_heartbeat(
                artifacts["heartbeat"],
                {
                    "process_alive": True,
                    "quiet_level": "quiet",
                    "elapsed_seconds": 120,
                    "seconds_since_log_update": 75,
                    "recommended_action": "Continue observing.",
                    "nudge_prompt": "Status check: report current status.",
                },
            )
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="ACCESS",
                    action="run",
                    status="planned",
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="fixture",
                    metadata={"artifacts": {key: str(value) for key, value in artifacts.items()}},
                    **event_provenance(roadmap, "ACCESS"),
                ),
            )
            snapshot = StateSnapshot(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phases={"CONTRACT": "complete", "ACCESS": "planned", "RUNNER": "unplanned"},
                current_phase="ACCESS",
                **snapshot_provenance(roadmap),
            )

            text = write_tui_handoff(repo, roadmap, snapshot, action="status").read_text(encoding="utf-8")

            self.assertIn("Latest launch metadata:", text)
            self.assertIn("Latest run log:", text)
            self.assertIn("Latest heartbeat:", text)
            self.assertIn("## Observed Liveness", text)
            self.assertIn("## Injected Context", text)
            self.assertIn("injected bundle: `codex:plan:codex-plan-phase`", text)
            self.assertIn("recommended installed roots: `~/.codex/skills`", text)
            self.assertIn("bridge skill `codex-phase-loop` parity: `missing_root`", text)
            self.assertIn("repo-owned plugin dir:", text)

    def test_handoff_surfaces_teamgov_without_claiming_runner_delegation(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(
                repo,
                "RUNNER",
                roadmap,
                body=(
                    "# RUNNER\n\n"
                    "## Lanes\n\n"
                    "### SL-0 - One\n"
                    "- **Owned files**: `src/one.py`\n\n"
                    "### SL-1 - Two\n"
                    "- **Owned files**: `src/two.py`\n"
                ),
            )
            request = build_launch_request(
                executor="claude",
                action="execute",
                repo=repo,
                roadmap=roadmap,
                phase="RUNNER",
                plan=plan,
                model_selection=resolve_profile("execute"),
                prompt_bundle=build_prompt("execute", roadmap, phase="RUNNER", plan=plan, harness_target="claude"),
                json_output=False,
                bypass_approvals=False,
                claude_execution_mode="agent_team",
            )
            spec = build_launch_spec(request)
            run_artifacts(repo, "RUNNER", "execute", 1, spec)
            snapshot = StateSnapshot(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phases={"RUNNER": "planned"},
                current_phase="RUNNER",
                **snapshot_provenance(roadmap),
            )

            text = write_tui_handoff(repo, roadmap, snapshot, action="status").read_text(encoding="utf-8")

            self.assertIn("Claude execution mode: `agent_team`", text)
            self.assertIn("TEAMGOV maturity: `experimental`", text)
            self.assertIn("phase team eligibility: `disjoint_write_lanes`", text)
            self.assertIn("## Native Team Ledger", text)
            self.assertIn("task snapshot:", text)
            self.assertIn("hook manifest:", text)
            self.assertIn("hook inventory: `TaskCreated, TaskCompleted, TeammateIdle, SubagentStop, PostToolBatch, WorktreeCreate`", text)
            self.assertIn("task snapshot freshness: `fresh`", text)
            self.assertIn("wait classification: `claude_agent_team_active`", text)
            self.assertNotIn("## Delegation Lineage", text)
            self.assertIn("plugin artifact inventory: `plugin-dir, settings, agents, mcp-config`", text)
            self.assertIn("installed-skill parity warnings:", text)
            self.assertIn("output.log", text)
            self.assertNotIn("secret-value", text)

    def test_handoff_surfaces_cross_harness_reentry_context_and_manual_import(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = make_repo(root)
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            with isolated_codex_home(root) as codex_home:
                write_skill_handoff(codex_home, repo, "gemini-execute-phase", "RUNNER", "complete", plan)
                append_manual_import_event(
                    repo,
                    roadmap,
                    "RUNNER",
                    "complete",
                    harness="claude",
                    skill="claude-execute-phase",
                    artifact=plan,
                    installed_skill_warnings=(
                        "claude:claude-phase-loop: installed bridge root missing; autonomous injected bundle remains authoritative, but local reentry should run sync-skills --apply",
                    ),
                    bridge_skill_inventory=(
                        {
                            "harness_target": "claude",
                            "skill_name": "claude-phase-loop",
                            "source_dir": "/repo/claude-config/claude-skills/claude-phase-loop",
                            "recommended_installed_roots": ["~/.claude/skills"],
                            "installed_skill_roots": [],
                            "installed_path": None,
                            "parity_status": "missing_root",
                            "repair_target": "/home/test/.claude/skills/claude-phase-loop",
                        },
                    ),
                )
                snapshot = StateSnapshot(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phases={"CONTRACT": "complete", "ACCESS": "complete", "RUNNER": "complete"},
                    current_phase="RUNNER",
                    **snapshot_provenance(roadmap),
                )

                text = write_tui_handoff(repo, roadmap, snapshot, action="handoff").read_text(encoding="utf-8")

            self.assertIn("## Reentry Context", text)
            self.assertIn("trusted workflow handoff: `gemini-execute-phase`", text)
            self.assertIn("trusted originating harness: `gemini`", text)
            self.assertIn("latest manual import harness: `claude`", text)
            self.assertIn("latest manual import workflow skill: `claude-execute-phase`", text)
            self.assertIn("latest manual import bridge skill `claude-phase-loop` parity: `missing_root`", text)
            self.assertIn("latest manual import parity warnings:", text)

    def test_handoff_falls_back_to_latest_active_run_heartbeat(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            artifacts = run_artifacts(repo, "RUNNER", "execute", 1, ["codex", "exec"])
            write_run_heartbeat(
                artifacts["heartbeat"],
                {
                    "process_alive": True,
                    "quiet_level": "active",
                    "elapsed_seconds": 12,
                    "seconds_since_log_update": 1,
                },
            )
            snapshot = StateSnapshot(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phases={"RUNNER": "planned"},
                current_phase="RUNNER",
                **snapshot_provenance(roadmap),
            )

            text = write_tui_handoff(repo, roadmap, snapshot, action="status").read_text(encoding="utf-8")

            self.assertIn("Latest heartbeat:", text)
            self.assertIn("## Observed Liveness", text)

    def test_access_attempts_are_redacted_metadata_only(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            snapshot = StateSnapshot(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phases={"CONTRACT": "blocked"},
                current_phase="CONTRACT",
                human_required=True,
                blocker_class="missing_secret",
                blocker_summary="Missing credential item.",
                required_human_inputs=("provide credential item",),
                access_attempts=({"source": "op", "item": "Deploy Secrets", "status": "missing"},),
                **snapshot_provenance(roadmap),
            )
            text = write_tui_handoff(repo, roadmap, snapshot, action="run").read_text(encoding="utf-8")

            self.assertIn("Access Attempts", text)
            self.assertIn(json.dumps("Deploy Secrets"), text)
            self.assertNotIn("secret-value", text)

    def test_complete_roadmap_handoff_names_completion_without_resume_command(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fixture = make_completed_roadmap_fixture(Path(td))
            repo = fixture.repo
            roadmap = fixture.roadmap
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            artifacts = run_artifacts(repo, "RUNNER", "repair", 1, ["codex", "exec"])
            artifacts["metadata"].write_text(json.dumps({"skill_bundle_id": "codex:repair:codex-phase-loop"}, indent=2), encoding="utf-8")
            write_run_heartbeat(
                artifacts["heartbeat"],
                {
                    "process_alive": False,
                    "quiet_level": "active",
                    "elapsed_seconds": 132,
                    "seconds_since_log_update": 0,
                    "recommended_action": "Inspect stale final log.",
                    "nudge_prompt": "Status check: stale complete-roadmap nudge.",
                },
            )
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="RUNNER",
                    action="run",
                    status="complete",
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="fixture",
                    metadata={"artifacts": {key: str(value) for key, value in artifacts.items()}},
                    **event_provenance(roadmap, "RUNNER"),
                ),
            )
            snapshot = StateSnapshot(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phases={"OBSERVE": "complete", "RUNNER": "complete"},
                current_phase=None,
                **snapshot_provenance(roadmap),
            )

            with isolated_codex_home(root) as codex_home:
                write_skill_handoff(codex_home, repo, "codex-execute-phase", "RUNNER", "blocked", plan)
                text = write_tui_handoff(repo, roadmap, snapshot, action="run").read_text(encoding="utf-8")

            self.assertIn("Current phase: none", text)
            self.assertIn("Current status: complete", text)
            self.assertIn("All phases in the roadmap are complete.", text)
            self.assertIn("No resume command is required", text)
            self.assertNotIn("phase-loop run --repo", text)
            self.assertIn("phase-loop monitor", text)
            self.assertNotIn("## Reentry Context", text)
            self.assertNotIn("## Injected Context", text)
            self.assertNotIn("## Observed Liveness", text)
            self.assertNotIn("Status check: stale complete-roadmap nudge.", text)

    def test_complete_roadmap_handoff_omits_superseded_blocked_terminal_summary(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = make_completed_roadmap_fixture(Path(td))
            repo = fixture.repo
            roadmap = fixture.roadmap
            snapshot = StateSnapshot(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phases={"OBSERVE": "complete", "RUNNER": "complete"},
                current_phase=None,
                terminal_summary={
                    "phase": "RUNNER",
                    "terminal_status": "blocked",
                    "verification_status": "blocked",
                    "next_action": "Repair the recorded blocker before rerunning the loop.",
                    "dirty_paths": [],
                    "phase_owned_dirty": False,
                },
                **snapshot_provenance(roadmap),
            )

            text = write_tui_handoff(repo, roadmap, snapshot, action="resume").read_text(encoding="utf-8")

            self.assertIn("Current status: complete", text)
            self.assertIn("All phases in the roadmap are complete.", text)
            self.assertNotIn("## Terminal Summary", text)
            self.assertNotIn("Repair the recorded blocker", text)
            self.assertIn("No resume command is required", text)

    def test_handoff_leads_with_terminal_summary_after_exit(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            snapshot = StateSnapshot(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phases={"CONTRACT": "complete", "ACCESS": "planned", "RUNNER": "unplanned"},
                current_phase="ACCESS",
                terminal_summary={
                    "phase": "CONTRACT",
                    "terminal_status": "complete",
                    "terminal_blocker": None,
                    "verification_status": "passed",
                    "next_action": "Plan ACCESS next.",
                    "dirty_paths": [],
                    "phase_owned_dirty": False,
                    "artifact_paths": {"terminal": str(repo / ".phase-loop" / "runs" / "x" / "terminal-summary.json")},
                },
                **snapshot_provenance(roadmap),
            )

            text = write_tui_handoff(repo, roadmap, snapshot, action="status").read_text(encoding="utf-8")

            self.assertIn("## Terminal Summary", text)
            self.assertIn("The latest observed child exit for `CONTRACT` ended with terminal status `complete`.", text)
            self.assertIn("next action: Plan ACCESS next.", text)

    def test_handoff_includes_nested_pipeline_closeout_summary(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            snapshot = StateSnapshot(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phases={"CONTRACT": "complete"},
                current_phase="CONTRACT",
                terminal_summary={
                    "phase": "CONTRACT",
                    "terminal_status": "complete",
                    "verification_status": "passed",
                    "next_action": "done",
                    "phase_loop_closeout": {
                        "schema": "phase_loop_closeout.v1",
                        "phase": "CONTRACT",
                        "terminal_status": "complete",
                        "automation": {"status": "complete"},
                        "artifacts": {"plan_path": "plans/x.md", "plan_sha256": "abc"},
                        "verification": {"status": "passed"},
                        "blocker": {"human_required": False},
                        "source_bundle": {
                            "phase_id": "pipeline.phase.contract",
                            "sha256": "1234567890123456",
                            "pipeline_mode": "pipeline_required",
                        },
                    },
                },
                **snapshot_provenance(roadmap),
            )

            text = write_tui_handoff(repo, roadmap, snapshot, action="status").read_text(encoding="utf-8")

            self.assertIn("Pipeline closeout: `complete` for `pipeline.phase.contract`", text)
            self.assertIn("source bundle: `123456789012`", text)

    def test_handoff_omits_stale_heartbeat_section_after_terminal_exit(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            artifacts = run_artifacts(repo, "CONTRACT", "execute", 1, ["codex", "exec"])
            write_run_heartbeat(
                artifacts["heartbeat"],
                {
                    "process_alive": False,
                    "quiet_level": "stale",
                    "heartbeat_status": "stale",
                    "event_kind": "terminal_exit",
                },
            )
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="CONTRACT",
                    action="execute",
                    status="complete",
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="fixture",
                    metadata={"artifacts": {key: str(value) for key, value in artifacts.items()}},
                    **event_provenance(roadmap, "CONTRACT"),
                ),
            )
            snapshot = StateSnapshot(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phases={"CONTRACT": "complete", "ACCESS": "planned"},
                current_phase="ACCESS",
                terminal_summary={
                    "phase": "CONTRACT",
                    "terminal_status": "complete",
                    "terminal_blocker": None,
                    "verification_status": "passed",
                    "next_action": "Plan ACCESS next.",
                    "dirty_paths": [],
                    "phase_owned_dirty": False,
                },
                **snapshot_provenance(roadmap),
            )

            text = write_tui_handoff(repo, roadmap, snapshot, action="status").read_text(encoding="utf-8")

            self.assertIn("## Terminal Summary", text)
            self.assertNotIn("## Observed Liveness", text)

    def test_handoff_omits_stale_terminal_summary_after_current_phase_advances(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            snapshot = StateSnapshot(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phases={"CONTRACT": "complete", "ACCESS": "unplanned"},
                current_phase="ACCESS",
                terminal_summary={
                    "phase": "CONTRACT",
                    "terminal_status": "executed",
                    "verification_status": "not_run",
                    "next_action": "Preserve the verified phase-owned output before rerunning the loop.",
                    "dirty_paths": ["README.md"],
                    "phase_owned_dirty": True,
                },
                closeout_summary={
                    "phase": "CONTRACT",
                    "closeout_mode": "push",
                    "closeout_action": "push",
                    "closeout_commit": "abc123",
                    "closeout_push_ref": "origin refs/heads/main",
                    "verification_status": "passed",
                },
                **snapshot_provenance(roadmap),
            )

            text = write_tui_handoff(repo, roadmap, snapshot, action="status").read_text(encoding="utf-8")

            self.assertNotIn("## Terminal Summary", text)
            self.assertNotIn("Preserve the verified phase-owned output", text)
            self.assertIn("Latest Closeout Decision", text)
            self.assertIn("`ACCESS` is the nearest downstream phase without a current plan artifact.", text)

    def test_blocked_handoff_shows_blocker_metadata_without_human_required(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            snapshot = StateSnapshot(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phases={"CONTRACT": "blocked"},
                current_phase="CONTRACT",
                human_required=False,
                blocker_class="dirty_worktree_conflict",
                blocker_summary="Plan repair required before execution.",
                terminal_summary={
                    "phase": "CONTRACT",
                    "terminal_status": "executed",
                    "verification_status": "blocked",
                    "next_action": "Inspect stale machine state.",
                    "dirty_paths": ["README.md"],
                    "phase_owned_dirty": True,
                    "phase_owned_dirty_paths": ["README.md"],
                    "unowned_dirty_paths": [],
                    "pre_existing_dirty_paths": [],
                },
                dirty_paths=("README.md",),
                phase_owned_dirty_paths=("README.md",),
                phase_owned_dirty=True,
                **snapshot_provenance(roadmap),
            )

            text = write_tui_handoff(repo, roadmap, snapshot, action="status").read_text(encoding="utf-8")

            self.assertIn("Current status: blocked", text)
            self.assertIn("a non-human repair is required", text)
            self.assertNotIn("needs human action", text)
            self.assertIn("Blocker class: `dirty_worktree_conflict`", text)
            self.assertIn("Plan repair required before execution.", text)
            self.assertIn("`.phase-loop/tui-handoff.md`", text)
            self.assertIn("`phase-loop handoff`", text)
            self.assertIn("`phase-loop status --json`", text)
            self.assertIn("terminal status: `executed`", text)
            self.assertIn("phase-owned paths: `README.md`", text)

    def test_missing_plan_blocker_handoff_is_plain_language_non_human_repair(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            snapshot = StateSnapshot(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phases={"CONTRACT": "blocked"},
                current_phase="CONTRACT",
                human_required=False,
                blocker_class="repeated_verification_failure",
                blocker_summary="Planning turn for CONTRACT exited successfully but did not create a current phase plan artifact.",
                terminal_summary={
                    "phase": "CONTRACT",
                    "terminal_status": "blocked",
                    "verification_status": "blocked",
                    "next_action": "Repair the recorded blocker before rerunning the loop.",
                    "dirty_paths": [],
                    "phase_owned_dirty": False,
                },
                **snapshot_provenance(roadmap),
            )

            text = write_tui_handoff(repo, roadmap, snapshot, action="run").read_text(encoding="utf-8")

            self.assertIn("did not create a current phase plan artifact", text)
            self.assertIn("repeated_verification_failure", text)
            self.assertIn("phase-loop monitor --repo", text)
            self.assertIn("phase-loop status --json", text)

    def test_closeout_handoff_distinguishes_verified_dirty_output_from_blocker(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            snapshot = StateSnapshot(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phases={"CONTRACT": "awaiting_phase_closeout"},
                current_phase="CONTRACT",
                human_required=False,
                dirty_paths=("README.md",),
                phase_owned_dirty_paths=("README.md",),
                phase_owned_dirty=True,
                closeout_terminal_status="complete",
                closeout_summary={
                    "closeout_mode": "manual",
                    "closeout_action": "awaiting_phase_closeout",
                    "verification_status": "passed",
                },
                **snapshot_provenance(roadmap),
            )

            text = write_tui_handoff(repo, roadmap, snapshot, action="status").read_text(encoding="utf-8")

            self.assertIn("Current status: awaiting_phase_closeout", text)
            self.assertIn("verified dirty phase output", text)
            self.assertIn("manual` remains the default", text)
            self.assertIn("phase-owned dirty: `true`", text)
            self.assertIn("phase-owned paths: `README.md`", text)
            self.assertIn("Latest Closeout Decision", text)
            self.assertIn("mode: `manual`", text)
            self.assertIn("git status --short --branch", text)
            self.assertIn("phase-loop status --json", text)
            self.assertNotIn("Blocker class:", text)
            self.assertNotIn("needs human action", text)

    def test_handoff_renders_closeout_commit_and_push_refusal_summary(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            snapshot = StateSnapshot(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phases={"CONTRACT": "complete"},
                current_phase="CONTRACT",
                closeout_summary={
                    "closeout_mode": "push",
                    "closeout_action": "push_refused",
                    "closeout_commit": "abc123",
                    "closeout_push_ref": "refs/heads/ga-hardening",
                    "closeout_refusal_reason": "behind_upstream",
                    "verification_status": "passed",
                },
                **snapshot_provenance(roadmap),
            )

            text = write_tui_handoff(repo, roadmap, snapshot, action="status").read_text(encoding="utf-8")

            self.assertIn("Latest Closeout Decision", text)
            self.assertIn("commit: `abc123`", text)
            self.assertIn("push target: `refs/heads/ga-hardening`", text)
            self.assertIn("refusal reason: `behind_upstream`", text)

    def test_handoff_points_at_amended_downstream_phase_without_stale_warning_noise(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = make_regenesis_amendment_fixture(Path(td))
            repo = fixture.repo
            roadmap = fixture.roadmap
            roadmap.write_text(
                "# Roadmap\n\n"
                "### Phase 0 - Affordance Verification (AFFVERIFY)\n\n"
                "### Phase 1 - Mobile Shell (MOBSHELL)\n\n"
                "### Phase 2 - Visual Fidelity (VISUAL)\n"
            )
            snapshot = StateSnapshot(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phases={"AFFVERIFY": "complete", "MOBSHELL": "unplanned", "VISUAL": "unplanned"},
                current_phase=fixture.next_phase,
                **snapshot_provenance(roadmap),
            )

            text = write_tui_handoff(repo, roadmap, snapshot, action="run").read_text(encoding="utf-8")

            self.assertIn(f"Current phase: {fixture.next_phase}", text)
            self.assertIn("Current status: unplanned", text)
            self.assertIn("The next run should plan that phase.", text)
            self.assertNotIn("roadmap_mismatch", text)
            self.assertNotIn("VISUAL` has a current plan artifact", text)


if __name__ == "__main__":
    unittest.main()
