import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from phase_loop_runtime import reflection_sync as rs


REFLECTION = """\
# claude-plan-phase reflection — 2026-06-15T03:05:00Z

## Run context
- Skill: claude-plan-phase
- Timestamp: 2026-06-15T03:05:00Z
- Repo: 159800550286
- Branch: divorce-discovery-fix
- Commit: a1b2c3d
- Artifact: /home/someuser/projects/divorce/plans/phase-plan-v2-FOO.md

## What worked
- The plan resolver under /mnt/buildvol/code/dotfiles/scripts/x.py was clear.
- Wrote handoff to ~/.claude/skills/claude-plan-phase/handoffs cleanly.

## Improvements to SKILL.md
- Clarify step 3 about /home/jenner/projects/secretco/notes.md handling.
"""


class MachineIdTests(unittest.TestCase):
    def test_override_wins_and_is_slugged(self):
        os.environ["DOTFILES_MACHINE_ID"] = "Jesse's MacBook!!"
        try:
            self.assertEqual(rs.machine_id(), "Jesse-s-MacBook")
        finally:
            del os.environ["DOTFILES_MACHINE_ID"]

    def test_persisted_and_stable(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["XDG_CONFIG_HOME"] = d
            os.environ.pop("DOTFILES_MACHINE_ID", None)
            try:
                first = rs.machine_id()
                second = rs.machine_id()
                self.assertEqual(first, second)
                self.assertTrue((Path(d) / "dotfiles" / "machine-id").exists())
            finally:
                del os.environ["XDG_CONFIG_HOME"]


class RedactionTests(unittest.TestCase):
    def test_structured_fields_hashed_not_leaked(self):
        out = rs.redact_reflection_text(REFLECTION, machine="m1")
        self.assertNotIn("divorce-discovery-fix", out)
        self.assertNotIn("159800550286", out)
        self.assertNotIn("a1b2c3d", out)
        self.assertNotIn("projects/divorce", out)
        self.assertIn("<branch:", out)
        self.assertIn("<repo:", out)
        self.assertIn("<commit:", out)
        self.assertIn("<artifact:", out)

    def test_same_value_hashes_stably_for_correlation(self):
        a = rs.redact_reflection_text("- Branch: feature-x\n")
        b = rs.redact_reflection_text("- Branch: feature-x\n")
        c = rs.redact_reflection_text("- Branch: feature-y\n")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_body_paths_collapsed_username_stripped(self):
        out = rs.redact_reflection_text(REFLECTION, machine="m1")
        self.assertNotIn("/home/someuser", out)
        self.assertNotIn("/home/jenner", out)
        self.assertNotIn("secretco", out)
        # generic, instructive tails survive
        self.assertIn("<repo>/scripts/x.py", out)
        self.assertIn("~/.claude/skills/claude-plan-phase/handoffs", out)

    def test_idempotent(self):
        once = rs.redact_reflection_text(REFLECTION, machine="m1")
        twice = rs.redact_reflection_text(once, machine="m1")
        self.assertEqual(once, twice)

    def test_none_artifact_preserved(self):
        out = rs.redact_reflection_text("- Artifact: none\n")
        self.assertIn("Artifact: none", out)

    def test_provenance_header_added(self):
        out = rs.redact_reflection_text("# r\n", machine="testhost")
        self.assertTrue(out.startswith("<!-- reflection-sync: redacted; machine=testhost -->"))


class ExportTests(unittest.TestCase):
    def _make_reflection(self, root: Path, skill: str, rel: str, text: str) -> Path:
        p = root / skill / "reflections" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def test_export_namespaces_and_redacts_and_skips_archive(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            bundle = d / "bundle"
            dest = d / "store"
            repo = d / "repo"
            repo.mkdir()
            # one live reflection (nested layout) + one archived (must be skipped)
            self._make_reflection(
                bundle, "claude-plan-phase", "159800550286/main/run1.md", REFLECTION
            )
            self._make_reflection(
                bundle, "claude-plan-phase", "archive/old.md", REFLECTION
            )
            os.environ["PHASE_LOOP_SKILL_BUNDLE"] = str(bundle)
            try:
                written = rs.export_reflections(
                    repo, dest, machine="testhost", harnesses=("claude",)
                )
            finally:
                del os.environ["PHASE_LOOP_SKILL_BUNDLE"]

            self.assertEqual(len(written), 1)
            expected = dest / "claude-plan-phase" / "159800550286" / "main" / "testhost" / "run1.md"
            self.assertTrue(expected.exists())
            content = expected.read_text(encoding="utf-8")
            self.assertNotIn("divorce-discovery-fix", content)
            self.assertIn("<branch:", content)
            # archived reflection did not export
            self.assertFalse((dest / "claude-plan-phase" / "archive").exists())

    def test_export_skips_already_pooled_reflections(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            bundle = d / "bundle"
            dest = d / "store"
            repo = d / "repo"
            repo.mkdir()
            pooled = rs.redact_reflection_text(REFLECTION, machine="other-machine")
            self._make_reflection(
                bundle, "claude-plan-phase", "159800550286/main/imported.md", pooled
            )
            os.environ["PHASE_LOOP_SKILL_BUNDLE"] = str(bundle)
            try:
                written = rs.export_reflections(
                    repo, dest, machine="testhost", harnesses=("claude",)
                )
            finally:
                del os.environ["PHASE_LOOP_SKILL_BUNDLE"]
            self.assertEqual(written, [])


if __name__ == "__main__":
    unittest.main()
