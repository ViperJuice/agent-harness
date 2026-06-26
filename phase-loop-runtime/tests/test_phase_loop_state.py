import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.events import append_event, read_events
from phase_loop_runtime.launcher import build_launch_request, build_launch_spec
from phase_loop_runtime.models import DelegationBudget, DelegationRequest, LoopEvent, StateSnapshot, utc_now
from phase_loop_runtime.observability import (
    append_work_unit_metric,
    build_terminal_summary,
    build_work_unit_metric,
    run_artifacts,
    write_run_heartbeat,
)
from phase_loop_runtime.profiles import resolve_profile
from phase_loop_runtime.provenance import event_provenance, snapshot_provenance
from phase_loop_runtime.prompts import build_prompt
from phase_loop_runtime.runner import launch_delegated_child
from phase_loop_runtime.state import load_state, state_path, write_state
from phase_loop_runtime.state import write_work_unit_state
from phase_loop_runtime.models import WorkUnitIdentity, WorkUnitState
from phase_loop_runtime.git_topology import resolve_closeout_push_target
from phase_loop_runtime.state_ops import inspect_state
from phase_loop_smoke_utils import append_manual_import_event, isolated_codex_home, write_skill_handoff
from phase_loop_test_utils import (
    make_code_index_blocker_fixture,
    make_greenfield_closeout_fixture,
    make_repo,
    provenanced_event,
    provenanced_state,
    write_phase_plan,
)

import pytest

# TESTDECOUPLE SL-1 (overlay-dependent): builds a skill/adoption bundle or runs the
# runtime execute path, which resolves the dotfiles skill-source / profile overlay
# (claude-config/*, codex-config/* …) absent standalone. Run-time integration: the
# conftest hook skips it when no dotfiles tree is reachable.
pytestmark = pytest.mark.dotfiles_integration


