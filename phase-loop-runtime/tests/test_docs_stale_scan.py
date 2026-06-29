"""docs-freshness v4 P2 — stale-text scanner + its release-class audit wiring.

The scanner is pure (text in → findings out); these tests need only strings. The
last two exercise the docs_audit integration (a release-in-flight stale doc fails loud).
"""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from phase_loop_runtime import docs_audit, docs_stale_scan as s


class ScanTextTest(unittest.TestCase):
    def _codes(self, findings):
        return sorted(f.code for f in findings)

    def test_each_placeholder_flags(self):
        for txt in (
            "Status: recovery commit pending",
            "Message Board SHA: commit pending",
            "Tag result pending",
            "Install path: TBD",
            "Runtime docs coming soon",
            "TODO: write this",
        ):
            with self.subTest(txt=txt):
                hits = s.scan_text(txt)
                self.assertTrue(hits, f"expected a placeholder finding for {txt!r}")
                self.assertEqual(hits[0].code, "placeholder")

    def test_bare_prose_words_do_not_flag(self):
        # Calibration (dogfood): bare English "pending"/"placeholder" in prose is NOT a
        # stale marker — only template/evidence forms are. Prevents false positives on
        # legitimate docs (and on docs that *describe* the scanner).
        for txt in (
            "The verification result is pending review.",
            "This field is a placeholder for the real value.",
            "We mark it pending until the gate clears.",
        ):
            with self.subTest(txt=txt):
                self.assertEqual(s.scan_text(txt), [], f"false positive on {txt!r}")

    def test_placeholder_in_code_span_is_an_example_not_a_finding(self):
        # Documenting a placeholder (in backticks) must not flag.
        self.assertEqual(s.scan_text("placeholders like `recovery commit pending` are caught"), [])

    def test_changelog_old_versions_exempt(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "CHANGELOG.md").write_text("## v0.1.4\n- old\n## v0.1.5\n- new\n", encoding="utf-8")
            (repo / "README.md").write_text("Install the 0.1.4 package.\n", encoding="utf-8")
            res = s.scan_doc_paths(repo, ["CHANGELOG.md", "README.md"], current_version="0.1.5")
            self.assertNotIn("CHANGELOG.md", res)  # changelog history is exempt
            self.assertIn("README.md", res)         # a README claiming an old version is stale

    def test_clean_text_has_no_findings(self):
        self.assertEqual(s.scan_text("The runtime ships three packages and a CLI."), [])

    def test_stale_package_count_flags_when_inconsistent(self):
        hits = s.scan_text("This release publishes three packages.", expected_package_count=4)
        self.assertIn("stale_package_count", self._codes(hits))

    def test_package_count_consistent_does_not_flag(self):
        hits = s.scan_text("This release publishes three packages.", expected_package_count=3)
        self.assertNotIn("stale_package_count", self._codes(hits))

    def test_digit_package_count(self):
        hits = s.scan_text("Ships 3 packages now.", expected_package_count=5)
        self.assertIn("stale_package_count", self._codes(hits))

    def test_old_version_unlabeled_flags(self):
        hits = s.scan_text("Install the published 1.0.1 package set.", current_version="1.0.5")
        self.assertIn("unlabeled_old_version", self._codes(hits))

    def test_old_version_labeled_historical_does_not_flag(self):
        hits = s.scan_text("v0.1.0 (historical) is superseded.", current_version="0.1.5")
        self.assertNotIn("unlabeled_old_version", self._codes(hits))

    def test_current_version_not_flagged(self):
        hits = s.scan_text("Pin 1.0.5 in your install.", current_version="1.0.5")
        self.assertNotIn("unlabeled_old_version", self._codes(hits))

    def test_version_check_off_by_default(self):
        # No current_version → the old-version check is skipped entirely.
        self.assertEqual(
            self._codes(s.scan_text("Use 1.0.1 here.")), [],
        )

    def test_pure_deterministic(self):
        txt = "pending; ships two packages; pin 1.0.1"
        a = s.scan_text(txt, expected_package_count=3, current_version="1.0.5")
        b = s.scan_text(txt, expected_package_count=3, current_version="1.0.5")
        self.assertEqual([f.to_json() for f in a], [f.to_json() for f in b])

    def test_configurable_extra_placeholder(self):
        cfg = s.with_patterns([r"under construction"])
        self.assertTrue(s.scan_text("This section is under construction.", config=cfg))
        # default config does NOT know that phrase
        self.assertEqual(s.scan_text("This section is under construction."), [])

    def test_line_numbers(self):
        hits = s.scan_text("clean line\nrecovery commit pending\n")
        self.assertEqual(hits[0].line, 2)


class _Repo:
    def __init__(self, root: Path):
        self.root = root

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.root), *args], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def write(self, rel, body):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")


class AuditWiringTest(unittest.TestCase):
    """A release-in-flight whose changed doc still carries a placeholder must fail loud,
    even though the surface/decision contract (P1) would otherwise pass."""

    def _repo(self, td):
        r = _Repo(Path(td))
        r.git("init", "-q")
        r.git("config", "user.email", "t@t")
        r.git("config", "user.name", "t")
        r.write("pyproject.toml", 'version = "1.0.5"\n')
        r.write("CHANGELOG.md", "# Changelog\n\n## 1.0.4\n- old\n")
        r.git("add", "-A")
        r.git("commit", "-qm", "base")
        return r

    def test_release_with_stale_changed_doc_blocks(self):
        with TemporaryDirectory() as td:
            r = self._repo(td)
            # a release-class change (version bump) WITH the required CHANGELOG touched
            # (P1 satisfied) — but the CHANGELOG carries a placeholder → P2 fails loud.
            r.write("pyproject.toml", 'version = "1.0.6"\n')
            r.write("CHANGELOG.md", "# Changelog\n\n## 1.0.6\n- recovery commit pending\n")
            r.git("add", "-A")
            r.git("commit", "-qm", "release")
            report = docs_audit.run_audit(Path(td), base="HEAD~1")
            self.assertEqual(report.docs_freshness, "blocked")
            self.assertTrue(any(f["code"].startswith("stale_") for f in report.findings),
                            f"expected a stale finding, got {report.findings}")

    def test_release_with_clean_changed_doc_passes(self):
        with TemporaryDirectory() as td:
            r = self._repo(td)
            r.write("pyproject.toml", 'version = "1.0.6"\n')
            r.write("CHANGELOG.md", "# Changelog\n\n## 1.0.6\n- shipped the runtime cleanly\n")
            r.git("add", "-A")
            r.git("commit", "-qm", "release")
            report = docs_audit.run_audit(Path(td), base="HEAD~1")
            self.assertEqual(report.docs_freshness, "passed", report.findings)


if __name__ == "__main__":
    unittest.main()
