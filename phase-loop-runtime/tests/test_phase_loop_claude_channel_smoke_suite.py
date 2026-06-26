import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock


import pytest
from _dotfiles_tree import dotfiles_tree_present

# TESTDECOUPLE SL-1: this file reads dotfiles fleet paths (absent in the
# extracted agent-harness layout). Skip at MODULE level before any such read so
# collection does not error standalone; the marker keeps it deselected by
# `pytest -m "not dotfiles_integration"` and the conftest run-time hook.
if not dotfiles_tree_present():
    pytest.skip("requires dotfiles tree", allow_module_level=True)

pytestmark = pytest.mark.dotfiles_integration

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "scripts" / "smoke-claude-channel-proof"
LIVE = ROOT / "scripts" / "smoke-phase-loop-live-adapters"
smoke = SourceFileLoader("smoke_claude_channel_proof", str(SMOKE)).load_module()


class ClaudeChannelSmokeSuiteTest(unittest.TestCase):
    def test_dry_run_is_offline_print_free_and_reports_operator_matrix(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            status = smoke.run_dry_run()

        payload = json.loads(stdout.getvalue())
        rendered = json.dumps(payload, sort_keys=True)
        self.assertEqual(status, 0)
        self.assertEqual(payload["status"], "skipped")
        self.assertTrue(payload["metadata_only"])
        self.assertFalse(payload["live_credentials_required"])
        self.assertEqual(payload["channel_ingress_reply"]["route"], "claude_channel")
        self.assertEqual(payload["channel_ingress_reply"]["ack_policy"], "tool_ack_required")
        self.assertIn("done", payload["channel_ingress_reply"]["result_statuses"])
        self.assertEqual(payload["permission_relay"]["audit"], "metadata_only")
        self.assertIn("request_id", payload["permission_relay"]["request_fields"])
        self.assertIn("verdict", payload["permission_relay"]["verdict_fields"])
        self.assertEqual(payload["agent_view_lifecycle"]["route"], "claude_agent_view")
        self.assertIn("blocked", payload["agent_view_lifecycle"]["states"])
        self.assertEqual(payload["print_compatibility"]["route"], "claude_print")
        self.assertTrue(payload["print_compatibility"]["billing_sensitive"])
        self.assertEqual(payload["print_compatibility"]["silent_fallback"], "blocked")
        self.assertIn("server:phase-loop-channel", payload["command"])
        self.assertNotIn("claude -p", rendered)
        self.assertNotIn("--print", rendered)
        self.assertNotIn("bearer", rendered.lower())
        self.assertNotIn("token", rendered.lower())

    def test_permission_relay_is_opt_in_and_uses_token_file_presence_only(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(smoke.run_permission_relay(), 2)

        with tempfile.TemporaryDirectory() as tmpdir, mock.patch.dict(
            os.environ,
            {
                "PHASE_LOOP_ENABLE_CLAUDE_CHANNEL_LIVE_TEST": "1",
                "PHASE_LOOP_CHANNEL_BEARER_TOKEN_FILE": str(Path(tmpdir) / "missing"),
            },
            clear=True,
        ):
            self.assertEqual(smoke.run_permission_relay(), 6)

    def test_no_print_route_guard_rejects_print_like_tokens(self):
        for command in (
            ["claude", "-p"],
            ["claude", "--print"],
            ["claude", "--bare"],
            ["claude -p"],
        ):
            with self.subTest(command=command):
                with self.assertRaisesRegex(RuntimeError, "forbidden Claude print/bare route"):
                    smoke.validate_no_print_route(command)

        smoke.validate_no_print_route(smoke.command_template())

    def test_live_adapter_wrapper_names_harden_operator_matrix(self):
        source = LIVE.read_text(encoding="utf-8")
        for token in (
            "scripts/smoke-claude-channel-proof --dry-run",
            "PHASE_LOOP_ENABLE_CLAUDE_CHANNEL_LIVE_TEST=1",
            "--permission-relay",
            "PHASE_LOOP_CHANNEL_BEARER_TOKEN_FILE",
            "Agent View blocked/completed lifecycle",
            "claude -p print compatibility",
            "PHASE_LOOP_CLAUDE_ROUTE=channel",
            "PHASE_LOOP_CLAUDE_ROUTE=agent_view",
            "PHASE_LOOP_CLAUDE_ROUTE=print",
            "billing-sensitive and must be selected explicitly",
            "PTY/tmux control is manual fallback only",
        ):
            self.assertIn(token, source)


if __name__ == "__main__":
    unittest.main()
