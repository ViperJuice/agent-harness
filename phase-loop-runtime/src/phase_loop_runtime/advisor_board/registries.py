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
