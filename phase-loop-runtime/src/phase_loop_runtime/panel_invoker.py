"""Panel-invoker interface (model-routing-v1 P2, IF-0-P2-2).

The deterministic Python runner has no native "invoke a skill" primitive, so a
3-harness advisor panel means spawning the subscription CLI legs
(codex / agy / native-claude) as child processes. This module is the *named,
fail-closed* boundary for that — not an inline call buried in the runner.

Real CLI execution is a single injectable seam (`spawn`); the test suite mocks
it and never calls a frontier model. Each leg's result carries an explicit
status so a verbose auth error is never mistaken for a real review.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import Callable, Sequence

# Panel legs are vendor identities (one model class per vendor for the panel).
PANEL_LEGS: tuple[str, ...] = ("codex", "gemini", "claude")
LEG_STATUSES: tuple[str, ...] = ("ok", "empty", "degraded", "timeout", "unavailable")

# Which CLI binary backs each leg (used for metadata-only liveness preflight).
_LEG_CLI: dict[str, str] = {"codex": "codex", "gemini": "agy", "claude": "claude"}


@dataclass(frozen=True)
class PanelLegResult:
    leg: str            # vendor: codex | gemini | claude
    status: str         # one of LEG_STATUSES
    text: str = ""
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.status not in LEG_STATUSES:
            raise ValueError(f"invalid panel leg status: {self.status!r}")

    @property
    def usable(self) -> bool:
        return self.status == "ok" and bool(self.text.strip())


@dataclass(frozen=True)
class PanelResult:
    legs: tuple[PanelLegResult, ...] = ()

    @property
    def usable_legs(self) -> tuple[PanelLegResult, ...]:
        return tuple(leg for leg in self.legs if leg.usable)


def available_panel_legs(probe: Callable[[str], bool] | None = None) -> tuple[str, ...]:
    """Metadata-only liveness preflight: which panel legs have their CLI present.

    `probe(cli) -> bool` is injectable for tests; the default checks PATH only
    (does not authenticate or spend tokens).
    """
    check = probe if probe is not None else (lambda cli: shutil.which(cli) is not None)
    return tuple(leg for leg in PANEL_LEGS if check(_LEG_CLI[leg]))


# spawn(leg, artifact) -> (status, text); the only real-exec boundary.
SpawnFn = Callable[[str, str], "tuple[str, str]"]


def _default_spawn(leg: str, artifact: str) -> tuple[str, str]:  # pragma: no cover
    """Placeholder real-exec boundary. Production wiring shells out to the
    advisor-panel CLI legs; never exercised by the test suite (mocked)."""
    raise NotImplementedError(
        "panel_invoker._default_spawn must be wired to the advisor-panel CLI "
        "legs in production; tests inject a spawn mock"
    )


def invoke_panel(
    artifact: str,
    legs: Sequence[str],
    *,
    spawn: SpawnFn | None = None,
) -> PanelResult:
    """Run the requested panel legs through the spawn boundary, fail-closed.

    A leg whose spawn raises, returns an unknown status, or returns empty text
    on an `ok` status is recorded as `degraded`/`empty` — never silently dropped
    and never mistaken for a real review.
    """
    runner = spawn if spawn is not None else _default_spawn
    results: list[PanelLegResult] = []
    for leg in legs:
        try:
            status, text = runner(leg, artifact)
        except Exception as exc:  # fail-closed: a broken leg degrades, never crashes the gate
            results.append(PanelLegResult(leg=leg, status="degraded", text="", detail=str(exc)[:200]))
            continue
        status = status if status in LEG_STATUSES else "degraded"
        if status == "ok" and not str(text).strip():
            status = "empty"
        results.append(PanelLegResult(leg=leg, status=status, text=str(text)))
    return PanelResult(legs=tuple(results))
