"""ABDHOME — homebrew backing through the seam (Phase 4).

Proves the four ABDHOME deliverables against the FROZEN ABDFREEZE contracts and
the shared canonical fixtures:

1. **effort plumbing** — ``seat.effort`` reaches each spawned CLI via the frozen
   ``render_seat_invocation`` mapping, INCLUDING the agy leg where effort is baked
   into the model-name string (this was hard-coded before ABDHOME).
2. **byte-equivalence** — the ``default`` board routed THROUGH the seam
   (``invoke_board``) renders each built-3 leg to the exact same argv + env as the
   legacy ``invoke_panel`` path (Exit #2 is vacuous if the seam silently takes the
   effort-absent fallback, so this compares the rendered-through-the-map path).
3. **active env scrubbing (no-silent-key)** — a subscription seat scrubs EVERY
   vendor API-key var from the real subprocess env of every launcher; an api-key
   seat injects ONLY its own vendor's key (behind the board opt-in); an api-key
   seat without the opt-in never launches. Negative tests per launcher + fallback.
4. **native-host-leg-stays-native** — the host leg is never routed through a
   gateway; ``enforce_native_host_leg`` raises on a host-leg omnigent seat, and the
   standalone runner has no host leg (all subprocess, byte-neutral).

Plus the skip-with-warning fail-closed boundary for omnigent/breadth seats.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime import panel_invoker as pi
from phase_loop_runtime.advisor_board import (
    AUTH_API_KEY,
    BACKING_HOMEBREW,
    BACKING_OMNIGENT,
    Board,
    HostContext,
    Seat,
    resolve_seat_env,
)
from phase_loop_runtime.advisor_board import SeatValidationError, resolve_board
from phase_loop_runtime.advisor_board.fixtures import DEFAULT_BOARD, TWO_SAME_VENDOR_BOARD
from phase_loop_runtime.advisor_board.harness_mapping import render_gemini_model


def _capture_run(stdout: str = ""):
    """Capture the LAST ``subprocess.run`` call's cmd/env/timeout (the leg cmd;
    the codex auth preflight also calls run but is overwritten by the main cmd)."""
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["env"] = kwargs.get("env")
        captured["timeout"] = kwargs.get("timeout")

        class _R:
            returncode = 0

        r = _R()
        r.stdout = stdout
        r.stderr = ""
        return r

    return captured, fake_run


class EffortReachesEachCliTests(unittest.TestCase):
    """seat.effort → each CLI's real invocation via the frozen mapping."""

    def test_codex_effort_reaches_reasoning_config(self) -> None:
        captured, fake = _capture_run()
        with patch.object(subprocess, "run", fake), tempfile.TemporaryDirectory() as rd, tempfile.TemporaryDirectory() as od:
            pi._exec_leg("codex", Path(rd), Path(od), effort="high", model="gpt-5.5")
        self.assertIn("model_reasoning_effort=high", captured["cmd"])  # canonical high → high
        captured, fake = _capture_run()
        with patch.object(subprocess, "run", fake), tempfile.TemporaryDirectory() as rd, tempfile.TemporaryDirectory() as od:
            pi._exec_leg("codex", Path(rd), Path(od), effort="max", model="gpt-5.5")
        self.assertIn("model_reasoning_effort=xhigh", captured["cmd"])  # canonical max → xhigh

    def test_agy_effort_is_baked_into_the_model_name(self) -> None:
        # The NEW case: effort was hard-coded in the model string before ABDHOME.
        captured, fake = _capture_run(stdout="AGREE")
        with patch.object(subprocess, "run", fake), tempfile.TemporaryDirectory() as rd, tempfile.TemporaryDirectory() as od:
            pi._exec_leg("gemini", Path(rd), Path(od), effort="low", model="Gemini 3.1 Pro")
        model = captured["cmd"][captured["cmd"].index("--model") + 1]
        self.assertEqual(model, "Gemini 3.1 Pro (Low)")  # effort embedded in the name

    def test_agy_effort_is_idempotent_on_an_already_baked_model(self) -> None:
        captured, fake = _capture_run(stdout="AGREE")
        with patch.object(subprocess, "run", fake), tempfile.TemporaryDirectory() as rd, tempfile.TemporaryDirectory() as od:
            pi._exec_leg("gemini", Path(rd), Path(od), effort="high", model="Gemini 3.1 Pro (High)")
        model = captured["cmd"][captured["cmd"].index("--model") + 1]
        self.assertEqual(model, "Gemini 3.1 Pro (High)")

    def test_claude_effort_reaches_the_effort_flag(self) -> None:
        with tempfile.TemporaryDirectory() as rd:
            cmd = pi._claude_tui_command(Path(rd), Path(rd), "claude-sonnet-5", "high")
        self.assertIn("--effort", cmd)
        self.assertEqual(cmd[cmd.index("--effort") + 1], "high")

    def test_off_host_agent_view_leg_threads_effort(self) -> None:
        # The Agent-View (off-host TUI) leg is one of the named built-3; it is
        # currently dormant (no caller today — the live claude leg uses the local
        # TUI route), but its per-seat effort must plumb through for when Agent-View
        # is re-enabled. Prove the effort reaches the adapter's launch command.
        captured: dict = {}

        class _FakeAdapter:
            def launch_command(self, _prompt, **kwargs):
                captured["effort"] = kwargs.get("effort")
                return ["claude", "--effort", kwargs.get("effort", "")]

        with patch.object(subprocess, "run", side_effect=FileNotFoundError), \
                tempfile.TemporaryDirectory() as rd:
            status, _ = pi._exec_claude_agent_view_attempt(
                _FakeAdapter(), review_dir=Path(rd), timeout_s=600, prompt="p", env={}, effort="low"
            )
        self.assertEqual(captured["effort"], "low")
        self.assertEqual(status, "UNAVAILABLE")  # missing CLI → fail-closed, effort still threaded


