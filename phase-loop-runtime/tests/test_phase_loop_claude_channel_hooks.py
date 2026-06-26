import json
import unittest
from pathlib import Path

from phase_loop_runtime.claude_channel_sidecar import ChannelSidecar


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
PLUGIN = ROOT / "claude-config" / "plugins" / "phase-loop-channel" / "channel" / "phase_loop_channel.ts"
NOTIFY = ROOT / "claude-config" / "notify.sh"


class ClaudeChannelHooksTest(unittest.TestCase):
    def test_notification_hook_maps_pending_permission_to_needs_permission(self):
        sidecar = ChannelSidecar()
        sidecar.register_session({"session_id": "session-a", "adapter": "claude_channel", "cwd": "/repo", "state": "ready", "channel_health": "ready"})
        event = sidecar.create_message("session-a", sender="phase-loop", content="channel-ping")
        permission = sidecar.create_permission_request(
            "session-a",
            {
                "tool_name": "Bash",
                "description": "Run a harmless command",
                "input_preview": "echo ok",
                "risk_class": "low",
            },
        )

        hook = sidecar.record_hook_event("session-a", {"hook": "Notification", "cwd": "/repo", "permission_mode": "default"})
        session = sidecar.get_session("session-a")

        self.assertEqual(hook["hook"], "Notification")
        self.assertEqual(session["state"], "needs_permission")
        self.assertEqual(session["channel_health"], "needs_permission")
        self.assertEqual(permission.event_id, event.event_id)
        self.assertEqual(session["permission_state"]["last_request_id"], permission.request_id)
        rendered = json.dumps({"hook": hook, "session": session}, sort_keys=True)
        self.assertNotIn("provider payload", rendered)
        self.assertNotIn("raw terminal", rendered)

    def test_notification_hook_without_pending_permission_maps_to_needs_input(self):
        sidecar = ChannelSidecar()
        sidecar.register_session({"session_id": "session-a", "adapter": "claude_channel", "cwd": "/repo", "state": "ready", "channel_health": "ready"})

        sidecar.record_hook_event("session-a", {"hook": "Notification", "cwd": "/repo", "permission_mode": "default"})
        session = sidecar.get_session("session-a")

        self.assertEqual(session["state"], "needs_input")
        self.assertEqual(session["channel_health"], "needs_input")

    def test_plugin_permission_notification_metadata_excludes_token_and_payload_values(self):
        source = PLUGIN.read_text(encoding="utf-8")
        self.assertIn("event_type: 'permission_request'", source)
        for field in ("request_id", "session_id", "tool_name", "description", "input_preview", "risk_class", "requested_at"):
            self.assertIn(field, source)
        permission_meta = source[source.index("function permissionMeta") :]
        self.assertNotIn("bearerToken", permission_meta)
        self.assertNotIn("provider_payload", permission_meta)
        self.assertNotIn("terminal_transcript", permission_meta)

    def test_notify_hook_fanout_posts_only_metadata_fields(self):
        source = NOTIFY.read_text(encoding="utf-8")
        self.assertIn('"hook": os.environ.get("PHASE_LOOP_HOOK_NAME", "Notification")', source)
        self.assertIn('"cwd": os.environ.get("PHASE_LOOP_HOOK_CWD", "")', source)
        self.assertIn('"permission_mode": os.environ.get("PHASE_LOOP_HOOK_PERMISSION_MODE", "")', source)
        self.assertNotIn('"payload"', source)
        self.assertNotIn('"terminal_transcript"', source)
        self.assertNotIn('"provider_payload"', source)


if __name__ == "__main__":
    unittest.main()
