"""Shared pytest fixtures for the phase-loop-runtime test suite."""
from __future__ import annotations

import os

import pytest


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
