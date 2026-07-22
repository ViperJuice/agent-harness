import json
import unittest
from unittest.mock import patch

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
        # FAV (issue #91): the closeout schema gained optional visual-evidence
        # fields (additive). The typed payload dumps them as None when absent.
        # FAV #272: visual_render_declared is the same kind of additive
        # optional field.
        expected = {
            **payload,
            "visual_evidence_path": None,
            "visual_evidence_non_black_pixels": None,
            "visual_evidence_pixel_min": None,
            "visual_evidence_pixel_max": None,
            "visual_evidence_opt_out": None,
            "visual_render_declared": None,
        }
        self.assertEqual(parsed.payload, expected)
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

    def test_build_baml_request_converts_pyo3_panic_to_validation_error(self):
        class PanicException(BaseException):
            pass

        PanicException.__module__ = "pyo3_runtime"

        class FakeRuntime:
            def build_request_sync(self, *_args, **_kwargs):
                raise PanicException("Attempted to create a NULL object.")

        class FakeContextManager:
            def clone_context(self):
                return object()

        with patch("phase_loop_runtime.baml_modular._runtime", return_value=(FakeRuntime(), FakeContextManager())):
            with self.assertRaisesRegex(BamlValidationError, "Attempted to create a NULL object"):
                build_baml_request("EvaluateSuspectedFakeEvidence", {})


if __name__ == "__main__":
    unittest.main()
