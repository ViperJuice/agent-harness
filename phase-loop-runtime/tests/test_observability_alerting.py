import json
import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.observability import (
    append_work_unit_metric,
    build_notification_payload,
    build_terminal_summary,
    build_work_unit_metric,
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


if __name__ == "__main__":
    unittest.main()
