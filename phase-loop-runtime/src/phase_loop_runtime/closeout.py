from __future__ import annotations

import hashlib
import os
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Mapping

from .closeout_validation import verification_enforcement_mode
from .closeout_validators import (
    CloseoutContext,
    apply_review_findings,
    run_closeout_validators,
)
from .models import (
    PIPELINE_CLOSEOUT_OUTCOMES,
    PIPELINE_CLOSEOUT_SCHEMA,
    PIPELINE_PROTECTED_SOURCE_CATEGORIES,
    PIPELINE_PROTECTED_SOURCE_LEGACY_ROLES,
    PIPELINE_PROTECTED_SOURCE_ROLES,
    SPEC_DELTA_CLOSEOUT_SCHEMA,
    SPEC_DELTA_DECISIONS,
    DirtyPathClassification,
    PhaseLoopArtifacts,
    PhaseLoopAutomation,
    PhaseLoopBlocker,
    PhaseLoopCloseout,
    PhaseLoopSourceBundle,
    SpecDeltaCloseout,
    PhaseLoopVerification,
    PhasePlanLane,
    PhaseSourceBundle,
    PipelineMetadataDiagnostic,
    PipelinePlanMetadata,
    SourceTruthImpact,
    WorkUnitCloseout,
)
from .redaction import build_source_truth_impact, metadata_redaction_diagnostic
from .verification_evidence import ARTIFACT_NAME, validate_verification_artifact


def reduce_lane_dirty_paths(
    dirty_paths: tuple[str, ...] | list[str],
    lanes: tuple[PhasePlanLane, ...] | list[PhasePlanLane],
    *,
    active_lane_id: str | None = None,
    pre_existing_paths: tuple[str, ...] | list[str] = (),
    reducer_paths: tuple[str, ...] | list[str] = (),
) -> tuple[DirtyPathClassification, ...]:
    pre_existing = set(pre_existing_paths)
    reducers = set(reducer_paths)
    reduced: list[DirtyPathClassification] = []
    for path in dirty_paths:
        if path in pre_existing:
            reduced.append(DirtyPathClassification(path=path, classification="pre_existing"))
            continue
        if path in reducers:
            reduced.append(DirtyPathClassification(path=path, classification="reducer_owned"))
            continue
        owner = _owning_lane(path, lanes)
        if owner is None:
            reduced.append(DirtyPathClassification(path=path, classification="unowned"))
        elif active_lane_id is None or owner.lane_id == active_lane_id:
            reduced.append(DirtyPathClassification(path=path, classification="lane_owned", lane_id=owner.lane_id))
        else:
            reduced.append(DirtyPathClassification(path=path, classification="peer_owned", lane_id=owner.lane_id))
    return tuple(reduced)


def _owning_lane(path: str, lanes: tuple[PhasePlanLane, ...] | list[PhasePlanLane]) -> PhasePlanLane | None:
    for lane in lanes:
        if any(_path_matches(path, pattern) for pattern in lane.owned_files):
            return lane
    return None


def _path_matches(path: str, pattern: str) -> bool:
    if path == pattern:
        return True
    if any(token in pattern for token in ("*", "?", "[")):
        return fnmatchcase(path, pattern)
    return path.startswith(pattern.rstrip("/") + "/")