class DefaultBoardByteEquivalenceTests(unittest.TestCase):
    """The default board THROUGH the seam renders each built-3 leg to the exact
    same argv + env as today's legacy hard-coded path (rendered through the map,
    not silently via the effort-absent fallback)."""

    def _default_seat(self, harness: str) -> Seat:
        (seat,) = [s for s in DEFAULT_BOARD.seats if s.harness == harness]
        return seat

    def test_codex_seam_argv_and_env_equal_legacy(self) -> None:
        base = dict(os.environ)
        seat = self._default_seat("codex")
        with tempfile.TemporaryDirectory() as rd, tempfile.TemporaryDirectory() as od:
            legacy, fake = _capture_run()
            with patch.object(subprocess, "run", fake):
                pi._exec_leg("codex", Path(rd), Path(od))  # effort/env absent (legacy)
            seam, fake2 = _capture_run()
            with patch.object(subprocess, "run", fake2):
                pi._exec_leg("codex", Path(rd), Path(od), effort=seat.effort, model=seat.model,
                             env=resolve_seat_env(seat, base))
        self.assertEqual(legacy["cmd"], seam["cmd"])
        self.assertEqual(legacy["env"], seam["env"])

    def test_gemini_seam_argv_and_env_equal_legacy(self) -> None:
        base = dict(os.environ)
        seat = self._default_seat("gemini")
        with tempfile.TemporaryDirectory() as rd, tempfile.TemporaryDirectory() as od:
            legacy, fake = _capture_run(stdout="AGREE")
            with patch.object(subprocess, "run", fake):
                pi._exec_leg("gemini", Path(rd), Path(od))
            seam, fake2 = _capture_run(stdout="AGREE")
            with patch.object(subprocess, "run", fake2):
                pi._exec_leg("gemini", Path(rd), Path(od), effort=seat.effort, model=seat.model,
                             env=resolve_seat_env(seat, base))
        self.assertEqual(legacy["cmd"], seam["cmd"])
        self.assertEqual(legacy["env"], seam["env"])

    def test_claude_seam_argv_equals_legacy(self) -> None:
        seat = self._default_seat("claude")
        with tempfile.TemporaryDirectory() as rd:
            legacy = pi._claude_tui_command(Path(rd), Path(rd), None, None)
            seam = pi._claude_tui_command(Path(rd), Path(rd), seat.model, seat.effort)
        self.assertEqual(legacy, seam)

    def test_subscription_seat_env_equals_subscription_env(self) -> None:
        # The env-scrub axis of byte-equivalence: a subscription seat resolves to
        # exactly today's _subscription_env().
        seat = self._default_seat("codex")
        self.assertEqual(resolve_seat_env(seat, dict(os.environ)), pi._subscription_env())

    def test_default_board_seam_calls_default_spawn_with_seat_effort_and_scrubbed_env(self) -> None:
        base = {var: "secret" for var in pi._API_KEY_VARS} | {"PATH": "/usr/bin"}
        with patch.object(pi, "_default_spawn", return_value=("OK", "AGREE")) as ds:
            pi.invoke_board(DEFAULT_BOARD, "artifact", base_env=base)
        by_leg = {c.args[0]: c.kwargs for c in ds.call_args_list}
        self.assertEqual(set(by_leg), {"codex", "gemini", "claude"})
        self.assertEqual(by_leg["codex"]["effort"], "max")
        self.assertEqual(by_leg["gemini"]["effort"], "high")
        self.assertEqual(by_leg["claude"]["effort"], "max")
        for leg, kwargs in by_leg.items():
            for var in pi._API_KEY_VARS:
                self.assertNotIn(var, kwargs["env"], f"{leg} env leaked {var}")
            self.assertEqual(kwargs["env"]["PATH"], "/usr/bin")  # non-key vars preserved


