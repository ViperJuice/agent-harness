"""CANON / IF-0-CANON-2 — hard parity gate for the committed skill bundle.

Asserts the committed ``phase-loop-skills/`` is byte-identical to a fresh
``build_bundle(<canonical skills-src sources>)``. An edit to a skill source
under ``skills-src/`` that is not followed by
``scripts/regenerate_skills_bundle.py`` cannot ship a stale committed bundle with
green CI, and a hand-edit to ``phase-loop-skills/`` that diverges from the sources
is likewise caught.

This is the upstream half of the bundle pipeline (the downstream
``phase-loop-skills/`` -> packaged ``skills_bundle/`` half is guarded by
``test_skills_bundle_drift.py``).

SELF-CONTAINED: reads only in-repo paths (``skills-src/`` + ``phase-loop-skills/``),
no dotfiles checkout. It is intentionally NOT marked ``dotfiles_integration`` so it
runs on every CI lane. It IS skipped in the standalone-from-wheel clean-room, where
the sibling sources are isolated away (same gate the sync-drift test uses).

README NOTE: ``phase-loop-skills/README.md`` is a hand-authored bundle index, NOT
``build_bundle`` output, so it is excluded from the byte-for-byte comparison
below. Everything ``build_bundle`` *does* own (every ``<skill>/`` tree) is
compared exactly.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from phase_loop_runtime.build_bundle import DEFAULT_SOURCES, build_bundle

PKG = Path(__file__).resolve().parents[1]      # phase-loop-runtime/
REPO = PKG.parent                              # agent-harness/
SKILLS_SRC = {harness: REPO / rel for harness, rel in DEFAULT_SOURCES.items()}
COMMITTED_BUNDLE = REPO / "phase-loop-skills"

# Hand-authored, not build_bundle output. Excluded from parity (see module docstring).
NON_GENERATED = {"README.md"}


def _bundle_files(root: Path) -> dict[Path, Path]:
    out: dict[Path, Path] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        rel = path.relative_to(root)
        if rel.as_posix() in NON_GENERATED:
            continue
        out[rel] = path
    return out


class SkillsCanonParityTest(unittest.TestCase):
    def _require_sources(self) -> None:
        if not all(root.is_dir() for root in SKILLS_SRC.values()):
            self.skipTest("canonical skills-src/ sources absent (from-wheel layout)")
        if not COMMITTED_BUNDLE.is_dir():
            self.skipTest("committed phase-loop-skills/ absent (from-wheel layout)")

    def test_committed_bundle_is_byte_identical_to_canonical_build(self):
        self._require_sources()
        with tempfile.TemporaryDirectory() as td:
            fresh_root = Path(td) / "phase-loop-skills"
            result = build_bundle(SKILLS_SRC, fresh_root, dry_run=False, apply=True, force=True)
            self.assertEqual(
                [s.skill for s in result.skills_skipped],
                [],
                "build_bundle skipped skills; a canonical source root is missing a SKILL.md",
            )

            committed = _bundle_files(COMMITTED_BUNDLE)
            fresh = _bundle_files(fresh_root)
            self.assertEqual(
                set(committed),
                set(fresh),
                "phase-loop-skills/ file set drifted from build_bundle(skills-src/); "
                "run scripts/regenerate_skills_bundle.py",
            )
            for rel, cpath in committed.items():
                with self.subTest(path=str(rel)):
                    self.assertEqual(
                        cpath.read_bytes(),
                        fresh[rel].read_bytes(),
                        f"phase-loop-skills/{rel} drifted from its skills-src source; "
                        "run scripts/regenerate_skills_bundle.py",
                    )

    def test_regenerate_script_is_a_noop_on_committed_tree(self):
        """The documented one-command regenerate must produce zero changes on a
        clean tree (parity is reachable, not just asserted)."""
        self._require_sources()
        import importlib.util

        script = PKG / "scripts" / "regenerate_skills_bundle.py"
        spec = importlib.util.spec_from_file_location("regenerate_skills_bundle_under_test", script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        result = mod.regenerate(dry_run=True)
        # dry_run reports would-change files; on a parity-clean tree this is empty.
        changed = [p for p in result.files_written]
        self.assertEqual(
            changed,
            [],
            "regenerate_skills_bundle.py --dry-run reports pending changes; "
            "committed phase-loop-skills/ is stale vs skills-src/",
        )


if __name__ == "__main__":
    unittest.main()
