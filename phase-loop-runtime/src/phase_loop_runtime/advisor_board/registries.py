"""Registry interfaces + (model x harness) matrix API — INTERFACES ONLY
(IF-0-ABDFREEZE-2).

This freezes the *return types* and the *method surface* that ABDREG populates
and that ABDRESOLVE / ABDHOME import. It ships importable Protocols, the frozen
record + result types, and ``NotImplemented``-raising stubs — **no six-harness
data** (that is ABDREG). Keeping the return types concrete here (rather than bare
tuples) is what lets ABDREG's matrix and ABDRESOLVE's validation compile against
the same shapes without divergence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .schema import AUTH_SUBSCRIPTION, BACKING_HOMEBREW


# --- frozen record + result types (imported by ABDREG and ABDRESOLVE) -------


@dataclass(frozen=True)
class HarnessSpec:
    """A registered harness (execution lane). Populated by ABDREG.

    ``cli`` is the availability-probe binary (e.g. ``codex`` / ``agy`` / ``claude``
    / ``cursor-agent``); ``auth_lanes`` the credential lanes it supports; ``backing``
    its default provider transport. ``available`` is a *probe result*, not frozen
    data — the stub registry leaves it ``None`` (unknown)."""

    name: str
    cli: str
    auth_lanes: tuple[str, ...] = (AUTH_SUBSCRIPTION,)
    backing: str = BACKING_HOMEBREW
    available: bool | None = None


@dataclass(frozen=True)
class ModelSpec:
    """A registered model. Populated by ABDREG.

    ``default_lane`` the harness a bare seat resolves to; ``runnable_by`` the
    harness lanes that can run it; ``effort_ceiling`` the max canonical effort the
    model honors (a seat above it is clamped/rejected at validation)."""

    model: str
    vendor_family: str
    default_lane: str
    runnable_by: tuple[str, ...] = ()
    effort_ceiling: str = "max"


@dataclass(frozen=True)
class AuthAvailability:
    """Which credential lanes are usable for a ``(model, harness)`` pair.

    Returned as the second element of ``CompatibilityMatrix.is_valid``. Concrete
    (not a bare bool) so no-silent-key is *testable* for a seat: a seat may be a
    valid pairing yet have neither lane available (skip-with-warning), or only the
    subscription lane (the default), or the api-key lane behind an explicit
    board opt-in."""

    subscription: bool = False
    api_key: bool = False
    detail: str = ""

    @property
    def any_available(self) -> bool:
        return self.subscription or self.api_key


# ``is_valid`` return alias — frozen so both the matrix impl and validation import it.
MatrixVerdict = tuple[bool, AuthAvailability]


# --- interfaces (Protocols) -------------------------------------------------


@runtime_checkable
class HarnessRegistry(Protocol):
    """The six-harness registry (claude, codex, gemini, opencode, pi, cursor)."""

    def list_harnesses(self) -> tuple[HarnessSpec, ...]: ...

    def get(self, name: str) -> HarnessSpec: ...

    def is_available(self, name: str) -> bool: ...


@runtime_checkable
class ModelRegistry(Protocol):
    """Model -> default lane / runnable_by / effort ceiling."""

    def list_models(self) -> tuple[ModelSpec, ...]: ...

    def get(self, model: str) -> ModelSpec: ...

    def default_lane(self, model: str) -> str: ...


@runtime_checkable
class CompatibilityMatrix(Protocol):
    """The ``(model x harness)`` compatibility + per-lane auth-availability API.

    ``is_valid(model, harness)`` -> ``(ok, AuthAvailability)``: ``ok`` is whether
    the pairing is *expressible at all* (an invalid pairing such as
    ``claude:gpt-5.5`` is rejected at config time with a clear message); the
    ``AuthAvailability`` reports which lanes are actually usable so a valid-but-
    unauthed seat degrades rather than blocks. ``default_lane`` is the harness a
    bare ``model`` seat resolves onto.
    """

    def is_valid(self, model: str, harness: str) -> MatrixVerdict: ...

    def default_lane(self, model: str) -> str: ...


# --- stubs (ABDREG replaces these with populated data) ----------------------

_STUB_MSG = "advisor-board registry is populated in ABDREG (Phase 2); this is the frozen stub"


@dataclass
class StubHarnessRegistry:
    """Importable placeholder satisfying ``HarnessRegistry``. Every accessor raises
    so a phase that accidentally depends on real data before ABDREG fails loudly."""

    def list_harnesses(self) -> tuple[HarnessSpec, ...]:
        raise NotImplementedError(_STUB_MSG)

    def get(self, name: str) -> HarnessSpec:
        raise NotImplementedError(_STUB_MSG)

    def is_available(self, name: str) -> bool:
        raise NotImplementedError(_STUB_MSG)


@dataclass
class StubModelRegistry:
    def list_models(self) -> tuple[ModelSpec, ...]:
        raise NotImplementedError(_STUB_MSG)

    def get(self, model: str) -> ModelSpec:
        raise NotImplementedError(_STUB_MSG)

    def default_lane(self, model: str) -> str:
        raise NotImplementedError(_STUB_MSG)


@dataclass
class StubCompatibilityMatrix:
    def is_valid(self, model: str, harness: str) -> MatrixVerdict:
        raise NotImplementedError(_STUB_MSG)

    def default_lane(self, model: str) -> str:
        raise NotImplementedError(_STUB_MSG)


# ===========================================================================
# ABDREG (Phase 2) — populated registries.
#
# Everything above is the ABDFREEZE interface freeze and is byte-frozen (the
# stubs still raise; ``tests/test_advisor_board_registries.py`` asserts it).
# Below is the real six-harness / model data ABDREG owns. It is additive: no
# frozen type, protocol, or stub changes. The compatibility + auth matrix that
# consumes these lives in ``matrix.py``; the presets + config loader in
# ``presets.py`` / ``config.py``.
# ===========================================================================

import shutil
from collections.abc import Callable

from .schema import AUTH_API_KEY, BACKING_OMNIGENT, vendor_family, vendor_of_harness


class UnknownModelError(KeyError):
    """A seat references a model the registry does not know. Raised with the
    known-model list so a config-time rejection is actionable."""


class UnknownHarnessError(KeyError):
    """A seat references a harness the registry does not know."""


# --- harness registry data --------------------------------------------------

# The six harnesses. claude / codex / gemini are the built-3 (homebrew, native +
# TUI legs, ABDHOME); opencode / pi / cursor are breadth — registered here
# regardless, but their default transport is ``omnigent`` (routed via
# omniagent-plus -> Omnigent per ABDOMNI; NOT hand-written homebrew adapters).
# ``cli`` is the availability-probe binary: codex -> ``codex``, gemini -> ``agy``
# (panel_invoker._LEG_CLI), cursor -> ``cursor-agent`` (so cursor is gated on the
# cursor-agent binary being present). All six support both credential lanes; the
# subscription lane is the default and the api-key lane is reachable only behind a
# board opt-in (never-silent-key).
_BOTH_LANES: tuple[str, ...] = (AUTH_SUBSCRIPTION, AUTH_API_KEY)

_HARNESS_SPECS: tuple[HarnessSpec, ...] = (
    HarnessSpec(name="claude", cli="claude", auth_lanes=_BOTH_LANES, backing=BACKING_HOMEBREW),
    HarnessSpec(name="codex", cli="codex", auth_lanes=_BOTH_LANES, backing=BACKING_HOMEBREW),
    HarnessSpec(name="gemini", cli="agy", auth_lanes=_BOTH_LANES, backing=BACKING_HOMEBREW),
    HarnessSpec(name="opencode", cli="opencode", auth_lanes=_BOTH_LANES, backing=BACKING_OMNIGENT),
    HarnessSpec(name="pi", cli="pi", auth_lanes=_BOTH_LANES, backing=BACKING_OMNIGENT),
    HarnessSpec(name="cursor", cli="cursor-agent", auth_lanes=_BOTH_LANES, backing=BACKING_OMNIGENT),
)


@dataclass
class DefaultHarnessRegistry:
    """Populated ``HarnessRegistry`` over the six harnesses.

    ``is_available`` is a PATH probe only (``shutil.which``) — metadata-only, it
    never authenticates or spends tokens, mirroring
    ``panel_invoker.available_panel_legs``. The probe is injectable so tests are
    deterministic (cursor's gate is just ``which("cursor-agent")``).
    """

    probe: Callable[[str], bool] | None = None

    def _probe(self) -> Callable[[str], bool]:
        return self.probe if self.probe is not None else (lambda cli: shutil.which(cli) is not None)

    def list_harnesses(self) -> tuple[HarnessSpec, ...]:
        return _HARNESS_SPECS

    def get(self, name: str) -> HarnessSpec:
        key = (name or "").lower()
        for spec in _HARNESS_SPECS:
            if spec.name == key:
                return spec
        known = ", ".join(s.name for s in _HARNESS_SPECS)
        raise UnknownHarnessError(f"unknown harness {name!r}; known harnesses: {known}")

    def is_available(self, name: str) -> bool:
        spec = self.get(name)
        return self._probe()(spec.cli)


# --- model registry data ----------------------------------------------------

# (model, default_lane, effort_ceiling). ``vendor_family`` is derived from the
# FROZEN ``schema.vendor_family`` projection (not retyped, so it can never drift
# from the governed-gate mapping). ``runnable_by`` is derived from that family
# across the six harnesses (a harness runs a model iff its lane vendor-family
# matches). ``default_lane`` is PINNED to a built-3 leg so a bare seat resolves
# onto the native lane — e.g. ``gpt-5.5`` is runnable by both ``codex`` and
# ``opencode`` but a bare ``gpt-5.5`` seat MUST resolve to ``codex`` or the
# default board's back-compat breaks. ``effort_ceiling`` defaults to the ladder
# max; lower it only where there is concrete evidence a model caps out below max
# (none of the built-3 do — codex's ``xhigh`` IS canonical ``max``).
_MODEL_DEFS: tuple[tuple[str, str, str], ...] = (
    ("gpt-5.5", "codex", "max"),
    ("claude-sonnet-5", "claude", "max"),
    ("claude-opus-4-8", "claude", "max"),
    ("claude-haiku-4-5", "claude", "max"),
    ("claude-fable-5", "claude", "max"),
    ("Gemini 3.1 Pro", "gemini", "max"),
)


def _runnable_by(model_vendor: str) -> tuple[str, ...]:
    """Harness lanes that can run a model of ``model_vendor``: those whose lane
    vendor-family matches. Derived from the six registered harnesses so a new
    harness automatically joins its family's runnable set."""
    return tuple(s.name for s in _HARNESS_SPECS if vendor_of_harness(s.name) == model_vendor)


def _build_model_specs() -> tuple[ModelSpec, ...]:
    specs: list[ModelSpec] = []
    for model, default_lane, ceiling in _MODEL_DEFS:
        family = vendor_family(model, default_lane)
        specs.append(
            ModelSpec(
                model=model,
                vendor_family=family,
                default_lane=default_lane,
                runnable_by=_runnable_by(family),
                effort_ceiling=ceiling,
            )
        )
    return tuple(specs)


_MODEL_SPECS: tuple[ModelSpec, ...] = _build_model_specs()


@dataclass
class DefaultModelRegistry:
    """Populated ``ModelRegistry``. ``get`` / ``default_lane`` raise
    ``UnknownModelError`` (with the known-model list) for an unregistered model —
    this IS the unknown-model config-time rejection."""

    def list_models(self) -> tuple[ModelSpec, ...]:
        return _MODEL_SPECS

    def get(self, model: str) -> ModelSpec:
        for spec in _MODEL_SPECS:
            if spec.model == model:
                return spec
        known = ", ".join(s.model for s in _MODEL_SPECS)
        raise UnknownModelError(f"unknown model {model!r}; known models: {known}")

    def default_lane(self, model: str) -> str:
        return self.get(model).default_lane


# Module-level singletons the matrix / config loader default to.
DEFAULT_HARNESS_REGISTRY = DefaultHarnessRegistry()
DEFAULT_MODEL_REGISTRY = DefaultModelRegistry()
