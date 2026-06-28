#!/usr/bin/env python3
"""Regenerate the packaged neutral skill bundle shipped inside the wheel (fix #12).

Assembles the neutral ``phase-loop-skills/`` source into
``src/phase_loop_runtime/skills_bundle/<harness>-<skill>/`` for every active
harness, using the same overlay-folding assembler as ``phase-loop install``
(``skill_install.install_skills``). Shipping the assembled bundle as package-data
lets a pinned ``pip install`` resolve the workflow skills (``run``/``dry-run``)
without a dotfiles overlay.

This output is generated, not hand-edited. Re-run after changing
``phase-loop-skills/``:

    python phase-loop-runtime/scripts/sync_skills_bundle.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[1]          # phase-loop-runtime/
_REPO_ROOT = _PKG_ROOT.parent                            # agent-harness/
SRC_SKILLS = _REPO_ROOT / "phase-loop-skills"
DEST = _PKG_ROOT / "src" / "phase_loop_runtime" / "skills_bundle"

sys.path.insert(0, str(_PKG_ROOT / "src"))
from phase_loop_runtime.build_bundle import ACTIVE_HARNESSES  # noqa: E402
from phase_loop_runtime.skill_install import install_skills  # noqa: E402


def main() -> int:
    if not SRC_SKILLS.is_dir():
        print(f"source skills bundle not found: {SRC_SKILLS}", file=sys.stderr)
        return 1
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)
    for harness in ACTIVE_HARNESSES:
        install_skills(harness=harness, source=SRC_SKILLS, destination=DEST, mode="copy", apply=True)
    dirs = sorted(p.name for p in DEST.iterdir() if p.is_dir())
    print(f"assembled {len(dirs)} skill dirs into {DEST.relative_to(_REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
