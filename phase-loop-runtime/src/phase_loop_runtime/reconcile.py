from __future__ import annotations

import subprocess
from pathlib import Path

from .classifier import classify_all
from .discovery import PLAN_RE, find_plan_artifact, parse_automation_status, plan_matches_roadmap
from .events import read_events
from .models import BLOCKER_CLASSES, StateSnapshot, utc_now
from .provenance import (
    phase_provenance_map,
    provenance_mismatch_reason,
    roadmap_sha256,
    snapshot_provenance,
    status_provenance_matches,
)
from .state import load_state


RECONCILE_EVENT_STATUSES = {
    "complete",
    "blocked",
    "unknown",
    "executed",
    "awaiting_phase_closeout",
    "executing",
    "planned",
    "unplanned",
}


def reconcile(repo: Path, roadmap: Path) -> StateSnapshot:
    phases = classify_all(repo, roadmap)
    current_roadmap_sha = roadmap_sha256(roadmap)
    current_phase_sha = phase_provenance_map(roadmap)
    snapshot = load_state(repo)
    human_required = False
    blocker_class = None
    blocker_summary = None
    required_human_inputs: tuple[str, ...] = ()
    access_attempts: tuple[dict, ...] = ()
    dirty_paths: tuple[str, ...] = ()
    phase_owned_dirty_paths: tuple[str, ...] = ()
    previous_phase_owned_paths: tuple[str, ...] = ()
    unowned_dirty_paths: tuple[str, ...] = ()
    pre_existing_dirty_paths: tuple[str, ...] = ()
    phase_owned_dirty = False
    terminal_summary = None
    closeout_terminal_status = None
    closeout_summary = None
    latest_closeout_summary = None
    latest_terminal_summary = None
    ledger_warnings: list[dict] = []
    ledger_duplicates_skipped: list[dict] = []
    seen_event_keys: set[tuple[object, ...]] = set()
    blocker_phase: str | None = None
    dirty_summary_by_phase: dict[str, dict[str, object]] = {}
    closeout_summary_by_phase: dict[str, dict[str, object]] = {}
    terminal_summary_by_phase: dict[str, dict[str, object]] = {}
    if snapshot:
        same_roadmap = Path(snapshot.roadmap).expanduser().resolve() == roadmap.resolve()
        if same_roadmap:
            for phase, status in snapshot.phases.items():
                if phase not in phases:
                    continue
                if status == "unplanned":
                    continue
                if not status_provenance_matches(status, snapshot.roadmap_sha256, snapshot.phase_sha256.get(phase), current_roadmap_sha, current_phase_sha.get(phase)):
                    ledger_warnings.append(_ledger_warning("state", phase, status, provenance_mismatch_reason(status, snapshot.roadmap_sha256, snapshot.phase_sha256.get(phase), current_roadmap_sha, current_phase_sha.get(phase))))
                    continue
                if status == "planned" and find_plan_artifact(repo, phase, roadmap=roadmap) is None:
                    continue
                if status == "executing":
                    phases[phase] = "unknown" if _dirty(repo) else "executing"
                elif status in {"planned", "complete", "blocked", "unknown", "executed", "awaiting_phase_closeout"}:
                    phases[phase] = status
            if (
                snapshot.current_phase
                and snapshot.phases.get(snapshot.current_phase) in {"blocked", "awaiting_phase_closeout"}
                and status_provenance_matches(
                    snapshot.phases.get(snapshot.current_phase, "unknown"),
                    snapshot.roadmap_sha256,
                    snapshot.phase_sha256.get(snapshot.current_phase),
                    current_roadmap_sha,
                    current_phase_sha.get(snapshot.current_phase),
                )
            ):
                dirty_summary_by_phase[snapshot.current_phase] = {
                    "dirty_paths": list(snapshot.dirty_paths),
                    "phase_owned_dirty_paths": list(snapshot.phase_owned_dirty_paths),
                    "previous_phase_owned_paths": list(snapshot.previous_phase_owned_paths),
                    "unowned_dirty_paths": list(snapshot.unowned_dirty_paths),
                    "pre_existing_dirty_paths": list(snapshot.pre_existing_dirty_paths),
                    "phase_owned_dirty": snapshot.phase_owned_dirty,
                    "terminal_status": snapshot.closeout_terminal_status,
                }
                if snapshot.closeout_summary:
                    closeout_summary_by_phase[snapshot.current_phase] = dict(snapshot.closeout_summary)
                if snapshot.terminal_summary:
                    terminal_summary_by_phase[snapshot.current_phase] = dict(snapshot.terminal_summary)
        if same_roadmap and snapshot.human_required and _snapshot_blocker_trusted(snapshot, current_roadmap_sha, current_phase_sha):
            human_required = snapshot.human_required
            blocker_class = snapshot.blocker_class
            blocker_summary = snapshot.blocker_summary
            required_human_inputs = snapshot.required_human_inputs
            access_attempts = snapshot.access_attempts
    pending_event_warnings: dict[str, list[dict]] = {}
    latest_untrusted_terminal_event: dict[str, dict] = {}
    for raw_event in read_events(repo):
        event = _normalize_automation_event(repo, roadmap, raw_event, current_roadmap_sha, current_phase_sha)
        if Path(str(event.get("roadmap", ""))).expanduser().resolve() != roadmap.resolve():
            continue
        dedup_key = _event_dedup_key(event)
        if event.get("action") == "phase_reopen":
            seen_event_keys = {key for key in seen_event_keys if key[1] != dedup_key[1]}
        dedup_identity = _event_dedup_identity(event, dedup_key)
        if dedup_identity in seen_event_keys:
            ledger_duplicates_skipped.append(_ledger_duplicate_record(event, dedup_key))
            continue
        seen_event_keys.add(dedup_identity)
        phase = str(event.get("phase", "")).upper()
        status = event.get("status")
        if phase not in phases:
            ledger_warnings.append(_ledger_warning("event", phase, str(status or ""), "phase_missing", raw_event=event))
            continue
        if status not in RECONCILE_EVENT_STATUSES:
            ledger_warnings.append(_ledger_warning("event", phase, str(status or ""), "not_in_allowed_status_set", raw_event=event))
            continue
        if int(event.get("schema_version") or 1) < 2:
            ledger_warnings.append(_ledger_warning("event", phase, str(status), "legacy_pre_schema_v2", raw_event=event))
            continue
        if event.get("action") == "state_transition" and _state_transition_metadata(event) is None:
            ledger_warnings.append(_ledger_warning("event", phase, str(status), "malformed_state_transition", raw_event=event))
            continue
        if event.get("action") == "manual_recovery" and _manual_recovery_metadata(event) is None:
            ledger_warnings.append(_ledger_warning("event", phase, str(status), "malformed_manual_recovery", raw_event=event))
            continue
        if phase in phases and status in RECONCILE_EVENT_STATUSES:
            if not _event_status_provenance_matches(event, str(status), current_roadmap_sha, current_phase_sha.get(phase)):
                pending_event_warnings.setdefault(phase, []).append(
                    _ledger_warning(
                        "event",
                        phase,
                        str(status),
                        provenance_mismatch_reason(
                            str(status),
                            event.get("roadmap_sha256"),
                            event.get("phase_sha256"),
                            current_roadmap_sha,
                            current_phase_sha.get(phase),
                        ),
                        raw_event=event,
                    )
                )
                if status in {"blocked", "unknown", "executed", "awaiting_phase_closeout"}:
                    latest_untrusted_terminal_event[phase] = event
                continue
            if status == "planned" and event.get("action") != "phase_reopen" and find_plan_artifact(repo, phase, roadmap=roadmap) is None:
                ledger_warnings.append(_ledger_warning("event", phase, str(status), "planned_without_plan_artifact", raw_event=event))
                continue
            latest_untrusted_terminal_event.pop(phase, None)
            pending_event_warnings.pop(phase, None)
            if phases.get(phase) == "blocked" and status in {"planned", "unplanned"} and not _planned_event_clears_blocker(event):
                ledger_warnings.append(_ledger_warning("event", phase, str(status), "blocker_supersession", raw_event=event))
                continue
            closeout_summary = _event_closeout_summary(event)
            if _closeout_completed(status, closeout_summary):
                status = "complete"
            phases[phase] = status
            dirty_summary = _event_dirty_summary(event)
            if dirty_summary:
                dirty_summary_by_phase[phase] = dirty_summary
            elif status in {"planned", "unplanned", "complete", "executed", "unknown"}:
                dirty_summary_by_phase.pop(phase, None)
            if closeout_summary:
                closeout_summary_by_phase[phase] = closeout_summary
                latest_closeout_summary = {"phase": phase, **closeout_summary}
            elif status in {"planned", "unplanned", "unknown"}:
                closeout_summary_by_phase.pop(phase, None)
            terminal = _event_terminal_summary(event)
            if terminal and _event_terminal_summary_is_event_only(event, terminal):
                ledger_warnings.append(
                    _ledger_warning(
                        "event",
                        phase,
                        "dry_run",
                        "event_only_status",
                        raw_event=event,
                        value="dry_run",
                    )
                )
            elif terminal:
                terminal_summary_by_phase[phase] = terminal
                latest_terminal_summary = {"phase": phase, **terminal}
            elif _event_clears_terminal_summary(event, status):
                terminal_summary_by_phase.pop(phase, None)
                if latest_terminal_summary and latest_terminal_summary.get("phase") == phase:
                    latest_terminal_summary = None
            elif status in {"planned", "unplanned", "unknown"}:
                terminal_summary_by_phase.pop(phase, None)
            blocker = _event_blocker(event)
            preserve_human_blocker = (
                human_required
                and blocker_phase == phase
                and (
                    status == "executed"
                    or (status == "blocked" and _event_has_verified_dirty_closeout_recovery(event))
                )
            )
            if not preserve_human_blocker and not (status == "blocked" and blocker):
                human_required = False
                blocker_class = None
                blocker_summary = None
                required_human_inputs = ()
                access_attempts = ()
                blocker_phase = None
        blocker = _event_blocker(event)
        if (
            blocker
            and phase in phases
            and (phases.get(phase) == "blocked" or (status == "executed" and _truthy(blocker.get("human_required"))))
            and _event_status_provenance_matches(event, str(status), current_roadmap_sha, current_phase_sha.get(phase))
        ):
            human_required = _truthy(blocker.get("human_required"))
            blocker_class = blocker.get("blocker_class")
            blocker_summary = blocker.get("blocker_summary")
            required_human_inputs = tuple(blocker.get("required_human_inputs", ()))
            access_attempts = tuple(blocker.get("access_attempts", ()))
            blocker_phase = phase
    for warnings in pending_event_warnings.values():
        ledger_warnings.extend(warnings)
    for phase, event in latest_untrusted_terminal_event.items():
        if phases.get(phase) == "complete":
            phases[phase] = "unknown"
            ledger_warnings.append(
                _ledger_warning(
                    "event",
                    phase,
                    str(event.get("status")),
                    "newer_untrusted_terminal_event",
                )
            )
    current = _current_phase(phases)
    if current:
        latest_dirty = dirty_summary_by_phase.get(current, {})
        dirty_paths = tuple(latest_dirty.get("dirty_paths", ()))
        phase_owned_dirty_paths = tuple(latest_dirty.get("phase_owned_dirty_paths", ()))
        previous_phase_owned_paths = tuple(latest_dirty.get("previous_phase_owned_paths", ()))
        unowned_dirty_paths = tuple(latest_dirty.get("unowned_dirty_paths", ()))
        pre_existing_dirty_paths = tuple(latest_dirty.get("pre_existing_dirty_paths", ()))
        phase_owned_dirty = bool(latest_dirty.get("phase_owned_dirty", False))
        phase_terminal_summary = terminal_summary_by_phase.get(current)
        terminal_summary = {"phase": current, **phase_terminal_summary} if phase_terminal_summary else None
        closeout_terminal_status = _optional_text(latest_dirty.get("terminal_status"))
        if closeout_terminal_status is None and terminal_summary:
            closeout_terminal_status = _optional_text(terminal_summary.get("terminal_status"))
        closeout_summary = closeout_summary_by_phase.get(current) or latest_closeout_summary
        if not closeout_summary and phases.get(current) == "awaiting_phase_closeout":
            closeout_summary = _default_closeout_summary(closeout_terminal_status)
    else:
        terminal_summary = None if phases and all(status == "complete" for status in phases.values()) else latest_terminal_summary
        closeout_summary = latest_closeout_summary
    if current and phases.get(current) == "blocked" and not human_required:
        plan_blocker = _plan_blocker(repo, roadmap, current)
        if plan_blocker:
            human_required = _truthy(plan_blocker.get("human_required"))
            blocker_class = plan_blocker.get("blocker_class")
            blocker_summary = plan_blocker.get("blocker_summary")
            required_human_inputs = tuple(plan_blocker.get("required_human_inputs", ()))
    if (
        current
        and phases.get(current) == "blocked"
        and not human_required
        and _clean_planned_artifact_supersedes_blocker(repo, roadmap, current, pending_event_warnings.get(current, ()))
    ):
        phases[current] = "planned"
        blocker_class = None
        blocker_summary = None
        required_human_inputs = ()
        access_attempts = ()
        dirty_paths = ()
        phase_owned_dirty_paths = ()
        previous_phase_owned_paths = ()
        unowned_dirty_paths = ()
        pre_existing_dirty_paths = ()
        phase_owned_dirty = False
        terminal_summary = None
        closeout_terminal_status = None
        ledger_warnings.append(_ledger_warning("plan", current, "planned", "clean_plan_superseded_nonhuman_blocker"))
    if current and phases.get(current) == "blocked" and not human_required:
        recovery_closeout = _clean_verified_dirty_closeout_recovery_supersedes_blocker(
            repo,
            roadmap,
            current,
            current_roadmap_sha,
            current_phase_sha,
        )
        if recovery_closeout is not None:
            repaired_phase = current
            phases[repaired_phase] = "complete"
            current = _current_phase(phases)
            blocker_class = None
            blocker_summary = None
            required_human_inputs = ()
            access_attempts = ()
            dirty_paths = ()
            phase_owned_dirty_paths = ()
            previous_phase_owned_paths = ()
            unowned_dirty_paths = ()
            pre_existing_dirty_paths = ()
            phase_owned_dirty = False
            terminal_summary = None
            closeout_terminal_status = None
            closeout = recovery_closeout.get("closeout_summary")
            if isinstance(closeout, dict) and closeout:
                closeout_summary = {"phase": repaired_phase, **closeout}
            ledger_warnings.append(
                _ledger_warning("event", repaired_phase, "complete", "clean_verified_dirty_closeout_recovery_superseded_nonhuman_blocker")
            )
            return StateSnapshot(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(roadmap),
                phases=phases,
                current_phase=current,
                last_action="reconcile",
                human_required=human_required,
                blocker_class=blocker_class,
                blocker_summary=blocker_summary,
                required_human_inputs=required_human_inputs,
                access_attempts=access_attempts,
                dirty_paths=dirty_paths,
                phase_owned_dirty_paths=phase_owned_dirty_paths,
                previous_phase_owned_paths=previous_phase_owned_paths,
                unowned_dirty_paths=unowned_dirty_paths,
                pre_existing_dirty_paths=pre_existing_dirty_paths,
                phase_owned_dirty=phase_owned_dirty,
                terminal_summary=terminal_summary,
                closeout_terminal_status=closeout_terminal_status,
                closeout_summary=closeout_summary,
                ledger_warnings=tuple(ledger_warnings),
                ledger_duplicates_skipped=tuple(ledger_duplicates_skipped),
                **snapshot_provenance(roadmap),
            )
        repair_closeout = _clean_manual_repair_complete_supersedes_blocker(
            repo,
            roadmap,
            current,
            current_roadmap_sha,
            current_phase_sha,
        )
        if repair_closeout is not None:
            repaired_phase = current
            phases[repaired_phase] = "complete"
            current = _current_phase(phases)
            blocker_class = None
            blocker_summary = None
            required_human_inputs = ()
            access_attempts = ()
            dirty_paths = ()
            phase_owned_dirty_paths = ()
            previous_phase_owned_paths = ()
            unowned_dirty_paths = ()
            pre_existing_dirty_paths = ()
            phase_owned_dirty = False
            terminal_summary = None
            closeout_terminal_status = None
            closeout = repair_closeout.get("closeout_summary")
            if isinstance(closeout, dict) and closeout:
                closeout_summary = {"phase": repaired_phase, **closeout}
            ledger_warnings.append(
                _ledger_warning("event", repaired_phase, "complete", "clean_manual_repair_superseded_nonhuman_blocker")
            )
    return StateSnapshot(
        timestamp=utc_now(),
        repo=str(repo),
        roadmap=str(roadmap),
        phases=phases,
        current_phase=current,
        last_action="reconcile",
        human_required=human_required,
        blocker_class=blocker_class,
        blocker_summary=blocker_summary,
        required_human_inputs=required_human_inputs,
        access_attempts=access_attempts,
        dirty_paths=dirty_paths,
        phase_owned_dirty_paths=phase_owned_dirty_paths,
        previous_phase_owned_paths=previous_phase_owned_paths,
        unowned_dirty_paths=unowned_dirty_paths,
        pre_existing_dirty_paths=pre_existing_dirty_paths,
        phase_owned_dirty=phase_owned_dirty,
        terminal_summary=terminal_summary,
        closeout_terminal_status=closeout_terminal_status,
        closeout_summary=closeout_summary,
        ledger_warnings=tuple(ledger_warnings),
        ledger_duplicates_skipped=tuple(ledger_duplicates_skipped),
        **snapshot_provenance(roadmap),
    )