def build_phase_loop_closeout(
    *,
    phase_alias: str,
    plan_path: str | Path,
    source_bundle: PhaseSourceBundle | None = None,
    plan_metadata: PipelinePlanMetadata | None = None,
    pipeline_diagnostic: PipelineMetadataDiagnostic | None = None,
    terminal_summary: Mapping[str, Any] | None = None,
    automation: Mapping[str, Any] | None = None,
    blocker: Mapping[str, Any] | None = None,
    access_attempts: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]] = (),
    changed_paths: tuple[str, ...] | list[str] = (),
    artifact_paths: Mapping[str, str] | None = None,
    evidence_refs: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]] = (),
    work_unit_closeout: WorkUnitCloseout | Mapping[str, Any] | None = None,
    spec_delta_closeout: Mapping[str, Any] | SpecDeltaCloseout | None = None,
    run_mode: str = "autonomous",
) -> dict[str, Any]:
    terminal = dict(terminal_summary or {})
    normalized_automation = _automation_fields(dict(automation or {}))
    blocker_data = dict(blocker or terminal.get("terminal_blocker") or {})
    verification_results: list[dict[str, Any]] = []
    agent_reported_verification_status = str(
        terminal.get("verification_status") or normalized_automation.get("verification_status") or "unknown"
    )
    evidence_update = _apply_verification_evidence_gate(
        phase_alias=phase_alias,
        plan_path=Path(plan_path),
        terminal=terminal,
        automation=normalized_automation,
        blocker=blocker_data,
    )
    if evidence_update:
        terminal = evidence_update["terminal"]
        normalized_automation = evidence_update["automation"]
        blocker_data = evidence_update["blocker"]
        verification_results = evidence_update["results"]

    # Pluggable review gates (rigor-v1 P1). With zero validators registered this
    # is a no-op; gates default to `warn` (record + continue) and never set
    # human_required. See closeout_validators for the severity model.
    review_findings = run_closeout_validators(
        CloseoutContext(
            phase_alias=phase_alias,
            plan_path=str(plan_path),
            terminal=terminal,
            automation=normalized_automation,
            blocker=blocker_data,
            changed_paths=tuple(
                changed_paths
                or terminal.get("dirty_paths")
                or terminal.get("phase_owned_dirty_paths")
                or terminal.get("previous_phase_owned_paths")
                or ()
            ),
            run_mode=run_mode,
        )
    )
    if review_findings:
        review_update = apply_review_findings(
            findings=review_findings,
            terminal=terminal,
            automation=normalized_automation,
            blocker=blocker_data,
        )
        terminal = review_update["terminal"]
        normalized_automation = review_update["automation"]
        blocker_data = review_update["blocker"]
        verification_results = list(verification_results) + review_update["results"]
    metadata = plan_metadata or (pipeline_diagnostic.metadata if pipeline_diagnostic else None)

    source_bundle_path = source_bundle.path if source_bundle else (metadata.source_bundle if metadata else None)
    if source_bundle is not None:
        source_bundle_sha256 = source_bundle.sha256
    elif pipeline_diagnostic is not None:
        source_bundle_sha256 = pipeline_diagnostic.actual_sha256 or pipeline_diagnostic.expected_sha256
    else:
        source_bundle_sha256 = metadata.source_bundle_sha256 if metadata else None
    pipeline_phase_id = source_bundle.phase_id if source_bundle else (metadata.pipeline_phase_id if metadata else None)
    plan_path_text = str(plan_path)

    outcome = _closeout_outcome(
        terminal_summary=terminal,
        automation=normalized_automation,
        blocker=blocker_data,
        pipeline_diagnostic=pipeline_diagnostic,
        work_unit_closeout=work_unit_closeout,
    )

    # Build nested model
    auto = PhaseLoopAutomation(
        status=str(normalized_automation.get("status") or "unknown"),
        next_skill=normalized_automation.get("next_skill"),
        next_command=normalized_automation.get("next_command"),
        next_model_hint=normalized_automation.get("next_model_hint"),
        next_effort_hint=normalized_automation.get("next_effort_hint"),
        human_required=bool(normalized_automation.get("human_required", False)),
        blocker_class=normalized_automation.get("blocker_class"),
        blocker_summary=normalized_automation.get("blocker_summary"),
        required_human_inputs=tuple(normalized_automation.get("required_human_inputs") or ()),
        verification_status=str(normalized_automation.get("verification_status") or "not_run"),
        artifact=normalized_automation.get("artifact"),
        artifact_state=normalized_automation.get("artifact_state"),
    )

    arts = PhaseLoopArtifacts(
        plan_path=plan_path_text,
        plan_sha256=_file_sha256(plan_path),
        artifact_paths=dict(artifact_paths or terminal.get("artifact_paths") or {}),
        changed_paths=tuple(_stable_paths(
            changed_paths
            or terminal.get("dirty_paths")
            or terminal.get("phase_owned_dirty_paths")
            or terminal.get("previous_phase_owned_paths")
            or ()
        )),
        evidence_refs=tuple(_evidence_refs(evidence_refs, terminal, work_unit_closeout)),
    )
    source_truth_impact = build_source_truth_impact(
        arts.changed_paths,
        _protected_source_roles(source_bundle),
    )

    verification = PhaseLoopVerification(
        status=str(terminal.get("verification_status") or normalized_automation.get("verification_status") or "unknown"),
        commands=tuple(_verification_commands(terminal)),
        results=tuple(verification_results),
        agent_reported_verification_status=agent_reported_verification_status,
    )

    blkr = PhaseLoopBlocker(
        human_required=bool(blocker_data.get("human_required", normalized_automation.get("human_required", False))),
        blocker_class=blocker_data.get("blocker_class") or normalized_automation.get("blocker_class"),
        blocker_summary=blocker_data.get("blocker_summary") or normalized_automation.get("blocker_summary"),
        required_human_inputs=tuple(blocker_data.get("required_human_inputs") or normalized_automation.get("required_human_inputs") or ()),
        access_attempts=tuple(access_attempts or blocker_data.get("access_attempts") or ()),
    )

    sb = PhaseLoopSourceBundle(
        path=source_bundle_path,
        sha256=source_bundle_sha256,
        phase_id=pipeline_phase_id,
        pipeline_mode=source_bundle.pipeline_mode if source_bundle else ((metadata.pipeline_mode if metadata else None) or "standalone"),
        protected_sources=tuple(source.to_json() for source in source_bundle.protected_sources) if source_bundle else (),
    )

    closeout = PhaseLoopCloseout(
        phase=phase_alias,
        terminal_status=outcome,
        automation=auto,
        artifacts=arts,
        verification=verification,
        blocker=blkr,
        source_bundle=sb,
        source_truth_impact=source_truth_impact,
        spec_delta_closeout=_spec_delta_closeout(spec_delta_closeout, terminal, normalized_automation),
    )

    payload = closeout.to_json()

    if source_bundle is not None:
        payload["source_bundle"]["artifact_target_root"] = source_bundle.artifact_target_root
    if pipeline_diagnostic is not None:
        payload["pipeline_execution_preflight"] = {
            "status": "blocked",
            "diagnostic": pipeline_diagnostic.to_json(),
        }
    if work_unit_closeout is not None:
        payload["work_unit"] = _work_unit_fields(work_unit_closeout)
        payload["lane"] = _lane_closeout_fields(work_unit_closeout)
    return _clean(payload)


