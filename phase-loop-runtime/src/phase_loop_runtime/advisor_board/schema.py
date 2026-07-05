"""Advisor Board — frozen seat/board schema + vendor projection (IF-0-ABDFREEZE-1, -4).

This module is the **interface freeze** for the model-first Advisor Board. It is
additive and behavior-neutral: it introduces no runtime path and does not touch
`panel_invoker`. Downstream phases (ABDREG / ABDRESOLVE / ABDHOME) code against
the typed structures frozen here so the fan-out integrates without a big-bang.

The seat model inverts the v4 panel: a **seat is a cognition** — `{model, effort,
harness?, lens?, auth?}` — where the harness is a defaulted-but-overridable
execution *lane*, not the primary key. A **board** is a named, purpose-tagged,
open-ended list of seats.

Two freezes here are load-bearing for back-compat and must reproduce today's
behavior exactly (proven by ``tests/test_advisor_board_schema.py`` and
``tests/test_advisor_board_backcompat.py``):

* **Seat -> vendor-family projection** (``vendor_family`` / ``seat_vendor_family``)
  must be byte-consistent with the existing governed-gate logic
  (`governed_review.author_vendor_for_model` :60-75 and
  `governed_review._EXECUTOR_VENDOR` :47-53). The governed reviewer != author
  disjointness keys on vendor identity; model-first breaks the ``leg == vendor``
  assumption, so ABDHOME rewires `governed_review` / `governed_premerge` to
  consume *this* canonical projection. If the projection drifts, custom boards
  silently corrupt reviewer-disjointness.

* **Host-leg identity** — which seat is the native in-process host leg when the
  board runs *inside* a harness (e.g. Claude Code -> native ``Agent``) versus the
  homebrew subprocess legs. Today the standalone Python runner spawns every leg
  as a subprocess (no host leg), so the additive ``Seat.host_leg`` marker
  defaults ``False`` and ``identify_host_leg`` returns ``None`` for the default
  board — today's behavior, untouched.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


# --- canonical vocabularies -------------------------------------------------

# Model-first, harness-agnostic effort ladder. Each harness translates a canonical
# level to its own invocation form (see ``harness_mapping``). Ordered low -> high.
EFFORT_LEVELS: tuple[str, ...] = ("low", "medium", "high", "max")

# Credential lane for a seat. Subscription is the default lane (Assumptions §);
# ``api_key`` is only reachable behind an explicit board-level opt-in
# (``Board.allow_api_key_fallback``) and injects ONLY the seat vendor's key.
AUTH_SUBSCRIPTION = "subscription"
AUTH_API_KEY = "api_key"
AUTH_LANES: tuple[str, ...] = (AUTH_SUBSCRIPTION, AUTH_API_KEY)

# Provider transport backing a seat. ``homebrew`` keeps the built-3 + the native
# host leg (ABDHOME); ``omnigent`` supplies breadth via omniagent-plus -> Omnigent
# (ABDOMNI). Default homebrew keeps the default board all-homebrew.
BACKING_HOMEBREW = "homebrew"
BACKING_OMNIGENT = "omnigent"
PROVIDER_BACKINGS: tuple[str, ...] = (BACKING_HOMEBREW, BACKING_OMNIGENT)

# Board config format + location (IF-0-ABDFREEZE-1). Honors ``XDG_CONFIG_HOME``.
CONFIG_RELATIVE_PATH = "agent-harness/advisor-boards.toml"


def board_config_path(env: "os._Environ[str] | dict[str, str] | None" = None) -> Path:
    """Frozen config location: ``$XDG_CONFIG_HOME/agent-harness/advisor-boards.toml``
    (default ``~/.config/agent-harness/advisor-boards.toml``). The loader lands in
    ABDREG; this only freezes *where* it reads."""
    src = os.environ if env is None else env
    base = src.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / CONFIG_RELATIVE_PATH


# --- vendor-family projection (governed reviewer != author, model-first) -----

# Harness -> vendor family. Mirrors `governed_review._EXECUTOR_VENDOR` (:47-53)
# EXACTLY so the projection reproduces today's executor->vendor mapping. Two
# same-vendor seats on different harnesses (gpt-5.5 on ``codex`` and on
# ``opencode``) MUST project to the same family, which this table encodes.
_HARNESS_VENDOR: dict[str, str] = {
    "codex": "codex",
    "opencode": "codex",  # openai-family models
    "claude": "claude",
    "gemini": "gemini",
    "pi": "pi",
}


def vendor_of_harness(harness: str) -> str:
    """Harness (execution lane) -> vendor family, with an identity fallback for an
    unknown harness. Byte-consistent with
    `governed_review.author_vendor_for_executor`."""
    h = (harness or "").lower()
    return _HARNESS_VENDOR.get(h, h)


def vendor_of_model(model: str) -> str:
    """Concrete model id -> vendor family, or ``""`` when the model is inconclusive.

    The matched branches reproduce `governed_review.author_vendor_for_model`
    (:69-74) EXACTLY. The one intentional difference: this returns ``""`` (rather
    than the raw model) for an unmatched model, so ``vendor_family`` can then fall
    back to the harness lane. ``author_vendor_for_model``-equivalence for a bare
    model is recovered as ``vendor_of_model(m) or m.lower()`` and is asserted in
    the schema test.
    """
    m = (model or "").lower()
    if not m:
        return ""
    if "claude" in m or "opus" in m or "sonnet" in m or "haiku" in m:
        return "claude"
    if "gemini" in m or m in {"pro", "flash", "flash-lite", "auto"}:
        return "gemini"
    if m.startswith("gpt") or m.startswith("o1") or m.startswith("o3") or m.startswith("openai/"):
        return "codex"  # the codex panel leg runs the openai-family model
    return ""


def vendor_family(model: str, harness: str | None = None) -> str:
    """Canonical model-first seat -> vendor-family projection.

    Model wins when conclusive; otherwise the harness lane decides; otherwise the
    lowercased model is the family (matching the historical bare-model fallback).
    This is THE canonical projection ABDHOME rewires the governed gates onto — not
    a parallel copy — so reviewer != author disjointness survives model-first.
    """
    fam = vendor_of_model(model)
    if fam:
        return fam
    if harness:
        return vendor_of_harness(harness)
    return (model or "").lower()


# --- seat + board -----------------------------------------------------------


@dataclass(frozen=True)
class Seat:
    """A single cognition on a board (model-first).

    ``model``     concrete model id (the primary key). Required.
    ``effort``    canonical effort level (``EFFORT_LEVELS``). Required — the
                  model-registry default is applied by the resolver (ABDRESOLVE),
                  not silently here.
    ``harness``   execution lane; ``None`` -> ``default_lane(model)`` at resolution.
    ``lens``      optional review persona/lens (advisory framing); no behavior yet.
    ``auth``      credential lane (``AUTH_LANES``); subscription by default.
    ``backing``   provider transport (``PROVIDER_BACKINGS``); homebrew by default.
    ``host_leg``  additive marker: this seat is the native in-process host leg
                  (not a subprocess). Default ``False`` == today's subprocess leg.
    """

    model: str
    effort: str
    harness: str | None = None
    lens: str | None = None
    auth: str = AUTH_SUBSCRIPTION
    backing: str = BACKING_HOMEBREW
    host_leg: bool = False

    def __post_init__(self) -> None:
        if not self.model or not str(self.model).strip():
            raise ValueError("seat.model is required")
        if self.effort not in EFFORT_LEVELS:
            raise ValueError(f"seat.effort {self.effort!r} not in {EFFORT_LEVELS}")
        if self.auth not in AUTH_LANES:
            raise ValueError(f"seat.auth {self.auth!r} not in {AUTH_LANES}")
        if self.backing not in PROVIDER_BACKINGS:
            raise ValueError(f"seat.backing {self.backing!r} not in {PROVIDER_BACKINGS}")

    @property
    def vendor_family(self) -> str:
        """This seat's vendor family (model-first projection)."""
        return vendor_family(self.model, self.harness)

    @property
    def seat_key(self) -> str:
        """Stable per-seat LABEL for result re-keying (ABDRESOLVE re-keys
        ``PanelLegResult.leg`` -> seat so two same-vendor seats are expressible).

        Incorporates every user-distinguishing field — (harness-lane, model,
        effort, lens) — so seats that differ only by ``lens`` (a natural
        brainstorm board: same model, adversarial vs supportive) get distinct
        keys. This is a LABEL, not a guaranteed-unique id: a board may still hold
        two byte-identical seats, so ABDRESOLVE MUST key results by **seat
        position** and use ``seat_key`` only as the human-readable label. Frozen
        this way so the fan-out builds on a stable, collision-aware contract.
        """
        lane = self.harness or f"@{vendor_family(self.model)}"
        base = f"{lane}:{self.model}:{self.effort}"
        return f"{base}:{self.lens}" if self.lens else base


