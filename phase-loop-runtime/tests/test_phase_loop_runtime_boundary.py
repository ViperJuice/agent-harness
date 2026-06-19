from pathlib import Path
import unittest
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]

class TestPhaseLoopRuntimeBoundary(unittest.TestCase):
    def test_package_structure(self):
        import phase_loop_runtime
        self.assertTrue(hasattr(phase_loop_runtime, "__version__"))
        
        # Verify __all__ in phase_loop matches expected public boundary
        expected_all = [
            "__version__",
            "cli",
            "discovery",
            "handoff",
            "maintenance",
            "models",
            "observability",
            "profiles",
            "reconcile",
            "render",
            "runtime_paths",
            "runner",
            "state",
            "state_ops",
        ]
        self.assertCountEqual(phase_loop_runtime.__all__, expected_all)

    def test_module_imports(self):
        # Verify we can import all modules defined in __all__
        import phase_loop_runtime
        for module_name in phase_loop_runtime.__all__:
            if module_name == "__version__":
                continue
            with self.subTest(module=module_name):
                module = __import__(f"phase_loop_runtime.{module_name}", fromlist=["*"])
                self.assertIsNotNone(module)

    def test_public_modules_match_runtime_boundary_doc(self):
        import phase_loop_runtime

        doc = (ROOT / "docs" / "phase-loop" / "runtime-boundary.md").read_text(encoding="utf-8")
        documented = {
            line.split("`")[1].removeprefix("phase_loop_runtime.")
            for line in doc.splitlines()
            if line.startswith("- `phase_loop_runtime.")
        }
        exported = set(phase_loop_runtime.__all__) - {"__version__"}
        self.assertEqual(exported, documented)

    def test_runtime_paths_prefer_canonical_state_when_legacy_exists(self):
        from phase_loop_runtime.runtime_paths import (
            phase_loop_event_read_files,
            phase_loop_read_dir,
            phase_loop_state_read_file,
        )

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            canonical = repo / ".phase-loop"
            legacy = repo / ".codex" / "phase-loop"
            canonical.mkdir()
            legacy.mkdir(parents=True)
            canonical_state = canonical / "state.json"
            legacy_state = legacy / "state.json"
            canonical_events = canonical / "events.jsonl"
            legacy_events = legacy / "events.jsonl"
            canonical_state.write_text("{}", encoding="utf-8")
            legacy_state.write_text("{}", encoding="utf-8")
            canonical_events.write_text("", encoding="utf-8")
            legacy_events.write_text("", encoding="utf-8")

            self.assertEqual(phase_loop_read_dir(repo), canonical)
            self.assertEqual(phase_loop_state_read_file(repo), canonical_state)
            self.assertEqual(phase_loop_event_read_files(repo), (canonical_events,))

    def test_cli_version_flag(self):
        result = subprocess.run(
            ["phase-loop", "--version"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("phase-loop", result.stdout)

    def test_cli_version_command(self):
        result = subprocess.run(
            ["phase-loop", "version"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("phase-loop", result.stdout)

    def test_cli_help_is_neutral(self):
        result = subprocess.run(
            ["phase-loop", "--help"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("Neutral phase-loop runner", result.stdout)
        self.assertIn("codex-phase-loop remains a Codex bridge alias", result.stdout)

    def test_runtime_boundary_locks_future_extraction_identity(self):
        doc = (ROOT / "docs" / "phase-loop" / "runtime-boundary.md").read_text(encoding="utf-8")
        self.assertIn("vendored package `phase-loop-runtime`", doc)
        self.assertIn("Python import package `phase_loop_runtime`", doc)
        self.assertIn("neutral `phase-loop` command", doc)
        self.assertIn("backward-compatible `codex-phase-loop`", doc)
        self.assertIn("same parser", doc)

    def test_runtime_boundary_cites_substrate_without_dotfiles_root_dependency(self):
        doc = (ROOT / "docs" / "phase-loop" / "runtime-boundary.md").read_text(encoding="utf-8")
        flat = " ".join(doc.split())
        for token in (
            "docs/phase-loop/harness-substrate-manifest.md",
            "IF-0-SUBSTRATE-1",
            "canonical `.phase-loop/**` state",
            "without governed-pipeline",
            "without governed-pipeline, `.pipeline/**`, Portal, Greenfield, a source bundle, credentials, Host bootstrap",
            "MCP gateway setup",
            "provider payloads",
            "local environment values",
            "Governed Pipeline owns adoption",
            "source-bundle emission",
            "closeout ingest",
            "Portal projection",
        ):
            self.assertIn(token, flat)
        for token in (
            "requires the full dotfiles",
            "client dependency on the dotfiles root",
            "must install owner dotfiles",
            "must source shell profile",
        ):
            self.assertNotIn(token, flat)

if __name__ == "__main__":
    unittest.main()