def _spec_delta_closeout(
    explicit: Mapping[str, Any] | SpecDeltaCloseout | None,
    terminal: Mapping[str, Any],
    automation: Mapping[str, Any],
) -> SpecDeltaCloseout | None:
    value = explicit or terminal.get("spec_delta_closeout") or automation.get("spec_delta_closeout")
    if value is None:
        return None
    if isinstance(value, SpecDeltaCloseout):
        return value
    data = dict(value)
    schema = data.get("schema") or SPEC_DELTA_CLOSEOUT_SCHEMA
    if schema != SPEC_DELTA_CLOSEOUT_SCHEMA:
        raise ValueError(f"invalid spec delta closeout schema: {schema}")
    decision = str(data.get("decision") or "")
    if decision not in SPEC_DELTA_DECISIONS:
        raise ValueError(f"invalid spec delta decision: {decision}")
    return SpecDeltaCloseout(
        schema=schema,
        decision=decision,
        target_surfaces=tuple(str(item) for item in data.get("target_surfaces") or ()),
        evidence_paths=tuple(str(item) for item in data.get("evidence_paths") or ()),
        redaction_posture=str(data.get("redaction_posture") or "metadata_only"),
        blocker_class=data.get("blocker_class"),
    )


def _apply_verification_evidence_gate(
    *,
    phase_alias: str,
    plan_path: Path,
    terminal: dict[str, Any],
    automation: dict[str, Any],
    blocker: dict[str, Any],
) -> dict[str, Any] | None:
    reported = str(terminal.get("verification_status") or automation.get("verification_status") or "")
    if reported != "passed":
        return None
    artifact_path = _verification_artifact_path(terminal)
    if artifact_path is None:
        if _verification_evidence_required(phase_alias, plan_path):
            validation_payload = {
                "ok": False,
                "code": "missing_verification_artifact",
                "artifact_path": None,
                "log_path": None,
                "exit_summary": {},
                "findings": ["passed closeout requires IF-0-VC-1 verification evidence"],
                "enforcement": verification_enforcement_mode(os.environ),
            }
            return _verification_evidence_block_or_warn(
                validation_payload=validation_payload,
                terminal=terminal,
                automation=automation,
                blocker=blocker,
            )
        return None
    validation = validate_verification_artifact(artifact_path)
    validation_payload = validation.to_json()
    mode = verification_enforcement_mode(os.environ)
    validation_payload["enforcement"] = mode
    return _verification_evidence_block_or_warn(
        validation_payload=validation_payload,
        terminal=terminal,
        automation=automation,
        blocker=blocker,
    )


