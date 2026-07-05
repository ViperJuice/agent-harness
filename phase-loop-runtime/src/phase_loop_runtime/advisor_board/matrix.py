"""(model x harness) compatibility + auth-availability matrix + config-time
validation (ABDREG, Phase 2 — lane 3).

Populates the frozen ``CompatibilityMatrix`` interface (registries.py:101-115):

* ``is_valid(model, harness) -> (ok, AuthAvailability)`` — ``ok`` is whether the
  pairing is *expressible at all* (an ``openai``-family model on the ``claude``
  lane is not); ``AuthAvailability`` reports which credential lanes are actually
  usable so a valid-but-unauthed seat degrades (skip-with-warning) rather than
  blocking. ``is_valid`` is TOTAL — it never raises, returning ``(False, …)`` with
  a ``detail`` for an unknown/incompatible pairing.
* ``default_lane(model)`` — the harness a bare ``model`` seat resolves onto,
  delegated to the model registry.

Validity is model-first: a pairing is valid iff the harness's lane vendor-family
matches the model's vendor-family (the FROZEN ``schema.vendor_family`` projection
that also keeps the governed reviewer≠author disjointness intact). That makes
``gpt-5.5`` valid on both ``codex`` and ``opencode`` (same family) but invalid on
``claude`` — exactly ``fixtures.CANONICAL_VALID_PAIRS`` /
``CANONICAL_INVALID_PAIRS``.

``validate_seat`` / ``validate_board`` are the CONFIG-TIME gate: they resolve a
seat's lane (``harness or default_lane(model)``), reject an invalid pairing or an
over-ceiling effort with an actionable message, and are what the config loader
(``config.py``) runs on every board — presets included.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .backing import VENDOR_API_KEY_VARS
from .registries import (
    DEFAULT_HARNESS_REGISTRY,
    DEFAULT_MODEL_REGISTRY,
    AuthAvailability,
    CompatibilityMatrix,
    HarnessRegistry,
    MatrixVerdict,
    ModelRegistry,
    UnknownHarnessError,
    UnknownModelError,
)
from .schema import (
    AUTH_API_KEY,
    AUTH_SUBSCRIPTION,
    EFFORT_LEVELS,
    Board,
    Seat,
    vendor_family,
    vendor_of_harness,
)


class SeatValidationError(ValueError):
    """A seat cannot be expressed against the registries/matrix (invalid
    ``(model, harness)`` pairing, unknown model, or effort above the model's
    ceiling). Raised at CONFIG TIME with an actionable message."""


@dataclass
class DefaultCompatibilityMatrix:
    """Populated ``CompatibilityMatrix`` over the six-harness / model registries.

    ``env`` (default ``os.environ``) and ``probe`` (default the harness
    registry's PATH probe) are injectable so auth-availability is deterministic in
    tests: the api-key lane is reported available ONLY when the vendor's key var
    is actually present in ``env`` (so no-silent-key is testable, not asserted),
    and the subscription lane ONLY when the harness CLI is on PATH.
    """

    harnesses: HarnessRegistry = DEFAULT_HARNESS_REGISTRY
    models: ModelRegistry = DEFAULT_MODEL_REGISTRY
    env: Mapping[str, str] | None = None

    def _env(self) -> Mapping[str, str]:
        return os.environ if self.env is None else self.env

    def default_lane(self, model: str) -> str:
        # Delegates to the model registry, which raises UnknownModelError (with
        # the known-model list) for an unregistered model — the unknown-model
        # config-time rejection.
        return self.models.default_lane(model)

    def _auth_availability(self, model: str, harness_spec) -> AuthAvailability:
        env = self._env()
        sub = AUTH_SUBSCRIPTION in harness_spec.auth_lanes and self.harnesses.is_available(
            harness_spec.name
        )
        vendor = vendor_family(model, harness_spec.name)
        key_vars = VENDOR_API_KEY_VARS.get(vendor, ())
        api = (
            AUTH_API_KEY in harness_spec.auth_lanes
            and bool(key_vars)
            and any(var in env for var in key_vars)
        )
        bits = []
        if sub:
            bits.append("subscription")
        if api:
            bits.append("api_key")
        detail = ("usable lanes: " + ", ".join(bits)) if bits else (
            f"no usable auth lane for {model!r} on {harness_spec.name!r} "
            "(no subscription CLI on PATH, no vendor api key) — seat will skip-with-warning"
        )
        return AuthAvailability(subscription=sub, api_key=api, detail=detail)

    def is_valid(self, model: str, harness: str) -> MatrixVerdict:
        try:
            self.models.get(model)
        except UnknownModelError as exc:
            return (False, AuthAvailability(detail=str(exc)))
        try:
            harness_spec = self.harnesses.get(harness)
        except UnknownHarnessError as exc:
            return (False, AuthAvailability(detail=str(exc)))
        model_family = vendor_family(model, harness)
        lane_family = vendor_of_harness(harness)
        if model_family != lane_family:
            valid_lanes = ", ".join(self.models.get(model).runnable_by) or "(none)"
            detail = (
                f"model {model!r} ({model_family} family) cannot run on harness "
                f"{harness!r} ({lane_family} family); valid harness lanes for "
                f"{model!r}: {valid_lanes}"
            )
            return (False, AuthAvailability(detail=detail))
        return (True, self._auth_availability(model, harness_spec))


def default_matrix(
    *,
    env: Mapping[str, str] | None = None,
    probe: Callable[[str], bool] | None = None,
) -> DefaultCompatibilityMatrix:
    """The matrix over the default six-harness / model registries."""
    from .registries import DefaultHarnessRegistry

    harnesses = DefaultHarnessRegistry(probe=probe) if probe is not None else DEFAULT_HARNESS_REGISTRY
    return DefaultCompatibilityMatrix(harnesses=harnesses, models=DEFAULT_MODEL_REGISTRY, env=env)


# --- config-time seat/board validation --------------------------------------


def resolved_lane(seat: Seat, matrix: CompatibilityMatrix) -> str:
    """The lane a seat runs on: its explicit ``harness`` or, for a bare seat,
    ``default_lane(model)``. Raises ``UnknownModelError`` (via ``default_lane``)
    for an unregistered bare-seat model."""
    return seat.harness or matrix.default_lane(seat.model)


def validate_seat(
    seat: Seat,
    *,
    matrix: CompatibilityMatrix | None = None,
    models: ModelRegistry | None = None,
) -> AuthAvailability:
    """Reject an inexpressible seat at CONFIG TIME with an actionable message.

    Checks, in order: (1) model is registered and its lane resolves (unknown
    model -> clear error); (2) the ``(model, lane)`` pairing is valid on the
    matrix (invalid pairing such as ``gpt-5.5`` on ``claude`` -> clear error
    naming the valid lanes); (3) the seat's effort does not exceed the model's
    effort ceiling. Returns the pair's ``AuthAvailability`` (a valid but unauthed
    seat is NOT rejected here — it degrades to skip-with-warning at launch).
    """
    matrix = matrix or default_matrix()
    models = models or DEFAULT_MODEL_REGISTRY
    try:
        lane = resolved_lane(seat, matrix)
    except UnknownModelError as exc:
        raise SeatValidationError(str(exc)) from exc
    ok, avail = matrix.is_valid(seat.model, lane)
    if not ok:
        raise SeatValidationError(avail.detail or f"invalid seat {seat.seat_key}")
    ceiling = models.get(seat.model).effort_ceiling
    if EFFORT_LEVELS.index(seat.effort) > EFFORT_LEVELS.index(ceiling):
        raise SeatValidationError(
            f"seat effort {seat.effort!r} exceeds the {seat.model!r} effort ceiling "
            f"{ceiling!r} (ladder {EFFORT_LEVELS}); lower the seat's effort"
        )
    return avail


def validate_board(
    board: Board,
    *,
    matrix: CompatibilityMatrix | None = None,
    models: ModelRegistry | None = None,
) -> None:
    """Validate every seat of a board; raise on the first inexpressible seat with
    the board name prefixed for a locatable diagnostic. This is what the config
    loader runs on every board (presets included), so an invalid preset or an
    invalid user board fails at load time rather than silently."""
    matrix = matrix or default_matrix()
    models = models or DEFAULT_MODEL_REGISTRY
    for index, seat in enumerate(board.seats):
        try:
            validate_seat(seat, matrix=matrix, models=models)
        except SeatValidationError as exc:
            raise SeatValidationError(
                f"board {board.name!r} seat #{index} ({seat.seat_key}): {exc}"
            ) from exc


__all__ = [
    "DefaultCompatibilityMatrix",
    "default_matrix",
    "SeatValidationError",
    "resolved_lane",
    "validate_seat",
    "validate_board",
]
