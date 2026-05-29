import json
import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.provenance import event_provenance, phase_sha256, roadmap_sha256
from phase_loop_runtime.reconcile import _event_dedup_key, reconcile
from phase_loop_test_utils import make_repo, utc_now, write_phase_plan


class PhaseLoopLedgerDedupTest(unittest.TestCase):
    def test_identical_invalid_events_warn_once_and_record_duplicate(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            timestamp = utc_now()
            event = _raw_event(repo, roadmap, "contract", "run", "plan_skipped", timestamp)
            _append_raw_event(repo, event)
            _append_raw_event(repo, {**event, "phase": "CONTRACT"})

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(len(snapshot.ledger_warnings), 1)
            self.assertEqual(snapshot.ledger_warnings[0]["reason"], "not_in_allowed_status_set")
            self.assertEqual(len(snapshot.ledger_duplicates_skipped), 1)
            duplicate = snapshot.ledger_duplicates_skipped[0]
            self.assertEqual(duplicate["phase"], "CONTRACT")
            self.assertEqual(duplicate["duplicate_key"]["status"], "plan_skipped")

    def test_first_event_wins_and_duplicate_does_not_update_closeout(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "CONTRACT", roadmap)
            timestamp = utc_now()
            first = _raw_event(repo, roadmap, "CONTRACT", "execute", "complete", timestamp)
            first["metadata"] = {"closeout": {"closeout_commit": "firstcommit", "closeout_action": "commit"}}
            duplicate = dict(first)
            duplicate["metadata"] = {"closeout": {"closeout_commit": "secondcommit", "closeout_action": "commit"}}
            _append_raw_event(repo, first)
            _append_raw_event(repo, duplicate)

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.phases["CONTRACT"], "complete")
            self.assertEqual(snapshot.closeout_summary["closeout_commit"], "firstcommit")
            self.assertEqual(len(snapshot.ledger_duplicates_skipped), 1)

    def test_timestamp_different_events_are_not_dedupped(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            _append_raw_event(repo, _raw_event(repo, roadmap, "CONTRACT", "run", "plan_skipped", "2026-05-23T00:00:00Z"))
            _append_raw_event(repo, _raw_event(repo, roadmap, "CONTRACT", "run", "plan_skipped", "2026-05-23T00:00:01Z"))

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(len(snapshot.ledger_warnings), 2)
            self.assertEqual(snapshot.ledger_duplicates_skipped, ())

    def test_different_identity_fields_are_not_dedupped(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            events = (
                _raw_event(repo, roadmap, "CONTRACT", "run", "plan_skipped", "2026-05-23T00:00:00Z"),
                _raw_event(repo, roadmap, "RUNNER", "run", "plan_skipped", "2026-05-23T00:00:00Z"),
                _raw_event(repo, roadmap, "CONTRACT", "plan", "plan_skipped", "2026-05-23T00:00:00Z"),
                _raw_event(repo, roadmap, "CONTRACT", "run", "dry_run", "2026-05-23T00:00:00Z"),
                _raw_event(
                    repo,
                    roadmap,
                    "CONTRACT",
                    "run",
                    "plan_skipped",
                    "2026-05-23T00:00:00Z",
                    automation_status="blocked",
                ),
                _raw_event(
                    repo,
                    roadmap,
                    "CONTRACT",
                    "run",
                    "plan_skipped",
                    "2026-05-23T00:00:00Z",
                    blocker_class="dirty_worktree_conflict",
                ),
            )
            for event in events:
                _append_raw_event(repo, event)

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(len(snapshot.ledger_warnings), 6)
            self.assertEqual(snapshot.ledger_duplicates_skipped, ())

    def test_def4_same_key_events_with_different_terminal_summary_both_survive(self):
        """DEF-4 regression. Replays the regenesis VISIONHARNESS 2026-05-29T06:48:34Z
        shape: two events with identical (timestamp, phase, action, status,
        automation_status, blocker_class) where one carries an empty
        terminal_summary and the other carries the executor's 7 dirty paths +
        produced_if_gates. Before the content-signature fix, the empty event
        won the dedup and the rich event was silently dropped, leaving
        snapshot.dirty_paths empty even though the executor accurately
        reported its phase-owned output.
        """
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            timestamp = "2026-05-29T06:48:34Z"

            empty_event = _raw_event(
                repo, roadmap, "RUNNER", "run", "blocked", timestamp,
                automation_status="blocked", blocker_class="dirty_worktree_conflict",
            )
            empty_event["metadata"] = {
                **empty_event.get("metadata", {}),
                "terminal_summary": {},
            }

            rich_event = _raw_event(
                repo, roadmap, "RUNNER", "run", "blocked", timestamp,
                automation_status="blocked", blocker_class="dirty_worktree_conflict",
            )
            rich_event["metadata"] = {
                **rich_event.get("metadata", {}),
                "terminal_summary": {
                    "terminal_status": "blocked",
                    "verification_status": "blocked",
                    "dirty_paths": [
                        "docs/contract.md",
                        "package.json",
                        ".github/workflows/x.yml",
                        "scripts/__tests__/foo.smoke.mjs",
                        "scripts/foo.mjs",
                        "planning/phase-artifacts/foo/",
                        "apps/dist/",
                    ],
                    "phase_owned_dirty_paths": [],
                    "produced_if_gates": ["IF-0-RUNNER-1"],
                },
            }

            _append_raw_event(repo, empty_event)
            _append_raw_event(repo, rich_event)

            snapshot = reconcile(repo, roadmap)

            # Both events must survive dedup: the rich event's dirty_paths
            # must reach the snapshot via _event_dirty_summary's terminal_summary
            # fallback.
            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            self.assertEqual(len(snapshot.dirty_paths), 7,
                msg=f"expected 7 dirty paths from rich event, got {list(snapshot.dirty_paths)!r}")
            # Idempotent: no spurious duplicate records.
            self.assertEqual(snapshot.ledger_duplicates_skipped, ())

    def test_def4_byte_identical_events_still_dedup(self):
        """Regression guard for the content-signature change: byte-identical
        events (e.g. from replaying an events.jsonl.bak-* restore or a harness
        retry) must still collapse to one via dedup. The content_sig component
        of the identity matches exactly, so the second event is correctly
        recorded as a duplicate.
        """
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            timestamp = "2026-05-29T06:48:34Z"

            event = _raw_event(
                repo, roadmap, "RUNNER", "run", "blocked", timestamp,
                automation_status="blocked", blocker_class="dirty_worktree_conflict",
            )
            event["metadata"] = {
                **event.get("metadata", {}),
                "terminal_summary": {
                    "terminal_status": "blocked",
                    "verification_status": "blocked",
                    "dirty_paths": ["a.py", "b.py"],
                    "phase_owned_dirty_paths": ["a.py"],
                    "produced_if_gates": ["IF-0-RUNNER-1"],
                },
            }

            _append_raw_event(repo, event)
            _append_raw_event(repo, dict(event))  # exact replay

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            self.assertEqual(len(snapshot.ledger_duplicates_skipped), 1)
            self.assertEqual(snapshot.ledger_duplicates_skipped[0]["phase"], "RUNNER")

    def test_def4_production_executor_closeout_event_shape_no_terminal_summary_key(self):
        """PR #24 review nit: the production `executor_closeout_event` writer
        (runner.py:4647) emits metadata with `executor_closeout_event` +
        `child_automation` but NO `terminal_summary` key at all (not even
        an empty dict). Confirm that shape coexists with a later launch
        closeout event carrying the executor's full terminal_summary —
        mirrors the regenesis VISIONHARNESS 2026-05-29T06:48:34Z events
        more faithfully than the dict-with-empty-terminal_summary stub.
        """
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            timestamp = "2026-05-29T06:48:34Z"

            # Production event 1 shape — wraps the executor's own outcome,
            # no terminal_summary key in metadata.
            executor_closeout_event = _raw_event(
                repo, roadmap, "RUNNER", "run", "blocked", timestamp,
                automation_status="blocked", blocker_class="dirty_worktree_conflict",
            )
            executor_closeout_event["metadata"] = {
                **executor_closeout_event.get("metadata", {}),
                "executor_closeout_event": {
                    "source_status": "blocked",
                    "verification_status": "blocked",
                    "produced_if_gates": ["IF-0-RUNNER-1"],
                    "dirty_paths": ["a.py", "b.py", "c.py", "d.py", "e.py", "f.py", "g.py"],
                },
            }

            # Production event 2 shape — runner-classified, carries the full
            # terminal_summary with dirty_paths + produced_if_gates.
            launch_event = _raw_event(
                repo, roadmap, "RUNNER", "run", "blocked", timestamp,
                automation_status="blocked", blocker_class="dirty_worktree_conflict",
            )
            launch_event["metadata"] = {
                **launch_event.get("metadata", {}),
                "terminal_summary": {
                    "terminal_status": "blocked",
                    "verification_status": "blocked",
                    "dirty_paths": ["a.py", "b.py", "c.py", "d.py", "e.py", "f.py", "g.py"],
                    "phase_owned_dirty_paths": [],
                    "produced_if_gates": ["IF-0-RUNNER-1"],
                },
                "launch_request": {},
                "launch_spec": {},
            }

            _append_raw_event(repo, executor_closeout_event)
            _append_raw_event(repo, launch_event)

            snapshot = reconcile(repo, roadmap)

            # Both events must survive. The rich launch event's dirty_paths
            # must reach the snapshot via _event_dirty_summary's
            # terminal_summary fallback. The executor_closeout_event (no
            # terminal_summary key) gets content_signature=() which is
            # distinct from the launch event's non-trivial signature.
            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            self.assertEqual(len(snapshot.dirty_paths), 7,
                msg=f"expected 7 dirty paths from launch event, got {list(snapshot.dirty_paths)!r}")
            self.assertEqual(snapshot.ledger_duplicates_skipped, ())

    def test_def4_phase_reopen_cleanup_works_with_extended_identity(self):
        """PR #24 review nit: after extending _event_dedup_identity with
        content_signature, confirm the seen_event_keys cleanup at
        reconcile.py:117 (`key for key in seen_event_keys if key[1] !=
        dedup_key[1]`) still correctly filters by phase. Phase remains at
        index 1 of dedup_key, which is spread at the front of the identity
        tuple, so `key[1]` is still phase. Exercise the path explicitly so
        a future refactor that reorders the prefix can't silently break
        phase_reopen recovery.
        """
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            timestamp1 = "2026-05-29T06:48:34Z"
            timestamp2 = "2026-05-29T06:55:00Z"
            timestamp3 = "2026-05-29T07:00:00Z"

            # 1. A blocked event with a real terminal_summary lands first.
            blocked_event = _raw_event(
                repo, roadmap, "RUNNER", "run", "blocked", timestamp1,
                automation_status="blocked", blocker_class="dirty_worktree_conflict",
            )
            blocked_event["metadata"] = {
                **blocked_event.get("metadata", {}),
                "terminal_summary": {
                    "terminal_status": "blocked",
                    "verification_status": "blocked",
                    "dirty_paths": ["x.py", "y.py"],
                },
            }
            _append_raw_event(repo, blocked_event)

            # 2. A phase_reopen event clears the blocker.
            reopen_event = _raw_event(
                repo, roadmap, "RUNNER", "phase_reopen", "planned", timestamp2,
            )
            reopen_event["metadata"] = {
                **reopen_event.get("metadata", {}),
                "phase_reopen": {
                    "reason": "operator unblocked",
                    "prior_status": "blocked",
                    "prior_closeout_commit": "abc123",
                    "reopen_commit": "def456",
                },
            }
            _append_raw_event(repo, reopen_event)

            # 3. After reopen, a fresh blocked event with the SAME (timestamp,
            #    phase, action, status, blocker_class, content_signature) as
            #    the pre-reopen event must NOT be deduplicated against the
            #    pre-reopen one — because the reopen cleared seen_event_keys
            #    for the phase. We simulate by re-appending the original.
            replay_event = dict(blocked_event)
            replay_event = {**replay_event, "timestamp": timestamp3}
            _append_raw_event(repo, replay_event)

            snapshot = reconcile(repo, roadmap)

            # Final state: the replayed blocked event must have been processed
            # (not dropped as a duplicate of the pre-reopen one) so the phase
            # ends up blocked again, not stuck at planned.
            self.assertEqual(snapshot.phases["RUNNER"], "blocked")
            self.assertEqual(snapshot.ledger_duplicates_skipped, ())

    def test_def4_dirty_paths_order_invariant(self):
        """Two events with the same dirty_paths in different order share the
        same content signature (sorted), so the second is correctly recorded
        as a duplicate. Guards against the harness emitting the same dirty
        set in a different iteration order across retries.
        """
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_phase_plan(repo, "RUNNER", roadmap)
            timestamp = "2026-05-29T06:48:34Z"

            event_a = _raw_event(
                repo, roadmap, "RUNNER", "run", "blocked", timestamp,
                automation_status="blocked", blocker_class="dirty_worktree_conflict",
            )
            event_a["metadata"] = {
                **event_a.get("metadata", {}),
                "terminal_summary": {"dirty_paths": ["b.py", "a.py"]},
            }
            event_b = dict(event_a)
            event_b["metadata"] = {
                **event_a.get("metadata", {}),
                "terminal_summary": {"dirty_paths": ["a.py", "b.py"]},
            }

            _append_raw_event(repo, event_a)
            _append_raw_event(repo, event_b)

            snapshot = reconcile(repo, roadmap)

            self.assertEqual(len(snapshot.ledger_duplicates_skipped), 1)

    def test_duplicate_key_reads_nested_automation_status_and_blocker_class(self):
        event = {
            "timestamp": "2026-05-23T00:00:00Z",
            "phase": "contract",
            "action": "execute",
            "status": "blocked",
            "metadata": {
                "child_automation": {
                    "automation_status": "blocked",
                    "automation_blocker_class": "dirty_worktree_conflict",
                }
            },
        }

        self.assertEqual(
            _event_dedup_key(event),
            (
                "2026-05-23T00:00:00Z",
                "CONTRACT",
                "execute",
                "blocked",
                "blocked",
                "dirty_worktree_conflict",
            ),
        )


def _raw_event(
    repo: Path,
    roadmap: Path,
    phase: str,
    action: str,
    status: str,
    timestamp: str,
    *,
    automation_status: str | None = None,
    blocker_class: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "timestamp": timestamp,
        "repo": str(repo),
        "roadmap": str(roadmap),
        "phase": phase,
        "action": action,
        "status": status,
        "source": "fixture",
        "schema_version": 2,
        "roadmap_sha256": roadmap_sha256(roadmap),
        "phase_sha256": phase_sha256(roadmap, phase.upper()),
    }
    if automation_status:
        payload["metadata"] = {"child_automation": {"automation_status": automation_status}}
    if blocker_class:
        payload["blocker"] = {
            "human_required": False,
            "blocker_class": blocker_class,
            "blocker_summary": "fixture blocker",
            "required_human_inputs": (),
        }
    return payload


def _append_raw_event(repo: Path, payload: dict[str, object]) -> None:
    path = repo / ".phase-loop" / "events.jsonl"
    path.parent.mkdir(exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
