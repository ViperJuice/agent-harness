"""Run-end review-findings summary (rigor-v1 P7 / acceptance #3).

Review gates default to ``warn``: each finding is recorded inside its phase's
closeout (``verification.results``, ``kind="review_finding"``) and the loop
continues. So a human reviewing a bounded run between ``--max-phases`` batches
needs the findings aggregated and surfaced — that is what this module does.

``collect_review_findings`` recursively scans ledger events for review-finding
records (robust to exactly where the closeout nests them) and de-duplicates.
``render_review_findings_summary`` renders an operator-readable block. The
runner emits it to stderr at run end.
"""
from __future__ import annotations

from typing import Any, Iterable


def _walk_kind(obj: Any, kind: str) -> Iterable[dict]:
    """Recursively yield every dict in ``obj`` whose ``kind`` field matches."""
    if isinstance(obj, dict):
        if obj.get("kind") == kind:
            yield obj
        for value in obj.values():
            yield from _walk_kind(value, kind)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _walk_kind(item, kind)


def _collect_by_kind(events, kind, *, key, tag_phase):
    """Shared collect-and-dedup skeleton: scan events for records of ``kind``,
    dedup by ``key(record, event_phase)``. When ``tag_phase`` the returned record
    is tagged with its event's ``phase`` (review findings); otherwise the record
    is returned as-is (panel verdicts already carry their own ``phase``)."""
    out: list[dict] = []
    seen: set[tuple] = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        phase = event.get("phase")
        for record in _walk_kind(event, kind):
            k = key(record, phase)
            if k in seen:
                continue
            seen.add(k)
            if tag_phase:
                tagged = dict(record)
                tagged["phase"] = phase
                out.append(tagged)
            else:
                out.append(record)
    return out


def collect_review_findings(events: Iterable[Any]) -> list[dict]:
    """Extract de-duplicated review findings from ledger events, tagging each with
    its event's ``phase``; dedup by (phase, code, reason, severity)."""
    return _collect_by_kind(
        events,
        "review_finding",
        key=lambda r, phase: (phase, r.get("code"), r.get("reason"), r.get("severity")),
        tag_phase=True,
    )


def render_review_findings_summary(findings: list[dict]) -> str:
    """Operator-readable summary block, or '' when there are no findings."""
    if not findings:
        return ""
    by_sev: dict[str, int] = {}
    for f in findings:
        by_sev[str(f.get("severity") or "warn")] = by_sev.get(str(f.get("severity") or "warn"), 0) + 1
    counts = ", ".join(f"{n} {sev}" for sev, n in sorted(by_sev.items()))
    lines = [
        f"Review findings this run: {len(findings)} ({counts}).",
        "Recorded by autonomy-first gates; the loop was not stalled. "
        "Address them or set PHASE_LOOP_REVIEW=block to enforce on the next run.",
    ]
    for f in findings:
        lines.append(
            f"  - [{f.get('severity', 'warn')}] {f.get('phase') or '?'}: "
            f"{f.get('code')} — {f.get('reason')}"
        )
    return "\n".join(lines)


def summarize_run_review_findings(events: Iterable[Any]) -> str:
    """Convenience: collect + render in one call."""
    return render_review_findings_summary(collect_review_findings(events))


# --- model-routing-v1 P4: governed panel verdicts in the run-end summary -------

def panel_verdict_record(
    *,
    phase: str,
    outcome: str,
    degraded: bool = False,
    rounds: int | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """A metadata-only ledger record describing a governed panel outcome.

    ``outcome`` is one of ``mergeable`` / ``blocked`` / ``degraded``; the governed
    pre-merge/planning gates emit this so a human auditing a governed run sees the
    verdict in the run-end summary.
    """
    record: dict[str, Any] = {"kind": "panel_verdict", "phase": phase, "outcome": outcome, "degraded": bool(degraded)}
    if rounds is not None:
        record["rounds"] = rounds
    if reason is not None:
        record["reason"] = reason
    return record


def collect_panel_verdicts(events: Iterable[Any]) -> list[dict]:
    """Extract de-duplicated ``panel_verdict`` records from ledger events
    (records already carry their own ``phase``); dedup by
    (phase, outcome, degraded, rounds)."""
    return _collect_by_kind(
        events,
        "panel_verdict",
        key=lambda r, _phase: (r.get("phase"), r.get("outcome"), r.get("degraded"), r.get("rounds")),
        tag_phase=False,
    )


def render_panel_verdicts_summary(verdicts: list[dict]) -> str:
    """Operator-readable panel-verdict block, or '' when there are none."""
    if not verdicts:
        return ""
    lines = [f"Governed panel verdicts this run: {len(verdicts)}."]
    for v in verdicts:
        flag = " (degraded — advisory only)" if v.get("degraded") else ""
        rounds = f", {v['rounds']} round(s)" if v.get("rounds") is not None else ""
        lines.append(
            f"  - {v.get('phase') or '?'}: {v.get('outcome')}{flag}{rounds}"
            + (f" — {v['reason']}" if v.get("reason") else "")
        )
    return "\n".join(lines)


def summarize_run(events: Iterable[Any]) -> str:
    """The full run-end summary: review findings + governed panel verdicts."""
    events = list(events)
    blocks = [
        render_review_findings_summary(collect_review_findings(events)),
        render_panel_verdicts_summary(collect_panel_verdicts(events)),
    ]
    return "\n\n".join(b for b in blocks if b)
