import hashlib
import json
import unittest
from pathlib import Path

from phase_loop_runtime.baml_modular import export_function_schema
from phase_loop_runtime.launcher import CODEX_OUTPUT_SCHEMA_PLACEHOLDER, build_launch_request, build_launch_spec
from phase_loop_runtime.models import CLOSEOUT_SCHEMA
from phase_loop_runtime.profiles import resolve_profile_for_executor
from phase_loop_runtime.prompts import build_prompt


def _schema_hash(schema: dict) -> str:
    return hashlib.sha256(json.dumps(schema, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


class PhaseLoopSchemaFlowTest(unittest.TestCase):
    def _spec(self, executor: str, action: str = "execute"):
        roadmap = Path("/repo/specs/phase-plans-v20.md")
        plan = Path("/repo/plans/phase-plan-v20-BAMLNATIVE.md")
        request = build_launch_request(
            executor=executor,
            action=action,
            repo=Path("/repo"),
            roadmap=roadmap,
            phase="BAMLNATIVE",
            plan=plan,
            model_selection=resolve_profile_for_executor(action=action, executor=executor),
            prompt_bundle=build_prompt(action, roadmap, phase="BAMLNATIVE", plan=plan, harness_target=executor),
            json_output=True,
            bypass_approvals=False,
        )
        return build_launch_spec(request)

    def test_models_schema_is_baml_exported_schema(self):
        self.assertEqual(_schema_hash(CLOSEOUT_SCHEMA), _schema_hash(export_function_schema("EmitPhaseCloseout")))

    def test_all_closeout_actions_receive_same_schema_across_five_harnesses(self):
        schema = export_function_schema("EmitPhaseCloseout")
        expected_hash = _schema_hash(schema)
        for action in ("execute", "repair", "review"):
            with self.subTest(action=action, executor="codex"):
                spec = self._spec("codex", action)
                # #63: codex schema is carried on the spec for launch-time
                # materialization; the build command holds a placeholder.
                self.assertEqual(spec.command[spec.command.index("--output-schema") + 1], CODEX_OUTPUT_SCHEMA_PLACEHOLDER)
                self.assertEqual(_schema_hash(spec.codex_output_schema), expected_hash)

            with self.subTest(action=action, executor="claude"):
                spec = self._spec("claude", action)
                self.assertEqual(_schema_hash(json.loads(spec.command[spec.command.index("--json-schema") + 1])), expected_hash)

            for executor in ("gemini", "opencode", "pi"):
                with self.subTest(action=action, executor=executor):
                    spec = self._spec(executor, action)
                    self.assertIn(f"schema_sha256: {expected_hash}", spec.prompt_bundle.render_context())
                    self.assertIn("Phase-loop closeout JSON schema description:", spec.prompt_bundle.render_context())

    def test_plan_action_is_not_forced_into_closeout_schema(self):
        for executor in ("codex", "claude", "gemini", "opencode", "pi"):
            with self.subTest(executor=executor):
                spec = self._spec(executor, "plan")
                self.assertNotIn("--output-schema", spec.command)
                self.assertNotIn("--json-schema", spec.command)
                self.assertNotIn("Phase-loop closeout JSON schema description:", spec.prompt_bundle.render_context())


if __name__ == "__main__":
    unittest.main()
