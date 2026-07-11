"""#62 — `phase-loop status` / read-path reconcile must be structurally read-only.

`status` (cli.py) → `status_snapshot` (runner.py) → `reconcile()` →
`_reconcile_plan_manifest` used to UNCONDITIONALLY write `plans/manifest.json`
(auto-import ``append_entry`` + orphan ``update_lifecycle``). So a mere
``phase-loop status --json`` could dirty a tracked file. This suite pins the fix:

- the read path leaves the worktree byte-clean (git status empty before/after),
- the guarantee is *structural* — the writers are skipped by construction, not by
  a caller remembering to (proven by making the writers raise), and
- the write-intent path (``reconcile(read_only=False)``) is unchanged.

It also carries the #62 dedup verification (Assumption 2 of phase-plans-v9): a
committed ("accepted") planner entry + a real reconcile must NOT re-append a
duplicate ``imported`` row for the same phase-plan file+phase — and that this is
load-bearing (removing the #46 file+phase dedup re-introduces the duplicate).
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import phase_loop_runtime.plan_manifest as pm
from phase_loop_runtime import reconcile as reconcile_mod
from phase_loop_runtime.cli import main as cli_main
from phase_loop_runtime.plan_manifest import DotfilesPlanEntry, append_entry, read_manifest
from phase_loop_runtime.reconcile import reconcile
from phase_loop_runtime.runner import status_snapshot

from phase_loop_runtime.state import write_state

from phase_loop_test_utils import make_repo, provenanced_state, write_phase_plan


def _git_porcelain(repo: Path) -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, check=True, text=True, capture_output=True
    ).stdout


def _seed_import_prone_repo(td: Path) -> tuple[Path, Path]:
    """A committed, byte-clean repo whose reconcile has a *pending auto-import*:
    a phase-plan file for RUNNER exists on disk but no manifest entry represents
    it, so the write path would ``append_entry`` a new ``imported`` row."""
    repo = make_repo(td)
    roadmap = repo / "specs" / "phase-plans-v1.md"
    write_phase_plan(repo, "RUNNER", roadmap)
    # An (otherwise empty) VALID manifest so _reconcile_plan_manifest does not
    # early-return: the RUNNER phase-plan file on disk has no manifest entry, so
    # the write path will append_entry a synthetic `v1-RUNNER` import.
    pm._write_manifest(repo, pm.DotfilesPlanManifest(plans=()))
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "seed import-prone manifest"],
        cwd=repo, check=True, stdout=subprocess.DEVNULL,
    )
    assert _git_porcelain(repo) == "", "fixture must start byte-clean"
    return repo, roadmap


class StatusReadOnly62Test(unittest.TestCase):
    def test_write_mode_reconcile_appends_import_and_dirties_manifest(self):
        # Proves the fixture is LIVE: without read-only, reconcile mutates the
        # tracked manifest (so the read-only assertions below are not vacuous).
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = _seed_import_prone_repo(Path(td))
            before = (repo / "plans" / "manifest.json").read_bytes()
            reconcile(repo, roadmap, read_only=False)
            after = (repo / "plans" / "manifest.json").read_bytes()
            self.assertNotEqual(before, after, "write-mode reconcile should append the auto-import")
            self.assertNotEqual(_git_porcelain(repo), "", "write-mode reconcile should dirty the tree")
            slugs = {entry.slug for entry in read_manifest(repo).plans}
            self.assertIn("v1-RUNNER", slugs)

    def test_read_only_reconcile_leaves_manifest_byte_clean(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = _seed_import_prone_repo(Path(td))
            before = (repo / "plans" / "manifest.json").read_bytes()
            reconcile(repo, roadmap, read_only=True)
            after = (repo / "plans" / "manifest.json").read_bytes()
            self.assertEqual(before, after, "read-only reconcile must not write the manifest")
            self.assertEqual(_git_porcelain(repo), "", "read-only reconcile must leave the tree byte-clean")

    def test_status_snapshot_is_read_only_by_default(self):
        # The runner status entry point defaults to read-only by construction.
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = _seed_import_prone_repo(Path(td))
            before = (repo / "plans" / "manifest.json").read_bytes()
            status_snapshot(repo, roadmap)
            after = (repo / "plans" / "manifest.json").read_bytes()
            self.assertEqual(before, after, "status_snapshot must not write the manifest")
            self.assertEqual(_git_porcelain(repo), "", "status_snapshot must leave the tree byte-clean")

    def test_cli_status_leaves_tree_byte_clean(self):
        # End-to-end exit criterion: `phase-loop status` on a dirty-manifest-prone
        # repo leaves `git status` empty before AND after.
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = _seed_import_prone_repo(Path(td))
            self.assertEqual(_git_porcelain(repo), "")
            for as_json in (False, True):
                argv = ["status", "--repo", str(repo), "--roadmap", str(roadmap)]
                if as_json:
                    argv.append("--json")
                rc = cli_main(argv)
                self.assertEqual(rc, 0)
            self.assertEqual(
                _git_porcelain(repo), "", "`phase-loop status` must not dirty any tracked file"
            )

    def test_read_only_skips_writers_by_construction(self):
        # Structural proof: the read-only path never CALLS the manifest writers
        # (skipped by construction, not by a caller remembering to). The SAME
        # fixture does call a writer on the write path (so this is not vacuous).
        # (append_entry is wrapped in try/except in the writer, so we assert on
        # call counts rather than propagation.)
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = _seed_import_prone_repo(Path(td))

            with unittest.mock.patch.object(pm, "append_entry") as append_ro, \
                 unittest.mock.patch.object(pm, "update_lifecycle") as lifecycle_ro:
                reconcile(repo, roadmap, read_only=True)
                append_ro.assert_not_called()
                lifecycle_ro.assert_not_called()

            with unittest.mock.patch.object(pm, "append_entry") as append_rw:
                reconcile(repo, roadmap, read_only=False)
                self.assertTrue(
                    append_rw.called, "write-mode reconcile must call the manifest writer on this fixture"
                )


class Dedup62VerificationTest(unittest.TestCase):
    """Assumption 2: the #46 (file, phase_alias) dedup already fixes the
    duplicate-ACCEPT drift, so #62 keeps it. If it did not hold, this is the
    fail-loud lane."""

    def _seed_committed_accept(self, td: Path) -> tuple[Path, Path]:
        repo = make_repo(td)
        roadmap = repo / "specs" / "phase-plans-v1.md"
        plan = write_phase_plan(repo, "RUNNER", roadmap)
        rel = plan.relative_to(repo).as_posix()
        # A committed, ACCEPTED planner entry whose slug is the file STEM
        # (`phase-plan-v1-RUNNER`) — distinct from the synthetic import slug
        # (`v1-RUNNER`) that import_existing_phase_plans derives for the same file.
        append_entry(
            repo,
            DotfilesPlanEntry(
                slug="phase-plan-v1-RUNNER",
                file=rel,
                type="phase",
                status="complete",
                created_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-01T00:00:00Z",
                owner_skill="claude-plan-phase",
                phase_alias="RUNNER",
            ),
        )
        return repo, roadmap

    def test_import_slug_genuinely_collides(self):
        # Non-vacuity guard: the real importer derives a DIFFERENT slug for the
        # same file+phase, so only the file+phase dedup (not slug dedup) can stop
        # the duplicate.
        with tempfile.TemporaryDirectory() as td:
            repo, _roadmap = self._seed_committed_accept(Path(td))
            imported = pm.import_existing_phase_plans(repo)
            slugs = {e.slug for e in imported.plans}
            self.assertIn("v1-RUNNER", slugs)
            self.assertNotIn("phase-plan-v1-RUNNER", slugs)

    def test_committed_accept_reconcile_adds_no_duplicate(self):
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = self._seed_committed_accept(Path(td))
            reconcile(repo, roadmap, read_only=False)
            plans = read_manifest(repo).plans
            runner_entries = [
                e for e in plans
                if (e.phase_alias or "").upper() == "RUNNER" and str(e.file).endswith("phase-plan-v1-RUNNER.md")
            ]
            self.assertEqual(
                len(runner_entries), 1,
                f"#46 dedup must keep exactly one entry for the file+phase; got {[e.slug for e in runner_entries]}",
            )
            self.assertNotIn("v1-RUNNER", {e.slug for e in runner_entries})

    def test_dedup_is_load_bearing(self):
        # Remove the file+phase dedup (revert to slug-only keying) and the
        # duplicate reappears — proving the assertion above is not trivially green.
        with tempfile.TemporaryDirectory() as td:
            repo, roadmap = self._seed_committed_accept(Path(td))
            with unittest.mock.patch.object(
                reconcile_mod, "_manifest_file_phase_key", lambda entry: (entry.slug, "")
            ):
                reconcile(repo, roadmap, read_only=False)
            slugs = [e.slug for e in read_manifest(repo).plans]
            self.assertIn(
                "v1-RUNNER", slugs,
                "without the #46 file+phase dedup, reconcile SHOULD re-append the duplicate import",
            )


class OrphanRenamedPlanParity162Test(unittest.TestCase):
    """agent-harness#162 (LEGACY / CLEANSHIP P7) — read/write parity on an
    orphaned-entry + renamed-plan repo.

    The #162 follow-up (grok CR of READONLY #62) hypothesized that a read-only
    ``reconcile()`` over a manifest entry whose plan file was RENAMED (a stale
    live-missing entry + a regex-reachable renamed plan, phase ``planned`` in state)
    would DIVERGE from the write-intent path — a DUPLICATE ``manifest_plan_file_missing``
    warning plus the classifier-default phase instead of ``planned``.

    This does NOT reproduce on the current base, and this test pins that as a
    regression guard. The reason is structural, not a fix that made them match: the
    ``manifest_plan_file_missing`` branch in ``discovery.manifest_plan_artifact`` is
    UNREACHABLE — a missing-file entry fails PER-ENTRY validation (its existence
    check), so ``_phase_manifest_entries`` excludes it (IF-0-MANIFEST-1 / #164:
    per-entry validation replaced the old all-or-nothing validate gate). Here the
    stale entry is the ONLY entry, so both read and write mode still see NO manifest
    entries and fall through to the regex-reachable renamed plan, emitting exactly
    the one orphan-loop warning (from reconcile's independent ``read_manifest`` loop,
    not the discovery filter). If a future change breaks the per-entry exclusion or
    the orphan logic, this test fails and forces the read/write views back into
    agreement.
    """

    def _build(self, td: Path, status: str) -> tuple[Path, Path]:
        repo = make_repo(td)
        roadmap = repo / "specs" / "phase-plans-v1.md"
        # The renamed (current) plan exists on disk and is regex-reachable for RUNNER.
        write_phase_plan(repo, "RUNNER", roadmap)
        # A stale manifest entry points at a now-MISSING (old) plan file for the same
        # phase, non-orphaned.
        append_entry(
            repo,
            DotfilesPlanEntry(
                slug="v1-RUNNER-old",
                file="plans/phase-plan-v1-RUNNER-old.md",
                type="phase",
                status=status,
                created_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-01T00:00:00Z",
                owner_skill="claude-plan-phase",
                phase_alias="RUNNER",
            ),
        )
        # The state snapshot has RUNNER planned (load-bearing: it is what makes the
        # write path resolve RUNNER to planned via the provenance-matched override).
        write_state(repo, provenanced_state(repo, roadmap, {"RUNNER": "planned"}))
        return repo, roadmap

    @staticmethod
    def _warns(snapshot) -> list[tuple[str, str]]:
        return sorted((w.get("reason"), w.get("phase")) for w in snapshot.ledger_warnings)

    def test_read_matches_write_across_statuses(self):
        # The EXACT expected warning content per status (not just read==write list
        # equality — CR item 7: a bare equality could silently lose the warning on both
        # sides and still pass). The orphan-detection loop emits ONE
        # manifest_plan_file_missing warning for a live-missing NON-terminal entry
        # (committed/executing) in BOTH read and write mode; a `completed` entry is
        # skipped by that loop, so NO such warning fires — on both sides.
        expected_warns = {
            "committed": [("manifest_plan_file_missing", "RUNNER")],
            "executing": [("manifest_plan_file_missing", "RUNNER")],
            "completed": [],
        }
        for status in ("committed", "executing", "completed"):
            with tempfile.TemporaryDirectory() as td:
                repo, roadmap = self._build(Path(td), status)
                write = reconcile(repo, roadmap, read_only=False)
            with tempfile.TemporaryDirectory() as td:
                repo, roadmap = self._build(Path(td), status)
                read = reconcile(repo, roadmap, read_only=True)
            self.assertEqual(
                dict(read.phases), dict(write.phases),
                f"[status={status}] read-mode phases must match the write path",
            )
            # Assert the CONCRETE warning set on BOTH sides (non-vacuous): the write
            # path is the source of truth and read must match it exactly.
            self.assertEqual(
                self._warns(write), expected_warns[status],
                f"[status={status}] write-mode warnings must be exactly {expected_warns[status]}",
            )
            self.assertEqual(
                self._warns(read), expected_warns[status],
                f"[status={status}] read-mode warnings must equal the write path (no duplicate, "
                "no dropped manifest_plan_file_missing)",
            )
            # The concrete converged view: RUNNER resolves to planned (via the
            # regex-reachable renamed plan), not the classifier default.
            self.assertEqual(read.phases.get("RUNNER"), "planned")
            self.assertEqual(write.phases.get("RUNNER"), "planned")


if __name__ == "__main__":
    unittest.main()
