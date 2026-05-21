import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
from phase_loop_runtime.provenance import phase_provenance_map, phase_sha256, roadmap_sha256
from phase_loop_smoke_utils import make_two_phase_repo


class PhaseLoopProvenanceTest(unittest.TestCase):
    def test_phase_hash_changes_only_for_edited_phase(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            before_roadmap = roadmap_sha256(roadmap)
            before = phase_provenance_map(roadmap)
            roadmap.write_text(roadmap.read_text().replace("### Phase 1 - Beta (BETA)", "### Phase 1 - Beta Revised (BETA)"))
            after = phase_provenance_map(roadmap)
            self.assertNotEqual(before_roadmap, roadmap_sha256(roadmap))
            self.assertEqual(before["ALPHA"], after["ALPHA"])
            self.assertNotEqual(before["BETA"], after["BETA"])

    def test_phase_hash_changes_when_phase_block_changes(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            before = phase_sha256(roadmap, "ALPHA")
            roadmap.write_text(roadmap.read_text().replace("### Phase 0 - Alpha (ALPHA)", "### Phase 0 - Alpha Changed (ALPHA)"))
            self.assertNotEqual(before, phase_sha256(roadmap, "ALPHA"))

    def test_missing_phase_has_no_phase_hash(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.write_text("# Roadmap\n\n### Phase 0 - Alpha (ALPHA)\n")
            self.assertIsNone(phase_sha256(roadmap, "BETA"))

    def test_phase_hashes_allow_amendment_suffixes(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_two_phase_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            roadmap.write_text(
                "### Phase 0 - Alpha (ALPHA)\n\n"
                "### Phase 1 - Portal RLS Fix (PORTALRLSFIX) *(amendment, 2026-04-28)*\n"
            )
            hashes = phase_provenance_map(roadmap)
            self.assertIn("PORTALRLSFIX", hashes)
            self.assertIsNotNone(phase_sha256(roadmap, "PORTALRLSFIX"))


if __name__ == "__main__":
    unittest.main()
