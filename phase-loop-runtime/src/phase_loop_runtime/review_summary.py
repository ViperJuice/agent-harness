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


def _walk_findings(obj: Any) -> Iterable[dict]:
    if isinstance(obj, dict):
        if obj.get("kind") == "review_finding":
            yield obj
        for value in obj.values():
            yield from _walk_findings(value)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _walk_findings(item)


def collect_review_findings(events: Iterable[Any]) -> list[dict]:
    """Extract de-duplicated review findings from ledger events, newest first.

    Each returned finding is tagged with the ``phase`` of the event it was found
    in. The same finding can appear in several events (the closeout is echoed
    across records); dedup by (phase, code, reason, severity).
    """
    out: list[dict] = []
    seen: set[tuple] = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        phase = event.get("phase")
        for finding in _walk_findings(event):
            key = (
                phase,
                finding.get("code"),
                finding.get("reason"),
                finding.get("severity"),
            )
            if key in seen:
                continue
            seen.add(key)
            tagged = dict(finding)
            tagged["phase"] = phase
            out.append(tagged)
    return out


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
    """Extract de-duplicated ``panel_verdict`` records from ledger events."""
    out: list[dict] = []
    seen: set[tuple] = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        for record in _walk_panel_verdicts(event):
            key = (record.get("phase"), record.get("outcome"), record.get("degraded"), record.get("rounds"))
            if key in seen:
                continue
            seen.add(key)
            out.append(record)
    return out


def _walk_panel_verdicts(obj: Any) -> Iterable[dict]:
    if isinstance(obj, dict):
        if obj.get("kind") == "panel_verdict":
            yield obj
        for value in obj.values():
            yield from _walk_panel_verdicts(value)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _walk_panel_verdicts(item)


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