def _event_blocker(event: dict) -> dict:
    blocker = event.get("blocker")
    if isinstance(blocker, dict):
        return _normalize_blocker(blocker)
    if event.get("human_required") is not None or event.get("blocker_class") or event.get("blocker_summary"):
        return _normalize_blocker({
            "human_required": _truthy(event.get("human_required")),
            "blocker_class": event.get("blocker_class"),
            "blocker_summary": event.get("blocker_summary"),
            "required_human_inputs": event.get("required_human_inputs", ()),
            "access_attempts": event.get("access_attempts", ()),
        })
    return {}


def _plan_blocker(repo: Path, roadmap: Path, phase: str) -> dict:
    plan = find_plan_artifact(repo, phase, roadmap=roadmap)
    if not plan:
        return {}
    from .plan_ir import parse_phase_plan_ir

    lane_ir = parse_phase_plan_ir(plan)
    if lane_ir.lanes and lane_ir.diagnostics:
        return {
            "human_required": False,
            "blocker_class": "contract_bug",
            "blocker_summary": "Lane IR diagnostics failed closed for the current phase plan.",
            "required_human_inputs": (),
            "lane_ir_diagnostics": tuple(diagnostic.to_json() for diagnostic in lane_ir.diagnostics),
        }
    automation = parse_automation_status(plan.read_text(encoding="utf-8"))
    if automation.get("automation_status") != "blocked":
        return {}
    human_required = automation.get("automation_human_required")
    blocker_class = automation.get("automation_blocker_class")
    if blocker_class in {None, "", "none"}:
        blocker_class = None
    return {
        "human_required": _truthy(human_required),
        "blocker_class": blocker_class,
        "blocker_summary": automation.get("automation_blocker_summary"),
        "required_human_inputs": automation.get("automation_required_human_inputs", ()),
    }


