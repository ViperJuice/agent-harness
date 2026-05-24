import json
import unittest

from phase_loop_runtime.baml_modular import BamlValidationError, build_baml_request, parse_baml_response
from phase_loop_runtime.models import PHASE_STATUSES


class PhaseLoopBamlModularTest(unittest.TestCase):
    def test_build_baml_request_returns_prompt_and_request_metadata(self):
        request = build_baml_request(
            "EmitPhaseCloseout",
            {
                "phase_alias": "BAMLBASE",
                "plan_produces": ["IF-0-BAMLBASE-1"],
                "plan_owned_files": ["vendor/phase-loop-runtime/src/phase_loop_runtime/baml_modular.py"],
                "closeout_commit_sha": None,
            },
        )
        self.assertEqual(request.method, "POST")
        self.assertTrue(request.url)
        self.assertTrue(request.headers)
        self.assertTrue(request.body)
        self.assertIn("Emit the phase-loop closeout", request.prompt)
        self.assertIn("IF-0-BAMLBASE-1", request.prompt)

    def test_valid_closeout_json_parses_to_typed_payload(self):
        payload = {
            "terminal_status": "complete",
            "verification_status": "passed",
            "dirty_paths": [],
            "produced_if_gates": ["IF-0-BAMLBASE-1"],
            "next_action": None,
            "blocker_class": None,
            "blocker_summary": None,
            "human_required": None,
            "required_human_inputs": [],
        }
        parsed = parse_baml_response("EmitPhaseCloseout", json.dumps(payload))
        self.assertEqual(parsed.payload, payload)
        self.assertEqual(parsed.value.terminal_status, "complete")

    def test_missing_produced_if_gates_raises_validation_error(self):
        payload = {
            "terminal_status": "complete",
            "verification_status": "passed",
            "dirty_paths": [],
            "next_action": None,
            "blocker_class": None,
            "blocker_summary": None,
            "human_required": None,
            "required_human_inputs": [],
        }
        with self.assertRaises(BamlValidationError):
            parse_baml_response("EmitPhaseCloseout", json.dumps(payload))

    def test_current_closeout_rejects_dry_run_terminal_status(self):
        payload = {
            "terminal_status": "dry_run",
            "verification_status": "not_run",
            "dirty_paths": [],
            "produced_if_gates": [],
            "next_action": None,
            "blocker_class": None,
            "blocker_summary": None,
            "human_required": None,
            "required_human_inputs": [],
        }

        self.assertNotIn("dry_run", PHASE_STATUSES)
        with self.assertRaises(BamlValidationError) as ctx:
            parse_baml_response("EmitPhaseCloseout", json.dumps(payload))
        self.assertIn("invalid terminal_status: dry_run", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
