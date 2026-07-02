"""CS-0.8 — AgentRuntimeProvider seam + homebrew degraded-profile provider.

Covers the provider Protocol/impl in isolation (single-turn buffered replay,
health() degraded-capability reporting, cancel_turn process-kill semantics)
and the panel_invoker adaptation (`_default_spawn_via_provider` routes the
existing `_default_spawn` real-exec boundary through the seam without
changing `_default_spawn`'s call signature or `invoke_panel`'s downstream
normalization).
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from phase_loop_runtime import panel_invoker as pi
from phase_loop_runtime.agent_runtime_provider import (
    AgentRuntimeProvider,
    CreateSessionRequest,
    HomebrewAgentRuntimeProvider,
    ProviderHealth,
    RuntimeProviderError,
    RUNTIME_HOMEBREW,
    SendTurnRequest,
)


def _provider(spawn):
    return HomebrewAgentRuntimeProvider(spawn=spawn)


class InterfaceShapeTest(unittest.TestCase):
    def test_homebrew_provider_satisfies_the_protocol(self):
        provider = _provider(lambda request, register_process=None: ("OK", "hi"))
        self.assertIsInstance(provider, AgentRuntimeProvider)
        for method in (
            "create_session", "send_turn", "read_history", "stream_events",
            "cancel_turn", "close_session", "get_session_info", "health",
        ):
            self.assertTrue(callable(getattr(provider, method)))


class SingleTurnBufferedReplayTest(unittest.TestCase):
    def test_spawn_runs_as_single_turn_session_with_buffered_replay(self):
        provider = _provider(lambda request, register_process=None: ("OK", "AGREE — looks fine"))
        session = provider.create_session(
            CreateSessionRequest(target_harness="claude-code", idempotency_key="k1", title="t")
        )
        self.assertEqual(session.runtime, RUNTIME_HOMEBREW)
        self.assertEqual(session.state, "idle")

        handle = provider.send_turn(
            SendTurnRequest(session_id=session.id, idempotency_key="turn-1", message="do the thing")
        )
        self.assertEqual(handle.state, "completed")

        history = provider.read_history(session.id)
        types = [event.type for event in history.events]
        self.assertEqual(
            types,
            [
                "runtime.session.created",
                "runtime.turn.started",
                "runtime.text.delta",
                "runtime.turn.completed",
            ],
        )
        self.assertTrue(all(e.terminal is (e.type == "runtime.turn.completed") for e in history.events))

        # stream_events (no live stream) replays the identical buffer.
        streamed = list(provider.stream_events(session.id))
        self.assertEqual([e.type for e in streamed], types)
        self.assertEqual([e.sequence for e in streamed], [e.sequence for e in history.events])

        info = provider.get_session_info(session.id)
        self.assertEqual(info.state, "idle")  # back to idle after the single turn completes
        self.assertIsNone(info.active_turn_id)

    def test_failed_spawn_produces_a_failed_terminal_turn(self):
        provider = _provider(lambda request, register_process=None: ("ERROR", "boom"))
        session = provider.create_session(
            CreateSessionRequest(target_harness="codex", idempotency_key="k2", title="t")
        )
        handle = provider.send_turn(
            SendTurnRequest(session_id=session.id, idempotency_key="turn-1", message="m")
        )
        self.assertEqual(handle.state, "failed")
        terminal = [e for e in provider.read_history(session.id).events if e.terminal]
        self.assertEqual(len(terminal), 1)
        self.assertEqual(terminal[0].type, "runtime.turn.failed")

    def test_raising_spawn_fails_the_turn_instead_of_propagating(self):
        def spawn(request, register_process=None):
            raise RuntimeError("leg crashed")

        provider = _provider(spawn)
        session = provider.create_session(
            CreateSessionRequest(target_harness="codex", idempotency_key="k3", title="t")
        )
        handle = provider.send_turn(
            SendTurnRequest(session_id=session.id, idempotency_key="turn-1", message="m")
        )
        self.assertEqual(handle.state, "failed")

    def test_concurrent_turn_on_the_same_session_is_rejected(self):
        import threading

        release = threading.Event()
        started = threading.Event()

        def spawn(request, register_process=None):
            started.set()
            release.wait(timeout=5)
            return "OK", "x"

        provider = _provider(spawn)
        session = provider.create_session(
            CreateSessionRequest(target_harness="codex", idempotency_key="k4", title="t")
        )

        first = threading.Thread(
            target=provider.send_turn,
            args=(SendTurnRequest(session_id=session.id, idempotency_key="turn-1", message="m"),),
        )
        first.start()
        started.wait(timeout=5)
        try:
            with self.assertRaises(RuntimeProviderError):
                provider.send_turn(
                    SendTurnRequest(session_id=session.id, idempotency_key="turn-2", message="m2")
                )
        finally:
            release.set()
            first.join(timeout=5)

    def test_send_turn_is_idempotent_on_repeated_key(self):
        calls = {"n": 0}

        def spawn(request, register_process=None):
            calls["n"] += 1
            return "OK", "x"

        provider = _provider(spawn)
        session = provider.create_session(
            CreateSessionRequest(target_harness="codex", idempotency_key="k5", title="t")
        )
        h1 = provider.send_turn(SendTurnRequest(session_id=session.id, idempotency_key="dup", message="m"))
        h2 = provider.send_turn(SendTurnRequest(session_id=session.id, idempotency_key="dup", message="m"))
        self.assertIs(h1, h2)
        self.assertEqual(calls["n"], 1)

    def test_unknown_session_raises_provider_error(self):
        provider = _provider(lambda request, register_process=None: ("OK", "x"))
        with self.assertRaises(RuntimeProviderError):
            provider.read_history("no-such-session")


class HealthDegradedCapsTest(unittest.TestCase):
    def test_health_reports_homebrew_and_unsupported_capabilities(self):
        provider = _provider(lambda request, register_process=None: ("OK", "x"))
        health = provider.health()
        self.assertIsInstance(health, ProviderHealth)
        self.assertEqual(health.runtime, RUNTIME_HOMEBREW)
        self.assertTrue(health.available)
        self.assertIn("live_event_streaming", health.unsupported_capabilities)
        self.assertIn("mid_turn_cancellation_of_synchronous_spawns", health.unsupported_capabilities)
        self.assertEqual(health.active_sessions, 0)

    def test_health_counts_open_sessions(self):
        provider = _provider(lambda request, register_process=None: ("OK", "x"))
        session = provider.create_session(
            CreateSessionRequest(target_harness="codex", idempotency_key="k6", title="t")
        )
        self.assertEqual(provider.health().active_sessions, 1)
        provider.close_session(session.id)
        self.assertEqual(provider.health().active_sessions, 0)


class CancelTurnTerminatesTest(unittest.TestCase):
    def test_cancel_turn_kills_a_registered_live_process(self):
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        try:
            def spawn(request, register_process=None):
                register_process(proc.pid)
                # Simulate a still-running turn: return only after the caller has
                # had a chance to cancel it. In the homebrew (synchronous) model
                # send_turn blocks until spawn returns, so we cancel from inside
                # the spawn itself to exercise the kill path deterministically.
                deadline = time.monotonic() + 5
                while proc.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                    if proc.poll() is not None:
                        break
                return "OK", "n/a"

            provider = _provider(lambda request, register_process=None: spawn(request, register_process))
            session = provider.create_session(
                CreateSessionRequest(target_harness="codex", idempotency_key="k7", title="t")
            )

            # Cancel concurrently with the in-flight (blocking) send_turn.
            import threading

            def canceller():
                time.sleep(0.1)
                record = provider._sessions[session.id]
                turn_id = next(iter(record.live_pids), None)
                deadline = time.monotonic() + 2
                while turn_id is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                    turn_id = next(iter(record.live_pids), None)
                if turn_id is not None:
                    handle = record.turns[turn_id]
                    provider.cancel_turn(handle)

            t = threading.Thread(target=canceller)
            t.start()
            provider.send_turn(
                SendTurnRequest(session_id=session.id, idempotency_key="turn-1", message="m")
            )
            t.join(timeout=5)

            proc.wait(timeout=5)
            self.assertIsNotNone(proc.returncode)
            self.assertNotEqual(proc.returncode, 0)  # terminated, not a clean exit
        finally:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    proc.kill()

    def test_cancel_turn_on_an_already_completed_turn_is_idempotent(self):
        provider = _provider(lambda request, register_process=None: ("OK", "x"))
        session = provider.create_session(
            CreateSessionRequest(target_harness="codex", idempotency_key="k8", title="t")
        )
        handle = provider.send_turn(
            SendTurnRequest(session_id=session.id, idempotency_key="turn-1", message="m")
        )
        self.assertEqual(handle.state, "completed")
        result = provider.cancel_turn(handle)  # nothing live to kill — no-op success
        self.assertEqual(result.state, "completed")

    def test_cancel_turn_unknown_turn_raises(self):
        provider = _provider(lambda request, register_process=None: ("OK", "x"))
        session = provider.create_session(
            CreateSessionRequest(target_harness="codex", idempotency_key="k9", title="t")
        )
        from phase_loop_runtime.agent_runtime_provider import TurnHandle

        bogus = TurnHandle(
            session_id=session.id, turn_id="nope", idempotency_key="x",
            state="running", created_at="now", updated_at="now",
        )
        with self.assertRaises(RuntimeProviderError):
            provider.cancel_turn(bogus)


class PanelInvokerAdaptationTest(unittest.TestCase):
    """The seam adaptation: invoke_panel's default (spawn=None) path now
    routes through HomebrewAgentRuntimeProvider, but `_default_spawn` keeps its
    exact call signature/single-call semantics and invoke_panel's fail-closed
    status normalization is untouched — these are the "behavior unchanged"
    regression guards.
    """

    def test_default_spawn_via_provider_calls_default_spawn_once_same_signature(self):
        with patch.object(pi, "_default_spawn", return_value=("OK", "AGREE")) as spawn:
            status, text = pi._default_spawn_via_provider(
                "claude", "bundle", repo_dir="/tmp/repo", mode="review", model="m1"
            )
        spawn.assert_called_once_with("claude", "bundle", repo_dir="/tmp/repo", mode="review", model="m1")
        self.assertEqual((status, text), ("OK", "AGREE"))

    def test_invoke_panel_default_path_routes_through_provider_unchanged_result(self):
        with patch.object(pi, "_default_spawn", return_value=("OK", "Looks good.\nAGREE")) as spawn:
            panel = pi.invoke_panel("b", ("claude",), repo_dir="/tmp/repo")
        spawn.assert_called_once_with("claude", "b", repo_dir="/tmp/repo", mode="review", model=None)
        self.assertEqual(panel.legs[0].status, "OK")
        self.assertTrue(panel.legs[0].usable)

    def test_invoke_panel_default_path_empty_text_still_normalizes_to_empty(self):
        with patch.object(pi, "_default_spawn", return_value=("OK", "   ")):
            panel = pi.invoke_panel("b", ("codex",))
        # invoke_panel's OK+empty-text -> EMPTY rule still applies downstream of
        # the provider transport, unchanged.
        self.assertEqual(panel.legs[0].status, "EMPTY")
        self.assertFalse(panel.legs[0].usable)

    def test_invoke_panel_default_path_error_status_propagates(self):
        with patch.object(pi, "_default_spawn", return_value=("TIMEOUT", "")):
            panel = pi.invoke_panel("b", ("gemini",))
        self.assertEqual(panel.legs[0].status, "TIMEOUT")


if __name__ == "__main__":
    unittest.main()
