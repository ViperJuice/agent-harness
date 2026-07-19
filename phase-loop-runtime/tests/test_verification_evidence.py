import json
import sys
import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.verification_evidence import (
    append_evidence_entry,
    load_verification_artifact,
    run_verification,
    validate_verification_artifact,
    validate_verification_commands,
)


class VerificationEvidenceTest(unittest.TestCase):
    def test_threaded_phase_alias_wins_over_current_phase(self):
        # ah#85(b): verification.json must record the LIVE run alias threaded by the caller,
        # not re-derive from state.json:current_phase (which drifts after a mid-run roadmap
        # amendment). Here current_phase is OVERLAY but the run's alias is VIRTUALDEV.
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / ".phase-loop").mkdir()
            (repo / ".phase-loop/state.json").write_text('{"current_phase": "OVERLAY"}', encoding="utf-8")
            run_dir = repo / ".phase-loop/runs/test-run"

            run_verification(
                repo, run_dir, [[sys.executable, "-c", "print('ok')"]], None, None, 5,
                phase_alias="VIRTUALDEV",
            )
            payload = json.loads((run_dir / "verification.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["phase_alias"], "VIRTUALDEV")

    def test_phase_alias_precedence_env_over_threaded_over_current_phase(self):
        import os
        from unittest.mock import patch

        from phase_loop_runtime.verification_evidence import _phase_alias

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / ".phase-loop").mkdir()
            (repo / ".phase-loop/state.json").write_text('{"current_phase": "OVERLAY"}', encoding="utf-8")
            # Clear any ambient alias env so the fallback/threaded asserts are hermetic.
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("PHASE_LOOP_PHASE_ALIAS", None)
                os.environ.pop("PHASE_ALIAS", None)
                self.assertEqual(_phase_alias(repo), "OVERLAY")                  # no alias -> current_phase
                self.assertEqual(_phase_alias(repo, "VIRTUALDEV"), "VIRTUALDEV")  # threaded beats current_phase
                os.environ["PHASE_LOOP_PHASE_ALIAS"] = "ENVWINS"
                self.assertEqual(_phase_alias(repo, "VIRTUALDEV"), "ENVWINS")     # env escape-hatch wins

    def test_execute_verification_forwards_live_alias_into_artifact(self):
        # ah#85(b) — cover the helper->run_verification hop: _run_execute_verification must
        # thread `phase_alias` into the written verification.json, so breaking that forwarding
        # fails HERE. Uses a differing current_phase.
        import os
        from unittest.mock import patch

        from phase_loop_runtime import runner
        from phase_loop_test_utils import commit_fixture_paths, make_repo, write_phase_plan

        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.write_text(
                "---\n"
                f"automation:\n  suite_command: [{sys.executable!r}, -c, 'print(\"suite\")']\n"
                "---\n"
                "# Roadmap\n\n### Phase 0 - Runner (RUNNER)\n",
                encoding="utf-8",
            )
            plan = write_phase_plan(
                repo, "RUNNER", roadmap,
                body=f"# RUNNER\n\n## Verification\n- `{sys.executable} -c \"print('verify')\"`\n",
            )
            commit_fixture_paths(repo, "add plan", roadmap, plan)
            # state.json current_phase DIFFERS from the run's live alias (the drift scenario).
            (repo / ".phase-loop").mkdir(parents=True, exist_ok=True)
            (repo / ".phase-loop/state.json").write_text('{"current_phase": "OVERLAY"}', encoding="utf-8")
            run_dir = repo / ".phase-loop/runs/exec-test"
            run_dir.mkdir(parents=True, exist_ok=True)

            # Hermetic: an ambient operator override would (correctly) outrank the threaded
            # alias and mask the assertion, so clear both env vars for this check.
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("PHASE_LOOP_PHASE_ALIAS", None)
                os.environ.pop("PHASE_ALIAS", None)
                result = runner._run_execute_verification(
                    repo=repo, roadmap=roadmap, plan=plan,
                    artifacts={"root": run_dir}, phase_alias="VIRTUALDEV",
                )
            # sanity: verification actually ran and wrote the artifact (not an early return)
            self.assertTrue(result.get("ok"), result)
            payload = json.loads((run_dir / "verification.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["phase_alias"], "VIRTUALDEV")

    def test_run_loop_callsite_forwards_live_alias_to_execute_verification(self):
        # ah#85(b) — pin the CALLSITE (runner.py:3508): run_loop must forward the LIVE run
        # `alias` into _run_execute_verification, so verification.json is attributed to this
        # run's phase (not re-derived from a drifted current_phase). Driving full run_loop at
        # runtime requires the injected skill bundle, which is why the end-to-end verification
        # test is dotfiles_integration-marked (excluded by CI's `-m "not dotfiles_integration"`).
        # Pin the invariant STATICALLY instead: parse run_loop's AST and assert the call passes
        # `phase_alias=alias`. Deleting the forwarding arg fails HERE — CI-visible, hermetic,
        # bundle-free. Pairs with the helper->artifact behavioral test above.
        import ast
        import inspect

        from phase_loop_runtime import runner

        tree = ast.parse(inspect.getsource(runner.run_loop))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_run_execute_verification"
        ]
        self.assertTrue(calls, "run_loop must call _run_execute_verification")
        for call in calls:
            kwargs = {kw.arg: kw.value for kw in call.keywords}
            self.assertIn(
                "phase_alias", kwargs, "run_loop must forward phase_alias to _run_execute_verification"
            )
            self.assertIsInstance(kwargs["phase_alias"], ast.Name)
            self.assertEqual(
                kwargs["phase_alias"].id, "alias", "run_loop must forward the live run `alias`"
            )

    def test_all_pass_commands_write_artifact_and_log(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / ".phase-loop").mkdir()
            (repo / ".phase-loop/state.json").write_text('{"current_phase": "VC"}', encoding="utf-8")
            run_dir = repo / ".phase-loop/runs/test-run"

            result = run_verification(
                repo,
                run_dir,
                [[sys.executable, "-c", "print('first')"], [sys.executable, "-c", "print('second')"]],
                None,
                None,
                5,
            )

            artifact_path = run_dir / "verification.json"
            log_path = run_dir / "verification.log"
            self.assertTrue(artifact_path.exists())
            self.assertTrue(log_path.exists())
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 2)
            self.assertEqual(payload["run_id"], "test-run")
            self.assertEqual(payload["phase_alias"], "VC")
            self.assertEqual(payload["env_refresh"], None)
            self.assertEqual(payload["suite"], None)
            self.assertEqual(len(payload["commands"]), 2)
            self.assertEqual([item["exit_code"] for item in payload["commands"]], [0, 0])
            self.assertGreaterEqual(payload["commands"][1]["log_offset"], payload["commands"][0]["log_offset"])
            self.assertTrue(payload["started_at"])
            self.assertTrue(payload["finished_at"])
            self.assertEqual(payload["log_sha256"], result.log_sha256)
            self.assertIn("first", log_path.read_text(encoding="utf-8"))
            self.assertIn("second", log_path.read_text(encoding="utf-8"))

    def test_failing_command_is_recorded_as_data(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            run_dir = repo / ".phase-loop/runs/test-run"

            result = run_verification(repo, run_dir, [[sys.executable, "-c", "raise SystemExit(7)"]], None, None, 5)

            self.assertEqual(result.commands[0].exit_code, 7)
            payload = json.loads((run_dir / "verification.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["commands"][0]["exit_code"], 7)

    def test_validator_reports_empty_missing_executable_and_path_findings(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "exists.txt").write_text("ok", encoding="utf-8")

            findings = validate_verification_commands(
                repo,
                [
                    [],
                    ["definitely-not-a-phase-loop-command"],
                    [sys.executable, "missing/file.txt"],
                    [sys.executable, "--cwd=../outside"],
                    [sys.executable, "exists.txt"],
                ],
            )

            self.assertEqual(
                [finding.code for finding in findings],
                ["empty_argv", "unresolved_executable", "missing_path", "outside_repo_path"],
            )

    def test_suite_timeout_is_recorded_as_failed_suite_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            run_dir = repo / ".phase-loop/runs/test-run"

            result = run_verification(
                repo,
                run_dir,
                [],
                [sys.executable, "-c", "import time; time.sleep(2)"],
                None,
                0.1,
            )

            self.assertIsNotNone(result.suite)
            self.assertEqual(result.suite.exit_code, 124)
            self.assertIn("timed out", (run_dir / "verification.log").read_text(encoding="utf-8"))

    def test_load_verification_artifact_rejects_malformed_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            artifact = Path(td) / "verification.json"
            artifact.write_text('{"schema_version": 1}', encoding="utf-8")

            with self.assertRaises(ValueError):
                load_verification_artifact(artifact)

    def test_validate_verification_artifact_checks_hash_and_exit_codes(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            run_dir = repo / ".phase-loop/runs/test-run"
            run_verification(repo, run_dir, [[sys.executable, "-c", "print('ok')"]], None, None, 5)

            validation = validate_verification_artifact(run_dir / "verification.json")

            self.assertTrue(validation.ok)
            self.assertEqual(validation.code, "ok")
            self.assertEqual(validation.exit_summary["commands"], [0])

            (run_dir / "verification.log").write_text("tampered", encoding="utf-8")
            tampered = validate_verification_artifact(run_dir / "verification.json")
            self.assertFalse(tampered.ok)
            self.assertEqual(tampered.code, "log_sha256_mismatch")

    def test_validate_verification_artifact_reports_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            run_dir = repo / ".phase-loop/runs/test-run"
            run_verification(repo, run_dir, [[sys.executable, "-c", "raise SystemExit(9)"]], None, None, 5)

            validation = validate_verification_artifact(run_dir / "verification.json")

            self.assertFalse(validation.ok)
            self.assertEqual(validation.code, "nonzero_exit")
            self.assertEqual(validation.exit_summary["commands"], [9])

    def test_append_evidence_entry_preserves_existing_bytes_and_appends_json_line(self):
        with tempfile.TemporaryDirectory() as td:
            doc = Path(td) / "evidence.md"
            doc.write_bytes(b"existing evidence")

            payload = append_evidence_entry(doc, {"kind": "operator_check", "status": "passed"})

            data = doc.read_bytes()
            self.assertTrue(data.startswith(b"existing evidence\n"))
            appended = json.loads(data.splitlines()[-1])
            self.assertEqual(appended["entry"], {"kind": "operator_check", "status": "passed"})
            self.assertEqual(payload["entry"]["kind"], "operator_check")


class VerificationFailureDiagnosticsTest(unittest.TestCase):
    """agent-harness#209: the verdict must localize + preserve the raw diagnostic of
    the stage that broke, with a runner-observed failure_kind, in declared order."""

    def _validate(self, repo, commands, suite=None, env_refresh=None, timeout=30):
        run_dir = repo / ".phase-loop/runs/test-run"
        run_verification(repo, run_dir, commands, suite, env_refresh, timeout)
        return validate_verification_artifact(run_dir / "verification.json")

    def test_single_failing_command_preserves_stderr_and_kind(self):
        # (a)
        with tempfile.TemporaryDirectory() as td:
            v = self._validate(
                Path(td),
                [[sys.executable, "-c", "import sys; sys.stderr.write('DISTINCTIVE_REASON\\n'); sys.exit(1)"]],
            )
            self.assertFalse(v.ok)
            diags = v.to_json()["diagnostics"]
            self.assertEqual(len(diags), 1)
            self.assertIn("DISTINCTIVE_REASON", diags[0]["raw_tail"])
            self.assertEqual(diags[0]["failure_kind"], "nonzero_exit")
            self.assertEqual(diags[0]["role"], "command")
            self.assertTrue(diags[0]["argv"])

    def test_failing_last_command_tail_excludes_passing_suite_output(self):
        # (b) the decisive round-1 boundary bug: the command's tail must be the
        # command's OWN output, never the trailing passing suite's.
        with tempfile.TemporaryDirectory() as td:
            v = self._validate(
                Path(td),
                [[sys.executable, "-c", "import sys; sys.stderr.write('CMD_FAILURE_MARK\\n'); sys.exit(1)"]],
                suite=[sys.executable, "-c", "print('SUITE_PASS_NOISE ' * 50)"],
            )
            self.assertFalse(v.ok)
            cmd_diag = [d for d in v.diagnostics if d["role"] == "command"][0]
            self.assertIn("CMD_FAILURE_MARK", cmd_diag["raw_tail"])
            self.assertNotIn("SUITE_PASS_NOISE", cmd_diag["raw_tail"])

    def test_two_step_chain_step1_fail_step2_pass_reduces_fail_closed_in_order(self):
        # (c)
        with tempfile.TemporaryDirectory() as td:
            v = self._validate(
                Path(td),
                [
                    [sys.executable, "-c", "import sys; sys.stderr.write('STEP1\\n'); sys.exit(1)"],
                    [sys.executable, "-c", "print('step2 ok')"],
                ],
            )
            self.assertFalse(v.ok)
            self.assertEqual(v.exit_summary["commands"], [1, 0])
            diags = v.diagnostics
            self.assertEqual(len(diags), 1)
            self.assertEqual(diags[0]["index"], 0)
            self.assertIn("STEP1", diags[0]["raw_tail"])

    def test_env_refresh_failure_tail_comes_from_head_of_log(self):
        # (d) env_refresh runs FIRST; a tail-of-whole-log would miss it.
        with tempfile.TemporaryDirectory() as td:
            v = self._validate(
                Path(td),
                [[sys.executable, "-c", "print('later command ok')"]],
                env_refresh={
                    "triggered": True,
                    "install_argv": [sys.executable, "-c", "import sys; sys.stderr.write('ENV_REFRESH_BROKE\\n'); sys.exit(3)"],
                },
            )
            self.assertFalse(v.ok)
            env_diag = [d for d in v.diagnostics if d["role"] == "env_refresh"][0]
            self.assertIn("ENV_REFRESH_BROKE", env_diag["raw_tail"])

    def test_failure_kind_runner_observed_not_derived_from_exit_code(self):
        # (e) a real timeout is "timeout"; a child that ITSELF returns 124 is
        # "nonzero_exit"; a missing executable is "error".
        with tempfile.TemporaryDirectory() as td:
            v = self._validate(
                Path(td),
                [[sys.executable, "-c", "import time; time.sleep(30)"]],
                timeout=1,
            )
            self.assertEqual(v.diagnostics[0]["failure_kind"], "timeout")
        with tempfile.TemporaryDirectory() as td:
            v = self._validate(Path(td), [[sys.executable, "-c", "import sys; sys.exit(124)"]])
            self.assertEqual(v.diagnostics[0]["failure_kind"], "nonzero_exit")
        with tempfile.TemporaryDirectory() as td:
            v = self._validate(Path(td), [["/nonexistent/verify-binary-xyz"]])
            self.assertEqual(v.diagnostics[0]["failure_kind"], "error")

    def test_no_output_failure_is_flagged_missing_output_not_absent(self):
        # (f) anti-scrubbing: a silent failure still carries typed context.
        with tempfile.TemporaryDirectory() as td:
            v = self._validate(Path(td), [[sys.executable, "-c", "import sys; sys.exit(1)"]])
            self.assertFalse(v.ok)
            self.assertEqual(len(v.diagnostics), 1)
            self.assertEqual(v.diagnostics[0]["diagnostic_status"], "missing_output")
            self.assertEqual(v.diagnostics[0]["failure_kind"], "nonzero_exit")
            self.assertEqual(v.diagnostics[0]["exit_code"], 1)

    def test_raw_tail_is_bounded(self):
        # (g)
        from phase_loop_runtime.verification_evidence import DIAGNOSTIC_TAIL_BYTES

        with tempfile.TemporaryDirectory() as td:
            v = self._validate(
                Path(td),
                [[sys.executable, "-c", f"import sys; sys.stdout.write('x' * {DIAGNOSTIC_TAIL_BYTES * 3}); sys.exit(1)"]],
            )
            self.assertLessEqual(len(v.diagnostics[0]["raw_tail"].encode("utf-8")), DIAGNOSTIC_TAIL_BYTES)
            self.assertTrue(v.diagnostics[0]["truncated"])

    def test_green_run_has_no_diagnostics(self):
        # (h)
        with tempfile.TemporaryDirectory() as td:
            v = self._validate(Path(td), [[sys.executable, "-c", "print('ok')"]])
            self.assertTrue(v.ok)
            self.assertEqual(v.diagnostics, ())
            self.assertEqual(v.to_json()["diagnostics"], [])

    def test_v1_artifact_still_loads(self):
        # (i) back-compat: a v1 payload (no v2 stage fields) parses, fields default None.
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "run"
            run_dir.mkdir(parents=True)
            log = run_dir / "verification.log"
            log.write_bytes(b"legacy log\n")
            import hashlib as _h

            payload = {
                "schema_version": 1,
                "run_id": "run",
                "phase_alias": "VC",
                "commands": [{"argv": ["true"], "cwd": ".", "exit_code": 0, "duration_s": 0.1, "log_offset": 0}],
                "env_refresh": None,
                "suite": None,
                "started_at": "2026-07-19T00:00:00Z",
                "finished_at": "2026-07-19T00:00:01Z",
                "log_sha256": _h.sha256(b"legacy log\n").hexdigest(),
            }
            (run_dir / "verification.json").write_text(json.dumps(payload), encoding="utf-8")
            result = load_verification_artifact(run_dir / "verification.json")
            self.assertEqual(result.schema_version, 1)
            self.assertIsNone(result.commands[0].log_end_offset)
            self.assertIsNone(result.commands[0].failure_kind)

    def test_non_executable_target_is_evidence_not_a_crash(self):
        # CR codex#3: a PermissionError (non-executable target) must be recorded as a
        # failed stage (failure_kind=error), not crash run_verification.
        import os

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            target = repo / "noexec.sh"
            target.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
            os.chmod(target, 0o644)
            v = self._validate(repo, [[str(target)]])
            self.assertFalse(v.ok)
            self.assertEqual(v.diagnostics[0]["failure_kind"], "error")

    def test_load_rejects_unknown_failure_kind(self):
        # CR codex#2: an out-of-enum failure_kind is rejected at load, not passed
        # through as if runner-observed.
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            run_dir = repo / ".phase-loop/runs/test-run"
            run_verification(repo, run_dir, [[sys.executable, "-c", "raise SystemExit(1)"]], None, None, 5)
            artifact = run_dir / "verification.json"
            payload = json.loads(artifact.read_text(encoding="utf-8"))
            payload["commands"][0]["failure_kind"] = "totally_bogus"
            artifact.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_verification_artifact(artifact)

    def _tamper_end_and_validate(self, cmd0_argv):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            run_dir = repo / ".phase-loop/runs/test-run"
            run_verification(
                repo,
                run_dir,
                [cmd0_argv, [sys.executable, "-c", "print('CMD1_SECRET ' * 20)"]],
                None,
                None,
                5,
            )
            artifact = run_dir / "verification.json"
            payload = json.loads(artifact.read_text(encoding="utf-8"))
            payload["commands"][0]["log_end_offset"] = 10_000_000  # lie: reach into cmd1
            artifact.write_text(json.dumps(payload), encoding="utf-8")
            return validate_verification_artifact(artifact)

    def test_tampered_end_offset_fails_closed_no_leak(self):
        # CR codex#1: a lying log_end_offset (> the next stage's start) is an invalid
        # recorded range -> fail closed to an empty tail, never the following stage's bytes.
        v = self._tamper_end_and_validate(
            [sys.executable, "-c", "import sys; sys.stderr.write('CMD0_MARK\\n'); sys.exit(1)"]
        )
        d0 = v.diagnostics[0]
        self.assertNotIn("CMD1_SECRET", d0["raw_tail"])
        self.assertEqual(d0["raw_tail"], "")
        self.assertEqual(d0["diagnostic_status"], "missing_output")

    def test_tampered_end_on_zero_output_stage_cannot_leak_shared_start(self):
        # CR codex#1 + gemini round 2: a zero-output failing stage shares its start offset
        # with the next stage; the execution-order upper bound must still confine it so a
        # tampered end cannot steal the next stage's bytes.
        v = self._tamper_end_and_validate([sys.executable, "-c", "import sys; sys.exit(1)"])
        d0 = v.diagnostics[0]
        self.assertNotIn("CMD1_SECRET", d0["raw_tail"])
        self.assertEqual(d0["raw_tail"], "")

    def test_tampered_backward_start_cannot_leak_preceding_stage_bytes(self):
        # CR Fable round 3 (dual of the round-2 END leak): cmd0 PASSES printing a secret,
        # cmd1 fails zero-output; tampering cmd1's log_offset BACKWARD must not surface
        # cmd0's bytes as cmd1's diagnostic (lower-bound validation).
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            run_dir = repo / ".phase-loop/runs/test-run"
            run_verification(
                repo,
                run_dir,
                [
                    [sys.executable, "-c", "print('EARLIER_STAGE_SECRET' * 5)"],
                    [sys.executable, "-c", "import sys; sys.exit(1)"],
                ],
                None,
                None,
                5,
            )
            artifact = run_dir / "verification.json"
            payload = json.loads(artifact.read_text(encoding="utf-8"))
            payload["commands"][1]["log_offset"] = 0  # lie: reach back into cmd0
            artifact.write_text(json.dumps(payload), encoding="utf-8")
            v = validate_verification_artifact(artifact)
            d = v.diagnostics[0]
            self.assertNotIn("EARLIER_STAGE_SECRET", d["raw_tail"])
            self.assertEqual(d["raw_tail"], "")

    def test_v2_subprocess_env_refresh_missing_failure_kind_is_rejected(self):
        # CR Fable round 3 nit (a): a subprocess-backed v2 env_refresh (int log_offset)
        # that failed must also carry failure_kind.
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            run_dir = repo / ".phase-loop/runs/test-run"
            run_verification(
                repo,
                run_dir,
                [[sys.executable, "-c", "print('ok')"]],
                None,
                {"triggered": True, "install_argv": [sys.executable, "-c", "raise SystemExit(2)"]},
                5,
            )
            artifact = run_dir / "verification.json"
            payload = json.loads(artifact.read_text(encoding="utf-8"))
            self.assertIsInstance(payload["env_refresh"].get("log_offset"), int)
            self.assertEqual(payload["env_refresh"]["failure_kind"], "nonzero_exit")
            del payload["env_refresh"]["failure_kind"]
            artifact.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_verification_artifact(artifact)

    def test_v2_failed_command_missing_failure_kind_is_rejected(self):
        # CR codex#2 round 2: a v2 subprocess-backed failed stage must carry failure_kind;
        # stripping it (e.g. to mislabel a timeout) is rejected at load.
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            run_dir = repo / ".phase-loop/runs/test-run"
            run_verification(repo, run_dir, [[sys.executable, "-c", "raise SystemExit(1)"]], None, None, 5)
            artifact = run_dir / "verification.json"
            payload = json.loads(artifact.read_text(encoding="utf-8"))
            self.assertEqual(payload["commands"][0]["failure_kind"], "nonzero_exit")
            del payload["commands"][0]["failure_kind"]
            artifact.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_verification_artifact(artifact)

    def test_v2_failure_kind_inconsistent_with_exit_code_is_rejected(self):
        # CR codex#2 round 2: a valid-enum-but-inconsistent value (timeout without exit 124)
        # is rejected.
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            run_dir = repo / ".phase-loop/runs/test-run"
            run_verification(repo, run_dir, [[sys.executable, "-c", "raise SystemExit(1)"]], None, None, 5)
            artifact = run_dir / "verification.json"
            payload = json.loads(artifact.read_text(encoding="utf-8"))
            payload["commands"][0]["failure_kind"] = "timeout"  # exit is 1, not 124
            artifact.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_verification_artifact(artifact)

    def test_raw_tail_byte_bounded_on_multibyte_output(self):
        # CR Fable#1: a multibyte / high-density-replacement tail still respects the cap.
        from phase_loop_runtime.verification_evidence import DIAGNOSTIC_TAIL_BYTES

        with tempfile.TemporaryDirectory() as td:
            v = self._validate(
                Path(td),
                [[sys.executable, "-c",
                  f"import sys; sys.stdout.buffer.write(b'\\xe2\\x82\\xac' * {DIAGNOSTIC_TAIL_BYTES}); sys.exit(1)"]],
            )
            self.assertLessEqual(len(v.diagnostics[0]["raw_tail"].encode("utf-8")), DIAGNOSTIC_TAIL_BYTES)
            self.assertTrue(v.diagnostics[0]["truncated"])

    def test_single_field_exit_code_flip_to_zero_is_rejected(self):
        # CR codex#4 round 4: flipping a failed v2 stage's exit_code 1->0 (log +
        # failure_kind untouched) must not read as green — the leftover failure_kind
        # makes the single-field tamper fail closed.
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            run_dir = repo / ".phase-loop/runs/test-run"
            run_verification(repo, run_dir, [[sys.executable, "-c", "raise SystemExit(1)"]], None, None, 5)
            artifact = run_dir / "verification.json"
            payload = json.loads(artifact.read_text(encoding="utf-8"))
            payload["commands"][0]["exit_code"] = 0  # lie: turn the red stage green
            artifact.write_text(json.dumps(payload), encoding="utf-8")
            v = validate_verification_artifact(artifact)
            self.assertFalse(v.ok)  # NOT a green pass
            self.assertEqual(v.code, "malformed_artifact")

    def test_malformed_schema_version_type_fails_closed_not_crash(self):
        # CR codex#4 round 4: a non-int schema_version (unhashable) must yield a
        # malformed_artifact verdict, not an uncaught TypeError.
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            run_dir = repo / ".phase-loop/runs/test-run"
            run_verification(repo, run_dir, [[sys.executable, "-c", "print('ok')"]], None, None, 5)
            artifact = run_dir / "verification.json"
            payload = json.loads(artifact.read_text(encoding="utf-8"))
            payload["schema_version"] = []  # unhashable -> would crash `x not in frozenset`
            artifact.write_text(json.dumps(payload), encoding="utf-8")
            v = validate_verification_artifact(artifact)  # must not raise
            self.assertFalse(v.ok)
            self.assertEqual(v.code, "malformed_artifact")

    def test_interpreter_blocker_surfaces_reason_not_scrubbed(self):
        # (j) a requires-python/pin mismatch synthesizes 127 evidence OUTSIDE
        # _run_process; its diagnostic must surface the "unavailable" reason.
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            # requires-python floor no local interpreter can satisfy -> blocker.
            (repo / "pyproject.toml").write_text(
                "[project]\nname='x'\nversion='0'\nrequires-python='>=99.0'\n", encoding="utf-8"
            )
            v = self._validate(repo, [[sys.executable, "-c", "print('never runs')"]])
            self.assertFalse(v.ok)
            self.assertTrue(v.diagnostics)
            blocker_diag = v.diagnostics[0]
            self.assertEqual(blocker_diag["failure_kind"], "error")
            self.assertIn("interpreter", blocker_diag["raw_tail"].lower())
            self.assertEqual(blocker_diag["diagnostic_status"], "present")


if __name__ == "__main__":
    unittest.main()