def _clean_planned_artifact_supersedes_blocker(repo: Path, roadmap: Path, phase: str, warnings: list[dict] | tuple[dict, ...]) -> bool:
    if not any(warning.get("source") == "event" and warning.get("status") == "planned" for warning in warnings):
        return False
    if _dirty(repo):
        return False
    plan = find_plan_artifact(repo, phase, roadmap=roadmap)
    if not plan:
        return False
    try:
        automation = parse_automation_status(plan.read_text(encoding="utf-8"))
    except OSError:
        return False
    return automation.get("automation_status") == "planned"


def _clean_manual_repair_complete_supersedes_blocker(
    repo: Path,
    roadmap: Path,
    phase: str,
    current_roadmap_sha: str,
    current_phase_sha: dict[str, str],
) -> dict[str, object] | None:
    if _dirty(repo):
        return None
    complete_event: dict | None = None
    stale_blocked_after_complete = False
    for raw_event in read_events(repo):
        event = _normalize_automation_event(repo, roadmap, raw_event, current_roadmap_sha, current_phase_sha)
        if Path(str(event.get("roadmap", ""))).expanduser().resolve() != roadmap.resolve():
            continue
        event_phase = str(event.get("phase", "")).upper()
        status = event.get("status")
        if event_phase != phase:
            continue
        if not status_provenance_matches(
            str(status),
            event.get("roadmap_sha256"),
            event.get("phase_sha256"),
            current_roadmap_sha,
            current_phase_sha.get(phase),
        ):
            continue
        if status == "complete" and event.get("action") == "manual_repair" and _planned_event_clears_blocker(event):
            complete_event = event
            stale_blocked_after_complete = False
            continue
        if complete_event and status == "blocked" and _supersedable_nonhuman_repair_blocker(_event_blocker(event)):
            stale_blocked_after_complete = True
    if not complete_event or not stale_blocked_after_complete:
        return None
    return {
        "terminal_summary": _event_terminal_summary(complete_event),
        "closeout_summary": _event_closeout_summary(complete_event),
    }


