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
``gpt-5.6-sol`` valid on both ``codex`` and ``opencode`` (same family) but invalid on
``claude`` — exactly ``fixtures.CANONICAL_VALID_PAIRS`` /
``CANONICAL_INVALID_PAIRS``.

The CONFIG-TIME gate (``validate_seat`` / ``validate_board``) lives in
``validation.py`` — the single canonical seat-validation API. It resolves a
seat's lane (``harness or default_lane(model)``), rejects an invalid pairing or an
over-ceiling effort with an actionable message, and is what the config loader
(``config.py``) runs on every board — presets included.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from .backing import VENDOR_API_KEY_VARS
from .registries import (
    DEFAULT_HARNESS_REGISTRY,
    DEFAULT_MODEL_REGISTRY,
    AuthAvailability,
    HarnessRegistry,
    MatrixVerdict,
    ModelRegistry,
    UnknownHarnessError,
    UnknownModelError,
)
from .schema import (
    AUTH_API_KEY,
    AUTH_SUBSCRIPTION,
    vendor_family,
    vendor_of_harness,
)


@dataclass
class DefaultCompatibilityMatrix:
    """Populated ``CompatibilityMatrix`` over the six-harness / model registries.

    ``env`` (default ``os.environ``) and ``probe`` (default the harness
    registry's PATH probe) are injectable so auth-availability is deterministic in
    tests: the api-key lane is reported available ONLY when the vendor's key var
    is actually present in ``env`` (so no-silent-key is testable, not asserted),
    and the subscription lane ONLY when the harness CLI is on PATH.
    """

    harnesses: HarnessRegistry = field(default_factory=lambda: DEFAULT_HARNESS_REGISTRY)
    models: ModelRegistry = field(default_factory=lambda: DEFAULT_MODEL_REGISTRY)
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


# Config-time seat/board validation lives in ``validation.py`` (the single
# canonical API — ``validate_seat`` / ``validate_board`` / ``SeatValidationError``
# / ``SeatVerdict``). This module owns only the matrix itself: ``is_valid`` and
# ``default_lane`` (the frozen ``CompatibilityMatrix`` interface) plus the
# populated ``DefaultCompatibilityMatrix`` / ``default_matrix`` factory. The
# effort-ceiling check that used to live here is folded into that canonical
# ``validate_seat`` (which reads the ceiling off this matrix's ``.models``).


__all__ = [
    "DefaultCompatibilityMatrix",
    "default_matrix",
]
