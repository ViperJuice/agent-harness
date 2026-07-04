from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.fleet_metrics import record_phase_fleet_metrics
from phase_loop_runtime.fleet_metrics_export import (
    SanitizationError,
    assert_sanitized,
    build_export_payload,
    build_sanitized_export,
)


class ExportShapeTest(unittest.TestCase):
    def test_export_carries_only_the_three_named_ledger_faithful_series(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            record_phase_fleet_metrics(
                repo, phase="P1", completed=True, total_scope=3, completed_count=1,
                missing_gates=("IF-A-1",), timestamp="2026-07-04T00:00:00Z",
            )
            payload = build_sanitized_export(
                repo, repo_id="agent-harness", captured_at="2026-07-04T02:00:00Z"
            )
            self.assertEqual(payload["schema"], "fleet_metrics_export.v1")
            self.assertEqual(payload["repo"], "agent-harness")
            kinds = {s["series_kind"] for s in payload["series"]}
            self.assertEqual(kinds, {"velocity", "burn_down", "promise_broken_duration"})
            self.assertTrue(all(s["provenance"] == "ledger-faithful" for s in payload["series"]))


class SanitizationTest(unittest.TestCase):
    """The provable-sanitization contract: no path/secret/payload/gate crosses the wall."""

    def test_no_gate_identifier_survives_into_the_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            # A gate id that is ALSO path-like — must never appear in the export.
            leaky_gate = "IF-/etc/passwd/SECRET-TOKEN"
            record_phase_fleet_metrics(
                repo, phase="P1", completed=False, total_scope=3, completed_count=0,
                missing_gates=(leaky_gate,), timestamp="2026-07-04T00:00:00Z",
            )
            payload = build_sanitized_export(
                repo, repo_id="agent-harness", captured_at="2026-07-04T01:00:00Z"
            )
            blob = json.dumps(payload)
            self.assertNotIn("passwd", blob)
            self.assertNotIn("SECRET", blob)
            self.assertNotIn("IF-", blob)
            # ...but the durational aggregate DID cross (open break for 1h).
            pbd = next(s for s in payload["series"] if s["series_kind"] == "promise_broken_duration")
            self.assertEqual(pbd["aggregate"]["open_count"], 1)
            self.assertEqual(pbd["aggregate"]["max_open_seconds"], 3600.0)

    def test_assert_sanitized_rejects_a_raw_path(self) -> None:
        with self.assertRaises(SanitizationError):
            assert_sanitized({"series": [{"points": [{"t": "/home/user/.phase-loop/events.jsonl"}]}]})

    def test_assert_sanitized_rejects_a_forbidden_key(self) -> None:
        with self.assertRaises(SanitizationError):
            assert_sanitized({"gate": "IF-A-1"})  # 'gate' is not an allowed key

    def test_assert_sanitized_rejects_a_secret_substring(self) -> None:
        with self.assertRaises(SanitizationError):
            assert_sanitized({"repo": "ok", "series": [{"provenance": "bearer token abc123"}]})

    def test_assert_sanitized_rejects_a_non_timestamp_string(self) -> None:
        with self.assertRaises(SanitizationError):
            assert_sanitized({"series": [{"series_kind": "velocity", "points": [{"t": "not-a-timestamp"}]}]})

    def test_assert_sanitized_accepts_clean_aggregate_payload(self) -> None:
        payload = build_export_payload(
            "agent-harness",
            {
                "velocity": {"points": [{"t": "2026-07-04T00:00:00Z", "completed_total": 1}]},
                "burn_down": {"points": [{"t": "2026-07-04T00:00:00Z", "total": 3, "completed": 1, "remaining": 2}]},
                "promise_broken_duration": {
                    "points": [{"t": "2026-07-04T00:00:00Z", "broken_seconds": 60.0, "repaired": True}],
                    "aggregate": {"open_count": 0, "max_open_seconds": 0, "mean_repaired_seconds": 60.0, "repaired_count": 1},
                },
            },
            "2026-07-04T02:00:00Z",
        )
        assert_sanitized(payload)  # must not raise

    def test_repo_id_must_be_a_slug_not_a_path(self) -> None:
        with self.assertRaises(SanitizationError):
            build_export_payload("/home/user/repo", {}, "2026-07-04T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
