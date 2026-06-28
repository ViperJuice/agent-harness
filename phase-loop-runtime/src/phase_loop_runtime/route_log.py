"""Route-decision logging (model-routing-v1 P4).

A metadata-only record of *why* a dispatch resolved to a given model/effort, so a
human auditing a run can answer "what tier ran this, at what effort, and why".
Built from a `ModelSelection` (which carries only routing provenance — model,
effort, source, override_reason, model_class), so the record is redaction-safe by
construction: it never touches artifact bodies, prompts, or credentials.
"""
from __future__ import annotations

from typing import Any

from .models import ModelSelection

ROUTE_LOG_KEY = "route"


def build_route_log(
    selection: ModelSelection,
    *,
    route_reason: str | None = None,
    escalated_from: str | None = None,
    escalated_to: str | None = None,
) -> dict[str, Any]:
    """A metadata-only routing record for a dispatch event's ``metadata[route]``."""
    log: dict[str, Any] = {
        "model_class": selection.model_class,
        "concrete_model": selection.model,
        "effort": selection.effort,
        "route_reason": route_reason or selection.override_reason or selection.source,
    }
    if escalated_from is not None:
        log["escalated_from"] = escalated_from
    if escalated_to is not None:
        log["escalated_to"] = escalated_to
    return log


def with_route_log(
    metadata: dict[str, Any] | None,
    selection: ModelSelection,
    *,
    route_reason: str | None = None,
    escalated_from: str | None = None,
    escalated_to: str | None = None,
) -> dict[str, Any]:
    """Return ``metadata`` with the route record merged under ``ROUTE_LOG_KEY``.

    Additive: a dispatch event-emission site calls this on its existing metadata
    (or None) to attach the route record without disturbing other keys.
    """
    merged = dict(metadata or {})
    merged[ROUTE_LOG_KEY] = build_route_log(
        selection,
        route_reason=route_reason,
        escalated_from=escalated_from,
        escalated_to=escalated_to,
    )
    return merged
