import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.plan_ir import parse_phase_plan_ir
from phase_loop_test_utils import make_repo, write_phase_plan


class PhaseLoopLaneIRTest(unittest.TestCase):
    def test_parser_extracts_lane_contract_fields_and_policy(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(
                repo,
                "RUNNER",
                roadmap,
                body=(
                    "# RUNNER\n\n"
                    "## Lane Index & Dependencies\n\n"
                    "SL-0 - Contract\n"
                    "  Depends on: (none)\n"
                    "  Blocks: SL-1\n"
                    "  Parallel-safe: no\n\n"
                    "SL-1 - Reducer\n"
                    "  Depends on: SL-0\n"
                    "  Blocks: (none)\n"
                    "  Parallel-safe: yes\n\n"
                    "## Lanes\n\n"
                    "### SL-0 - Contract\n\n"
                    "- **Owned files**: `src/contract.py`, `tests/test_contract.py`\n"
                    "- **Interfaces provided**: `CONTRACT-api`, `parse_phase_plan_ir`\n"
                    "- **Interfaces consumed**: `ExecutionPolicyDocument` (pre-existing)\n"
                    "- **Parallel-safe**: no\n"
                    "- **Tasks**:\n"
                    "  - test: Add parser tests.\n"
                    "  - impl: Implement parser.\n"
                    "  - verify: `python3 -m unittest tests.test_contract`\n\n"
                    "### SL-1 - Acceptance Reducer\n\n"
                    "- **Owned files**: none\n"
                    "- **Interfaces provided**: `RUNNER-acceptance`\n"
                    "- **Interfaces consumed**: `CONTRACT-api`\n"
                    "- **Parallel-safe**: yes\n"
                    "- **Tasks**:\n"
                    "  - test: Reduce exit criteria.\n"
                    "  - impl: No production write expected.\n"
                    "  - verify: `python3 -m unittest tests.test_contract`\n\n"
                    "## Execution Policy\n\n"
                    "- work-unit defaults: work-unit=`lane_execute`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`\n"
                    "- execute: executor=`codex`, model=`gpt-5.5`, effort=`medium`, work-unit=`lane_execute`, unsupported=`inherit_default`, inherit-default=`true`\n"
                    "- SL-1: executor=`codex`, effort=`high`, work-unit=`phase_reducer`, unsupported=`inherit_default`, inherit-default=`true`\n\n"
                    "## Dispatch Hints\n\n"
                    "- preferred executors: `codex`\n"
                    "- required capabilities: `structured_output`\n"
                ),
            )

            ir = parse_phase_plan_ir(plan)

            self.assertTrue(ir.valid, [diagnostic.to_json() for diagnostic in ir.diagnostics])
            self.assertEqual([lane.lane_id for lane in ir.lanes], ["SL-0", "SL-1"])
            self.assertEqual(ir.lanes[0].owned_files, ("src/contract.py", "tests/test_contract.py"))
            self.assertEqual(ir.lanes[1].depends_on, ("SL-0",))
            self.assertTrue(ir.lanes[1].read_only)
            self.assertTrue(ir.lanes[1].parallel_safe)
            self.assertEqual(ir.lanes[1].reducer_kind, "acceptance_reducer")
            self.assertEqual(ir.lanes[1].execution_policy.work_unit_kind, "phase_reducer")
            self.assertEqual(ir.dispatch_hints["default"].required_capabilities, ("structured_output",))
            self.assertIsNone(ir.merge_policy)

    def test_parser_exposes_merge_policy_without_breaking_metadata_or_hints(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(
                repo,
                "RUNNER",
                roadmap,
                extra_frontmatter={"merge_policy": '{"on_pass": "auto", "approvers": ["ops"]}'},
                body=(
                    "# RUNNER\n\n"
                    "## Lanes\n\n"
                    "### SL-0 - Contract\n\n"
                    "- **Owned files**: none\n"
                    "- **Interfaces provided**: `CONTRACT-api`\n"
                    "- **Tasks**:\n"
                    "  - verify: `python3 -m unittest tests.test_contract`\n\n"
                    "## Dispatch Hints\n\n"
                    "- preferred executors: `codex`\n"
                    "- required capabilities: `structured_output`\n"
                ),
            )

            ir = parse_phase_plan_ir(plan)

            self.assertTrue(ir.valid, [diagnostic.to_json() for diagnostic in ir.diagnostics])
            self.assertEqual(ir.metadata["merge_policy"], '{"on_pass": "auto", "approvers": ["ops"]}')
            self.assertEqual(ir.merge_policy.on_pass, "auto")
            self.assertEqual(ir.merge_policy.approvers, ("ops",))
            self.assertEqual(ir.lanes[0].lane_id, "SL-0")
            self.assertEqual(ir.dispatch_hints["default"].required_capabilities, ("structured_output",))

    def test_parser_reports_malformed_contract_diagnostics(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(
                repo,
                "RUNNER",
                roadmap,
                body=(
                    "# RUNNER\n\n"
                    "## Lane Index & Dependencies\n\n"
                    "- SL-0 - Bad; Depends on: SL-1\n"
                    "- SL-1 - Bad; Depends on: SL-0\n"
                    "- SL-2 - Missing Producer\n\n"
                    "## Lanes\n\n"
                    "### SL-0 - Bad\n"
                    "- **Owned files**: src/app.py\n"
                    "- **Interfaces provided**: `A`\n\n"
                    "### SL-1 - Bad\n"
                    "- **Owned files**: `src/*.py`\n"
                    "- **Interfaces provided**: `B`\n\n"
                    "### SL-2 - Missing Producer\n"
                    "- **Owned files**: `src/app.py`\n"
                    "- **Interfaces consumed**: `A`\n"
                ),
            )

            ir = parse_phase_plan_ir(plan)
            kinds = {diagnostic.kind for diagnostic in ir.diagnostics}

            self.assertIn("malformed_owned_files", kinds)
            self.assertIn("cycle", kinds)
            self.assertIn("overlapping_write_ownership", kinds)
            self.assertIn("missing_producer_dependency", kinds)
            self.assertFalse(ir.valid)


if __name__ == "__main__":
    unittest.main()
