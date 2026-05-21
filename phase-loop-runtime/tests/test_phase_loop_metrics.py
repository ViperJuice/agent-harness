import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.launcher import LaunchResult
from phase_loop_runtime.observability import (
    append_work_unit_metric,
    build_terminal_summary,
    build_work_unit_metric,
    phase_loop_metrics_file,
    phase_loop_metrics_path,
    read_work_unit_metrics,
    summarize_work_unit_metrics,
)
from phase_loop_test_utils import make_repo
from phase_loop_test_utils import assert_metadata_only_evidence_refs


class PhaseLoopMetricsTest(unittest.TestCase):
    def test_dfparsoak_metric_records_lane_wave_blocker_and_redacted_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            evidence_refs = ("phase-loop-run:dfparsoak-wave-001", "metrics:sha256:" + "5" * 64)
            metric = build_work_unit_metric(
                repo=repo,
                phase="DFPARSOAK",
                action="execute",
                lane_id="SL-2",
                launch_metadata={
                    "executor": "codex",
                    "selected_model": "gpt-5.5",
                    "harness_lane_assignment": {"lane_id": "SL-2"},
                    "execution_policy": {
                        "work_unit_kind": "lane_execute",
                        "effort": "medium",
                        "execution_policy_source": "phase-plan policy",
                        "fallback_applied": False,
                    },
                    "wave_id": "wave-001",
                },
                launch_result=LaunchResult(command=["codex", "exec"], returncode=0),
                terminal_summary=build_terminal_summary(
                    terminal_status="blocked",
                    terminal_blocker={"blocker_class": "dirty_worktree_conflict"},
                    verification_status="blocked",
                    next_action="repair dirty worktree",
                    artifact_paths={"evidence": evidence_refs[0]},
                ),
                artifact_paths={"metrics": evidence_refs[1]},
            )

            data = metric.to_json()
            self.assertEqual(data["phase"], "DFPARSOAK")
            self.assertEqual(data["lane_id"], "SL-2")
            self.assertEqual(data["wave_id"], "wave-001")
            self.assertEqual(data["blocker_class"], "dirty_worktree_conflict")
            self.assertEqual(data["artifact_paths"]["metrics"], evidence_refs[1])
            assert_metadata_only_evidence_refs(self, evidence_refs)
            self.assertNotIn("provider_payload", json.dumps(data))

    def test_builds_redaction_safe_metric_schema(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            metric = build_work_unit_metric(
                repo=repo,
                phase="RUNNER",
                action="execute",
                launch_metadata={
                    "executor": "codex",
                    "selected_model": "gpt-5.4",
                    "wave_id": "wave-001",
                    "execution_policy": {
                        "work_unit_kind": "lane_execute",
                        "effort": "medium",
                    "execution_policy_source": "phase plan",
                    "model_source": "phase plan",
                    "fallback_applied": True,
                    "fallback": "codex",
                    "fallback_reason": "preferred executor unavailable",
                },
                    "command": ["codex", "exec", "<prompt redacted sha256=abc>"],
                    "context_path": str(repo / ".phase-loop" / "runs" / "context.md"),
                },
                launch_result=LaunchResult(
                    command=["codex", "exec"],
                    returncode=0,
                    started_at="2026-05-10T00:00:00Z",
                    finished_at="2026-05-10T00:00:03Z",
                ),
                terminal_summary=build_terminal_summary(
                    terminal_status="complete",
                    terminal_blocker=None,
                    verification_status="passed",
                    next_action="done",
                ),
                artifact_paths={"metadata": str(repo / ".phase-loop" / "runs" / "launch.json")},
            )

            data = metric.to_json()
            self.assertEqual(data["schema_version"], "work_unit_metric.v1")
            self.assertEqual(data["work_unit_kind"], "lane_execute")
            self.assertEqual(data["executor"], "codex")
            self.assertEqual(data["provider"], "openai")
            self.assertEqual(data["model"], "gpt-5.4")
            self.assertEqual(data["effort"], "medium")
            self.assertEqual(data["wave_id"], "wave-001")
            self.assertTrue(data["fallback_applied"])
            self.assertEqual(data["profile_source"], "phase plan")
            self.assertEqual(data["fallback_reason"], "preferred executor unavailable")
            self.assertEqual(data["duration_seconds"], 3.0)
            serialized = json.dumps(data)
            self.assertNotIn("prompt text", serialized)
            self.assertNotIn("secret", serialized.lower())

    def test_append_read_tolerates_malformed_lines_and_summarizes(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            metric = build_work_unit_metric(
                repo=repo,
                phase="RUNNER",
                action="plan",
                launch_metadata={"executor": "claude", "selected_model": "claude-opus-4-7"},
                terminal_summary=build_terminal_summary(
                    terminal_status="blocked",
                    terminal_blocker={"blocker_class": "repeated_verification_failure"},
                    verification_status="blocked",
                    next_action="retry",
                ),
            )
            self.assertEqual(phase_loop_metrics_file(repo), repo / ".phase-loop" / "metrics.jsonl")
            self.assertEqual(phase_loop_metrics_path(repo), phase_loop_metrics_file(repo))
            append_work_unit_metric(repo, metric)
            with phase_loop_metrics_path(repo).open("a", encoding="utf-8") as handle:
                handle.write("{not-json}\n")

            records = read_work_unit_metrics(repo)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["metric_id"], metric.metric_id)
            summary = summarize_work_unit_metrics(records)
            self.assertEqual(summary["by_executor"]["claude"], 1)
            self.assertEqual(summary["by_terminal_status"]["blocked"], 1)
            self.assertEqual(summary["by_blocker_class"]["repeated_verification_failure"], 1)

    def test_dfparsoak_metric_records_parallel_utilization_and_fallback_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            metric = build_work_unit_metric(
                repo=repo,
                phase="DFPARSOAK",
                action="execute",
                launch_metadata={
                    "executor": "gemini",
                    "selected_model": "auto",
                    "lane_id": "SL-2",
                    "wave_id": "wave-dfparsoak-001",
                    "execution_policy": {"work_unit_kind": "lane_execute", "effort": "medium"},
                    "execution_policy_source": "phase-plan",
                    "model_source": "gemini_default",
                    "fallback_applied": True,
                    "fallback_reason": "gemini_cli_fallback",
                    "parallelism": {"ready_lanes": 3, "launched_lanes": 3},
                },
                launch_result=LaunchResult(
                    command=["gemini", "--model", "auto"],
                    returncode=0,
                    started_at="2026-05-14T00:00:00Z",
                    finished_at="2026-05-14T00:00:02Z",
                ),
                terminal_summary=build_terminal_summary(
                    terminal_status="complete",
                    terminal_blocker=None,
                    verification_status="passed",
                    next_action="done",
                ),
                lane_id="SL-2",
            )

            data = metric.to_json()

            self.assertEqual(data["phase"], "DFPARSOAK")
            self.assertEqual(data["lane_id"], "SL-2")
            self.assertEqual(data["wave_id"], "wave-dfparsoak-001")
            self.assertEqual(data["executor"], "gemini")
            self.assertEqual(data["model"], "auto")
            self.assertEqual(data["fallback_reason"], "gemini_cli_fallback")
            self.assertNotIn("provider payload", json.dumps(data).lower())


if __name__ == "__main__":
    unittest.main()
