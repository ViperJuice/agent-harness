import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
SRC = ROOT / "vendor" / "phase-loop-runtime" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from phase_loop_runtime.events import append_event, read_events
from phase_loop_runtime.launcher import AuthPreflightResult, LaunchResult
from phase_loop_runtime.models import LoopEvent, utc_now
from phase_loop_runtime.provenance import event_provenance
from phase_loop_runtime.reconcile import reconcile
from phase_loop_runtime.runner import run_loop
from phase_loop_test_utils import make_repo, write_phase_plan


class PhaseLoopCloseoutEventTest(unittest.TestCase):
    def test_executor_complete_closeout_emits_executor_closeout_event(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = self._fixture(Path(td))

            self._run_with_closeout(repo, roadmap, self._payload("complete"))

            event = self._executor_closeout_events(repo)[0]
            self.assertEqual(event["action"], "executor.closeout")
            self.assertEqual(event["status"], "complete")
            self.assertEqual(event["metadata"]["executor_closeout_event"]["source_status"], "complete")

    def test_executor_event_keeps_valid_phase_provenance(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = self._fixture(Path(td))

            self._run_with_closeout(repo, roadmap, self._payload("complete"))

            event = self._executor_closeout_events(repo)[0]
            provenance = event_provenance(roadmap, "CLOSEOUTEVENT")
            self.assertEqual(event["roadmap_sha256"], provenance["roadmap_sha256"])
            self.assertEqual(event["phase_sha256"], provenance["phase_sha256"])

    def test_executor_event_preserves_gates_and_dirty_paths(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = self._fixture(Path(td))
            payload = self._payload("complete", dirty_paths=["phase-owned.log"])

            self._run_with_closeout(repo, roadmap, payload)

            closeout = self._executor_closeout_events(repo)[0]["metadata"]["executor_closeout_event"]
            self.assertEqual(closeout["produced_if_gates"], ["IF-0-CLOSEOUTEVENT-1", "IF-0-CLOSEOUTEVENT-2"])
            self.assertEqual(closeout["dirty_paths"], ["phase-owned.log"])

    def test_runner_blocked_event_supersedes_executor_complete(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = self._fixture(Path(td))

            def fake_launch(spec, **_kwargs):
                (repo / "unowned.txt").write_text("dirty\n", encoding="utf-8")
                return LaunchResult(command=spec.command, returncode=0, output=json.dumps(self._payload("complete")), executor=spec.executor)

            self._run_with_fake_launch(repo, roadmap, fake_launch)

            events = read_events(repo)
            self.assertEqual(self._executor_closeout_events(repo)[0]["status"], "complete")
            self.assertEqual(events[-1]["status"], "blocked")
            self.assertEqual(reconcile(repo, roadmap).phases["CLOSEOUTEVENT"], "blocked")

    def test_executor_blocked_closeout_reduces_to_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = self._fixture(Path(td))
            payload = self._payload(
                "blocked",
                verification_status="blocked",
                produced_if_gates=[],
                blocker_class="repeated_verification_failure",
                blocker_summary="Focused fixture blocker.",
                human_required=False,
            )

            self._run_with_closeout(repo, roadmap, payload)

            self.assertEqual(self._executor_closeout_events(repo)[0]["status"], "blocked")
            self.assertEqual(reconcile(repo, roadmap).phases["CLOSEOUTEVENT"], "blocked")

    def test_reopen_execute_cycle_records_new_executor_event(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = self._fixture(Path(td))

            self._run_with_closeout(repo, roadmap, self._payload("complete"))
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="CLOSEOUTEVENT",
                    action="phase_reopen",
                    status="planned",
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="fixture",
                    **event_provenance(roadmap, "CLOSEOUTEVENT"),
                ),
            )
            self._run_with_closeout(repo, roadmap, self._payload("complete"))

            self.assertEqual(len(self._executor_closeout_events(repo)), 2)
            self.assertEqual(reconcile(repo, roadmap).phases["CLOSEOUTEVENT"], "complete")

    def test_legacy_none_phase_sha_event_coexists_with_executor_event(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = self._fixture(Path(td))
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="CLOSEOUTEVENT",
                    action="manual_repair",
                    status="complete",
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="fixture",
                    roadmap_sha256=event_provenance(roadmap, "CLOSEOUTEVENT")["roadmap_sha256"],
                    phase_sha256=None,
                ),
            )

            self._run_with_closeout(repo, roadmap, self._payload("complete"))
            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.phases["CLOSEOUTEVENT"], "complete")
            self.assertEqual(len(snapshot.ledger_warnings), 0)

    def test_invalid_gate_closeout_does_not_emit_executor_event(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = self._fixture(Path(td))
            payload = self._payload("complete", produced_if_gates=["IF-0-CLOSEOUTEVENT-1"])

            self._run_with_closeout(repo, roadmap, payload)

            self.assertEqual(self._executor_closeout_events(repo), [])
            self.assertEqual(reconcile(repo, roadmap).phases["CLOSEOUTEVENT"], "blocked")

    def _payload(
        self,
        terminal_status: str,
        *,
        verification_status: str = "passed",
        dirty_paths: list[str] | None = None,
        produced_if_gates: list[str] | None = None,
        blocker_class: str | None = None,
        blocker_summary: str | None = None,
        human_required: bool | None = None,
    ) -> dict:
        return {
            "terminal_status": terminal_status,
            "verification_status": verification_status,
            "dirty_paths": [] if dirty_paths is None else dirty_paths,
            "produced_if_gates": (
                ["IF-0-CLOSEOUTEVENT-1", "IF-0-CLOSEOUTEVENT-2"]
                if produced_if_gates is None
                else produced_if_gates
            ),
            "next_action": None,
            "blocker_class": blocker_class,
            "blocker_summary": blocker_summary,
            "human_required": human_required,
            "required_human_inputs": [],
        }

    def _run_with_closeout(self, repo: Path, roadmap: Path, payload: dict) -> None:
        def fake_launch(spec, **_kwargs):
            return LaunchResult(command=spec.command, returncode=0, output=json.dumps(payload), executor=spec.executor)

        self._run_with_fake_launch(repo, roadmap, fake_launch)

    def _run_with_fake_launch(self, repo: Path, roadmap: Path, fake_launch) -> None:
        with patch("phase_loop_runtime.runner.run_auth_preflight", return_value=AuthPreflightResult(ok=True, metadata={})), patch(
            "phase_loop_runtime.runner.launch_with_spec", side_effect=fake_launch
        ):
            run_loop(repo, roadmap, phase="CLOSEOUTEVENT", executor="codex")

    def _executor_closeout_events(self, repo: Path) -> list[dict]:
        return [
            event
            for event in read_events(repo)
            if isinstance(event.get("metadata"), dict) and isinstance(event["metadata"].get("executor_closeout_event"), dict)
        ]

    def _fixture(self, tmp_path: Path) -> tuple[Path, Path]:
        repo = make_repo(tmp_path)
        roadmap = repo / "specs" / "phase-plans-v25.md"
        roadmap.write_text("# v25\n\n### Phase 2 - Guaranteed Run-Status Event After Closeout (CLOSEOUTEVENT)\n", encoding="utf-8")
        write_phase_plan(
            repo,
            "CLOSEOUTEVENT",
            roadmap,
            body=(
                "# CLOSEOUTEVENT\n\n"
                "## Interface Freeze Gates\n\n"
                "- [ ] IF-0-CLOSEOUTEVENT-1\n"
                "- [ ] IF-0-CLOSEOUTEVENT-2\n\n"
                "## Lanes\n\n"
                "### SL-0 - Closeout event\n"
                "- **Owned files**: `runner.py`\n"
                "- **Interfaces provided**: `IF-0-CLOSEOUTEVENT-1`, `IF-0-CLOSEOUTEVENT-2`\n"
            ),
        )
        subprocess.run(["git", "add", "specs/phase-plans-v25.md", "plans/phase-plan-v1-CLOSEOUTEVENT.md"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "closeout event fixture"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
        return repo, roadmap


if __name__ == "__main__":
    unittest.main()
