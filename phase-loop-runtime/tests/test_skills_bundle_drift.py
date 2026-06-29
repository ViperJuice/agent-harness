"""#12 CR #3 — drift guard for the generated `skills_bundle/` package-data.

Mirrors `test_phase_loop_runtime_package_data` for the other generated data: regenerate
the bundle from the canonical `phase-loop-skills/` source and assert the committed copy
is byte-identical, so an edit to the source without re-running `scripts/sync_skills_bundle.py`
cannot ship stale skills with green CI. Skipped standalone (no sibling source to compare).
"""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]  # phase-loop-runtime/
COMMITTED = PKG / "src" / "phase_loop_runtime" / "skills_bundle"
SRC_SKILLS = PKG.parent / "phase-loop-skills"
SYNC = PKG / "scripts" / "sync_skills_bundle.py"


def _load_sync():
    spec = importlib.util.spec_from_file_location("sync_skills_bundle_under_test", SYNC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class SkillsBundleDriftTest(unittest.TestCase):
    def test_committed_bundle_is_byte_identical_to_regen(self):
        if not SRC_SKILLS.is_dir():
            self.skipTest("sibling phase-loop-skills/ source absent (from-wheel layout)")
        sync = _load_sync()

        def _bundle_files(root: Path) -> dict:
            # Exclude bytecode caches: importing a vendored bundle script (e.g. in another
            # test) writes __pycache__/*.pyc into the committed tree, which would falsely
            # read as drift. 0 .pyc are committed; they are never meaningful bundle content.
            return {
                p.relative_to(root): p
                for p in root.rglob("*")
                if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
            }

        with tempfile.TemporaryDirectory() as td:
            regen = sync.assemble_bundle(SRC_SKILLS, Path(td) / "skills_bundle")
            committed = _bundle_files(COMMITTED)
            fresh = _bundle_files(regen)
            self.assertEqual(
                set(committed),
                set(fresh),
                "skills_bundle/ file set drifted from phase-loop-skills/; "
                "run scripts/sync_skills_bundle.py",
            )
            for rel, cpath in committed.items():
                with self.subTest(path=str(rel)):
                    self.assertEqual(
                        cpath.read_bytes(),
                        fresh[rel].read_bytes(),
                        f"skills_bundle/{rel} drifted; run scripts/sync_skills_bundle.py",
                    )


if __name__ == "__main__":
    unittest.main()
