import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from phase_loop_test_utils import ROOT
from phase_loop_runtime.build_bundle import ACTIVE_HARNESSES, DEFAULT_SOURCES, build_bundle
from phase_loop_runtime.cli import build_parser, main
from phase_loop_runtime.skill_install import REQUIRED_SKILLS


def _skill_text(harness: str, bare: str, extra: str = "") -> str:
    title = harness.capitalize()
    return (
        "---\n"
        f"name: {harness}-{bare}\n"
        f"description: \"{title} executor for {harness}-{bare}.\"\n"
        "---\n\n"
        f"# {title} {bare}\n\n"
        f"Run `{harness}-{bare}` and then call `{harness}-plan-phase`.\n"
        f"{extra}"
    )


class PhaseLoopBuildBundleTest(unittest.TestCase):
    def make_sources(self, root: Path, skills: tuple[str, ...] = REQUIRED_SKILLS) -> dict[str, Path]:
        sources = {
            "claude": root / "claude-config" / "claude-skills",
            "codex": root / "codex-config" / "skills",
            "gemini": root / "gemini-config" / "skills",
            "opencode": root / "opencode-config" / "skills",
        }
        for harness, source in sources.items():
            for skill in skills:
                skill_dir = source / f"{harness}-{skill}"
                skill_dir.mkdir(parents=True, exist_ok=True)
                (skill_dir / "SKILL.md").write_text(_skill_text(harness, skill), encoding="utf-8")
        return sources

    def test_synthetic_roundtrip_layout_and_override_emission(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sources = self.make_sources(root)
            claude_skill = sources["claude"] / "claude-execute-detailed" / "SKILL.md"
            claude_skill.write_text(
                _skill_text("claude", "execute-detailed", extra="Claude-only approval boundary.\n"),
                encoding="utf-8",
            )

            destination = root / "bundle"
            result = build_bundle(sources, destination, dry_run=False, apply=True)

            self.assertIn("execute-detailed", result.skills_regenerated)
            self.assertIn("execute-detailed/_overrides/claude/SKILL.md", "\n".join(result.overrides_written))
            base = destination / "execute-detailed" / "SKILL.md"
            self.assertIn("name: execute-detailed", base.read_text(encoding="utf-8"))
            self.assertIn("`<harness>-execute-detailed`", base.read_text(encoding="utf-8"))
            self.assertIn("`<harness>-plan-phase`", base.read_text(encoding="utf-8"))
            self.assertTrue((destination / "execute-detailed" / "_overrides" / "claude" / "SKILL.md").is_file())
            for harness in ACTIVE_HARNESSES:
                self.assertTrue((destination / "execute-detailed" / "_overrides" / harness / "README.md").is_file())

    def test_second_run_is_idempotent_and_force_rewrites(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sources = self.make_sources(root)
            destination = root / "bundle"

            first = build_bundle(sources, destination, dry_run=False, apply=True)
            target = destination / "execute-detailed" / "SKILL.md"
            first_mtime = target.stat().st_mtime_ns
            second = build_bundle(sources, destination, dry_run=False, apply=True)
            second_mtime = target.stat().st_mtime_ns
            forced = build_bundle(sources, destination, dry_run=False, apply=True, force=True)

            self.assertTrue(first.files_written)
            self.assertEqual(second.files_written, [])
            self.assertEqual(first_mtime, second_mtime)
            self.assertIn(target.as_posix(), forced.files_written)

    def test_skip_on_missing_warns_without_output(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sources = self.make_sources(root)
            (sources["gemini"] / "gemini-execute-detailed" / "SKILL.md").unlink()

            result = build_bundle(sources, root / "bundle", dry_run=False, apply=True)

            self.assertEqual(len(result.skills_skipped), 1)
            self.assertEqual(result.skills_skipped[0].skill, "execute-detailed")
            self.assertEqual(result.skills_skipped[0].missing_harnesses, ("gemini",))
            self.assertEqual(result.warnings[0].skill, "execute-detailed")
            self.assertFalse((root / "bundle" / "execute-detailed" / "SKILL.md").exists())

    def test_dry_run_is_non_mutating_with_summary_shape(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sources = self.make_sources(root)
            destination = root / "bundle"

            result = build_bundle(sources, destination, dry_run=True, apply=True)
            payload = result.to_dict()

            self.assertFalse(destination.exists())
            self.assertEqual(
                set(payload),
                {
                    "skills_regenerated",
                    "overrides_written",
                    "skills_skipped",
                    "warnings",
                    "files_written",
                    "dry_run",
                    "applied",
                },
            )
            self.assertTrue(payload["dry_run"])
            self.assertFalse(payload["applied"])

    def test_cli_parser_help_and_json_accept_build_bundle(self):
        parser = build_parser()
        args = parser.parse_args(["build-bundle", "--source", "claude-config/claude-skills", "--destination", "out", "--apply", "--force"])
        self.assertEqual(args.command, "build-bundle")
        self.assertEqual(args.source, ["claude-config/claude-skills"])
        self.assertEqual(args.destination, "out")
        self.assertTrue(args.apply)
        self.assertTrue(args.force)

        stream = io.StringIO()
        with self.assertRaises(SystemExit) as raised, contextlib.redirect_stdout(stream):
            parser.parse_args(["build-bundle", "--help"])
        self.assertEqual(raised.exception.code, 0)
        help_text = stream.getvalue()
        for token in ("--source", "--destination", "--dry-run", "--apply", "--force"):
            self.assertIn(token, help_text)

    def test_cli_dry_run_prints_json_without_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sources = self.make_sources(root)
            destination = root / "bundle"
            argv = ["build-bundle", "--repo", str(root), "--destination", str(destination), "--dry-run", "--json"]
            for source in sources.values():
                argv.extend(["--source", str(source)])

            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                self.assertEqual(main(argv), 0)

            payload = json.loads(stream.getvalue())
            self.assertFalse(destination.exists())
            self.assertTrue(payload["dry_run"])
            self.assertFalse(payload["applied"])

    def test_real_canonical_execute_detailed_matches_committed_bundle(self):
        with tempfile.TemporaryDirectory() as td:
            destination = Path(td) / "bundle"
            # Anchor the canonical sources to ROOT (repo root) rather than the
            # CWD-relative DEFAULT_SOURCES fallback, so this test passes whether the
            # suite runs from the repo root or from vendor/phase-loop-runtime.
            sources = {harness: ROOT / rel for harness, rel in DEFAULT_SOURCES.items()}
            result = build_bundle(sources, destination, dry_run=False, apply=True)
            self.assertNotIn("execute-detailed", [item.skill for item in result.skills_skipped])

            generated = sorted(
                path.relative_to(destination / "execute-detailed").as_posix()
                for path in (destination / "execute-detailed").rglob("*")
                if path.is_file()
            )
            committed_root = ROOT / "vendor" / "phase-loop-skills" / "execute-detailed"
            committed = sorted(
                path.relative_to(committed_root).as_posix()
                for path in committed_root.rglob("*")
                if path.is_file()
            )
            self.assertEqual(generated, committed)
            for relative in generated:
                self.assertEqual(
                    (destination / "execute-detailed" / relative).read_text(encoding="utf-8"),
                    (committed_root / relative).read_text(encoding="utf-8"),
                    msg=relative,
                )


    def test_aux_subdirs_propagate_into_neutral_bundle(self):
        # scripts/, references/, assets/ from a canonical source must land in the
        # neutral bundle so `install` copytrees them to every harness root
        # (regression: build_bundle previously emitted only SKILL.md, so e.g.
        # scripts/validate_roadmap.py never reached installed bundles).
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sources = self.make_sources(root)
            skill = REQUIRED_SKILLS[0]
            aux_file = sources["claude"] / f"claude-{skill}" / "scripts" / "validate_roadmap.py"
            aux_file.parent.mkdir(parents=True, exist_ok=True)
            aux_file.write_text("print('ok')\n", encoding="utf-8")
            pyc = sources["claude"] / f"claude-{skill}" / "scripts" / "__pycache__" / "x.pyc"
            pyc.parent.mkdir(parents=True, exist_ok=True)
            pyc.write_text("junk", encoding="utf-8")

            destination = root / "bundle"
            build_bundle(sources, destination, dry_run=False, apply=True)

            carried = destination / skill / "scripts" / "validate_roadmap.py"
            self.assertTrue(carried.is_file(), "aux script not carried into neutral bundle")
            self.assertEqual(carried.read_text(encoding="utf-8"), "print('ok')\n")
            self.assertFalse(
                (destination / skill / "scripts" / "__pycache__" / "x.pyc").exists(),
                "__pycache__ should be excluded from the bundle",
            )

if __name__ == "__main__":
    unittest.main()
