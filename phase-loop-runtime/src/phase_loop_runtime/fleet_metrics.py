"""CS-2.1 SA — fleet-metric ledger emission + pure derivation.

This is the enforcement-side PUSH source for the three LEDGER-FAITHFUL named
fleet-trend metrics the CS-2.1 spine reserved (velocity / burn_down /
promise_broken_duration). It is additive observability on the harness's OWN
ledger:

  * Events are appended to a SEPARATE sibling ledger,
    ``.phase-loop/fleet-metrics.jsonl`` — ``events.jsonl`` is never touched, so
    its bytes, digests, and reconcile/fold semantics are provably unaffected.
  * Every append is a single ``os.write`` on an ``O_APPEND`` fd (mirrors
    ``events._append_jsonl`` and ``train_ledger.append_record``).
  * Derivation is pure (no I/O) so the velocity/burn_down/promise-broken-duration
    series are unit-testable without a runner or a database.

The load-bearing boundary: this file lives on the ENFORCEMENT side of the
``.phase-loop`` wall. Portal never reads it. Only the sanitized aggregate
series (see ``fleet_metrics_export``) crosses to Portal, one-way.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import FleetMetricEvent
from .runtime_paths import (
    ensure_phase_loop_excluded,
    phase_loop_fleet_metrics_file,
    phase_loop_fleet_metrics_read_files,
)


# ── append + read (the sibling ledger) ─────────────────────────────────────

def fleet_metrics_path(repo: Path) -> Path:
    return phase_loop_fleet_metrics_file(repo)


def append_fleet_metric(repo: Path, event: FleetMetricEvent) -> None:
    """Atomically append one fleet-metric event to the sibling ledger."""
    ensure_phase_loop_excluded(repo)
    path = fleet_metrics_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(event.to_json(), sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o666)
    try:
        os.write(fd, encoded)
    finally:
        os.close(fd)


def read_fleet_metrics(repo: Path) -> list[dict[str, Any]]:
    """Tolerant reader: skips blank/malformed lines, keeps only fleet_metric records."""
    records: list[dict[str, Any]] = []
    for path in phase_loop_fleet_metrics_read_files(repo):
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict) and record.get("event_kind") == "fleet_metric":
                    records.append(record)
    return records


# ── runner hook — emit at closeout ─────────────────────────────────────────

def record_phase_fleet_metrics(
    repo: Path,
    *,
    phase: str,
    completed: bool,
    total_scope: int,
    completed_count: int,
    missing_gates: Iterable[str] = (),
    produced_gates: Iterable[str] = (),
    timestamp: str | None = None,
) -> list[FleetMetricEvent]:
    """Emit the fleet-metric events implied by one phase closeout.

    Truth sources (all already known at the closeout site, runner.py):
      * ``completed`` / ``completed_count`` / ``total_scope`` — velocity + burn_down.
      * ``missing_gates`` (declared-but-not-produced, from ``validate_produced_gates``)
        — a BROKEN promise per gate.
      * ``produced_gates`` that had an earlier unmatched ``promise_broken`` — a
        REPAIRED promise (break-anchored, so duration is broken→repaired, never
        mere fulfillment lead-time).

    Best-effort: callers wrap this so observability can never break the loop.
    """
    ts = timestamp or _utc_now()
    emitted: list[FleetMetricEvent] = []

    if completed:
        emitted.append(
            FleetMetricEvent(
                metric_kind="velocity",
                timestamp=ts,
                phase=phase,
                payload={"completed_total": int(completed_count)},
            )
        )
        remaining = max(0, int(total_scope) - int(completed_count))
        emitted.append(
            FleetMetricEvent(
                metric_kind="burn_down",
                timestamp=ts,
                phase=phase,
                payload={
                    "total_scope": int(total_scope),
                    "completed": int(completed_count),
                    "remaining": remaining,
                },
            )
        )

    # Which gates are currently open breaks (declared-but-not-produced without a
    # later repair) — computed from the ledger so repairs are break-anchored.
    open_breaks = _open_promise_breaks(read_fleet_metrics(repo))
    missing = [str(g) for g in missing_gates if str(g).strip()]
    produced = {str(g) for g in produced_gates if str(g).strip()}

    for gate in missing:
        if gate in open_breaks:
            continue  # already an open break — don't double-count
        emitted.append(
            FleetMetricEvent(
                metric_kind="promise_broken",
                timestamp=ts,
                phase=phase,
                payload={"gate": gate},
            )
        )
        open_breaks[gate] = ts

    for gate in sorted(produced):
        if gate in open_breaks:
            emitted.append(
                FleetMetricEvent(
                    metric_kind="promise_repaired",
                    timestamp=ts,
                    phase=phase,
                    payload={"gate": gate},
                )
            )
            open_breaks.pop(gate, None)

    for event in emitted:
        append_fleet_metric(repo, event)
    return emitted


def _open_promise_breaks(records: Iterable[dict[str, Any]]) -> dict[str, str]:
    """Fold the ledger to gates with an open break (break with no later repair).

    Returns ``{gate: broken_at_ts}``. Records are in append order.
    """
    open_at: dict[str, str] = {}
    for record in records:
        kind = record.get("metric_kind")
        gate = str((record.get("payload") or {}).get("gate") or "").strip()
        if not gate:
            continue
        if kind == "promise_broken":
            open_at.setdefault(gate, str(record.get("timestamp") or ""))
        elif kind == "promise_repaired":
            open_at.pop(gate, None)
    return open_at


# ── pure derivation — the sanitizable aggregate series ─────────────────────

def derive_fleet_metric_series(
    records: Iterable[dict[str, Any]],
    *,
    now: str | None = None,
) -> dict[str, Any]:
    """Fold fleet-metric records into ledger-faithful aggregate time-series.

    Pure: no I/O. Output contains ONLY numeric aggregates + ISO timestamps +
    fixed enum labels — no gate identifiers, paths, secrets, or payloads. This
    is exactly what the export bridge is allowed to push to Portal.
    """
    records = list(records)
    now_ts = now or _utc_now()

    velocity_points: list[dict[str, Any]] = []
    burndown_points: list[dict[str, Any]] = []
    # Break/repair pairing keyed by gate (gate id stays LOCAL — only durations leave).
    break_open: dict[str, str] = {}
    repaired_durations: list[float] = []
    open_break_started: dict[str, str] = {}

    for record in records:
        kind = record.get("metric_kind")
        ts = str(record.get("timestamp") or "")
        payload = record.get("payload") or {}
        if kind == "velocity":
            velocity_points.append(
                {"t": ts, "completed_total": int(payload.get("completed_total") or 0)}
            )
        elif kind == "burn_down":
            burndown_points.append(
                {
                    "t": ts,
                    "total": int(payload.get("total_scope") or 0),
                    "completed": int(payload.get("completed") or 0),
                    "remaining": int(payload.get("remaining") or 0),
                }
            )
        elif kind == "promise_broken":
            gate = str(payload.get("gate") or "")
            if gate:
                break_open.setdefault(gate, ts)
                open_break_started.setdefault(gate, ts)
        elif kind == "promise_repaired":
            gate = str(payload.get("gate") or "")
            started = break_open.pop(gate, None)
            open_break_started.pop(gate, None)
            if started:
                repaired_durations.append(_seconds_between(started, ts))

    # promise_broken_duration series: one point per broken promise carrying only
    # its duration (seconds) + whether it was repaired — never the gate id.
    broken_points: list[dict[str, Any]] = []
    # Re-walk to preserve order + emit repaired ones with their measured duration.
    replay_open: dict[str, str] = {}
    for record in records:
        kind = record.get("metric_kind")
        ts = str(record.get("timestamp") or "")
        gate = str((record.get("payload") or {}).get("gate") or "")
        if kind == "promise_broken" and gate:
            replay_open.setdefault(gate, ts)
        elif kind == "promise_repaired" and gate and gate in replay_open:
            started = replay_open.pop(gate)
            broken_points.append(
                {"t": ts, "broken_seconds": _seconds_between(started, ts), "repaired": True}
            )
    # Still-open breaks: duration measured to `now`.
    for gate, started in sorted(open_break_started.items()):
        broken_points.append(
            {"t": now_ts, "broken_seconds": _seconds_between(started, now_ts), "repaired": False}
        )

    open_count = len(open_break_started)
    open_seconds = [
        _seconds_between(started, now_ts) for started in open_break_started.values()
    ]
    mean_repaired = (
        round(sum(repaired_durations) / len(repaired_durations), 3)
        if repaired_durations
        else None
    )

    return {
        "velocity": {"points": velocity_points},
        "burn_down": {"points": burndown_points},
        "promise_broken_duration": {
            "points": broken_points,
            "aggregate": {
                "open_count": open_count,
                "max_open_seconds": round(max(open_seconds), 3) if open_seconds else 0,
                "mean_repaired_seconds": mean_repaired,
                "repaired_count": len(repaired_durations),
            },
        },
    }


def _seconds_between(start_ts: str, end_ts: str) -> float:
    try:
        start = _parse_ts(start_ts)
        end = _parse_ts(end_ts)
    except ValueError:
        return 0.0
    return max(0.0, round((end - start).total_seconds(), 3))


def _parse_ts(value: str) -> datetime:
    text = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
