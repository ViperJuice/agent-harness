"""Dotfiles skill-sources plugin: the per-harness overlay roots for the bundle.

DISENTANGLE (EXTRACTSKILLS SL-2) moves the per-harness dotfiles overlay roots
(``claude-config/claude-skills``, ``codex-config/skills``, ``gemini-config/skills``,
``opencode-config/skills``) out of the generic runtime's
``skill_inventory.HARNESS_SOURCE_ROOTS`` constant and behind this provider. The
generic runtime contributes nothing on its own; these roots appear only when this
plugin is loaded -- either via the ``phase_loop_runtime.skill_sources`` entry-point
group (declared in pyproject, live in a real install) or via the explicit
``PHASE_LOOP_SKILL_SOURCE_PLUGINS`` opt-in used in source-mode runs and tests.

The neutral generated bundle is the shared-canonical artifact (it moves to
agent-harness); these 4 source roots that produce the ``_overrides/`` are the
dotfiles-OVERLAY and stay in dotfiles, contributed through this seam.

This module is the dotfiles *profile* counterpart to ``dotfiles_profile_plugin``:
it is allowed to depend on dotfiles-domain knowledge. The roots are single-sourced
from ``build_bundle.DEFAULT_SOURCES`` so the bundle builder and this overlay seam
never drift apart.
"""
from __future__ import annotations


def register_skill_sources() -> dict[str, tuple[str, ...]]:
    """Return the dotfiles per-harness overlay source roots.

    Single-sourced from ``build_bundle.DEFAULT_SOURCES`` (a ``{harness: path}`` map);
    each path is wrapped as a one-element tuple to match the
    ``{harness_target: (root, ...)}`` provider contract consumed by
    :func:`phase_loop_runtime.skill_inventory.iter_skill_source_roots`.

    Imported lazily to keep ``import phase_loop_runtime.skill_inventory`` clean,
    matching ``dotfiles_profile_plugin.register_profile_commands``.
    """
    from .build_bundle import DEFAULT_SOURCES

    return {harness: (path,) for harness, path in DEFAULT_SOURCES.items()}