class ActiveEnvScrubbingNegativeTests(unittest.TestCase):
    """No-silent-key by ACTIVE scrubbing — proven at the real subprocess env of
    every launcher, for both the subscription and api-key fallback paths."""

    def _all_keys_base(self) -> dict:
        return {var: "secret" for var in pi._API_KEY_VARS} | {"PATH": "/usr/bin"}

    def _codex_seat(self, auth: str) -> Seat:
        return Seat(model="gpt-5.5", effort="max", harness="codex", auth=auth)

    def _gemini_seat(self, auth: str) -> Seat:
        return Seat(model="Gemini 3.1 Pro", effort="high", harness="gemini", auth=auth)

    def _claude_seat(self, auth: str) -> Seat:
        return Seat(model="claude-sonnet-5", effort="max", harness="claude", auth=auth)

    def test_codex_launcher_subscription_scrubs_every_key(self) -> None:
        env = resolve_seat_env(self._codex_seat("subscription"), self._all_keys_base())
        captured, fake = _capture_run()
        with patch.object(subprocess, "run", fake), tempfile.TemporaryDirectory() as rd, tempfile.TemporaryDirectory() as od:
            pi._exec_leg("codex", Path(rd), Path(od), effort="max", model="gpt-5.5", env=env)
        for var in pi._API_KEY_VARS:
            self.assertNotIn(var, captured["env"])

    def test_codex_launcher_api_key_injects_only_openai(self) -> None:
        env = resolve_seat_env(self._codex_seat(AUTH_API_KEY), self._all_keys_base(),
                               allow_api_key_fallback=True)
        captured, fake = _capture_run()
        with patch.object(subprocess, "run", fake), tempfile.TemporaryDirectory() as rd, tempfile.TemporaryDirectory() as od:
            pi._exec_leg("codex", Path(rd), Path(od), effort="max", model="gpt-5.5", env=env)
        self.assertEqual(captured["env"]["OPENAI_API_KEY"], "secret")
        self.assertNotIn("ANTHROPIC_API_KEY", captured["env"])
        self.assertNotIn("GEMINI_API_KEY", captured["env"])

    def test_gemini_launcher_subscription_scrubs_every_key(self) -> None:
        env = resolve_seat_env(self._gemini_seat("subscription"), self._all_keys_base())
        captured, fake = _capture_run(stdout="AGREE")
        with patch.object(subprocess, "run", fake), tempfile.TemporaryDirectory() as rd, tempfile.TemporaryDirectory() as od:
            pi._exec_leg("gemini", Path(rd), Path(od), effort="high", model="Gemini 3.1 Pro", env=env)
        for var in pi._API_KEY_VARS:
            self.assertNotIn(var, captured["env"])

    def test_gemini_launcher_api_key_injects_only_google_vars(self) -> None:
        env = resolve_seat_env(self._gemini_seat(AUTH_API_KEY), self._all_keys_base(),
                               allow_api_key_fallback=True)
        captured, fake = _capture_run(stdout="AGREE")
        with patch.object(subprocess, "run", fake), tempfile.TemporaryDirectory() as rd, tempfile.TemporaryDirectory() as od:
            pi._exec_leg("gemini", Path(rd), Path(od), effort="high", model="Gemini 3.1 Pro", env=env)
        self.assertEqual(captured["env"]["GEMINI_API_KEY"], "secret")
        self.assertEqual(captured["env"]["GOOGLE_API_KEY"], "secret")
        self.assertNotIn("OPENAI_API_KEY", captured["env"])
        self.assertNotIn("ANTHROPIC_API_KEY", captured["env"])

    def test_claude_launcher_subscription_scrubs_every_key(self) -> None:
        env = resolve_seat_env(self._claude_seat("subscription"), self._all_keys_base())
        captured: dict = {}

        def fake_session(**kwargs):
            captured["env"] = kwargs.get("env")
            return 0, "AGREE", ""

        with patch.object(pi, "_claude_code_support_status", return_value=(True, "")), \
                patch.object(pi, "_run_claude_tui_session", side_effect=fake_session), \
                tempfile.TemporaryDirectory() as rd, tempfile.TemporaryDirectory() as od:
            pi._exec_claude_tui_leg(Path(rd), Path(od), 600, "bundle", effort="max", env=env)
        for var in pi._API_KEY_VARS:
            self.assertNotIn(var, captured["env"])

    def test_claude_launcher_api_key_injects_only_anthropic(self) -> None:
        env = resolve_seat_env(self._claude_seat(AUTH_API_KEY), self._all_keys_base(),
                               allow_api_key_fallback=True)
        captured: dict = {}

        def fake_session(**kwargs):
            captured["env"] = kwargs.get("env")
            return 0, "AGREE", ""

        with patch.object(pi, "_claude_code_support_status", return_value=(True, "")), \
                patch.object(pi, "_run_claude_tui_session", side_effect=fake_session), \
                tempfile.TemporaryDirectory() as rd, tempfile.TemporaryDirectory() as od:
            pi._exec_claude_tui_leg(Path(rd), Path(od), 600, "bundle", effort="max", env=env)
        self.assertEqual(captured["env"]["ANTHROPIC_API_KEY"], "secret")
        self.assertNotIn("OPENAI_API_KEY", captured["env"])
        self.assertNotIn("GEMINI_API_KEY", captured["env"])

    def test_api_key_seat_without_optin_never_launches(self) -> None:
        # resolve_seat_env fail-closes BEFORE any subprocess is built.
        with self.assertRaises(ValueError):
            resolve_seat_env(self._codex_seat(AUTH_API_KEY), self._all_keys_base(),
                             allow_api_key_fallback=False)

    def test_board_rejects_api_key_seat_without_optin(self) -> None:
        # The board-level never-silent-key guard: a board holding an api-key seat
        # cannot even be constructed unless it opts in.
        with self.assertRaises(ValueError):
            Board(name="b", purpose="x", seats=(self._codex_seat(AUTH_API_KEY),))

    def test_invoke_board_api_key_optin_reaches_spawn_with_only_vendor_key(self) -> None:
        board = Board(name="b", purpose="x", seats=(self._codex_seat(AUTH_API_KEY),),
                      allow_api_key_fallback=True)
        with patch.object(pi, "_default_spawn", return_value=("OK", "AGREE")) as ds:
            pi.invoke_board(board, "artifact", base_env=self._all_keys_base())
        env = ds.call_args_list[0].kwargs["env"]
        self.assertEqual(env["OPENAI_API_KEY"], "secret")
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("GEMINI_API_KEY", env)


