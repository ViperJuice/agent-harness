from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .discovery import manifest_plan_artifact, roadmap_fingerprint, roadmap_repo_relative_path
from .injection import build_lane_prompt_bundle, build_prompt_bundle
from .models import DelegationRequest, HarnessLaneAssignment, ParentChildRunMetadata, PhaseSourceBundle, PromptBundle


def build_prompt(
    action: str,
    roadmap: Path,
    phase: str | None = None,
    plan: Path | None = None,
    blocker_summary: str | None = None,
    repair_context: dict[str, object] | None = None,
    harness_target: str = "codex",
    injection_mode_override: str | None = None,
    harness_lane_assignment: HarnessLaneAssignment | None = None,
    delegation_request: DelegationRequest | None = None,
    parent_child_metadata: ParentChildRunMetadata | None = None,
    planner_source_bundle_context: PhaseSourceBundle | None = None,
) -> PromptBundle:
    repo = _roadmap_repo_root(roadmap)
    if harness_lane_assignment is not None:
        return _with_delegation_guidance(
            build_lane_prompt_bundle(
                repo=repo,
                harness_target=harness_target,
                action=action,
                roadmap=roadmap,
                assignment=harness_lane_assignment,
                plan=plan,
                injection_mode_override=injection_mode_override,
            ),
            delegation_request=delegation_request,
            parent_child_metadata=parent_child_metadata,
        )
    if action == "roadmap":
        return build_prompt_bundle(
            repo=repo,
            harness_target=harness_target,
            action="roadmap",
            roadmap=roadmap,
            injection_mode_override=injection_mode_override,
        )
    if action == "plan":
        body = _plan_execution_body(repo, roadmap, phase)
        if planner_source_bundle_context is not None:
            body = f"{body}\n\n{_planner_source_bundle_section(planner_source_bundle_context)}"
        return build_prompt_bundle(
            repo=repo,
            harness_target=harness_target,
            action="plan",
            roadmap=roadmap,
            phase=phase,
            body=body,
            injection_mode_override=injection_mode_override,
        )
    if action == "execute":
        return _with_delegation_guidance(
            build_prompt_bundle(
            repo=repo,
            harness_target=harness_target,
                action="execute",
                roadmap=roadmap,
                phase=phase,
                plan=plan,
                injection_mode_override=injection_mode_override,
                body=(
                "If this phase discovers steering that changes downstream work, amend the phase roadmap at the nearest downstream phase "
                "that is not already executing. Do not treat an older downstream phase plan as authoritative after a roadmap amendment. "
                "Treat ignored, private, raw-data, credential, and evidence-source files as read-protected unless the phase plan or source bundle explicitly allowlists the exact path or glob for read access. "
                "Before closeout, run `git status --short` and classify every dirty path against the active owned-file contract; report a repairable dirty_worktree_conflict instead of completion for unowned generated files, unauthorized ignored outputs, or outputs derived from unauthorized raw/private reads."
            ),
            ),
            delegation_request=delegation_request,
            parent_child_metadata=parent_child_metadata,
        )
    if action == "skill-maintenance":
        return build_prompt_bundle(repo=repo, harness_target="codex", action="maintain-skills", roadmap=roadmap)
    if action == "repair":
        summary = blocker_summary or "non-human phase-loop blocker"
        context = repair_context or {}
        state_path = context.get("state_path", ".phase-loop/state.json")
        events_path = context.get("events_path", ".phase-loop/events.jsonl")
        handoff_path = context.get("handoff_path", ".phase-loop/tui-handoff.md")
        handoff_command = context.get("handoff_command", "phase-loop handoff")
        status_command = context.get("status_command", "phase-loop status --json")
        plan_path = str(plan or context.get("plan_path") or "none")
        terminal_summary = context.get("terminal_summary") or {}
        closeout_summary = context.get("closeout_summary") or {}
        artifact_paths = context.get("artifact_paths") or {}
        dirty_paths = context.get("dirty_paths") or ()
        phase_owned_dirty_paths = context.get("phase_owned_dirty_paths") or ()
        previous_phase_owned_paths = context.get("previous_phase_owned_paths") or ()
        unowned_dirty_paths = context.get("unowned_dirty_paths") or ()
        pre_existing_dirty_paths = context.get("pre_existing_dirty_paths") or ()
        phase_owned_dirty = str(bool(context.get("phase_owned_dirty", False))).lower()
        continuation_guidance = (
            "These paths were recorded as phase-owned output from a previous attempt for this same phase. "
            "Continue or restart the previous execute attempt; do not treat these as unrelated dirty files.\n"
            if previous_phase_owned_paths
            else ""
        )
        return _with_delegation_guidance(
            build_prompt_bundle(
            repo=repo,
            harness_target=harness_target,
                action="repair",
                roadmap=roadmap,
                phase=phase,
                plan=plan,
                injection_mode_override=injection_mode_override,
                body=(
                f"Current blocker: {summary}\n\n"
                "Canonical runner state:\n"
                "- When `.phase-loop/` exists, it is the authoritative phase-loop state.\n"
                "- Legacy `.codex/phase-loop/` files are compatibility artifacts only; do not use them to block or supersede canonical `.phase-loop/` state.\n\n"
                "Repair checklist:\n"
                f"1. Inspect `{state_path}`.\n"
                f"2. Inspect `{events_path}`.\n"
                f"3. Inspect `{handoff_path}` or run `{handoff_command}`.\n"
                f"4. Inspect the active phase plan artifact: `{plan_path}`.\n"
                f"5. Re-check machine state with `{status_command}` before and after any local repair.\n"
                "6. Verify whether every dirty path is phase-owned before changing repo state.\n"
                "7. Apply closeout only when the recorded closeout policy allows it.\n"
                "8. Runner-owned `.phase-loop/` ledger writes are optional and must only be attempted when this launch explicitly allows them. "
                "If adapter constraints make `.phase-loop/` read-only or ignored, do not claim ledger mutation; emit a valid shared automation closeout so the parent runner can reconcile the repair.\n"
                "9. Treat ignored, private, raw-data, credential, and evidence-source files as read-protected unless the phase plan or source bundle explicitly allowlists the exact path or glob for read access.\n"
                "10. Before repair closeout, classify every dirty path against the active owned-file contract; preserve ignored phase-owned outputs only when the plan/source bundle includes an explicit allowlist or staging policy.\n\n"
                f"{continuation_guidance}"
                "Allowed outcomes only:\n"
                "- Make the smallest local repair that clears the blocker and leaves the phase resume-ready.\n"
                "- Refresh stale roadmap or phase-plan artifacts when the repair is purely mechanical.\n"
                "- Preserve or freeze a true human blocker with `human_required=true` and the frozen blocker taxonomy.\n\n"
                "Trusted machine context:\n"
                f"- terminal_status={terminal_summary.get('terminal_status', 'unknown')}\n"
                f"- verification_status={terminal_summary.get('verification_status', 'unknown')}\n"
                f"- next_action={terminal_summary.get('next_action', 'unknown')}\n"
                f"- dirty_paths={', '.join(dirty_paths) if dirty_paths else 'none'}\n"
                f"- phase_owned_dirty={phase_owned_dirty}\n"
                f"- phase_owned_dirty_paths={', '.join(phase_owned_dirty_paths) if phase_owned_dirty_paths else 'none'}\n"
                f"- previous_phase_owned_paths={', '.join(previous_phase_owned_paths) if previous_phase_owned_paths else 'none'}\n"
                f"- unowned_dirty_paths={', '.join(unowned_dirty_paths) if unowned_dirty_paths else 'none'}\n"
                f"- pre_existing_dirty_paths={', '.join(pre_existing_dirty_paths) if pre_existing_dirty_paths else 'none'}\n"
                f"- closeout_mode={closeout_summary.get('closeout_mode', 'none')}\n"
                f"- closeout_action={closeout_summary.get('closeout_action', 'none')}\n"
                f"- closeout_verification={closeout_summary.get('verification_status', 'none')}\n"
                f"- latest_run_log={artifact_paths.get('log', 'none')}\n"
                f"- latest_terminal_summary={artifact_paths.get('terminal', 'none')}\n"
                f"- latest_launch_metadata={artifact_paths.get('metadata', 'none')}\n\n"
                "If release preparation and release dispatch are mixed, amend the roadmap at the nearest downstream phase "
                "that is not already executing so preparation and `phase_loop_mutation: release_dispatch` happen in "
                "separate phases. If the operator TUI is stale, point it back to the handoff file or commands above "
                "instead of inventing a new recovery path."
            ),
            ),
            delegation_request=delegation_request,
            parent_child_metadata=parent_child_metadata,
        )
    return _with_delegation_guidance(
        build_prompt_bundle(
            repo=repo,
            harness_target=harness_target,
            action="review",
            roadmap=roadmap,
            phase=phase,
            plan=plan,
            injection_mode_override=injection_mode_override,
        ),
        delegation_request=delegation_request,
        parent_child_metadata=parent_child_metadata,
    )