class PhaseLoopStateTest(unittest.TestCase):
    def test_inspect_state_falls_back_to_latest_run_delegation_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "CONTRACT", roadmap, owned_files=("notes.md",))
            request = DelegationRequest(
                request_id="req-state",
                product_action="review",
                target_executor="codex",
                reason="Need delegated review.",
                owned_files=("notes.md",),
                expected_output="Review findings",
                budget=DelegationBudget(max_seconds=45),
            )
            launch_delegated_child(
                repo=repo,
                roadmap=roadmap,
                parent_phase="CONTRACT",
                parent_action="execute",
                plan=plan,
                request=request,
                dry_run=True,
            )

            summary = inspect_state(repo, roadmap)

            self.assertEqual(summary["latest_launch_metadata"]["delegation_request"]["request_id"], "req-state")
            self.assertEqual(summary["monitor_status"]["delegation"]["status"], "approved")
            self.assertEqual(summary["monitor_status"]["delegation_lineage"]["parent_phase"], "CONTRACT")

    def test_state_json_is_rewritten_and_events_append(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_state(repo, provenanced_state(repo, roadmap, {"RUNNER": "planned"}))
            first = state_path(repo).read_text()
            write_state(repo, provenanced_state(repo, roadmap, {"RUNNER": "complete"}))
            self.assertNotEqual(first, state_path(repo).read_text())
            self.assertEqual(load_state(repo).phases["RUNNER"], "complete")

            event = provenanced_event(repo, roadmap, "RUNNER", "planned")
            append_event(repo, event)
            append_event(repo, event)
            self.assertEqual(len(read_events(repo)), 2)

    def test_inspect_state_ignores_stale_active_loop_lock(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            lock = repo / ".phase-loop" / "active-loop.json"
            lock.parent.mkdir(parents=True)
            lock.write_text(json.dumps({"mode": "product", "pid": -1}), encoding="utf-8")

            summary = inspect_state(repo, roadmap)

            self.assertFalse(summary["active_loop_exists"])
            self.assertFalse(lock.exists())

    def test_neutral_runtime_writes_and_legacy_state_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            legacy = repo / ".codex" / "phase-loop"
            legacy.mkdir(parents=True)
            legacy_state = provenanced_state(repo, roadmap, {"LEGACY": "planned"})
            (legacy / "state.json").write_text(json.dumps(legacy_state.to_json(), indent=2), encoding="utf-8")
            legacy_event = provenanced_event(repo, roadmap, "LEGACY", "planned")
            (legacy / "events.jsonl").write_text(json.dumps(legacy_event.to_json()) + "\n", encoding="utf-8")

            self.assertEqual(load_state(repo).phases["LEGACY"], "planned")
            self.assertEqual(read_events(repo)[0]["phase"], "LEGACY")

            write_state(repo, provenanced_state(repo, roadmap, {"RUNNER": "complete"}))
            append_event(repo, provenanced_event(repo, roadmap, "RUNNER", "complete"))

            self.assertEqual(state_path(repo), repo / ".phase-loop" / "state.json")
            self.assertTrue((repo / ".phase-loop" / "state.json").exists())
            self.assertEqual(load_state(repo).phases["RUNNER"], "complete")
            self.assertEqual([event["phase"] for event in read_events(repo)], ["RUNNER"])

    def test_runtime_state_is_excluded_from_git_status(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_state(repo, provenanced_state(repo, roadmap, {"RUNNER": "planned"}))
            append_event(repo, provenanced_event(repo, roadmap, "RUNNER", "planned"))
            handoff = repo / ".phase-loop" / "tui-handoff.md"
            handoff.write_text("# handoff\n")

            status = subprocess.check_output(["git", "-C", str(repo), "status", "--short", "--", ".phase-loop", ".codex"], text=True)
            self.assertEqual(status.strip(), "")
            exclude = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "--git-path", "info/exclude"], text=True).strip()
            exclude_text = (repo / exclude).read_text()
            self.assertIn(".phase-loop/", exclude_text)
            self.assertIn(".codex/phase-loop/", exclude_text)

    def test_archive_state_stays_hidden_from_git_status(self):
        with tempfile.TemporaryDirectory() as td:
            from phase_loop_runtime.state_ops import archive_state

            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_state(repo, provenanced_state(repo, roadmap, {"RUNNER": "planned"}))
            handoff = repo / ".phase-loop" / "tui-handoff.md"
            handoff.write_text("# handoff\n")
            archive_state(repo, reason="fixture")
            status = subprocess.check_output(["git", "-C", str(repo), "status", "--short", "--", ".phase-loop", ".codex"], text=True)
            self.assertEqual(status.strip(), "")
            self.assertFalse(handoff.exists())

    def test_archive_state_dry_run_does_not_mutate(self):
        # #39: `archive-state --dry-run` must be read-only — describe the planned
        # move set without renaming any .phase-loop file.
        with tempfile.TemporaryDirectory() as td:
            from phase_loop_runtime.state_ops import archive_state

            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_state(repo, provenanced_state(repo, roadmap, {"RUNNER": "planned"}))
            events = repo / ".phase-loop" / "events.jsonl"
            events.write_text('{"phase":"RUNNER"}\n', encoding="utf-8")
            handoff = repo / ".phase-loop" / "tui-handoff.md"
            handoff.write_text("# handoff\n", encoding="utf-8")

            def snapshot():
                return sorted(
                    (p.relative_to(repo).as_posix(), p.read_bytes())
                    for p in (repo / ".phase-loop").rglob("*") if p.is_file()
                )

            before = snapshot()
            result = archive_state(repo, reason="dry", dry_run=True)

            # No filesystem mutation.
            self.assertEqual(snapshot(), before, "dry-run must not move/rename/create files")
            self.assertTrue((repo / ".phase-loop" / "state.json").exists())
            self.assertTrue(events.exists() and handoff.exists())
            self.assertFalse((repo / ".phase-loop" / "archive").exists())
            # Result reports it did NOT archive, flags dry-run, and lists the planned moves.
            self.assertFalse(result.get("archived"))
            self.assertTrue(result.get("dry_run"))
            planned = result.get("moved") or []
            planned_sources = {Path(m["source"]).name for m in planned}
            self.assertIn("state.json", planned_sources)
            self.assertIn("events.jsonl", planned_sources)
            self.assertIn("tui-handoff.md", planned_sources)

            # A real archive (no dry-run) still moves them.
            real = archive_state(repo, reason="real")
            self.assertTrue(real.get("archived"))
            self.assertFalse((repo / ".phase-loop" / "state.json").exists())

    def test_state_and_events_capture_git_topology(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            (repo / "README.md").write_text("fixture\nsecond line\n")
            subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "second"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

            with patch.dict(
                "os.environ",
                {
                    "PHASE_LOOP_BASE_REF": "HEAD~1",
                    "PHASE_LOOP_TARGET_PUSH_REF": "refs/heads/ga-hardening",
                    "PHASE_LOOP_PR_URL": "https://github.com/example/repo/pull/123",
                },
            ):
                write_state(repo, provenanced_state(repo, roadmap, {"RUNNER": "planned"}))
                append_event(repo, provenanced_event(repo, roadmap, "RUNNER", "planned"))

            branch = subprocess.check_output(["git", "-C", str(repo), "branch", "--show-current"], text=True).strip()
            snapshot = load_state(repo)
            self.assertEqual(snapshot.git_topology["branch"], branch)
            self.assertEqual(snapshot.git_topology["base_ref"], "HEAD~1")
            self.assertEqual(snapshot.git_topology["ahead_of_base"], 1)
            self.assertEqual(snapshot.git_topology["target_push_ref"], "refs/heads/ga-hardening")
            self.assertEqual(read_events(repo)[-1]["git_topology"]["pr_url"], "https://github.com/example/repo/pull/123")

    def test_inspect_state_reports_live_git_topology_over_stored_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_state(repo, provenanced_state(repo, roadmap, {"RUNNER": "planned"}))
            (repo / "dirty.txt").write_text("live dirty state\n", encoding="utf-8")

            summary = inspect_state(repo, roadmap)

            self.assertFalse(summary["git_topology"]["clean"])
            self.assertIn("dirty.txt", summary["git_topology"]["status_short_branch"])

    def test_inspect_state_reports_latest_heartbeat(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            artifacts = run_artifacts(repo, "RUNNER", "execute", 1, ["codex", "exec"])
            write_run_heartbeat(
                artifacts["heartbeat"],
                {
                    "process_alive": True,
                    "quiet_level": "quiet",
                    "elapsed_seconds": 30,
                    "seconds_since_log_update": 20,
                },
            )
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="RUNNER",
                    action="status",
                    status="planned",
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="fixture",
                    metadata={"artifacts": {key: str(value) for key, value in artifacts.items()}},
                    roadmap_sha256=provenanced_event(repo, roadmap, "RUNNER", "planned").roadmap_sha256,
                    phase_sha256=provenanced_event(repo, roadmap, "RUNNER", "planned").phase_sha256,
                ),
            )

            summary = inspect_state(repo, roadmap)

            self.assertEqual(summary["latest_heartbeat"]["quiet_level"], "quiet")
            self.assertTrue(summary["latest_heartbeat"]["process_alive"])
            self.assertEqual(summary["monitor_status"]["event_kind"], "heartbeat")
            self.assertEqual(summary["monitor_status"]["heartbeat_status"], "quiet")

    def test_inspect_state_reports_canonical_metrics(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            canonical = build_work_unit_metric(
                repo=repo,
                phase="RUNNER",
                action="execute",
                launch_metadata={"executor": "codex", "selected_model": "gpt-5.4"},
                terminal_summary=build_terminal_summary(
                    terminal_status="complete",
                    terminal_blocker=None,
                    verification_status="passed",
                    next_action="done",
                ),
            )
            legacy = repo / ".codex" / "phase-loop"
            legacy.mkdir(parents=True)
            (legacy / "metrics.jsonl").write_text(json.dumps({"metric_id": "legacy-metric"}) + "\n")
            append_work_unit_metric(repo, canonical)

            summary = inspect_state(repo, roadmap)

            self.assertEqual(summary["latest_metric"]["metric_id"], canonical.metric_id)
            self.assertEqual(summary["monitor_status"]["latest_metric"]["metric_id"], canonical.metric_id)
            self.assertEqual(summary["metrics_summary"]["by_executor"]["codex"], 1)

    def test_inspect_state_reports_phase_and_work_unit_status_separately(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            subprocess.run(["git", "add", str(plan.relative_to(repo))], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "add runner plan"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            write_state(repo, provenanced_state(repo, roadmap, {"RUNNER": "executing"}))
            write_work_unit_state(
                repo,
                WorkUnitState(
                    identity=WorkUnitIdentity(phase="RUNNER", kind="lane_execute", lane_id="SL-0", attempt=1),
                    status="running",
                ),
                roadmap=roadmap,
            )

            summary = inspect_state(repo, roadmap)

            self.assertEqual(summary["phase_status"], "executing")
            self.assertEqual(summary["work_unit_status"], "running")
            self.assertEqual(summary["monitor_status"]["phase_status"], "executing")
            self.assertEqual(summary["monitor_status"]["work_unit_status"], "running")

    def test_inspect_state_prefers_active_launch_metadata_over_stale_event_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(repo, provenanced_event(repo, roadmap, "CONTRACT", "complete", action="execute"))
            append_event(repo, provenanced_event(repo, roadmap, "ACCESS", "complete", action="execute"))
            write_phase_plan(repo, "RUNNER", roadmap)
            old_artifacts = run_artifacts(repo, "RUNNER", "repair", 1, ["codex", "repair"])
            active_artifacts = run_artifacts(repo, "RUNNER", "execute", 2, ["codex", "execute"])
            write_run_heartbeat(
                active_artifacts["heartbeat"],
                {
                    "process_alive": True,
                    "quiet_level": "active",
                    "heartbeat_status": "active",
                    "event_kind": "heartbeat",
                    "heartbeat_path": str(active_artifacts["heartbeat"]),
                },
            )
            active_lock = repo / ".phase-loop" / "active-loop.json"
            active_lock.write_text(json.dumps({"mode": "product", "pid": os.getpid()}), encoding="utf-8")
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="RUNNER",
                    action="repair",
                    status="planned",
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="fixture",
                    metadata={"artifacts": {key: str(value) for key, value in old_artifacts.items()}},
                    **event_provenance(roadmap, "RUNNER"),
                ),
            )
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="RUNNER",
                    action="manual_repair",
                    status="planned",
                    model="manual",
                    reasoning_effort="none",
                    source="fixture",
                    metadata={
                        "terminal_summary": {
                            "terminal_status": "planned",
                            "verification_status": "not_run",
                            "next_action": "Run the active phase.",
                            "dirty_paths": [],
                            "phase_owned_dirty": False,
                        }
                    },
                    **event_provenance(roadmap, "RUNNER"),
                ),
            )
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="RUNNER",
                    action="repair",
                    status="blocked",
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="fixture",
                    metadata={
                        "terminal_summary": {
                            "terminal_status": "blocked",
                            "verification_status": "blocked",
                            "next_action": "Stale blocked state from a previous repair.",
                            "dirty_paths": [],
                            "phase_owned_dirty": False,
                        }
                    },
                    **event_provenance(roadmap, "RUNNER"),
                ),
            )

            summary = inspect_state(repo, roadmap)

            self.assertEqual(summary["latest_launch_metadata"]["action"], "execute")
            self.assertEqual(summary["latest_launch_metadata"]["run_root"], str(active_artifacts["root"]))
            self.assertEqual(summary["latest_heartbeat"]["heartbeat_path"], str(active_artifacts["heartbeat"]))
            self.assertEqual(summary["monitor_status"]["event_kind"], "heartbeat")
            self.assertEqual(summary["monitor_status"]["current_status"], "executing")
            self.assertIsNone(summary["monitor_status"]["blocker_class"])
            self.assertIsNone(summary["monitor_status"]["terminal_status"])

    def test_context_file_artifact_and_redacted_metadata_are_persisted_for_context_delivery(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            request = build_launch_request(
                executor="gemini",
                action="execute",
                repo=repo,
                roadmap=roadmap,
                phase="RUNNER",
                plan=repo / "plans" / "phase-plan-v1-RUNNER.md",
                model_selection=resolve_profile("execute"),
                prompt_bundle=build_prompt(
                    "execute",
                    roadmap,
                    phase="RUNNER",
                    plan=repo / "plans" / "phase-plan-v1-RUNNER.md",
                    harness_target="gemini",
                ),
                json_output=False,
                bypass_approvals=False,
            )
            spec = build_launch_spec(request)
            artifacts = run_artifacts(repo, "RUNNER", "execute", 1, spec)

            self.assertIn("context", artifacts)
            self.assertTrue(artifacts["context"].exists())
            metadata = json.loads(artifacts["metadata"].read_text(encoding="utf-8"))
            self.assertEqual(metadata["injection_mode"], "context_file")
            self.assertEqual(metadata["context_path"], str(artifacts["context"]))
            self.assertEqual(metadata["context_sha256"], spec.prompt_bundle.context_sha256())
            self.assertEqual(metadata["context_line_count"], spec.prompt_bundle.context_line_count())
            self.assertEqual(metadata["context_char_count"], spec.prompt_bundle.context_char_count())
            self.assertNotIn(spec.prompt_bundle.render_context(), json.dumps(metadata))

    def test_inspect_state_surfaces_claude_team_policy_separately_from_delegation(self):
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

            summary = inspect_state(repo, roadmap)

            self.assertEqual(summary["latest_launch_metadata"]["claude_execution_mode"], "agent_team")
            self.assertEqual(summary["monitor_status"]["claude_team_policy"]["maturity_label"], "experimental")
            self.assertEqual(summary["monitor_status"]["phase_team_eligibility"]["reason"], "disjoint_write_lanes")
            self.assertEqual(summary["monitor_status"]["task_snapshot_freshness"], "fresh")
            self.assertEqual(summary["monitor_status"]["wait_classification"], "claude_agent_team_active")
            self.assertEqual(summary["monitor_status"]["latest_team_activity"]["classification"], "claude_agent_team_active")
            self.assertNotIn("delegation", summary["monitor_status"])

    def test_inspect_state_reports_terminal_summary(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(repo, provenanced_event(repo, roadmap, "CONTRACT", "complete", action="execute"))
            append_event(repo, provenanced_event(repo, roadmap, "ACCESS", "complete", action="execute"))
            write_phase_plan(repo, "RUNNER", roadmap)
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="RUNNER",
                    action="execute",
                    status="planned",
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="fixture",
                    metadata={
                        "terminal_summary": {
                            "terminal_status": "executed",
                            "terminal_blocker": None,
                            "verification_status": "not_run",
                            "next_action": "Close out RUNNER before moving on.",
                            "dirty_paths": [],
                            "phase_owned_dirty": False,
                            "artifact_paths": {"terminal": "runs/x/terminal-summary.json"},
                        }
                    },
                    **event_provenance(roadmap, "RUNNER"),
                ),
            )

            summary = inspect_state(repo, roadmap)

            self.assertEqual(summary["terminal_summary"]["terminal_status"], "executed")
            self.assertEqual(summary["latest_terminal_summary"]["verification_status"], "not_run")
            self.assertEqual(summary["monitor_status"]["event_kind"], "terminal_exit")

    def test_inspect_state_prefers_terminal_exit_over_stale_heartbeat_after_child_exit(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(repo, provenanced_event(repo, roadmap, "CONTRACT", "complete", action="execute"))
            append_event(repo, provenanced_event(repo, roadmap, "ACCESS", "complete", action="execute"))
            write_phase_plan(repo, "RUNNER", roadmap)
            artifacts = run_artifacts(repo, "RUNNER", "execute", 1, ["codex", "exec"])
            write_run_heartbeat(
                artifacts["heartbeat"],
                {
                    "process_alive": False,
                    "quiet_level": "stale",
                    "heartbeat_status": "stale",
                    "event_kind": "stale_heartbeat",
                    "recommended_action": "stale heartbeat text",
                },
            )
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="RUNNER",
                    action="execute",
                    status="planned",
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="fixture",
                    metadata={
                        "artifacts": {key: str(value) for key, value in artifacts.items()},
                        "terminal_summary": {
                            "terminal_status": "executed",
                            "terminal_blocker": None,
                            "verification_status": "not_run",
                            "next_action": "Close out RUNNER before moving on.",
                            "dirty_paths": [],
                            "phase_owned_dirty": False,
                            "artifact_paths": {},
                        }
                    },
                    **event_provenance(roadmap, "RUNNER"),
                ),
            )
            write_state(repo, provenanced_state(repo, roadmap, {"RUNNER": "planned"}))

            summary = inspect_state(repo, roadmap)

            self.assertEqual(summary["monitor_status"]["event_kind"], "terminal_exit")
            self.assertEqual(summary["monitor_status"]["recommended_action"], "Close out RUNNER before moving on.")

    def test_terminal_exit_supersedes_stale_task_snapshot(self):
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
                    "- **Owned files**: `src/one.py`\n"
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
                claude_execution_mode="subagent",
            )
            spec = build_launch_spec(request)
            artifacts = run_artifacts(repo, "RUNNER", "execute", 1, spec)
            write_run_heartbeat(
                artifacts["heartbeat"],
                {
                    "process_alive": False,
                    "quiet_level": "stale",
                    "heartbeat_status": "stale",
                },
            )
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="RUNNER",
                    action="execute",
                    status="blocked",
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="fixture",
                    metadata={
                        "artifacts": {key: str(value) for key, value in artifacts.items()},
                        "terminal_summary": {
                            "terminal_status": "blocked",
                            "terminal_blocker": None,
                            "verification_status": "blocked",
                            "next_action": "Inspect the blocked child turn.",
                            "dirty_paths": [],
                            "phase_owned_dirty": False,
                            "artifact_paths": {},
                        },
                    },
                    **event_provenance(roadmap, "RUNNER"),
                ),
            )

            summary = inspect_state(repo, roadmap)

            self.assertEqual(summary["monitor_status"]["task_snapshot_freshness"], "superseded")
            self.assertEqual(summary["monitor_status"]["wait_classification"], "superseded")
            self.assertEqual(summary["monitor_status"]["latest_team_activity"]["classification"], "superseded_by_terminal")

    def test_inspect_state_fails_closed_on_missing_latest_terminal_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="RUNNER",
                    action="execute",
                    status="complete",
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="fixture",
                    metadata={"artifacts": {"terminal": str(repo / ".phase-loop" / "runs" / "x" / "terminal-summary.json")}},
                    **event_provenance(roadmap, "RUNNER"),
                ),
            )

            summary = inspect_state(repo, roadmap)

            self.assertIsNone(summary["latest_terminal_summary"])

    def test_inspect_state_reports_reconciled_human_blocker(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="RUNNER",
                    action="execute",
                    status="blocked",
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="fixture",
                    blocker={
                        "human_required": True,
                        "blocker_class": "dirty_worktree_conflict",
                        "blocker_summary": "Clean worktree required.",
                        "required_human_inputs": ("commit or clear changes",),
                    },
                    **event_provenance(roadmap, "RUNNER"),
                ),
            )

            summary = inspect_state(repo, roadmap)

            self.assertTrue(summary["human_required"])
            self.assertEqual(summary["blocker_class"], "dirty_worktree_conflict")
            self.assertEqual(summary["required_human_inputs"], ("commit or clear changes",))
            self.assertEqual(summary["monitor_status"]["event_kind"], "blocked")

    def test_inspect_state_prefers_reconciled_terminal_over_stale_state_terminal(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            stale = StateSnapshot(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phases={"RUNNER": "planned"},
                current_phase="RUNNER",
                terminal_summary={
                    "phase": "RUNNER",
                    "terminal_status": "executed",
                    "verification_status": "blocked",
                    "next_action": "Stale dirty closeout.",
                },
                **snapshot_provenance(roadmap),
            )
            write_state(repo, stale)
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="RUNNER",
                    action="execute",
                    status="blocked",
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="fixture",
                    blocker={
                        "human_required": True,
                        "blocker_class": "contract_bug",
                        "blocker_summary": "Contract needs amendment.",
                        "required_human_inputs": ("amend roadmap",),
                    },
                    metadata={
                        "terminal_summary": {
                            "terminal_status": "blocked",
                            "verification_status": "blocked",
                            "next_action": "Amend the contract roadmap.",
                            "terminal_blocker": {
                                "human_required": True,
                                "blocker_class": "contract_bug",
                                "blocker_summary": "Contract needs amendment.",
                                "required_human_inputs": ("amend roadmap",),
                            },
                        }
                    },
                    **event_provenance(roadmap, "RUNNER"),
                ),
            )

            summary = inspect_state(repo, roadmap)

            self.assertEqual(summary["latest_terminal_summary"]["terminal_status"], "blocked")
            self.assertEqual(summary["latest_terminal_summary"]["next_action"], "Amend the contract roadmap.")
            self.assertEqual(summary["monitor_status"]["event_kind"], "terminal_exit")
            self.assertEqual(summary["monitor_status"]["recommended_action"], "Amend the contract roadmap.")

    def test_monitor_status_is_json_stable(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(repo, provenanced_event(repo, roadmap, "RUNNER", "blocked", action="execute"))

            summary = inspect_state(repo, roadmap)
            encoded = json.dumps(summary["monitor_status"], sort_keys=True)

            self.assertIn('"event_kind": "blocked"', encoded)
            self.assertEqual(summary["monitor_status"]["current_phase"], "RUNNER")
            self.assertEqual(summary["monitor_status"]["current_status"], "blocked")
            self.assertIn("tui_handoff_path", summary["monitor_status"])

    def test_inspect_state_reports_trusted_dirty_classification(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = make_greenfield_closeout_fixture(Path(td))
            repo = fixture.repo
            roadmap = fixture.roadmap
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase=fixture.execute_phase,
                    action="execute",
                    status="awaiting_phase_closeout",
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="fixture",
                    metadata={
                        "completion_dirty_worktree": {
                            "reason": "complete_status_with_dirty_worktree",
                            "terminal_status": "complete",
                            "dirty_paths": ["artifacts/enforce-report.json"],
                            "phase_owned_dirty_paths": ["artifacts/enforce-report.json"],
                            "unowned_dirty_paths": [],
                            "pre_existing_dirty_paths": [],
                            "phase_owned_dirty": True,
                        }
                    },
                    **event_provenance(roadmap, fixture.execute_phase),
                ),
            )

            summary = inspect_state(repo, roadmap)

            self.assertEqual(summary["dirty_paths"], ("artifacts/enforce-report.json",))
            self.assertEqual(summary["phase_owned_dirty_paths"], ("artifacts/enforce-report.json",))
            self.assertEqual(summary["unowned_dirty_paths"], ())
            self.assertTrue(summary["phase_owned_dirty"])

    def test_inspect_state_reports_code_index_product_decision_blocker(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = make_code_index_blocker_fixture(Path(td))
            repo = fixture.repo
            roadmap = fixture.roadmap
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase=fixture.execute_phase,
                    action="plan",
                    status="blocked",
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="fixture",
                    blocker={
                        "human_required": True,
                        "blocker_class": "product_decision_missing",
                        "blocker_summary": "code_index still needs a product decision.",
                        "required_human_inputs": ("choose the query behavior",),
                    },
                    **event_provenance(roadmap, fixture.execute_phase),
                ),
            )

            summary = inspect_state(repo, roadmap)

            self.assertTrue(summary["human_required"])
            self.assertEqual(summary["blocker_class"], "product_decision_missing")
            self.assertEqual(summary["required_human_inputs"], ("choose the query behavior",))

    def test_inspect_state_surfaces_cross_harness_reentry_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = make_repo(root)
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            with isolated_codex_home(root) as codex_home:
                write_skill_handoff(codex_home, repo, "opencode-execute-phase", "RUNNER", "complete", plan)
                append_manual_import_event(
                    repo,
                    roadmap,
                    "RUNNER",
                    "complete",
                    harness="thawedcode",
                    skill="claude-execute-phase",
                    artifact=plan,
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
                summary = inspect_state(repo, roadmap)

            trusted = summary["monitor_status"]["trusted_workflow_handoff"]
            manual = summary["monitor_status"]["latest_manual_import"]
            self.assertEqual(trusted["workflow_skill"], "opencode-execute-phase")
            self.assertEqual(trusted["originating_harness"], "opencode")
            self.assertEqual(manual["originating_harness"], "thawedcode")
            self.assertEqual(manual["workflow_skill"], "claude-execute-phase")
            self.assertEqual(manual["bridge_skill_inventory"][0]["parity_status"], "missing_root")

    def test_complete_manual_repair_suppresses_stale_blocked_terminal_and_handoff(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = make_repo(root)
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            with isolated_codex_home(root) as codex_home:
                write_state(repo, provenanced_state(repo, roadmap, {"CONTRACT": "complete", "ACCESS": "complete", "RUNNER": "complete"}))
                write_skill_handoff(codex_home, repo, "codex-execute-phase", "RUNNER", "blocked", plan)
                append_event(
                    repo,
                    LoopEvent(
                        timestamp=utc_now(),
                        repo=str(repo),
                        roadmap=str(roadmap),
                        phase="RUNNER",
                        action="run",
                        status="blocked",
                        model="gpt-5.4",
                        reasoning_effort="medium",
                        source="fixture",
                        metadata={
                            "terminal_summary": {
                                "terminal_status": "blocked",
                                "verification_status": "blocked",
                                "next_action": "stale blocker",
                            }
                        },
                        **event_provenance(roadmap, "RUNNER"),
                    ),
                )
                append_event(
                    repo,
                    LoopEvent(
                        timestamp=utc_now(),
                        repo=str(repo),
                        roadmap=str(roadmap),
                        phase="RUNNER",
                        action="manual_repair",
                        status="complete",
                        model="gpt-5.4",
                        reasoning_effort="medium",
                        source="manual",
                        metadata={
                            "manual_repair": {
                                "clears_blocker": True,
                                "closeout_commit": "repair123",
                            }
                        },
                        **event_provenance(roadmap, "RUNNER"),
                    ),
                )
                summary = inspect_state(repo, roadmap)

            self.assertEqual(summary["monitor_status"]["current_status"], "complete")
            self.assertEqual(summary["monitor_status"]["event_kind"], "complete")
            self.assertNotIn("trusted_workflow_handoff", summary["monitor_status"])
            self.assertIsNone(summary["latest_terminal_summary"])
            self.assertIsNone(summary["terminal_summary"])
            self.assertEqual(summary["closeout_summary"]["phase"], "RUNNER")
            self.assertEqual(summary["closeout_summary"]["closeout_action"], "commit")
            self.assertEqual(summary["closeout_summary"]["closeout_commit"], "repair123")
            self.assertEqual(summary["closeout_summary"]["verification_status"], "passed")

    def test_planned_manual_repair_suppresses_stale_blocked_terminal(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="RUNNER",
                    action="run",
                    status="blocked",
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="fixture",
                    metadata={
                        "terminal_summary": {
                            "terminal_status": "blocked",
                            "verification_status": "blocked",
                            "next_action": "stale blocker",
                        }
                    },
                    **event_provenance(roadmap, "RUNNER"),
                ),
            )
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="RUNNER",
                    action="manual_repair",
                    status="planned",
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="manual",
                    metadata={"manual_repair": {"clears_blocker": True}},
                    **event_provenance(roadmap, "RUNNER"),
                ),
            )

            summary = inspect_state(repo, roadmap)

            self.assertEqual(summary["monitor_status"]["current_status"], "planned")
            self.assertIsNone(summary["latest_terminal_summary"])
            self.assertIsNone(summary["terminal_summary"])

    def test_inspect_state_reports_closeout_summary(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
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
                    metadata={
                        "closeout": {
                            "closeout_mode": "push",
                            "closeout_action": "push_refused",
                            "closeout_commit": "abc123",
                            "closeout_push_ref": "refs/heads/ga-hardening",
                            "closeout_refusal_reason": "behind_upstream",
                            "verification_status": "passed",
                        }
                    },
                    **event_provenance(roadmap, "RUNNER"),
                ),
            )

            summary = inspect_state(repo, roadmap)

            self.assertEqual(summary["closeout_summary"]["closeout_mode"], "push")
            self.assertEqual(summary["closeout_summary"]["closeout_refusal_reason"], "behind_upstream")

    def test_closeout_push_target_prefers_explicit_target_ref_and_fails_when_behind_upstream(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))

            decision = resolve_closeout_push_target(
                repo,
                {
                    "available": True,
                    "clean": True,
                    "upstream_ref": "origin/main",
                    "target_push_ref": "refs/heads/ga-hardening",
                    "behind_upstream": 1,
                },
            )

            self.assertFalse(decision["allowed"])
            self.assertEqual(decision["push_ref"], "refs/heads/ga-hardening")
            self.assertEqual(decision["refusal_reason"], "behind_upstream")


if __name__ == "__main__":
    unittest.main()
