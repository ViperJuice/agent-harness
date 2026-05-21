import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.models import BLOCKER_CLASSES, CLOSEOUT_SCHEMA, PHASE_STATUSES


class PhaseLoopCloseoutSchemaTest(unittest.TestCase):
    def test_closeout_schema_requires_native_fields(self):
        self.assertEqual(CLOSEOUT_SCHEMA["type"], "object")
        self.assertEqual(
            tuple(CLOSEOUT_SCHEMA["required"]),
            ("terminal_status", "verification_status", "dirty_paths", "produced_if_gates"),
        )
        properties = CLOSEOUT_SCHEMA["properties"]
        for field in CLOSEOUT_SCHEMA["required"]:
            self.assertIn(field, properties)
        self.assertEqual(tuple(properties["terminal_status"]["enum"]), PHASE_STATUSES)
        self.assertEqual(tuple(properties["blocker_class"]["enum"]), (*BLOCKER_CLASSES, "none"))

    def test_complete_closeout_requires_at_least_one_produced_gate_via_runner_check(self):
        # The conditional rule "when terminal_status=complete, produced_if_gates
        # must be non-empty" was previously expressed in the schema via allOf +
        # if/then. OpenAI's response_format JSON Schema dialect (used by Codex
        # --output-schema) rejects allOf/anyOf/oneOf/not/if/then — only a strict
        # subset is supported. We moved the conditional enforcement to runner-
        # side IF-gate Tier 1 validation in closeout_validation. The schema
        # therefore should NOT contain allOf.
        self.assertNotIn("allOf", CLOSEOUT_SCHEMA, msg="schema must avoid allOf for Codex --output-schema dialect compatibility")
        # produced_if_gates remains structurally required at the schema layer:
        self.assertIn("produced_if_gates", CLOSEOUT_SCHEMA["required"])
        # The complete-status non-empty enforcement lives in closeout_validation:
        from phase_loop_runtime.closeout_validation import validate_produced_gates
        with tempfile.TemporaryDirectory() as td:
            plan = Path(td) / "phase-plan.md"
            plan.write_text("# X\n\n**Produces**: IF-0-X-1\n", encoding="utf-8")
            result = validate_produced_gates(plan, {"terminal_status": "complete", "produced_if_gates": []})
        self.assertFalse(result.ok, "validator must reject empty produced_if_gates when terminal_status=complete")
        self.assertIsNotNone(result.blocker_class, "rejection must surface a typed blocker_class")


if __name__ == "__main__":
    unittest.main()
