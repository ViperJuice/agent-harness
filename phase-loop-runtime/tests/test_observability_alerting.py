import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.observability import (
    append_work_unit_metric,
    build_notification_payload,
    build_terminal_summary,
    build_work_unit_metric,
    run_heartbeat_summary,
    summarize_work_unit_metrics,
)
from phase_loop_test_utils import make_repo


def _metric(status: str) -> dict[str, object]:
    return {"verification_status": status, "executor": "codex", "model": "gpt-5.5"}


class ObservabilityAlertingTest(unittest.TestCase):
    def test_not_run_alert_above_threshold(self):
        metrics = [_metric("passed") for _ in range(39)] + [_metric("not_run") for _ in range(11)]

        summary = summarize_work_unit_metrics(metrics)

        self.assertTrue(summary["not_run_alert"])
        self.assertEqual(summary["not_run_count"], 11)
        self.assertEqual(summary["sample_size"], 50)
        self.assertEqual(summary["threshold"], 0.2)
        self.assertEqual(summary["not_run_ratio"], 0.22)

    def test_not_run_alert_exactly_at_threshold_is_quiet(self):
        metrics = [_metric("passed") for _ in range(40)] + [_metric("not_run") for _ in range(10)]

        summary = summarize_work_unit_metrics(metrics)

        self.assertFalse(summary["not_run_alert"])
        self.assertEqual(summary["not_run_ratio"], 0.2)

    def test_not_run_alert_below_threshold_is_quiet(self):
        metrics = [_metric("passed") for _ in range(41)] + [_metric("not_run") for _ in range(9)]

        summary = summarize_work_unit_metrics(metrics)

        self.assertFalse(summary["not_run_alert"])
        self.assertEqual(summary["not_run_ratio"], 0.18)

    def test_not_run_alert_uses_smaller_recent_sample(self):
        metrics = [_metric("passed") for _ in range(4)] + [_metric("not_run")]

        summary = summarize_work_unit_metrics(metrics)

        self.assertFalse(summary["not_run_alert"])
        self.assertEqual(summary["sample_size"], 5)
        self.assertEqual(summary["not_run_count"], 1)
        self.assertEqual(summary["not_run_ratio"], 0.2)

    def test_notification_payload_exposes_not_run_ratio_without_raw_logs(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            metric = build_work_unit_metric(
                repo=repo,
                phase="RUNNER",
                action="execute",
                launch_metadata={"executor": "codex", "selected_model": "gpt-5.5"},
                terminal_summary=build_terminal_summary(
                    terminal_status="executed",
                    terminal_blocker=None,
                    verification_status="not_run",
                    next_action="collect verification",
                ),
            )
            append_work_unit_metric(repo, metric)
            metrics_summary = summarize_work_unit_metrics([metric.to_json()])

            payload = build_notification_payload(
                repo=repo,
                roadmap=roadmap,
                monitor_status={
                    "event_kind": "blocked",
                    "current_status": "blocked",
                    "metrics_summary": metrics_summary,
                    "recommended_action": "inspect state",
                },
                state_summary={"current_phase": "RUNNER", "metrics_summary": metrics_summary},
            )

            self.assertEqual(payload["not_run_ratio"], 1.0)
            self.assertEqual(payload["not_run_count"], 1)
            self.assertEqual(payload["sample_size"], 1)
            self.assertEqual(payload["threshold"], 0.2)
            serialized = json.dumps(payload)
            self.assertNotIn("output.log contents", serialized)
            self.assertNotIn("secret", serialized.lower())

    def test_heartbeat_summary_marks_stale_cpu_idle_as_suspect_stalled(self):
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "output.log"
            log_path.write_text("waiting\n", encoding="utf-8")
            old = time.time() - 10
            log_path.touch()
            import os

            os.utime(log_path, (old, old))

            with (
                patch("phase_loop_runtime.observability._pid_is_live", return_value=True),
                patch("phase_loop_runtime.observability._process_cpu_percent", return_value=0.0),
            ):
                summary = run_heartbeat_summary(
                    log_path=log_path,
                    pid=12345,
                    quiet_warning_seconds=0,
                    quiet_blocker_seconds=0,
                )

            self.assertEqual(summary["cpu_percent"], 0.0)
            self.assertEqual(summary["quiet_level"], "stale")
            self.assertEqual(summary["liveness_class"], "suspect_stalled")
            self.assertTrue(summary["stalled_suspect"])

    def test_heartbeat_summary_marks_stale_cpu_active_as_quiet_active(self):
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "output.log"
            log_path.write_text("working\n", encoding="utf-8")

            with (
                patch("phase_loop_runtime.observability._pid_is_live", return_value=True),
                patch("phase_loop_runtime.observability._process_cpu_percent", return_value=2.5),
            ):
                summary = run_heartbeat_summary(
                    log_path=log_path,
                    pid=12345,
                    quiet_warning_seconds=0,
                    quiet_blocker_seconds=0,
                )

            self.assertEqual(summary["cpu_percent"], 2.5)
            self.assertEqual(summary["quiet_level"], "stale")
            self.assertEqual(summary["liveness_class"], "cpu_active_quiet")
            self.assertFalse(summary["stalled_suspect"])

    def test_heartbeat_summary_uses_process_group_cpu_for_child_activity(self):
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "output.log"
            log_path.write_text("child working\n", encoding="utf-8")

            with (
                patch("phase_loop_runtime.observability._pid_is_live", return_value=True),
                patch("phase_loop_runtime.observability._process_cpu_percent", return_value=0.0),
                patch("phase_loop_runtime.observability._process_group_cpu_percent", return_value=3.0),
            ):
                summary = run_heartbeat_summary(
                    log_path=log_path,
                    pid=12345,
                    process_group_id=12345,
                    quiet_warning_seconds=0,
                    quiet_blocker_seconds=0,
                )

            self.assertEqual(summary["process_group_id"], 12345)
            self.assertEqual(summary["cpu_percent"], 3.0)
            self.assertEqual(summary["quiet_level"], "stale")
            self.assertEqual(summary["liveness_class"], "cpu_active_quiet")
            self.assertFalse(summary["stalled_suspect"])

    def test_heartbeat_summary_marks_stale_cpu_unknown_without_crashing(self):
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "output.log"
            log_path.write_text("unknown\n", encoding="utf-8")

            with (
                patch("phase_loop_runtime.observability._pid_is_live", return_value=True),
                patch("phase_loop_runtime.observability._process_cpu_percent", return_value=None),
            ):
                summary = run_heartbeat_summary(
                    log_path=log_path,
                    pid=12345,
                    quiet_warning_seconds=0,
                    quiet_blocker_seconds=0,
                )

            self.assertEqual(summary["quiet_level"], "stale")
            self.assertEqual(summary["liveness_class"], "quiet_unknown")
            self.assertFalse(summary["stalled_suspect"])


if __name__ == "__main__":
    unittest.main()
