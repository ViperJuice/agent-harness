import glob
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.launcher import (
    CODEX_OUTPUT_SCHEMA_PLACEHOLDER,
    LaunchResult,
    build_claude_command,
    build_codex_command,
    build_launch_request,
    build_launch_spec,
    launch_with_spec,
)
from phase_loop_runtime.models import CLOSEOUT_SCHEMA
from phase_loop_runtime.profiles import resolve_profile, resolve_profile_for_executor
from phase_loop_runtime.prompts import build_prompt


JSON_CLOSEOUT_SCHEMA = json.loads(json.dumps(CLOSEOUT_SCHEMA))

import pytest

# TESTDECOUPLE SL-1 (overlay-dependent): builds a skill/adoption bundle or runs the
# runtime execute path, which resolves the dotfiles skill-source / profile overlay
# (claude-config/*, codex-config/* …) absent standalone. Run-time integration: the
# conftest hook skips it when no dotfiles tree is reachable.
pytestmark = pytest.mark.dotfiles_integration


class PhaseLoopNativeFlagsTest(unittest.TestCase):
    def test_codex_command_uses_output_schema_placeholder_without_build_time_temp(self):
        # #63: build emits a placeholder and creates no temp file. The real schema
        # is materialized at launch (see test_launch_with_spec_removes_codex_schema_temp_file).
        selection = resolve_profile("execute")
        pattern = str(Path(tempfile.gettempdir()) / "*-phase-loop-closeout-schema.json")
        before = set(glob.glob(pattern))
        command = build_codex_command(Path("/repo"), selection, "prompt", closeout_schema=CLOSEOUT_SCHEMA)
        self.assertEqual(command[command.index("--output-schema") + 1], CODEX_OUTPUT_SCHEMA_PLACEHOLDER)
        self.assertEqual(set(glob.glob(pattern)) - before, set())

    def test_claude_command_uses_compact_inline_json_schema(self):
        selection = resolve_profile_for_executor(action="execute", executor="claude")
        command = build_claude_command(
            Path("/repo"),
            selection,
            "prompt",
            permission_mode="bypassPermissions",
            closeout_schema=CLOSEOUT_SCHEMA,
        )
        schema_text = command[command.index("--json-schema") + 1]

        self.assertEqual(json.loads(schema_text), JSON_CLOSEOUT_SCHEMA)
        self.assertNotIn("\n", schema_text)

    def test_build_launch_spec_limits_native_flags_to_codex_and_claude(self):
        codex_request = build_launch_request(
            executor="codex",
            action="execute",
            repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
            model_selection=resolve_profile("execute"),
            prompt_bundle=build_prompt(
                "execute",
                Path("/repo/specs/phase-plans-v1.md"),
                phase="RUNNER",
                plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
            ),
            json_output=True,
            bypass_approvals=False,
        )
        codex_spec = build_launch_spec(codex_request)
        self.assertIn("--output-schema", codex_spec.command)
        # #63: no build-time temp; placeholder carried, cleanup deferred to launch.
        self.assertEqual(codex_spec.command[codex_spec.command.index("--output-schema") + 1], CODEX_OUTPUT_SCHEMA_PLACEHOLDER)
        self.assertEqual(codex_spec.cleanup_paths, ())

        gemini_request = build_launch_request(
            executor="gemini",
            action="execute",
            repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
            model_selection=resolve_profile_for_executor(action="execute", executor="gemini"),
            prompt_bundle=build_prompt(
                "execute",
                Path("/repo/specs/phase-plans-v1.md"),
                phase="RUNNER",
                plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
                harness_target="gemini",
            ),
            json_output=True,
            bypass_approvals=False,
        )
        gemini_spec = build_launch_spec(gemini_request)
        self.assertNotIn("--output-schema", gemini_spec.command)
        self.assertEqual(gemini_spec.cleanup_paths, ())

    def test_launch_with_spec_removes_codex_schema_temp_file(self):
        request = build_launch_request(
            executor="codex",
            action="execute",
            repo=Path("/repo"),
            roadmap=Path("/repo/specs/phase-plans-v1.md"),
            phase="RUNNER",
            plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
            model_selection=resolve_profile("execute"),
            prompt_bundle=build_prompt(
                "execute",
                Path("/repo/specs/phase-plans-v1.md"),
                phase="RUNNER",
                plan=Path("/repo/plans/phase-plan-v1-RUNNER.md"),
            ),
            json_output=True,
            bypass_approvals=False,
        )
        spec = build_launch_spec(request)
        # #63: nothing materialized at build — the schema is created at launch under
        # the run-scoped log dir and removed in the finally.
        self.assertEqual(spec.cleanup_paths, ())

        with tempfile.TemporaryDirectory() as td, patch(
            "phase_loop_runtime.launcher.launch",
            return_value=LaunchResult(command=spec.command, returncode=0, output="{}"),
        ):
            log_path = Path(td) / "run.log"
            result = launch_with_spec(spec, log_path=log_path)
            schema_path = log_path.parent / "codex-output-schema.json"

        self.assertFalse(schema_path.exists())
        self.assertIn(str(schema_path), result.cleanup_evidence["schema_cleanup"]["removed"])


if __name__ == "__main__":
    unittest.main()