def build_skill_maintenance_prompt(options) -> str:
    if not options.apply_skill_edits:
        return f"codex-skill-improvement-planner --min-reflections {options.min_reflections}"
    parts = ["codex-skill-editor", "--improvement-plan", str(options.improvement_plan)]
    for skill in options.allow_skills:
        parts.extend(["--allow-skill", skill])
    return " ".join(parts)


def _plan_execution_body(repo: Path, roadmap: Path, phase: str | None) -> str:
    roadmap_rel = roadmap_repo_relative_path(repo, roadmap)
    plan_rel = _expected_plan_artifact_path(roadmap, phase)
    try:
        roadmap_hash = roadmap_fingerprint(roadmap)
    except OSError:
        roadmap_hash = "<compute-from-roadmap>"
    phase_text = phase or "the selected phase"
    return (
        "This phase-loop planning run is execution mode, not planning-only review mode. "
        f"Write the repo-local phase plan artifact for {phase_text} instead of returning only `<proposed_plan>` unless a real blocker prevents the write. "
        f"Write it exactly to `{plan_rel}`; do not use lowercase or alternate filename variants. "
        f"Use `roadmap: {roadmap_rel}` and `roadmap_sha256: {roadmap_hash}` in the plan frontmatter. "
        "Treat `.phase-loop/` as the authoritative runner state when it exists. Legacy `.codex/phase-loop/` files are compatibility artifacts only; do not use them to block or supersede canonical `.phase-loop/` state. "
        "Use optional `## Execution Policy` only for model, effort, work-unit defaults, lane-specific policy, fallback, policy source, or override reason; `Dispatch Hints` remain the executor-only fallback. "
        "Execution Policy selectors must be `work-unit defaults`, `roadmap`, `plan`, `execute`, `repair`, `review`, `maintain-skills`, or lane selectors such as `SL-2`; do not invent action selectors like `reduce` or `verify`. "
        "Reducer and verification work use lane selectors with `work-unit=phase_reducer` or `work-unit=phase_verify`. "
        "Policy precedence is CLI/operator override, phase-plan policy, roadmap policy, `Dispatch Hints`, then registry defaults, and silent downgrade is forbidden without explicit fallback or default inheritance."
    )


