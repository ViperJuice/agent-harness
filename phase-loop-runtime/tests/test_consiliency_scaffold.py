"""CS-0.5 -- `.consiliency/` scaffolder (first-writer)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from consiliency_contract import load_schema
from phase_loop_test_utils import make_repo
from phase_loop_runtime.consiliency_layout import (
    ARCHETYPE_IDS,
    find_consiliency_manifest,
    manifest_path,
)
from phase_loop_runtime.consiliency_scaffold import ScaffoldError, scaffold

BIN = (sys.executable, "-m", "phase_loop_runtime.cli")


class ConsiliencyScaffoldTest(unittest.TestCase):
    def _validate_manifest(self, repo: Path) -> dict:
        manifest = json.loads(manifest_path(repo).read_text(encoding="utf-8"))
        Draft202012Validator(load_schema("manifest")).validate(manifest)
        return manifest

    def test_baseline_only_manifest_is_schema_valid(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            result = scaffold(repo, mode="baseline-only")
            self.assertFalse(result.already_present)
            manifest = self._validate_manifest(repo)
            self.assertEqual(manifest["declaration"], {"mode": "baseline-only"})
            self.assertIn(".consiliency/status.json", [d["path"] for d in manifest["documents"]])

    def test_each_archetype_scaffolds_a_schema_valid_manifest(self):
        for archetype in ARCHETYPE_IDS:
            with self.subTest(archetype=archetype):
                with tempfile.TemporaryDirectory() as td:
                    repo = make_repo(Path(td))
                    result = scaffold(repo, mode="archetyped", archetypes=(archetype,))
                    self.assertTrue(find_consiliency_manifest(repo))
                    manifest = self._validate_manifest(repo)
                    self.assertEqual(manifest["declaration"]["archetypes"], [archetype])
                    self.assertTrue(result.created_paths, "expected at least one scaffolded stub")

    def test_baseline_only_and_archetype_do_not_double_declare_baseline_docs(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            scaffold(repo, mode="archetyped", archetypes=("service",))
            manifest = self._validate_manifest(repo)
            ids = [d["id"] for d in manifest["documents"]]
            self.assertEqual(len(ids), len(set(ids)), "duplicate document ids in composed manifest")
            self.assertIn("readme", ids)  # baseline
            self.assertIn("runbook", ids)  # service archetype extra

    def test_glossary_and_other_l0_stub_docs_are_presence_stubs_with_authored_zone(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            scaffold(repo, mode="baseline-only")
            glossary = (repo / ".consiliency" / "docs" / "glossary.md").read_text(encoding="utf-8")
            self.assertIn("maturity=presence-only", glossary)
            self.assertIn("authored-zone:start", glossary)
            self.assertIn("TODO: author", glossary)

    def test_readme_and_license_are_never_fabricated(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            # make_repo already writes README.md -- remove it so absence is real.
            (repo / "README.md").unlink()
            # consiliency-contract 0.3.0 moved `license` off the baseline
            # (and off every archetype) onto the `public` modifier -- request
            # it explicitly so this test still exercises a real LICENSE gap.
            result = scaffold(repo, mode="archetyped", archetypes=("library",), modifiers=("public",))
            self.assertIn("README.md", result.declared_missing_paths)
            self.assertIn("LICENSE", result.declared_missing_paths)
            self.assertFalse((repo / "README.md").exists())
            self.assertFalse((repo / "LICENSE").exists())

    def test_existing_readme_is_referenced_not_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            original = (repo / "README.md").read_text(encoding="utf-8")
            result = scaffold(repo, mode="baseline-only")
            self.assertIn("README.md", result.referenced_paths)
            self.assertEqual((repo / "README.md").read_text(encoding="utf-8"), original)

    def test_rerun_is_idempotent_and_never_overwrites_hand_edited_stubs(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            scaffold(repo, mode="baseline-only")
            glossary_path = repo / ".consiliency" / "docs" / "glossary.md"
            glossary_path.write_text("# Real, human-authored glossary\n", encoding="utf-8")
            result = scaffold(repo, mode="baseline-only")
            self.assertTrue(result.already_present)
            self.assertEqual(glossary_path.read_text(encoding="utf-8"), "# Real, human-authored glossary\n")

    def test_never_touches_phase_loop_or_pipeline_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            phase_loop_dir = repo / ".phase-loop"
            phase_loop_dir.mkdir()
            sentinel = phase_loop_dir / "state.json"
            sentinel.write_text('{"sentinel": true}\n', encoding="utf-8")
            pipeline_dir = repo / ".pipeline"
            pipeline_dir.mkdir()
            (pipeline_dir / "marker.txt").write_text("do-not-touch\n", encoding="utf-8")

            scaffold(repo, mode="archetyped", archetypes=("tooling-meta",))

            self.assertEqual(sentinel.read_text(encoding="utf-8"), '{"sentinel": true}\n')
            self.assertEqual((pipeline_dir / "marker.txt").read_text(encoding="utf-8"), "do-not-touch\n")

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            result = scaffold(repo, mode="baseline-only", dry_run=True)
            self.assertTrue(result.dry_run)
            self.assertFalse((repo / ".consiliency").exists())
            self.assertTrue(result.created_paths)

    def test_baseline_only_rejects_archetype_args(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            with self.assertRaises(ScaffoldError):
                scaffold(repo, mode="baseline-only", archetypes=("product",))

    def test_archetyped_requires_at_least_one_archetype(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            with self.assertRaises(ScaffoldError):
                scaffold(repo, mode="archetyped")

    def test_unknown_archetype_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            with self.assertRaises(ScaffoldError):
                scaffold(repo, mode="archetyped", archetypes=("not-a-real-archetype",))


class ConsiliencyScaffoldCLITest(unittest.TestCase):
    def test_cli_scaffolds_and_second_invocation_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            first = subprocess.run(
                [*BIN, "consiliency-scaffold", "--repo", str(repo), "--archetype", "library", "--json"],
                text=True,
                capture_output=True,
                check=True,
            )
            payload = json.loads(first.stdout)
            self.assertFalse(payload["already_present"])
            self.assertTrue((repo / ".consiliency" / "manifest.json").is_file())

            second = subprocess.run(
                [*BIN, "consiliency-scaffold", "--repo", str(repo), "--archetype", "library", "--json"],
                text=True,
                capture_output=True,
                check=True,
            )
            payload2 = json.loads(second.stdout)
            self.assertTrue(payload2["already_present"])
            self.assertEqual(payload2["created_paths"], [])

    def test_cli_rejects_baseline_only_with_archetype(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            result = subprocess.run(
                [*BIN, "consiliency-scaffold", "--repo", str(repo), "--archetype", "library", "--baseline-only"],
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