def _verification_evidence_block_or_warn(
    *,
    validation_payload: dict[str, Any],
    terminal: dict[str, Any],
    automation: dict[str, Any],
    blocker: dict[str, Any],
) -> dict[str, Any]:
    updated_terminal = dict(terminal)
    updated_automation = dict(automation)
    updated_blocker = dict(blocker)
    if validation_payload.get("ok"):
        return {
            "terminal": updated_terminal,
            "automation": updated_automation,
            "blocker": updated_blocker,
            "results": [validation_payload],
        }
    mode = str(validation_payload.get("enforcement") or verification_enforcement_mode(os.environ))
    if mode == "warn":
        validation_payload["warning"] = "verification evidence failed validation under PHASE_LOOP_VERIFY_ENFORCE=warn"
        return {
            "terminal": updated_terminal,
            "automation": updated_automation,
            "blocker": updated_blocker,
            "results": [validation_payload],
        }
    updated_terminal["terminal_status"] = "blocked"
    updated_terminal["verification_status"] = "blocked"
    updated_automation["status"] = "blocked"
    updated_automation["verification_status"] = "blocked"
    updated_automation["blocker_class"] = "verification_evidence_missing"
    updated_automation["blocker_summary"] = f"Verification evidence invalid: {validation_payload.get('code')}"
    updated_automation["human_required"] = False
    updated_blocker.update(
        {
            "human_required": False,
            "blocker_class": "verification_evidence_missing",
            "blocker_summary": f"Verification evidence invalid: {validation_payload.get('code')}",
            "required_human_inputs": (),
        }
    )
    return {
        "terminal": updated_terminal,
        "automation": updated_automation,
        "blocker": updated_blocker,
        "results": [validation_payload],
    }


def _verification_evidence_required(phase_alias: str, plan_path: Path) -> bool:
    if phase_alias.upper() == "RG":
        return True
    try:
        text = plan_path.read_text(encoding="utf-8")
    except OSError:
        return False
    return "IF-0-RG-1" in text or "--verification-log" in text


def _verification_artifact_path(terminal: Mapping[str, Any]) -> Path | None:
    artifact_paths = terminal.get("artifact_paths")
    if not isinstance(artifact_paths, Mapping):
        return None
    explicit = artifact_paths.get("verification") or artifact_paths.get("verification_artifact") or artifact_paths.get("verification_log")
    if explicit:
        candidate = Path(str(explicit))
        if candidate.name == "verification.log":
            return candidate.parent / ARTIFACT_NAME
        return candidate
    root = artifact_paths.get("root")
    if root:
        return Path(str(root)) / ARTIFACT_NAME
    return None


