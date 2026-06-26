"""SL-1: dotfiles-domain commands load only via the profile-command plugin seam.

The generic CLI must register NO dotfiles-domain command (`adoption-bundle`,
`sync-skills`, `build-bundle`, `hotfix`) at import or in `--help` unless a profile
plugin is loaded -- either via the ``phase_loop_runtime.profile_commands``
entry-point group or the explicit ``PHASE_LOOP_PROFILE_PLUGINS`` opt-in (used by
tests and source-mode runs where no distribution metadata declares the group).

Importing ``phase_loop_runtime.cli`` must not pull in the dotfiles-domain modules
at module level.
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src"

DOTFILES_COMMANDS = ("adoption-bundle", "sync-skills", "build-bundle", "hotfix")
DOTFILES_OPT_IN = "phase_loop_runtime.dotfiles_profile_plugin:register_profile_commands"

# Modules cli.py must not import at its OWN module level (the SL-1 deliverable).
SOURCE_FORBIDDEN_IMPORTS = (
    "from .adoption_bundle import",
    "from .build_bundle import",
    "from .maintenance import MaintenanceOptions, SyncSkillsOptions, sync_bridge_skills",
    "from .runtime_projection import",
)

# Modules that must NOT appear in sys.modules after `import phase_loop_runtime.cli`.
# NOTE: phase_loop_runtime.maintenance is intentionally excluded -- it is NOT
# imported by cli.py directly (verified by SOURCE_FORBIDDEN_IMPORTS), but it is a
# generic run-loop dependency reachable transitively via runner.py, so it loads
# regardless. The DECOUPLE roadmap listed `maintenance` beside the pure-command
# modules; that sub-claim of IF-0-DECOUPLE-1 proved partially wrong and is recorded
# as a post-execution amendment. cli.py itself is fully decoupled.
RUNTIME_FORBIDDEN_MODULES = (
    "phase_loop_runtime.adoption_bundle",
    "phase_loop_runtime.build_bundle",
    "phase_loop_runtime.runtime_projection",
)

import pytest

# TESTDECOUPLE SL-1 (overlay-dependent): builds a skill/adoption bundle or runs the
# runtime execute path, which resolves the dotfiles skill-source / profile overlay
# (claude-config/*, codex-config/* …) absent standalone. Run-time integration: the
# conftest hook skips it when no dotfiles tree is reachable.
pytestmark = pytest.mark.dotfiles_integration


def _subcommand_names(parser) -> set[str]:
    names: set[str] = set()
    for action in parser._actions:
        if hasattr(action, "choices") and action.choices:
            try:
                names.update(action.choices.keys())
            except AttributeError:
                continue
    return names


class CliPluginGatingTest(unittest.TestCase):
    def test_generic_cli_omits_dotfiles_commands_without_plugin(self):
        from phase_loop_runtime import cli

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PHASE_LOOP_PROFILE_PLUGINS", None)
            with mock.patch(
                "phase_loop_runtime.cli._profile_command_registrars",
                return_value=(),
            ):
                parser = cli.build_parser()
        names = _subcommand_names(parser)
        for command in DOTFILES_COMMANDS:
            self.assertNotIn(
                command,
                names,
                f"{command} must NOT be registered without a profile plugin",
            )
        # generic commands still present
        self.assertIn("status", names)
        self.assertIn("version", names)

    def test_dotfiles_commands_present_with_opt_in(self):
        from phase_loop_runtime import cli

        with mock.patch.dict(
            os.environ, {"PHASE_LOOP_PROFILE_PLUGINS": DOTFILES_OPT_IN}, clear=False
        ):
            parser = cli.build_parser()
        names = _subcommand_names(parser)
        for command in DOTFILES_COMMANDS:
            self.assertIn(
                command,
                names,
                f"{command} must be registered when the dotfiles profile is opted in",
            )

    def test_dotfiles_command_dispatches_with_opt_in(self):
        from phase_loop_runtime import cli

        parser = cli.build_parser_with_profile(DOTFILES_OPT_IN)
        args = parser.parse_args(["adoption-bundle", "status", "--repo", "."])
        self.assertEqual(args.command, "adoption-bundle")
        self.assertTrue(hasattr(args, "func"))

    def test_registrars_deduped_across_entry_point_and_opt_in(self):
        # The in-tree dotfiles profile is reachable via BOTH the
        # phase_loop_runtime.profile_commands entry point AND the
        # PHASE_LOOP_PROFILE_PLUGINS opt-in (test conftest sets the same spec).
        # _profile_command_registrars must dedupe by callable identity so each
        # subparser is added exactly once.
        from phase_loop_runtime import cli
        from phase_loop_runtime import dotfiles_profile_plugin

        registrar = dotfiles_profile_plugin.register_profile_commands

        class _EP:
            name = "dotfiles"

            def load(self):
                return registrar

        def _fake_entry_points(*, group):
            if group == "phase_loop_runtime.profile_commands":
                return [_EP()]
            return []

        with mock.patch("importlib.metadata.entry_points", _fake_entry_points):
            with mock.patch.dict(
                os.environ, {"PHASE_LOOP_PROFILE_PLUGINS": DOTFILES_OPT_IN}, clear=False
            ):
                registrars = cli._profile_command_registrars()
                parser = cli.build_parser()

        self.assertEqual(
            registrars.count(registrar),
            1,
            "the dotfiles registrar must be de-duplicated across entry-point + opt-in",
        )
        # and each command appears exactly once in the parser
        choice_lists = [
            list(a.choices.keys())
            for a in parser._actions
            if getattr(a, "choices", None) and hasattr(a.choices, "keys")
        ]
        for command in DOTFILES_COMMANDS:
            total = sum(cl.count(command) for cl in choice_lists)
            self.assertEqual(total, 1, f"{command} registered {total}x (dedup regression)")

    def test_cli_source_has_no_module_level_dotfiles_imports(self):
        # The true SL-1 deliverable: cli.py registers no dotfiles-domain command at
        # import because it does not import those modules at its own module level.
        cli_source = (SRC / "phase_loop_runtime" / "cli.py").read_text(encoding="utf-8")
        # Only inspect the module-level import region (before the first def).
        header = cli_source.split("\ndef ", 1)[0]
        for needle in SOURCE_FORBIDDEN_IMPORTS:
            self.assertNotIn(
                needle,
                header,
                f"cli.py must not import {needle!r} at module level",
            )

    def test_importing_cli_does_not_import_dotfiles_command_modules(self):
        # Fresh subprocess: import cli with NO opt-in, assert the dotfiles command
        # modules never entered sys.modules at import time. (maintenance excluded;
        # see RUNTIME_FORBIDDEN_MODULES note.)
        code = (
            "import os, sys\n"
            "os.environ.pop('PHASE_LOOP_PROFILE_PLUGINS', None)\n"
            "import phase_loop_runtime.cli\n"
            "leaked = [m for m in %r if m in sys.modules]\n"
            "print('LEAKED:' + ','.join(leaked))\n"
            "sys.exit(1 if leaked else 0)\n" % (RUNTIME_FORBIDDEN_MODULES,)
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        env.pop("PHASE_LOOP_PROFILE_PLUGINS", None)
        result = subprocess.run(
            [sys.executable, "-c", code], env=env, text=True, capture_output=True
        )
        self.assertEqual(
            result.returncode,
            0,
            f"dotfiles command modules leaked at import: {result.stdout}\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