class NativeHostLegStaysNativeTests(unittest.TestCase):
    """The host leg is never routed through a gateway."""

    def test_standalone_runner_has_no_host_leg(self) -> None:
        # host_harness=None → every leg a subprocess, exactly as today (byte-neutral).
        self.assertIsNone(pi.enforce_native_host_leg(DEFAULT_BOARD, None))
        res = pi.invoke_board(DEFAULT_BOARD, "artifact", host=None,
                              spawn=lambda leg, art: ("OK", f"{leg}\nAGREE"))
        self.assertEqual(tuple(l.leg for l in res.legs), ("codex", "gemini", "claude"))
        self.assertTrue(all(l.status == "OK" for l in res.legs))

    def test_inside_claude_the_claude_seat_is_the_host_leg(self) -> None:
        host = HostContext(host_harness="claude")
        seat = pi.enforce_native_host_leg(DEFAULT_BOARD, host)
        self.assertIsNotNone(seat)
        self.assertEqual(seat.harness, "claude")

    def test_host_leg_omnigent_seat_is_rejected_loud(self) -> None:
        # A host-leg seat may not be gatewayed — hard raise, NOT a skip.
        host = HostContext(host_harness="claude")
        board = Board(name="b", purpose="x", seats=(
            Seat(model="claude-sonnet-5", effort="max", harness="claude",
                 backing=BACKING_OMNIGENT, host_leg=True),
        ))
        with self.assertRaises(ValueError):
            pi.enforce_native_host_leg(board, host)
        with self.assertRaises(ValueError):
            pi.invoke_board(board, "artifact", host=host,
                            spawn=lambda leg, art: ("OK", "AGREE"))

    def test_inside_claude_homebrew_host_leg_spawns_natively(self) -> None:
        host = HostContext(host_harness="claude")
        with patch.object(pi, "_default_spawn", return_value=("OK", "AGREE")) as ds:
            res = pi.invoke_board(DEFAULT_BOARD, "artifact", host=host)
        # The claude host leg spawned through the native homebrew path (never a gateway).
        self.assertIn("claude", {c.args[0] for c in ds.call_args_list})
        self.assertTrue(any(l.leg == "claude" and l.status == "OK" for l in res.legs))