@dataclass(frozen=True)
class Board:
    """A named, purpose-tagged, open-ended list of seats.

    ``allow_api_key_fallback`` defaults ``False`` — no seat may use the api-key
    lane unless the board explicitly opts in (never-silent-key, IF-0-ABDFREEZE-3).
    """

    name: str
    purpose: str
    seats: tuple[Seat, ...]
    allow_api_key_fallback: bool = False

    def __post_init__(self) -> None:
        if not self.name or not str(self.name).strip():
            raise ValueError("board.name is required")
        if not isinstance(self.seats, tuple):
            # keep the board hashable/frozen: seats must be a tuple of Seat
            raise TypeError("board.seats must be a tuple of Seat")
        for seat in self.seats:
            if not isinstance(seat, Seat):
                raise TypeError(f"board.seats entries must be Seat, got {type(seat)!r}")
        if not self.allow_api_key_fallback:
            offenders = [s for s in self.seats if s.auth == AUTH_API_KEY]
            if offenders:
                raise ValueError(
                    "board has api_key seats but allow_api_key_fallback is False "
                    "(never-silent-key): "
                    + ", ".join(s.seat_key for s in offenders)
                )

    def seat_vendor_families(self) -> tuple[str, ...]:
        return tuple(seat.vendor_family for seat in self.seats)