def _clean_verified_dirty_closeout_recovery_supersedes_blocker(
    repo: Path,
    roadmap: Path,
    phase: str,
    current_roadmap_sha: str,
    current_phase_sha: dict[str, str],
) -> dict[str, object] | None:
    if _dirty(repo):
        return None
    latest_event: dict | None = None
    for raw_event in read_events(repo):
        event = _normalize_automation_event(repo, roadmap, raw_event, current_roadmap_sha, current_phase_sha)
        if Path(str(event.get("roadmap", ""))).expanduser().resolve() != roadmap.resolve():
            continue
        event_phase = str(event.get("phase", "")).upper()
        status = event.get("status")
        if event_phase != phase:
            continue
        if not status_provenance_matches(
            str(status),
            event.get("roadmap_sha256"),
            event.get("phase_sha256"),
            current_roadmap_sha,
            current_phase_sha.get(phase),
        ):
            continue
        latest_event = event
    if latest_event is None:
        return None
    metadata = latest_event.get("metadata")
    if not isinstance(metadata, dict):
        return None
    dirty = metadata.get("completion_dirty_worktree")
    if not isinstance(dirty, dict):
        return None
    if dirty.get("reason") != "verified_dirty_closeout_recovery":
        return None
    if dirty.get("unowned_dirty_paths"):
        return None
    closeout_summary = _event_closeout_summary(latest_event)
    if not closeout_summary.get("closeout_commit"):
        return None
    terminal_summary = _event_terminal_summary(latest_event)
    if terminal_summary.get("verification_status") != "passed":
        return None
    return {
        "terminal_summary": terminal_summary,
        "closeout_summary": closeout_summary,
        "auto_recovery": True,
    }


