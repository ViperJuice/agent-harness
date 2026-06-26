from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from phase_loop_runtime.cli import main
from phase_loop_runtime.evidence_audit import (
    detect_boilerplate_text,
    detect_missing_references,
    detect_uniform_numeric,
    run_evidence_audit,
)


import pytest

# TESTDECOUPLE (runtime-core): the evidence-audit calibration fixtures are the
# runtime's OWN test fixtures; they ship as _test_fixtures package-data and resolve
# via importlib.resources, so the detector tests run standalone. FIXTURE_ROOT is the
# bundled tree. Only test_calibration_dry_run (runs the dotfiles-tree
# tests/calibrate_tier3.py via cwd=ROOT) stays integration.
from _contract_docs import fixture_path

ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = fixture_path("evidence-audit-calibration")


def _init_git_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)


class EvidenceAuditDetectorTuningTest(unittest.TestCase):
    # TESTDECOUPLE: integration — resolves the known-real fixtures' path-shaped
    # strings (e.g. "vendor/phase-loop-runtime/src/.../emit_phase_closeout.baml")
    # against the repo root ROOT. The assertion (no missing refs) only holds when
    # those paths exist under a real dotfiles checkout; standalone ROOT overshoots
    # to a marker-less dir and they correctly flag as missing. The detector's
    # behavior under a real tree is the load-bearing thing under test, so this stays
    # in-tree. (The sibling fixture detectors below run standalone from package-data.)
    @pytest.mark.dotfiles_integration
    def test_strict_missing_references_rejects_known_real_path_shaped_strings(self):
        known_real = FIXTURE_ROOT / "known-real"
        json_files = sorted(known_real.rglob("*.json"))

        findings = []
        for path in json_files:
            findings.extend(detect_missing_references(path, ROOT, strict=True))

        self.assertEqual(findings, [])

    def test_strict_missing_references_detects_known_fake_missing_artifacts(self):
        fixture = FIXTURE_ROOT / "known-fake" / "fake-missing-references"
        manifest = fixture / "claimed-results.json"

        findings = detect_missing_references(manifest, fixture, strict=True)

        self.assertEqual({finding.missing_path for finding in findings}, {
            "evidence/home.png",
            "evidence/settings.png",
            "evidence/reports.png",
        })

    def test_loose_missing_references_preserves_path_shaped_string_scan(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            artifact = repo / "artifact.json"
            artifact.write_text(json.dumps({"command": "tests/test_missing.py"}), encoding="utf-8")

            strict_findings = detect_missing_references(artifact, repo, strict=True)
            loose_findings = detect_missing_references(artifact, repo, strict=False)

            self.assertEqual(strict_findings, [])
            self.assertEqual([finding.missing_path for finding in loose_findings], ["tests/test_missing.py"])

    def test_boilerplate_flags_known_fake_templated_prose_fixture(self):
        fixture = FIXTURE_ROOT / "known-fake" / "fake-templated-prose"

        findings = detect_boilerplate_text([fixture / "findings.md"])

        self.assertEqual(len(findings), 1)
        self.assertGreaterEqual(findings[0].shared_token_count, 8)

    def test_boilerplate_does_not_flag_known_real_fixture_group(self):
        known_real_files = [
            path
            for path in (FIXTURE_ROOT / "known-real").rglob("*")
            if path.is_file() and path.suffix.lower() in {".json", ".jsonl", ".md", ".txt", ".ppm"}
        ]

        findings = detect_boilerplate_text(known_real_files)

        self.assertEqual(findings, [])

    def test_borderline_fixture_remains_uncertain_not_tier1_failure(self):
        fixture = FIXTURE_ROOT / "borderline" / "borderline-mixed-signals"

        result = run_evidence_audit(fixture, dirty_only=False, tier2_enabled=True)
        manifest = json.loads((fixture / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(result.duplicate_content, [])
        self.assertEqual(result.uniform_numeric, [])
        self.assertEqual(result.missing_references, [])
        self.assertEqual(manifest["expected_verdict_class"], "uncertain")

    def test_full_tree_loose_cli_selects_liberal_missing_reference_mode(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "artifact.json").write_text(
                json.dumps({"command": "tests/test_missing.py"}),
                encoding="utf-8",
            )
            _init_git_repo(repo)

            strict_stdout = io.StringIO()
            with contextlib.redirect_stdout(strict_stdout):
                strict_code = main(["evidence-audit", "--repo", str(repo), "--full-tree", "--json"])
            strict_payload = json.loads(strict_stdout.getvalue())

            loose_stdout = io.StringIO()
            with contextlib.redirect_stdout(loose_stdout):
                loose_code = main(["evidence-audit", "--repo", str(repo), "--full-tree-loose", "--json"])
            loose_payload = json.loads(loose_stdout.getvalue())

            self.assertEqual(strict_code, 0)
            self.assertEqual(strict_payload["missing_references"], [])
            self.assertEqual(loose_code, 5)
            self.assertEqual(loose_payload["missing_references"][0]["missing_path"], "tests/test_missing.py")

    def test_full_tree_loose_cli_help_documents_flag(self):
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout), self.assertRaises(SystemExit) as cm:
            main(["evidence-audit", "--help"])

        self.assertEqual(cm.exception.code, 0)
        self.assertIn("--full-tree-loose", stdout.getvalue())
        self.assertIn("--full-tree", stdout.getvalue())

    # TESTDECOUPLE: integration — runs the dotfiles-tree script tests/calibrate_tier3.py
    # with cwd=ROOT (the dotfiles checkout root); that script is not part of the package.
    @pytest.mark.dotfiles_integration
    def test_calibration_dry_run_remains_offline_compatible(self):
        result = subprocess.run(
            ["python3", "tests/calibrate_tier3.py", "--dry-run"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )

        self.assertIn("calibration", result.stdout.lower())

    def test_uniform_detector_still_covers_known_fake_uniform_scores(self):
        fixture = FIXTURE_ROOT / "known-fake" / "fake-uniform-scores" / "scores.json"

        findings = detect_uniform_numeric(fixture)

        self.assertTrue(findings)


if __name__ == "__main__":
    unittest.main()
