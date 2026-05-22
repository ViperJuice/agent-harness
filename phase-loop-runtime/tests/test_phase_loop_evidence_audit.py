"""Tests for evidence_audit detectors + CLI subcommand.

Codifies the v20 spot-check protocol catching fake-evidence patterns
surfaced in the regen VISUALMATCH 2026-05-22 incident.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.evidence_audit import (
    DuplicateContentFinding,
    MissingReferenceFinding,
    UniformNumericFinding,
    detect_duplicate_content,
    detect_missing_references,
    detect_uniform_numeric,
    run_evidence_audit,
)


def _init_git_repo(repo: Path) -> None:
    """Minimal git repo + initial commit so dirty-paths detection works."""
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    (repo / ".gitkeep").write_text("")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)


class DuplicateContentTest(unittest.TestCase):
    def test_flags_n_copies_of_same_file(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            for i in range(5):
                (d / f"prototype-{i}.png").write_bytes(b"placeholder-bytes")
            (d / "real.png").write_bytes(b"different-content-here")
            findings = detect_duplicate_content(list(d.glob("*.png")), min_duplicates=3)
            self.assertEqual(len(findings), 1)
            self.assertEqual(len(findings[0].paths), 5)
            self.assertEqual(findings[0].size_bytes, len(b"placeholder-bytes"))

    def test_below_threshold_does_not_flag(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            for i in range(2):
                (d / f"dup-{i}.txt").write_text("same")
            findings = detect_duplicate_content(list(d.glob("*.txt")), min_duplicates=3)
            self.assertEqual(findings, [])

    def test_distinct_files_not_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            for i in range(5):
                (d / f"distinct-{i}.png").write_bytes(f"content-{i}".encode())
            findings = detect_duplicate_content(list(d.glob("*.png")), min_duplicates=3)
            self.assertEqual(findings, [])


class UniformNumericTest(unittest.TestCase):
    def test_flags_array_of_identical_numbers(self):
        with tempfile.TemporaryDirectory() as td:
            jp = Path(td) / "scores.json"
            jp.write_text(json.dumps({"similarities": [0.999999] * 19}))
            findings = detect_uniform_numeric(jp)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].array_length, 19)
            self.assertEqual(findings[0].sample_value, 0.999999)

    def test_flags_array_of_objects_with_uniform_field(self):
        """Regen VISUALMATCH attempt #1 pattern: list of routes each with similarity=0.999999."""
        with tempfile.TemporaryDirectory() as td:
            jp = Path(td) / "closeout-integrity.md.json"
            routes = [
                {"role": f"role-{i}", "similarity": 0.999999, "pixelsDifferent": 1}
                for i in range(19)
            ]
            jp.write_text(json.dumps({"routes": routes}))
            findings = detect_uniform_numeric(jp)
            # Both 'similarity' and 'pixelsDifferent' fields are uniform across 19 entries
            pointers = {f.json_pointer for f in findings}
            self.assertIn("$.routes[*].similarity", pointers)
            self.assertIn("$.routes[*].pixelsDifferent", pointers)

    def test_does_not_flag_varied_values(self):
        with tempfile.TemporaryDirectory() as td:
            jp = Path(td) / "scores.json"
            jp.write_text(json.dumps({"similarities": [0.95, 0.87, 0.99, 0.72, 0.81]}))
            findings = detect_uniform_numeric(jp)
            self.assertEqual(findings, [])

    def test_does_not_flag_short_arrays(self):
        """3-element arrays with identical values are common in legit code; default min_array_length=4."""
        with tempfile.TemporaryDirectory() as td:
            jp = Path(td) / "scores.json"
            jp.write_text(json.dumps({"axes": [0, 0, 0]}))
            findings = detect_uniform_numeric(jp)
            self.assertEqual(findings, [])


class MissingReferencesTest(unittest.TestCase):
    def test_flags_cited_path_that_does_not_exist(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "real-file.png").write_bytes(b"x")
            manifest = d / "manifest.json"
            manifest.write_text(json.dumps({
                "prototype_paths": [
                    "real-file.png",  # exists
                    "missing/fake-prototype-1.png",  # does not exist
                    "missing/fake-prototype-2.png",  # does not exist
                ],
            }))
            findings = detect_missing_references(manifest, repo=d)
            missing_paths = {f.missing_path for f in findings}
            self.assertNotIn("real-file.png", missing_paths)
            self.assertIn("missing/fake-prototype-1.png", missing_paths)
            self.assertIn("missing/fake-prototype-2.png", missing_paths)

    def test_does_not_flag_urls(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "manifest.json").write_text(json.dumps({
                "url": "https://example.com/some/path.html",
            }))
            findings = detect_missing_references(d / "manifest.json", repo=d)
            self.assertEqual(findings, [])


class FullAuditIntegrationTest(unittest.TestCase):
    def test_dirty_only_catches_visualmatch_attempt1_fingerprint(self):
        """Exact regen VISUALMATCH attempt-#1 fingerprint as an end-to-end fixture."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _init_git_repo(repo)
            screenshots = repo / "iter3-screenshots"
            screenshots.mkdir()
            # 19 "distinct" prototype PNGs that are all the same bytes (placeholder).
            placeholder = b"PNG-PLACEHOLDER-BYTES" * 1000
            for i in range(19):
                (screenshots / f"prototype-route-{i}.png").write_bytes(placeholder)
            # The closeout JSON with uniform similarity scores
            (repo / "closeout-integrity.md.json").write_text(json.dumps({
                "routes": [
                    {"role": f"role-{i}", "similarity": 0.999999, "pixelsDifferent": 1}
                    for i in range(19)
                ],
            }))

            result = run_evidence_audit(repo, dirty_only=True)
            self.assertFalse(result.is_clean())
            self.assertEqual(len(result.duplicate_content), 1)
            self.assertEqual(len(result.duplicate_content[0].paths), 19)
            self.assertGreaterEqual(len(result.uniform_numeric), 2)  # similarity + pixelsDifferent

    def test_legitimate_evidence_passes(self):
        """Attempt #2-style: varied scores + varied bytes → clean."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _init_git_repo(repo)
            screenshots = repo / "iter3-screenshots"
            screenshots.mkdir()
            for i in range(8):
                (screenshots / f"live-route-{i}.png").write_bytes(f"distinct-content-{i}".encode() * 50)
            (repo / "closeout-integrity.md.json").write_text(json.dumps({
                "routes": [
                    {"role": "admin", "similarity": 0.95, "pixelsDifferent": 12000},
                    {"role": "coachee", "similarity": 0.87, "pixelsDifferent": 50000},
                    {"role": "coach", "similarity": 0.99, "pixelsDifferent": 200},
                    {"role": "landing", "similarity": 0.72, "pixelsDifferent": 800000},
                    {"role": "executive", "similarity": 0.81, "pixelsDifferent": 150000},
                ],
            }))
            result = run_evidence_audit(repo, dirty_only=True)
            self.assertTrue(result.is_clean(), msg=f"expected clean, got {result.to_json()}")


if __name__ == "__main__":
    unittest.main()
