"""Per-harness model/effort -> invocation mapping (IF-0-ABDFREEZE-1).

The v5 panel-verification found that **per-leg effort is NOT supported today** —
it is hard-coded per leg (claude ``--effort max`` panel_invoker.py:324, codex
``model_reasoning_effort=xhigh`` :992) and for the agy/gemini leg **effort is
baked into the model-name string** (``"Gemini 3.1 Pro (High)"`` :51/:1016). The
model-first ``{model, effort}`` split therefore needs a per-harness mapping that
turns a canonical ``(model, effort)`` back into each harness's actual invocation.

This module freezes that contract. ABDHOME does the plumbing (reaching the real
CLI arg lists in `panel_invoker`); it codes against ``render_seat_invocation``.
The freeze is load-bearing for back-compat: the ``default`` board's three seats
MUST render to today's exact literals —

    claude  -> effort flag        ``--effort max``
    codex   -> config override    ``-c model_reasoning_effort=xhigh``
    gemini  -> model-name embed   ``Gemini 3.1 Pro (High)``

— proven by ``tests/test_advisor_board_backcompat.py`` against the live
`panel_invoker` constants (`DEFAULT_LEG_MODELS`, the codex/claude/agy arg forms).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .schema import EFFORT_LEVELS


class EffortMappingError(NotImplementedError):
    """Raised when a harness's effort mapping is not frozen here (breadth lanes
    land in ABDREG/ABDHOME/ABDOMNI)."""


# Effort mechanism: HOW the effort reaches the CLI for a harness.
MECH_FLAG = "flag"          # a dedicated flag, e.g. claude ``--effort <level>``
MECH_CONFIG = "config"      # a ``-c key=value`` override, e.g. codex reasoning
MECH_MODEL_NAME = "model_name"  # baked into the model string, e.g. agy/gemini


@dataclass(frozen=True)
class SeatInvocation:
    """The frozen, harness-specific shape ``(model, effort)`` renders to.

    ``model``        the model string to pass to the CLI (effort-embedded for the
                     ``model_name`` mechanism; otherwise the model verbatim).
    ``effort_args``  extra CLI args carrying effort (empty for ``model_name``).
    ``mechanism``    one of ``MECH_FLAG`` / ``MECH_CONFIG`` / ``MECH_MODEL_NAME``.
    ``harness``      the execution lane this rendering targets.
    """

    harness: str
    model: str
    effort_args: tuple[str, ...]
    mechanism: str


# canonical effort -> codex ``model_reasoning_effort`` token. codex's max reasoning
# is ``xhigh`` (panel_invoker.py:992), so canonical ``max`` -> ``xhigh``.
_CODEX_EFFORT: dict[str, str] = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "max": "xhigh",
}

# canonical effort -> grok ``--reasoning-effort`` token. The grok CLI accepts ONLY
# ``high | medium | low`` (verified via an out-of-range probe: ``--reasoning-effort max``
# -> ``unknown effort level 'max'; use one of: high, medium, low``). So canonical ``max``
# CLAMPS to grok's own ``high`` ceiling — the panel's grok seat runs at grok-4.5's maximum
# reasoning. (ah#222: a prior literal ``max`` made the grok leg ERROR on every default panel
# run.) The grokexec/launcher grok effort path is separate (capability_registry) — not fixed here.
_GROK_EFFORT: dict[str, str] = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "max": "high",  # grok has no 'max'/'xhigh'; its ceiling is 'high'
}

# canonical effort -> the ``(Word)`` token agy/gemini bakes into the model name
# (panel_invoker.py:1016 uses ``(High)``).
_GEMINI_EFFORT_WORD: dict[str, str] = {
    "low": "Low",
    "medium": "Medium",
    "high": "High",
    "max": "Max",
}

# strip a trailing ``" (Effort)"`` embed so re-rendering is idempotent when a caller
# passes an already-baked model string (e.g. ``"Gemini 3.1 Pro (High)"``). Matches
# ONLY the four canonical effort words (the Title-case tokens ``render_gemini_model``
# emits) — a model whose name genuinely ends in a parenthetical (e.g.
# ``"Gemini 3.1 Pro (Preview)"``) is a DIFFERENT model and must be left untouched,
# never silently rewritten to a lower effort.
_GEMINI_EMBED_RE = re.compile(r"\s*\((?:Low|Medium|High|Max)\)\s*$")


def gemini_base_model(model: str) -> str:
    """Return the gemini model with any trailing ``(Effort)`` embed removed."""
    return _GEMINI_EMBED_RE.sub("", model or "").strip()


def render_gemini_model(model: str, effort: str) -> str:
    """``("Gemini 3.1 Pro", "high") -> "Gemini 3.1 Pro (High)"`` — the agy leg's
    effort-in-the-model-name special case. Idempotent on an already-baked model."""
    _require_effort(effort)
    return f"{gemini_base_model(model)} ({_GEMINI_EFFORT_WORD[effort]})"


def _require_effort(effort: str) -> None:
    if effort not in EFFORT_LEVELS:
        raise ValueError(f"effort {effort!r} not in {EFFORT_LEVELS}")


def render_seat_invocation(harness: str, model: str, effort: str) -> SeatInvocation:
    """Freeze: turn a canonical ``(harness, model, effort)`` into its CLI invocation.

    Only the homebrew lanes (claude / codex / gemini / grok) are frozen here —
    claude / codex / gemini are what the ``default`` board's back-compat proof
    rides on; grok joins them for the 4-vendor ``code-review`` board. Breadth
    lanes (opencode / pi / cursor / amp) raise ``EffortMappingError`` until
    ABDREG/ABDHOME/ABDOMNI populate them; a board with an unmapped lane degrades
    skip-with-warning, never silently drops effort.
    """
    _require_effort(effort)
    lane = (harness or "").lower()
    if lane == "claude":
        # panel_invoker.py:322-325 -> ``--model <model> --effort <level>``
        return SeatInvocation(lane, model, ("--effort", effort), MECH_FLAG)
    if lane == "codex":
        # panel_invoker.py:991-992 -> ``--model <model> -c model_reasoning_effort=<tok>``
        token = _CODEX_EFFORT[effort]
        return SeatInvocation(lane, model, ("-c", f"model_reasoning_effort={token}"), MECH_CONFIG)
    if lane == "gemini":
        # panel_invoker.py:1016 -> effort baked into ``--model "<base> (Word)"``
        return SeatInvocation(lane, render_gemini_model(model, effort), (), MECH_MODEL_NAME)
    if lane == "grok":
        # grok headless -> ``--reasoning-effort <token>`` (alias ``--effort``); the
        # model is passed verbatim via ``-m``. Same flag mechanism as claude.
        token = _GROK_EFFORT[effort]
        return SeatInvocation(lane, model, ("--reasoning-effort", token), MECH_FLAG)
    raise EffortMappingError(
        f"effort mapping for harness {harness!r} is populated in ABDREG/ABDHOME/ABDOMNI"
    )
