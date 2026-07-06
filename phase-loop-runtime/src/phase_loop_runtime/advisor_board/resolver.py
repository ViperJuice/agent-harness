"""Board resolution — name -> seats, settable default, ad-hoc seats (ABDRESOLVE lane 1).

Turns a board *reference* into a concrete ``Board`` of seats:

* a **name** (``"default"``, ``"code-review"``, ...) -> the named board from the
  catalog;
* **nothing** -> the settable *default* board (bare ``advisor-board``);
* an **ad-hoc** ``--seats model:effort[:harness]`` spec -> a synthesized board.

Coded against the frozen registry/matrix interface (``registries``); the harness
lane for a bare ``model:effort`` seat is resolved via
``CompatibilityMatrix.default_lane``. Until ABDREG ships the config loader +
presets, the catalog defaults to a **stand-in** built from the shared canonical
fixtures (``STANDIN_BOARDS``) so ABDRESOLVE resolves ``default`` and
``code-review`` without depending on ABDREG's live output (integration at
ABDVERIFY). A caller (or ABDREG) injects the real catalog + matrix.

``advisor-panel`` remains a working alias of the default board
(``BOARD_ALIASES``) so the historical name keeps resolving after the
advisor-panel -> advisor-board rename.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from .fixtures import DEFAULT_BOARD
from .registries import CompatibilityMatrix
from .schema import EFFORT_LEVELS, Board, Seat


class SeatSpecError(ValueError):
    """A ``--seats model:effort[:harness]`` token is malformed."""


class BoardResolutionError(KeyError):
    """A board reference names no board in the catalog (and is not an alias)."""

    def __init__(self, name: str, available: Sequence[str]) -> None:
        self.name = name
        self.available = tuple(available)
        joined = ", ".join(sorted(self.available)) or "(none)"
        super().__init__(
            f"unknown board {name!r}; available boards: {joined} "
            "(ad-hoc seats: --seats model:effort[:harness])"
        )

    def __str__(self) -> str:  # KeyError repr wraps the message in quotes otherwise
        return self.args[0]


# The historical panel name resolves to the default premerge-review board, so
# existing agent instructions that say "advisor-panel" keep working after the
# advisor-panel -> advisor-board rename.
BOARD_ALIASES: dict[str, str] = {
    "advisor-panel": "default",
    "panel": "default",
}


# --- ad-hoc seat parsing ----------------------------------------------------


def parse_seat_spec(spec: str, *, matrix: CompatibilityMatrix | None = None) -> Seat:
    """Parse one ``model:effort[:harness]`` token into a ``Seat``.

    ``model``   required, the primary key.
    ``effort``  required, a canonical level in ``EFFORT_LEVELS``.
    ``harness`` optional; when omitted the lane is resolved via
                ``matrix.default_lane(model)`` (frozen interface). With no matrix
                the seat is left lane-unresolved (``harness=None``) for the
                validation/backing phase to fill.
    """
    raw = (spec or "").strip()
    if not raw:
        raise SeatSpecError("empty seat spec; expected model:effort[:harness]")
    parts = raw.split(":")
    if len(parts) < 2 or len(parts) > 3:
        raise SeatSpecError(
            f"seat spec {spec!r} must be model:effort[:harness] (1 or 2 colons)"
        )
    model = parts[0].strip()
    effort = parts[1].strip().lower()
    harness = parts[2].strip() if len(parts) == 3 and parts[2].strip() else None
    if not model:
        raise SeatSpecError(f"seat spec {spec!r} has an empty model")
    if effort not in EFFORT_LEVELS:
        raise SeatSpecError(
            f"seat spec {spec!r} effort {effort!r} not in {EFFORT_LEVELS}"
        )
    if harness is None and matrix is not None:
        harness = matrix.default_lane(model)
    return Seat(model=model, effort=effort, harness=harness)


def parse_seats(
    specs: str | Iterable[str], *, matrix: CompatibilityMatrix | None = None
) -> tuple[Seat, ...]:
    """Parse a comma-separated string OR an iterable of ``model:effort[:harness]``
    tokens into an ordered tuple of seats (order preserved for result re-keying)."""
    if isinstance(specs, str):
        tokens = [tok for tok in (t.strip() for t in specs.split(",")) if tok]
    else:
        tokens = [str(tok).strip() for tok in specs if str(tok).strip()]
    if not tokens:
        raise SeatSpecError("no seats parsed; expected model:effort[:harness], ...")
    return tuple(parse_seat_spec(tok, matrix=matrix) for tok in tokens)


# --- stand-in board catalog (until ABDREG's config loader + presets) --------

# A stand-in ``code-review`` preset mirroring ``fixtures/advisor-boards.example.toml``
# so ``advisor-board --board code-review`` resolved in ABDRESOLVE before the config
# loader landed. SUPERSEDED by ``presets.PRESETS`` / ``config.load_boards`` (which
# ship the seven real presets — the live ``code-review`` is now three frontier
# adversarial seats on Fable, not this two-seat mirror); kept only as the ABDRESOLVE
# stand-in fixture, off every live path.
_STANDIN_CODE_REVIEW = Board(
    name="code-review",
    purpose="code-review",
    seats=(
        Seat(model="gpt-5.5", effort="max", harness="codex"),
        Seat(model="claude-sonnet-5", effort="max", harness="claude", lens="adversarial"),
    ),
)

STANDIN_BOARDS: dict[str, Board] = {
    DEFAULT_BOARD.name: DEFAULT_BOARD,
    _STANDIN_CODE_REVIEW.name: _STANDIN_CODE_REVIEW,
}


# --- resolver ---------------------------------------------------------------


class BoardResolver:
    """Resolve a board reference to seats against an (injectable) catalog + matrix.

    ``boards``          the named-board catalog; defaults to ``STANDIN_BOARDS``
                        (ABDREG injects the real config-loaded presets).
    ``default_board``   the board a bare ``advisor-board`` resolves to (settable
                        via ``set_default``).
    ``matrix``          frozen ``CompatibilityMatrix`` used to resolve a bare
                        ad-hoc seat's default lane; optional.
    ``aliases``         board-name aliases (``advisor-panel`` -> ``default`` by
                        default) so the historical name keeps resolving.
    """

    def __init__(
        self,
        boards: Mapping[str, Board] | None = None,
        *,
        default_board: str = "default",
        matrix: CompatibilityMatrix | None = None,
        aliases: Mapping[str, str] | None = None,
    ) -> None:
        self._boards: dict[str, Board] = dict(boards) if boards is not None else dict(STANDIN_BOARDS)
        self._aliases: dict[str, str] = dict(aliases) if aliases is not None else dict(BOARD_ALIASES)
        self._matrix = matrix
        self.set_default(default_board)

    @property
    def default_board(self) -> str:
        return self._default_board

    def set_default(self, name: str) -> None:
        """Set the board a bare ``advisor-board`` resolves to. Fails fast if the
        name (after alias) is not in the catalog."""
        resolved = self._resolve_name(name)
        if resolved not in self._boards:
            raise BoardResolutionError(name, self.available())
        self._default_board = resolved

    def available(self) -> tuple[str, ...]:
        """Every resolvable board name (catalog names + aliases)."""
        return tuple(self._boards) + tuple(self._aliases)

    def _resolve_name(self, name: str) -> str:
        key = (name or "").strip()
        return self._aliases.get(key, key)

    def resolve(
        self,
        name: str | None = None,
        *,
        seats: str | Iterable[str] | None = None,
    ) -> Board:
        """Resolve to a concrete ``Board``.

        * ``seats`` given  -> a synthesized ad-hoc board (``--seats`` path);
        * ``name`` is None/empty -> the settable default board;
        * else            -> the named board (alias-aware), or ``BoardResolutionError``.
        """
        if seats is not None:
            parsed = parse_seats(seats, matrix=self._matrix)
            return Board(name="ad-hoc", purpose="ad-hoc", seats=parsed)
        if name is None or not str(name).strip():
            return self._boards[self._default_board]
        resolved = self._resolve_name(name)
        try:
            return self._boards[resolved]
        except KeyError:
            raise BoardResolutionError(name, self.available()) from None


def resolve_board(
    name: str | None = None,
    *,
    seats: str | Iterable[str] | None = None,
    boards: Mapping[str, Board] | None = None,
    matrix: CompatibilityMatrix | None = None,
) -> Board:
    """One-shot convenience over ``BoardResolver`` (stand-in catalog by default)."""
    return BoardResolver(boards, matrix=matrix).resolve(name, seats=seats)


# --- leg -> seat result re-keying (expressibility for same-vendor seats) -----


def seat_result_key(seat: Seat) -> str:
    """The stable per-seat label a result is keyed by (``Seat.seat_key``)."""
    return seat.seat_key


def key_results_by_seat(
    seats: Sequence[Seat], results: Sequence["object"]
) -> tuple[tuple[Seat, object], ...]:
    """Pair a board's seats with its panel-leg results **by position**.

    The v4 ``PanelLegResult.leg`` keys results by vendor, so a board with two
    same-vendor seats (e.g. two openai seats on ``codex`` and ``opencode``) could
    not be told apart. Pairing by position, with ``seat.seat_key`` as the label,
    makes two same-vendor seats distinctly expressible. ABDHOME wires the actual
    per-seat spawn; ABDRESOLVE freezes the position<->seat_key identity.
    """
    if len(seats) != len(results):
        raise ValueError(
            f"seat/result count mismatch: {len(seats)} seats vs {len(results)} results"
        )
    return tuple((seat, result) for seat, result in zip(seats, results))


__all__ = [
    "SeatSpecError",
    "BoardResolutionError",
    "BOARD_ALIASES",
    "STANDIN_BOARDS",
    "BoardResolver",
    "resolve_board",
    "parse_seat_spec",
    "parse_seats",
    "seat_result_key",
    "key_results_by_seat",
]
