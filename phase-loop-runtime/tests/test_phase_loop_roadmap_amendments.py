import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.reconcile import detect_downstream_plan_staleness, invalidate_stale_downstream_plans
from phase_loop_test_utils import commit_fixture_paths, make_repo, write_named_roadmap, write_phase_plan


class PhaseLoopRoadmapAmendmentsTest(unittest.TestCase):
    def test_changed_roadmap_bytes_invalidate_downstream_plans(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = write_named_roadmap(repo, (("ALPHA", "Alpha"), ("BETA", "Beta")))
            plan = write_phase_plan(repo, "BETA", roadmap)
            commit_fixture_paths(repo, "roadmap fixture", roadmap, plan)

            roadmap.write_text(roadmap.read_text(encoding="utf-8").replace("Beta", "Beta Revised"), encoding="utf-8")

            stale = detect_downstream_plan_staleness(repo, roadmap, "ALPHA")
            invalidation = invalidate_stale_downstream_plans(repo, roadmap, "ALPHA")

            self.assertEqual(stale[0]["phase"], "BETA")
            self.assertIn("roadmap_sha256", stale[0]["reasons"])
            self.assertEqual(invalidation["status"], "planning_required")
            self.assertEqual(invalidation["blocker_class"], "gold_record_amendment")
            self.assertIn("codex-plan-phase", invalidation["next_command"])

    def test_unchanged_downstream_plan_remains_executable(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = write_named_roadmap(repo, (("ALPHA", "Alpha"), ("BETA", "Beta")))
            plan = write_phase_plan(repo, "BETA", roadmap)
            commit_fixture_paths(repo, "roadmap fixture", roadmap, plan)

            self.assertEqual(detect_downstream_plan_staleness(repo, roadmap, "ALPHA"), ())
            self.assertEqual(invalidate_stale_downstream_plans(repo, roadmap, "ALPHA")["status"], "unchanged")


if __name__ == "__main__":
    unittest.main()
