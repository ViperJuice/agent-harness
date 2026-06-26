import tempfile
import textwrap
import unittest
from pathlib import Path

from phase_loop_runtime.discovery import parse_roadmap_phases
from phase_loop_runtime.models import LaneIRDiagnostic
from phase_loop_runtime.plan_ir import iter_waves


import pytest
from _dotfiles_tree import dotfiles_tree_present

# TESTDECOUPLE SL-1: this file reads dotfiles fleet paths (absent in the
# extracted agent-harness layout). Skip at MODULE level before any such read so
# collection does not error standalone; the marker keeps it deselected by
# `pytest -m "not dotfiles_integration"` and the conftest run-time hook.
if not dotfiles_tree_present():
    pytest.skip("requires dotfiles tree", allow_module_level=True)

pytestmark = pytest.mark.dotfiles_integration

ROOT = Path(__file__).resolve().parents[3]


class PlanIRIterWavesTest(unittest.TestCase):
    def test_strict_serial_dag_yields_one_phase_per_wave(self):
        roadmap = self._write_roadmap(
            """
            ### Phase 1 - One (A)
            **Depends on**
            - (none)

            ---

            ### Phase 2 - Two (B)
            **Depends on**
            - A

            ---

            ### Phase 3 - Three (C)
            **Depends on**
            - B
            """
        )

        self.assertEqual(list(iter_waves(roadmap)), [("A",), ("B",), ("C",)])

    def test_fully_parallel_dag_yields_single_wave(self):
        roadmap = self._write_roadmap(
            """
            ### Phase 1 - One (A)
            **Depends on**
            - (none)

            ---

            ### Phase 2 - Two (B)
            **Depends on**
            - (none)

            ---

            ### Phase 3 - Three (C)
            **Depends on**
            - (none)
            """
        )

        self.assertEqual(list(iter_waves(roadmap)), [("A", "B", "C")])

    def test_mixed_dag_preserves_declared_topological_waves(self):
        roadmap = self._write_roadmap(
            """
            ### Phase 1 - Access contract (AC)
            **Depends on**
            - (none)

            ---

            ### Phase 2 - Adapter mode (AM)
            **Depends on**
            - AC

            ---

            ### Phase 3 - User recovery flow (URF)
            **Depends on**
            - AM

            ---

            ### Phase 4 - Auth updates (AU)
            **Depends on**
            - AM

            ---

            ### Phase 5 - App polish migration (APM)
            **Depends on**
            - URF
            - AU

            ---

            ### Phase 6 - Synthetic user notes (SUN)
            **Depends on**
            - APM
            """
        )

        self.assertEqual(list(iter_waves(roadmap)), [("AC",), ("AM",), ("URF", "AU"), ("APM",), ("SUN",)])

    def test_cycle_detection_raises_lane_ir_diagnostic_payload(self):
        roadmap = self._write_roadmap(
            """
            ### Phase 1 - One (A)
            **Depends on**
            - B

            ---

            ### Phase 2 - Two (B)
            **Depends on**
            - A
            """
        )

        with self.assertRaises(LaneIRDiagnostic) as ctx:
            list(iter_waves(roadmap))

        diagnostic = ctx.exception
        self.assertIsInstance(diagnostic, LaneIRDiagnostic)
        self.assertEqual(diagnostic.kind, "cycle")

    def test_simple_arrow_dag_supplements_phase_blocks(self):
        roadmap = self._write_roadmap(
            """
            ### Phase 1 - One (A)
            **Depends on**
            - (none)

            ---

            ### Phase 2 - Two (B)
            **Depends on**
            - (none)

            ---

            ### Phase 3 - Three (C)
            **Depends on**
            - (none)

            ## Phase Dependency DAG

            ```
            A -> B -> C
            ```
            """
        )

        self.assertEqual(list(iter_waves(roadmap)), [("A",), ("B",), ("C",)])

    def test_shipped_roadmaps_have_sane_wave_segmentation(self):
        for roadmap in sorted((ROOT / "specs").glob("phase-plans-v*.md")):
            with self.subTest(roadmap=roadmap.name):
                aliases = parse_roadmap_phases(roadmap)
                if not aliases:
                    continue

                waves = list(iter_waves(roadmap))
                flattened = [alias for wave in waves for alias in wave]

                self.assertTrue(waves)
                self.assertTrue(all(wave for wave in waves))
                self.assertEqual(set(flattened), set(aliases))
                self.assertEqual(len(flattened), len(set(flattened)))

    def _write_roadmap(self, body: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        roadmap = Path(directory.name) / "phase-plans-test.md"
        roadmap.write_text(
            "# Test roadmap\n\n" + textwrap.dedent(body).strip() + "\n",
            encoding="utf-8",
        )
        return roadmap


if __name__ == "__main__":
    unittest.main()
