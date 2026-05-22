import json
import unittest

from phase_loop_runtime.baml_modular import build_baml_request
from phase_loop_runtime.discovery import (
    CloseoutParseError,
    parse_closeout_payload,
    parse_closeout_payload_doc,
)
from phase_loop_runtime.runner import _parse_native_closeout_status


def _valid_payload(**updates):
    payload = {
        "terminal_status": "complete",
        "verification_status": "passed",
        "dirty_paths": ["docs/result.md"],
        "produced_if_gates": ["IF-0-CLOSEOUTHARDEN-1"],
        "next_action": None,
        "blocker_class": None,
        "blocker_summary": None,
        "human_required": None,
        "required_human_inputs": [],
    }
    payload.update(updates)
    return payload


class PhaseLoopCloseoutHardeningTest(unittest.TestCase):
    def test_prompt_contains_closeout_hardening_contract(self):
        request = build_baml_request(
            "EmitPhaseCloseout",
            {
                "phase_alias": "CLOSEOUTHARDEN",
                "plan_produces": ["IF-0-CLOSEOUTHARDEN-1", "IF-0-CLOSEOUTHARDEN-2"],
                "plan_owned_files": ["vendor/phase-loop-runtime/src/phase_loop_runtime/discovery.py"],
                "closeout_commit_sha": None,
            },
        )

        self.assertIn("Field-anchored enum list for terminal_status", request.prompt)
        self.assertIn("Field-anchored enum list for verification_status", request.prompt)
        self.assertIn("terminal_status must never use dry_run", request.prompt)
        self.assertIn("Terminal-status decision tree", request.prompt)
        self.assertIn("Field-pair invariants", request.prompt)
        self.assertIn("terminal_status=complete requires verification_status=passed", request.prompt)

    def test_valid_payload_parses_and_preserves_sibling_fields(self):
        payload = _valid_payload(
            dirty_paths=["docs/result.md"],
            produced_if_gates=["IF-0-CLOSEOUTHARDEN-1", "IF-0-CLOSEOUTHARDEN-2"],
            human_required=False,
            required_human_inputs=["operator decision"],
        )

        parsed, errors = parse_closeout_payload_doc(json.dumps(payload), kind="test")

        self.assertEqual(errors, ())
        self.assertEqual(parsed["dirty_paths"], ["docs/result.md"])
        self.assertEqual(parsed["produced_if_gates"], ["IF-0-CLOSEOUTHARDEN-1", "IF-0-CLOSEOUTHARDEN-2"])
        self.assertFalse(parsed["human_required"])
        self.assertEqual(parsed["required_human_inputs"], ["operator decision"])

    def test_json_string_payload_parses(self):
        payload = _valid_payload()
        parsed, errors = parse_closeout_payload_doc(json.dumps(json.dumps(payload)), kind="test")

        self.assertEqual(errors, ())
        self.assertEqual(parsed["terminal_status"], "complete")

    def test_invalid_terminal_status_dry_run_soft_fails(self):
        parsed, errors = parse_closeout_payload_doc(
            json.dumps(_valid_payload(terminal_status="dry_run")),
            kind="test",
        )

        self.assertIsNone(parsed)
        self.assertIsInstance(errors[0], CloseoutParseError)
        self.assertEqual(errors[0].field, "terminal_status")
        self.assertEqual(errors[0].invalid_literal, "dry_run")
        self.assertIn("dry_run", errors[0].raw_message)

    def test_invalid_verification_status_soft_fails(self):
        parsed, errors = parse_closeout_payload_doc(
            json.dumps(_valid_payload(verification_status="verified")),
            kind="test",
        )

        self.assertIsNone(parsed)
        self.assertEqual(errors[0].field, "verification_status")
        self.assertEqual(errors[0].invalid_literal, "verified")

    def test_invalid_blocker_class_soft_fails(self):
        parsed, errors = parse_closeout_payload_doc(
            json.dumps(
                _valid_payload(
                    terminal_status="blocked",
                    verification_status="blocked",
                    blocker_class="blocked_by_executor",
                    blocker_summary="bad literal",
                    human_required=False,
                )
            ),
            kind="test",
        )

        self.assertIsNone(parsed)
        self.assertEqual(errors[0].field, "blocker_class")
        self.assertEqual(errors[0].invalid_literal, "blocked_by_executor")

    def test_complete_with_not_run_field_pair_soft_fails(self):
        parsed, errors = parse_closeout_payload_doc(
            json.dumps(_valid_payload(verification_status="not_run")),
            kind="test",
        )

        self.assertIsNone(parsed)
        self.assertEqual(errors[0].field, "terminal_status+verification_status")
        self.assertEqual(errors[0].invalid_literal, "complete/not_run")

    def test_backward_compatible_payload_parser_returns_payload(self):
        payload = _valid_payload()

        parsed = parse_closeout_payload(json.dumps(payload), kind="test")

        self.assertEqual(parsed["terminal_status"], "complete")

    def test_backward_compatible_payload_parser_returns_none_for_literal_drift(self):
        parsed = parse_closeout_payload(
            json.dumps(_valid_payload(terminal_status="dry_run")),
            kind="test",
        )

        self.assertIsNone(parsed)

    def test_runner_converts_native_closeout_literal_drift_to_contract_bug(self):
        parsed = _parse_native_closeout_status(
            "executor logs\n"
            + json.dumps(_valid_payload(terminal_status="dry_run"))
            + "\nmore logs"
        )

        self.assertEqual(parsed["automation_status"], "blocked")
        self.assertEqual(parsed["automation_blocker_class"], "contract_bug")
        self.assertEqual(parsed["automation_human_required"], "false")
        self.assertIn("dry_run", parsed["automation_blocker_summary"])
        self.assertIn("terminal_status", parsed["automation_blocker_summary"])

    def test_regen_dry_run_reproduction_payload_soft_fails_before_schema(self):
        payload = {
            "terminal_status": "dry_run",
            "verification_status": "passed",
            "dirty_paths": [],
            "produced_if_gates": [],
            "required_human_inputs": [],
        }

        parsed, errors = parse_closeout_payload_doc(json.dumps(payload), kind="regen")

        self.assertIsNone(parsed)
        self.assertEqual(errors[0].source, "regen")
        self.assertEqual(errors[0].invalid_literal, "dry_run")


if __name__ == "__main__":
    unittest.main()
