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
        # CR codex#1 (round 1-3): a lying log_end_offset (> the next stage's start) was an
        # invalid recorded range that the #209 neighbour-bounds check failed closed to an
        # empty tail. agent-harness#243 CR round 4: the artifacts these tests build via
        # ``run_verification`` are SEALED, and the seal digest now covers the per-stage
        # offset fields too (a prior round's exclusion was itself the #243 CR round-4
        # defect being fixed). A sealed artifact's offset tamper now trips the STRONGER
        # ``artifact_seal_mismatch`` verdict before ever reaching the nonzero_exit /
        # neighbour-bounds branch -- still zero leak, just a different (and more
        # correct) verdict code. See ``test_unsealed_legacy_offset_tamper_still_bounded_by_neighbor_checks``
        # below for the neighbour-bounds check's continuing role on an UNSEALED artifact.
        v = self._tamper_end_and_validate(
            [sys.executable, "-c", "import sys; sys.stderr.write('CMD0_MARK\\n'); sys.exit(1)"]
        )
        self.assertFalse(v.ok)
        self.assertEqual(v.code, "artifact_seal_mismatch")
        self.assertEqual(v.diagnostics, ())
        self.assertNotIn("CMD1_SECRET", " ".join(v.findings))

    def test_tampered_end_on_zero_output_stage_cannot_leak_shared_start(self):
        # CR codex#1 + gemini round 2 (updated agent-harness#243 CR round 4): a zero-output
        # failing stage shares its start offset with the next stage. On the SEALED artifacts
        # these tests build, the seal now covers offsets, so this tamper trips
        # artifact_seal_mismatch before the neighbour-bounds check is even reached -- no
        # leak either way.
        v = self._tamper_end_and_validate([sys.executable, "-c", "import sys; sys.exit(1)"])
        self.assertFalse(v.ok)
        self.assertEqual(v.code, "artifact_seal_mismatch")
        self.assertEqual(v.diagnostics, ())
        self.assertNotIn("CMD1_SECRET", " ".join(v.findings))

    def test_tampered_backward_start_cannot_leak_preceding_stage_bytes(self):
        # CR Fable round 3 (dual of the round-2 END leak, updated agent-harness#243 CR
        # round 4): cmd0 PASSES printing a secret, cmd1 fails zero-output; tampering cmd1's
        # log_offset BACKWARD used to be caught by the neighbour-bounds lower-bound check on
        # the nonzero_exit branch. The SEALED artifact this test builds now has offsets
        # covered by the seal, so the tamper trips artifact_seal_mismatch first -- still no
        # leak, just caught one layer earlier.
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
            self.assertFalse(v.ok)
            self.assertEqual(v.code, "artifact_seal_mismatch")
            self.assertEqual(v.diagnostics, ())
            self.assertNotIn("EARLIER_STAGE_SECRET", " ".join(v.findings))

    def test_unsealed_legacy_offset_tamper_still_bounded_by_neighbor_checks(self):
        # agent-harness#243 CR round 4: on an UNSEALED legacy artifact (no valid seal
        # trailer in the log), the whole-artifact seal check is skipped entirely (back-compat)
        # -- so the #209 neighbour-bounds check (``_stage_bounds`` / ``_stage_raw_tail``)
        # remains the sole offset-integrity guard, exactly as it did before #243 existed.
        # This is the dual of the sealed-artifact tests above: same tamper shape, but on a
        # hand-built log with NO seal trailer, and the assertion is that it still fails
        # closed via nonzero_exit / bounded-empty-tail, not via a seal mismatch it can no
        # longer detect (there is nothing to seal-check).
        import hashlib as _h

        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "run"
            run_dir.mkdir(parents=True)
            log_bytes = b"CMD0_MARK\nCMD1_SECRET_OUTPUT " * 5 + b"\n"
            (run_dir / "verification.log").write_bytes(log_bytes)
            payload = {
                "schema_version": 2,
                "run_id": "run",
                "phase_alias": "VC",
                "commands": [
                    {
                        "argv": ["x"], "cwd": ".", "exit_code": 1, "duration_s": 0.1,
                        "log_offset": 0, "log_end_offset": 10, "failure_kind": "nonzero_exit",
                    },
                    {
                        "argv": ["y"], "cwd": ".", "exit_code": 0, "duration_s": 0.1,
                        "log_offset": 10, "log_end_offset": len(log_bytes),
                    },
                ],
                "env_refresh": None,
                "suite": None,
                "started_at": "2026-07-21T00:00:00Z",
                "finished_at": "2026-07-21T00:00:01Z",
                "log_sha256": _h.sha256(log_bytes).hexdigest(),
            }
            # tamper: extend cmd0's end offset to reach into cmd1's (secret-bearing) region.
            payload["commands"][0]["log_end_offset"] = len(log_bytes)
            artifact = run_dir / "verification.json"
            artifact.write_text(json.dumps(payload), encoding="utf-8")
            v = validate_verification_artifact(artifact)
            self.assertFalse(v.ok)
            self.assertEqual(v.code, "nonzero_exit")  # no seal trailer -> neighbour-bounds guards
            self.assertEqual(v.diagnostics[0]["raw_tail"], "")
            self.assertNotIn("CMD1_SECRET", v.diagnostics[0]["raw_tail"])

    def test_unsealed_legacy_backward_start_tamper_still_bounded_by_neighbor_checks(self):
        # agent-harness#243 CR round 4: dual of the test above, exercising the LOWER edge of
        # the #209 neighbour-bounds check (Fable round 3's contribution -- a tampered backward
        # ``log_offset`` must not leak a PRECEDING stage's bytes) on an UNSEALED artifact. All
        # three sealed tests that used to cover this edge (in ``VerificationFailureDiagnosticsTest``
        # above) now short-circuit at the whole-artifact seal check before ever reaching
        # ``_stage_raw_tail`` -- so this unsealed variant is what keeps the lower-edge fail-closed
        # path itself under live test coverage.
        import hashlib as _h

        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "run"
            run_dir.mkdir(parents=True)
            secret = b"EARLIER_STAGE_SECRET" * 5
            log_bytes = secret + b"\n"  # cmd0 (passing) prints the secret; cmd1 fails zero-output
            (run_dir / "verification.log").write_bytes(log_bytes)
            cmd0_end = len(secret)
            payload = {
                "schema_version": 2,
                "run_id": "run",
                "phase_alias": "VC",
                "commands": [
                    {
                        "argv": ["x"], "cwd": ".", "exit_code": 0, "duration_s": 0.1,
                        "log_offset": 0, "log_end_offset": cmd0_end,
                    },
                    {
                        "argv": ["y"], "cwd": ".", "exit_code": 1, "duration_s": 0.1,
                        "log_offset": len(log_bytes), "log_end_offset": len(log_bytes),
                        "failure_kind": "nonzero_exit",
                    },
                ],
                "env_refresh": None,
                "suite": None,
                "started_at": "2026-07-21T00:00:00Z",
                "finished_at": "2026-07-21T00:00:01Z",
                "log_sha256": _h.sha256(log_bytes).hexdigest(),
            }
            # tamper: pull cmd1's start BACKWARD into cmd0's (secret-bearing) region.
            payload["commands"][1]["log_offset"] = 0
            artifact = run_dir / "verification.json"
            artifact.write_text(json.dumps(payload), encoding="utf-8")
            v = validate_verification_artifact(artifact)
            self.assertFalse(v.ok)
            self.assertEqual(v.code, "nonzero_exit")  # no seal trailer -> neighbour-bounds guards
            self.assertEqual(v.diagnostics[0]["raw_tail"], "")
            self.assertNotIn("EARLIER_STAGE_SECRET", v.diagnostics[0]["raw_tail"])

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