def _event_has_verified_dirty_closeout_recovery(event: dict) -> bool:
    metadata = event.get("metadata")
    if not isinstance(metadata, dict):
        return False
    dirty = metadata.get("completion_dirty_worktree")
    return isinstance(dirty, dict) and dirty.get("reason") == "verified_dirty_closeout_recovery"


def _supersedable_nonhuman_repair_blocker(blocker: dict) -> bool:
    if not blocker or _truthy(blocker.get("human_required")):
        return False
    summary = str(blocker.get("blocker_summary") or "")
    blocker_class = blocker.get("blocker_class")
    if blocker_class == "repeated_verification_failure":
        return (
            "did not emit a valid shared automation closeout" in summary
            or "blocked outcome without the shared blocker metadata" in summary
        )
    if blocker_class == "dirty_worktree_conflict":
        return "left dirty paths that are not closeout-safe" in summary
    return False


def _dirty(repo: Path) -> bool:
    try:
        return bool(subprocess.check_output(["git", "-C", str(repo), "status", "--short"], text=True).strip())
    except Exception:
        return False


def detect_downstream_plan_staleness(repo: Path, roadmap: Path, completed_phase: str) -> tuple[dict[str, object], ...]:
    from .discovery import parse_frontmatter, parse_roadmap_phases

    aliases = [str(alias).upper() for alias in parse_roadmap_phases(roadmap)]
    current_roadmap_sha = roadmap_sha256(roadmap)
    current_phase_sha = phase_provenance_map(roadmap)
    try:
        start = aliases.index(completed_phase.upper()) + 1
    except ValueError:
        start = 0
    stale: list[dict[str, object]] = []
    for alias in aliases[start:]:
        for plan in sorted((repo / "plans").glob("phase-plan-v*-*.md")):
            match = PLAN_RE.search(plan.name)
            if not match or match.group(2).upper() != alias:
                continue
            metadata = parse_frontmatter(plan.read_text(encoding="utf-8"))
            reasons: list[str] = []
            if metadata.get("roadmap_sha256") != current_roadmap_sha:
                reasons.append("roadmap_sha256")
            plan_phase_sha = metadata.get("phase_sha256")
            if plan_phase_sha and plan_phase_sha != current_phase_sha.get(alias):
                reasons.append("phase_sha256")
            if reasons:
                stale.append({"phase": alias, "plan": str(plan), "reasons": tuple(reasons)})
    return tuple(stale)


def invalidate_stale_downstream_plans(repo: Path, roadmap: Path, completed_phase: str) -> dict[str, object]:
    stale = detect_downstream_plan_staleness(repo, roadmap, completed_phase)
    if not stale:
        return {"status": "unchanged", "stale_plans": ()}
    next_phase = str(stale[0]["phase"])
    return {
        "status": "planning_required",
        "blocker_class": "gold_record_amendment",
        "next_skill": "codex-plan-phase",
        "next_command": f"codex-plan-phase {roadmap} {next_phase}",
        "stale_plans": stale,
    }


def _ledger_warning(
    source: str,
    phase: str,
    status: str,
    reason: str,
    *,
    raw_event: dict | None = None,
    value: object | None = None,
) -> dict:
    return _ledger_warning_record(source, phase, status, reason, raw_event=raw_event, value=value)