class SkipWithWarningTests(unittest.TestCase):
    """An unavailable lane degrades the seat gracefully without blocking the board."""

    def test_breadth_homebrew_seat_skips_with_warning(self) -> None:
        res = pi.invoke_board(TWO_SAME_VENDOR_BOARD, "artifact",
                              spawn=lambda leg, art: ("OK", f"{leg}\nAGREE"))
        by_leg = {l.leg: l for l in res.legs}
        self.assertEqual(by_leg["codex"].status, "OK")            # built-3 homebrew spawns
        self.assertEqual(by_leg["opencode"].status, "UNAVAILABLE")  # breadth → skip
        self.assertIn("Omnigent-or-skip", by_leg["opencode"].detail)

    def test_omnigent_seat_without_gateway_skips_with_warning(self) -> None:
        board = Board(name="o", purpose="x", seats=(
            Seat(model="gpt-5.5", effort="high", harness="opencode", backing=BACKING_OMNIGENT),
        ))
        res = pi.invoke_board(board, "artifact", gateway_available=False,
                              spawn=lambda leg, art: ("OK", "AGREE"))
        self.assertEqual(res.legs[0].status, "UNAVAILABLE")
        self.assertIn("gateway unavailable", res.legs[0].detail)

    def test_omnigent_seat_with_gateway_is_not_served_by_homebrew(self) -> None:
        # ABDHOME does not implement omnigent transport — even WITH a gateway an
        # omnigent seat is ABDOMNI's job, never a silent homebrew fallback.
        board = Board(name="o", purpose="x", seats=(
            Seat(model="gpt-5.5", effort="high", harness="opencode", backing=BACKING_OMNIGENT),
        ))
        res = pi.invoke_board(board, "artifact", gateway_available=True,
                              spawn=lambda leg, art: ("OK", "AGREE"))
        self.assertEqual(res.legs[0].status, "UNAVAILABLE")
        self.assertIn("ABDOMNI", res.legs[0].detail)

    def test_a_skipped_seat_does_not_block_a_healthy_seat(self) -> None:
        board = Board(name="mix", purpose="x", seats=(
            Seat(model="gpt-5.5", effort="max", harness="codex", backing=BACKING_HOMEBREW),
            Seat(model="gpt-5.5", effort="high", harness="opencode", backing=BACKING_OMNIGENT),
        ))
        res = pi.invoke_board(board, "artifact", spawn=lambda leg, art: ("OK", f"{leg}\nAGREE"))
        self.assertEqual(res.legs[0].status, "OK")
        self.assertEqual(res.legs[1].status, "UNAVAILABLE")


