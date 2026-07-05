"""Named board presets (ABDREG, Phase 2 — lane 4).

Four built-in presets, each a named, purpose-tagged, open-ended seat list:

* ``default``     — IS ``fixtures.DEFAULT_BOARD`` (imported, not re-declared), so
                    the back-compat keystone holds by construction: the default
                    board reconstructs today's exact three seats.
* ``code-review`` — the ``fixtures/advisor-boards.example.toml`` code-review board:
                    codex ``gpt-5.5`` + an adversarial-lens claude seat.
* ``brainstorm``  — multi-vendor divergent thinking, lens-differentiated.
* ``doc-edit``    — a lighter documentation-editing board.

Every preset seat uses only REGISTERED, VALID ``(model, harness)`` pairs on the
built-3 + ``opencode`` lanes (no ``pi`` / ``cursor`` / unregistered-model seats),
so the presets pass their OWN config-time validation — ``config.load_boards()``
self-validates every preset (``tests/test_advisor_board_config.py``). Adding a
preset seat on an unregistered model or an incompatible lane would turn the suite
red, which is the intended guardrail.
"""
from __future__ import annotations

from .fixtures import DEFAULT_BOARD
from .schema import Board, Seat

# code-review: mirrors fixtures/advisor-boards.example.toml's code-review board.
CODE_REVIEW_BOARD: Board = Board(
    name="code-review",
    purpose="code-review",
    seats=(
        Seat(model="gpt-5.5", effort="max", harness="codex"),
        Seat(model="claude-sonnet-5", effort="max", harness="claude", lens="adversarial"),
    ),
)

# brainstorm: divergent, multi-vendor, each seat a different thinking lens.
BRAINSTORM_BOARD: Board = Board(
    name="brainstorm",
    purpose="brainstorm",
    seats=(
        Seat(model="claude-sonnet-5", effort="high", harness="claude", lens="adversarial"),
        Seat(model="gpt-5.5", effort="high", harness="codex", lens="supportive"),
        Seat(model="Gemini 3.1 Pro", effort="high", harness="gemini", lens="lateral"),
    ),
)

# doc-edit: a lighter documentation-editing board (structure + copyedit).
DOC_EDIT_BOARD: Board = Board(
    name="doc-edit",
    purpose="doc-edit",
    seats=(
        Seat(model="claude-sonnet-5", effort="medium", harness="claude", lens="copyedit"),
        Seat(model="gpt-5.5", effort="medium", harness="codex", lens="structure"),
    ),
)

# The built-in presets, keyed by name. ``default`` IS the shared fixture board.
PRESETS: dict[str, Board] = {
    DEFAULT_BOARD.name: DEFAULT_BOARD,
    CODE_REVIEW_BOARD.name: CODE_REVIEW_BOARD,
    BRAINSTORM_BOARD.name: BRAINSTORM_BOARD,
    DOC_EDIT_BOARD.name: DOC_EDIT_BOARD,
}

PRESET_NAMES: tuple[str, ...] = tuple(PRESETS)

# The board a bare ``advisor-board`` invocation resolves to absent a user override.
DEFAULT_BOARD_NAME: str = DEFAULT_BOARD.name


def get_preset(name: str) -> Board:
    """Return a built-in preset by name, or raise ``KeyError`` naming the known
    presets."""
    try:
        return PRESETS[name]
    except KeyError as exc:
        known = ", ".join(PRESET_NAMES)
        raise KeyError(f"unknown board preset {name!r}; known presets: {known}") from exc


__all__ = [
    "PRESETS",
    "PRESET_NAMES",
    "DEFAULT_BOARD_NAME",
    "CODE_REVIEW_BOARD",
    "BRAINSTORM_BOARD",
    "DOC_EDIT_BOARD",
    "get_preset",
]