def phase_loop_closeout_diagnostic(payload: Mapping[str, Any] | None) -> dict[str, str] | None:
    if not isinstance(payload, Mapping):
        return {"kind": "malformed_closeout", "message": "closeout payload must be an object"}
    if payload.get("schema") != PIPELINE_CLOSEOUT_SCHEMA:
        return {"kind": "malformed_closeout", "message": f"closeout schema must be {PIPELINE_CLOSEOUT_SCHEMA}"}
    deprecated_root_fields = (
        "status",
        "next_skill",
        "next_command",
        "verification_status",
        "artifact",
        "artifact_state",
    )
    for field in deprecated_root_fields:
        if field in payload:
            return {"kind": "malformed_closeout", "message": f"native v1 closeout contains deprecated root field {field}"}

    # Single source of truth: the export-schema generator owns the canonical
    # required-nested-field set, so the schema artifact and this validator can
    # never drift apart (SCHEMA SL-1 / CR #4).
    from .schema_export import required_closeout_fields

    for field in required_closeout_fields():
        if field not in payload or payload.get(field) is None:
            return {"kind": "malformed_closeout", "message": f"closeout payload is missing nested field {field}"}

    if payload.get("terminal_status") not in PIPELINE_CLOSEOUT_OUTCOMES:
        return {"kind": "malformed_closeout", "message": f"invalid terminal status: {payload.get('terminal_status')}"}
    if payload.get("outcome") is not None and payload.get("outcome") not in PIPELINE_CLOSEOUT_OUTCOMES:
        return {"kind": "malformed_closeout", "message": f"invalid closeout outcome: {payload.get('outcome')}"}
    if not isinstance(payload.get("automation"), Mapping):
        return {"kind": "malformed_closeout", "message": "closeout automation must be an object"}
    artifacts = payload.get("artifacts")
    verification = payload.get("verification")
    blocker = payload.get("blocker")
    source_bundle = payload.get("source_bundle")
    if not isinstance(artifacts, Mapping):
        return {"kind": "malformed_closeout", "message": "closeout artifacts must be an object"}
    if not isinstance(verification, Mapping):
        return {"kind": "malformed_closeout", "message": "closeout verification must be an object"}
    if not isinstance(blocker, Mapping):
        return {"kind": "malformed_closeout", "message": "closeout blocker must be an object"}
    if not isinstance(source_bundle, Mapping):
        return {"kind": "malformed_closeout", "message": "closeout source_bundle must be an object"}
    if source_bundle.get("pipeline_mode") not in ("standalone", "pipeline_optional", "pipeline_required"):
        return {"kind": "malformed_closeout", "message": "closeout source_bundle has invalid pipeline_mode"}
    protected_sources = source_bundle.get("protected_sources")
    if protected_sources is not None:
        if not isinstance(protected_sources, list):
            return {"kind": "malformed_closeout", "message": "closeout source_bundle.protected_sources must be a list"}
        for item in protected_sources:
            if not isinstance(item, Mapping):
                return {"kind": "malformed_closeout", "message": "closeout source_bundle.protected_sources entries must be objects"}
            category = item.get("category")
            role = item.get("role")
            if category not in PIPELINE_PROTECTED_SOURCE_CATEGORIES:
                return {"kind": "malformed_closeout", "message": "closeout source_bundle protected source has invalid category"}
            if role is not None and role not in PIPELINE_PROTECTED_SOURCE_ROLES and role not in PIPELINE_PROTECTED_SOURCE_LEGACY_ROLES:
                return {"kind": "malformed_closeout", "message": "closeout source_bundle protected source has invalid role"}
    if source_bundle.get("pipeline_mode") == "pipeline_required":
        for field in ("path", "phase_id"):
            if not source_bundle.get(field):
                return {"kind": "malformed_closeout", "message": f"pipeline_required closeout missing source_bundle.{field}"}
        if not source_bundle.get("sha256"):
            return {"kind": "missing_source_bundle_sha256", "message": "pipeline_required closeout missing source_bundle.sha256"}
    if not artifacts.get("plan_path") or not artifacts.get("plan_sha256"):
        return {"kind": "malformed_closeout", "message": "closeout artifacts must include plan_path and plan_sha256"}
    if not verification.get("status"):
        return {"kind": "malformed_closeout", "message": "closeout verification must include status"}
    impact = payload.get("source_truth_impact")
    if not isinstance(impact, Mapping):
        return {"kind": "malformed_closeout", "message": "closeout source_truth_impact must be an object"}
    impact_diagnostic = _source_truth_impact_diagnostic(impact)
    if impact_diagnostic is not None:
        return impact_diagnostic
    redaction = metadata_redaction_diagnostic(payload)
    if redaction is not None:
        return redaction
    return None


def _automation_fields(raw: dict[str, Any]) -> dict[str, Any]:
    if not raw:
        return {}
    mapping = {
        "automation_status": "status",
        "automation_next_skill": "next_skill",
        "automation_next_command": "next_command",
        "automation_human_required": "human_required",
        "automation_blocker_class": "blocker_class",
        "automation_blocker_summary": "blocker_summary",
        "automation_required_human_inputs": "required_human_inputs",
        "automation_verification_status": "verification_status",
    }
    normalized = {mapping.get(key, key): value for key, value in raw.items()}
    if isinstance(normalized.get("human_required"), str):
        normalized["human_required"] = normalized["human_required"].lower() == "true"
    if normalized.get("blocker_class") in ("", "none"):
        normalized["blocker_class"] = None
    if normalized.get("blocker_summary") == "none":
        normalized["blocker_summary"] = None
    return normalized


