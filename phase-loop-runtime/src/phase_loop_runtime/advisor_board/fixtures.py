"""Shared canonical fixtures (IF-0-ABDFREEZE-2 keystone).

ONE fixture set, importable from both ``src`` and ``tests``, that ABDREG
populates *from* and ABDRESOLVE / ABDHOME test *against* — so the parallel lanes
never diverge into a mock-vs-real integration cliff.

The values here are **golden expectations**, deliberately hard-coded (not derived
from ``panel_invoker``) so a change to today's panel constants trips
``tests/test_advisor_board_backcompat.py`` instead of silently re-baselining. The
back-compat proof re-derives each default seat's invocation and asserts it equals
today's literals in ``panel_invoker`` (``DEFAULT_LEG_MODELS`` +
``_API_KEY_VARS``).
"""
from __future__ import annotations

from .schema import (
    AUTH_SUBSCRIPTION,
    BACKING_HOMEBREW,
    Board,
    Seat,
)

# The built-3 panel legs, in ``panel_invoker.PANEL_LEGS`` order — the order the
# default board's seats MUST preserve for byte-for-byte back-compat.
CANONICAL_LEG_ORDER: tuple[str, ...] = ("codex", "gemini", "claude")

# The default board's three seats — model-first, effort split out of the model
# name. These reconstruct today's ``DEFAULT_LEG_MODELS`` under
# ``harness_mapping.render_seat_invocation``:
#   codex  gpt-5.6-sol           + effort max  -> ``-c model_reasoning_effort=xhigh``
#   gemini "Gemini 3.1 Pro"  + effort high -> model ``"Gemini 3.1 Pro (High)"``
#   claude claude-fable-5    + effort max  -> ``--effort max``
#
# The claude seat runs Fable (``claude-fable-5``): pre-merge review is a mid-tier
# decision where being wrong is expensive, so the default review board reviews on
# Fable, not on the implementer model ``claude-sonnet-5``. This is byte-pinned to
# ``panel_invoker.DEFAULT_LEG_MODELS["claude"]`` (also Fable) by the golden proof.
DEFAULT_SEATS: tuple[Seat, ...] = (
    Seat(model="gpt-5.6-sol", effort="max", harness="codex",
         auth=AUTH_SUBSCRIPTION, backing=BACKING_HOMEBREW),
    Seat(model="Gemini 3.1 Pro", effort="high", harness="gemini",
         auth=AUTH_SUBSCRIPTION, backing=BACKING_HOMEBREW),
    Seat(model="claude-fable-5", effort="max", harness="claude",
         auth=AUTH_SUBSCRIPTION, backing=BACKING_HOMEBREW),
)

DEFAULT_BOARD: Board = Board(
    name="default",
    purpose="premerge-review",
    seats=DEFAULT_SEATS,
    allow_api_key_fallback=False,
)

# Golden literals the default seats must reproduce (cross-checked in the
# back-compat test against the live ``panel_invoker`` constants).
DEFAULT_SEAT_RENDERED_MODEL: dict[str, str] = {
    "codex": "gpt-5.6-sol",
    "gemini": "Gemini 3.1 Pro (High)",
    "claude": "claude-fable-5",
}
DEFAULT_SEAT_EFFORT_ARGS: dict[str, tuple[str, ...]] = {
    "codex": ("-c", "model_reasoning_effort=xhigh"),
    "gemini": (),
    "claude": ("--effort", "max"),
}

# Canonical (model x harness) pairs ABDREG's matrix + ABDRESOLVE's validation test
# against. Same-vendor-across-harness (gpt-5.6-sol on codex and opencode) is VALID and
# projects to one family; a cross-vendor mismatch (gpt-5.6-sol on claude) is INVALID.
CANONICAL_VALID_PAIRS: tuple[tuple[str, str], ...] = (
    ("gpt-5.6-sol", "codex"),
    ("gpt-5.6-sol", "opencode"),
    ("claude-sonnet-5", "claude"),
    ("Gemini 3.1 Pro", "gemini"),
)
CANONICAL_INVALID_PAIRS: tuple[tuple[str, str], ...] = (
    ("gpt-5.6-sol", "claude"),        # openai-family model on the claude lane
    ("claude-sonnet-5", "codex"),  # anthropic model on the codex lane
)

# A two-same-vendor-seat board: exercises result re-keying (leg -> seat) and the
# governed reviewer != author disjointness under model-first (both seats project
# to ``codex``). Used by ABDRESOLVE / ABDHOME.
TWO_SAME_VENDOR_BOARD: Board = Board(
    name="two-openai",
    purpose="brainstorm",
    seats=(
        Seat(model="gpt-5.6-sol", effort="high", harness="codex"),
        Seat(model="gpt-5.6-sol", effort="high", harness="opencode"),
    ),
)
