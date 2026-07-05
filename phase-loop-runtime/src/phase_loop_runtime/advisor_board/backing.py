"""Provider-backing selector + auth-enforcement contract (IF-0-ABDFREEZE-3).

Two orthogonal per-seat axes, frozen here:

* **backing** (``homebrew | omnigent``) — the transport. Selected per seat;
  ``homebrew`` is the default and keeps the built-3 + native host leg.
* **auth** (``subscription | api_key``) — the credential lane. Subscription is
  the default; api-key is reachable only behind ``Board.allow_api_key_fallback``.

**No-silent-key is enforced by ACTIVE environment scrubbing**, freezing the
existing ``_subscription_env`` pattern (panel_invoker.py:226-230,348-353) into a
per-seat, vendor-keyed contract:

* a **subscription** seat scrubs EVERY vendor API-key var from the subprocess env
  / gateway payload (identical to ``_subscription_env`` today); and
* an **api-key fallback** seat scrubs everything, then injects ONLY the seat
  vendor's key var(s) — never another vendor's, never silently.

``VENDOR_API_KEY_VARS`` is the flat ``panel_invoker._API_KEY_VARS`` tuple
re-expressed keyed by vendor family, so "inject only the seat vendor's key" is
expressible. The union of its values equals the current flat tuple (asserted in
``tests/test_advisor_board_backcompat.py``), so scrubbing stays byte-equivalent.
The reference env functions below are pure and importable; ABDHOME wires them
into the real launch/gateway env (this module changes no running path).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .schema import (
    AUTH_API_KEY,
    AUTH_SUBSCRIPTION,
    BACKING_HOMEBREW,
    BACKING_OMNIGENT,
    PROVIDER_BACKINGS,
    Seat,
    seat_vendor_family,
)


# Vendor family -> the provider API-key env var(s) that vendor authenticates with.
# The UNION of all values MUST equal ``panel_invoker._API_KEY_VARS`` so subscription
# scrubbing is byte-equivalent to ``_subscription_env`` today.
VENDOR_API_KEY_VARS: dict[str, tuple[str, ...]] = {
    "codex": ("OPENAI_API_KEY",),
    "claude": ("ANTHROPIC_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY"),
}


def all_vendor_key_vars() -> tuple[str, ...]:
    """Every vendor API-key var (scrub set for a subscription seat)."""
    seen: list[str] = []
    for vars_ in VENDOR_API_KEY_VARS.values():
        for var in vars_:
            if var not in seen:
                seen.append(var)
    return tuple(seen)


# --- backing selector -------------------------------------------------------


@dataclass(frozen=True)
class BackingDecision:
    """Which transport a seat resolves to, and why. Fail-closed: an ``omnigent``
    seat whose gateway is unavailable degrades to ``skip`` (never a silent
    homebrew fallback for a breadth harness — hand-writing breadth defeats the
    Omnigent maintenance-offload, per ABDHOME non-goals)."""

    backing: str
    skip: bool = False
    reason: str = ""


def select_backing(seat: Seat, *, gateway_available: bool = True) -> BackingDecision:
    """Freeze the selector contract: honor ``seat.backing``; an ``omnigent`` seat
    with no gateway degrades skip-with-warning. ABDHOME/ABDOMNI supply the real
    availability probes; this fixes the DECISION SHAPE they return."""
    if seat.backing not in PROVIDER_BACKINGS:
        raise ValueError(f"seat.backing {seat.backing!r} not in {PROVIDER_BACKINGS}")
    if seat.backing == BACKING_OMNIGENT and not gateway_available:
        return BackingDecision(BACKING_OMNIGENT, skip=True, reason="omnigent gateway unavailable")
    return BackingDecision(seat.backing)


# --- auth enforcement (active env scrubbing) --------------------------------


def resolve_seat_env(
    seat: Seat,
    base_env: Mapping[str, str],
    *,
    allow_api_key_fallback: bool = False,
) -> dict[str, str]:
    """Reference implementation of the frozen no-silent-key env contract.

    * Always scrub EVERY vendor API-key var from a copy of ``base_env`` (the
      ``_subscription_env`` behavior — a subscription seat ends here).
    * For an ``api_key`` seat, and ONLY when the board opts in
      (``allow_api_key_fallback``), re-inject ONLY the seat vendor's key var(s)
      from ``base_env``. Any other lane / a disallowed board leaves the env
      scrubbed (fail-closed).

    Raises when a seat requests the api-key lane without the board opt-in, so a
    silent key can never slip through. Pure and side-effect-free; ABDHOME wires
    it into the subprocess/gateway env.
    """
    env = {k: v for k, v in base_env.items() if k not in all_vendor_key_vars()}
    if seat.auth == AUTH_SUBSCRIPTION:
        return env
    if seat.auth == AUTH_API_KEY:
        if not allow_api_key_fallback:
            raise ValueError(
                f"seat {seat.seat_key} requests the api_key lane but the board did not "
                "opt in (allow_api_key_fallback=False) — never-silent-key"
            )
        vendor = seat_vendor_family(seat)
        for var in VENDOR_API_KEY_VARS.get(vendor, ()):  # ONLY this vendor's key(s)
            if var in base_env:
                env[var] = base_env[var]
        return env
    raise ValueError(f"unknown seat.auth {seat.auth!r}")


__all__ = [
    "VENDOR_API_KEY_VARS",
    "all_vendor_key_vars",
    "BackingDecision",
    "select_backing",
    "resolve_seat_env",
    "AUTH_SUBSCRIPTION",
    "AUTH_API_KEY",
    "BACKING_HOMEBREW",
    "BACKING_OMNIGENT",
]
