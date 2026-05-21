import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.reconcile import reconcile
from phase_loop_runtime.render import render_status
from phase_loop_runtime.events import append_event
from phase_loop_runtime.models import LoopEvent, utc_now
from phase_loop_runtime.provenance import event_provenance
from phase_loop_smoke_utils import (
    BIN,
    append_phase_event,
    make_code_index_blocker_smoke_fixture,
    make_two_phase_repo,
    missing_secret_blocker,
)


class PhaseLoopSmokeBlockersTest(unittest.TestCase):
    def test_ambiguous_roadmap_selection_returns_typed_blocker(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            second = repo / "specs" / "phase-plans-v2.md"
            second.write_text("### Phase 0 - Other (OTHER)\n")
            stamp = 1_800_000_000
            os.utime(repo / "specs" / "phase-plans-v1.md", (stamp, stamp))
            os.utime(second, (stamp, stamp))
            result = subprocess.run([str(BIN), "status", "--repo", str(repo), "--json"], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 2)
            data = json.loads(result.stdout)
            self.assertTrue(data["human_required"])
            self.assertEqual(data["blocker_class"], "ambiguous_roadmap_selection")
            self.assertEqual(data["required_human_inputs"], ["explicit roadmap path or valid roadmap handoff"])

    def test_missing_secret_blocker_preserves_redacted_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_phase_event(repo, roadmap, "ALPHA", "blocked", blocker=missing_secret_blocker())
            snapshot = reconcile(repo, roadmap)
            data = json.loads(render_status(snapshot, as_json=True))
            self.assertTrue(data["human_required"])
            self.assertEqual(data["blocker_class"], "missing_secret")
            self.assertEqual(data["required_human_inputs"], ["provide fixture credential item"])
            self.assertNotIn("secret-value", json.dumps(data))
            self.assertEqual(data["access_attempts"][0]["source"], "op")

    def test_code_index_product_decision_blocker_stays_human_required(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = make_code_index_blocker_smoke_fixture(Path(td))
            append_phase_event(
                fixture.repo,
                fixture.roadmap,
                fixture.execute_phase,
                "blocked",
                blocker={
                    "human_required": True,
                    "blocker_class": "product_decision_missing",
                    "blocker_summary": "code_index still needs a product decision.",
                    "required_human_inputs": ["choose the query behavior"],
                },
            )

            snapshot = reconcile(fixture.repo, fixture.roadmap)
            data = json.loads(render_status(snapshot, as_json=True))

            self.assertTrue(data["human_required"])
            self.assertEqual(data["blocker_class"], "product_decision_missing")
            self.assertEqual(data["required_human_inputs"], ["choose the query behavior"])

    def test_status_text_omits_stale_terminal_summary_after_closeout_advances_current_phase(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="ALPHA",
                    action="execute",
                    status="awaiting_phase_closeout",
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="fixture",
                    metadata={
                        "terminal_summary": {
                            "phase": "ALPHA",
                            "terminal_status": "executed",
                            "verification_status": "not_run",
                            "next_action": "Preserve the verified phase-owned output before rerunning the loop.",
                            "dirty_paths": ["README.md"],
                            "phase_owned_dirty": True,
                        }
                    },
                    **event_provenance(roadmap, "ALPHA"),
                ),
            )
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="ALPHA",
                    action="execute",
                    status="executed",
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="fixture",
                    metadata={
                        "closeout": {
                            "closeout_mode": "push",
                            "closeout_action": "push",
                            "closeout_commit": "abc123",
                            "closeout_push_ref": "origin refs/heads/main",
                            "verification_status": "passed",
                        }
                    },
                    **event_provenance(roadmap, "ALPHA"),
                ),
            )

            text = render_status(reconcile(repo, roadmap))

            self.assertIn("ALPHA: complete", text)
            self.assertIn("* BETA: unplanned", text)
            self.assertNotIn("Terminal dirty paths", text)
            self.assertNotIn("Preserve the verified phase-owned output", text)

    def test_status_text_omits_blocked_terminal_after_manual_repair_completes_roadmap(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            append_phase_event(repo, roadmap, "ALPHA", "complete")
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="BETA",
                    action="execute",
                    status="blocked",
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="fixture",
                    metadata={
                        "terminal_summary": {
                            "phase": "BETA",
                            "terminal_status": "blocked",
                            "verification_status": "blocked",
                            "next_action": "Repair the recorded blocker before rerunning the loop.",
                            "dirty_paths": [],
                            "phase_owned_dirty": False,
                        }
                    },
                    **event_provenance(roadmap, "BETA"),
                ),
            )
            append_event(
                repo,
                LoopEvent(
                    timestamp=utc_now(),
                    repo=str(repo),
                    roadmap=str(roadmap),
                    phase="BETA",
                    action="manual_repair",
                    status="complete",
                    model="gpt-5.4",
                    reasoning_effort="medium",
                    source="operator",
                    metadata={"clears_blocker": True},
                    **event_provenance(roadmap, "BETA"),
                ),
            )

            text = render_status(reconcile(repo, roadmap))

            self.assertIn("Roadmap complete: all phases are complete", text)
            self.assertNotIn("Terminal status: blocked", text)
            self.assertNotIn("Repair the recorded blocker", text)

    def test_render_status_includes_live_git_topology_block(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            snapshot = reconcile(repo, roadmap)
            text = render_status(snapshot)
            self.assertIn("Live git topology:", text)
            self.assertIn("Branch:", text)
            self.assertIn("HEAD:", text)
            self.assertIn("Working tree: clean", text)

    def test_render_status_json_includes_execution_policy_block_per_phase(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.write_text(
                roadmap.read_text(encoding="utf-8")
                + "\n## Execution Policy\n"
                + "- execute: executor=`codex`, model=`gpt-5.5`, effort=`high`\n"
                + "- review: executor=`claude`, model=`claude-opus-4-7`, effort=`high`\n",
                encoding="utf-8",
            )
            snapshot = reconcile(repo, roadmap)
            payload = json.loads(render_status(snapshot, as_json=True))
            policy = payload.get("execution_policy") or {}
            self.assertIn("ALPHA", policy)
            self.assertEqual(policy["ALPHA"]["execute"]["executor"], "codex")
            self.assertEqual(policy["ALPHA"]["execute"]["model"], "gpt-5.5")
            self.assertEqual(policy["ALPHA"]["execute"]["source"], "roadmap policy")
            self.assertEqual(policy["ALPHA"]["review"]["executor"], "claude")

    def test_render_status_json_omits_execution_policy_when_no_policies_present(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            snapshot = reconcile(repo, roadmap)
            payload = json.loads(render_status(snapshot, as_json=True))
            self.assertNotIn("execution_policy", payload)

    def test_render_status_omits_live_git_topology_when_unavailable(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            snapshot = reconcile(repo, roadmap)
            import unittest.mock as _mock
            with _mock.patch(
                "phase_loop_runtime.git_topology.collect_git_topology",
                return_value={"available": False, "reason": "not a git worktree"},
            ):
                from dataclasses import replace as _replace
                stripped = _replace(snapshot, git_topology=None)
                text = render_status(stripped)
            self.assertNotIn("Live git topology:", text)


if __name__ == "__main__":
    unittest.main()
