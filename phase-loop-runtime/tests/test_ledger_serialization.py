from __future__ import annotations

import json
import os
import tempfile
import textwrap
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from phase_loop_runtime.events import append_event, event_path, read_events
from phase_loop_runtime.launcher import LaunchResult
from phase_loop_runtime.models import StateSnapshot, utc_now
from phase_loop_runtime.provenance import snapshot_provenance
from phase_loop_runtime.runner import run_loop
from phase_loop_runtime.state import state_path, write_state
from phase_loop_runtime.worker_pool import read_worker_summary, worker_summary_path, write_worker_summary
from phase_loop_test_utils import (
    build_fake_automation_output,
    commit_fixture_paths,
    make_repo,
    provenanced_event,
    write_phase_plan,
)

import pytest

# TESTDECOUPLE SL-1 (overlay-dependent): builds a skill/adoption bundle or runs the
# runtime execute path, which resolves the dotfiles skill-source / profile overlay
# (claude-config/*, codex-config/* …) absent standalone. Run-time integration: the
# conftest hook skips it when no dotfiles tree is reachable.
pytestmark = pytest.mark.dotfiles_integration


class LedgerSerializationTest(unittest.TestCase):
    def test_concurrent_coordinator_appends_write_complete_jsonl_records(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            def append(index: int) -> None:
                append_event(repo, provenanced_event(repo, roadmap, f"P{index}", "complete", action="coordinator.worker_completed"))

            with ThreadPoolExecutor(max_workers=5) as executor:
                tuple(executor.map(append, range(5)))

            event_file = event_path(repo)
            lines = event_file.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 5)
            decoded = [json.loads(line) for line in lines]
            self.assertEqual({event["phase"] for event in decoded}, {"P0", "P1", "P2", "P3", "P4"})

    def test_state_write_uses_atomic_replace_without_temp_residue(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            snapshot = StateSnapshot(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phases={"A": "complete"},
                **snapshot_provenance(roadmap),
            )

            write_state(repo, snapshot)

            data = json.loads(state_path(repo).read_text(encoding="utf-8"))
            self.assertEqual(data["phases"], {"A": "complete"})
            self.assertEqual(list(state_path(repo).parent.glob("state.*.json")), [])

    def test_worker_summary_written_before_read_is_ingested(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"

            path = write_worker_summary(repo, roadmap, "A", {"terminal_status": "complete"})
            result = read_worker_summary(repo, roadmap, "A")

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["path"], str(path))
            self.assertEqual(result["summary"]["phase"], "A")
            self.assertEqual(result["summary"]["terminal_status"], "complete")

    def test_torn_worker_summary_is_detected_from_file_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            path = worker_summary_path(repo, roadmap, "A")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{", encoding="utf-8")

            result = read_worker_summary(repo, roadmap, "A", stale_after_seconds=0)

            self.assertEqual(result["status"], "torn")
            self.assertEqual(result["path"], str(path))
            self.assertEqual(result["size"], 1)

    def test_parallel_dispatch_worker_completion_records_summary_ingest(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = _write_two_phase_roadmap(repo)
            plans = tuple(write_phase_plan(repo, phase, roadmap) for phase in ("A", "B"))
            commit_fixture_paths(repo, "add parallel plans", roadmap, *plans)

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                phase = _phase_from_spec(spec)
                return LaunchResult(
                    command=spec.command,
                    returncode=0,
                    output=build_fake_automation_output(
                        status="complete",
                        verification_status="passed",
                        artifact=str(repo / "plans" / f"phase-plan-v1-{phase}.md"),
                        artifact_state="tracked",
                    ),
                    executor=spec.executor,
                    log_path=str(log_path) if log_path else None,
                )

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                snapshot, results = run_loop(repo, roadmap, parallel_dispatch=True)

            self.assertEqual(len(results), 2)
            self.assertEqual(snapshot.phases["A"], "complete")
            worker_events = [
                event["metadata"]["coordinator"]
                for event in read_events(repo)
                if event["action"] == "coordinator.worker_completed"
            ]
            self.assertEqual({event["summary_read"]["status"] for event in worker_events}, {"ok"})

    def test_torn_worker_summary_marks_phase_blocked_without_losing_sibling(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = _write_two_phase_roadmap(repo)
            plans = tuple(write_phase_plan(repo, phase, roadmap) for phase in ("A", "B"))
            commit_fixture_paths(repo, "add parallel plans", roadmap, *plans)

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                phase = _phase_from_spec(spec)
                return LaunchResult(
                    command=spec.command,
                    returncode=0,
                    output=build_fake_automation_output(
                        status="complete",
                        verification_status="passed",
                        artifact=str(repo / "plans" / f"phase-plan-v1-{phase}.md"),
                        artifact_state="tracked",
                    ),
                    executor=spec.executor,
                    log_path=str(log_path) if log_path else None,
                )

            def write_summary(repo_arg, roadmap_arg, phase, summary):
                path = worker_summary_path(repo_arg, roadmap_arg, phase)
                path.parent.mkdir(parents=True, exist_ok=True)
                if phase == "A":
                    path.write_text("{", encoding="utf-8")
                    old = time.time() - 5
                    os.utime(path, (old, old))
                    return path
                return write_worker_summary(repo_arg, roadmap_arg, phase, summary)

            with (
                patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch),
                patch("phase_loop_runtime.runner.write_worker_summary", side_effect=write_summary),
            ):
                snapshot, results = run_loop(repo, roadmap, parallel_dispatch=True)

            self.assertEqual(len(results), 2)
            self.assertEqual(snapshot.phases["A"], "blocked")
            self.assertEqual(snapshot.phases["B"], "complete")
            worker_events = [
                event["metadata"]["coordinator"]
                for event in read_events(repo)
                if event["action"] == "coordinator.worker_completed"
            ]
            by_phase = {event["phase_alias"]: event for event in worker_events}
            self.assertEqual(by_phase["A"]["summary_read"]["status"], "torn")
            self.assertEqual(by_phase["B"]["summary_read"]["status"], "ok")

    def test_serial_mode_does_not_create_worker_summary_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.write_text("### Phase 1 - Alpha (A)\n", encoding="utf-8")
            plan = write_phase_plan(repo, "A", roadmap)
            commit_fixture_paths(repo, "add serial plan", roadmap, plan)

            def fake_launch(spec, dry_run=False, log_path=None, stream_output=False, **kwargs):
                return LaunchResult(
                    command=spec.command,
                    returncode=0,
                    output=build_fake_automation_output(
                        status="complete",
                        verification_status="passed",
                        artifact=str(plan),
                        artifact_state="tracked",
                    ),
                    executor=spec.executor,
                    log_path=str(log_path) if log_path else None,
                )

            with patch("phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch):
                run_loop(repo, roadmap, phase="A")

            self.assertFalse(worker_summary_path(repo, roadmap, "A").exists())


def _write_two_phase_roadmap(repo: Path) -> Path:
    roadmap = repo / "specs" / "phase-plans-v1.md"
    roadmap.write_text(
        textwrap.dedent(
            """
            # Roadmap

            ### Phase 1 - Alpha (A)
            **Depends on**
            - (none)

            ---

            ### Phase 2 - Beta (B)
            **Depends on**
            - (none)
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return roadmap


def _phase_from_spec(spec) -> str:
    prompt = spec.prompt_bundle.render_prompt()
    if "phase-plan-v1-A.md" in prompt:
        return "A"
    if "phase-plan-v1-B.md" in prompt:
        return "B"
    raise AssertionError(f"unexpected prompt: {prompt}")


if __name__ == "__main__":
    unittest.main()