class BoardSeamValidationTests(unittest.TestCase):
    """CR findings 4/6/7 — the ad-hoc/seam board is lane-resolved, matrix-validated
    before spawn, and every result carries a per-seat key."""

    def test_bare_seat_runs_on_its_default_lane_not_skip(self) -> None:
        # finding 6: a bare seat (harness=None) must resolve to its default lane
        # (claude-sonnet-5 -> claude) and RUN, not skip on an empty ('') lane.
        board = Board(name="bare", purpose="x", seats=(Seat(model="claude-sonnet-5", effort="max"),))
        res = pi.invoke_board(board, "artifact", spawn=lambda leg, art: ("OK", f"{leg}\nAGREE"))
        self.assertEqual(res.legs[0].leg, "claude")
        self.assertEqual(res.legs[0].status, "OK")

    def test_invalid_pairing_rejects_before_any_spawn(self) -> None:
        # finding 7: gpt-5.5 on the claude lane must reject, never spawn
        # `claude --model gpt-5.5`.
        spawned: list[str] = []

        def _spawn(leg, art):
            spawned.append(leg)
            return ("OK", "x")

        board = Board(name="bad", purpose="x", seats=(Seat(model="gpt-5.5", effort="max", harness="claude"),))
        with self.assertRaises(SeatValidationError):
            pi.invoke_board(board, "artifact", spawn=_spawn)
        self.assertEqual(spawned, [])  # rejected before spawn

    def test_ad_hoc_resolve_board_invalid_pairing_rejects_before_spawn(self) -> None:
        # finding 7: the same invariant on the resolve_board(seats=...) ad-hoc path.
        board = resolve_board(seats="gpt-5.5:max:claude", matrix=pi.default_matrix())
        spawned: list[str] = []

        def _spawn(leg, art):
            spawned.append(leg)
            return ("OK", "x")

        with self.assertRaises(SeatValidationError):
            pi.invoke_board(board, "artifact", spawn=_spawn)
        self.assertEqual(spawned, [])

    def test_every_result_carries_a_seat_key(self) -> None:
        # finding 4: both OK and skip results carry seat_key.
        res = pi.invoke_board(TWO_SAME_VENDOR_BOARD, "artifact", spawn=lambda leg, art: ("OK", f"{leg}\nAGREE"))
        self.assertTrue(all(l.seat_key for l in res.legs))

    def test_two_same_lane_seats_are_distinguishable_by_seat_key(self) -> None:
        # finding 4: two claude seats differing only by lens share a leg but get
        # distinct seat_keys (the whole point of carrying seat_key through the seam).
        board = Board(name="twolens", purpose="x", seats=(
            Seat(model="claude-sonnet-5", effort="max", harness="claude", lens="adversarial"),
            Seat(model="claude-sonnet-5", effort="max", harness="claude", lens="supportive"),
        ))
        res = pi.invoke_board(board, "artifact", spawn=lambda leg, art: ("OK", "AGREE"))
        self.assertEqual([l.leg for l in res.legs], ["claude", "claude"])
        self.assertNotEqual(res.legs[0].seat_key, res.legs[1].seat_key)


class GeminiEffortEmbedTests(unittest.TestCase):
    """CR finding 5 — only the four canonical effort words are stripped from a
    gemini model name; a real parenthetical (e.g. ``(Preview)``) is preserved."""

    def test_preview_parenthetical_is_not_mistaken_for_effort(self) -> None:
        out = render_gemini_model("Gemini 3.1 Pro (Preview)", "high")
        self.assertEqual(out, "Gemini 3.1 Pro (Preview) (High)")

    def test_effort_word_is_still_stripped_for_idempotency(self) -> None:
        self.assertEqual(render_gemini_model("Gemini 3.1 Pro (High)", "max"), "Gemini 3.1 Pro (Max)")


if __name__ == "__main__":
    unittest.main()
