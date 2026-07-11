"""User-editable board config loader (ABDREG, Phase 2 — lane 4).

Loads ``$XDG_CONFIG_HOME/agent-harness/advisor-boards.toml`` (the FROZEN location,
``schema.board_config_path``; format frozen by
``fixtures/advisor-boards.example.toml``) and layers user boards on top of the
built-in ``presets``. Contract:

* The four built-in presets are always present (base layer); a user ``[[boards]]``
  with the same ``name`` overrides its preset.
* ``allow_api_key_fallback`` defaults ``False`` — a board with an ``api_key`` seat
  and no opt-in is rejected by the ``Board`` schema (never-silent-key).
* **Unknown keys are a hard error, never a silent drop** — an unrecognised
  top-level, board, or seat key raises ``BoardConfigError`` naming the key.
* Every board (presets AND user boards) is validated through the compatibility
  matrix at load time, so an invalid ``(model, harness)`` pairing (e.g.
  ``claude:gpt-5.6-sol``) or an over-ceiling effort is rejected at CONFIG TIME with an
  actionable message (``validation.validate_board``).

A missing config file is not an error: the built-in presets load on their own.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # Python 3.10 — the requires-python floor
    import tomli as tomllib  # type: ignore[no-redef]

from .validation import SeatValidationError, validate_board
from .composition import compose_review_board, default_board_auth_ok
from .presets import DEFAULT_BOARD_NAME, PRESETS
from .schema import (
    AUTH_LANES,
    PROVIDER_BACKINGS,
    Board,
    Seat,
    board_config_path,
)

# Recognised keys — anything else is a hard error (no silent drop).
_KNOWN_TOP_KEYS: frozenset[str] = frozenset({"default_board", "boards"})
_KNOWN_BOARD_KEYS: frozenset[str] = frozenset(
    {"name", "purpose", "allow_api_key_fallback", "seats"}
)
_KNOWN_SEAT_KEYS: frozenset[str] = frozenset(
    {"model", "effort", "harness", "lens", "auth", "backing", "host_leg"}
)


class BoardConfigError(ValueError):
    """The board config is malformed: an unknown key, a wrong-typed value, a
    missing required field, or a board that fails matrix validation."""


@dataclass(frozen=True)
class BoardConfig:
    """Resolved board set: the built-in presets overlaid with the user's boards,
    plus the resolved ``default_board`` name."""

    boards: dict[str, Board]
    default_board: str

    def get(self, name: str | None = None) -> Board:
        """Resolve a board by name; a bare ``advisor-board`` (``name is None``)
        resolves to ``default_board``. Raises ``BoardConfigError`` naming the
        available boards for an unknown name."""
        key = name or self.default_board
        try:
            return self.boards[key]
        except KeyError as exc:
            available = ", ".join(sorted(self.boards))
            raise BoardConfigError(
                f"unknown board {key!r}; available boards: {available}"
            ) from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self.boards))


def _reject_unknown(keys, allowed: frozenset[str], where: str) -> None:
    unknown = sorted(set(keys) - allowed)
    if unknown:
        raise BoardConfigError(
            f"unknown config key(s) {unknown} in {where}; "
            f"recognised keys: {sorted(allowed)}"
        )


def _require_bool(raw: Mapping[str, Any], key: str, default: bool, where: str) -> bool:
    """Read a boolean config key STRICTLY: an absent key yields ``default``, a
    present key MUST be a literal TOML boolean. Never coerce a non-bool scalar —
    ``bool("false")`` is ``True``, so coercion would silently flip an opt-in gate
    (e.g. a quoted ``allow_api_key_fallback = "false"`` would enable the api-key
    fallback). A wrong-typed value is a hard ``BoardConfigError`` naming the key."""
    if key not in raw:
        return default
    value = raw[key]
    if not isinstance(value, bool):
        raise BoardConfigError(
            f"{where}: {key!r} must be a boolean (true/false), got "
            f"{type(value).__name__} {value!r}"
        )
    return value


def _parse_seat(raw: Mapping[str, Any], where: str) -> Seat:
    if not isinstance(raw, Mapping):
        raise BoardConfigError(f"{where} must be a table, got {type(raw).__name__}")
    _reject_unknown(raw.keys(), _KNOWN_SEAT_KEYS, where)
    if "model" not in raw:
        raise BoardConfigError(f"{where} is missing the required 'model' key")
    if "effort" not in raw:
        raise BoardConfigError(f"{where} (model={raw['model']!r}) is missing the required 'effort' key")
    auth = raw.get("auth", AUTH_LANES[0])
    backing = raw.get("backing", PROVIDER_BACKINGS[0])
    try:
        return Seat(
            model=str(raw["model"]),
            effort=str(raw["effort"]),
            harness=(str(raw["harness"]) if raw.get("harness") is not None else None),
            lens=(str(raw["lens"]) if raw.get("lens") is not None else None),
            auth=str(auth),
            backing=str(backing),
            host_leg=_require_bool(raw, "host_leg", False, where),
        )
    except (ValueError, TypeError) as exc:
        # Seat.__post_init__ fail-closed validation (bad effort/auth/backing) ->
        # surface as a config error at load time.
        raise BoardConfigError(f"{where}: {exc}") from exc


def _parse_board(raw: Mapping[str, Any], index: int) -> Board:
    where = f"boards[{index}]"
    if not isinstance(raw, Mapping):
        raise BoardConfigError(f"{where} must be a table, got {type(raw).__name__}")
    _reject_unknown(raw.keys(), _KNOWN_BOARD_KEYS, where)
    if "name" not in raw:
        raise BoardConfigError(f"{where} is missing the required 'name' key")
    name = str(raw["name"])
    seats_raw = raw.get("seats", [])
    if not isinstance(seats_raw, list):
        raise BoardConfigError(f"board {name!r} 'seats' must be a list of seat tables")
    seats = tuple(
        _parse_seat(seat, f"board {name!r} seats[{i}]") for i, seat in enumerate(seats_raw)
    )
    try:
        return Board(
            name=name,
            purpose=str(raw.get("purpose", "")),
            seats=seats,
            allow_api_key_fallback=_require_bool(
                raw, "allow_api_key_fallback", False, where
            ),
        )
    except (ValueError, TypeError) as exc:
        # Board.__post_init__ (e.g. api_key seat without opt-in) -> config error.
        raise BoardConfigError(f"board {name!r}: {exc}") from exc


def load_boards(
    path: Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    matrix: Any | None = None,
    validate: bool = True,
    is_available: "Callable[[str], bool] | None" = None,
    auth_ok: "Callable[[str], bool] | None" = None,
) -> BoardConfig:
    """Load the board config, layering user boards over the built-in presets.

    ``path`` defaults to ``board_config_path(env)``. A missing file yields just the
    presets. ``matrix`` defaults to ``matrix.default_matrix()``; pass ``validate=
    False`` only for shape-parsing without availability (kept for tests). Raises
    ``BoardConfigError`` for any unknown key, malformed value, or matrix-invalid
    board — never a silent drop.

    The ``code-review`` board is composed AVAILABILITY-AWARE (down vendors are
    backfilled with distinct lenses onto available vendors, so a convened panel
    never collapses to 1–2 reviewers) via ``composition.compose_review_board``.
    ``is_available(vendor) -> bool`` decides which vendors are up; when omitted it
    is taken from a probe-backed ``matrix`` (its harness registry probe, so the
    availability view is single-sourced) and otherwise defaults to the advisor-
    board PATH probe. Composition happens BEFORE the user overlay, so a
    user-defined ``code-review`` board still wins.

    ``auth_ok(vendor) -> bool`` (REVIEWGOV-W1 / #151) additionally gates the
    composed ``code-review`` board on AUTHENTICATION so a PATH-present-but-unauthed
    vendor is dropped and backfilled. When omitted it defaults to
    ``composition.default_board_auth_ok`` — the cached, timeout-bounded, fail-closed
    ``auth_ok_for`` gate — so the LIVE convening path is genuinely auth-aware (the
    real fix for #151). The gate only runs for vendors that pass the availability
    (PATH) probe, so a host with no vendor CLI installed short-circuits without
    shelling out. Inject ``auth_ok`` (e.g. ``lambda _v: True``) to isolate the
    availability dimension in a test.
    """
    boards: dict[str, Board] = dict(PRESETS)
    default_board = DEFAULT_BOARD_NAME

    # Availability-aware code-review (before the user overlay so a user override wins).
    compose_probe = is_available
    if compose_probe is None and matrix is not None:
        compose_probe = getattr(getattr(matrix, "harnesses", None), "is_available", None)
    # #151: the LIVE convening path is auth-aware by DEFAULT — a PATH-present but
    # unauthenticated vendor is dropped and backfilled. Pass an explicit ``auth_ok``
    # so the composer never falls through to its is_available-injected pass-through
    # affordance (which exists only for the static presets / simulation tests). The
    # gate short-circuits for vendors that fail the availability probe, so a host
    # with no vendor CLI never shells out.
    compose_auth = auth_ok if auth_ok is not None else default_board_auth_ok
    composed_review = compose_review_board(is_available=compose_probe, auth_ok=compose_auth)
    boards[composed_review.name] = composed_review

    cfg_path = path if path is not None else board_config_path(env)
    if cfg_path.exists():
        with open(cfg_path, "rb") as fh:
            try:
                data = tomllib.load(fh)
            except tomllib.TOMLDecodeError as exc:
                raise BoardConfigError(f"{cfg_path} is not valid TOML: {exc}") from exc
        _reject_unknown(data.keys(), _KNOWN_TOP_KEYS, str(cfg_path))
        raw_boards = data.get("boards", [])
        if not isinstance(raw_boards, list):
            raise BoardConfigError(f"{cfg_path}: 'boards' must be an array of tables")
        for i, raw in enumerate(raw_boards):
            board = _parse_board(raw, i)
            boards[board.name] = board  # user board overrides a same-named preset
        if "default_board" in data:
            default_board = str(data["default_board"])

    if default_board not in boards:
        available = ", ".join(sorted(boards))
        raise BoardConfigError(
            f"default_board {default_board!r} is not a defined board; "
            f"available boards: {available}"
        )

    if validate:
        if matrix is None:
            from .matrix import default_matrix

            matrix = default_matrix(env=env)
        for board in boards.values():
            try:
                validate_board(board, matrix=matrix)
            except SeatValidationError as exc:
                # Surface matrix-level rejections under the config error type so a
                # caller catches one exception for any load-time failure.
                raise BoardConfigError(str(exc)) from exc

    return BoardConfig(boards=boards, default_board=default_board)


__all__ = [
    "BoardConfig",
    "BoardConfigError",
    "load_boards",
]