def _planner_source_bundle_section(bundle: PhaseSourceBundle) -> str:
    protected_summary = ", ".join(
        f"{source.path} ({source.category}, sha256={source.sha256 or 'none'})"
        for source in bundle.protected_sources
    )
    source_files = ", ".join(
        f"{item.get('path')} ({item.get('purpose', 'source')}, sha256={item.get('sha256', 'none')})"
        for item in bundle.source_files
        if item.get("path")
    )
    owned_files = bundle.delegated_write_policy.get("owned_files", ())
    read_only_files = bundle.delegated_write_policy.get("read_only_files", ())
    return (
        "## Pipeline planning source bundle\n\n"
        "Prefer the bundled canonical specs, diagrams, current-state artifacts, execution graph inputs, "
        "and protected source manifest over ambient repository guesses.\n\n"
        f"- source_bundle: `{bundle.path}`\n"
        f"- source_bundle_sha256: `{bundle.sha256}`\n"
        f"- pipeline_phase_id: `{bundle.phase_id}`\n"
        f"- pipeline_phase_alias: `{bundle.phase_alias}`\n"
        f"- pipeline_mode: `{bundle.pipeline_mode}`\n"
        f"- roadmap: `{bundle.roadmap_path}` sha256=`{bundle.roadmap_sha256}`\n"
        f"- phase_plan_path: `{bundle.phase_plan_path}`\n"
        f"- artifact_target_root: `{bundle.artifact_target_root or 'none'}`\n"
        f"- protected_sources: {protected_summary or 'none'}\n"
        f"- source_files: {source_files or 'none'}\n"
        f"- delegated_write_policy.owned_files: {_policy_values(owned_files)}\n"
        f"- delegated_write_policy.read_only_files: {_policy_values(read_only_files)}\n\n"
        "When writing the plan frontmatter, include `source_bundle`, `source_bundle_sha256`, "
        "`pipeline_phase_id`, and `pipeline_mode` exactly from this bundle. Preserve the exact artifact path supplied by the bundle "
        "for the phase plan target. The delegated write policy names the only Pipeline-owned write scope; "
        "protected-source entries are read-only unless that policy explicitly owns the exact path or glob. "
        "Do not infer writes to `.pipeline/**`, governed-pipeline specs, Portal contracts, Greenfield authority files, "
        "private evidence, raw data, credentials, provider payloads, or legacy `.codex/phase-loop/` state from ambient repo context. "
        "If any bundle freshness or protected-source entry is stale, block with a repairable non-human blocker instead of writing a partial plan."
    )


def _policy_values(value: object) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value) or "none"
    return str(value) if value else "none"


