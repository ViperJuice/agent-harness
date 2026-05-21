import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.classifier import classify_phase
from phase_loop_runtime.models import StateSnapshot, utc_now
from phase_loop_runtime.state import write_state
from phase_loop_smoke_utils import isolated_codex_home, write_skill_handoff
from phase_loop_test_utils import make_repo, provenanced_state, write_phase_plan


class PhaseLoopClassifierTest(unittest.TestCase):
    def test_unplanned_and_planned_statuses(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            self.assertEqual(classify_phase(repo, roadmap, "RUNNER"), "unplanned")
            write_phase_plan(repo, "RUNNER", roadmap)
            self.assertEqual(classify_phase(repo, roadmap, "RUNNER"), "planned")

    def test_provenanced_state_terminal_status_wins(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_state(repo, provenanced_state(repo, roadmap, {"RUNNER": "complete"}))
            self.assertEqual(classify_phase(repo, roadmap, "RUNNER"), "complete")

    def test_provenanced_state_closeout_status_is_trusted(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_state(repo, provenanced_state(repo, roadmap, {"RUNNER": "awaiting_phase_closeout"}))
            self.assertEqual(classify_phase(repo, roadmap, "RUNNER"), "awaiting_phase_closeout")

    def test_legacy_state_status_is_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            write_state(repo, StateSnapshot(timestamp=utc_now(), repo=str(repo), roadmap=str(roadmap), phases={"RUNNER": "complete"}))
            self.assertEqual(classify_phase(repo, roadmap, "RUNNER"), "unplanned")

    def test_matching_handoff_status_is_trusted(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = make_repo(root)
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            with isolated_codex_home(root) as codex_home:
                write_skill_handoff(codex_home, repo, "codex-execute-phase", "RUNNER", "complete", plan)
                self.assertEqual(classify_phase(repo, roadmap, "RUNNER"), "complete")

    def test_matching_non_codex_handoff_status_is_trusted(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = make_repo(root)
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            with isolated_codex_home(root) as codex_home:
                write_skill_handoff(codex_home, repo, "gemini-execute-phase", "RUNNER", "complete", plan)
                self.assertEqual(classify_phase(repo, roadmap, "RUNNER"), "complete")

    def test_stale_handoff_status_is_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = make_repo(root)
            roadmap = repo / "specs" / "phase-plans-v1.md"
            plan = write_phase_plan(repo, "RUNNER", roadmap)
            roadmap.write_text(roadmap.read_text() + "\n### Phase 3 - Docs (DOCS)\n")
            with isolated_codex_home(root) as codex_home:
                write_skill_handoff(codex_home, repo, "codex-execute-phase", "RUNNER", "complete", plan)
                self.assertEqual(classify_phase(repo, roadmap, "RUNNER"), "unplanned")

    def test_amended_downstream_roadmap_does_not_reuse_stale_phase_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = make_repo(root)
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.write_text(
                "# Roadmap\n\n"
                "### Phase 0 - Affordance Verification (AFFVERIFY)\n\n"
                "### Phase 1 - Visual Fidelity (VISUAL)\n"
            )
            plan = write_phase_plan(repo, "VISUAL", roadmap)
            with isolated_codex_home(root) as codex_home:
                write_skill_handoff(codex_home, repo, "codex-execute-phase", "VISUAL", "complete", plan)
                roadmap.write_text(
                    "# Roadmap\n\n"
                    "### Phase 0 - Affordance Verification (AFFVERIFY)\n\n"
                    "### Phase 1 - Mobile Shell (MOBSHELL)\n\n"
                    "### Phase 2 - Visual Fidelity (VISUAL)\n"
                )
                self.assertEqual(classify_phase(repo, roadmap, "MOBSHELL"), "unplanned")
                self.assertEqual(classify_phase(repo, roadmap, "VISUAL"), "unplanned")


if __name__ == "__main__":
    unittest.main()
