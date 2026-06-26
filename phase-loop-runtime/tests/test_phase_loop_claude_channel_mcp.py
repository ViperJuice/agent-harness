import json
import subprocess
import unittest
from pathlib import Path

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
PLUGIN_ROOT = ROOT / "claude-config" / "plugins" / "phase-loop-channel"
CHANNEL_SCRIPT = PLUGIN_ROOT / "channel" / "phase_loop_channel.ts"
SMOKE_SCRIPT = ROOT / "scripts" / "smoke-claude-channel-proof"


class ClaudeChannelMcpTest(unittest.TestCase):
    def test_plugin_manifest_declares_channel_protocol(self):
        manifest = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))

        self.assertTrue(manifest["capabilities"]["permissionRelay"])
        channels = manifest["components"]["channels"]
        self.assertEqual(channels[0]["protocol"], "experimental.claude/channel")
        self.assertEqual(channels[0]["name"], "phase-loop")
        self.assertEqual(channels[0]["command"], "bun")

    def test_package_uses_mcp_sdk_transport(self):
        package_json = json.loads((PLUGIN_ROOT / "package.json").read_text(encoding="utf-8"))
        source = CHANNEL_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("@modelcontextprotocol/sdk", package_json["dependencies"])
        self.assertIn("StdioServerTransport", source)
        self.assertIn("new Server(", source)
        self.assertIn("await mcp.connect(new StdioServerTransport())", source)

    def test_reply_and_status_schemas_expose_frozen_fields(self):
        source = CHANNEL_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("tools: ['reply', 'status'].map", source)
        for field in ("event_id", "status", "text", "artifacts", "error", "final"):
            self.assertIn(field, source)
        for status in ("received", "working", "blocked", "done", "error"):
            self.assertIn(status, source)

    def test_channel_notification_includes_event_id_metadata(self):
        source = CHANNEL_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("notifications/claude/channel", source)
        self.assertIn('event_id="${event.event_id}"', source)
        self.assertIn("mcp__phase-loop-channel__status", source)
        self.assertIn("function channelMeta", source)
        self.assertIn("Record<string, string>", source)

    def test_permission_relay_notification_is_metadata_only(self):
        source = CHANNEL_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("'claude/channel/permission': {}", source)
        self.assertNotIn("permissionRelay: true", source)
        self.assertIn("/permission/requests", source)
        self.assertIn("function permissionContent", source)
        self.assertIn("function permissionMeta", source)
        for field in ("request_id", "tool_name", "description", "input_preview", "risk_class", "requested_at"):
            self.assertIn(field, source)
        self.assertNotIn("raw_input", source)
        self.assertNotIn("tool_payload", source)

    def test_smoke_dry_run_rejects_print_bare_command_templates(self):
        text = SMOKE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("FORBIDDEN_COMMAND_TOKENS", text)
        self.assertNotIn('"claude", "-p"', text)
        self.assertIn("pty.openpty", text)
        self.assertNotIn("def channel_ack_prompt", text)
        self.assertNotIn("_submit_claude_prompt", text)
        self.assertNotIn('"--disallowedTools"', text)
        self.assertIn("--permission-relay", text)
        self.assertIn("PHASE_LOOP_CHANNEL_BEARER_TOKEN_FILE", text)
        self.assertNotIn('"--print"', json.dumps(json.loads(subprocess.check_output([str(SMOKE_SCRIPT), "--dry-run"], text=True))["command"]))
        self.assertNotIn('"--bare"', json.dumps(json.loads(subprocess.check_output([str(SMOKE_SCRIPT), "--dry-run"], text=True))["command"]))


if __name__ == "__main__":
    unittest.main()