def _blocker_fields(blocker: dict[str, Any], automation: dict[str, Any]) -> dict[str, Any]:
    return _clean(
        {
            "human_required": bool(blocker.get("human_required", automation.get("human_required", False))),
            "blocker_class": blocker.get("blocker_class") or automation.get("blocker_class"),
            "blocker_summary": blocker.get("blocker_summary") or automation.get("blocker_summary"),
            "required_human_inputs": blocker.get("required_human_inputs") or automation.get("required_human_inputs") or (),
        }
    )


def _closeout_outcome(
    *,
    terminal_summary: dict[str, Any],
    automation: dict[str, Any],
    blocker: dict[str, Any],
    pipeline_diagnostic: PipelineMetadataDiagnostic | None,
    work_unit_closeout: WorkUnitCloseout | Mapping[str, Any] | None,
) -> str:
    if bool(blocker.get("human_required")) or bool(automation.get("human_required")):
        return "human_required"
    if pipeline_diagnostic is not None or _is_stale_blocker(blocker):
        return "stale_input"
    verification = str(terminal_summary.get("verification_status") or automation.get("verification_status") or "")
    blocker_class = str(blocker.get("blocker_class") or automation.get("blocker_class") or "")
    work_status = _work_unit_status(work_unit_closeout)
    if verification == "failed" or blocker_class == "repeated_verification_failure" or work_status == "blocked":
        return "failed_verification"
    if terminal_summary.get("terminal_status") == "complete" and verification == "passed" and not blocker:
        return "complete"
    return "blocked"


def _is_stale_blocker(blocker: dict[str, Any]) -> bool:
    summary = str(blocker.get("blocker_summary") or "")
    return "Pipeline execution freshness validation failed" in summary or "source_bundle" in summary


def _work_unit_status(work_unit_closeout: WorkUnitCloseout | Mapping[str, Any] | None) -> str | None:
    if work_unit_closeout is None:
        return None
    if isinstance(work_unit_closeout, WorkUnitCloseout):
        return work_unit_closeout.status
    return str(work_unit_closeout.get("status")) if work_unit_closeout.get("status") else None


def _work_unit_fields(work_unit_closeout: WorkUnitCloseout | Mapping[str, Any]) -> dict[str, Any]:
    data = work_unit_closeout.to_json() if isinstance(work_unit_closeout, WorkUnitCloseout) else dict(work_unit_closeout)
    identity = data.get("identity") if isinstance(data.get("identity"), dict) else {}
    return _clean(
        {
            "work_unit_id": identity.get("work_unit_id"),
            "phase": identity.get("phase"),
            "kind": identity.get("kind"),
            "lane_id": identity.get("lane_id"),
            "status": data.get("status"),
        }
    )


def _lane_closeout_fields(work_unit_closeout: WorkUnitCloseout | Mapping[str, Any]) -> dict[str, Any]:
    data = work_unit_closeout.to_json() if isinstance(work_unit_closeout, WorkUnitCloseout) else dict(work_unit_closeout)
    identity = data.get("identity") if isinstance(data.get("identity"), dict) else {}
    terminal_summary = data.get("terminal_summary") if isinstance(data.get("terminal_summary"), Mapping) else {}
    closeout_summary = data.get("closeout_summary") if isinstance(data.get("closeout_summary"), Mapping) else {}
    assignment = closeout_summary.get("harness_lane_assignment") if isinstance(closeout_summary.get("harness_lane_assignment"), Mapping) else {}
    worktree_assignment = assignment.get("worktree_assignment") if isinstance(assignment.get("worktree_assignment"), Mapping) else {}
    return _clean(
        {
            "lane_id": identity.get("lane_id"),
            "wave_id": data.get("wave_id") or terminal_summary.get("wave_id"),
            "worktree_path": data.get("worktree_path") or terminal_summary.get("worktree_path"),
            "worktree_isolation_mode": data.get("worktree_isolation_mode") or terminal_summary.get("worktree_isolation_mode") or worktree_assignment.get("isolation_mode"),
            "base_sha": data.get("base_sha") or terminal_summary.get("base_sha") or worktree_assignment.get("base_sha"),
            "harness_route": data.get("harness_route") or terminal_summary.get("harness_route") or assignment.get("harness_route"),
            "work_unit_kind": identity.get("kind"),
            "model": data.get("model") or terminal_summary.get("model") or assignment.get("model"),
            "effort": data.get("effort") or terminal_summary.get("effort") or assignment.get("effort"),
            "policy_source": data.get("policy_source") or terminal_summary.get("policy_source") or _policy_source(assignment),
            "fallback_reason": data.get("fallback_reason") or terminal_summary.get("fallback_reason") or assignment.get("fallback_reason"),
            "verification_status": data.get("verification_status") or terminal_summary.get("verification_status"),
            "changed_paths": data.get("changed_paths") or terminal_summary.get("changed_paths"),
            "evidence_refs": data.get("evidence_refs") or terminal_summary.get("evidence_refs"),
        }
    )


