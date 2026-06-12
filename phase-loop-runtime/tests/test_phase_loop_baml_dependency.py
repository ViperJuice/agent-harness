import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


class PhaseLoopBamlDependencyTest(unittest.TestCase):
    def test_pyproject_declares_baml_runtime_and_packaged_source(self):
        text = (ROOT / "vendor/phase-loop-runtime/pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"baml-py>=0.222,<0.223"', text)
        self.assertIn('"pydantic>=2,<3"', text)
        self.assertIn('"share/phase-loop-runtime/baml_src"', text)
        for name in (
            "emit_phase_closeout",
            "dotfiles_adoption_manifest",
            "dotfiles_runtime_projection",
            "dotfiles_c4_document",
            "dotfiles_task_catalog",
            "verification_evidence",
        ):
            self.assertIn(f'"baml_src/{name}.baml"', text)

    def test_baml_py_imports_after_install(self):
        import baml_py

        self.assertTrue(hasattr(baml_py, "BamlRuntime"))


if __name__ == "__main__":
    unittest.main()
