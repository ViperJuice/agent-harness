import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from phase_loop_runtime.claude_agent_view import AgentViewLifecycleResult, BlockerSummary, ClaudeAgentViewAdapter
from phase_loop_runtime.launcher import LaunchSpec, _agent_view_route_status, _launch_claude_agent_view


class ClaudeAgentViewLifecycleTest(unittest.TestCase):
    def test_background_launch_returns_metadata_only_lifecycle_shape(self):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            if command == ["claude", "--bg", "--help"]:
                return subprocess.CompletedProcess(command, 0, stdout="Usage: claude\n")
            if command[:2] == ["claude", "--bg"]:
                return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"id": "agent-1", "logs": "raw transcript"}))
            if command == ["claude", "agents", "--json", "--all"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps(
                        [
                            {
                                "id": "agent-1",
                                "session_id": "session-1",
                                "cwd": "/repo",
                                "state": "running",
                                "started_at": "2026-06-19T12:00:00Z",
                                "auth_posture": {"status": "authenticated", "method": "subscription"},
                                "logs": "raw transcript",
                            }
                        ]
                    ),
                )
            raise AssertionError(f"unexpected command: {command}")

        adapter = ClaudeAgentViewAdapter(runner=run)
        with mock.patch("phase_loop_runtime.claude_agent_view.shutil.which", return_value="/usr/bin/claude"):
            lifecycle = adapter.launch_background("do work", cwd="/repo", name="c2-bg-test", permission="plan")

        self.assertEqual(lifecycle.to_json(), {
            "session_id": "session-1",
            "state": "running",
            "cwd": "/repo",
            "logs_ref": "claude logs session-1",
            "started_at": "2026-06-19T12:00:00Z",
            "completed_at": None,
            "stop_result": None,
            "auth_posture": "subscription_local",
            "billing_posture": "subscription_included",
        })
        self.assertIn(["claude", "--bg", "--name", "c2-bg-test", "--cwd", "/repo", "--permission-mode", "plan", "do work"], calls)
        rendered = json.dumps(lifecycle.to_json(), sort_keys=True)
        self.assertNotIn("raw transcript", rendered)
        self.assertNotIn("logs\":", rendered)

    def test_pending_workspace_or_mcp_trust_blocks_launch_before_subprocess(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir)
            (path / ".mcp.json").write_text(
                json.dumps({"mcpServers": {"pmcp": {"approval": "pending"}}}),
                encoding="utf-8",
            )
            runner = mock.Mock()
            adapter = ClaudeAgentViewAdapter(runner=runner)

            result = adapter.prepare_launch("do work", cwd=path)

        self.assertFalse(result.trusted)
        self.assertEqual(result.trust_state["mcp"], "pmcp_pending_approval")
        self.assertEqual(result.blocker.reason, "trust_preflight_blocked")
        runner.assert_not_called()

    def test_lifecycle_states_include_running_done_blocked_stopped_and_failed(self):
        adapter = ClaudeAgentViewAdapter(
            runner=_runner(
                stdout=json.dumps(
                    [
                        {"id": "running-1", "state": "running", "pid": 10, "name": "run"},
                        {"id": "done-1", "state": "done", "status": "completed"},
                        {"id": "blocked-1", "state": "blocked", "status": "needs_input"},
                        {"id": "stopped-1", "state": "stopped"},
                        {"id": "failed-1", "state": "failed"},
                        {"id": "partial-1"},
                    ]
                )
            )
        )

        states = [session.state for session in adapter.list_sessions().sessions]

        self.assertEqual(states, ["running", "done", "blocked", "stopped", "failed", "unknown"])

    def test_failed_session_inspection_is_terminal_metadata_only(self):
        adapter = ClaudeAgentViewAdapter(
            runner=_runner(
                stdout=json.dumps(
                    [
                        {
                            "id": "agent-1",
                            "session_id": "session-1",
                            "cwd": "/repo",
                            "state": "failed",
                            "logs": "raw transcript",
                            "stderr": "sensitive stderr",
                        }
                    ]
                )
            )
        )

        lifecycle = adapter.inspect("agent-1", cwd="/repo")

        self.assertEqual(lifecycle.state, "failed")
        self.assertIsNotNone(lifecycle.completed_at)
        self.assertEqual(lifecycle.logs_ref, "claude logs session-1")
        self.assertEqual(lifecycle.blocker.reason, "agent_view_failed")
        self.assertEqual(_agent_view_route_status(lifecycle.state), "blocked")
        self.assertEqual(_agent_view_route_status("unknown"), "stale")
        rendered = json.dumps(lifecycle.to_json(), sort_keys=True)
        self.assertNotIn("raw transcript", rendered)
        self.assertNotIn("sensitive stderr", rendered)
        self.assertNotIn("agent_view_failed", rendered)

    def test_failed_agent_view_launch_returns_blocked_result(self):
        class FakeAgentViewAdapter:
            def launch_background(self, prompt, *, cwd, **kwargs):
                return AgentViewLifecycleResult(
                    session_id="agent-1",
                    state="failed",
                    cwd=str(cwd),
                    logs_ref="claude logs agent-1",
                    started_at="2026-06-19T12:00:00Z",
                    completed_at="2026-06-19T12:05:00Z",
                    stop_result=None,
                    auth_posture="subscription_local",
                    billing_posture="subscription_included",
                    blocker=BlockerSummary("agent_view_failed", "Claude Agent View reported a terminal failed state."),
                )

        with tempfile.TemporaryDirectory() as tmpdir:
            spec = LaunchSpec(
                executor="claude",
                command=["claude", "--bg", "--permission-mode", "plan"],
                prompt_bundle=_PromptBundle(),
                injection_metadata=None,
                delivery_mode="agent_view",
                dispatch_decision=None,
                available=True,
                selected_model="opus",
                selected_effort="max",
                wrapped_cwd=tmpdir,
                claude_route="claude_agent_view",
            )
            with mock.patch("phase_loop_runtime.launcher.ClaudeAgentViewAdapter", return_value=FakeAgentViewAdapter()):
                result = _launch_claude_agent_view(spec, log_path=None)

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.claude_route, "claude_agent_view")
        self.assertEqual(result.claude_route_result["status"], "blocked")
        self.assertEqual(result.claude_route_result["text"], "Claude Agent View reported a terminal failed state.")
        self.assertEqual(result.claude_route_result["warnings"], ["Claude Agent View reported a terminal failed state."])
        self.assertEqual(result.claude_route_result["artifacts"][0]["state"], "failed")
        self.assertEqual(result.claude_route_result["artifacts"][0]["logs_ref"], "claude logs agent-1")
        rendered = json.dumps(result.event_metadata(), sort_keys=True)
        self.assertNotIn("raw transcript", rendered)
        self.assertNotIn("Bearer", rendered)

    def test_failure_aliases_reduce_to_failed(self):
        adapter = ClaudeAgentViewAdapter(
            runner=_runner(
                stdout=json.dumps(
                    [
                        {"id": "failed-1", "state": "failed"},
                        {"id": "error-1", "state": "error"},
                        {"id": "errored-1", "state": "errored"},
                        {"id": "crashed-1", "state": "crashed"},
                        {"id": "failure-1", "state": "failure"},
                        {"id": "unknown-1", "state": "future_state"},
                    ]
                )
            )
        )

        states = [session.state for session in adapter.list_sessions().sessions]

        self.assertEqual(states, ["failed", "failed", "failed", "failed", "failed", "unknown"])

    def test_unsupported_launch_surface_is_structured_blocker(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = ClaudeAgentViewAdapter(runner=_runner(stdout="unknown option", returncode=2))
            with mock.patch("phase_loop_runtime.claude_agent_view.shutil.which", return_value="/usr/bin/claude"):
                result = adapter.prepare_launch("do work", cwd=tmpdir)

        self.assertFalse(result.trusted)
        self.assertEqual(result.blocker.reason, "unsupported_launch")

    def test_completed_sessions_can_build_cleanup_but_blocked_sessions_are_not_force_removed(self):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 1, stdout="record is blocked")

        adapter = ClaudeAgentViewAdapter(runner=run)

        self.assertEqual(adapter.remove_command("done-1"), ["claude", "rm", "done-1"])
        result = adapter.remove("blocked-1")

        self.assertEqual(calls, [["claude", "rm", "blocked-1"]])
        self.assertFalse(result.ok)
        self.assertEqual(result.output, "")
        self.assertEqual(result.blocker.reason, "remove_refused")

    def test_logs_are_human_readable_output_not_machine_state(self):
        adapter = ClaudeAgentViewAdapter(runner=_runner(stdout="human log text\n"))

        result = adapter.logs("agent-1")

        self.assertTrue(result.ok)
        self.assertEqual(result.command, ("claude", "logs", "agent-1"))
        self.assertEqual(result.output, "human log text\n")

    def test_attach_and_stop_use_documented_agent_view_commands(self):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            if command == ["claude", "attach", "agent-1"]:
                return subprocess.CompletedProcess(command, 0, stdout="attached")
            if command == ["claude", "stop", "agent-1"]:
                return subprocess.CompletedProcess(command, 0, stdout="stopped")
            if command == ["claude", "agents", "--json", "--all"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps([{"id": "agent-1", "state": "stopped", "cwd": "/repo", "completed_at": "2026-06-19T12:05:00Z"}]),
                )
            raise AssertionError(f"unexpected command: {command}")

        adapter = ClaudeAgentViewAdapter(runner=run)

        attach = adapter.attach("agent-1")
        stopped = adapter.stop("agent-1", cwd="/repo")

        self.assertTrue(attach.ok)
        self.assertEqual(attach.output, "")
        self.assertEqual(stopped.state, "stopped")
        self.assertEqual(stopped.stop_result, "stopped")
        self.assertEqual(stopped.logs_ref, "claude logs agent-1")
        self.assertEqual(calls, [["claude", "attach", "agent-1"], ["claude", "stop", "agent-1"], ["claude", "agents", "--json", "--all"]])


def _runner(*, stdout, returncode=0):
    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, returncode, stdout=stdout)

    return run


class _PromptBundle:
    def render_context(self):
        return "do work"


if __name__ == "__main__":
    unittest.main()
