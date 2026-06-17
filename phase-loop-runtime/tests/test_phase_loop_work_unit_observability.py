import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.closeout import build_phase_loop_closeout
from phase_loop_runtime.events import append_work_unit_event, read_events
from phase_loop_runtime.handoff import render_tui_handoff
from phase_loop_runtime.models import StateSnapshot, WorkUnitCloseout, WorkUnitEventMetadata, WorkUnitIdentity, WorkUnitState, utc_now
from phase_loop_runtime.observability import append_work_unit_metric, build_terminal_summary, build_work_unit_metric
from phase_loop_runtime.state import write_state, write_work_unit_state
from phase_loop_runtime.state_ops import inspect_state
from phase_loop_test_utils import make_repo, provenanced_state


class PhaseLoopWorkUnitObservabilityTest(unittest.TestCase):
    def test_terminal_summary_and_handoff_render_previous_phase_owned_paths(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            summary = build_terminal_summary(
                terminal_status="executed",
                terminal_blocker=None,
                verification_status="not_run",
                next_action="Preserve previous phase output.",
                dirty_paths=("README.md",),
                phase_owned_dirty=True,
                previous_phase_owned_paths=("README.md",),
            )
            rendered = render_tui_handoff(
                repo,
                roadmap,
                StateSnapshot(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phases={"RUNNER": "awaiting_phase_closeout"},
                    current_phase="RUNNER",
                    dirty_paths=("README.md",),
                    previous_phase_owned_paths=("README.md",),
                    phase_owned_dirty=True,
                    terminal_summary=summary,
                ),
                action="status",
            )

            self.assertEqual(summary["previous_phase_owned_paths"], ["README.md"])
            self.assertIn("previous phase-owned paths: `README.md`", rendered)

    def test_terminal_summary_applies_child_baml_closeout_overlay(self):
        summary = build_terminal_summary(
            terminal_status="executing",
            terminal_blocker=None,
            verification_status="not_run",
            next_action="Await runner closeout.",
            child_baml_closeout={
                "terminal_status": "awaiting_phase_closeout",
                "verification_status": "passed",
                "produced_if_gates": ["IF-0-RECONCILESTATEAUDIT-1"],
                "dirty_paths": [],
                "blocker_class": "none",
                "blocker_summary": "none",
                "human_required": False,
                "required_human_inputs": [],
            },
        )

        self.assertEqual(summary["terminal_status"], "awaiting_phase_closeout")
        self.assertEqual(summary["verification_status"], "passed")
        self.assertEqual(summary["produced_if_gates"], ["IF-0-RECONCILESTATEAUDIT-1"])
        self.assertIsNone(summary["terminal_blocker"])

    def test_runner_block_is_authoritative_over_child_complete_claim(self):
        # #38: when the runner has rejected the child's closeout (here a
        # produced_if_gates contract_bug), the child's self-reported "complete" must
        # NOT be overlaid back onto the persisted terminal-summary. Otherwise the file
        # surfaces complete/passed to the next run and the executor reconcile-skips
        # instead of redoing the work.
        runner_blocker = {
            "human_required": False,
            "blocker_class": "contract_bug",
            "blocker_summary": "completed closeout produced_if_gates did not match the active phase plan",
            "required_human_inputs": [],
            "access_attempts": (),
        }
        summary = build_terminal_summary(
            terminal_status="blocked",
            terminal_blocker=runner_blocker,
            verification_status="blocked",
            next_action=runner_blocker["blocker_summary"],
            child_baml_closeout={
                "terminal_status": "complete",
                "verification_status": "passed",
                "produced_if_gates": ["IF-0-NATIVE-1", "IF-0-EXTRA-1"],
                "dirty_paths": [],
                "blocker_class": "none",
                "blocker_summary": "none",
                "human_required": False,
                "required_human_inputs": [],
            },
        )

        # runner verdict wins
        self.assertEqual(summary["terminal_status"], "blocked")
        self.assertEqual(summary["verification_status"], "blocked")
        self.assertEqual(summary["terminal_blocker"]["blocker_class"], "contract_bug")

    def test_child_overlay_still_applies_when_runner_not_blocked(self):
        # Guard is scoped to runner rejections — a non-blocked runner verdict still
        # mirrors the child's closeout (no regression to the BAML-mirror behavior).
        summary = build_terminal_summary(
            terminal_status="executing",
            terminal_blocker=None,
            verification_status="not_run",
            next_action="Await runner closeout.",
            child_baml_closeout={
                "terminal_status": "complete",
                "verification_status": "passed",
                "produced_if_gates": ["IF-0-NATIVE-1"],
                "dirty_paths": [],
                "blocker_class": "none",
                "blocker_summary": "none",
                "human_required": False,
                "required_human_inputs": [],
            },
        )
        self.assertEqual(summary["terminal_status"], "complete")
        self.assertEqual(summary["verification_status"], "passed")

    def test_terminal_summary_preserves_existing_shape_without_child_closeout(self):
        summary = build_terminal_summary(
            terminal_status="executed",
            terminal_blocker=None,
            verification_status="not_run",
            next_action="Await closeout.",
        )

        self.assertEqual(summary["terminal_status"], "executed")
        self.assertEqual(summary["verification_status"], "not_run")
        self.assertNotIn("produced_if_gates", summary)
        self.assertNotIn("extraction_failure", summary)

    def test_terminal_summary_sanitizes_extraction_failure_metadata(self):
        summary = build_terminal_summary(
            terminal_status="executed",
            terminal_blocker=None,
            verification_status="not_run",
            next_action="Await closeout.",
            extraction_failure={
                "reason": "missing_native_closeout",
                "source": "output",
                "classification": "native_closeout_extraction",
                "raw_output": "api_key=should-not-survive",
                "detail": "safe metadata",
            },
        )

        self.assertEqual(summary["extraction_failure"]["reason"], "missing_native_closeout")
        self.assertEqual(summary["extraction_failure"]["detail"], "safe metadata")
        self.assertNotIn("raw_output", summary["extraction_failure"])
        self.assertNotIn("api_key", str(summary["extraction_failure"]))

    def test_terminal_summary_metric_monitor_and_handoff_render_work_unit(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            identity = WorkUnitIdentity(phase="RUNNER", kind="lane_execute", lane_id="SL-0", attempt=1)
            state = WorkUnitState(identity=identity, status="running", retry_of="RUNNER.lane_execute.SL-0.0")
            write_state(repo, provenanced_state(repo, roadmap, {"RUNNER": "executing"}))
            write_work_unit_state(repo, state, roadmap=roadmap)
            summary = build_terminal_summary(
                terminal_status="complete",
                terminal_blocker=None,
                verification_status="passed",
                next_action="done",
                work_unit=state.to_json(),
            )
            metric = build_work_unit_metric(
                repo=repo,
                phase="RUNNER",
                action="execute",
                launch_metadata={"executor": "codex", "selected_model": "gpt-5.5"},
                terminal_summary=summary,
                lane_id="SL-0",
            )
            append_work_unit_metric(repo, metric)

            inspected = inspect_state(repo, roadmap)
            rendered = render_tui_handoff(
                repo,
                roadmap,
                StateSnapshot(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phases={"RUNNER": "executing"},
                    current_phase="RUNNER",
                    latest_metric=metric.to_json(),
                    latest_work_unit=state.to_json(),
                ),
                action="status",
            )

            self.assertEqual(metric.to_json()["work_unit_id"], "RUNNER.lane_execute.SL-0.1")
            self.assertEqual(metric.to_json()["lane_id"], "SL-0")
            self.assertEqual(summary["work_unit"]["status"], "running")
            self.assertEqual(inspected["monitor_status"]["work_unit"]["work_unit_id"], "RUNNER.lane_execute.SL-0.1")
            self.assertIn("## Latest Work Unit", rendered)
            self.assertIn("RUNNER.lane_execute.SL-0.1", rendered)

    def test_phase_verify_summary_keeps_command_outcomes_separate(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            identity = WorkUnitIdentity(phase="REDUCEVERIFY", kind="phase_verify", lane_id="SL-verify", attempt=1)
            state = WorkUnitState(identity=identity, status="blocked")
            summary = build_terminal_summary(
                terminal_status="blocked",
                terminal_blocker={
                    "human_required": False,
                    "blocker_class": "repeated_verification_failure",
                    "blocker_summary": "phase verification failed",
                },
                verification_status="failed",
                next_action="Repair failing phase verification.",
                latest_verification_unit=state.identity.to_json(),
                verification_commands=(
                    {"command": "python3 -m unittest test_phase_loop_reducers", "status": "passed", "returncode": 0},
                    {"command": "python3 -m unittest discover -s tests -p test_phase_loop*.py", "status": "failed", "returncode": 1},
                ),
            )
            metric = build_work_unit_metric(
                repo=repo,
                phase="REDUCEVERIFY",
                action="execute",
                launch_metadata={"execution_policy": {"work_unit_kind": "phase_verify", "effort": "high"}},
                terminal_summary=summary,
            )

            rendered = render_tui_handoff(
                repo,
                roadmap,
                StateSnapshot(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phases={"REDUCEVERIFY": "blocked"},
                    current_phase="REDUCEVERIFY",
                    terminal_summary=summary,
                    blocker_class="repeated_verification_failure",
                ),
                action="status",
            )

            self.assertEqual(metric.to_json()["work_unit_kind"], "phase_verify")
            self.assertEqual(summary["verification_status"], "failed")
            self.assertEqual(summary["latest_verification_unit"]["work_unit_id"], "REDUCEVERIFY.phase_verify.SL-verify.1")
            self.assertIn("latest verification unit", rendered)
            self.assertIn("failed verification commands", rendered)

    def test_work_unit_event_extracts_nested_phase_loop_closeout_summary(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = repo / "plans" / "phase-plan-v1-RUNNER.md"
            plan.parent.mkdir(parents=True, exist_ok=True)
            plan.write_text("# RUNNER\n", encoding="utf-8")
            identity = WorkUnitIdentity(phase="RUNNER", kind="lane_execute", lane_id="SL-0", attempt=1)
            closeout = build_phase_loop_closeout(
                phase_alias="RUNNER",
                plan_path=plan,
                terminal_summary={
                    "terminal_status": "complete",
                    "verification_status": "passed",
                    "evidence_refs": ({"path": "vendor/phase-loop-runtime/tests/fixtures/phase_loop_pipeline_bridge/dfbundlecloseout_complete.json", "sha256": "a" * 64},),
                },
                automation={"status": "complete", "verification_status": "passed"},
                changed_paths=("vendor/phase-loop-runtime/src/phase_loop_runtime/events.py",),
            )
            work_unit = WorkUnitCloseout(
                identity=identity,
                status="complete",
                closeout_summary={"phase_loop_closeout": closeout},
            )

            append_work_unit_event(
                repo,
                WorkUnitEventMetadata(identity=identity, status="complete", closeout_summary=work_unit.closeout_summary),
                roadmap=roadmap,
            )

            event = read_events(repo)[-1]
            metadata = event["metadata"]
            self.assertEqual(metadata["phase_loop_closeout"]["phase"], "RUNNER")
            self.assertEqual(metadata["phase_alias"], "RUNNER")
            self.assertEqual(metadata["pipeline_mode"], "standalone")
            self.assertEqual(metadata["verification_status"], "passed")
            self.assertEqual(metadata["changed_paths"], ["vendor/phase-loop-runtime/src/phase_loop_runtime/events.py"])
            self.assertEqual(metadata["evidence_refs"][0]["path"], "vendor/phase-loop-runtime/tests/fixtures/phase_loop_pipeline_bridge/dfbundlecloseout_complete.json")


if __name__ == "__main__":
    unittest.main()
