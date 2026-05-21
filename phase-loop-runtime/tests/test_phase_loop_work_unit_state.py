import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.events import append_event, append_work_unit_event, read_events, read_work_unit_events
from phase_loop_runtime.models import LoopEvent, WorkUnitEventMetadata, WorkUnitIdentity, WorkUnitState, utc_now
from phase_loop_runtime.provenance import event_provenance
from phase_loop_runtime.state import load_work_unit_state, state_path, write_state, write_work_unit_state
from phase_loop_test_utils import make_repo, provenanced_state


class PhaseLoopWorkUnitStateTest(unittest.TestCase):
    def test_work_unit_state_round_trips_all_statuses(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_state(repo, provenanced_state(repo, roadmap, {"RUNNER": "executing"}))

            for index, status in enumerate(("pending", "running", "complete", "blocked", "skipped", "superseded", "awaiting-closeout"), start=1):
                identity = WorkUnitIdentity(phase="RUNNER", kind="lane_execute", lane_id=f"SL-{index}", attempt=1)
                write_work_unit_state(
                    repo,
                    WorkUnitState(
                        identity=identity,
                        status=status,
                        artifacts={"terminal": f"/tmp/{index}/terminal-summary.json"},
                        terminal_summary_path=f"/tmp/{index}/terminal-summary.json",
                    ),
                    roadmap=roadmap,
                )

            loaded = load_work_unit_state(repo)

            self.assertEqual(len(loaded), 7)
            self.assertEqual(loaded["RUNNER.lane_execute.SL-1.1"].status, "pending")
            raw = json.loads(state_path(repo).read_text(encoding="utf-8"))
            self.assertEqual(raw["latest_work_unit"]["status"], "awaiting-closeout")

    def test_work_unit_events_coexist_with_phase_events_and_legacy_events(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            legacy = repo / ".codex" / "phase-loop"
            legacy.mkdir(parents=True)
            legacy_event = LoopEvent(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phase="RUNNER",
                action="status",
                status="planned",
                model="gpt-5.4",
                reasoning_effort="medium",
                source="legacy",
                **event_provenance(roadmap, "RUNNER"),
            )
            (legacy / "events.jsonl").write_text(json.dumps(legacy_event.to_json()) + "\n", encoding="utf-8")
            append_event(repo, legacy_event)
            identity = WorkUnitIdentity(phase="RUNNER", kind="lane_execute", lane_id="SL-0", attempt=1)
            append_work_unit_event(
                repo,
                WorkUnitEventMetadata(identity=identity, status="running", event_type="launch"),
                roadmap=roadmap,
            )

            events = read_events(repo)
            work_unit_events = read_work_unit_events(repo, "RUNNER.lane_execute.SL-0.1")

            self.assertEqual(events[0]["phase"], "RUNNER")
            self.assertEqual(work_unit_events[-1]["work_unit"]["status"], "running")
            self.assertEqual(work_unit_events[-1]["event_kind"], "work_unit")

    def test_malformed_work_unit_records_are_ignored_by_loader(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            identity = WorkUnitIdentity(phase="RUNNER", kind="lane_execute", lane_id="SL-0", attempt=1)
            good = WorkUnitState(identity=identity, status="complete")
            write_work_unit_state(repo, good, roadmap=roadmap)
            raw = json.loads(state_path(repo).read_text(encoding="utf-8"))
            raw["work_units"]["bad"] = {"identity": {"phase": "RUNNER", "kind": "lane_execute"}, "status": "bad"}
            state_path(repo).write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")

            loaded = load_work_unit_state(repo)

            self.assertEqual(tuple(loaded), ("RUNNER.lane_execute.SL-0.1",))


if __name__ == "__main__":
    unittest.main()