class VerificationEvidenceHardening243Test(unittest.TestCase):
    # agent-harness#243: whole-artifact integrity (seal + size bound) and closeout-diagnostic
    # redaction. Mirrors the #209 tamper tests in VerificationEvidenceTest above.

    def _run(self, repo, commands, env_refresh=None, suite=None, timeout=30):
        run_dir = repo / ".phase-loop/runs/test-run"
        run_verification(repo, run_dir, commands, suite, env_refresh, timeout)
        return run_dir / "verification.json"

    def test_normal_sealed_artifact_still_passes(self):
        # Round-trip: a freshly-sealed GREEN artifact validates ok=True (proves the writer
        # digest and the validator's recomputed digest agree byte-for-byte after JSON).
        with tempfile.TemporaryDirectory() as td:
            artifact = self._run(Path(td), [[sys.executable, "-c", "print('ok')"]])
            v = validate_verification_artifact(artifact)
            self.assertTrue(v.ok)
            self.assertEqual(v.code, "ok")

    def test_seal_line_present_in_log_and_after_stage_regions(self):
        with tempfile.TemporaryDirectory() as td:
            artifact = self._run(Path(td), [[sys.executable, "-c", "print('ok')"]])
            log = (artifact.parent / "verification.log").read_bytes()
            self.assertIn(b"verification-artifact-sha256:", log)
            # The seal is the LAST line.
            self.assertTrue(log.rstrip(b"\n").split(b"\n")[-1].startswith(b"verification-artifact-sha256:"))

    def test_structural_edit_deleting_failed_command_is_caught_fail_closed(self):
        # THE headline #243 case: a run with a failing cmd0 + passing cmd1; deleting the
        # failed commands[] entry would forge a PASS under #209 (log_sha256 still matches, no
        # nonzero remains). The whole-artifact seal detects the mutated payload.
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            artifact = self._run(
                repo,
                [
                    [sys.executable, "-c", "import sys; sys.exit(1)"],
                    [sys.executable, "-c", "print('cmd1 ok')"],
                ],
            )
            payload = json.loads(artifact.read_text(encoding="utf-8"))
            del payload["commands"][0]  # forge: drop the only failing stage
            artifact.write_text(json.dumps(payload), encoding="utf-8")
            v = validate_verification_artifact(artifact)
            self.assertFalse(v.ok)  # NOT a forged green pass
            self.assertEqual(v.code, "artifact_seal_mismatch")

    def test_consistent_field_edit_on_pass_path_is_caught(self):
        # A single/multi-field internally-consistent edit that does not touch pass/fail (e.g.
        # rewriting phase_alias) still changes the sealed payload digest -> fail closed.
        with tempfile.TemporaryDirectory() as td:
            artifact = self._run(Path(td), [[sys.executable, "-c", "print('ok')"]])
            payload = json.loads(artifact.read_text(encoding="utf-8"))
            payload["phase_alias"] = "FORGED-PHASE"
            artifact.write_text(json.dumps(payload), encoding="utf-8")
            v = validate_verification_artifact(artifact)
            self.assertFalse(v.ok)
            self.assertEqual(v.code, "artifact_seal_mismatch")

    def test_unsealed_legacy_artifact_still_validates(self):
        # Back-compat: an artifact whose log carries NO seal trailer (v1/older, or an
        # externally-built log) skips the seal check and still passes.
        import hashlib as _h

        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "run"
            run_dir.mkdir(parents=True)
            log_bytes = b"legacy stage output\n"
            (run_dir / "verification.log").write_bytes(log_bytes)
            payload = {
                "schema_version": 1,
                "run_id": "run",
                "phase_alias": "VC",
                "commands": [{"argv": ["true"], "cwd": ".", "exit_code": 0, "duration_s": 0.1, "log_offset": 0}],
                "env_refresh": None,
                "suite": None,
                "started_at": "2026-07-21T00:00:00Z",
                "finished_at": "2026-07-21T00:00:01Z",
                "log_sha256": _h.sha256(log_bytes).hexdigest(),
            }
            (run_dir / "verification.json").write_text(json.dumps(payload), encoding="utf-8")
            v = validate_verification_artifact(run_dir / "verification.json")
            self.assertTrue(v.ok)
            self.assertEqual(v.code, "ok")

    def test_oversized_artifact_rejected_before_parse(self):
        from unittest.mock import patch

        from phase_loop_runtime import verification_evidence as ve

        with tempfile.TemporaryDirectory() as td:
            artifact = self._run(Path(td), [[sys.executable, "-c", "print('ok')"]])
            # Patch the bound below the (normal, valid) artifact's size so the size gate fires.
            with patch.object(ve, "MAX_ARTIFACT_BYTES", 10):
                v = validate_verification_artifact(artifact)
            self.assertFalse(v.ok)
            self.assertEqual(v.code, "oversized_artifact")

    def test_failing_artifact_field_tamper_is_caught_as_seal_mismatch(self):
        # agent-harness#243 CR round 3 (defect 1): a FAILING artifact whose non-offset field is
        # tampered (seal stale) was previously UNCAUGHT on the failing path -- the seal was only
        # checked on the would-be-PASS branch, so the nonzero_exit verdict masked the tamper.
        # The seal check now runs BEFORE the pass/fail branch, so a failing artifact is
        # seal-protected too: this is caught as its own integrity verdict.
        with tempfile.TemporaryDirectory() as td:
            artifact = self._run(Path(td), [[sys.executable, "-c", "import sys; sys.exit(1)"]])
            payload = json.loads(artifact.read_text(encoding="utf-8"))
            payload["phase_alias"] = "FORGED-WHILE-FAILING"  # tamper a sealed field; log untouched
            artifact.write_text(json.dumps(payload), encoding="utf-8")
            v = validate_verification_artifact(artifact)
            self.assertFalse(v.ok)
            self.assertEqual(v.code, "artifact_seal_mismatch")  # not masked by nonzero_exit

    def test_last_stage_tail_cannot_swallow_seal_trailer(self):
        # agent-harness#243 CR round 3 (defect 2), updated CR round 4: extending the LAST
        # (failing) stage's log_end_offset to len(log) used to make its tail swallow the
        # appended ``verification-artifact-sha256:`` trailer (persisting the seal bytes as
        # unredacted stage output). Round 3 fixed the bounding (``_artifact_seal_region_start``
        # keeps stage regions OUTSIDE the trailer, kept unchanged here) but excluded offsets
        # from the seal digest, so the tamper flowed to the nonzero_exit branch where bounding
        # caught it. Round 4 folds offsets INTO the seal digest (this file's actual fix), so
        # this exact tamper is now caught EARLIER, as its own seal-integrity verdict -- still
        # zero leak (the trailer never appears anywhere, since diagnostics are never built),
        # just a stronger, earlier-firing check.
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            artifact = self._run(repo, [[sys.executable, "-c", "import sys; sys.stderr.write('CMD_MARK\\n'); sys.exit(1)"]])
            log_bytes = (artifact.parent / "verification.log").read_bytes()
            self.assertIn(b"verification-artifact-sha256:", log_bytes)
            payload = json.loads(artifact.read_text(encoding="utf-8"))
            payload["commands"][0]["log_end_offset"] = len(log_bytes)  # lie: reach into the trailer
            artifact.write_text(json.dumps(payload), encoding="utf-8")
            v = validate_verification_artifact(artifact)
            self.assertFalse(v.ok)
            self.assertEqual(v.code, "artifact_seal_mismatch")  # offset tamper IS a seal break now
            self.assertEqual(v.diagnostics, ())
            self.assertNotIn("verification-artifact-sha256:", " ".join(v.findings))

    def test_correctly_sealed_failing_artifact_reports_failure_normally(self):
        # agent-harness#243 CR round 3: moving the seal check earlier must NOT reclassify a
        # LEGITIMATE (correctly-sealed) failing artifact -- it still reports its stage failure via
        # the normal nonzero_exit path with a preserved diagnostic tail, exactly as before. Only an
        # actual seal MISMATCH changes the outcome.
        with tempfile.TemporaryDirectory() as td:
            artifact = self._run(
                Path(td),
                [[sys.executable, "-c", "import sys; sys.stderr.write('LEGIT_FAILURE_MARK\\n'); sys.exit(1)"]],
            )
            v = validate_verification_artifact(artifact)  # no tampering
            self.assertFalse(v.ok)
            self.assertEqual(v.code, "nonzero_exit")  # NOT artifact_seal_mismatch
            self.assertEqual(len(v.diagnostics), 1)
            self.assertIn("LEGIT_FAILURE_MARK", v.diagnostics[0]["raw_tail"])
            self.assertEqual(v.diagnostics[0]["failure_kind"], "nonzero_exit")

    def test_coordinated_offset_tamper_across_sealed_stages_is_seal_mismatch(self):
        # agent-harness#243 CR round 4 (codex + Fable, the headline defect this round fixes):
        # a prior round excluded per-stage offsets from the seal digest so a #209 offset tamper
        # would still be caught by the neighbour-bounds check on the nonzero_exit branch. But a
        # COORDINATED two-stage tamper -- extend the failing stage's log_end_offset to N AND
        # move its passing sibling's log_offset to the SAME N -- is internally CONSISTENT with
        # the neighbour-bounds check (both edges agree with each other), so it passed that
        # check while the seal digest stayed unchanged (offsets excluded) -- forging ownership
        # of the widened range and surfacing both stages' output in the failing diagnostic's
        # tail. With offsets now covered by the seal, this coordinated tamper changes the
        # digest and is caught as artifact_seal_mismatch before any diagnostic is built.
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            artifact = self._run(
                repo,
                [
                    [sys.executable, "-c", "import sys; sys.stderr.write('CMD0_FAIL_MARK\\n'); sys.exit(1)"],
                    [sys.executable, "-c", "print('CMD1_SIBLING_SECRET ' * 20)"],
                ],
            )
            payload = json.loads(artifact.read_text(encoding="utf-8"))
            # N must land STRICTLY INSIDE the sibling's own original [log_offset, log_end_offset)
            # region -- an N beyond the log's total length fails closed for an unrelated reason
            # (the overall len(log) bound), not because of the coordinated-tamper bypass this
            # test targets. +100 is comfortably inside the ~400-byte secret-bearing region below.
            cmd1_start = payload["commands"][1]["log_offset"]
            self.assertLess(cmd1_start + 100, payload["commands"][1]["log_end_offset"])
            coordinated_n = cmd1_start + 100
            payload["commands"][0]["log_end_offset"] = coordinated_n  # extend failing stage's end
            payload["commands"][1]["log_offset"] = coordinated_n  # move sibling's start to match
            artifact.write_text(json.dumps(payload), encoding="utf-8")
            v = validate_verification_artifact(artifact)
            self.assertFalse(v.ok)
            self.assertEqual(v.code, "artifact_seal_mismatch")
            self.assertEqual(v.diagnostics, ())  # no diagnostic built at all -- zero leak
            self.assertNotIn("CMD1_SIBLING_SECRET", " ".join(v.findings))
            self.assertNotIn("CMD0_FAIL_MARK", " ".join(v.findings))

    def test_redact_diagnostics_metadata_only_scrubs_secret_shaped_tail(self):
        from phase_loop_runtime.redaction import redact_diagnostics_metadata_only

        diagnostics = [
            {
                "role": "command", "index": 0, "argv": [sys.executable, "-c", "x"],
                "exit_code": 1, "failure_kind": "nonzero_exit",
                "raw_tail": "api_key='AKIAIOSFODNN7EXAMPLEKEY'\ntest failed\n",
                "truncated": False, "diagnostic_status": "present",
            },
            {
                "role": "suite", "index": None, "argv": ["pytest"],
                "exit_code": 1, "failure_kind": "nonzero_exit",
                "raw_tail": "3 failed, 1 passed\n",  # no secret
                "truncated": False, "diagnostic_status": "present",
            },
        ]
        out = redact_diagnostics_metadata_only(diagnostics)
        # Secret-bearing diagnostic -> metadata-only (no raw_tail / argv).
        self.assertTrue(out[0]["redacted"])
        self.assertEqual(out[0]["diagnostic_status"], "redacted")
        self.assertNotIn("raw_tail", out[0])
        self.assertNotIn("argv", out[0])
        self.assertEqual(out[0]["redaction_reason"], "secret_like_value")
        self.assertGreater(out[0]["raw_tail_bytes"], 0)
        # Clean diagnostic passes through untouched.
        self.assertNotIn("redacted", out[1])
        self.assertEqual(out[1]["raw_tail"], "3 failed, 1 passed\n")
        # No forbidden token survives in the serialized output.
        self.assertNotIn("AKIAIOSFODNN7EXAMPLEKEY", json.dumps(out))

    def test_redact_diagnostics_force_all_suppresses_every_tail(self):
        from phase_loop_runtime.redaction import redact_diagnostics_metadata_only

        diagnostics = [{
            "role": "command", "index": 0, "argv": ["x"], "exit_code": 1,
            "failure_kind": "nonzero_exit", "raw_tail": "totally benign output\n",
            "truncated": False, "diagnostic_status": "present",
        }]
        out = redact_diagnostics_metadata_only(diagnostics, force_all=True)
        self.assertTrue(out[0]["redacted"])
        self.assertEqual(out[0]["redaction_reason"], "operator_forced")
        self.assertNotIn("raw_tail", out[0])

    def test_redact_diagnostics_metadata_only_scrubs_double_quoted_secret(self):
        # agent-harness#243 CR (defect 1): the pre-fix matcher tested a json.dumps(...)
        # serialization of the diagnostic. json.dumps backslash-escapes an embedded double
        # quote (api_key="X" -> api_key=\"X\" in the serialized blob), which put the escape
        # backslash between "=" and the quote and broke secret_like_value for a DOUBLE-quoted
        # secret (single-quoted secrets were unaffected, since JSON doesn't escape '). The fix
        # walks raw, unescaped leaf strings instead, so a double-quoted secret must now be
        # caught -- both by the redaction path and by the fatal closeout metadata gate, which
        # share the corrected matcher.
        from phase_loop_runtime.redaction import metadata_redaction_diagnostic, redact_diagnostics_metadata_only

        diagnostics = [
            {
                "role": "command", "index": 0, "argv": [sys.executable, "-c", "x"],
                "exit_code": 1, "failure_kind": "nonzero_exit",
                "raw_tail": 'api_key="AKIAIOSFODNN7EXAMPLEKEY"\ntest failed\n',
                "truncated": False, "diagnostic_status": "present",
            },
        ]
        out = redact_diagnostics_metadata_only(diagnostics)
        self.assertTrue(out[0]["redacted"])
        self.assertEqual(out[0]["diagnostic_status"], "redacted")
        self.assertNotIn("raw_tail", out[0])
        self.assertNotIn("argv", out[0])
        self.assertEqual(out[0]["redaction_reason"], "secret_like_value")
        self.assertNotIn("AKIAIOSFODNN7EXAMPLEKEY", json.dumps(out))
        # The fatal closeout metadata gate must independently catch the SAME unredacted
        # diagnostic (agent-harness#243: both paths reuse the corrected leaf-walk matcher, not
        # a forked/re-implemented pattern parser).
        gate = metadata_redaction_diagnostic({"verification": {"results": [{"diagnostics": diagnostics}]}})
        self.assertIsNotNone(gate)
        assert gate is not None
        self.assertEqual(gate["kind"], "malformed_closeout")

    def test_redact_diagnostics_metadata_only_scrubs_secret_in_nested_argv(self):
        # agent-harness#243 CR: a secret embedded inside a nested argv list ELEMENT (not just
        # a top-level raw_tail string) must be caught -- the leaf-walk must recurse into lists.
        from phase_loop_runtime.redaction import metadata_redaction_diagnostic, redact_diagnostics_metadata_only

        diagnostics = [
            {
                "role": "command", "index": 0,
                "argv": [sys.executable, "-c", "print(1)", '--token="AKIAIOSFODNN7EXAMPLEKEY"'],
                "exit_code": 1, "failure_kind": "nonzero_exit",
                "raw_tail": "ordinary failing output, no secret text here\n",
                "truncated": False, "diagnostic_status": "present",
            },
        ]
        out = redact_diagnostics_metadata_only(diagnostics)
        self.assertTrue(out[0]["redacted"])
        self.assertEqual(out[0]["redaction_reason"], "secret_like_value")
        self.assertNotIn("argv", out[0])
        self.assertNotIn("AKIAIOSFODNN7EXAMPLEKEY", json.dumps(out))
        gate = metadata_redaction_diagnostic({"verification": {"results": [{"diagnostics": diagnostics}]}})
        self.assertIsNotNone(gate)
        assert gate is not None
        self.assertEqual(gate["kind"], "malformed_closeout")

    def test_metadata_redaction_diagnostic_catches_secret_json_embedded_in_closeout_payload(self):
        # agent-harness#243 CR: a double-quoted secret buried deep inside the JSON-shaped
        # closeout payload (as `metadata_redaction_diagnostic` is actually invoked in
        # closeout.py, over the FULL closeout record, not just one diagnostic) must be caught.
        # This mirrors how a redacted diagnostic would reach closeout.py's fatal metadata gate
        # if redaction were ever bypassed.
        from phase_loop_runtime.redaction import metadata_redaction_diagnostic, redact_diagnostics_metadata_only

        closeout_shaped_payload = {
            "schema": "phase_loop_closeout.v1",
            "verification": {
                "status": "blocked",
                "results": [
                    {
                        "code": "nonzero_exit",
                        "diagnostics": [
                            {
                                "role": "command", "index": 0, "argv": [sys.executable, "-c", "x"],
                                "exit_code": 1, "failure_kind": "nonzero_exit",
                                "raw_tail": 'api_key="AKIAIOSFODNN7EXAMPLEKEY"\ntest failed\n',
                                "truncated": False, "diagnostic_status": "present",
                            }
                        ],
                    }
                ],
            },
        }
        gate = metadata_redaction_diagnostic(closeout_shaped_payload)
        self.assertIsNotNone(gate)
        assert gate is not None
        self.assertEqual(gate["kind"], "malformed_closeout")
        # Once redacted, the same payload must clear the gate.
        diagnostics = closeout_shaped_payload["verification"]["results"][0]["diagnostics"]
        closeout_shaped_payload["verification"]["results"][0]["diagnostics"] = redact_diagnostics_metadata_only(diagnostics)
        self.assertIsNone(metadata_redaction_diagnostic(closeout_shaped_payload))

    def test_redact_diagnostics_metadata_only_scrubs_json_struct_secret_in_raw_tail(self):
        # agent-harness#243 CR round 4 (codex + Fable): a failing command that PRINTS ordinary
        # JSON credentials, e.g. ``print(json.dumps({"api_key": "SECRET"}))``, is captured
        # verbatim into ``raw_tail`` as the literal text ``{"api_key":"SECRET"}``. The
        # closing quote on the JSON KEY sits directly between the keyword and the ``:``
        # separator, breaking the (then-)required keyword->separator->value adjacency, so
        # neither redaction nor the fatal gate caught it. Must be redacted-to-metadata-only
        # and independently caught by the fatal closeout gate.
        from phase_loop_runtime.redaction import metadata_redaction_diagnostic, redact_diagnostics_metadata_only

        diagnostics = [
            {
                "role": "command", "index": 0, "argv": [sys.executable, "-c", "x"],
                "exit_code": 1, "failure_kind": "nonzero_exit",
                "raw_tail": '{"api_key":"AKIAIOSFODNN7EXAMPLEKEY"}\n',
                "truncated": False, "diagnostic_status": "present",
            },
        ]
        out = redact_diagnostics_metadata_only(diagnostics)
        self.assertTrue(out[0]["redacted"])
        self.assertEqual(out[0]["diagnostic_status"], "redacted")
        self.assertNotIn("raw_tail", out[0])
        self.assertEqual(out[0]["redaction_reason"], "secret_like_value")
        self.assertNotIn("AKIAIOSFODNN7EXAMPLEKEY", json.dumps(out))
        gate = metadata_redaction_diagnostic({"verification": {"results": [{"diagnostics": diagnostics}]}})
        self.assertIsNotNone(gate)
        assert gate is not None
        self.assertEqual(gate["kind"], "malformed_closeout")

    def test_redact_diagnostics_metadata_only_scrubs_nested_json_struct_secret(self):
        # agent-harness#243 CR round 4: the same JSON-struct blind spot, one level deeper --
        # the secret key/value pair is nested inside another object
        # (``{"outer": {"token": "SECRET"}}``), as printed verbatim by a failing command.
        from phase_loop_runtime.redaction import metadata_redaction_diagnostic, redact_diagnostics_metadata_only

        diagnostics = [
            {
                "role": "command", "index": 0, "argv": [sys.executable, "-c", "x"],
                "exit_code": 1, "failure_kind": "nonzero_exit",
                "raw_tail": '{"outer":{"token":"AKIAIOSFODNN7EXAMPLEKEY"}}\n',
                "truncated": False, "diagnostic_status": "present",
            },
        ]
        out = redact_diagnostics_metadata_only(diagnostics)
        self.assertTrue(out[0]["redacted"])
        self.assertNotIn("raw_tail", out[0])
        self.assertNotIn("AKIAIOSFODNN7EXAMPLEKEY", json.dumps(out))
        gate = metadata_redaction_diagnostic({"verification": {"results": [{"diagnostics": diagnostics}]}})
        self.assertIsNotNone(gate)
        assert gate is not None
        self.assertEqual(gate["kind"], "malformed_closeout")

    def test_redact_diagnostics_metadata_only_scrubs_json_struct_password(self):
        # agent-harness#243 CR round 4: same JSON-struct blind spot with the "password" keyword.
        from phase_loop_runtime.redaction import metadata_redaction_diagnostic, redact_diagnostics_metadata_only

        diagnostics = [
            {
                "role": "command", "index": 0, "argv": [sys.executable, "-c", "x"],
                "exit_code": 1, "failure_kind": "nonzero_exit",
                "raw_tail": '{"password": "AKIAIOSFODNN7EXAMPLEKEY"}\n',
                "truncated": False, "diagnostic_status": "present",
            },
        ]
        out = redact_diagnostics_metadata_only(diagnostics)
        self.assertTrue(out[0]["redacted"])
        self.assertNotIn("raw_tail", out[0])
        self.assertNotIn("AKIAIOSFODNN7EXAMPLEKEY", json.dumps(out))
        gate = metadata_redaction_diagnostic({"verification": {"results": [{"diagnostics": diagnostics}]}})
        self.assertIsNotNone(gate)
        assert gate is not None
        self.assertEqual(gate["kind"], "malformed_closeout")

    def test_metadata_redaction_diagnostic_catches_json_struct_secret_as_nested_mapping(self):
        # agent-harness#243 CR round 4: the JSON-struct blind spot also applies when the
        # secret is a genuine NESTED PYTHON MAPPING inside the closeout payload (not merely
        # literal JSON text inside a raw_tail string) -- e.g. some other closeout field that
        # is itself a dict rather than a pre-serialized string. `_iter_leaf_strings` used to
        # yield a Mapping's key and its scalar value as two SEPARATE leaves, so neither carried
        # the other's keyword context.
        from phase_loop_runtime.redaction import metadata_redaction_diagnostic

        self.assertIsNotNone(metadata_redaction_diagnostic({"api_key": "AKIAIOSFODNN7EXAMPLEKEY"}))
        self.assertIsNotNone(metadata_redaction_diagnostic({"outer": {"token": "AKIAIOSFODNN7EXAMPLEKEY"}}))
        self.assertIsNotNone(metadata_redaction_diagnostic({"password": "AKIAIOSFODNN7EXAMPLEKEY"}))
        # No false positive on an ordinary benign nested mapping.
        self.assertIsNone(metadata_redaction_diagnostic({"outer": {"status": "ok"}}))

    def test_legacy_lookalike_seal_marker_not_final_line_still_validates(self):
        # agent-harness#243 CR (defect 2): the seal is written as the FINAL trailer line of
        # verification.log. An UNSEALED legacy log whose captured command output happens to
        # contain a marker-shaped line (e.g. a test that echoes a fake seal marker) followed
        # by MORE output must still be treated as unsealed (skip the seal check, legacy-
        # compatible) -- not misclassified as sealed from an earlier lookalike line and
        # rejected with a seal mismatch.
        import hashlib as _h

        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "run"
            run_dir.mkdir(parents=True)
            log_bytes = (
                b"test output line 1\n"
                b"verification-artifact-sha256:" + b"d" * 64 + b"\n"
                b"more ordinary test output printed AFTER the lookalike marker line\n"
            )
            (run_dir / "verification.log").write_bytes(log_bytes)
            payload = {
                "schema_version": 1,
                "run_id": "run",
                "phase_alias": "VC",
                "commands": [{"argv": ["true"], "cwd": ".", "exit_code": 0, "duration_s": 0.1, "log_offset": 0}],
                "env_refresh": None,
                "suite": None,
                "started_at": "2026-07-21T00:00:00Z",
                "finished_at": "2026-07-21T00:00:01Z",
                "log_sha256": _h.sha256(log_bytes).hexdigest(),
            }
            (run_dir / "verification.json").write_text(json.dumps(payload), encoding="utf-8")
            v = validate_verification_artifact(run_dir / "verification.json")
            self.assertTrue(v.ok)
            self.assertEqual(v.code, "ok")

    def test_whitespace_padded_final_line_seal_lookalike_treated_as_unsealed(self):
        # agent-harness#243 CR (round 2, cross-vendor): _extract_artifact_seal used to
        # `.strip()` the final line before the anchored exact-match regex, so a final line with
        # incidental leading/trailing SPACE around an otherwise well-formed marker was still
        # accepted as a valid seal. A legacy/externally-built log whose final captured line
        # happens to have surrounding whitespace around a marker-shaped lookalike must be
        # treated as UNSEALED (skip the seal check, still validates) -- not seal-matched (which
        # would then fail closed on a legitimate legacy artifact whose digest was never sealed
        # in the first place).
        import hashlib as _h

        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td) / "run"
            run_dir.mkdir(parents=True)
            log_bytes = (
                b"legacy stage output\n"
                b" verification-artifact-sha256:" + b"e" * 64 + b" \n"
            )
            (run_dir / "verification.log").write_bytes(log_bytes)
            payload = {
                "schema_version": 1,
                "run_id": "run",
                "phase_alias": "VC",
                "commands": [{"argv": ["true"], "cwd": ".", "exit_code": 0, "duration_s": 0.1, "log_offset": 0}],
                "env_refresh": None,
                "suite": None,
                "started_at": "2026-07-21T00:00:00Z",
                "finished_at": "2026-07-21T00:00:01Z",
                "log_sha256": _h.sha256(log_bytes).hexdigest(),
            }
            (run_dir / "verification.json").write_text(json.dumps(payload), encoding="utf-8")
            v = validate_verification_artifact(run_dir / "verification.json")
            # Treated as UNSEALED legacy (whitespace-padded lookalike is not an exact seal
            # match) -> back-compat pass, NOT an artifact_seal_mismatch rejection.
            self.assertTrue(v.ok)
            self.assertEqual(v.code, "ok")

    def test_redact_diagnostics_metadata_only_scrubs_secret_in_dict_key(self):
        # agent-harness#243 CR (round 2, cross-vendor): the leaf-values-only walk dropped dict
        # KEYS entirely, so a secret embedded in a KEY (not a value) -- e.g. an argv-parsing
        # bug that folds `--api-key=X` into a single dict key -- silently passed both the
        # redaction path and the fatal closeout gate. Both must catch it once the walker also
        # tests `str(key)`.
        from phase_loop_runtime.redaction import metadata_redaction_diagnostic, redact_diagnostics_metadata_only

        diagnostics = [
            {
                "role": "command", "index": 0, "argv": [sys.executable],
                "exit_code": 1, "failure_kind": "nonzero_exit",
                "raw_tail": "ordinary failing output, no secret text here\n",
                "truncated": False, "diagnostic_status": "present",
                "extra": {"api_key=AKIAIOSFODNN7EXAMPLEKEY": "safe-looking-value"},
            },
        ]
        out = redact_diagnostics_metadata_only(diagnostics)
        self.assertTrue(out[0]["redacted"])
        self.assertEqual(out[0]["redaction_reason"], "secret_like_value")
        self.assertNotIn("AKIAIOSFODNN7EXAMPLEKEY", json.dumps(out))
        gate = metadata_redaction_diagnostic({"verification": {"results": [{"diagnostics": diagnostics}]}})
        self.assertIsNotNone(gate)
        assert gate is not None
        self.assertEqual(gate["kind"], "malformed_closeout")

    def test_redact_diagnostics_metadata_only_scrubs_numeric_scalar_secret(self):
        # agent-harness#243 CR (round 2, cross-vendor): the leaf-values-only walk yielded only
        # `isinstance(value, str)` leaves, so a non-string scalar (int/float/bool) was silently
        # dropped from the matched corpus -- the old json.dumps(...)-blob approach stringified
        # it in place and could still catch it. Exercise the restored non-string-scalar
        # coverage via an argv element that is a Python int (not a str) sitting adjacent to a
        # `--token` flag -- the split-argv adjacency join (defect 3's fix) is what makes this
        # concretely matchable, and it specifically requires the int leaf to be stringified.
        from phase_loop_runtime.redaction import metadata_redaction_diagnostic, redact_diagnostics_metadata_only

        diagnostics = [
            {
                "role": "command", "index": 0,
                "argv": [sys.executable, "-c", "x", "--token", 123456789012345],
                "exit_code": 1, "failure_kind": "nonzero_exit",
                "raw_tail": "ordinary failing output, no secret text here\n",
                "truncated": False, "diagnostic_status": "present",
            },
        ]
        out = redact_diagnostics_metadata_only(diagnostics)
        self.assertTrue(out[0]["redacted"])
        self.assertEqual(out[0]["redaction_reason"], "secret_like_value")
        self.assertNotIn("123456789012345", json.dumps(out))
        gate = metadata_redaction_diagnostic({"verification": {"results": [{"diagnostics": diagnostics}]}})
        self.assertIsNotNone(gate)
        assert gate is not None
        self.assertEqual(gate["kind"], "malformed_closeout")

    def test_redact_diagnostics_metadata_only_scrubs_split_argv_flag_value_pair(self):
        # agent-harness#243 CR (round 2, cross-vendor, defect 3): a SPLIT argv flag/value pair
        # -- e.g. argv=["tool", "--token", "ABCDEFGHIJKL"] -- puts the keyword and the value in
        # separate list elements, so examining one leaf at a time never sees them contiguous
        # and the old (post-fix, pre-CR2) secret_like_value pattern -- which needs the keyword
        # and value adjacent -- never matched. The traversal now also matches the space-joined
        # concatenation of a list's stringified elements.
        from phase_loop_runtime.redaction import metadata_redaction_diagnostic, redact_diagnostics_metadata_only

        diagnostics = [
            {
                "role": "command", "index": 0,
                "argv": [sys.executable, "-c", "x", "--token", "AKIAIOSFODNN7EXAMPLEKEY"],
                "exit_code": 1, "failure_kind": "nonzero_exit",
                "raw_tail": "ordinary failing output, no secret text here\n",
                "truncated": False, "diagnostic_status": "present",
            },
        ]
        out = redact_diagnostics_metadata_only(diagnostics)
        self.assertTrue(out[0]["redacted"])
        self.assertEqual(out[0]["redaction_reason"], "secret_like_value")
        self.assertNotIn("argv", out[0])
        self.assertNotIn("AKIAIOSFODNN7EXAMPLEKEY", json.dumps(out))
        gate = metadata_redaction_diagnostic({"verification": {"results": [{"diagnostics": diagnostics}]}})
        self.assertIsNotNone(gate)
        assert gate is not None
        self.assertEqual(gate["kind"], "malformed_closeout")


if __name__ == "__main__":
    unittest.main()
