import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.plan_ir import parse_phase_plan_ir


class PhaseLoopLaneIRFixturesTest(unittest.TestCase):
    def test_current_v7_and_v8_phase_plans_parse_without_rewrites(self):
        plans = sorted((ROOT / "plans").glob("phase-plan-v7-*.md")) + sorted((ROOT / "plans").glob("phase-plan-v8-*.md"))
        self.assertTrue(plans)
        failures = {}
        for plan in plans:
            ir = parse_phase_plan_ir(plan)
            if not ir.valid:
                failures[plan.name] = [diagnostic.to_json() for diagnostic in ir.diagnostics]
        self.assertEqual(failures, {})

    def test_harness_plan_shape_fixtures_parse(self):
        fixtures = sorted((Path(__file__).resolve().parent / "fixtures" / "phase_loop_lane_ir").glob("*.md"))
        self.assertEqual(len(fixtures), 5)
        for fixture in fixtures:
            with self.subTest(fixture=fixture.name):
                ir = parse_phase_plan_ir(fixture)
                self.assertTrue(ir.valid, [diagnostic.to_json() for diagnostic in ir.diagnostics])
                self.assertEqual(len(ir.lanes), 1)


if __name__ == "__main__":
    unittest.main()