def _ledger_warning_record(
    source: str,
    phase: str,
    status: str,
    reason: str,
    *,
    raw_event: dict | None = None,
    value: object | None = None,
) -> dict:
    warning = {
        "source": source,
        "phase": phase,
        "status": status,
        "reason": reason,
        "canonical_reason": _canonical_ledger_reason(reason),
        "value": value,
    }
    if raw_event is not None:
        warning["timestamp"] = _optional_text(raw_event.get("timestamp"))
        warning["action"] = _optional_text(raw_event.get("action"))
        warning["raw_event_summary"] = _raw_event_summary(raw_event)
    return {key: value for key, value in warning.items() if value is not None}


def _event_dedup_key(event: dict) -> tuple[object, ...]:
    phase = str(event.get("phase", "")).upper()
    automation_status = _event_automation_status(event)
    blocker_class = _event_blocker_class(event)
    return (
        _optional_text(event.get("timestamp")),
        phase,
        _optional_text(event.get("action")),
        _optional_text(event.get("status")),
        automation_status,
        blocker_class,
    )


def _event_dedup_identity(event: dict, dedup_key: tuple[object, ...]) -> tuple[object, ...]:
    return (
        *dedup_key,
        _optional_text(event.get("roadmap_sha256")),
        _optional_text(event.get("phase_sha256")),
        event.get("schema_version"),
    )


def _ledger_duplicate_record(event: dict, dedup_key: tuple[object, ...]) -> dict:
    timestamp, phase, action, status, automation_status, blocker_class = dedup_key
    duplicate_key = {
        "timestamp": timestamp,
        "phase": phase,
        "action": action,
        "status": status,
        "automation_status": automation_status,
        "blocker_class": blocker_class,
    }
    return {
        "phase": phase,
        "timestamp": timestamp,
        "action": action,
        "status": status,
        "automation_status": automation_status,
        "blocker_class": blocker_class,
        "duplicate_key": {key: value for key, value in duplicate_key.items() if value is not None},
        "raw_event_summary": _raw_event_summary(event),
    }


def _event_status_provenance_matches(
    event: dict,
    status: str,
    current_roadmap_sha: str,
    current_phase_sha: str | None,
) -> bool:
    if event.get("action") == "manual_recovery" and status == "unplanned":
        status = "planned"
    return status_provenance_matches(
        status,
        event.get("roadmap_sha256"),
        event.get("phase_sha256"),
        current_roadmap_sha,
        current_phase_sha,
    )


def _event_automation_status(event: dict) -> str | None:
    value = _optional_text(event.get("automation_status"))
    if value:
        return value
    automation = event.get("automation")
    if isinstance(automation, dict):
        value = _optional_text(automation.get("status") or automation.get("automation_status"))
        if value:
            return value
    metadata = event.get("metadata")
    if isinstance(metadata, dict):
        child_automation = metadata.get("child_automation")
        if isinstance(child_automation, dict):
            value = _optional_text(child_automation.get("automation_status") or child_automation.get("status"))
            if value:
                return value
        closeout = metadata.get("closeout")
        if isinstance(closeout, dict):
            value = _optional_text(closeout.get("status") or closeout.get("terminal_status"))
            if value:
                return value
        terminal = metadata.get("terminal_summary")
        if isinstance(terminal, dict):
            value = _optional_text(terminal.get("terminal_status"))
            if value:
                return value
    return None


def _event_blocker_class(event: dict) -> str | None:
    blocker = _event_blocker(event)
    value = _optional_text(blocker.get("blocker_class")) if blocker else None
    if value:
        return value
    value = _optional_text(event.get("blocker_class"))
    if value:
        return _normalize_blocker_class(value)
    automation = event.get("automation")
    if isinstance(automation, dict):
        value = _optional_text(automation.get("blocker_class") or automation.get("automation_blocker_class"))
        if value:
            return _normalize_blocker_class(value)
    metadata = event.get("metadata")
    if isinstance(metadata, dict):
        child_automation = metadata.get("child_automation")
        if isinstance(child_automation, dict):
            value = _optional_text(
                child_automation.get("automation_blocker_class") or child_automation.get("blocker_class")
            )
            if value:
                return _normalize_blocker_class(value)
        terminal = metadata.get("terminal_summary")
        if isinstance(terminal, dict):
            terminal_blocker = terminal.get("terminal_blocker")
            if isinstance(terminal_blocker, dict):
                value = _optional_text(terminal_blocker.get("blocker_class"))
                if value:
                    return _normalize_blocker_class(value)
    return None


def _canonical_ledger_reason(reason: str) -> str:
    if reason in {
        "phase_missing",
        "not_in_allowed_status_set",
        "legacy_pre_schema_v2",
        "planned_without_plan_artifact",
        "blocker_supersession",
        "event_only_status",
        "malformed_state_transition",
        "malformed_manual_recovery",
    }:
        return reason
    return "provenance_mismatch"


def _raw_event_summary(event: dict) -> dict[str, object]:
    summary = {
        "schema_version": event.get("schema_version"),
        "source": event.get("source"),
        "phase": event.get("phase"),
        "action": event.get("action"),
        "status": event.get("status"),
        "timestamp": event.get("timestamp"),
        "roadmap_sha256_present": bool(event.get("roadmap_sha256")),
        "phase_sha256_present": bool(event.get("phase_sha256")),
    }
    transition = _state_transition_metadata(event)
    if transition is not None:
        summary["state_transition"] = transition
    return {key: value for key, value in summary.items() if value not in (None, "")}