def _roadmap_repo_root(roadmap: Path) -> Path:
    resolved = roadmap.expanduser().resolve()
    cwd = resolved.parent if resolved.parent.exists() else Path.cwd()
    try:
        output = subprocess.check_output(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if output:
            return Path(output).resolve()
    except Exception:
        pass
    return resolved.parents[1] if len(resolved.parents) > 1 else resolved.parent


def _expected_plan_artifact_path(roadmap: Path, phase: str | None) -> str:
    if phase:
        repo = _roadmap_repo_root(roadmap)
        manifest_plan, conflict = manifest_plan_artifact(repo, phase, roadmap=roadmap)
        if manifest_plan is not None and conflict is None:
            try:
                return manifest_plan.relative_to(repo).as_posix()
            except ValueError:
                pass
    version_match = re.search(r"(v[\w.-]+)", roadmap.stem)
    version = version_match.group(1) if version_match else "v1"
    alias = phase or "selected-phase"
    return f"plans/phase-plan-{version}-{alias}.md"


def _with_delegation_guidance(
    bundle: PromptBundle,
    *,
    delegation_request: DelegationRequest | None,
    parent_child_metadata: ParentChildRunMetadata | None,
) -> PromptBundle:
    body_sections = [bundle.body.strip()] if bundle.body.strip() else []
    context_sections = [bundle.context_body.strip()] if bundle.context_body else ([bundle.body.strip()] if bundle.body.strip() else [])
    guidance = _delegation_request_guidance()
    body_sections.append(guidance)
    context_sections.append(guidance)
    if delegation_request is not None:
        delegated_context = _delegation_request_context(delegation_request, parent_child_metadata)
        body_sections.append(delegated_context)
        context_sections.append(delegated_context)
    return PromptBundle(
        workflow_command=bundle.workflow_command,
        body="\n\n".join(section for section in body_sections if section).strip(),
        injection_mode=bundle.injection_mode,
        context_body="\n\n".join(section for section in context_sections if section).strip(),
        expected_skill_pack=bundle.expected_skill_pack,
        product_action=bundle.product_action,
        skill_bundle_id=bundle.skill_bundle_id,
        skill_bundle_sha256=bundle.skill_bundle_sha256,
        fallback_mode=bundle.fallback_mode,
        context_path=bundle.context_path,
        recommended_installed_roots=bundle.recommended_installed_roots,
        installed_skill_roots=bundle.installed_skill_roots,
        installed_skill_warnings=bundle.installed_skill_warnings,
        bridge_skill_inventory=bundle.bridge_skill_inventory,
    )


def _delegation_request_guidance() -> str:
    return (
        "Delegation broker contract:\n"
        "- Do not spawn peer harnesses directly.\n"
        "- Native Claude team tasks stay internal unless they emit a typed runner delegation request.\n"
        "- If review, repair, or execute work should move to another harness, emit a typed delegation request instead.\n"
        "- Required request fields: `request_id`, `product_action`, `target_executor`, `reason`, `owned_files`, `expected_output`, `priority`.\n"
        "- Optional request fields: `review_context`, `repair_context`, metadata-only `budget`.\n"
        "- Approved cross-harness child work is limited to `codex` or `claude` for `execute`, `repair`, and `review`.\n"
        "- Keep owned-file claims narrow and phase-valid. The runner validates active-loop mode, ownership, depth, fanout, budget metadata, and dispatch policy before any child launch."
    )


def _delegation_request_context(
    request: DelegationRequest,
    parent_child_metadata: ParentChildRunMetadata | None,
) -> str:
    lines = [
        "Delegated child context:",
        f"- request id: `{request.request_id}`",
        f"- requested action: `{request.product_action}`",
        f"- target executor: `{request.target_executor}`",
        f"- priority: `{request.priority}`",
        f"- reason: {request.reason}",
        f"- owned files: `{', '.join(request.owned_files) if request.owned_files else 'none'}`",
        f"- expected output: {request.expected_output}",
    ]
    if request.review_context:
        lines.append(f"- review context: {request.review_context}")
    if request.repair_context:
        lines.append(f"- repair context: {request.repair_context}")
    if request.budget is not None:
        budget = request.budget.to_json()
        if budget:
            rendered = ", ".join(f"{key}={value}" for key, value in budget.items())
            lines.append(f"- budget: `{rendered}`")
    if parent_child_metadata is not None:
        lines.append(f"- parent phase: `{parent_child_metadata.parent_phase}`")
        if parent_child_metadata.parent_executor:
            lines.append(f"- parent executor: `{parent_child_metadata.parent_executor}`")
        if parent_child_metadata.parent_run_id:
            lines.append(f"- parent run id: `{parent_child_metadata.parent_run_id}`")
        if parent_child_metadata.child_executor:
            lines.append(f"- resolved child executor: `{parent_child_metadata.child_executor}`")
        if parent_child_metadata.child_worktree_root:
            lines.append(f"- child worktree root: `{parent_child_metadata.child_worktree_root}`")
        if parent_child_metadata.child_closeout_result:
            lines.append(f"- child closeout result: {parent_child_metadata.child_closeout_result}")
    return "\n".join(lines)
