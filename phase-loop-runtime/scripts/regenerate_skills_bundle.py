#!/usr/bin/env python3
"""Regenerate the committed neutral skill bundle from the canonical in-repo sources.

CANON / IF-0-CANON-2 — the one-command regenerate for the upstream half of the
skill-bundle pipeline:

    skills-src/<harness>/        (authored canonical source, IF-0-CANON-1)
        │  build_bundle (neutral base + per-harness _overrides)
        ▼
    phase-loop-skills/           (committed bundle, parity-gated)
        │  scripts/sync_skills_bundle.py
        ▼
    src/phase_loop_runtime/skills_bundle/   (ships in the wheel)

This script owns the FIRST arrow. It runs ``build_bundle`` over the in-repo
``skills-src/`` roots (``DEFAULT_SOURCES``) and writes the result into the
committed ``phase-loop-skills/`` directory. It is SELF-CONTAINED: no dotfiles
checkout is required.

Re-run after editing anything under ``skills-src/``:

    python phase-loop-runtime/scripts/regenerate_skills_bundle.py

The CI parity gate (``tests/test_skills_canon_parity.py``) fails if the committed
``phase-loop-skills/`` is not byte-identical to a fresh run of this regenerate.
After regenerating ``phase-loop-skills/``, also re-run
``scripts/sync_skills_bundle.py`` to refresh the packaged ``skills_bundle/``.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[1]          # phase-loop-runtime/
_REPO_ROOT = _PKG_ROOT.parent                            # agent-harness/
DEST_BUNDLE = _REPO_ROOT / "phase-loop-skills"

sys.path.insert(0, str(_PKG_ROOT / "src"))
from phase_loop_runtime.build_bundle import DEFAULT_SOURCES, build_bundle  # noqa: E402


def canonical_sources() -> dict[str, Path]:
    """The in-repo per-harness source roots, anchored at the agent-harness root.

    ``DEFAULT_SOURCES`` holds repo-relative paths (``skills-src/<harness>``); anchor
    them here so the regenerate works regardless of the caller's CWD.
    """
    return {harness: _REPO_ROOT / rel for harness, rel in DEFAULT_SOURCES.items()}


def regenerate(dest: Path = DEST_BUNDLE, *, dry_run: bool = False):
    """Build the neutral bundle from the canonical sources into ``dest``.

    With ``dry_run=True`` nothing is written; the returned ``BuildResult`` reports
    what *would* change (used by the parity gate to assert a zero diff).
    """
    return build_bundle(
        canonical_sources(),
        dest,
        dry_run=dry_run,
        apply=not dry_run,
        # force: rewrite even unchanged files so the committed tree is a pure
        # function of the sources (no stale leftovers survive a rename/removal).
        force=not dry_run,
    )


def main() -> int:
    for harness, root in canonical_sources().items():
        if not root.is_dir():
            print(f"canonical source root missing for {harness}: {root}", file=sys.stderr)
            return 1
    result = regenerate()
    if result.skills_skipped:
        for skipped in result.skills_skipped:
            print(
                f"skipped {skipped.skill}: missing {', '.join(skipped.missing_harnesses)}",
                file=sys.stderr,
            )
        return 1
    print(
        f"regenerated {len(result.skills_regenerated)} skills into "
        f"{DEST_BUNDLE.relative_to(_REPO_ROOT)} "
        f"({len(result.files_written)} files)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