def _normalize_automation_event(repo: Path, roadmap: Path, event: dict, current_roadmap_sha: str, current_phase_sha: dict[str, str]) -> dict:
    if event.get("phase") or not isinstance(event.get("automation"), dict):
        return event

    automation = event["automation"]
    status = automation.get("status")
    artifact = automation.get("artifact")
    if status not in RECONCILE_EVENT_STATUSES or not artifact:
        return event

    artifact_path = Path(str(artifact)).expanduser().resolve()
    try:
        artifact_path.relative_to(repo.resolve())
    except ValueError:
        return event
    match = PLAN_RE.search(artifact_path.name)
    if not match or not artifact_path.exists():
        return event
    phase = match.group(2).upper()
    if not plan_matches_roadmap(repo, artifact_path, roadmap, phase):
        return event

    normalized = dict(event)
    normalized.update(
        {
            "repo": str(repo),
            "roadmap": str(roadmap),
            "phase": phase,
            "action": event.get("action", "automation"),
            "status": status,
            "source": event.get("source", "manual"),
            "schema_version": 2,
            "roadmap_sha256": current_roadmap_sha,
            "phase_sha256": current_phase_sha.get(phase),
        }
    )
    blocker = _normalize_blocker(
        {
            "human_required": _truthy(automation.get("human_required")),
            "blocker_class": automation.get("blocker_class"),
            "blocker_summary": automation.get("blocker_summary"),
            "required_human_inputs": automation.get("required_human_inputs", ()),
        }
    )
    if blocker:
        normalized["blocker"] = blocker
    return normalized


def _normalize_blocker(raw: dict) -> dict:
    human_required = _truthy(raw.get("human_required"))
    blocker_class = _normalize_blocker_class(_optional_text(raw.get("blocker_class")))
    blocker_summary = _optional_text(raw.get("blocker_summary"))
    required_human_inputs = tuple(raw.get("required_human_inputs") or ())
    access_attempts = tuple(raw.get("access_attempts") or ())
    if not human_required and not blocker_class and not blocker_summary and not required_human_inputs and not access_attempts:
        return {}
    return {
        "human_required": human_required,
        "blocker_class": blocker_class,
        "blocker_summary": blocker_summary,
        "required_human_inputs": required_human_inputs,
        "access_attempts": access_attempts,
    }


