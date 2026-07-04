from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.events import append_event, event_path
from phase_loop_runtime.fleet_metrics import (
    append_fleet_metric,
    derive_fleet_metric_series,
    read_fleet_metrics,
    record_phase_fleet_metrics,
)
from phase_loop_runtime.models import FleetMetricEvent, LoopEvent, utc_now
from phase_loop_runtime.runtime_paths import (
    phase_loop_event_file,
    phase_loop_fleet_metrics_file,
)


def _loop_event(phase: str = "P1", status: str = "complete") -> LoopEvent:
    return LoopEvent(
        timestamp=utc_now(),
        repo="/tmp/repo",
        roadmap="roadmap.md",
        phase=phase,
        action="execute",
        status=status,
        model="claude-opus-4-8",
        reasoning_effort="high",
        source="test",
    )


class FleetMetricsAdditiveTest(unittest.TestCase):
    def test_fleet_metric_events_do_not_touch_events_jsonl_bytes(self) -> None:
        """Additive-proof: emitting fleet metrics leaves events.jsonl byte-identical."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            append_event(repo, _loop_event())
            events_file = phase_loop_event_file(repo)
            before = events_file.read_bytes()

            record_phase_fleet_metrics(
                repo,
                phase="P1",
                completed=True,
                total_scope=3,
                completed_count=1,
                missing_gates=("IF-A-1",),
                produced_gates=(),
            )

            # events.jsonl is untouched; the new ledger is a SEPARATE file.
            self.assertEqual(events_file.read_bytes(), before)
            self.assertTrue(phase_loop_fleet_metrics_file(repo).exists())
            self.assertNotEqual(
                phase_loop_fleet_metrics_file(repo), phase_loop_event_file(repo)
            )

    def test_fleet_metric_ledger_is_append_only_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            append_fleet_metric(repo, FleetMetricEvent(metric_kind="velocity", payload={"completed_total": 1}))
            append_fleet_metric(repo, FleetMetricEvent(metric_kind="velocity", payload={"completed_total": 2}))
            records = read_fleet_metrics(repo)
            self.assertEqual([r["payload"]["completed_total"] for r in records], [1, 2])
            self.assertTrue(all(r["event_kind"] == "fleet_metric" for r in records))


class FleetMetricsEmissionTest(unittest.TestCase):
    def test_completion_emits_velocity_and_burn_down(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            emitted = record_phase_fleet_metrics(
                repo,
                phase="P1",
                completed=True,
                total_scope=4,
                completed_count=2,
                timestamp="2026-07-04T00:00:00Z",
            )
            kinds = [e.metric_kind for e in emitted]
            self.assertIn("velocity", kinds)
            self.assertIn("burn_down", kinds)
            burn = next(e for e in emitted if e.metric_kind == "burn_down")
            self.assertEqual(burn.payload, {"total_scope": 4, "completed": 2, "remaining": 2})

    def test_incomplete_phase_emits_no_velocity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            emitted = record_phase_fleet_metrics(
                repo,
                phase="P1",
                completed=False,
                total_scope=4,
                completed_count=1,
                missing_gates=("IF-A-1",),
            )
            kinds = {e.metric_kind for e in emitted}
            self.assertNotIn("velocity", kinds)
            self.assertNotIn("burn_down", kinds)
            self.assertIn("promise_broken", kinds)

    def test_missing_gate_breaks_then_produced_gate_repairs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            # Phase P1 declares IF-A-1 but does not produce it -> break.
            record_phase_fleet_metrics(
                repo, phase="P1", completed=False, total_scope=3, completed_count=0,
                missing_gates=("IF-A-1",), timestamp="2026-07-04T00:00:00Z",
            )
            # Phase P2 produces IF-A-1 -> repair (break-anchored).
            record_phase_fleet_metrics(
                repo, phase="P2", completed=True, total_scope=3, completed_count=1,
                produced_gates=("IF-A-1",), timestamp="2026-07-04T01:00:00Z",
            )
            records = read_fleet_metrics(repo)
            kinds = [r["metric_kind"] for r in records]
            self.assertEqual(kinds.count("promise_broken"), 1)
            self.assertEqual(kinds.count("promise_repaired"), 1)

    def test_break_is_not_double_counted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            for _ in range(2):
                record_phase_fleet_metrics(
                    repo, phase="P1", completed=False, total_scope=3, completed_count=0,
                    missing_gates=("IF-A-1",),
                )
            broken = [r for r in read_fleet_metrics(repo) if r["metric_kind"] == "promise_broken"]
            self.assertEqual(len(broken), 1)


class FleetMetricsDerivationTest(unittest.TestCase):
    def test_promise_broken_duration_is_break_anchored(self) -> None:
        records = [
            FleetMetricEvent(metric_kind="promise_broken", timestamp="2026-07-04T00:00:00Z", payload={"gate": "IF-A-1"}).to_json(),
            FleetMetricEvent(metric_kind="promise_repaired", timestamp="2026-07-04T02:00:00Z", payload={"gate": "IF-A-1"}).to_json(),
        ]
        series = derive_fleet_metric_series(records, now="2026-07-04T03:00:00Z")
        pbd = series["promise_broken_duration"]
        self.assertEqual(pbd["points"][0]["broken_seconds"], 7200.0)  # 2h
        self.assertTrue(pbd["points"][0]["repaired"])
        self.assertEqual(pbd["aggregate"]["open_count"], 0)
        self.assertEqual(pbd["aggregate"]["mean_repaired_seconds"], 7200.0)

    def test_open_break_duration_measured_to_now(self) -> None:
        records = [
            FleetMetricEvent(metric_kind="promise_broken", timestamp="2026-07-04T00:00:00Z", payload={"gate": "IF-A-1"}).to_json(),
        ]
        series = derive_fleet_metric_series(records, now="2026-07-04T01:00:00Z")
        pbd = series["promise_broken_duration"]
        self.assertEqual(pbd["aggregate"]["open_count"], 1)
        self.assertEqual(pbd["aggregate"]["max_open_seconds"], 3600.0)
        self.assertEqual(pbd["aggregate"]["mean_repaired_seconds"], None)

    def test_velocity_and_burn_down_points_ordered(self) -> None:
        records = [
            FleetMetricEvent(metric_kind="velocity", timestamp="2026-07-04T00:00:00Z", payload={"completed_total": 1}).to_json(),
            FleetMetricEvent(metric_kind="burn_down", timestamp="2026-07-04T00:00:00Z", payload={"total_scope": 3, "completed": 1, "remaining": 2}).to_json(),
            FleetMetricEvent(metric_kind="velocity", timestamp="2026-07-04T01:00:00Z", payload={"completed_total": 2}).to_json(),
        ]
        series = derive_fleet_metric_series(records)
        self.assertEqual([p["completed_total"] for p in series["velocity"]["points"]], [1, 2])
        self.assertEqual(series["burn_down"]["points"][0]["remaining"], 2)


class RunnerHookLagTest(unittest.TestCase):
    """The runner hook fires mid-closeout; a fresh reconcile may lag by one.

    Proves _record_fleet_metrics_best_effort counts the just-completed phase even
    when reconcile has not yet folded its completion event — so burn_down reaches
    remaining=0 on the final phase instead of stalling at 1.
    """

    def test_completed_count_includes_the_just_completed_phase_despite_reconcile_lag(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import patch

        from phase_loop_runtime.runner import _record_fleet_metrics_best_effort

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            roadmap = repo / "roadmap.md"
            roadmap.write_text("# roadmap\n")
            # Reconcile still reports P1/P2 complete but NOT P3 (the completing one).
            lagging = SimpleNamespace(phases={"P1": "complete", "P2": "complete", "P3": "executed"})
            with patch("phase_loop_runtime.runner.reconcile", return_value=lagging), patch(
                "phase_loop_runtime.runner.parse_roadmap_phases", return_value=["P1", "P2", "P3"]
            ):
                _record_fleet_metrics_best_effort(
                    repo, roadmap, phase="P3", completed=True,
                    missing_gates=(), produced_gates=(),
                )
            burn = next(r for r in read_fleet_metrics(repo) if r["metric_kind"] == "burn_down")
            self.assertEqual(burn["payload"], {"total_scope": 3, "completed": 3, "remaining": 0})


if __name__ == "__main__":
    unittest.main()
