"""LEGACY (CLEANSHIP P7) — roadmap-discovery hygiene.

Pins the three reachable discovery-hygiene fixes that ship as a UNIT:

- ``manifest_backed_roadmap`` also skips ``status == "completed"`` entries (default
  ON) so a bare run never silently auto-selects a FINISHED roadmap, with a
  one-release escape hatch (``PHASE_LOOP_DISCOVERY_ALLOW_COMPLETED``). The escape-hatch
  test proves the skip is real (non-vacuous).
- The glob fall-through raises a TYPED ``AmbiguousRoadmapError`` (not a bare
  ``RuntimeError``) that the CLI turns into a RECOVERABLE
  ``blocker_class="ambiguous_roadmap_selection"`` snapshot (exit 2) — never an
  uncaught traceback. agent-harness itself ships ``v1``–``v9``, so once the
  completed-skip stops a frozen manifest from resolving, a bare run reaches exactly
  this branch and must not crash.
- Genuine resumption is protected by the state-file ladder
  (``active_state_roadmap``), which precedes the manifest and glob branches.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.cli import main as cli_main
from phase_loop_runtime.discovery import (
    AmbiguousRoadmapError,
    active_state_roadmap,
    manifest_backed_roadmap,
    select_roadmap,
)
from phase_loop_runtime.plan_manifest import DotfilesPlanEntry, DotfilesPlanRef, append_entry
from phase_loop_runtime.state import write_state

from phase_loop_test_utils import make_repo, provenanced_state, write_phase_plan


def _write_roadmap(repo: Path, version: str, alias: str) -> Path:
    roadmap = repo / "specs" / f"phase-plans-{version}.md"
    roadmap.write_text(f"# Roadmap {version}\n\n### Phase 0 — {alias} ({alias})\n")
    return roadmap


def _add_phase_entry(repo: Path, version: str, alias: str, status: str, roadmap: Path) -> None:
    """Append a VALID phase manifest entry (its plan file must exist) that points at
    ``roadmap`` with the given lifecycle ``status``."""
    plan = repo / "plans" / f"phase-plan-{version}-{alias}.md"
    plan.write_text(f"---\nphase: {alias}\nroadmap: {roadmap.relative_to(repo)}\n---\n# {alias}\n")
    append_entry(
        repo,
        DotfilesPlanEntry(
            slug=f"{version}-{alias}",
            file=f"plans/phase-plan-{version}-{alias}.md",
            type="phase",
            status=status,
            created_at="2026-06-01T00:00:00Z",
            updated_at="2026-06-01T00:00:00Z",
            owner_skill="codex-plan-phase",
            roadmap_ref=DotfilesPlanRef(
                slug=f"phase-plans-{version}",
                file=str(roadmap.relative_to(repo)),
                type="phase",
                status=status,
            ),
            phase_alias=alias,
        ),
    )


class CompletedSkipTest(unittest.TestCase):
    def test_bare_run_no_completed_autoselect(self):
        # An ALL-COMPLETED manifest must NOT auto-select its (finished) roadmap: the
        # completed-skip makes manifest_backed_roadmap fall through (returns None).
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = _write_roadmap(repo, "v9", "LEGACY")
            _add_phase_entry(repo, "v9", "LEGACY", "completed", roadmap)
            self.assertIsNone(
                manifest_backed_roadmap(repo),
                "an all-completed manifest must not resolve a completed roadmap",
            )

    def test_discovery_escape_hatch_allows_completed(self):
        # Non-vacuity + the one-release hatch: with the env override set, the SAME
        # all-completed manifest resolves again (proving the default skip is real).
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = _write_roadmap(repo, "v9", "LEGACY")
            _add_phase_entry(repo, "v9", "LEGACY", "completed", roadmap)
            prev = os.environ.get("PHASE_LOOP_DISCOVERY_ALLOW_COMPLETED")
            os.environ["PHASE_LOOP_DISCOVERY_ALLOW_COMPLETED"] = "1"
            try:
                self.assertEqual(manifest_backed_roadmap(repo), roadmap.resolve())
            finally:
                if prev is None:
                    os.environ.pop("PHASE_LOOP_DISCOVERY_ALLOW_COMPLETED", None)
                else:
                    os.environ["PHASE_LOOP_DISCOVERY_ALLOW_COMPLETED"] = prev

    def test_mixed_status_resolves_the_active_roadmap(self):
        # A completed entry (roadmap A) + an active entry (roadmap B): the completed
        # one is skipped, so the ACTIVE roadmap is the sole candidate and resolves.
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            done = _write_roadmap(repo, "v8", "OLD")
            active = _write_roadmap(repo, "v9", "NEW")
            _add_phase_entry(repo, "v8", "OLD", "completed", done)
            _add_phase_entry(repo, "v9", "NEW", "committed", active)
            self.assertEqual(
                manifest_backed_roadmap(repo), active.resolve(),
                "completed-skip must leave the active roadmap as the unique candidate",
            )


class AmbiguousRoadmapBlockerTest(unittest.TestCase):
    def test_select_roadmap_raises_typed_ambiguous_error(self):
        # >1 specs/phase-plans-v*.md, no state/manifest/handoff → a TYPED
        # AmbiguousRoadmapError carrying the candidates (not a bare RuntimeError).
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))  # ships v1
            _write_roadmap(repo, "v2", "OTHER")
            with self.assertRaises(AmbiguousRoadmapError) as ctx:
                select_roadmap(repo, None)
            self.assertGreaterEqual(len(ctx.exception.candidates), 2)
            # Back-compat: subclasses RuntimeError with the historical message.
            self.assertIsInstance(ctx.exception, RuntimeError)
            self.assertIn("ambiguous roadmap selection", str(ctx.exception))

    def test_cli_bare_run_is_recoverable_blocker_not_traceback(self):
        # End-to-end: a bare run/dry-run on a multi-roadmap repo surfaces the
        # recoverable blocker (exit 2, blocker_class in the snapshot), NOT a crash.
        for command in ("dry-run", "run"):
            with tempfile.TemporaryDirectory() as td:
                repo = make_repo(Path(td))
                _write_roadmap(repo, "v2", "OTHER")
                rc = cli_main([command, "--repo", str(repo)])
                self.assertEqual(rc, 2, f"{command} must exit 2 (recoverable blocker)")
                from phase_loop_runtime.state import load_state

                snap = load_state(repo)
                self.assertEqual(snap.blocker_class, "ambiguous_roadmap_selection")
                self.assertTrue(snap.human_required)

    def test_non_run_commands_degrade_not_traceback(self):
        # CR (grok, major): the crash-fix must cover EVERY command that auto-selects a
        # roadmap, not just the run-path handler. `execute` and `validate-roadmap` call
        # select_roadmap outside that handler; a multi-roadmap repo with no --roadmap
        # must exit 2 via the top-level safety net, not raise an uncaught
        # AmbiguousRoadmapError traceback.
        for argv in (
            ["execute", "SOMEPHASE"],
            ["validate-roadmap"],
        ):
            with tempfile.TemporaryDirectory() as td:
                repo = make_repo(Path(td))
                _write_roadmap(repo, "v2", "OTHER")
                rc = cli_main([*argv, "--repo", str(repo)])
                self.assertEqual(rc, 2, f"{argv[0]} must degrade to exit 2, not crash")


class ResumeLadderTest(unittest.TestCase):
    def test_select_roadmap_resume_state_wins(self):
        # Genuine resumption: a state file naming a roadmap wins over the ambiguous
        # glob — the state-file ladder precedes the manifest and glob branches.
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            v1 = repo / "specs" / "phase-plans-v1.md"
            _write_roadmap(repo, "v2", "OTHER")  # makes the glob ambiguous
            write_state(repo, provenanced_state(repo, v1, {"RUNNER": "planned"}))
            self.assertEqual(active_state_roadmap(repo), v1.resolve())
            # No ambiguity: select_roadmap returns the resumed roadmap, no raise.
            self.assertEqual(select_roadmap(repo, None), v1.resolve())


if __name__ == "__main__":
    unittest.main()
