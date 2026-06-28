"""TESTDECOUPLE SL-0: detect whether a dotfiles fleet tree is reachable.

The phase-loop-runtime test suite is GREEN both in-tree (vendored under the
dotfiles checkout at ``vendor/phase-loop-runtime/``) and STANDALONE (extracted to
``agent-harness/phase-loop-runtime/`` with no dotfiles tree above it). A large
subset of the suite — the *integration* bucket — reads fleet paths that only
exist inside dotfiles (``claude-config/``, ``codex-config/``, ``scripts/``,
``specs/``, ``plans/`` …). Those tests must SKIP when no dotfiles tree is
reachable and RUN when it is.

This module is the single detector both ``conftest.py`` (the run-time skip hook
for ``dotfiles_integration``-marked items) and the per-file module-level skip
guards (for files that read dotfiles paths at *import* time, before markers are
consulted) share. It has NO dependency on the dotfiles tree itself, so it imports
cleanly standalone.

Detection: walk up from this file looking for a directory that is unambiguously a
dotfiles checkout root — it must contain BOTH ``claude-config/`` (a fleet-only
directory) AND ``bootstrap.sh`` (the fleet bootstrap entry point). Probing two
co-located markers avoids a false positive from an unrelated ``claude-config`` or
``bootstrap.sh`` somewhere on the path.
"""
from __future__ import annotations

import functools
from pathlib import Path


def _has_dotfiles_markers(candidate: Path) -> bool:
    return (candidate / "claude-config").is_dir() and (candidate / "bootstrap.sh").is_file()


@functools.lru_cache(maxsize=1)
def dotfiles_root() -> Path | None:
    """Return the dotfiles checkout root above this package, or ``None``.

    Cached: the answer is fixed for the lifetime of the process (the tree does not
    appear or disappear mid-run). Tests that need to simulate the absent case
    clear the cache (``dotfiles_root.cache_clear()``) after relocating the file.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if _has_dotfiles_markers(parent):
            return parent
    return None


def dotfiles_tree_present() -> bool:
    """True iff a dotfiles fleet tree is reachable above this package."""
    return dotfiles_root() is not None


@functools.lru_cache(maxsize=1)
def skills_bundle_present() -> bool:
    """True iff the sibling ``phase-loop-skills/`` bundle is reachable beside the runtime.

    Tests that read the workflow-skill *source* (``phase-loop-skills/**/SKILL.md``) run in
    the agent-harness monorepo, where the bundle sits beside ``phase-loop-runtime/``, but must
    SKIP in the standalone-from-wheel clean-room (``gate_a_cleanroom.sh``), which isolates only
    ``phase-loop-runtime/`` — so the bundle is absent. This is the same cross-component
    decoupling the dotfiles guard enforces, keyed on the skills sibling instead of a dotfiles
    tree (the skills bundle IS present in the extracted agent-harness layout; a dotfiles tree
    is not, so ``dotfiles_tree_present()`` is the wrong gate for these). Cached like the others.
    """
    return (Path(__file__).resolve().parents[2] / "phase-loop-skills").is_dir()
