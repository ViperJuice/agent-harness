import unittest
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


class PhaseLoopBamlDependencyTest(unittest.TestCase):
    # TESTDECOUPLE: integration — reads vendor/phase-loop-runtime/pyproject.toml,
    # which is NEVER package-data (no repoint can make it standalone). This is the
    # source-tree PACKAGING contract; it legitimately runs only in-tree.
    @pytest.mark.dotfiles_integration
    def test_pyproject_declares_baml_runtime_and_packaged_source(self):
        # DECOUPLE SL-0: baml_src now ships as package-data inside the package
        # (resolved via importlib.resources), NOT via [tool.setuptools.data-files]
        # into share/. See test_pkg_layout_freeze.py for the full freeze contract.
        text = (ROOT / "vendor/phase-loop-runtime/pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"baml-py>=0.222,<0.223"', text)
        self.assertIn('"pydantic>=2,<3"', text)
        self.assertIn('"baml_src/*.baml"', text)
        self.assertNotIn('"share/phase-loop-runtime/baml_src"', text)
        pkg_baml_dir = ROOT / "vendor/phase-loop-runtime/src/phase_loop_runtime/baml_src"
        for name in (
            "emit_phase_closeout",
            "dotfiles_adoption_manifest",
            "dotfiles_runtime_projection",
            "dotfiles_c4_document",
            "dotfiles_task_catalog",
            "verification_evidence",
        ):
            self.assertTrue(
                (pkg_baml_dir / f"{name}.baml").is_file(),
                f"missing packaged baml source: {name}.baml",
            )

    def test_baml_py_imports_after_install(self):
        import baml_py

        self.assertTrue(hasattr(baml_py, "BamlRuntime"))


if __name__ == "__main__":
    unittest.main()
