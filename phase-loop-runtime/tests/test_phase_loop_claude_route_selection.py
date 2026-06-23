import json
import os
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.claude_channel_sidecar import ChannelSidecar, build_server
from phase_loop_runtime.claude_agent_view import AgentViewLifecycleResult
from phase_loop_runtime.launcher import (
    build_launch_request,
    build_launch_spec,
    launch_with_spec,
    resolve_claude_route,
)
from phase_loop_runtime.profiles import resolve_profile_for_executor
from phase_loop_runtime.prompts import build_prompt


class ClaudeRouteSelectionTest(unittest.TestCase):
    def _request(self, repo: Path, *, launch_timeout_seconds: float | None = None):
        roadmap = repo / "specs" / "phase-plans-v1.md"
        roadmap.parent.mkdir(parents=True, exist_ok=True)
        roadmap.write_text("### Phase 1 - Route (ROUTE)\n", encoding="utf-8")
        bundle = build_prompt("execute", roadmap, phase="ROUTE", harness_target="claude")
        return build_launch_request(
            executor="claude",
            action="execute",
            repo=repo,
            roadmap=roadmap,
            phase="ROUTE",
            plan=None,
            model_selection=resolve_profile_for_executor(action="execute", executor="claude"),
            prompt_bundle=bundle,
            json_output=False,
            bypass_approvals=False,
            launch_timeout_seconds=launch_timeout_seconds,
        )

    def test_route_parser_accepts_aliases_and_rejects_unknown_literals(self):
        self.assertEqual(resolve_claude_route("channel").route, "claude_channel")
        self.assertEqual(resolve_claude_route("claude-channel").route, "claude_channel")
        self.assertEqual(resolve_claude_route("agent_view").route, "claude_agent_view")
        self.assertEqual(resolve_claude_route("print").route, "claude_print")
        # DFCHROUTE: empty/unset non-CI route now defaults to Channel (was print).
        self.assertEqual(resolve_claude_route("", env={}).route, "claude_channel")
        self.assertEqual(resolve_claude_route("tui").error, "unsupported Claude route `tui`")

    def test_route_default_contract_post_flip(self):
        # IF-0-DFCHDEFAULT-1 + IF-0-DFCHROUTE-1 — the route-default contract AFTER the
        # DFCHROUTE flip: unset interactive route -> Channel; CI must select a route
        # explicitly (blocks otherwise); explicit print is billing-sensitive
        # compatibility; invalid blocks. (Pre-flip this pinned unset->claude_print;
        # DFCHROUTE inverts it to the v47-validated Channel default.)
        unset = resolve_claude_route(None, env={})
        self.assertEqual(unset.route, "claude_channel")
        self.assertEqual(unset.reason, "default_channel")
        self.assertIsNone(unset.error)
        # CI without an explicit route BLOCKS (actionable error) — never a silent
        # billing-sensitive print default.
        ci_unset = resolve_claude_route(None, env={"CI": "true"})
        self.assertEqual(ci_unset.reason, "ci_requires_explicit_route")
        self.assertIsNotNone(ci_unset.error)
        self.assertIn("explicit", ci_unset.error.lower())
        # Explicit print: deliberate billing-sensitive compatibility, distinct reason
        # from the default, and it RECORDS a billing-sensitive warning.
        explicit_print = resolve_claude_route("print", env={})
        self.assertEqual(explicit_print.route, "claude_print")
        self.assertEqual(explicit_print.reason, "explicit_print_compatibility")
        self.assertNotEqual(explicit_print.reason, unset.reason)
        self.assertTrue(explicit_print.warnings)
        self.assertTrue(any("billing" in w.lower() for w in explicit_print.warnings))
        # Invalid routes carry an error + reason `invalid_route` — not a silent print.
        invalid = resolve_claude_route("tui", env={})
        self.assertIsNotNone(invalid.error)
        self.assertEqual(invalid.reason, "invalid_route")
        # Explicit Channel and Agent View resolve to their own routes.
        self.assertEqual(resolve_claude_route("channel", env={}).route, "claude_channel")
        self.assertEqual(resolve_claude_route("agent_view", env={}).route, "claude_agent_view")

    def test_dfchroute_unset_defaults_to_channel_and_clean_blocks_without_session(self):
        # IF-0-DFCHROUTE-1 composition: an unset non-CI route resolves to Channel,
        # and with no session reduces to the DFCHPREFLIGHT clean block — never a
        # claude -p run. (The recoverable failure mode on a non-ready machine.)
        clear = {
            "PHASE_LOOP_CLAUDE_ROUTE": "",
            "CI": "",
            "PHASE_LOOP_CHANNEL_SESSION_ID": "",
            "PHASE_LOOP_CLAUDE_CHANNEL_SESSION_ID": "",
        }
        with patch.dict(os.environ, clear, clear=False):
            spec = build_launch_spec(self._request(Path("/tmp/repo")))
        self.assertFalse(spec.available)
        self.assertEqual(spec.claude_route, "claude_channel")
        self.assertEqual(spec.claude_route_reason, "default_channel")
        for tok in ("-p", "--print", "--bare", "--output-format"):
            self.assertNotIn(tok, spec.command)

    def test_dfchroute_ci_unset_blocks_without_print_command(self):
        # IF-0-DFCHROUTE-1: CI + unset route blocks with an actionable message and
        # builds NO claude -p command.
        with patch.dict(os.environ, {"PHASE_LOOP_CLAUDE_ROUTE": "", "CI": "true"}, clear=False):
            spec = build_launch_spec(self._request(Path("/tmp/repo")))
        self.assertFalse(spec.available)
        for tok in ("-p", "--print", "--bare", "--output-format"):
            self.assertNotIn(tok, spec.command)
        self.assertIn("explicit", (spec.reason or "").lower())

    def test_dfchroute_channel_and_agent_view_specs_have_no_print_tokens(self):
        # IF-0-DFCHROUTE-1 / criterion 5: Channel and Agent View specs never contain
        # -p / --print / --bare.
        with patch.dict(
            os.environ,
            {
                "PHASE_LOOP_CLAUDE_ROUTE": "channel",
                "PHASE_LOOP_CHANNEL_SESSION_ID": "s1",
                "PHASE_LOOP_CLAUDE_CHANNEL_URL": "http://127.0.0.1:8765",
            },
            clear=False,
        ):
            channel = build_launch_spec(self._request(Path("/tmp/repo")))
        with patch.dict(os.environ, {"PHASE_LOOP_CLAUDE_ROUTE": "agent_view"}, clear=False):
            agent_view = build_launch_spec(self._request(Path("/tmp/repo")))
        for spec in (channel, agent_view):
            self.assertTrue(spec.available)
            for tok in ("-p", "--print", "--bare"):
                self.assertNotIn(tok, spec.command)

    def test_print_route_keeps_existing_claude_print_command(self):
        with patch.dict(os.environ, {"PHASE_LOOP_CLAUDE_ROUTE": "print"}, clear=False):
            spec = build_launch_spec(self._request(Path("/tmp/repo")))

        self.assertTrue(spec.available)
        self.assertEqual(spec.claude_route, "claude_print")
        self.assertIn("-p", spec.command)
        self.assertIn("--output-format", spec.command)
        self.assertEqual(spec.claude_route_reason, "explicit_print_compatibility")

    def test_channel_route_uses_sidecar_command_metadata_and_requires_session_id(self):
        with patch.dict(os.environ, {"PHASE_LOOP_CLAUDE_ROUTE": "channel"}, clear=False):
            missing = build_launch_spec(self._request(Path("/tmp/repo")))

        self.assertFalse(missing.available)
        self.assertEqual(missing.claude_route, "claude_channel")
        self.assertNotIn("-p", missing.command)
        self.assertIn("requires PHASE_LOOP_CHANNEL_SESSION_ID", missing.reason)

        with patch.dict(
            os.environ,
            {
                "PHASE_LOOP_CLAUDE_ROUTE": "channel",
                "PHASE_LOOP_CHANNEL_SESSION_ID": "session-route",
                "PHASE_LOOP_CLAUDE_CHANNEL_URL": "http://127.0.0.1:8765",
            },
            clear=False,
        ):
            spec = build_launch_spec(self._request(Path("/tmp/repo")))

        self.assertTrue(spec.available)
        self.assertEqual(spec.claude_route, "claude_channel")
        self.assertEqual(spec.claude_channel_session_id, "session-route")
        self.assertEqual(spec.command[:2], ["claude-channel", "send"])
        self.assertNotIn("-p", spec.command)

    def test_print_spec_records_billing_warning(self):
        # IF-0-DFCHROUTE-1: explicit print must RECORD a billing-sensitive warning
        # that reaches the launch spec (and thus the launch event), not just live on
        # the route selection. (Guards against the warning being dead code.)
        with patch.dict(os.environ, {"PHASE_LOOP_CLAUDE_ROUTE": "print"}, clear=False):
            spec = build_launch_spec(self._request(Path("/tmp/repo")))
        self.assertTrue(spec.claude_route_warnings)
        self.assertTrue(any("billing" in w.lower() for w in spec.claude_route_warnings))
        self.assertTrue(spec.to_json()["claude_route_warnings"])

    def test_channel_preflight_prereqs_block_without_print_command(self):
        # IF-0-DFCHPREFLIGHT-1: each missing/blocked Channel prerequisite reduces to
        # a metadata-only route blocker at BUILD time — never a claude -p command.
        cases = {
            "missing_session_id": {"PHASE_LOOP_CLAUDE_ROUTE": "channel"},
            "non_loopback_sidecar": {
                "PHASE_LOOP_CLAUDE_ROUTE": "channel",
                "PHASE_LOOP_CHANNEL_SESSION_ID": "s1",
                "PHASE_LOOP_CLAUDE_CHANNEL_URL": "http://10.0.0.9:8765",
            },
            "https_non_loopback_transport": {
                "PHASE_LOOP_CLAUDE_ROUTE": "channel",
                "PHASE_LOOP_CHANNEL_SESSION_ID": "s1",
                "PHASE_LOOP_CLAUDE_CHANNEL_URL": "https://127.0.0.1:8765",  # https is not the loopback-http transport
            },
            "explicitly_blank_url": {
                "PHASE_LOOP_CLAUDE_ROUTE": "channel",
                "PHASE_LOOP_CHANNEL_SESSION_ID": "s1",
                "PHASE_LOOP_CLAUDE_CHANNEL_URL": "",  # blank env (operator typo) must block, not silently use the default
            },
        }
        for name, env in cases.items():
            with self.subTest(case=name):
                # Clear inherited channel env so each case is isolated.
                base = {
                    k: ""
                    for k in (
                        "PHASE_LOOP_CHANNEL_SESSION_ID",
                        "PHASE_LOOP_CLAUDE_CHANNEL_SESSION_ID",
                        "PHASE_LOOP_CLAUDE_CHANNEL_URL",
                    )
                }
                with patch.dict(os.environ, {**base, **env}, clear=False):
                    spec = build_launch_spec(self._request(Path("/tmp/repo")))
                self.assertFalse(spec.available, name)
                self.assertEqual(spec.claude_route, "claude_channel", name)
                self.assertNotIn("-p", spec.command, name)
                self.assertNotIn("--output-format", spec.command, name)
                self.assertNotIn("--print", spec.command, name)
                self.assertIsInstance(spec.reason, str)

    def test_channel_loopback_variants_are_accepted(self):
        # 127.0.0.0/8 (incl. non-.1) / localhost are accepted — the block targets
        # non-loopback / non-http transports, not valid loopback sidecars.
        for url in ("http://127.0.0.1:8765", "http://localhost:8765", "http://127.0.0.2:8765"):
            with self.subTest(url=url):
                with patch.dict(
                    os.environ,
                    {
                        "PHASE_LOOP_CLAUDE_ROUTE": "channel",
                        "PHASE_LOOP_CHANNEL_SESSION_ID": "s1",
                        "PHASE_LOOP_CLAUDE_CHANNEL_URL": url,
                    },
                    clear=False,
                ):
                    spec = build_launch_spec(self._request(Path("/tmp/repo")))
                self.assertTrue(spec.available, url)
                self.assertEqual(spec.command[:2], ["claude-channel", "send"])
                self.assertNotIn("-p", spec.command)

    def test_invalid_route_blocks_without_print_fallback(self):
        for route in ("tui",):
            with self.subTest(route=route), patch.dict(os.environ, {"PHASE_LOOP_CLAUDE_ROUTE": route}, clear=False):
                spec = build_launch_spec(self._request(Path("/tmp/repo")))

            self.assertFalse(spec.available)
            self.assertNotIn("-p", spec.command)
            self.assertNotIn("--output-format", spec.command)

    def test_agent_view_route_uses_background_command_metadata_without_print_fallback(self):
        with patch.dict(os.environ, {"PHASE_LOOP_CLAUDE_ROUTE": "agent_view"}, clear=False):
            spec = build_launch_spec(self._request(Path("/tmp/repo")))

        self.assertTrue(spec.available)
        self.assertEqual(spec.claude_route, "claude_agent_view")
        self.assertEqual(spec.command[:2], ["claude", "--bg"])
        self.assertNotIn("-p", spec.command)
        self.assertNotIn("--output-format", spec.command)

    def test_agent_view_launch_returns_async_route_result(self):
        class FakeAgentViewAdapter:
            def launch_background(self, prompt, *, cwd, **kwargs):
                return AgentViewLifecycleResult(
                    session_id="agent-1",
                    state="running",
                    cwd=str(cwd),
                    logs_ref="claude logs agent-1",
                    started_at="2026-06-19T12:00:00Z",
                    completed_at=None,
                    stop_result=None,
                    auth_posture="subscription_local",
                    billing_posture="subscription_included",
                )

        with patch.dict(os.environ, {"PHASE_LOOP_CLAUDE_ROUTE": "agent_view"}, clear=False):
            spec = build_launch_spec(self._request(Path("/tmp/repo")))
        with patch("phase_loop_runtime.launcher.ClaudeAgentViewAdapter", return_value=FakeAgentViewAdapter()):
            result = launch_with_spec(spec)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.claude_route, "claude_agent_view")
        self.assertEqual(result.claude_route_result["status"], "working")
        self.assertEqual(result.claude_route_result["session_id"], "agent-1")
        self.assertEqual(result.claude_route_result["artifacts"][0]["logs_ref"], "claude logs agent-1")
        self.assertNotIn("-p", result.command)
        rendered = json.dumps(result.event_metadata(), sort_keys=True)
        self.assertNotIn("raw transcript", rendered)
        self.assertNotIn("Bearer", rendered)

    def test_channel_launch_preflight_failure_is_blocked_not_print_fallback(self):
        sidecar = ChannelSidecar()
        sidecar.register_session(
            {
                "session_id": "session-blocked",
                "adapter": "claude_channel",
                "cwd": "/repo",
                "state": "blocked",
                "channel_health": "blocked",
            }
        )
        server = build_server("127.0.0.1", 0, sidecar=sidecar)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.dict(
                os.environ,
                {
                    "PHASE_LOOP_CLAUDE_ROUTE": "channel",
                    "PHASE_LOOP_CHANNEL_SESSION_ID": "session-blocked",
                    "PHASE_LOOP_CLAUDE_CHANNEL_URL": f"http://127.0.0.1:{server.server_port}",
                },
                clear=False,
            ):
                spec = build_launch_spec(self._request(Path("/tmp/repo")))
                result = launch_with_spec(spec)
        finally:
            server.shutdown()
            server.server_close()

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.claude_route, "claude_channel")
        self.assertEqual(result.claude_route_result["status"], "blocked")
        self.assertNotIn("-p", result.command)
        rendered = json.dumps(result.event_metadata(), sort_keys=True)
        self.assertIn("claude_channel", rendered)
        self.assertNotIn("Bearer", rendered)

    def test_channel_launch_returns_route_result_from_sidecar_ack(self):
        sidecar = ChannelSidecar()
        sidecar.register_session(
            {
                "session_id": "session-live",
                "adapter": "claude_channel",
                "cwd": "/repo",
                "state": "ready",
                "channel_health": "ready",
                "auth_posture": {"status": "authenticated", "method": "subscription"},
            }
        )
        server = build_server("127.0.0.1", 0, sidecar=sidecar)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def reply() -> None:
            event_id = None
            for _ in range(50):
                events = sidecar.list_events("session-live")
                if events:
                    event_id = events[0]["event_id"]
                    break
                time.sleep(0.01)
            if event_id:
                sidecar.record_reply({"event_id": event_id, "status": "done", "text": "complete", "final": True})

        try:
            with patch.dict(
                os.environ,
                {
                    "PHASE_LOOP_CLAUDE_ROUTE": "channel",
                    "PHASE_LOOP_CHANNEL_SESSION_ID": "session-live",
                    "PHASE_LOOP_CLAUDE_CHANNEL_URL": f"http://127.0.0.1:{server.server_port}",
                },
                clear=False,
            ):
                spec = build_launch_spec(self._request(Path("/tmp/repo")))
                reply_thread = threading.Thread(target=reply, daemon=True)
                reply_thread.start()
                result = launch_with_spec(spec)
                reply_thread.join(timeout=1)
        finally:
            server.shutdown()
            server.server_close()

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.output, "complete")
        self.assertEqual(result.claude_route_result["route"], "claude_channel")
        self.assertEqual(result.claude_route_result["billing_posture"], "subscription_included")


if __name__ == "__main__":
    unittest.main()
