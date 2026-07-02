import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.launcher import launch


class LauncherLivenessTest(unittest.TestCase):
    def test_launch_without_log_closes_stdin_when_no_payload(self):
        class Completed:
            returncode = 0
            stdout = "ok\n"
            stderr = ""

        with patch("phase_loop_runtime.launcher.subprocess.run", return_value=Completed()) as mocked:
            result = launch(["example-cli"])

        self.assertEqual(result.returncode, 0)
        self.assertEqual(mocked.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertNotIn("input", mocked.call_args.kwargs)

    def test_observed_launch_closes_stdin_when_no_payload(self):
        captured_kwargs = {}

        class FakeProcess:
            pid = 12345
            stdout = io.StringIO("ok\n")

            def poll(self):
                return 0

            def wait(self):
                return 0

        def fake_popen(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return FakeProcess()

        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "output.log"
            with (
                patch("phase_loop_runtime.launcher.subprocess.Popen", side_effect=fake_popen),
                patch("phase_loop_runtime.launcher._process_group_id", return_value=12345),
            ):
                result = launch(["example-cli"], log_path=log_path)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(captured_kwargs["stdin"], subprocess.DEVNULL)

    def test_timeout_launch_records_salvage_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_path = root / "output.log"
            heartbeat_path = root / "heartbeat.json"

            result = launch(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                log_path=log_path,
                heartbeat_path=heartbeat_path,
                timeout_seconds=1,
            )

            self.assertTrue(result.timed_out)
            self.assertIsNotNone(result.cleanup_evidence)
            self.assertEqual(result.cleanup_evidence["reason"], "timeout")
            self.assertIn("SIGTERM", result.cleanup_evidence["signals_sent"])
            self.assertFalse(result.cleanup_evidence["process_alive_after_cleanup"])
            self.assertEqual(result.cleanup_evidence["salvage_snapshot"]["reason"], "timeout")
            self.assertEqual(result.cleanup_evidence["salvage_snapshot"]["log_path"], str(log_path))

    def test_stale_cpu_idle_child_is_torn_down_and_marked_stalled(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_path = root / "output.log"
            heartbeat_path = root / "heartbeat.json"

            with patch("phase_loop_runtime.observability._process_cpu_percent", return_value=0.0):
                result = launch(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    log_path=log_path,
                    heartbeat_path=heartbeat_path,
                    heartbeat_interval_seconds=0,
                    quiet_warning_seconds=0,
                    quiet_blocker_seconds=0,
                )

            self.assertTrue(result.stalled)
            self.assertFalse(result.timed_out)
            self.assertIsNotNone(result.cleanup_evidence)
            self.assertEqual(result.cleanup_evidence["reason"], "stalled")
            self.assertEqual(
                result.cleanup_evidence["salvage_snapshot"]["heartbeat"]["liveness_class"],
                "suspect_stalled",
            )

    def test_stale_cpu_unknown_child_is_torn_down_after_grace(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_path = root / "output.log"
            heartbeat_path = root / "heartbeat.json"

            with (
                patch("phase_loop_runtime.observability._process_cpu_percent", return_value=None),
                patch("phase_loop_runtime.observability._process_group_cpu_percent", return_value=None),
            ):
                result = launch(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    log_path=log_path,
                    heartbeat_path=heartbeat_path,
                    heartbeat_interval_seconds=0,
                    quiet_warning_seconds=0,
                    quiet_blocker_seconds=0,
                )

            self.assertTrue(result.stalled)
            self.assertFalse(result.timed_out)
            self.assertIsNotNone(result.cleanup_evidence)
            self.assertEqual(result.cleanup_evidence["reason"], "stalled")
            heartbeat = result.cleanup_evidence["salvage_snapshot"]["heartbeat"]
            self.assertEqual(heartbeat["liveness_class"], "quiet_unknown")
            self.assertTrue(heartbeat["stalled_suspect"])

    def test_cpu_active_quiet_child_is_not_torn_down_for_stale_output_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_path = root / "output.log"
            heartbeat_path = root / "heartbeat.json"

            with patch("phase_loop_runtime.observability._process_cpu_percent", return_value=99.0):
                result = launch(
                    [sys.executable, "-c", "import time; time.sleep(0.2)"],
                    log_path=log_path,
                    heartbeat_path=heartbeat_path,
                    heartbeat_interval_seconds=0,
                    quiet_warning_seconds=0,
                    quiet_blocker_seconds=0,
                    timeout_seconds=2,
                )

            self.assertEqual(result.returncode, 0)
            self.assertFalse(result.stalled)
            self.assertFalse(result.timed_out)
            self.assertIsNone(result.cleanup_evidence)

    def test_process_group_cpu_activity_prevents_stale_cleanup(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_path = root / "output.log"
            heartbeat_path = root / "heartbeat.json"

            with (
                patch("phase_loop_runtime.observability._process_cpu_percent", return_value=0.0),
                patch("phase_loop_runtime.observability._process_group_cpu_percent", return_value=99.0),
            ):
                result = launch(
                    [sys.executable, "-c", "import time; time.sleep(0.2)"],
                    log_path=log_path,
                    heartbeat_path=heartbeat_path,
                    heartbeat_interval_seconds=0,
                    quiet_warning_seconds=0,
                    quiet_blocker_seconds=0,
                    timeout_seconds=2,
                )

            self.assertEqual(result.returncode, 0)
            self.assertFalse(result.stalled)
            self.assertFalse(result.timed_out)
            self.assertIsNone(result.cleanup_evidence)


if __name__ == "__main__":
    unittest.main()
