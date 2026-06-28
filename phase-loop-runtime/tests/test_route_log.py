"""model-routing-v1 P4 — route logging on dispatch events."""
import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.events import read_events
from phase_loop_runtime.models import ModelSelection, ResolvedExecutionPolicy
from phase_loop_runtime.profiles import resolve_model_selection_from_policy
from phase_loop_runtime.route_log import ROUTE_LOG_KEY, build_route_log, with_route_log
from phase_loop_runtime.runner import _append_coordinator_event
from phase_loop_test_utils import make_repo, write_named_roadmap


def _sel(**kw):
    base = dict(profile="execute", model="claude-sonnet-4-6", effort="medium",
                source="model_policy", override_reason="shipped model_policy", model_class="implementer")
    base.update(kw)
    return ModelSelection(**base)


class RouteLogTest(unittest.TestCase):
    def test_build_route_log_fields(self):
        log = build_route_log(_sel())
        self.assertEqual(log["model_class"], "implementer")
        self.assertEqual(log["concrete_model"], "claude-sonnet-4-6")
        self.assertEqual(log["effort"], "medium")
        self.assertEqual(log["route_reason"], "shipped model_policy")

    def test_escalation_fields_optional(self):
        self.assertNotIn("escalated_from", build_route_log(_sel()))
        log = build_route_log(_sel(), escalated_from="implementer", escalated_to="planner")
        self.assertEqual(log["escalated_from"], "implementer")
        self.assertEqual(log["escalated_to"], "planner")

    def test_metadata_only_no_secret_leak(self):
        # The record is built solely from routing scalars — only these keys exist.
        log = build_route_log(_sel())
        self.assertEqual(set(log), {"model_class", "concrete_model", "effort", "route_reason"})

    def test_with_route_log_preserves_other_keys(self):
        merged = with_route_log({"coordinator": {"x": 1}}, _sel())
        self.assertEqual(merged["coordinator"], {"x": 1})
        self.assertEqual(merged[ROUTE_LOG_KEY]["model_class"], "implementer")

    def test_model_class_flows_into_selection(self):
        rp = ResolvedExecutionPolicy(
            action="plan", lane=None, executor="claude", model="claude-opus-4-8",
            effort="max", work_unit_kind="phase_plan", model_class="planner",
        )
        sel = resolve_model_selection_from_policy(profile="plan", resolved_policy=rp)
        self.assertEqual(sel.model_class, "planner")

    def test_dispatch_event_carries_route_metadata(self):
        # The live site: _append_coordinator_event writes metadata.route.
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = write_named_roadmap(repo, (("ALPHA", "Alpha"),))
            _append_coordinator_event(
                repo=repo, roadmap=roadmap, phase="ALPHA",
                action="coordinator.phase_dispatched", status="executing",
                selection=_sel(), metadata={"wave_index": 0},
            )
            route = read_events(repo)[-1]["metadata"]["route"]
            self.assertEqual(route["model_class"], "implementer")
            self.assertEqual(route["concrete_model"], "claude-sonnet-4-6")
            self.assertEqual(route["effort"], "medium")


if __name__ == "__main__":
    unittest.main()
