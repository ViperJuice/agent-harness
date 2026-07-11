"""agent-harness#164 / IF-0-MANIFEST-1 — per-entry manifest validation.

A single stale/renamed/missing-file manifest entry must be skipped (treated
orphaned), NOT invalidate the whole manifest and silently degrade discovery back
to regex. These tests drive the real discovery *consumption* path
(``manifest_backed_roadmap`` / ``find_plan_artifact`` via ``_phase_manifest_entries``),
not just the validator's result shape — the pre-fix bug lived in consumption.

Unmarked (NOT ``dotfiles_integration``) so it runs under CI's
``pytest -m "not dotfiles_integration"`` gate; it needs only a git repo + a
manifest, no fleet/dotfiles tree.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.discovery import (
    find_plan_artifact,
    manifest_backed_roadmap,
    roadmap_fingerprint,
)
from phase_loop_runtime.plan_manifest import (
    DotfilesPlanEntry,
    DotfilesPlanRef,
    append_entry,
    validate_manifest,
)

from phase_loop_test_utils import make_repo, write_phase_plan


def _phase_entry(
    slug: str,
    file: str,
    phase_alias: str,
    *,
    roadmap_rel: str | None = None,
    status: str = "committed",
) -> DotfilesPlanEntry:
    roadmap_ref = (
        DotfilesPlanRef(slug=Path(roadmap_rel).stem, file=roadmap_rel, type="phase", status=status)
        if roadmap_rel
        else None
    )
    return DotfilesPlanEntry(
        slug=slug,
        file=file,
        type="phase",
        status=status,
        created_at="2026-05-30T00:00:00Z",
        updated_at="2026-05-30T00:00:00Z",
        owner_skill="codex-plan-phase",
        roadmap_ref=roadmap_ref,
        phase_alias=phase_alias,
    )


class ManifestPerEntryValidation164Test(unittest.TestCase):
    def test_manifest_backed_roadmap_survives_one_bad_entry(self):
        # Before the fix the missing-file entry flipped ``validate_manifest(...).valid``
        # to False, so ``_phase_manifest_entries`` returned () and discovery silently
        # degraded to regex (here: no manifest roadmap -> None). After the fix the bad
        # entry is skipped and the valid entry's roadmap still resolves.
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            good_roadmap = repo / "specs" / "phase-plans-v1.md"
            # A second, real roadmap the bad entry points at. If a naive over-fix
            # dropped validation entirely (rather than per-entry), the bad entry would
            # leak a SECOND candidate and ``manifest_backed_roadmap`` would return None.
            other_roadmap = repo / "specs" / "phase-plans-v2.md"
            other_roadmap.write_text("# Roadmap\n\n### Phase 0 - Gone (GONE)\n", encoding="utf-8")
            plan = write_phase_plan(repo, "RUNNER", good_roadmap)
            append_entry(
                repo,
                _phase_entry(
                    "manifest-runner",
                    plan.relative_to(repo).as_posix(),
                    "RUNNER",
                    roadmap_rel="specs/phase-plans-v1.md",
                ),
            )
            # Bad entry: its plan file does not exist (renamed/removed on disk).
            append_entry(
                repo,
                _phase_entry(
                    "manifest-gone",
                    "plans/phase-plan-v1-GONE.md",
                    "GONE",
                    roadmap_rel="specs/phase-plans-v2.md",
                ),
            )

            self.assertEqual(manifest_backed_roadmap(repo), good_roadmap.resolve())

    def test_find_plan_artifact_resolves_valid_entry_despite_bad_sibling(self):
        # Fuller path through ``plan_matches_roadmap``. The valid entry points at a
        # non-regex-reachable custom filename, so before the per-entry fix (whole
        # manifest invalidated by the bad sibling) discovery found nothing and
        # returned None; after the fix it resolves the valid entry's plan.
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = repo / "plans" / "custom-runner-plan.md"
            plan.write_text(
                "---\n"
                "phase_loop_plan_version: 1\n"
                "phase: RUNNER\n"
                "roadmap: specs/phase-plans-v1.md\n"
                f"roadmap_sha256: {roadmap_fingerprint(roadmap)}\n"
                "---\n"
                "# RUNNER\n",
                encoding="utf-8",
            )
            append_entry(
                repo,
                _phase_entry(
                    "manifest-runner",
                    "plans/custom-runner-plan.md",
                    "RUNNER",
                    roadmap_rel="specs/phase-plans-v1.md",
                ),
            )
            # Bad sibling for a DIFFERENT phase: missing file -> per-entry invalid.
            append_entry(
                repo,
                _phase_entry("manifest-access", "plans/phase-plan-v1-ACCESS.md", "ACCESS"),
            )

            self.assertEqual(find_plan_artifact(repo, "RUNNER", roadmap=roadmap), plan.resolve())

    def test_validate_manifest_reports_per_entry_verdicts(self):
        # Freeze IF-0-MANIFEST-1's shape: structural_valid is the whole-manifest
        # verdict; entries carries a per-entry verdict aligned by index; valid_indices
        # excludes only the bad entry. .valid / .errors remain the backward-compatible
        # aggregate.
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            append_entry(
                repo,
                _phase_entry(
                    "manifest-runner",
                    plan.relative_to(repo).as_posix(),
                    "RUNNER",
                    roadmap_rel="specs/phase-plans-v1.md",
                ),
            )
            append_entry(
                repo,
                _phase_entry("manifest-gone", "plans/phase-plan-v1-GONE.md", "GONE"),
            )

            result = validate_manifest(repo / "plans" / "manifest.json")
            # The manifest is structurally sound even though one entry is bad.
            self.assertTrue(result.structural_valid)
            # Backward-compatible aggregate still reports the whole-manifest verdict.
            self.assertFalse(result.valid)
            self.assertIn("file does not exist", "\n".join(result.errors))
            # Per-entry: exactly one entry is invalid, and it is the missing-file one.
            self.assertEqual(len(result.entries), 2)
            bad = [e for e in result.entries if not e.valid]
            self.assertEqual([e.slug for e in bad], ["manifest-gone"])
            good = [e for e in result.entries if e.valid]
            self.assertEqual([e.slug for e in good], ["manifest-runner"])
            # valid_indices excludes only the bad entry's index.
            self.assertEqual(result.valid_indices(), {good[0].index})

    def test_structural_failure_still_hides_whole_manifest(self):
        # A structural failure (schema_version) is NOT per-entry: the whole manifest
        # stays untrustworthy and discovery falls through (no manifest roadmap).
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            (repo / "plans").mkdir(exist_ok=True)
            (repo / "plans" / "manifest.json").write_text(
                '{"schema_version": 99, "plans": []}', encoding="utf-8"
            )
            result = validate_manifest(repo / "plans" / "manifest.json")
            self.assertFalse(result.structural_valid)
            self.assertFalse(result.valid)
            self.assertIsNone(manifest_backed_roadmap(repo))


if __name__ == "__main__":
    unittest.main()
