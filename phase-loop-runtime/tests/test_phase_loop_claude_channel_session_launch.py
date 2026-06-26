import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from importlib.machinery import SourceFileLoader
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
SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "launch-claude-channel-session"
launch = SourceFileLoader("launch_claude_channel_session", str(SCRIPT)).load_module()


class ClaudeChannelSessionLaunchTest(unittest.TestCase):
    def test_dry_run_metadata_uses_channel_adapter_and_hyphenated_tools(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(launch.shutil, "which", return_value="/usr/bin/claude"), mock.patch.object(
                launch.subprocess,
                "run",
                side_effect=[
                    mock.Mock(stdout=json.dumps({"loggedIn": True, "apiProvider": "claude", "authMethod": "subscription"}), returncode=0),
                    mock.Mock(stdout="Usage: claude", returncode=0),
                ],
            ):
                metadata = launch.session_metadata(Path(tmpdir))

        rendered = json.dumps(metadata, sort_keys=True)
        expected_fields = {
            "session_id",
            "adapter",
            "route",
            "cwd",
            "state",
            "auth_posture",
            "trust_state",
            "channel_health",
            "sidecar_url",
            "command",
            "plugin_dir",
        }
        self.assertEqual(set(metadata), expected_fields)
        self.assertEqual(metadata["adapter"], "claude_channel")
        self.assertEqual(metadata["route"], "claude_channel")
        self.assertEqual(metadata["sidecar_url"], "http://127.0.0.1:8765")
        self.assertEqual(metadata["state"], "starting")
        self.assertIn("server:phase-loop-channel", metadata["command"])
        self.assertIn("mcp__phase-loop-channel__reply,mcp__phase-loop-channel__status", metadata["command"])
        for forbidden in ("bearer", "token", "api_key", "oauth", "provider_payload", "terminal transcript"):
            self.assertNotIn(forbidden, rendered.lower())

    def test_dry_run_metadata_does_not_leak_auth_payload_secrets(self):
        # IF-0-DFCHPREFLIGHT-1: the operator-safe dry-run probe records auth posture
        # as metadata only — a token/bearer present in the raw `claude auth status`
        # payload must never appear in the dry-run output.
        secret = "tok-SUPERSECRET-deadbeef"
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(launch.shutil, "which", return_value="/usr/bin/claude"), mock.patch.object(
                launch.subprocess,
                "run",
                side_effect=[
                    mock.Mock(
                        stdout=json.dumps(
                            {
                                "loggedIn": True,
                                "apiProvider": "claude",
                                "authMethod": "subscription",
                                "token": secret,
                                "bearer": secret,
                                "oauth": {"access_token": secret},
                            }
                        ),
                        returncode=0,
                    ),
                    mock.Mock(stdout="Usage: claude", returncode=0),
                ],
            ):
                metadata = launch.session_metadata(Path(tmpdir))

        rendered = json.dumps(metadata, sort_keys=True)
        self.assertNotIn(secret, rendered)
        self.assertEqual(metadata["auth_posture"]["status"], "authenticated")
        self.assertEqual(metadata["auth_posture"]["method"], "subscription")

    def test_preflights_block_missing_auth_channel_and_pmcp_pending_approval(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir)
            (path / ".mcp.json").write_text(
                json.dumps({"mcpServers": {"pmcp": {"approval": "pending"}}}),
                encoding="utf-8",
            )
            with mock.patch.object(launch.shutil, "which", return_value="/usr/bin/claude"), mock.patch.object(
                launch,
                "claude_auth_posture",
                return_value={"status": "blocked", "reason": "not_logged_in"},
            ), mock.patch.object(launch, "channel_supported", return_value=False):
                metadata = launch.session_metadata(path)

        self.assertEqual(metadata["state"], "blocked")
        self.assertEqual(metadata["channel_health"], "blocked")
        self.assertEqual(metadata["trust_state"]["mcp"], "pmcp_pending_approval")
        self.assertEqual(metadata["auth_posture"]["status"], "blocked")

    def test_trusted_workspace_without_mcp_is_trusted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            trust_state = launch.workspace_trust_state(Path(tmpdir))

        self.assertEqual(trust_state, {"status": "trusted", "workspace": "trusted", "mcp": "absent"})

    def test_channel_package_manifests_are_consistent_without_node_modules(self):
        plugin = json.loads((ROOT / "claude-config/plugins/phase-loop-channel/.claude-plugin/plugin.json").read_text(encoding="utf-8"))
        package = json.loads((ROOT / "claude-config/plugins/phase-loop-channel/package.json").read_text(encoding="utf-8"))
        lockfile = (ROOT / "claude-config/plugins/phase-loop-channel/bun.lock").read_text(encoding="utf-8")

        self.assertEqual(plugin["name"], package["name"])
        self.assertEqual(plugin["version"], package["version"])
        channel = plugin["components"]["channels"][0]
        self.assertEqual(channel["protocol"], "experimental.claude/channel")
        self.assertEqual(channel["command"], "bun")
        self.assertIn("start", channel["args"])
        self.assertIn("@modelcontextprotocol/sdk", package["dependencies"])
        self.assertIn("@modelcontextprotocol/sdk", lockfile)
        tracked = subprocess.run(
            ["git", "ls-files", "claude-config/plugins/phase-loop-channel/node_modules"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            check=True,
        )
        self.assertEqual(tracked.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
