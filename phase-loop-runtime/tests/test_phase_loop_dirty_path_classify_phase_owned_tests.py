import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.runner import _classify_dirty_paths
from phase_loop_test_utils import commit_fixture_paths, make_repo, write_phase_plan


class DirtyPathClassifyPhaseOwnedTests(unittest.TestCase):
    def test_classifies_owned_implementation_and_paired_test_as_phase_owned(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, owned_files=("apps/portal/src/lib/feature.ts",))
            commit_fixture_paths(repo, "add runner plan", plan)

            impl_path = repo / "apps" / "portal" / "src" / "lib" / "feature.ts"
            test_path = repo / "apps" / "portal" / "src" / "lib" / "__tests__" / "feature.test.ts"
            impl_path.parent.mkdir(parents=True)
            test_path.parent.mkdir(parents=True)
            impl_path.write_text("export const feature = true;\n", encoding="utf-8")
            test_path.write_text("import '../feature';\n", encoding="utf-8")

            summary = _classify_dirty_paths(
                repo,
                roadmap,
                plan,
                pre_launch_dirty_paths=[],
                post_launch_dirty_paths=[
                    "apps/portal/src/lib/feature.ts",
                    "apps/portal/src/lib/__tests__/feature.test.ts",
                ],
                current_phase="RUNNER",
            )

            self.assertIn("apps/portal/src/lib/feature.ts", summary["phase_owned_dirty_paths"])
            self.assertIn("apps/portal/src/lib/__tests__/feature.test.ts", summary["phase_owned_dirty_paths"])
            self.assertEqual(summary["unowned_dirty_paths"], [])
            self.assertEqual(summary["pre_existing_dirty_paths"], [])
            self.assertTrue(summary["phase_owned_dirty"])

    def test_classifies_vendor_source_test_pair_as_phase_owned(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(
                repo,
                "RUNNER",
                roadmap,
                owned_files=("vendor/sample-runtime/src/sample_runtime/runner.py",),
            )
            commit_fixture_paths(repo, "add runner plan", plan)

            source_path = repo / "vendor" / "sample-runtime" / "src" / "sample_runtime" / "runner.py"
            test_path = repo / "vendor" / "sample-runtime" / "tests" / "test_runner.py"
            source_path.parent.mkdir(parents=True)
            test_path.parent.mkdir(parents=True)
            source_path.write_text("def run():\n    return True\n", encoding="utf-8")
            test_path.write_text("from sample_runtime.runner import run\n", encoding="utf-8")

            summary = _classify_dirty_paths(
                repo,
                roadmap,
                plan,
                pre_launch_dirty_paths=[],
                post_launch_dirty_paths=[
                    "vendor/sample-runtime/src/sample_runtime/runner.py",
                    "vendor/sample-runtime/tests/test_runner.py",
                ],
                current_phase="RUNNER",
            )

            self.assertIn("vendor/sample-runtime/src/sample_runtime/runner.py", summary["phase_owned_dirty_paths"])
            self.assertIn("vendor/sample-runtime/tests/test_runner.py", summary["phase_owned_dirty_paths"])
            self.assertEqual(summary["unowned_dirty_paths"], [])
            self.assertTrue(summary["phase_owned_dirty"])

    def test_unrelated_dirty_test_path_remains_unowned(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap, owned_files=("apps/portal/src/lib/feature.ts",))
            commit_fixture_paths(repo, "add runner plan", plan)

            impl_path = repo / "apps" / "portal" / "src" / "lib" / "feature.ts"
            unrelated_test_path = repo / "apps" / "portal" / "src" / "lib" / "__tests__" / "other.test.ts"
            impl_path.parent.mkdir(parents=True)
            unrelated_test_path.parent.mkdir(parents=True)
            impl_path.write_text("export const feature = true;\n", encoding="utf-8")
            unrelated_test_path.write_text("import '../other';\n", encoding="utf-8")

            summary = _classify_dirty_paths(
                repo,
                roadmap,
                plan,
                pre_launch_dirty_paths=[],
                post_launch_dirty_paths=[
                    "apps/portal/src/lib/feature.ts",
                    "apps/portal/src/lib/__tests__/other.test.ts",
                ],
                current_phase="RUNNER",
            )

            self.assertIn("apps/portal/src/lib/feature.ts", summary["phase_owned_dirty_paths"])
            self.assertNotIn("apps/portal/src/lib/__tests__/other.test.ts", summary["phase_owned_dirty_paths"])
            self.assertEqual(summary["unowned_dirty_paths"], ["apps/portal/src/lib/__tests__/other.test.ts"])
            self.assertFalse(summary["phase_owned_dirty"])


if __name__ == "__main__":
    unittest.main()
