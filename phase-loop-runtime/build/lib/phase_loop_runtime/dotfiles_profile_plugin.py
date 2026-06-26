"""Dotfiles profile plugin: dotfiles-domain phase-loop CLI commands.

DECOUPLE SL-1 moves the dotfiles-specific subcommands (`adoption-bundle`,
`sync-skills`, `build-bundle`, `hotfix`) out of the generic CLI and behind this
profile-command plugin. The generic ``phase_loop_runtime.cli`` registers NONE of
them at import; they appear only when this plugin is loaded -- either via the
``phase_loop_runtime.profile_commands`` entry-point group (declared by a profile
distribution) or via the explicit ``PHASE_LOOP_PROFILE_PLUGINS`` opt-in used in
source-mode runs and tests.

This module is the dotfiles *profile*: it is allowed to depend on dotfiles-domain
runtime modules. Decoupling means the generic CLI no longer imports them, not that
they cease to exist.
"""
from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from argparse import _SubParsersAction


# Default canonical skill-bundle source roots advertised by `build-bundle --help`.
# Imported lazily inside register to keep `import phase_loop_runtime.cli` clean.


def register_profile_commands(subparsers: "_SubParsersAction") -> None:
    """Register the dotfiles-domain subcommands on an existing subparsers action.

    Each subparser carries the shared common args (via the generic CLI helper) and
    a ``func`` default that the generic dispatcher invokes as
    ``func(repo=repo, args=args, as_json=as_json)``.
    """
    from . import cli
    from .build_bundle import DEFAULT_SOURCES

    def _add_common(sub: argparse.ArgumentParser, name: str) -> None:
        cli._add_common_subparser_args(sub, name=name)

    # adoption-bundle -------------------------------------------------------
    adoption = subparsers.add_parser("adoption-bundle")
    adoption.description = "Check or refresh the committed dotfiles adoption bundle."
    adoption.add_argument("adoption_bundle_action", choices=("status", "refresh"))
    _add_common(adoption, "adoption-bundle")
    adoption.set_defaults(command="adoption-bundle", func=_adoption_bundle_dispatch)

    # sync-skills -----------------------------------------------------------
    sync = subparsers.add_parser("sync-skills")
    sync.description = "Audit or repair harness-local phase-loop bridge skills for manual reentry."
    sync.add_argument("--harness", action="append", default=[], choices=("codex", "claude", "gemini", "opencode"))
    sync.add_argument("--check", action="store_true")
    sync.add_argument("--apply", action="store_true")
    _add_common(sync, "sync-skills")
    sync.set_defaults(command="sync-skills", func=cli._sync_skills_command)

    # build-bundle ----------------------------------------------------------
    build = subparsers.add_parser("build-bundle")
    build.description = "Regenerate the harness-neutral phase-loop skills bundle from canonical harness roots."
    build.add_argument(
        "--source",
        action="append",
        help=(
            "Canonical source root. Repeat for claude, codex, gemini, and opencode. "
            "Defaults: " + ", ".join(DEFAULT_SOURCES.values())
        ),
    )
    build.add_argument("--destination", default="vendor/phase-loop-skills")
    build.add_argument("--apply", action="store_true", help="Write generated bundle files. Without --apply, this command is read-only.")
    build.add_argument("--force", action="store_true", help="Rewrite generated outputs even when content is unchanged.")
    _add_common(build, "build-bundle")
    build.set_defaults(command="build-bundle", func=cli._build_bundle_command)

    # hotfix ----------------------------------------------------------------
    hotfix = subparsers.add_parser("hotfix")
    hotfix.description = "Run a bounded emergency hotfix through runner-owned verification evidence."
    hotfix.add_argument("--reason", help="Non-secret reason recorded in the hotfix closeout event.")
    hotfix.add_argument("--plan", help="Path to a hotfix stub containing a verification_command field.")
    hotfix.add_argument("--init-stub", help="Write a minimal hotfix stub and exit without executing.")
    _add_common(hotfix, "hotfix")
    hotfix.set_defaults(command="hotfix", func=cli._hotfix_command)


# --- dispatch ------------------------------------------------------------
# sync-skills/build-bundle/hotfix bind set_defaults(func=...) straight to the
# cli._*_command handlers (signatures already match the generic dispatcher's
# func(repo=, args=, as_json=) convention). Only adoption-bundle needs an adapter,
# to map the positional adoption_bundle_action -> the handler's `action` kwarg.


def _adoption_bundle_dispatch(*, repo, args, as_json):
    from . import cli

    return cli._adoption_bundle_command(
        repo=repo, action=args.adoption_bundle_action, as_json=as_json
    )
