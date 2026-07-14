"""agent-harness#182 — validate_plan_doc.py producer-dependency check.

The runtime lane IR (phase_loop_runtime.plan_ir._producer_dependency_diagnostics) fails
closed with `missing_producer_dependency` at execute time when a lane consumes an
interface provided by another in-plan lane it does not depend on directly. The plan
validator must enforce the SAME contract at plan time (fail-fast), so a reviewed, signed
plan cannot pass validation then become non-executable at the approval/baseline gate.
"""
import importlib.util
import sys
import unittest
from pathlib import Path

import pytest

from _dotfiles_tree import skills_bundle_present

if not skills_bundle_present():
    pytest.skip(
        "requires the sibling phase-loop-skills bundle (absent in the standalone-from-wheel clean-room)",
        allow_module_level=True,
    )

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "phase-loop-skills" / "plan-phase" / "scripts" / "validate_plan_doc.py"


def _load():
    spec = importlib.util.spec_from_file_location("validate_plan_doc_prod_dep_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve annotations via sys.modules
    spec.loader.exec_module(mod)
    return mod


def _sections(provided_by, consumed_by):
    """Build the lane_sections dict the check consumes: {sl_id: {provided, consumed}}."""
    ids = set(provided_by) | set(consumed_by)
    return {
        sl: {
            "interfaces_provided": list(provided_by.get(sl, [])),
            "interfaces_consumed": list(consumed_by.get(sl, [])),
        }
        for sl in ids
    }


class ProducerDependencyCheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def _lanes(self, deps):
        return [self.mod.Lane(sl_id=sl, name=sl, depends_on=list(d)) for sl, d in deps.items()]

    def test_flags_consumer_missing_direct_producer_edge(self):
        # SL-2 consumes ISchema (provided by SL-0) but depends only on SL-1 — the exact
        # transitive-only case the runtime rejects with missing_producer_dependency.
        lanes = self._lanes({"SL-0": [], "SL-1": ["SL-0"], "SL-2": ["SL-1"]})
        sections = _sections({"SL-0": ["ISchema"]}, {"SL-2": ["ISchema"]})
        findings = self.mod._check_o_producer_dependency(lanes, sections)
        self.assertEqual(len(findings), 1, findings)
        self.assertIn("SL-2", findings[0])
        self.assertIn("SL-0", findings[0])
        self.assertNotIn("WARN", findings[0])  # an ERROR, not advice — it blocks execution

    def test_clean_when_direct_producer_edge_present(self):
        lanes = self._lanes({"SL-0": [], "SL-2": ["SL-0"]})
        sections = _sections({"SL-0": ["ISchema"]}, {"SL-2": ["ISchema"]})
        self.assertEqual(self.mod._check_o_producer_dependency(lanes, sections), [])

    def test_pre_existing_interface_needs_no_edge(self):
        lanes = self._lanes({"SL-0": [], "SL-2": []})
        sections = _sections({"SL-0": ["ISchema"]}, {"SL-2": ["ISchema (pre-existing)"]})
        self.assertEqual(self.mod._check_o_producer_dependency(lanes, sections), [])

    def test_self_provided_interface_needs_no_edge(self):
        lanes = self._lanes({"SL-0": []})
        sections = _sections({"SL-0": ["ISchema"]}, {"SL-0": ["ISchema"]})
        self.assertEqual(self.mod._check_o_producer_dependency(lanes, sections), [])

    def test_external_interface_no_in_plan_provider_is_silent(self):
        # No lane provides IExternal → no in-plan producer → the DAG-trace check (F)
        # owns that WARN; this check stays silent (it only enforces the edge to a provider).
        lanes = self._lanes({"SL-2": []})
        sections = _sections({}, {"SL-2": ["IExternal"]})
        self.assertEqual(self.mod._check_o_producer_dependency(lanes, sections), [])


if __name__ == "__main__":
    unittest.main()