def seat_vendor_family(seat: Seat) -> str:
    """Free-function form of ``Seat.vendor_family`` (the projection ABDHOME calls
    when it holds a seat but not the property)."""
    return vendor_family(seat.model, seat.harness)


# --- host-leg identity ------------------------------------------------------


@dataclass(frozen=True)
class HostContext:
    """The runtime host the board is currently running *inside*.

    ``host_harness`` is the harness whose process is hosting the board (e.g.
    ``"claude"`` when the board runs inside Claude Code and can reach the native
    in-process ``Agent`` tool). ``None`` == the standalone Python runner, which
    spawns every leg as a subprocess — TODAY'S DEFAULT, no host leg.
    """

    host_harness: str | None = None


def identify_host_leg(board: Board, host: HostContext | None = None) -> Seat | None:
    """Return the seat that IS the native host leg for ``host``, or ``None``.

    A seat is the host leg iff (a) the board runs inside a harness
    (``host.host_harness`` set), and (b) the seat's resolved lane matches that
    harness, and (c) the seat is marked ``host_leg`` OR it is the first seat on
    the hosting harness (auto-identification for a board that predates the
    marker). For the standalone runner (``host_harness is None``) this returns
    ``None`` — every leg is a subprocess, exactly as today. The native host leg,
    once identified, must never be routed through the gateway (ABDHOME invariant).
    """
    if host is None or not host.host_harness:
        return None
    hosting = host.host_harness.lower()
    on_host = [s for s in board.seats if (s.harness or "").lower() == hosting]
    if not on_host:
        return None
    explicit = [s for s in on_host if s.host_leg]
    return explicit[0] if explicit else on_host[0]
