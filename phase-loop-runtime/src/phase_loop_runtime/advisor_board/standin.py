"""Fixture-backed stand-in registries + matrix (ABDRESOLVE / ABDHOME test support).

ABDREG (Phase 2) populates the *real* six-harness registry, model registry, and
``(model x harness)`` compatibility matrix behind the frozen Protocols in
``registries``. Until then, ABDRESOLVE codes against those Protocols using a
stand-in built **from the shared canonical fixtures** (``fixtures.py``) — the
ABDFREEZE anti-divergence keystone. Because the stand-in is derived from the same
``CANONICAL_VALID_PAIRS`` / ``CANONICAL_INVALID_PAIRS`` / ``DEFAULT_SEATS`` that
ABDREG populates from, the resolver/validation logic verified here integrates with
ABDREG's real data at ABDVERIFY without a mock-vs-real cliff.

This module is deliberately thin and additive: it introduces no running path and
is imported only by the resolver's default catalog and by tests. ABDREG replaces
these with populated implementations; ABDRESOLVE never depends on ABDREG's live
output (integration is at ABDVERIFY).
"""
from __future__ import annotations

from dataclasses import dataclass

from .fixtures import (
    CANONICAL_INVALID_PAIRS,
    CANONICAL_VALID_PAIRS,
    DEFAULT_SEATS,
)
from .registries import (
    AuthAvailability,
    HarnessSpec,
    MatrixVerdict,
    ModelSpec,
)
from .schema import (
    AUTH_SUBSCRIPTION,
    BACKING_HOMEBREW,
    vendor_family,
    vendor_of_harness,
    vendor_of_model,
)


# The default lane a bare ``model`` seat resolves onto, derived from the canonical
# default seats (the built-3) plus the same-vendor-across-harness valid pairs. This
# is the stand-in for ``ModelRegistry.default_lane`` / ``CompatibilityMatrix.default_lane``.
_DEFAULT_LANE: dict[str, str] = {seat.model: seat.harness or "" for seat in DEFAULT_SEATS}


def _default_lane_for(model: str) -> str:
    lane = _DEFAULT_LANE.get(model)
    if lane:
        return lane
    # Fall back to the first canonical valid lane for the model, else the vendor family.
    for pair_model, pair_harness in CANONICAL_VALID_PAIRS:
        if pair_model == model:
            return pair_harness
    return vendor_family(model)


@dataclass(frozen=True)
class StandinCompatibilityMatrix:
    """A ``CompatibilityMatrix`` (frozen Protocol) backed by the canonical fixtures.

    ``is_valid`` accepts every ``CANONICAL_VALID_PAIRS`` entry (and any
    same-vendor-family pairing derivable from them), rejects every
    ``CANONICAL_INVALID_PAIRS`` entry, and rejects an unknown pairing as a
    cross-vendor mismatch. The returned ``AuthAvailability`` reports the
    subscription lane available for a valid pair (the default lane) so a
    valid-but-unauthed seat is *expressible* distinctly from an invalid pairing.
    """

    def is_valid(self, model: str, harness: str) -> MatrixVerdict:
        pair = (model, harness)
        if pair in CANONICAL_INVALID_PAIRS:
            return (False, AuthAvailability(detail=f"{model} does not run on the {harness} lane"))
        if pair in CANONICAL_VALID_PAIRS:
            return (True, AuthAvailability(subscription=True, detail="subscription"))
        # Not enumerated: valid iff the model's vendor family matches the harness
        # lane's family (the same rule the canonical pairs encode) — so gpt-5.6-sol on
        # any openai-family lane is valid, gpt-5.6-sol on claude is not.
        model_fam = vendor_of_model(model)
        lane_fam = vendor_of_harness(harness)
        if model_fam and model_fam == lane_fam:
            return (True, AuthAvailability(subscription=True, detail="subscription"))
        return (
            False,
            AuthAvailability(detail=f"{model} is not compatible with the {harness} lane"),
        )

    def default_lane(self, model: str) -> str:
        return _default_lane_for(model)


@dataclass(frozen=True)
class StandinModelRegistry:
    """A ``ModelRegistry`` (frozen Protocol) backed by the canonical fixtures.

    ``runnable_by`` lists every canonical lane a model is valid on — used by
    ``validation`` to render an actionable "did you mean" diagnostic on an invalid
    seat.
    """

    def list_models(self) -> tuple[ModelSpec, ...]:
        return tuple(self.get(model) for model in _canonical_models())

    def get(self, model: str) -> ModelSpec:
        lanes = runnable_lanes(model)
        return ModelSpec(
            model=model,
            vendor_family=vendor_family(model),
            default_lane=_default_lane_for(model),
            runnable_by=lanes,
        )

    def default_lane(self, model: str) -> str:
        return _default_lane_for(model)


@dataclass(frozen=True)
class StandinHarnessRegistry:
    """A minimal ``HarnessRegistry`` over the canonical lanes (probe-agnostic)."""

    def list_harnesses(self) -> tuple[HarnessSpec, ...]:
        return tuple(self.get(name) for name in _canonical_harnesses())

    def get(self, name: str) -> HarnessSpec:
        return HarnessSpec(
            name=name,
            cli=name,
            auth_lanes=(AUTH_SUBSCRIPTION,),
            backing=BACKING_HOMEBREW,
        )

    def is_available(self, name: str) -> bool:
        return name in _canonical_harnesses()


def runnable_lanes(model: str) -> tuple[str, ...]:
    """The canonical harness lanes a ``model`` is valid on (fixture-derived)."""
    lanes: list[str] = []
    for pair_model, pair_harness in CANONICAL_VALID_PAIRS:
        if pair_model == model and pair_harness not in lanes:
            lanes.append(pair_harness)
    return tuple(lanes)


def _canonical_models() -> tuple[str, ...]:
    seen: list[str] = []
    for model, _ in CANONICAL_VALID_PAIRS:
        if model not in seen:
            seen.append(model)
    return tuple(seen)


def _canonical_harnesses() -> tuple[str, ...]:
    seen: list[str] = []
    for _, harness in CANONICAL_VALID_PAIRS:
        if harness not in seen:
            seen.append(harness)
    return tuple(seen)


__all__ = [
    "StandinCompatibilityMatrix",
    "StandinModelRegistry",
    "StandinHarnessRegistry",
    "runnable_lanes",
]
