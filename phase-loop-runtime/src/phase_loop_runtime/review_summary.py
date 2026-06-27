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
