"""model-routing-v1 P4 — governed panel verdicts in the run-end summary."""
import unittest

from phase_loop_runtime.review_summary import (
    collect_panel_verdicts,
    panel_verdict_record,
    render_panel_verdicts_summary,
    summarize_run,
)


class PanelVerdictSummaryTest(unittest.TestCase):
    def test_record_shape(self):
        r = panel_verdict_record(phase="P3", outcome="mergeable", rounds=2)
        self.assertEqual(r["kind"], "panel_verdict")
        self.assertEqual(r["outcome"], "mergeable")
        self.assertEqual(r["rounds"], 2)
        self.assertFalse(r["degraded"])

    def test_collect_finds_nested_and_dedups(self):
        rec = panel_verdict_record(phase="P3", outcome="blocked")
        events = [{"metadata": {"closeout": {"x": rec}}}, {"a": rec}]  # echoed twice
        self.assertEqual(len(collect_panel_verdicts(events)), 1)

    def test_render_names_outcome_and_degraded(self):
        out = render_panel_verdicts_summary([
            panel_verdict_record(phase="P2", outcome="degraded", degraded=True, reason="only author vendor authed"),
        ])
        self.assertIn("P2: degraded", out)
        self.assertIn("advisory only", out)
        self.assertIn("only author vendor authed", out)

    def test_summarize_run_includes_verdicts_and_findings(self):
        events = [
            {"phase": "P3", "metadata": {"closeout": {"verification": {"results": [
                {"kind": "review_finding", "code": "x", "reason": "r", "severity": "warn"},
            ]}}}},
            {"metadata": {"panel": panel_verdict_record(phase="P3", outcome="mergeable", rounds=1)}},
        ]
        out = summarize_run(events)
        self.assertIn("Review findings this run", out)   # back-compat: findings still there
        self.assertIn("Governed panel verdicts this run", out)
        self.assertIn("P3: mergeable", out)

    def test_empty_is_blank(self):
        self.assertEqual(summarize_run([]), "")


if __name__ == "__main__":
    unittest.main()
