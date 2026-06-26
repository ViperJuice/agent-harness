"""Shared pytest fixtures for the phase-loop-runtime test suite."""
from __future__ import annotations

import os

import pytest

from _dotfiles_tree import dotfiles_tree_present

# DECOUPLE SL-1: the dotfiles-domain CLI commands (adoption-bundle, sync-skills,
# build-bundle, hotfix) now load only via the dotfiles-profile plugin. The bulk of
# the suite exercises those commands through build_parser()/main() and expects them
# present, so opt the dotfiles profile in suite-wide (matching how
# phase_loop_test_utils pins PHASE_LOOP_RUNNER_REPO_ROOT). Tests that assert the
# *gating* behavior (test_phase_loop_cli_plugin_load.py) override this explicitly
# via patch.dict / build_parser_with_profile.
os.environ.setdefault(
    "PHASE_LOOP_PROFILE_PLUGINS",
    "phase_loop_runtime.dotfiles_profile_plugin:register_profile_commands",
)

# DISENTANGLE (EXTRACTSKILLS SL-2): the per-harness overlay source roots moved out
# of skill_inventory.HARNESS_SOURCE_ROOTS and behind the
# phase_loop_runtime.skill_sources seam. In source-mode (PYTHONPATH=src:tests, how
# this suite and CI run) the dist-info entry point is not live, so opt the in-tree
# dotfiles skill-sources plugin in suite-wide -- exactly as the profile plugin above
# -- so resolve_source_skill_dir / classify_skill_like_directories still resolve the
# dotfiles roots. Tests that assert the EMPTY/clean-runtime behavior override this
# explicitly (pop the env var + cache_clear).
os.environ.setdefault(
    "PHASE_LOOP_SKILL_SOURCE_PLUGINS",
    "phase_loop_runtime.skill_sources_plugin:register_skill_sources",
)


def pytest_configure(config):
    """Register the dotfiles_integration marker from conftest so it is known both
    in-tree (where pyproject.toml's [tool.pytest.ini_options] also registers it)
    and STANDALONE in the extracted agent-harness layout (where the wheel does not
    carry pyproject.toml, so the ini registration is absent and an unregistered
    marker would emit PytestUnknownMarkWarning)."""
    config.addinivalue_line(
        "markers",
        "dotfiles_integration: test requires a dotfiles fleet tree (skipped standalone)",
    )


def pytest_collection_modifyitems(config, items):
    """TESTDECOUPLE SL-0: skip ``dotfiles_integration``-marked items when no
    dotfiles fleet tree is reachable (the extracted ``agent-harness`` standalone
    layout). In-tree (the tree is present) they run unchanged.

    This run-time hook covers items whose modules import WITHOUT touching dotfiles
    paths. Integration modules that read dotfiles paths at *import* time carry an
    additional module-level ``pytest.skip(..., allow_module_level=True)`` guard
    (SL-1), because markers are only consulted after a module is imported.
    """
    if dotfiles_tree_present():
        return
    skip_marker = pytest.mark.skip(reason="requires dotfiles tree (dotfiles_integration)")
    for item in items:
        if item.get_closest_marker("dotfiles_integration") is not None:
            item.add_marker(skip_marker)


@pytest.fixture(autouse=True)
def _pin_claude_print_route_by_default():
    """Pin PHASE_LOOP_CLAUDE_ROUTE=print as the suite-wide test default.

    DFCHROUTE flipped the PRODUCTION default for an unset Claude route from
    `claude_print` to `claude_channel` (the v47-validated default). The bulk of
    the suite, however, exercises the Claude *execution* path (closeout parsing,
    auth preflight, schema delivery, repair/closeout flow) and just needs a
    runnable Claude spec — it does not care about the route default. Without an
    explicit route those tests would now resolve to `claude_channel`, which
    correctly blocks with no session and so never produces a launchable spec.

    Pinning the explicit `print` route keeps the execution-path tests testing
    execution. The route-default FLIP itself is asserted explicitly (with explicit
    env) in tests/test_phase_loop_claude_route_selection.py, and tests that need a
    different route override this via patch.dict / explicit `env=`.
    """

    # Also neutralize CI so the suite default is a deterministic interactive
    # context regardless of the host (this repo's own GitHub Actions set CI=true):
    # without this, a test that does patch.dict(..., clear=True) and then builds a
    # Claude spec would, under real CI, resolve an unset route to claude_channel and
    # block with no session — a CI-only flake. Tests that exercise the CI-block path
    # set CI explicitly via patch.dict (which overrides this).
    prior_route = os.environ.get("PHASE_LOOP_CLAUDE_ROUTE")
    prior_ci = os.environ.get("CI")
    os.environ["PHASE_LOOP_CLAUDE_ROUTE"] = "print"
    os.environ.pop("CI", None)
    try:
        yield
    finally:
        if prior_route is None:
            os.environ.pop("PHASE_LOOP_CLAUDE_ROUTE", None)
        else:
            os.environ["PHASE_LOOP_CLAUDE_ROUTE"] = prior_route
        if prior_ci is None:
            os.environ.pop("CI", None)
        else:
            os.environ["CI"] = prior_ci
