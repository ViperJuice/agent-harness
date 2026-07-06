"""Named board presets (ABDREG, Phase 2 — lane 4).

Nine built-in presets, each a named, purpose-tagged, open-ended seat list:

* ``default``     — IS ``fixtures.DEFAULT_BOARD`` (imported, not re-declared), so
                    the back-compat keystone holds by construction: the default
                    board reconstructs today's exact three seats (the claude seat
                    on Fable, ``claude-fable-5``).
* ``code-review`` — three frontier vendors, each adversarial: codex ``gpt-5.5``,
                    ``Gemini 3.1 Pro``, and ``claude-fable-5``.
* ``brainstorm``  — multi-vendor divergent thinking, lens-differentiated.
* ``doc-edit``    — a lighter documentation-editing board.
* ``legal-review`` / ``legal-strategy-review`` / ``legal-brainstorm`` — the legal
                    boards (see below).

**Review-class boards run on frontier models, never the implementer.** Pre-merge
and legal review are mid-tier decisions where being wrong is expensive, so the
review-class boards (``default``, ``code-review``, ``legal-review``,
``legal-strategy-review``) seat Fable (``claude-fable-5``) on the claude lane, not
the implementer ``claude-sonnet-5``. The divergent-thinking boards (``brainstorm``,
``doc-edit``, ``legal-brainstorm``) deliberately KEEP Sonnet — a diverse voice / a
low-stakes copyedit / an aggressive-but-cheap ideation seat — where it is the right
tool.

Every preset seat uses only REGISTERED, VALID ``(model, harness)`` pairs on the
built-3 + ``opencode`` lanes (no ``pi`` / ``cursor`` / unregistered-model seats),
so the presets pass their OWN config-time validation — ``config.load_boards()``
self-validates every preset (``tests/test_advisor_board_config.py``). Adding a
preset seat on an unregistered model or an incompatible lane would turn the suite
red, which is the intended guardrail. ``lens`` and ``purpose`` are free-form
strings (``schema.py``): the legal lenses/purposes below need no enum extension.
"""
from __future__ import annotations

from .fixtures import DEFAULT_BOARD
from .schema import Board, Seat

# code-review: review-class = three frontier vendors, always. Each seat carries the
# adversarial lens (find-the-bug framing); the claude seat is Fable, not the
# implementer. This supersedes the old two-seat (codex + sonnet) composition.
CODE_REVIEW_BOARD: Board = Board(
    name="code-review",
    purpose="code-review",
    seats=(
        Seat(model="gpt-5.5", effort="max", harness="codex", lens="adversarial"),
        Seat(model="Gemini 3.1 Pro", effort="high", harness="gemini", lens="adversarial"),
        Seat(model="claude-fable-5", effort="max", harness="claude", lens="adversarial"),
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

# --- legal boards ----------------------------------------------------------
#
# Each seat below encodes the PRIMARY review lens per vendor. The richer treatment
# — four lenses per seat, an apex-Opus seat, a verify-round, and retrieval-grounded
# citation-verification — is a documented deep-seat FOLLOW-ON (see CONTRACTS.md),
# intentionally NOT built here. Review-class legal boards seat Fable on claude;
# legal-brainstorm keeps Sonnet as a cheap aggressive ideation voice.

# legal-review: document/contract review. Opposing-counsel adversary, risk/liability
# scan, and authority (citation/precedent) verification.
LEGAL_REVIEW_BOARD: Board = Board(
    name="legal-review",
    purpose="legal-review",
    seats=(
        Seat(model="gpt-5.5", effort="max", harness="codex", lens="opposing-counsel"),
        Seat(model="Gemini 3.1 Pro", effort="high", harness="gemini", lens="risk-liability"),
        Seat(model="claude-fable-5", effort="max", harness="claude", lens="authority-verification"),
    ),
)

# legal-strategy-review: red-team a legal strategy — attack it, surface alternatives,
# and stress the downside / ethical exposure.
LEGAL_STRATEGY_REVIEW_BOARD: Board = Board(
    name="legal-strategy-review",
    purpose="legal-strategy-review",
    seats=(
        Seat(model="gpt-5.5", effort="max", harness="codex", lens="red-team"),
        Seat(model="Gemini 3.1 Pro", effort="high", harness="gemini", lens="alternatives"),
        Seat(model="claude-fable-5", effort="max", harness="claude", lens="downside-ethics"),
    ),
)

# legal-brainstorm: divergent legal ideation. KEEPS Sonnet (aggressive, cheap voice)
# alongside a conservative and a creative seat.
LEGAL_BRAINSTORM_BOARD: Board = Board(
    name="legal-brainstorm",
    purpose="legal-brainstorm",
    seats=(
        Seat(model="claude-sonnet-5", effort="high", harness="claude", lens="aggressive"),
        Seat(model="gpt-5.5", effort="high", harness="codex", lens="conservative"),
        Seat(model="Gemini 3.1 Pro", effort="high", harness="gemini", lens="creative"),
    ),
)

# --- general-purpose catch-alls -------------------------------------------
#
# For use cases we have NOT pre-modeled, so the board library is open-ended rather
# than limited to the named domains. Both default to TOP-END models: an unanticipated
# task cannot be assumed low-stakes, so the safe default is frontier — dial down
# explicitly (a cheaper board or max_concurrency aside) when a task is known-cheap.

# general: the domain-agnostic top-tier PANEL. Three frontier vendors with generic
# critical lenses (adversarial / alternative-angle / completeness) — hand it any
# task + brief and it convenes a cross-vendor frontier review.
GENERAL_BOARD: Board = Board(
    name="general",
    purpose="general",
    seats=(
        Seat(model="gpt-5.5", effort="max", harness="codex", lens="adversarial"),
        Seat(model="Gemini 3.1 Pro", effort="high", harness="gemini", lens="alternative"),
        Seat(model="claude-fable-5", effort="max", harness="claude", lens="completeness"),
    ),
)

# solo: the general-purpose single MEMBER — one quick top-end opinion when a full
# panel is overkill. A one-seat board resolves + validates like any other.
SOLO_BOARD: Board = Board(
    name="solo",
    purpose="general",
    seats=(
        Seat(model="claude-fable-5", effort="max", harness="claude", lens="completeness"),
    ),
)

# The built-in presets, keyed by name. ``default`` IS the shared fixture board.
PRESETS: dict[str, Board] = {
    DEFAULT_BOARD.name: DEFAULT_BOARD,
    CODE_REVIEW_BOARD.name: CODE_REVIEW_BOARD,
    BRAINSTORM_BOARD.name: BRAINSTORM_BOARD,
    DOC_EDIT_BOARD.name: DOC_EDIT_BOARD,
    LEGAL_REVIEW_BOARD.name: LEGAL_REVIEW_BOARD,
    LEGAL_STRATEGY_REVIEW_BOARD.name: LEGAL_STRATEGY_REVIEW_BOARD,
    LEGAL_BRAINSTORM_BOARD.name: LEGAL_BRAINSTORM_BOARD,
    GENERAL_BOARD.name: GENERAL_BOARD,
    SOLO_BOARD.name: SOLO_BOARD,
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
    "LEGAL_REVIEW_BOARD",
    "LEGAL_STRATEGY_REVIEW_BOARD",
    "LEGAL_BRAINSTORM_BOARD",
    "GENERAL_BOARD",
    "SOLO_BOARD",
    "get_preset",
]
