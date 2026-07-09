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
) -> BoardConfig:
    """Load the board config, layering user boards over the built-in presets.

    ``path`` defaults to ``board_config_path(env)``. A missing file yields just the
    presets. ``matrix`` defaults to ``matrix.default_matrix()``; pass ``validate=
    False`` only for shape-parsing without availability (kept for tests). Raises
    ``BoardConfigError`` for any unknown key, malformed value, or matrix-invalid
    board — never a silent drop.
    """
    boards: dict[str, Board] = dict(PRESETS)
    default_board = DEFAULT_BOARD_NAME

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
