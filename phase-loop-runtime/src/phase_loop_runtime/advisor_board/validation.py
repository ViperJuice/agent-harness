"""Seat validation wiring — fail-fast against the frozen matrix (ABDRESOLVE lane 2).

A seat's ``(model, harness)`` pairing is validated against the frozen
``CompatibilityMatrix.is_valid`` (``registries``). An invalid pairing (e.g.
``gpt-5.6-sol`` on the ``claude`` lane) fails fast with an **actionable** diagnostic:
the matrix's rejection reason plus, when a model registry is supplied, the lanes
the model actually runs on ("did you mean ...?"). A *valid* pairing returns its
``AuthAvailability`` so the caller can degrade a valid-but-unauthed seat
(skip-with-warning) rather than block — the no-silent-key posture is enforced
downstream in ABDHOME, not silently here.

Coded against the frozen interfaces only; ABDREG supplies the populated matrix +
model registry, ABDRESOLVE tests against the fixture-backed stand-in
(``standin``).
"""
from __future__ import annotations

from dataclasses import dataclass

from .registries import (
    AuthAvailability,
    CompatibilityMatrix,
    ModelRegistry,
    UnknownModelError,
)
from .schema import EFFORT_LEVELS, Board, Seat


class SeatValidationError(ValueError):
    """A seat is not a valid ``(model, harness)`` pairing (or a board opts out of a
    lane a seat requires). Carries the per-seat diagnostics for actionable output."""

    def __init__(self, message: str, *, seat_errors: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.seat_errors = seat_errors


@dataclass(frozen=True)
class SeatVerdict:
    """The result of validating one seat: the resolved lane + its auth availability."""

    seat: Seat
    harness: str
    auth: AuthAvailability


def _resolved_lane(seat: Seat, matrix: CompatibilityMatrix) -> str:
    """The seat's execution lane: its explicit harness, else the matrix default."""
    return seat.harness or matrix.default_lane(seat.model)


def _did_you_mean(model: str, models: ModelRegistry | None) -> str:
    if models is None:
        return ""
    try:
        spec = models.get(model)
    except Exception:
        return ""
    lanes = ", ".join(spec.runnable_by)
    return f"; {model} runs on: {lanes}" if lanes else ""


def _effort_ceiling(
    seat: Seat, matrix: CompatibilityMatrix, models: ModelRegistry | None
) -> str | None:
    """The model's effort ceiling, from ``models`` if given, else from the matrix's
    own model registry (``DefaultCompatibilityMatrix`` carries ``.models``). Returns
    ``None`` when no registry is reachable (e.g. the fixture ``Standin`` matrix) or
    the model is unregistered — the ceiling gate then no-ops and the pairing gate
    still holds."""
    registry = models if models is not None else getattr(matrix, "models", None)
    if registry is None:
        return None
    try:
        return registry.get(seat.model).effort_ceiling
    except UnknownModelError:
        return None


def validate_seat(
    seat: Seat,
    matrix: CompatibilityMatrix,
    *,
    models: ModelRegistry | None = None,
) -> SeatVerdict:
    """Validate one seat against the matrix; raise ``SeatValidationError`` (with an
    actionable message) on an invalid pairing OR an over-ceiling effort, else return
    its ``SeatVerdict``.

    The effort-ceiling gate is folded in from the (now-removed) ``matrix.validate_seat``
    so there is a single canonical seat-validation API. It is enforced whenever a
    model registry is reachable (config-time uses ``DefaultCompatibilityMatrix``,
    which carries ``.models``); today every registered model ceilings at ``max`` so
    the gate is not yet load-bearing, but it is preserved so a future sub-max ceiling
    rejects at config time rather than spawning.
    """
    try:
        lane = _resolved_lane(seat, matrix)
    except UnknownModelError as exc:
        # A bare seat (no explicit harness) with an unregistered model raises from
        # default_lane; surface it as SeatValidationError so the config loader and
        # the ad-hoc seam catch a single exception type (no raw leak before spawn).
        message = f"invalid seat {seat.seat_key!r}: {exc}"
        raise SeatValidationError(message, seat_errors=(message,)) from exc
    ok, auth = matrix.is_valid(seat.model, lane)
    if not ok:
        detail = auth.detail or f"{seat.model} is not compatible with the {lane} lane"
        message = (
            f"invalid seat {seat.seat_key!r}: {detail}{_did_you_mean(seat.model, models)}"
        )
        raise SeatValidationError(message, seat_errors=(message,))
    ceiling = _effort_ceiling(seat, matrix, models)
    if ceiling is not None and EFFORT_LEVELS.index(seat.effort) > EFFORT_LEVELS.index(ceiling):
        message = (
            f"invalid seat {seat.seat_key!r}: effort {seat.effort!r} exceeds the "
            f"{seat.model!r} effort ceiling {ceiling!r} (ladder {EFFORT_LEVELS}); "
            "lower the seat's effort"
        )
        raise SeatValidationError(message, seat_errors=(message,))
    return SeatVerdict(seat=seat, harness=lane, auth=auth)


def validate_board(
    board: Board,
    matrix: CompatibilityMatrix,
    *,
    models: ModelRegistry | None = None,
) -> tuple[SeatVerdict, ...]:
    """Validate every seat on a board. Collects ALL invalid seats and raises a
    single ``SeatValidationError`` enumerating each (more actionable than failing
    on the first), else returns the per-seat verdicts in seat order.

    Note: api-key-lane seats are already rejected at ``Board`` construction unless
    the board opts in (``allow_api_key_fallback``), so a board that reaches here
    has passed the never-silent-key gate; this adds the ``(model x harness)``
    compatibility gate on top.
    """
    verdicts: list[SeatVerdict] = []
    errors: list[str] = []
    for seat in board.seats:
        try:
            verdicts.append(validate_seat(seat, matrix, models=models))
        except SeatValidationError as exc:
            errors.extend(exc.seat_errors or (str(exc),))
    if errors:
        summary = (
            f"board {board.name!r} has {len(errors)} invalid seat(s):\n  - "
            + "\n  - ".join(errors)
        )
        raise SeatValidationError(summary, seat_errors=tuple(errors))
    return tuple(verdicts)


__all__ = [
    "SeatValidationError",
    "SeatVerdict",
    "validate_seat",
    "validate_board",
]
