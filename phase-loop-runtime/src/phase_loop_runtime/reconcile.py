from __future__ import annotations

import subprocess
from pathlib import Path

from .classifier import classify_all
from .closeout_classifier import classify_unowned_path
from .discovery import PLAN_RE, find_plan_artifact, manifest_plan_artifact, parse_automation_status, plan_matches_roadmap
from .events import read_events
from .models import BLOCKER_CLASSES, StateSnapshot, utc_now
from .pipeline_adapter.branch_ops import REFUSE_DEFAULT_BRANCH_COMMIT_PREFIX
from .runtime_paths import roadmap_paths_match
from .provenance import (
    phase_provenance_map,
    phase_sha256,
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


def reconcile(repo: Path, roadmap: Path, *, read_only: bool = False) -> StateSnapshot:
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
    ledger_warnings.extend(_reconcile_plan_manifest(repo, roadmap, phases, read_only=read_only))
    relocation_warned = False
    if snapshot:
        same_roadmap, _relocated = roadmap_paths_match(snapshot.repo, snapshot.roadmap, repo, roadmap)
        if same_roadmap and _relocated and not relocation_warned:
            # ah#85(C): persisted state came from a different (moved/renamed/copied) repo
            # root; we rebased it via repo-relative matching. Surface one informational
            # portability warning instead of silently replaying as all-unplanned.
            ledger_warnings.append(
                _ledger_warning("state", str(snapshot.current_phase or ""), "", "repo_relocated")
            )
            relocation_warned = True
        if same_roadmap:
            for phase, status in snapshot.phases.items():
                if phase not in phases:
                    continue
                if status == "unplanned":
                    continue
                entry_phase_sha = snapshot.phase_sha256.get(phase)
                current_sha = current_phase_sha.get(phase)
                if not status_provenance_matches(status, snapshot.roadmap_sha256, entry_phase_sha, current_roadmap_sha, current_sha):
                    reason = provenance_mismatch_reason(status, snapshot.roadmap_sha256, entry_phase_sha, current_roadmap_sha, current_sha)
                    warning = _ledger_warning("state", phase, status, reason)
                    warning.update(_amendment_drift_fields(status, reason, entry_phase_sha, current_sha))
                    ledger_warnings.append(warning)
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
    legacy_executor_closeout_action_warned = False
    for raw_event in read_events(repo):
        event = _normalize_automation_event(repo, roadmap, raw_event, current_roadmap_sha, current_phase_sha)
        event, normalized_legacy_executor_closeout = _normalize_legacy_executor_closeout_action(event)
        if not roadmap_paths_match(event.get("repo"), event.get("roadmap"), repo, roadmap)[0]:
            continue
        if normalized_legacy_executor_closeout and not legacy_executor_closeout_action_warned:
            ledger_warnings.append(
                _ledger_warning(
                    "event",
                    str(event.get("phase", "")).upper(),
                    str(event.get("status") or ""),
                    "legacy_executor_closeout_action_normalized",
                    raw_event=raw_event,
                )
            )
            legacy_executor_closeout_action_warned = True
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
                event_reason = provenance_mismatch_reason(
                    str(status),
                    event.get("roadmap_sha256"),
                    event.get("phase_sha256"),
                    current_roadmap_sha,
                    current_phase_sha.get(phase),
                )
                event_warning = _ledger_warning("event", phase, str(status), event_reason, raw_event=event)
                event_warning.update(
                    _amendment_drift_fields(
                        str(status), event_reason, event.get("phase_sha256"), current_phase_sha.get(phase)
                    )
                )
                pending_event_warnings.setdefault(phase, []).append(event_warning)
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
    # Self-clearing stale blocker pass (regenesis v37 fix).
    #
    # state.json (or the latest event) can hold a cached blocker whose
    # precondition was already resolved out-of-band (operator manually
    # created the pipeline branch, resolved the merge, etc.). Without
    # this pass reconcile would echo the stale blocker forever and the
    # runner would refuse to advance.
    #
    # Narrow scope, intentionally:
    #   - human_required=True blockers NEVER self-clear -- the operator
    #     tagged it and the runner does not second-guess that.
    #   - dirty_worktree_conflict is intentionally NOT covered here. The
    #     runner already emits "repair_precondition_cleared" state
    #     transitions for that class via a richer event-level mechanism
    #     (see test_phase_loop_repair_skipped_when_blocker_cleared);
    #     clearing it in reconcile would preempt that flow and break the
    #     audit trail.
    #   - Human-action classes (admin_approval, missing_secret,
    #     product_decision_missing, ...) default-deny -- they cannot be
    #     detected from repo state.
    if (
        current
        and phases.get(current) == "blocked"
        and blocker_class
        and not human_required
        and _blocker_precondition_cleared(
            repo, str(blocker_class), blocker_summary=str(blocker_summary or "")
        )
    ):
        cleared_class = str(blocker_class)
        phases[current] = "planned" if find_plan_artifact(repo, current, roadmap=roadmap) else "unplanned"
        human_required = False
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
        ledger_warnings.append(
            _ledger_warning(
                "blocker",
                current,
                phases[current],
                "blocker_precondition_self_cleared",
                value=cleared_class,
            )
        )
        current = _current_phase(phases)
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


def _manifest_file_phase_key(entry) -> tuple[str, str]:
    """#46: dedup identity for a manifest plan entry — normalized file path +
    upper-cased phase alias. Matches entries pointing at the same phase plan
    file/phase regardless of their (planner vs synthetic-import) slug, so
    reconcile does not re-add a duplicate imported row."""
    file_norm = str(getattr(entry, "file", "") or "").replace("\\", "/").strip().lower()
    phase_norm = str(getattr(entry, "phase_alias", "") or "").strip().upper()
    return (file_norm, phase_norm)


def _reconcile_plan_manifest(repo: Path, roadmap: Path, phases: dict[str, str], *, read_only: bool = False) -> list[dict]:
    from .discovery import _phase_manifest_disabled

    if _phase_manifest_disabled():
        return []
    if not (repo / "plans" / "manifest.json").exists():
        return []
    warnings: list[dict] = []
    try:
        from .plan_manifest import append_entry, import_existing_phase_plans, read_manifest, update_lifecycle
    except Exception as exc:
        return [_ledger_warning("manifest", "", "unknown", "manifest_import_unavailable", value=str(exc))]
    try:
        manifest = read_manifest(repo)
    except Exception as exc:
        return [_ledger_warning("manifest", "", "unknown", "manifest_read_failed", value=str(exc))]
    known_slugs = {entry.slug for entry in manifest.plans}
    # #46: also key by (normalized file, phase_alias) so an auto-import whose
    # SYNTHETIC slug differs from a committed planner entry (e.g. import slug
    # "v1-CORE" vs planner slug "phase-plan-v1-CORE") is not appended as a
    # duplicate when the same phase-plan file + phase alias is already present.
    # Dedup-by-slug-alone re-added a duplicate `imported` row (and, with it, a
    # punctuation-variant IF gate that the planner entry never contained).
    known_file_phase = {_manifest_file_phase_key(entry) for entry in manifest.plans}
    for entry in manifest.plans:
        if entry.type != "phase" or entry.status in {"orphaned", "completed", "failed"}:
            continue
        if not (repo / entry.file).exists():
            # #62: on a read-only path (status/handoff) the orphan detection is
            # still surfaced as a ledger warning, but the lifecycle transition is
            # NOT persisted — a read must never dirty plans/manifest.json.
            if not read_only:
                try:
                    update_lifecycle(
                        repo,
                        entry.slug,
                        "orphaned",
                        "phase-loop-reconcile",
                        {"reason": "manifest_plan_file_missing", "file": entry.file},
                    )
                except Exception as exc:
                    warnings.append(_ledger_warning("manifest", str(entry.phase_alias or ""), entry.status, "manifest_orphan_update_failed", value=str(exc)))
            warnings.append(_ledger_warning("manifest", str(entry.phase_alias or ""), "orphaned", "manifest_plan_file_missing", value=entry.file))
    try:
        imported = import_existing_phase_plans(repo)
    except Exception as exc:
        warnings.append(_ledger_warning("manifest", "", "unknown", "manifest_auto_import_scan_failed", value=str(exc)))
        return warnings
    for entry in imported.plans:
        if entry.slug in known_slugs:
            continue
        if _manifest_file_phase_key(entry) in known_file_phase:
            # #46: same phase-plan file + phase alias already represented by a
            # committed entry — do not append a second `imported` row.
            continue
        if entry.phase_alias and entry.phase_alias.upper() not in phases:
            continue
        if read_only:
            # #62: read-only status/reconcile must not persist auto-imports.
            # Track the entry in-memory so subsequent iterations dedup against
            # it exactly as the write path would, but skip the disk append.
            known_slugs.add(entry.slug)
            known_file_phase.add(_manifest_file_phase_key(entry))
            continue
        try:
            append_entry(repo, entry)
            known_slugs.add(entry.slug)
            known_file_phase.add(_manifest_file_phase_key(entry))
        except Exception as exc:
            warnings.append(_ledger_warning("manifest", str(entry.phase_alias or ""), "imported", "manifest_auto_import_failed", value=str(exc)))
        else:
            pass
    for phase in phases:
        _manifest_plan, conflict = manifest_plan_artifact(repo, phase, roadmap=roadmap)
        if conflict:
            warnings.append(
                _ledger_warning(
                    "manifest",
                    str(conflict.get("phase") or phase),
                    str(conflict.get("status") or "unknown"),
                    str(conflict.get("reason") or "manifest_warning"),
                    value=conflict,
                )
            )
    return warnings


def _blocker_precondition_cleared(repo: Path, blocker_class: str, *, blocker_summary: str = "") -> bool:
    """True when the current repo/git state no longer satisfies the cached
    blocker's precondition. The runner should drop the cached blocker in
    that case.

    NOTE: ``dirty_worktree_conflict`` is intentionally NOT covered here.
    The runner emits a richer ``repair_precondition_cleared`` state
    transition for that class on its own (see
    test_phase_loop_repair_skipped_when_blocker_cleared). Clearing it in
    reconcile would preempt that flow and drop the audit trail.

    Default-deny: human-action classes (admin_approval, missing_secret,
    product_decision_missing, account_or_billing_setup, ...) and
    branch_sync_conflict variants we cannot reliably detect (e.g.
    ``base_ref_unavailable`` from release_guard) stay cached.
    """
    if blocker_class == "branch_sync_conflict":
        # Narrow to the BranchGov default-branch refusal variant -- the
        # v37 case. Identified by the blocker_summary text emitted in
        # pipeline_adapter.branch_ops.refuse_default_branch_commit.
        # release_guard's "base_ref_unavailable" variant and other
        # branch_sync_conflict producers stay cached.
        summary = blocker_summary or ""
        if REFUSE_DEFAULT_BRANCH_COMMIT_PREFIX not in summary:
            return False
        try:
            current_branch = subprocess.check_output(
                ["git", "-C", str(repo), "branch", "--show-current"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except Exception:
            return False
        if not current_branch:
            return False
        remote_head = ""
        try:
            remote_head = subprocess.check_output(
                ["git", "-C", str(repo), "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except Exception:
            remote_head = ""
        default_branch = remote_head.removeprefix("origin/") if remote_head.startswith("origin/") else ""
        if not default_branch:
            try:
                ls_remote = subprocess.check_output(
                    ["git", "-C", str(repo), "ls-remote", "--symref", "origin", "HEAD"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                ls_remote = ""
            for line in ls_remote.splitlines():
                if line.startswith("ref: refs/heads/"):
                    ref = line.split("\t", 1)[0].removeprefix("ref: refs/heads/").strip()
                    if ref:
                        default_branch = ref
                        break
        if not default_branch:
            # When the default branch cannot be discovered, default-deny
            # rather than risk clearing a stale blocker that may still
            # apply.
            return False
        return current_branch != default_branch
    if blocker_class == "merge_conflict":
        # Cached blocker was "merge in progress with conflicts". If
        # `git status --porcelain` no longer reports unmerged paths
        # (UU/AA/DD/UD/DU/AU/UA), the precondition is cleared.
        try:
            out = subprocess.check_output(
                ["git", "-C", str(repo), "status", "--porcelain"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            return False
        unmerged_prefixes = ("UU", "AA", "DD", "UD", "DU", "AU", "UA")
        return not any(line.startswith(unmerged_prefixes) for line in out.splitlines())
    # Default-deny: dirty_worktree_conflict (handled by runner mechanism),
    # human-action blocker classes (admin_approval, missing_secret,
    # product_decision_missing, account_or_billing_setup,
    # operator_override_missing_reason, etc.) and any unknown class do
    # NOT self-clear from repo state alone.
    return False


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
        override = _lane_ir_override(repo, roadmap, phase, plan)
        remaining = tuple(diagnostic for diagnostic in lane_ir.diagnostics if diagnostic.kind not in override)
        if not remaining:
            return {}
        # #52: name the concrete failing diagnostic(s) and the plan file so the
        # repo-local repair can fix the plan instead of guessing. Previously the
        # summary was a generic "failed closed" string with no lane/kind/location,
        # forcing the operator to guess which lane tripped which contract rule.
        try:
            plan_rel: object = plan.relative_to(repo)
        except ValueError:
            plan_rel = plan
        detail = "; ".join(
            f"{d.kind}@{d.lane_id or 'plan'}"
            + (f" ({d.message})" if getattr(d, "message", None) else "")
            for d in remaining
        )
        return {
            "human_required": False,
            "blocker_class": "contract_bug",
            "blocker_summary": (
                f"Lane IR diagnostics failed closed for phase '{phase}' ({plan_rel}): "
                f"{detail}. Fix the named lane(s) in the phase plan, then re-run."
            ),
            "required_human_inputs": (),
            "lane_ir_diagnostics": tuple(diagnostic.to_json() for diagnostic in remaining),
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


def _lane_ir_override(repo: Path, roadmap: Path, phase: str, plan: Path) -> tuple[str, ...]:
    plan_path = plan.resolve()
    current_roadmap_sha = roadmap_sha256(roadmap)
    current_phase_sha = phase_sha256(roadmap, phase)
    override_kinds: tuple[str, ...] = ()
    for event in read_events(repo):
        if event.get("action") != "lane_ir_override":
            continue
        if event.get("roadmap_sha256") != current_roadmap_sha or event.get("phase_sha256") != current_phase_sha:
            continue
        if str(event.get("phase", "")).upper() != phase.upper():
            continue
        if not roadmap_paths_match(event.get("repo"), event.get("roadmap"), repo, roadmap)[0]:
            continue
        metadata = event.get("metadata")
        payload = metadata.get("runner.lane_ir_override_invoked") if isinstance(metadata, dict) else None
        if not isinstance(payload, dict):
            continue
        if not str(payload.get("operator_reason") or "").strip():
            continue
        event_plan = payload.get("plan_path")
        if event_plan:
            try:
                if Path(str(event_plan)).expanduser().resolve() != plan_path:
                    continue
            except OSError:
                continue
        kinds = payload.get("diagnostic_kinds_overridden")
        if not isinstance(kinds, list):
            continue
        override_kinds = tuple(str(kind) for kind in kinds if kind)
    return override_kinds


def _closeout_allow_unowned_attested(repo: Path, roadmap: Path, phase: str) -> bool:
    """BREAKGLASS SL-2: True when a non-stale ``closeout_allow_unowned`` operator
    attestation is recorded for this exact phase content. Scoping mirrors
    ``_lane_ir_override``: roadmap_sha256 + phase_sha256 (content-bound freshness) +
    phase + roadmap path + non-empty operator_reason. A stale attestation (content
    drifted since it was written) no longer matches and does NOT authorize a later
    closeout.
    """
    current_roadmap_sha = roadmap_sha256(roadmap)
    current_phase_sha = phase_sha256(roadmap, phase)
    plan = find_plan_artifact(repo, phase, roadmap=roadmap)
    plan_path = plan.resolve() if plan else None
    for event in read_events(repo):
        if event.get("action") != "closeout_allow_unowned":
            continue
        if event.get("roadmap_sha256") != current_roadmap_sha or event.get("phase_sha256") != current_phase_sha:
            continue
        if str(event.get("phase", "")).upper() != phase.upper():
            continue
        if not roadmap_paths_match(event.get("repo"), event.get("roadmap"), repo, roadmap)[0]:
            continue
        metadata = event.get("metadata")
        payload = metadata.get("runner.closeout_allow_unowned_invoked") if isinstance(metadata, dict) else None
        if not isinstance(payload, dict):
            continue
        if not str(payload.get("operator_reason") or "").strip():
            continue
        event_plan = payload.get("plan_path")
        if event_plan and plan_path is not None:
            try:
                if Path(str(event_plan)).expanduser().resolve() != plan_path:
                    continue
            except OSError:
                continue
        return True
    return False


def _clean_planned_artifact_supersedes_blocker(repo: Path, roadmap: Path, phase: str, warnings: list[dict] | tuple[dict, ...]) -> bool:
    if not any(warning.get("source") == "event" and warning.get("status") == "planned" for warning in warnings):
        return False
    if _dirty_except_plan_manifest(repo):
        return False
    plan = find_plan_artifact(repo, phase, roadmap=roadmap)
    if not plan:
        return False
    try:
        automation = parse_automation_status(plan.read_text(encoding="utf-8"))
    except OSError:
        return False
    return automation.get("automation_status") == "planned"


def _dirty_except_plan_manifest(repo: Path) -> bool:
    try:
        lines = subprocess.check_output(["git", "-C", str(repo), "status", "--short"], text=True).splitlines()
    except Exception:
        return False
    return any(line[3:] != "plans/manifest.json" for line in lines if len(line) > 3)


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
        if not roadmap_paths_match(event.get("repo"), event.get("roadmap"), repo, roadmap)[0]:
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
        if not roadmap_paths_match(event.get("repo"), event.get("roadmap"), repo, roadmap)[0]:
            continue
        # BREAKGLASS SL-2: operator attestation events are not closeout/terminal events;
        # they must not shadow the verified-dirty-closeout recovery event as latest_event.
        if event.get("action") == "closeout_allow_unowned":
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
    unowned = tuple(dirty.get("unowned_dirty_paths") or ())
    if unowned:
        # BREAKGLASS SL-2 (reconcile parity): a recorded operator attestation lifts the
        # unowned-remainder bail — EXCEPT secrets, which are never break-glassable. The
        # repo is already clean here (the closeout committed; the top-of-function _dirty
        # guard is the real secrets backstop since SL-1 never commits a secret, leaving
        # the worktree dirty); the explicit secrets check is defense-in-depth.
        if not _closeout_allow_unowned_attested(repo, roadmap, phase):
            return None
        if any(classify_unowned_path(p).sensitivity_class == "secrets" for p in unowned):
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


def _amendment_drift_fields(
    status: str,
    reason: str,
    entry_phase_sha: str | None,
    current_phase_sha: str | None,
) -> dict[str, object]:
    """#85: distinguish "a roadmap amendment changed a COMPLETED phase's hashes"
    from "this phase was genuinely never planned".

    When a phase's own roadmap block is amended in-flight its ``phase_sha256``
    drifts, and — by the completion-invalidation invariant that
    ``test_phase_block_edit_invalidates_complete_phase`` locks — the stored
    completion is (correctly) no longer trusted, so the phase falls back to
    ``unplanned``. That reclassification is right, but silent: the operator can't
    tell it apart from a phase that never had any state. Stamp the
    provenance-mismatch warning for a drifted terminal completion with a
    repairable ``gold_record_amendment`` marker (the same vocabulary
    ``invalidate_stale_downstream_plans`` uses for downstream plans) that names
    the drifted vs current hash, so ``status`` can surface a repairable signal.

    A genuinely-unplanned phase carries no ``complete``/``executed`` state or
    event here, so it never receives the marker — that asymmetry is exactly the
    "distinguish" the issue asks for."""
    if reason != "phase_mismatch" or status not in {"complete", "executed"}:
        return {}
    return {
        # `diagnostic_class`, NOT `blocker_class`: this is a warning-only marker on a
        # ledger warning, never a snapshot blocker — nothing elevates it into
        # `snapshot.blocker_class`/`human_required`. Using the real blocker key here
        # would mislead consumers/tools that scan any non-null `blocker_class` (CR:
        # codex/grok/agy). The `gold_record_amendment` VALUE reuses the same
        # vocabulary `invalidate_stale_downstream_plans` stamps for downstream plans.
        "diagnostic_class": "gold_record_amendment",
        "repairable": True,
        "amendment_drift": {
            "phase_status": status,
            "entry_phase_sha256": entry_phase_sha,
            "current_phase_sha256": current_phase_sha,
        },
        "repair_hint": (
            # A phase-hash mismatch is amendment-SHAPED but not proof of an amendment
            # (it could also be a tampered/hand-edited ledger); frame it as drift, not
            # a confirmed amendment (CR: codex).
            "Provenance drift (amendment-shaped): this completed phase's section hash "
            "no longer matches the roadmap. If a roadmap amendment changed it, restore "
            "the amended completed-phase wording or re-attest the completion against the "
            "amended roadmap, then rerun reconcile."
        ),
    }


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


def _event_content_signature(event: dict) -> tuple[object, ...]:
    """Stable content signature drawn from the executor's terminal_summary.

    DEF-4: the runner emits two events with identical (timestamp, phase,
    action, status, automation_status, blocker_class) keys during a single
    blocked-execute call — one carrying an empty terminal_summary, the other
    carrying the executor's dirty_paths / phase_owned_dirty_paths /
    produced_if_gates. Without a content signature they share a dedup
    identity; the first event wins and the rich one is silently dropped,
    leaving the snapshot's dirty_paths empty and the closeout auto-classifier
    unable to fire.

    Including the content signature in the dedup identity lets both events
    survive reconcile. The reducer's existing overwrite-on-non-empty +
    no-pop-on-blocked semantics then naturally surface the rich event's data.

    True byte-identical duplicates still collapse: their content signatures
    match exactly. The regression guard for that is intentional — replayed
    ledgers (e.g. after restoring `events.jsonl.bak-*`) must not double-count.
    """
    metadata = event.get("metadata")
    if not isinstance(metadata, dict):
        return ()
    summary = metadata.get("terminal_summary")
    if not isinstance(summary, dict):
        return ()
    return (
        tuple(sorted(str(p) for p in (summary.get("dirty_paths") or ()))),
        tuple(sorted(str(p) for p in (summary.get("phase_owned_dirty_paths") or ()))),
        tuple(sorted(str(g) for g in (summary.get("produced_if_gates") or ()))),
        _optional_text(summary.get("terminal_status")),
        _optional_text(summary.get("verification_status")),
    )


def _event_dedup_identity(event: dict, dedup_key: tuple[object, ...]) -> tuple[object, ...]:
    return (
        *dedup_key,
        _optional_text(event.get("roadmap_sha256")),
        _optional_text(event.get("phase_sha256")),
        event.get("schema_version"),
        _event_content_signature(event),
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
        "legacy_executor_closeout_action_normalized",
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


def _normalize_legacy_executor_closeout_action(event: dict) -> tuple[dict, bool]:
    metadata = event.get("metadata")
    if event.get("action") != "run" or not isinstance(metadata, dict):
        return event, False
    if not isinstance(metadata.get("executor_closeout_event"), dict):
        return event, False
    normalized = dict(event)
    normalized["action"] = "executor.closeout"
    return normalized, True


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
    # Fallback: executor's terminal_summary carries dirty_paths directly when
    # the runner hasn't (yet) wrapped them into the completion_dirty_worktree /
    # plan_dirty_worktree / incomplete_execute_dirty_worktree shapes. Without
    # this read, the closeout fallback at runner.py:6207 sees empty
    # snapshot.dirty_paths and refuses, even though the executor accurately
    # reported its phase-owned output. Skip dry-run summaries so the
    # event_only_status warning path stays intact.
    terminal_summary = metadata.get("terminal_summary")
    # Only fall back when the executor actually reported dirty work AND it's
    # not a dry-run. Empty-dirty terminal_summaries from closeout-refused
    # follow-up events would otherwise overwrite the prior executor report.
    if (
        isinstance(terminal_summary, dict)
        and terminal_summary.get("dirty_paths")
        and terminal_summary.get("terminal_status") != "dry_run"
        and not metadata.get("dry_run_only")
    ):
        return {
            "dirty_paths": list(terminal_summary.get("dirty_paths", ())),
            "phase_owned_dirty_paths": list(terminal_summary.get("phase_owned_dirty_paths", ())),
            "previous_phase_owned_paths": list(terminal_summary.get("previous_phase_owned_paths", ())),
            "unowned_dirty_paths": list(terminal_summary.get("unowned_dirty_paths", ())),
            "pre_existing_dirty_paths": list(terminal_summary.get("pre_existing_dirty_paths", ())),
            "phase_owned_dirty": bool(terminal_summary.get("phase_owned_dirty", False)),
            "terminal_status": terminal_summary.get("terminal_status"),
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
    # phase_reopen is explicit operator intent to re-dispatch from a stuck
    # status (typed event with --reason); always clears any prior blocker.
    if event.get("action") == "phase_reopen" and event.get("status") == "planned":
        return True
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
    if event.get("action") == "phase_reopen" and status == "planned":
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