def _normalize_blocker_class(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text in BLOCKER_CLASSES:
        return text
    aliases = {
        "operator_auth_required": "account_or_billing_setup",
        "blocked_operator_unavailable": "account_or_billing_setup",
        "interactive_auth_required": "account_or_billing_setup",
        "blocked_by_external_setup": "admin_approval",
        "blocked_by_implementation": "repeated_verification_failure",
    }
    return aliases.get(text, "repeated_verification_failure")


def _event_dirty_summary(event: dict) -> dict[str, object]:
    metadata = event.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    for key in ("completion_dirty_worktree", "plan_dirty_worktree", "incomplete_execute_dirty_worktree"):
        value = metadata.get(key)
        if isinstance(value, dict) and "dirty_paths" in value:
            return {
                "dirty_paths": list(value.get("dirty_paths", ())),
                "phase_owned_dirty_paths": list(value.get("phase_owned_dirty_paths", ())),
                "previous_phase_owned_paths": list(value.get("previous_phase_owned_paths", ())),
                "unowned_dirty_paths": list(value.get("unowned_dirty_paths", ())),
                "pre_existing_dirty_paths": list(value.get("pre_existing_dirty_paths", ())),
                "phase_owned_dirty": bool(value.get("phase_owned_dirty", False)),
                "terminal_status": value.get("terminal_status"),
            }
    return {}


def _event_closeout_summary(event: dict) -> dict[str, object]:
    metadata = event.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    closeout = metadata.get("closeout")
    if not isinstance(closeout, dict):
        manual_repair = metadata.get("manual_repair")
        closeout = manual_repair if isinstance(manual_repair, dict) and manual_repair.get("closeout_commit") else None
    if not isinstance(closeout, dict):
        return {}
    summary = {
        "closeout_mode": _optional_text(closeout.get("closeout_mode")) or ("commit" if closeout.get("closeout_commit") else None),
        "closeout_action": _optional_text(closeout.get("closeout_action")) or ("commit" if closeout.get("closeout_commit") else None),
        "closeout_commit": _optional_text(closeout.get("closeout_commit")),
        "closeout_push_ref": _optional_text(closeout.get("closeout_push_ref")),
        "closeout_refusal_reason": _optional_text(closeout.get("closeout_refusal_reason")),
        "verification_status": _optional_text(closeout.get("verification_status")) or ("passed" if closeout.get("closeout_commit") else None),
    }
    return {key: value for key, value in summary.items() if value is not None}


def _closeout_completed(status: object, closeout: dict[str, object]) -> bool:
    if status == "complete":
        return True
    if status == "blocked":
        return False
    if status == "planned":
        return False
    if closeout.get("verification_status") in {"blocked", "failed"}:
        return False
    action = closeout.get("closeout_action")
    commit = closeout.get("closeout_commit")
    return bool(commit and action in {"commit", "push", "push_refused"})


def _event_terminal_summary(event: dict) -> dict[str, object]:
    metadata = event.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    terminal = metadata.get("terminal_summary")
    if not isinstance(terminal, dict):
        return {}
    summary = {
        "terminal_status": _optional_text(terminal.get("terminal_status")),
        "terminal_blocker": terminal.get("terminal_blocker"),
        "verification_status": _optional_text(terminal.get("verification_status")),
        "next_action": _optional_text(terminal.get("next_action")),
        "dirty_paths": list(terminal.get("dirty_paths", ())),
        "phase_owned_dirty": bool(terminal.get("phase_owned_dirty", False)),
        "phase_owned_dirty_paths": list(terminal.get("phase_owned_dirty_paths", ())),
        "previous_phase_owned_paths": list(terminal.get("previous_phase_owned_paths", ())),
        "unowned_dirty_paths": list(terminal.get("unowned_dirty_paths", ())),
        "pre_existing_dirty_paths": list(terminal.get("pre_existing_dirty_paths", ())),
        "artifact_paths": dict(terminal.get("artifact_paths", {})) if isinstance(terminal.get("artifact_paths"), dict) else {},
    }
    return {key: value for key, value in summary.items() if value is not None}


def _event_terminal_summary_is_event_only(event: dict, terminal: dict[str, object]) -> bool:
    if terminal.get("terminal_status") != "dry_run":
        return False
    metadata = event.get("metadata")
    if not isinstance(metadata, dict):
        return False
    if metadata.get("dry_run_only") is True:
        return True
    launch = metadata.get("launch")
    return isinstance(launch, dict) and (launch.get("dry_run") is True or launch.get("dry_run_only") is True)


def _default_closeout_summary(terminal_status: object) -> dict[str, object]:
    verification_status = "passed" if terminal_status == "complete" else "not_run"
    return {
        "closeout_mode": "manual",
        "closeout_action": "awaiting_phase_closeout",
        "verification_status": verification_status,
    }


def _planned_event_clears_blocker(event: dict) -> bool:
    transition = _state_transition_metadata(event)
    if transition is not None and event.get("status") in {"planned", "executing"}:
        return transition.get("reason") == "repair_precondition_cleared"
    recovery = _manual_recovery_metadata(event)
    if recovery is not None and event.get("status") in {"planned", "unplanned"}:
        return bool(recovery.get("clears_blocker"))
    if event.get("action") != "manual_repair":
        return False
    metadata = event.get("metadata")
    if not isinstance(metadata, dict):
        return False
    manual_repair = metadata.get("manual_repair")
    if isinstance(manual_repair, dict):
        return bool(manual_repair.get("clears_blocker"))
    return bool(metadata.get("clears_blocker"))


def _manual_recovery_metadata(event: dict) -> dict[str, object] | None:
    if event.get("action") != "manual_recovery":
        return None
    metadata = event.get("metadata")
    if not isinstance(metadata, dict):
        return None
    recovery = metadata.get("manual_recovery")
    if not isinstance(recovery, dict):
        return None
    required = ("from", "to", "reason", "trigger")
    if any(not _optional_text(recovery.get(key)) for key in required):
        return None
    if recovery.get("from") != "blocked":
        return None
    if recovery.get("to") not in {"planned", "unplanned"}:
        return None
    if recovery.get("to") != event.get("status"):
        return None
    if recovery.get("trigger") != "cli":
        return None
    if recovery.get("verification_status") != "not_run":
        return None
    if not recovery.get("clears_blocker"):
        return None
    return recovery


def _state_transition_metadata(event: dict) -> dict[str, object] | None:
    if event.get("action") != "state_transition":
        return None
    metadata = event.get("metadata")
    if not isinstance(metadata, dict):
        return None
    transition = metadata.get("state_transition")
    if not isinstance(transition, dict):
        return None
    required = ("from", "to", "reason", "trigger")
    if any(not _optional_text(transition.get(key)) for key in required):
        return None
    return transition


def _event_clears_terminal_summary(event: dict, status: object) -> bool:
    if _manual_recovery_metadata(event) is not None and status in {"planned", "unplanned"}:
        return True
    if event.get("action") == "manual_repair":
        metadata = event.get("metadata")
        if isinstance(metadata, dict):
            manual_repair = metadata.get("manual_repair")
            if isinstance(manual_repair, dict) and manual_repair.get("clears_blocker") and status in {"complete", "planned"}:
                return True
            if metadata.get("clears_blocker") and status in {"complete", "planned"}:
                return True
    if status != "complete":
        return False
    return event.get("action") in {"execute", "run", "manual"}


def _optional_text(value: object) -> object | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lower() in {"", "none", "null"}:
            return None
        if stripped.startswith("<") and stripped.endswith(">") and "none" in stripped.lower():
            return None
        return stripped
    return value


def _snapshot_blocker_trusted(snapshot: StateSnapshot, current_roadmap_sha: str, current_phase_sha: dict[str, str]) -> bool:
    for phase, status in snapshot.phases.items():
        if status == "blocked" and status_provenance_matches(status, snapshot.roadmap_sha256, snapshot.phase_sha256.get(phase), current_roadmap_sha, current_phase_sha.get(phase)):
            return True
    return False


def _current_phase(phases: dict[str, str]) -> str | None:
    for preferred_status in ("blocked", "awaiting_phase_closeout", "unknown", "executing", "executed", "planned", "unplanned"):
        for phase, status in phases.items():
            if status == preferred_status:
                return phase
    return None


def _truthy(value: object) -> bool:
    return value is True or str(value).lower() == "true"