def _policy_source(assignment: Mapping[str, Any]) -> str | None:
    policy = assignment.get("execution_policy") if isinstance(assignment.get("execution_policy"), Mapping) else {}
    return policy.get("execution_policy_source") or policy.get("source")


def _file_sha256(path: str | Path) -> str:
    candidate = Path(path)
    try:
        return hashlib.sha256(candidate.read_bytes()).hexdigest()
    except OSError:
        return hashlib.sha256(str(path).encode("utf-8")).hexdigest()


def _stable_paths(paths: tuple[str, ...] | list[str] | Any) -> list[str]:
    if not isinstance(paths, (tuple, list)):
        return []
    return sorted(dict.fromkeys(str(path) for path in paths))


def _verification_commands(terminal: Mapping[str, Any]) -> list[str]:
    commands = terminal.get("verification_commands") or ()
    if not isinstance(commands, (tuple, list)):
        return []
    reduced: list[str] = []
    for command in commands:
        if isinstance(command, Mapping):
            value = command.get("command")
        else:
            value = command
        if value:
            reduced.append(str(value))
    return reduced


def _evidence_refs(
    explicit_refs: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
    terminal: Mapping[str, Any],
    work_unit_closeout: WorkUnitCloseout | Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for source in (explicit_refs, terminal.get("evidence_refs") or (), _work_unit_evidence_refs(work_unit_closeout)):
        if not isinstance(source, (tuple, list)):
            continue
        for item in source:
            if isinstance(item, Mapping):
                refs.append(dict(item))
    return refs


def _work_unit_evidence_refs(work_unit_closeout: WorkUnitCloseout | Mapping[str, Any] | None) -> tuple[dict[str, Any], ...]:
    if work_unit_closeout is None:
        return ()
    data = work_unit_closeout.to_json() if isinstance(work_unit_closeout, WorkUnitCloseout) else dict(work_unit_closeout)
    refs = data.get("evidence_refs")
    if not isinstance(refs, (tuple, list)):
        return ()
    return tuple(dict(ref) for ref in refs if isinstance(ref, Mapping))


def _source_truth_impact_diagnostic(impact: Mapping[str, Any]) -> dict[str, str] | None:
    try:
        SourceTruthImpact(
            changed_path_boundaries=tuple(
                dict(boundary)
                for boundary in impact.get("changed_path_boundaries", ())
                if isinstance(boundary, Mapping)
            ),
            canonical_refresh_recommended=bool(impact.get("canonical_refresh_recommended", False)),
            canonical_refresh_reason_codes=tuple(str(reason) for reason in impact.get("canonical_refresh_reason_codes", ())),
            redaction_posture=str(impact.get("redaction_posture") or "metadata_only"),
        )
    except (TypeError, ValueError) as exc:
        return {"kind": "malformed_closeout", "message": f"invalid source_truth_impact: {exc}"}
    return None


def _protected_source_roles(source_bundle: PhaseSourceBundle | None) -> dict[str, str]:
    if source_bundle is None:
        return {}
    roles: dict[str, str] = {}
    for source in source_bundle.protected_sources:
        if source.role:
            role = _classification_role(source.role)
            roles[source.path] = role
            roles[source.path.lower()] = role
    return roles


def _classification_role(role: str) -> str:
    if role in {"legacy_specs_bundle", "adapter_configured_intake_root"}:
        return "unmanaged_spec_input"
    return role


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items() if item is not None and item != ()}
    if isinstance(value, tuple):
        return [_clean(item) for item in value]
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value
