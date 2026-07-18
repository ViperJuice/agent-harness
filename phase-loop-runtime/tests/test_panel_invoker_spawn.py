"""model-routing-v2 P2 — real panel spawn (codex + gemini, fail-closed).

No live frontier CLI is ever called: the single subprocess boundary
`panel_invoker._exec_leg` is stubbed. We assert the status mapping, the
Claude TUI leg, bundle staging, subscription-only env, and reviewer≠author.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime import panel_invoker as pi
from phase_loop_runtime.governed_review import select_reviewer_pool


class ClaudeTuiLegTest(unittest.TestCase):
    def test_claude_leg_uses_tui_sonnet5_max_effort_and_canonical_output_file(self):
        captured = {}

        def fake_tui(*, command, cwd, prompt, output_file, timeout_s, env, mode="review", backstop_s=None):
            captured["command"] = command
            captured["cwd"] = cwd
            captured["prompt"] = prompt
            captured["output_file"] = output_file
            captured["timeout_s"] = timeout_s
            captured["env"] = env
            return 0, "Repo-grounded review.\nAGREE", "claude_tui_file_output", ""

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"ANTHROPIC_API_KEY": "secret"}):
            review_dir = Path(td) / "review"
            out_dir = Path(td) / "out"
            repo_dir = Path(td) / "repo"
            review_dir.mkdir()
            out_dir.mkdir()
            repo_dir.mkdir()
            with (
                patch("phase_loop_runtime.panel_invoker._claude_code_support_status", return_value=(True, "supported")),
                patch("phase_loop_runtime.panel_invoker._under_claude_code", return_value=False),
                patch("phase_loop_runtime.panel_invoker._run_claude_tui_session", side_effect=fake_tui),
            ):
                status, text = pi._exec_claude_tui_leg(
                    review_dir,
                    out_dir,
                    600,
                    "SENTINEL-CLAUDE-ARTIFACT",
                    repo_dir=repo_dir,
                )

        command = captured["command"]
        self.assertEqual(status, "OK")
        self.assertIn("AGREE", text)
        self.assertEqual(captured["cwd"], out_dir)
        self.assertEqual(captured["output_file"], out_dir / "panel-claude.txt")
        self.assertEqual(captured["timeout_s"], 600)
        self.assertNotIn("ANTHROPIC_API_KEY", captured["env"])
        self.assertEqual(command[0], "claude")
        self.assertIn("--ax-screen-reader", command)
        self.assertIn("--safe-mode", command)
        self.assertNotIn("--bg", command)
        self.assertNotIn("-p", command)
        self.assertIn("--model", command)
        # The default claude review leg runs Fable (review-path model), not the
        # implementer claude-sonnet-5. Source of truth: DEFAULT_LEG_MODELS["claude"].
        self.assertEqual(command[command.index("--model") + 1], "claude-fable-5")
        self.assertIn("--effort", command)
        self.assertEqual(command[command.index("--effort") + 1], "max")
        self.assertIn("--permission-mode", command)
        self.assertEqual(command[command.index("--permission-mode") + 1], "default")
        add_dirs = [command[index + 1] for index, value in enumerate(command) if value == "--add-dir"]
        self.assertIn(str(review_dir), add_dirs)
        self.assertIn(str(repo_dir), add_dirs)
        self.assertNotIn(str(Path.cwd()), add_dirs)
        self.assertEqual(command[command.index("--tools") + 1], "Read,Write")
        self.assertEqual(command[command.index("--allowedTools") + 1], "Read,Write")
        self.assertNotIn("SENTINEL-CLAUDE-ARTIFACT", captured["prompt"])
        self.assertIn("review-instructions.md", captured["prompt"])
        self.assertIn("review-bundle.md", captured["prompt"])
        self.assertIn(str(review_dir / "review-instructions.md"), captured["prompt"])
        self.assertIn(str(review_dir / "review-bundle.md"), captured["prompt"])
        self.assertIn("panel-claude.txt", captured["prompt"])
        self.assertIn(str(out_dir / "panel-claude.txt"), captured["prompt"])

    def test_claude_launch_id_parser_accepts_agent_view_background_output(self):
        self.assertEqual(
            pi._claude_agent_session_id("backgrounded · 170a3dd3 · advisor-panel-claude"),
            "170a3dd3",
        )
        self.assertEqual(pi._claude_agent_session_id("  claude attach 170a3dd3    open"), "170a3dd3")

    def test_claude_transcript_helpers_extract_assistant_text(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "session.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps({"message": {"role": "user", "content": "prompt"}}),
                        json.dumps({"message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "..."}]}}),
                        json.dumps({"message": {"role": "assistant", "content": [{"type": "text", "text": "AGREE"}]}}),
                    ]
                ),
                encoding="utf-8",
            )

            self.assertTrue(str(pi._claude_project_dir_for_cwd("/tmp/a_b/review")).endswith("/-tmp-a-b-review"))
            self.assertTrue(
                str(pi._claude_project_dir_for_cwd("/tmp/x-_1_y/review")).endswith("/-tmp-x--1-y-review")
            )
            self.assertEqual(pi._assistant_text_from_jsonl(path), "AGREE")

    def test_claude_below_minimum_version_is_unavailable_without_launch(self):
        with tempfile.TemporaryDirectory() as td:
            review_dir = Path(td) / "review"
            out_dir = Path(td) / "out"
            review_dir.mkdir()
            out_dir.mkdir()
            with (
                patch(
                    "phase_loop_runtime.panel_invoker._claude_code_support_status",
                    return_value=(False, "claude_code_version_below_minimum:2.1.196"),
                ),
                patch("phase_loop_runtime.panel_invoker._run_claude_tui_session") as run_tui,
            ):
                # env={} == a non-Claude host so the support check is reached (CR F4
                # ordered the under-Claude-Code deferral BEFORE the support check).
                status, text = pi._exec_claude_tui_leg(review_dir, out_dir, 600, "bundle", env={})

        self.assertEqual(status, "UNAVAILABLE")
        self.assertIn("below_minimum", text)
        run_tui.assert_not_called()

    def test_claude_tui_timeout_omits_artifact_payload(self):
        def fake_tui(**kwargs):
            return 124, "", "timeout after 777s", ""

        with tempfile.TemporaryDirectory() as td:
            review_dir = Path(td) / "review"
            out_dir = Path(td) / "out"
            review_dir.mkdir()
            out_dir.mkdir()
            with (
                patch("phase_loop_runtime.panel_invoker._claude_code_support_status", return_value=(True, "supported")),
                patch("phase_loop_runtime.panel_invoker._under_claude_code", return_value=False),
                patch("phase_loop_runtime.panel_invoker._run_claude_tui_session", side_effect=fake_tui),
            ):
                status, text = pi._exec_claude_tui_leg(review_dir, out_dir, 777, "SECRET-SENTINEL")

        self.assertEqual(status, "TIMEOUT")
        self.assertIn("777s", text)
        self.assertNotIn("SECRET-SENTINEL", text)

    def test_claude_tui_missing_canonical_file_is_not_success(self):
        def fake_tui(**kwargs):
            return 1, "Salvaged transcript review.\nAGREE", "claude_tui_missing_canonical_output", ""

        with tempfile.TemporaryDirectory() as td:
            review_dir = Path(td) / "review"
            out_dir = Path(td) / "out"
            review_dir.mkdir()
            out_dir.mkdir()
            with (
                patch("phase_loop_runtime.panel_invoker._claude_code_support_status", return_value=(True, "supported")),
                patch("phase_loop_runtime.panel_invoker._under_claude_code", return_value=False),
                patch("phase_loop_runtime.panel_invoker._run_claude_tui_session", side_effect=fake_tui),
            ):
                status, text = pi._exec_claude_tui_leg(review_dir, out_dir, 600, "bundle")

        self.assertEqual(status, "ERROR")
        self.assertIn("AGREE", text)

    def test_default_spawn_claude_is_not_hard_coded_unavailable(self):
        with patch.object(pi, "_exec_claude_tui_leg", return_value=("OK", "Looks good.\nAGREE")) as exec_claude:
            status, text = pi._default_spawn("claude", "bundle")

        self.assertEqual(status, "OK")
        self.assertIn("AGREE", text)
        exec_claude.assert_called_once()


class ClaudeLegDeferredUnderClaudeCodeTest(unittest.TestCase):
    """#92 — under Claude Code / no-tty, the claude leg must NOT spawn a TUI it
    cannot drive. It degrades to UNAVAILABLE with EMPTY text (never the reason as
    text — that would be a `panel_nonconforming` BLOCK), and is never counted as
    an AGREE."""

    def test_headless_degrades_without_spawning_tui(self):
        # env={"CLAUDECODE":"1"} injected through the guard's seam so the assertion
        # is host-portable (not accidentally passing off the ambient env).
        with tempfile.TemporaryDirectory() as td:
            review_dir = Path(td) / "review"
            out_dir = Path(td) / "out"
            review_dir.mkdir()
            out_dir.mkdir()
            with (
                patch("phase_loop_runtime.panel_invoker._claude_code_support_status", return_value=(True, "supported")),
                patch("phase_loop_runtime.panel_invoker._run_claude_tui_session") as run_tui,
            ):
                status, text = pi._exec_claude_tui_leg(
                    review_dir, out_dir, 600, "bundle", env={"CLAUDECODE": "1"}
                )
        self.assertEqual(status, "UNAVAILABLE")
        self.assertEqual(text, "")  # EMPTY text is load-bearing (A4)
        run_tui.assert_not_called()  # no PTY, no deadline wait

    def test_under_claude_code_detection(self):
        self.assertTrue(pi._under_claude_code({"CLAUDECODE": "1"}))
        self.assertTrue(pi._under_claude_code({"CLAUDE_CODE_ENTRYPOINT": "cli"}))
        self.assertFalse(pi._under_claude_code({}))
        self.assertFalse(pi._under_claude_code({"CLAUDECODE": "0"}))

    def test_tui_capable_no_tty_path(self):
        # Not under Claude Code: capability is exactly the tty check.
        self.assertFalse(pi._tui_capable(env={}, isatty=lambda: False))
        self.assertTrue(pi._tui_capable(env={}, isatty=lambda: True))
        # Under Claude Code: never capable, regardless of tty.
        self.assertFalse(pi._tui_capable(env={"CLAUDECODE": "1"}, isatty=lambda: True))

    def test_deferred_leg_is_not_counted_as_agreement(self):
        panel = pi.PanelResult(legs=(
            pi.PanelLegResult(leg="codex", status="OK", text="codex\nAGREE"),
            pi.PanelLegResult(leg="gemini", status="OK", text="gemini\nAGREE"),
            pi.PanelLegResult(leg="claude", status="UNAVAILABLE", text=""),
        ))
        claude = [leg for leg in panel.legs if leg.leg == "claude"][0]
        self.assertFalse(claude.usable)
        self.assertNotIn(claude, panel.usable_legs)
        self.assertEqual([leg.leg for leg in panel.usable_legs], ["codex", "gemini"])


class ClaudeLegNativeAdapterRequestTest(unittest.TestCase):
    """#125 + #183 reconciliation — ``_claude_leg_deferred_reason`` still distinguishes
    "under Claude Code" from the headless / no-tty ``native_adapter_required`` code
    (used by the standalone ``native_agent_leg_request`` affordance builder). But as
    of #183 (owner-confirmed) the runtime NO LONGER defers a headless NON-Claude host:
    ``_run_claude_tui_session`` self-allocates its PTY, so the leg RUNS there.
    ``native_adapter_required`` is now an affordance/fallback, not a runtime defer."""

    def test_reason_code_distinguishes_codex_host_from_claude_code(self):
        # Standalone builder codes (affordance): a non-Claude env → native_adapter_required.
        code, detail = pi._claude_leg_deferred_reason({})
        self.assertEqual(code, "native_adapter_required")
        self.assertIn("native sub-agent adapter", detail)
        self.assertNotIn("under Claude Code", detail)
        # Inside a Claude Code session → the driving session runs the Task Agent.
        code_cc, detail_cc = pi._claude_leg_deferred_reason({"CLAUDECODE": "1"})
        self.assertEqual(code_cc, "under_claude_code")
        self.assertIn("Task tool", detail_cc)

    def test_headless_non_claude_runs_self_pty_tui(self):
        # #183 (owner-confirmed reconciliation): a headless NON-Claude host
        # (CLAUDECODE unset, no controlling terminal) RUNS the self-PTY TUI — it no
        # longer defers as native_adapter_required. The self-allocated PTY in
        # _run_claude_tui_session makes the missing parent terminal irrelevant.
        with tempfile.TemporaryDirectory() as td:
            review_dir = Path(td) / "review"
            out_dir = Path(td) / "out"
            review_dir.mkdir()
            out_dir.mkdir()
            with (
                patch("phase_loop_runtime.panel_invoker._claude_code_support_status", return_value=(True, "supported")),
                patch(
                    "phase_loop_runtime.panel_invoker._run_claude_tui_session",
                    return_value=(0, "Headless review.\nAGREE", "claude_tui_file_output", ""),
                ) as run_tui,
            ):
                status, text = pi._exec_claude_tui_leg(
                    review_dir, out_dir, 600, "bundle",
                    env={"NO_TTY": "1"},  # CLAUDECODE unset ⇒ non-Claude host → RUNS
                )
        self.assertEqual(status, "OK")
        self.assertIn("AGREE", text)
        run_tui.assert_called_once()  # ran the self-PTY session, did NOT defer

    def test_native_agent_leg_request_review_shape(self):
        req = pi.native_agent_leg_request(mode="review", env={})
        self.assertEqual(req.leg, "claude")
        self.assertEqual(req.model, pi.DEFAULT_LEG_MODELS["claude"])
        self.assertEqual(req.reason, "native_adapter_required")
        self.assertTrue(req.verdict_required)
        self.assertIn("AGREE", req.verdict_contract)
        # instructions carry the runtime's review brief (what the driver lacks).
        self.assertEqual(req.instructions, pi._mode_instructions("review"))
        # BYTE-COMPAT (CR F1): a BARE builder call (no seat cognition) serializes to
        # the exact original 8-key shape — the additive optional keys are OMITTED
        # when None, so #125's existing to_dict() consumers are unchanged.
        self.assertEqual(
            sorted(req.to_dict()),
            ["detail", "instructions", "leg", "mode", "model", "reason",
             "verdict_contract", "verdict_required"],
        )

    def test_native_agent_leg_request_extended_shape_when_board_attached(self):
        # ABDNATIVE (#183): when the board supplies the seat cognition, to_dict()
        # ADDS exactly the set (non-None) optional keys — never a null placeholder.
        req = pi.native_agent_leg_request(
            mode="review", env={"CLAUDECODE": "1"}, model="claude-fable-5",
            seat_key="claude:claude-fable-5:max:correctness", effort="max",
            lens="correctness", artifact_ref="/tmp/bundle.md", brief_ref="/tmp/brief.md",
            instructions="CUSTOM BRIEF BODY",
        )
        d = req.to_dict()
        self.assertEqual(
            sorted(d),
            ["artifact_ref", "brief_ref", "detail", "effort", "instructions", "leg",
             "lens", "mode", "model", "reason", "seat_key", "verdict_contract",
             "verdict_required"],
        )
        self.assertEqual(d["seat_key"], "claude:claude-fable-5:max:correctness")
        self.assertEqual(d["effort"], "max")
        self.assertEqual(d["lens"], "correctness")
        self.assertEqual(d["artifact_ref"], "/tmp/bundle.md")
        self.assertEqual(d["brief_ref"], "/tmp/brief.md")
        self.assertEqual(d["instructions"], "CUSTOM BRIEF BODY")  # F5: brief override
        self.assertEqual(req.reason, "under_claude_code")

    def test_native_agent_leg_request_advisory_has_no_verdict(self):
        req = pi.native_agent_leg_request(mode="advisory", env={})
        self.assertFalse(req.verdict_required)
        self.assertIn("no AGREE", req.verdict_contract)
        self.assertIn("recommendation", req.verdict_contract)
        self.assertEqual(req.instructions, pi._mode_instructions("advisory"))

    def test_native_agent_leg_request_reason_tracks_host(self):
        self.assertEqual(
            pi.native_agent_leg_request(env={"CLAUDECODE": "1"}).reason,
            "under_claude_code",
        )
        self.assertEqual(
            pi.native_agent_leg_request(env={}).reason,
            "native_adapter_required",
        )


class StatusMappingTest(unittest.TestCase):
    def _spawn_with(self, rc, review_text, log_text):
        with patch.object(pi, "_exec_leg", return_value=(rc, review_text, log_text)):
            return pi._default_spawn("codex", "bundle")

    def test_ok(self):
        status, text = self._spawn_with(0, "A real review. " * 30 + "\nAGREE", "")
        self.assertEqual(status, "OK")
        self.assertIn("AGREE", text)

    def test_empty(self):
        self.assertEqual(self._spawn_with(0, "", "")[0], "EMPTY")      # truly empty body

    def test_nonconforming_is_degraded(self):
        # Substantial text WITHOUT a terminal verdict is non-conforming → fail-closed
        # (degraded), never a silent pass (advisor-panel reconciliation). The old
        # <=200-byte "empty" heuristic let such a non-review slip through.
        self.assertEqual(self._spawn_with(0, "tiny", "")[0], "DEGRADED")
        self.assertEqual(
            self._spawn_with(0, "I cannot AGREE or DISAGREE without more context", "")[0],
            "DEGRADED",
        )

    def test_terse_verdict_is_ok_not_empty(self):
        # A real but terse block (~35 bytes) carries the structured verdict and must
        # classify `ok`, not `empty` — else a genuine DISAGREE silently downgrades to
        # a non-gating warn (code-review finding #2, verified).
        status, text = self._spawn_with(0, "DISAGREE — the endpoint skips auth", "")
        self.assertEqual(status, "OK")
        self.assertIn("DISAGREE", text)

    def test_degraded_on_auth_signature(self):
        self.assertEqual(self._spawn_with(0, "x" * 300, "error: not logged in; please run codex login")[0], "DEGRADED")

    def test_timeout_rc124(self):
        self.assertEqual(self._spawn_with(124, "", "")[0], "TIMEOUT")

    def test_nonzero_non_auth_error(self):
        self.assertEqual(self._spawn_with(2, "A real review. " * 30 + "\nAGREE", "tool failed")[0], "ERROR")

    def test_exec_exception_degrades(self):
        with patch.object(pi, "_exec_leg", side_effect=RuntimeError("boom")):
            self.assertEqual(pi._default_spawn("gemini", "bundle")[0], "DEGRADED")


class BundleStagingTest(unittest.TestCase):
    def test_bundle_and_instructions_staged_readonly_dir(self):
        captured = {}

        def fake_exec(leg, review_dir, out_dir, timeout_s, artifact, mode="review", model=None, **kwargs):
            captured["bundle"] = (review_dir / "review-bundle.md").read_text(encoding="utf-8")
            captured["instructions_exists"] = (review_dir / "review-instructions.md").exists()
            captured["out_separate"] = out_dir != review_dir
            captured["timeout_s"] = timeout_s
            captured["artifact"] = artifact
            return 0, "x" * 300 + "\nAGREE", ""

        with patch.object(pi, "_exec_leg", side_effect=fake_exec):
            status, _ = pi._default_spawn("gemini", "BUNDLE-CONTENT")
        self.assertEqual(status, "OK")
        self.assertEqual(captured["bundle"], "BUNDLE-CONTENT")
        self.assertTrue(captured["instructions_exists"])
        self.assertTrue(captured["out_separate"])
        self.assertEqual(captured["timeout_s"], pi.panel_leg_timeout_seconds("gemini", "BUNDLE-CONTENT"))
        self.assertEqual(captured["artifact"], "BUNDLE-CONTENT")

    def test_timeout_policy_scales_and_caps(self):
        self.assertEqual(pi.panel_leg_timeout_seconds("codex", "small"), 600)
        self.assertEqual(pi.panel_leg_timeout_seconds("codex", "x" * 1_000_000), 1800)

    def test_codex_command_prompt_references_staged_artifact_file(self):
        captured = {}

        class Completed:
            returncode = 0
            stdout = ""
            stderr = ""

        # The auth preflight still uses subprocess.run — keep it logged-in.
        def fake_auth(cmd, **kwargs):
            return Completed()

        # The leg exec now goes through _run_leg_with_liveness; codex's prompt rides
        # ``input_text`` (stdin "-"), and its verdict is the --output-last-message file.
        def fake_liveness(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            captured["kwargs"] = kwargs
            out_file = Path(cmd[cmd.index("--output-last-message") + 1])
            out_file.write_text("Looks good.\nAGREE", encoding="utf-8")
            return pi._LegRun(0, "", "")

        with tempfile.TemporaryDirectory() as td:
            review_dir = Path(td) / "review"
            out_dir = Path(td) / "out"
            review_dir.mkdir()
            out_dir.mkdir()
            with patch("phase_loop_runtime.panel_invoker.subprocess.run", side_effect=fake_auth), \
                    patch.object(pi, "_run_leg_with_liveness", side_effect=fake_liveness):
                rc, review_text, _ = pi._exec_leg("codex", review_dir, out_dir, 600, "SENTINEL-CODEX-ARTIFACT")

        self.assertEqual(rc, 0)
        self.assertIn("AGREE", review_text)
        self.assertEqual(captured["cmd"][-1], "-")
        # the prompt is fed via the seam's ``input_text`` (the stdin writer thread),
        # never inlining the artifact body — it POINTS at the staged files.
        self.assertNotIn("SENTINEL-CODEX-ARTIFACT", captured["kwargs"]["input_text"])
        self.assertIn("review-instructions.md", captured["kwargs"]["input_text"])
        self.assertIn("review-bundle.md", captured["kwargs"]["input_text"])
        self.assertIn(str(review_dir / "review-instructions.md"), captured["kwargs"]["input_text"])
        self.assertIn(str(review_dir / "review-bundle.md"), captured["kwargs"]["input_text"])

    def test_gemini_command_prompt_references_staged_artifact_file_with_add_dir(self):
        captured = {}

        # gemini has no auth probe; the leg exec now flows through the liveness seam,
        # which reads the verdict from ``_LegRun.stdout``.
        def fake_liveness(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            captured["kwargs"] = kwargs
            return pi._LegRun(0, "Looks good.\nAGREE", "")

        with tempfile.TemporaryDirectory() as td:
            review_dir = Path(td) / "review"
            out_dir = Path(td) / "out"
            review_dir.mkdir()
            out_dir.mkdir()
            with patch.object(pi, "_run_leg_with_liveness", side_effect=fake_liveness):
                rc, review_text, _ = pi._exec_leg("gemini", review_dir, out_dir, 600, "SENTINEL-GEMINI-ARTIFACT")

        self.assertEqual(rc, 0)
        self.assertIn("AGREE", review_text)
        self.assertIn("--add-dir", captured["cmd"])
        self.assertEqual(captured["cmd"][captured["cmd"].index("--add-dir") + 1], str(review_dir))
        # BUGFIX: the prompt is the inline ``-p`` argv value (last arg), NOT the stdin
        # sentinel "-" + stdin feed (agy ``-p -`` ignored stdin → empty prompt). The leg
        # passes NO ``input_text`` to the seam, which then wires the child to DEVNULL.
        prompt_arg = captured["cmd"][-1]
        self.assertEqual(captured["cmd"][captured["cmd"].index("-p") + 1], prompt_arg)
        self.assertNotEqual(prompt_arg, "-")
        self.assertIsNone(captured["kwargs"].get("input_text"))
        # the prompt still POINTS at the staged files, never inlines the artifact body
        self.assertNotIn("SENTINEL-GEMINI-ARTIFACT", prompt_arg)
        self.assertIn("review-instructions.md", prompt_arg)
        self.assertIn("review-bundle.md", prompt_arg)
        self.assertIn(str(review_dir / "review-instructions.md"), prompt_arg)
        self.assertIn(str(review_dir / "review-bundle.md"), prompt_arg)

    def test_timeout_log_mentions_timeout_without_artifact_payload(self):
        with tempfile.TemporaryDirectory() as td:
            review_dir = Path(td) / "review"
            out_dir = Path(td) / "out"
            review_dir.mkdir()
            out_dir.mkdir()
            # An EXPLICIT per-leg override (timeout_s=777) is the HARD deadline, honored
            # as-is — the backstop is raised to _MAX_LEG_TIMEOUT_S only for the input-
            # scaled DEFAULT. So the deadline backstop fires at 777s and the log reports
            # 777s (frozen-contract: timeouts_by_leg is a real per-leg bound).
            with patch.object(pi, "_leg_auth_ok", return_value=(True, "")), \
                    patch.object(
                        pi, "_run_leg_with_liveness",
                        side_effect=subprocess.TimeoutExpired(["codex"], timeout=777),
                    ):
                rc, _, log_text = pi._exec_leg("codex", review_dir, out_dir, 777, "SECRET-SENTINEL")

        self.assertEqual(rc, 124)
        self.assertIn("777s", log_text)
        self.assertNotIn(f"{pi._MAX_LEG_TIMEOUT_S}s", log_text)  # NOT raised to the backstop
        self.assertNotIn("SECRET-SENTINEL", log_text)

    def test_large_artifact_prompt_is_file_reference_with_digest_metadata(self):
        artifact = "HEAD-SENTINEL\n" + ("x" * 200_000) + "\nMIDDLE-SENTINEL\n" + ("y" * 200_000) + "\nTAIL-SENTINEL"
        with tempfile.TemporaryDirectory() as td:
            review_dir = Path(td) / "review"
            prompt = pi._render_leg_prompt(artifact, review_dir)

        self.assertIn("sha256:", prompt)
        self.assertIn("bytes:", prompt)
        self.assertIn("review-bundle.md", prompt)
        self.assertIn(str(review_dir / "review-bundle.md"), prompt)
        self.assertNotIn("HEAD-SENTINEL", prompt)
        self.assertNotIn("MIDDLE-SENTINEL", prompt)
        self.assertNotIn("TAIL-SENTINEL", prompt)
        self.assertLess(len(prompt), 2000)


class SubscriptionAuthTest(unittest.TestCase):
    def test_api_keys_stripped(self):
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "sk-x", "ANTHROPIC_API_KEY": "sk-y", "GEMINI_API_KEY": "g",
            "PATH": os.environ.get("PATH", ""),
        }):
            env = pi._subscription_env()
        for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
            self.assertNotIn(var, env)
        self.assertIn("PATH", env)  # non-key env preserved


class InvokePanelFailClosedTest(unittest.TestCase):
    def test_degraded_and_empty_legs_are_not_usable(self):
        def spawn(leg, artifact):
            return {"codex": ("ok", "Solid review. " * 20 + "\nAGREE"),
                    "gemini": ("degraded", ""),
                    "claude": ("unavailable", "")}[leg]
        panel = pi.invoke_panel("b", ("codex", "gemini", "claude"), spawn=spawn)
        usable = {leg.leg for leg in panel.usable_legs}
        self.assertEqual(usable, {"codex"})  # only the ok leg with text is usable

    def test_real_spawn_receives_repo_dir(self):
        with tempfile.TemporaryDirectory() as td:
            repo_dir = Path(td) / "repo"
            repo_dir.mkdir()
            with patch.object(pi, "_default_spawn", return_value=("OK", "Looks good.\nAGREE")) as spawn:
                panel = pi.invoke_panel("b", ("claude",), repo_dir=repo_dir)

        self.assertEqual(panel.legs[0].status, "OK")
        spawn.assert_called_once_with("claude", "b", repo_dir=repo_dir, mode="review", model=None)


class ReviewerNeqAuthorTest(unittest.TestCase):
    def test_claude_author_reviewed_by_codex_gemini(self):
        pool, degraded = select_reviewer_pool("claude", ("codex", "gemini", "claude"))
        self.assertEqual(set(pool), {"codex", "gemini"})
        self.assertIsNone(degraded)

    def test_codex_author_has_disjoint_reviewer(self):
        pool, _ = select_reviewer_pool("codex", ("codex", "gemini", "claude"))
        self.assertNotIn("codex", pool)
        self.assertIn("gemini", pool)  # a usable disjoint vendor even if claude degrades


def _completed(command, *, stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)


if __name__ == "__main__":
    unittest.main()
