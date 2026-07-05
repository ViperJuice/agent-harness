"""Seat validation wiring — fail-fast against the frozen matrix (ABDRESOLVE lane 2).

A seat's ``(model, harness)`` pairing is validated against the frozen
``CompatibilityMatrix.is_valid`` (``registries``). An invalid pairing (e.g.
``gpt-5.5`` on the ``claude`` lane) fails fast with an **actionable** diagnostic:
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

from .registries import AuthAvailability, CompatibilityMatrix, ModelRegistry
from .schema import Board, Seat


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


def validate_seat(
    seat: Seat,
    matrix: CompatibilityMatrix,
    *,
    models: ModelRegistry | None = None,
) -> SeatVerdict:
    """Validate one seat against the matrix; raise ``SeatValidationError`` (with an
    actionable message) on an invalid pairing, else return its ``SeatVerdict``."""
    lane = _resolved_lane(seat, matrix)
    ok, auth = matrix.is_valid(seat.model, lane)
    if not ok:
        detail = auth.detail or f"{seat.model} is not compatible with the {lane} lane"
        message = (
            f"invalid seat {seat.seat_key!r}: {detail}{_did_you_mean(seat.model, models)}"
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
