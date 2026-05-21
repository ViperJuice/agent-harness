from __future__ import annotations

import json
import os
import re
import signal
import shlex
import string
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from typing import Any

from .capability_registry import capability_registry
from .discovery import classify_phase_team_eligibility
from .injection import materialize_claude_plugin_bundle
from .models import (
    ClaudeTeamPolicy,
    DelegationRequest,
    DispatchDecision,
    HarnessLaneAssignment,
    PhaseTeamEligibility,
    InjectionMetadata,
    LaunchRequest,
    ModelSelection,
    metadata_command,
    ParentChildRunMetadata,
    PermissionPolicy,
    PromptBundle,
)
from .observability import heartbeat_path_for_log, run_heartbeat_summary, write_run_heartbeat


STUB_EXECUTOR_REASONS = {
    "manual": "Manual executor is metadata-only in ADAPTER; resume through a TUI after INJECT/CAPREG wire manual handoff execution.",
}

CLAUDE_CONTEXT_PLACEHOLDER = "__PHASE_LOOP_CONTEXT_FILE__"
CLAUDE_PLUGIN_DIR_PLACEHOLDER = "__PHASE_LOOP_CLAUDE_PLUGIN_DIR__"
CLAUDE_SETTINGS_PLACEHOLDER = "__PHASE_LOOP_CLAUDE_SETTINGS__"
CLAUDE_AGENTS_PLACEHOLDER = "__PHASE_LOOP_CLAUDE_AGENTS__"
CLAUDE_MCP_CONFIG_PLACEHOLDER = "__PHASE_LOOP_CLAUDE_MCP_CONFIG__"
CLAUDE_ADAPTER_ALLOWED_TOOLS = "Bash,Read,Edit,MultiEdit,Write,Glob,Grep,LS"
CLAUDE_ADAPTER_DISALLOWED_TOOLS = (
    "Agent,TaskCreate,TaskUpdate,TaskList,TeamCreate,TeamDelete,SendMessage,"
    "EnterWorktree,ExitWorktree,AskUserQuestion,ExitPlanMode,ToolSearch,advisor"
)
GEMINI_CONTEXT_PLACEHOLDER = "__PHASE_LOOP_CONTEXT_FILE__"
OPENCODE_CONTEXT_PLACEHOLDER = "__PHASE_LOOP_CONTEXT_FILE__"
PI_CONTEXT_PLACEHOLDER = "__PHASE_LOOP_CONTEXT_FILE__"
COMMAND_CONTEXT_PLACEHOLDER = "__PHASE_LOOP_CONTEXT_FILE__"
COMMAND_TEMPLATE_ALLOWED_FIELDS = frozenset({"action", "repo", "roadmap", "phase", "plan", "context_file", "model", "effort", "cwd"})


@dataclass(frozen=True)
class LaunchSpec:
    executor: str
    command: list[str]
    prompt_bundle: PromptBundle
    injection_metadata: InjectionMetadata
    delivery_mode: str
    dispatch_decision: DispatchDecision | None
    available: bool
    harness_lane_assignment: HarnessLaneAssignment | None = None
    dry_run_only: bool = False
    reason: str | None = None
    live_proof_gate: str = "disposable_proof_required"
    promotion_status: str = "proof_gated"
    promotion_requirements: tuple[str, ...] = ()
    auth_preflight_mode: str = "none"
    auth_preflight_probes: tuple[str, ...] = ()
    timeout_posture: str = "runner_managed"
    output_capture_format: str = "combined_output"
    terminal_summary_artifact: str = "terminal-summary.json"
    permission_posture: str = "unknown"
    selected_agent: str | None = None
    selected_model: str | None = None
    selected_effort: str | None = None
    profile_source: str | None = None
    override_reason: str | None = None
    selected_variant: str | None = None
    command_adapter_name: str | None = None
    command_template: str | None = None
    wrapped_cwd: str | None = None
    launch_timeout_seconds: int | None = None
    claude_execution_mode: str | None = None
    claude_team_policy: ClaudeTeamPolicy | None = None
    phase_team_eligibility: PhaseTeamEligibility | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "executor": self.executor,
            "command": metadata_command(self.command, self.prompt_bundle.render_prompt()),
            "prompt_bundle": self.prompt_bundle.to_json(),
            "injection_metadata": self.injection_metadata.to_json(),
            "delivery_mode": self.delivery_mode,
            "dispatch_decision": self.dispatch_decision.to_json() if self.dispatch_decision else None,
            "harness_lane_assignment": self.harness_lane_assignment.to_json() if self.harness_lane_assignment else None,
            "available": self.available,
            "dry_run_only": self.dry_run_only,
            "reason": self.reason,
            "live_proof_gate": self.live_proof_gate,
            "promotion_status": self.promotion_status,
            "promotion_requirements": list(self.promotion_requirements),
            "auth_preflight_mode": self.auth_preflight_mode,
            "auth_preflight_probes": list(self.auth_preflight_probes),
            "timeout_posture": self.timeout_posture,
            "output_capture_format": self.output_capture_format,
            "terminal_summary_artifact": self.terminal_summary_artifact,
            "permission_posture": self.permission_posture,
            "selected_agent": self.selected_agent,
            "selected_model": self.selected_model,
            "selected_effort": self.selected_effort,
            "profile_source": self.profile_source,
            "override_reason": self.override_reason,
            "selected_variant": self.selected_variant,
            "command_adapter_name": self.command_adapter_name,
            "command_template": self.command_template,
            "wrapped_cwd": self.wrapped_cwd,
            "launch_timeout_seconds": self.launch_timeout_seconds,
            "claude_execution_mode": self.claude_execution_mode,
            "claude_team_policy": self.claude_team_policy.to_json() if self.claude_team_policy else None,
            "phase_team_eligibility": self.phase_team_eligibility.to_json() if self.phase_team_eligibility else None,
        }

    def delivery_payload(self) -> str | None:
        if self.delivery_mode == "prompt_only":
            return self.prompt_bundle.render_prompt()
        if self.delivery_mode in {"inline", "stdin", "context_file"}:
            return self.prompt_bundle.render_context()
        return None


@dataclass(frozen=True)
class LaunchResult:
    command: list[str]
    returncode: int | None
    output: str = ""
    dry_run: bool = False
    log_path: str | None = None
    heartbeat_path: str | None = None
    terminal_path: str | None = None
    heartbeat_summary: dict[str, Any] | None = None
    executor: str | None = None
    injection_mode: str | None = None
    context_sha256: str | None = None
    expected_skill_pack: tuple[str, ...] = ()
    available: bool = True
    dry_run_only: bool = False
    unavailable_reason: str | None = None
    live_proof_gate: str = "disposable_proof_required"
    promotion_status: str = "proof_gated"
    promotion_requirements: tuple[str, ...] = ()
    auth_preflight_mode: str = "none"
    auth_preflight_probes: tuple[str, ...] = ()
    timeout_posture: str = "runner_managed"
    output_capture_format: str = "combined_output"
    terminal_summary_artifact: str = "terminal-summary.json"
    permission_posture: str = "unknown"
    selected_agent: str | None = None
    selected_model: str | None = None
    selected_variant: str | None = None
    process_pid: int | None = None
    process_group_id: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    timed_out: bool = False
    interrupted: bool = False
    cleanup_evidence: dict[str, Any] | None = None

    @property
    def failed(self) -> bool:
        return self.returncode not in (None, 0)

    def event_metadata(self) -> dict[str, object]:
        data: dict[str, object] = {
            "returncode": self.returncode,
            "dry_run": self.dry_run,
            "executor": self.executor,
            "injection_mode": self.injection_mode,
            "context_sha256": self.context_sha256,
            "expected_skill_pack": list(self.expected_skill_pack),
            "available": self.available,
            "dry_run_only": self.dry_run_only,
            "unavailable_reason": self.unavailable_reason,
            "live_proof_gate": self.live_proof_gate,
            "promotion_status": self.promotion_status,
            "promotion_requirements": list(self.promotion_requirements),
            "auth_preflight_mode": self.auth_preflight_mode,
            "auth_preflight_probes": list(self.auth_preflight_probes),
            "timeout_posture": self.timeout_posture,
            "output_capture_format": self.output_capture_format,
            "terminal_summary_artifact": self.terminal_summary_artifact,
            "permission_posture": self.permission_posture,
            "selected_agent": self.selected_agent,
            "selected_model": self.selected_model,
            "selected_variant": self.selected_variant,
        }
        if self.log_path:
            data["log_path"] = self.log_path
        if self.heartbeat_path:
            data["heartbeat_path"] = self.heartbeat_path
        if self.terminal_path:
            data["terminal_path"] = self.terminal_path
        if self.heartbeat_summary:
            data["heartbeat"] = self.heartbeat_summary
        if self.process_pid is not None:
            data["process_pid"] = self.process_pid
        if self.process_group_id is not None:
            data["process_group_id"] = self.process_group_id
        if self.started_at is not None:
            data["started_at"] = self.started_at
        if self.finished_at is not None:
            data["finished_at"] = self.finished_at
        if self.timed_out:
            data["timed_out"] = self.timed_out
        if self.interrupted:
            data["interrupted"] = self.interrupted
        if self.cleanup_evidence:
            data["cleanup_evidence"] = self.cleanup_evidence
        return {key: value for key, value in data.items() if value not in (None, [])}


@dataclass(frozen=True)
class AuthPreflightResult:
    ok: bool
    blocker_class: str | None = None
    blocker_summary: str | None = None
    metadata: dict[str, Any] | None = None
    suggested_ttl_seconds: int | None = None
    demoted_to: str | None = None


def build_codex_command(
    repo: Path,
    selection: ModelSelection,
    prompt: str,
    json_output: bool = False,
    bypass_approvals: bool = False,
) -> list[str]:
    command = [
        "codex",
        "exec",
        "--cd",
        str(repo),
        "--model",
        selection.model,
        "-c",
        f'model_reasoning_effort="{selection.effort}"',
    ]
    if bypass_approvals:
        command.append("--dangerously-bypass-approvals-and-sandbox")
    else:
        command.extend(["--sandbox", "danger-full-access"])
    if json_output:
        command.append("--json")
    command.append(prompt)
    return command


def build_claude_command(
    repo: Path,
    selection: ModelSelection,
    prompt: str,
    *,
    permission_mode: str,
    allowed_tools: str = CLAUDE_ADAPTER_ALLOWED_TOOLS,
    disallowed_tools: str = CLAUDE_ADAPTER_DISALLOWED_TOOLS,
    bypass_approvals: bool = False,
) -> list[str]:
    command = [
        "claude",
        "-p",
        "--verbose",
        "--output-format",
        "stream-json",
        "--plugin-dir",
        CLAUDE_PLUGIN_DIR_PLACEHOLDER,
        "--settings",
        CLAUDE_SETTINGS_PLACEHOLDER,
        "--agents",
        CLAUDE_AGENTS_PLACEHOLDER,
        "--mcp-config",
        CLAUDE_MCP_CONFIG_PLACEHOLDER,
        "--tools",
        allowed_tools,
        "--disallowedTools",
        disallowed_tools,
        "--permission-mode",
        permission_mode,
        "--add-dir",
        str(repo),
        "--model",
        selection.model,
        "--effort",
        selection.effort,
    ]
    if bypass_approvals:
        command.append("--dangerously-skip-permissions")
    command.append(prompt)
    return command


def build_gemini_command(
    repo: Path,
    selection: ModelSelection,
    *,
    action: str,
    context_file: str,
    bypass_approvals: bool = False,
) -> list[str]:
    command = [
        "gemini",
        "-p",
        (
            f"Read and follow the workflow instructions in `{context_file}` exactly. "
            "Use that file as the authoritative workflow bundle, do not try to read installed skill files outside the workspace, "
            "and emit the required shared automation closeout."
        ),
        "--skip-trust",
        "--approval-mode",
        _gemini_approval_mode(action, bypass_approvals),
        "--include-directories",
        str(repo),
        "--output-format",
        "json",
    ]
    command.extend(["--model", _gemini_cli_model(selection.model)])
    return command


def _gemini_cli_model(model: str) -> str:
    candidate = model.strip()
    if candidate in {"auto", "pro"} or candidate.startswith("gemini-"):
        return candidate
    return "pro"


def build_opencode_command(
    repo: Path,
    selection: ModelSelection,
    *,
    action: str,
    agent: str,
    context_file: str,
    bypass_approvals: bool = False,
) -> tuple[list[str], str | None]:
    model = _opencode_model(selection.model)
    variant = _opencode_variant(action, selection.effort)
    command = [
        "opencode",
        "run",
        (
            f"Read and follow the workflow instructions in `{context_file}` exactly. "
            "Use that file as the authoritative workflow bundle, do not try to read installed skill files outside the workspace, "
            "and emit the required shared automation closeout."
        ),
        "--dir",
        str(repo),
        "--agent",
        agent,
        "--model",
        model,
        "--format",
        "json",
    ]
    if variant:
        command.extend(["--variant", variant])
    if bypass_approvals:
        command.append("--dangerously-skip-permissions")
    return command, variant


def build_pi_command(
    repo: Path,
    selection: ModelSelection,
    *,
    action: str,
    context_file: str,
    plan: Path | None = None,
    bypass_approvals: bool = False,
) -> list[str]:
    command = [
        "pi-agent-watch",
        "--repo",
        str(repo),
        "--prompt-file",
        context_file,
        "--model",
        selection.model,
        "--thinking",
        selection.effort,
        "--closeout-mode",
        "manual",
        "--json",
    ]
    if plan is not None:
        command.extend(["--phase-model", selection.model])
    return command


def build_launch_request(
    *,
    executor: str,
    action: str,
    repo: Path,
    roadmap: Path,
    phase: str | None,
    plan: Path | None,
    model_selection: ModelSelection,
    prompt_bundle: PromptBundle,
    json_output: bool,
    bypass_approvals: bool,
    command_adapter=None,
    launch_timeout_seconds: int | None = None,
    dispatch_decision: DispatchDecision | None = None,
    harness_lane_assignment: HarnessLaneAssignment | None = None,
    delegation_request: DelegationRequest | None = None,
    parent_child_metadata: ParentChildRunMetadata | None = None,
    claude_execution_mode: str | None = None,
    claude_team_policy: ClaudeTeamPolicy | None = None,
    phase_team_eligibility: PhaseTeamEligibility | None = None,
) -> LaunchRequest:
    resolved_mode = None
    resolved_policy = None
    resolved_eligibility = None
    if executor == "claude":
        capability = capability_registry()["claude"]
        resolved_mode = claude_execution_mode or capability.default_claude_execution_mode or "solo"
        resolved_policy = claude_team_policy or _claude_policy_for_mode(capability, resolved_mode)
        resolved_eligibility = phase_team_eligibility or classify_phase_team_eligibility(repo, roadmap, plan)
    return LaunchRequest(
        executor=executor,
        action=action,
        repo=repo,
        roadmap=roadmap,
        phase=phase,
        plan=plan,
        model_selection=model_selection,
        prompt_bundle=prompt_bundle,
        injection_metadata=InjectionMetadata(
            harness_target=executor,
            injection_mode=prompt_bundle.injection_mode,
            context_sha256=prompt_bundle.context_sha256(),
            context_line_count=prompt_bundle.context_line_count(),
            context_char_count=prompt_bundle.context_char_count(),
            expected_skill_pack=prompt_bundle.expected_skill_pack,
            skill_bundle_id=prompt_bundle.skill_bundle_id,
            skill_bundle_sha256=prompt_bundle.skill_bundle_sha256,
            context_path=prompt_bundle.context_path,
            fallback_mode=prompt_bundle.fallback_mode,
            recommended_installed_roots=prompt_bundle.recommended_installed_roots,
            installed_skill_roots=prompt_bundle.installed_skill_roots,
            installed_skill_warnings=prompt_bundle.installed_skill_warnings,
            bridge_skill_inventory=prompt_bundle.bridge_skill_inventory,
        ),
        permission_policy=PermissionPolicy(
            sandbox_mode="danger-full-access",
            bypass_approvals=bypass_approvals,
        ),
        command_adapter=command_adapter,
        dispatch_decision=dispatch_decision,
        harness_lane_assignment=harness_lane_assignment,
        delegation_request=delegation_request,
        parent_child_metadata=parent_child_metadata,
        claude_execution_mode=resolved_mode,
        claude_team_policy=resolved_policy,
        phase_team_eligibility=resolved_eligibility,
        json_output=json_output,
        bypass_approvals=bypass_approvals,
        launch_timeout_seconds=launch_timeout_seconds,
    )


def build_launch_spec(request: LaunchRequest) -> LaunchSpec:
    capability = capability_registry()[request.executor]
    if request.executor == "codex":
        return LaunchSpec(
            executor="codex",
            command=build_codex_command(
                request.repo,
                request.model_selection,
                request.prompt_bundle.render_prompt(),
                json_output=request.json_output,
                bypass_approvals=request.bypass_approvals,
            ),
            prompt_bundle=request.prompt_bundle,
            injection_metadata=request.injection_metadata,
            delivery_mode=request.injection_metadata.injection_mode,
            dispatch_decision=request.dispatch_decision,
            available=True,
            harness_lane_assignment=request.harness_lane_assignment,
            live_proof_gate=capability.live_proof_gate,
            promotion_status=capability.promotion_status,
            promotion_requirements=capability.promotion_requirements,
            auth_preflight_mode=capability.auth_preflight_mode,
            auth_preflight_probes=capability.auth_preflight_probes,
            timeout_posture=capability.timeout_posture,
            output_capture_format=capability.output_capture_format,
            terminal_summary_artifact=capability.terminal_summary_artifact,
            permission_posture=capability.permission_posture,
            selected_model=request.model_selection.model,
            selected_effort=request.model_selection.effort,
            profile_source=request.model_selection.source,
            override_reason=request.model_selection.override_reason,
            wrapped_cwd=str(request.repo),
            launch_timeout_seconds=request.launch_timeout_seconds,
        )
    if request.executor == "claude":
        delivery_mode = "context_file" if _claude_uses_context_file(request.prompt_bundle) else request.injection_metadata.injection_mode
        claude_policy = request.claude_team_policy or _claude_policy_for_mode(
            capability,
            request.claude_execution_mode or capability.default_claude_execution_mode or "solo",
        )
        eligibility = request.phase_team_eligibility or classify_phase_team_eligibility(request.repo, request.roadmap, request.plan)
        policy_error = _claude_team_policy_error(
            action=request.action,
            execution_mode=request.claude_execution_mode or "solo",
            policy=claude_policy,
            eligibility=eligibility,
        )
        if policy_error is not None:
            return LaunchSpec(
                executor="claude",
                command=_stub_command(request, policy_error),
                prompt_bundle=request.prompt_bundle,
                injection_metadata=request.injection_metadata,
                delivery_mode=delivery_mode,
                dispatch_decision=request.dispatch_decision,
                available=False,
                harness_lane_assignment=request.harness_lane_assignment,
                dry_run_only=False,
                reason=policy_error,
                live_proof_gate=capability.live_proof_gate,
                promotion_status=capability.promotion_status,
                promotion_requirements=capability.promotion_requirements,
                auth_preflight_mode=capability.auth_preflight_mode,
                auth_preflight_probes=capability.auth_preflight_probes,
                timeout_posture=capability.timeout_posture,
                output_capture_format=capability.output_capture_format,
                terminal_summary_artifact=capability.terminal_summary_artifact,
                permission_posture=capability.permission_posture,
                selected_model=request.model_selection.model,
                selected_effort=request.model_selection.effort,
                profile_source=request.model_selection.source,
                override_reason=request.model_selection.override_reason,
                wrapped_cwd=str(request.repo),
                launch_timeout_seconds=request.launch_timeout_seconds,
                claude_execution_mode=request.claude_execution_mode or "solo",
                claude_team_policy=claude_policy,
                phase_team_eligibility=eligibility,
            )
        return LaunchSpec(
            executor="claude",
            command=build_claude_command(
                request.repo,
                request.model_selection,
                CLAUDE_CONTEXT_PLACEHOLDER if delivery_mode == "context_file" else request.prompt_bundle.render_context(),
                permission_mode=_claude_permission_mode(request.action, request.bypass_approvals),
                allowed_tools=",".join(claude_policy.allowed_tools) if claude_policy.allowed_tools else CLAUDE_ADAPTER_ALLOWED_TOOLS,
                disallowed_tools=",".join(claude_policy.disallowed_tools) if claude_policy.disallowed_tools else CLAUDE_ADAPTER_DISALLOWED_TOOLS,
                bypass_approvals=request.bypass_approvals,
            ),
            prompt_bundle=request.prompt_bundle,
            injection_metadata=request.injection_metadata,
            delivery_mode=delivery_mode,
            dispatch_decision=request.dispatch_decision,
            available=True,
            harness_lane_assignment=request.harness_lane_assignment,
            live_proof_gate=capability.live_proof_gate,
            promotion_status=capability.promotion_status,
            promotion_requirements=capability.promotion_requirements,
            auth_preflight_mode=capability.auth_preflight_mode,
            auth_preflight_probes=capability.auth_preflight_probes,
            timeout_posture=capability.timeout_posture,
            output_capture_format=capability.output_capture_format,
            terminal_summary_artifact=capability.terminal_summary_artifact,
            permission_posture=capability.permission_posture,
            selected_model=request.model_selection.model,
            selected_effort=request.model_selection.effort,
            profile_source=request.model_selection.source,
            override_reason=request.model_selection.override_reason,
            wrapped_cwd=str(request.repo),
            launch_timeout_seconds=request.launch_timeout_seconds,
            claude_execution_mode=request.claude_execution_mode or "solo",
            claude_team_policy=claude_policy,
            phase_team_eligibility=eligibility,
        )
    if request.executor == "gemini":
        return LaunchSpec(
            executor="gemini",
            command=build_gemini_command(
                request.repo,
                request.model_selection,
                action=request.action,
                context_file=GEMINI_CONTEXT_PLACEHOLDER,
                bypass_approvals=request.bypass_approvals,
            ),
            prompt_bundle=request.prompt_bundle,
            injection_metadata=request.injection_metadata,
            delivery_mode=request.injection_metadata.injection_mode,
            dispatch_decision=request.dispatch_decision,
            available=True,
            harness_lane_assignment=request.harness_lane_assignment,
            live_proof_gate=capability.live_proof_gate,
            promotion_status=capability.promotion_status,
            promotion_requirements=capability.promotion_requirements,
            auth_preflight_mode=capability.auth_preflight_mode,
            auth_preflight_probes=capability.auth_preflight_probes,
            timeout_posture=capability.timeout_posture,
            output_capture_format=capability.output_capture_format,
            terminal_summary_artifact=capability.terminal_summary_artifact,
            permission_posture=capability.permission_posture,
            selected_model=request.model_selection.model,
            selected_effort=request.model_selection.effort,
            profile_source=request.model_selection.source,
            override_reason=request.model_selection.override_reason,
            launch_timeout_seconds=request.launch_timeout_seconds,
        )
    if request.executor == "opencode":
        selected_agent = _opencode_agent(request.action)
        selected_model = _opencode_model(request.model_selection.model)
        command, selected_variant = build_opencode_command(
            request.repo,
            request.model_selection,
            action=request.action,
            agent=selected_agent,
            context_file=OPENCODE_CONTEXT_PLACEHOLDER,
            bypass_approvals=request.bypass_approvals,
        )
        return LaunchSpec(
            executor="opencode",
            command=command,
            prompt_bundle=request.prompt_bundle,
            injection_metadata=request.injection_metadata,
            delivery_mode=request.injection_metadata.injection_mode,
            dispatch_decision=request.dispatch_decision,
            available=True,
            harness_lane_assignment=request.harness_lane_assignment,
            live_proof_gate=capability.live_proof_gate,
            promotion_status=capability.promotion_status,
            promotion_requirements=capability.promotion_requirements,
            auth_preflight_mode=capability.auth_preflight_mode,
            auth_preflight_probes=capability.auth_preflight_probes,
            timeout_posture=capability.timeout_posture,
            output_capture_format=capability.output_capture_format,
            terminal_summary_artifact=capability.terminal_summary_artifact,
            permission_posture=capability.permission_posture,
            selected_agent=selected_agent,
            selected_model=selected_model,
            selected_effort=request.model_selection.effort,
            profile_source=request.model_selection.source,
            override_reason=request.model_selection.override_reason,
            selected_variant=selected_variant,
            launch_timeout_seconds=request.launch_timeout_seconds,
        )
    if request.executor == "pi":
        return LaunchSpec(
            executor="pi",
            command=build_pi_command(
                request.repo,
                request.model_selection,
                action=request.action,
                context_file=PI_CONTEXT_PLACEHOLDER,
                plan=request.plan,
                bypass_approvals=request.bypass_approvals,
            ),
            prompt_bundle=request.prompt_bundle,
            injection_metadata=request.injection_metadata,
            delivery_mode=request.injection_metadata.injection_mode,
            dispatch_decision=request.dispatch_decision,
            available=True,
            harness_lane_assignment=request.harness_lane_assignment,
            live_proof_gate=capability.live_proof_gate,
            promotion_status=capability.promotion_status,
            promotion_requirements=capability.promotion_requirements,
            auth_preflight_mode=capability.auth_preflight_mode,
            auth_preflight_probes=capability.auth_preflight_probes,
            timeout_posture=capability.timeout_posture,
            output_capture_format=capability.output_capture_format,
            terminal_summary_artifact=capability.terminal_summary_artifact,
            permission_posture=capability.permission_posture,
            selected_model=request.model_selection.model,
            selected_effort=request.model_selection.effort,
            profile_source=request.model_selection.source,
            override_reason=request.model_selection.override_reason,
            wrapped_cwd=str(request.repo),
            launch_timeout_seconds=request.launch_timeout_seconds,
        )
    if request.executor == "command":
        return _build_command_launch_spec(request, capability)
    reason = STUB_EXECUTOR_REASONS[request.executor]
    return LaunchSpec(
        executor=request.executor,
        command=_stub_command(request, reason),
        prompt_bundle=request.prompt_bundle,
        injection_metadata=request.injection_metadata,
        delivery_mode=request.injection_metadata.injection_mode,
        dispatch_decision=request.dispatch_decision,
        available=False,
        harness_lane_assignment=request.harness_lane_assignment,
        dry_run_only=True,
        reason=reason,
        live_proof_gate=capability.live_proof_gate,
        promotion_status=capability.promotion_status,
        promotion_requirements=capability.promotion_requirements,
        auth_preflight_mode=capability.auth_preflight_mode,
        auth_preflight_probes=capability.auth_preflight_probes,
        timeout_posture=capability.timeout_posture,
        output_capture_format=capability.output_capture_format,
        terminal_summary_artifact=capability.terminal_summary_artifact,
        permission_posture=capability.permission_posture,
        launch_timeout_seconds=request.launch_timeout_seconds,
    )


def _build_command_launch_spec(request: LaunchRequest, capability) -> LaunchSpec:
    config = request.command_adapter
    if config is None:
        return _unavailable_command_spec(
            request,
            capability,
            "Command executor requires explicit adapter inputs: pass `--command-name` and `--command-template`.",
        )
    if request.action not in config.supported_actions:
        return _unavailable_command_spec(
            request,
            capability,
            f"Command adapter `{config.name}` does not allow the `{request.action}` action.",
        )
    if config.delivery_mode != "context_file":
        return _unavailable_command_spec(
            request,
            capability,
            f"Command adapter `{config.name}` delivery mode `{config.delivery_mode}` is unsupported; use `context_file`.",
        )
    template_error = _validate_command_template(config.template, request)
    if template_error:
        return _unavailable_command_spec(request, capability, template_error, command_adapter_name=config.name, command_template=config.template)
    command = _render_command_template(config.template, request)
    return LaunchSpec(
        executor="command",
        command=command,
        prompt_bundle=request.prompt_bundle,
        injection_metadata=request.injection_metadata,
        delivery_mode=config.delivery_mode,
        dispatch_decision=request.dispatch_decision,
        available=True,
        harness_lane_assignment=request.harness_lane_assignment,
        live_proof_gate=capability.live_proof_gate,
        promotion_status=capability.promotion_status,
        promotion_requirements=capability.promotion_requirements,
        auth_preflight_mode=capability.auth_preflight_mode,
        auth_preflight_probes=capability.auth_preflight_probes,
        timeout_posture=capability.timeout_posture,
        output_capture_format=capability.output_capture_format,
        terminal_summary_artifact=capability.terminal_summary_artifact,
        permission_posture=capability.permission_posture,
        selected_model=request.model_selection.model,
        selected_effort=request.model_selection.effort,
        profile_source=request.model_selection.source,
        override_reason=request.model_selection.override_reason,
        command_adapter_name=config.name,
        command_template=config.template,
        wrapped_cwd=str(request.repo),
        launch_timeout_seconds=request.launch_timeout_seconds,
        claude_execution_mode=request.claude_execution_mode,
        claude_team_policy=request.claude_team_policy,
        phase_team_eligibility=request.phase_team_eligibility,
    )


def _unavailable_command_spec(
    request: LaunchRequest,
    capability,
    reason: str,
    *,
    command_adapter_name: str | None = None,
    command_template: str | None = None,
) -> LaunchSpec:
    return LaunchSpec(
        executor="command",
        command=_stub_command(request, reason),
        prompt_bundle=request.prompt_bundle,
        injection_metadata=request.injection_metadata,
        delivery_mode=request.injection_metadata.injection_mode,
        dispatch_decision=request.dispatch_decision,
        available=False,
        harness_lane_assignment=request.harness_lane_assignment,
        dry_run_only=False,
        reason=reason,
        live_proof_gate=capability.live_proof_gate,
        promotion_status=capability.promotion_status,
        promotion_requirements=capability.promotion_requirements,
        auth_preflight_mode=capability.auth_preflight_mode,
        auth_preflight_probes=capability.auth_preflight_probes,
        timeout_posture=capability.timeout_posture,
        output_capture_format=capability.output_capture_format,
        terminal_summary_artifact=capability.terminal_summary_artifact,
        permission_posture=capability.permission_posture,
        selected_model=request.model_selection.model,
        selected_effort=request.model_selection.effort,
        profile_source=request.model_selection.source,
        override_reason=request.model_selection.override_reason,
        command_adapter_name=command_adapter_name,
        command_template=command_template,
        wrapped_cwd=str(request.repo),
        launch_timeout_seconds=request.launch_timeout_seconds,
        claude_execution_mode=request.claude_execution_mode,
        claude_team_policy=request.claude_team_policy,
        phase_team_eligibility=request.phase_team_eligibility,
    )


def _claude_policy_for_mode(capability, execution_mode: str) -> ClaudeTeamPolicy:
    for policy in capability.claude_execution_policies:
        if policy.execution_mode == execution_mode:
            return policy
    raise ValueError(f"missing Claude team policy for mode `{execution_mode}`")


def _claude_team_policy_error(
    *,
    action: str,
    execution_mode: str,
    policy: ClaudeTeamPolicy,
    eligibility: PhaseTeamEligibility,
) -> str | None:
    if action in policy.disallowed_actions:
        return f"Claude {execution_mode} mode is denied for `{action}` by TEAMGOV policy."
    if execution_mode == "solo":
        return None
    if not eligibility.eligible_for_native_team:
        return (
            f"Claude {execution_mode} mode is denied because the active phase plan is not team-safe: "
            f"{eligibility.reason}."
        )
    if policy.requires_disjoint_owned_files and not (eligibility.has_disjoint_write_lanes or eligibility.has_only_read_only_lanes):
        return (
            f"Claude {execution_mode} mode requires disjoint write ownership or true read-only lanes; "
            "the active phase plan does not satisfy that contract."
        )
    return None


def _validate_command_template(template: str, request: LaunchRequest) -> str | None:
    if not template.strip():
        return "Command executor requires a non-empty command template."
    formatter = string.Formatter()
    field_names: set[str] = set()
    for _literal_text, field_name, _format_spec, _conversion in formatter.parse(template):
        if field_name is None:
            continue
        field_names.add(field_name)
        if field_name not in COMMAND_TEMPLATE_ALLOWED_FIELDS:
            allowed = ", ".join(sorted(COMMAND_TEMPLATE_ALLOWED_FIELDS))
            return f"Command template uses unsupported placeholder `{field_name}`. Allowed placeholders: {allowed}."
    if "context_file" not in field_names:
        return "Command template must include `{context_file}` so the runner can deliver the repo-sourced workflow bundle."
    if "plan" in field_names and request.plan is None:
        return "Command template requires `{plan}`, but the selected phase has no current plan artifact."
    if "phase" in field_names and request.phase is None:
        return "Command template requires `{phase}`, but no phase alias was selected."
    return None


def _render_command_template(template: str, request: LaunchRequest) -> list[str]:
    rendered = template.format(
        action=request.action,
        repo=str(request.repo),
        roadmap=str(request.roadmap),
        phase=request.phase or "",
        plan=str(request.plan) if request.plan is not None else "",
        context_file=COMMAND_CONTEXT_PLACEHOLDER,
        model=request.model_selection.model,
        effort=request.model_selection.effort,
        cwd=str(request.repo),
    )
    return shlex.split(rendered)


def run_auth_preflight(spec: LaunchSpec) -> AuthPreflightResult:
    if spec.auth_preflight_mode != "metadata_only":
        return AuthPreflightResult(ok=True, metadata={})

    metadata: dict[str, Any] = {"executor": spec.executor, "probes": []}
    probe_outputs: dict[str, str] = {}
    for probe in spec.auth_preflight_probes:
        completed = subprocess.run(probe, shell=True, text=True, capture_output=True, check=False)
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        probe_outputs[probe] = " ".join(part for part in (stdout, stderr) if part)
        probe_metadata = {
            "probe": probe,
            "returncode": completed.returncode,
            "command_available": completed.returncode == 0,
            "stdout_present": bool(stdout),
            "stderr_present": bool(stderr),
            "stdout_bytes": len(completed.stdout or ""),
            "stderr_bytes": len(completed.stderr or ""),
        }
        if _looks_like_help_surface(stdout) or _looks_like_help_surface(stderr):
            probe_metadata["help_surface_present"] = True
        if probe.endswith("--version") and completed.returncode == 0:
            probe_metadata["version_surface_present"] = True
        if "auth status" in probe or "login status" in probe:
            probe_metadata["auth_surface_present"] = completed.returncode == 0
        metadata["probes"].append(probe_metadata)
        if completed.returncode != 0:
            label = {
                "claude": "Claude",
                "codex": "Codex",
                "gemini": "Gemini",
                "opencode": "OpenCode",
                "pi": "Pi Agent",
                "command": "command adapter",
                "manual": "manual handoff",
            }.get(spec.executor, spec.executor)
            if _looks_like_capacity_exhaustion(probe_outputs[probe]):
                summary = f"{label} CLI metadata preflight for `{probe}` reported temporary provider capacity or quota exhaustion."
                return AuthPreflightResult(
                    ok=False,
                    blocker_class="unretryable_external_outage",
                    blocker_summary=summary,
                    metadata=metadata,
                    suggested_ttl_seconds=1800,
                    demoted_to="manual_only",
                )
            summary = f"{label} CLI metadata preflight failed for `{probe}`."
            return AuthPreflightResult(
                ok=False,
                blocker_class="account_or_billing_setup",
                blocker_summary=summary,
                metadata=metadata,
                suggested_ttl_seconds=300,
                demoted_to="proof_gated",
            )

    if spec.executor == "codex":
        status_text = probe_outputs.get("codex login status", "").lower()
        metadata["auth_status"] = {"logged_in": "logged in" in status_text, "source": "codex login status"}
        if "logged in" not in status_text:
            return AuthPreflightResult(
                ok=False,
                blocker_class="account_or_billing_setup",
                blocker_summary="Codex CLI is installed but not logged in to an authenticated local subscription session.",
                metadata=metadata,
                suggested_ttl_seconds=300,
                demoted_to="proof_gated",
            )
        return AuthPreflightResult(ok=True, metadata=metadata)

    if spec.executor == "claude":
        status_payload = _parse_json_object(probe_outputs.get("claude auth status", ""))
        metadata["auth_status"] = {
            "logged_in": bool(status_payload.get("loggedIn")) if status_payload else False,
            "subscription_present": bool(status_payload.get("subscriptionType")) if status_payload else False,
            "quota_signal_present": _claude_status_has_quota_signal(status_payload),
            "source": "claude auth status",
        }
        if _claude_status_has_quota_signal(status_payload) or _looks_like_capacity_exhaustion(probe_outputs.get("claude auth status", "")):
            return AuthPreflightResult(
                ok=False,
                blocker_class="unretryable_external_outage",
                blocker_summary="Claude CLI auth metadata reported temporary provider capacity or quota exhaustion.",
                metadata=metadata,
                suggested_ttl_seconds=1800,
                demoted_to="manual_only",
            )
        if not status_payload or not status_payload.get("loggedIn"):
            return AuthPreflightResult(
                ok=False,
                blocker_class="account_or_billing_setup",
                blocker_summary="Claude CLI is installed but not logged in to an authenticated local subscription session.",
                metadata=metadata,
                suggested_ttl_seconds=300,
                demoted_to="proof_gated",
            )
        if not status_payload.get("subscriptionType"):
            return AuthPreflightResult(
                ok=False,
                blocker_class="account_or_billing_setup",
                blocker_summary="Claude CLI auth is present but the subscription state is missing from `claude auth status`.",
                metadata=metadata,
                suggested_ttl_seconds=300,
                demoted_to="proof_gated",
            )
        return AuthPreflightResult(ok=True, metadata=metadata)

    if spec.executor == "opencode":
        metadata["selected_agent"] = spec.selected_agent
        metadata["selected_model"] = spec.selected_model
        metadata["selected_variant"] = spec.selected_variant
        metadata["permission_posture"] = spec.permission_posture
        if not spec.selected_agent:
            return AuthPreflightResult(
                ok=False,
                blocker_class="repeated_verification_failure",
                blocker_summary="OpenCode live launch requires an explicit agent selection, but none was recorded in launch metadata.",
                metadata=metadata,
            )
        if not spec.permission_posture or spec.permission_posture == "permissive":
            return AuthPreflightResult(
                ok=False,
                blocker_class="product_decision_missing",
                blocker_summary="OpenCode live launch detected a permissive agent posture without an explicit runner opt-in.",
                metadata=metadata,
            )
        return AuthPreflightResult(ok=True, metadata=metadata)

    return AuthPreflightResult(ok=True, metadata=metadata)


def _looks_like_capacity_exhaustion(text: str) -> bool:
    normalized = re.sub(r"[\s_-]+", ".", text.lower())
    return bool(re.search(r"capacity|exhausted|rate\.?limit|503|temporarily\.?unavailable", normalized))


def _claude_status_has_quota_signal(payload: dict[str, Any]) -> bool:
    if not payload:
        return False
    return _looks_like_capacity_exhaustion(json.dumps(payload, sort_keys=True))


def launch_with_spec(
    spec: LaunchSpec,
    *,
    dry_run: bool = False,
    log_path: Path | None = None,
    stream_output: bool = False,
    heartbeat_path: Path | None = None,
    heartbeat_interval_seconds: int = 30,
    quiet_warning_seconds: int = 600,
    quiet_blocker_seconds: int = 1800,
) -> LaunchResult:
    if not spec.available and not dry_run:
        raise ValueError("live launch requested for unavailable executor")
    command = _resolve_command_context(spec, log_path)
    result = launch(
        command,
        dry_run=dry_run,
        log_path=log_path,
        stdin_text=spec.delivery_payload() if spec.delivery_mode == "stdin" else None,
        stream_output=stream_output,
        heartbeat_path=heartbeat_path,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        quiet_warning_seconds=quiet_warning_seconds,
        quiet_blocker_seconds=quiet_blocker_seconds,
        timeout_seconds=spec.launch_timeout_seconds,
        cwd=spec.wrapped_cwd,
    )
    return _result_with_spec(result, spec)


def extract_executor_output_text(result: LaunchResult, spec: LaunchSpec) -> str:
    if spec.executor == "codex":
        text = _extract_codex_stream_json_text(result.output)
        return text if text else result.output
    if spec.executor == "claude":
        stream_text = _extract_claude_stream_json_text(result.output)
        if stream_text:
            return _normalize_claude_output_text(stream_text)
        payload = _find_json_object(result.output)
        if not payload:
            return result.output
        text = payload.get("result")
        return _normalize_claude_output_text(text) if isinstance(text, str) else result.output
    if spec.executor == "gemini":
        text = _extract_gemini_output_text(result.output)
        return text if text else result.output
    if spec.executor == "opencode":
        text = _extract_opencode_output_text(result.output)
        return text if text else result.output
    if spec.executor == "pi":
        text = _extract_pi_output_text(result.output)
        return text if text else result.output
    return result.output


def _extract_gemini_output_text(raw: str) -> str:
    stream_text = _extract_gemini_stream_json_text(raw)
    if stream_text:
        return stream_text
    payload = _find_json_object_with_any_key(raw, ("response", "result"))
    if not payload:
        payload = _find_json_object(raw)
    if not payload:
        return raw
    text = payload.get("response")
    if isinstance(text, str):
        return text
    text = payload.get("result")
    return text if isinstance(text, str) else raw


def _extract_opencode_output_text(raw: str) -> str:
    stream_text = _extract_opencode_json_lines(raw)
    if stream_text:
        return stream_text
    payload = _find_json_object(raw)
    if not payload:
        return raw
    for key in ("response", "result", "content", "message"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            content = value.get("content")
            if isinstance(content, str):
                return content
    return raw


def _extract_pi_output_text(raw: str) -> str:
    payload = _find_json_object(raw)
    if not payload:
        return raw
    for key in ("automation", "response", "result", "content", "message"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            text = value.get("text") or value.get("content")
            if isinstance(text, str):
                return text
    return raw


def launch(
    command: list[str],
    dry_run: bool = False,
    log_path: Path | None = None,
    stdin_text: str | None = None,
    stream_output: bool = False,
    heartbeat_path: Path | None = None,
    heartbeat_interval_seconds: int = 30,
    quiet_warning_seconds: int = 600,
    quiet_blocker_seconds: int = 1800,
    timeout_seconds: int | None = None,
    cwd: str | Path | None = None,
) -> LaunchResult:
    if log_path is not None and heartbeat_path is None:
        heartbeat_path = heartbeat_path_for_log(log_path)
    if dry_run:
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("dry run: command not executed\n", encoding="utf-8")
        heartbeat_summary = None
        if heartbeat_path is not None:
            heartbeat_summary = run_heartbeat_summary(
                log_path=log_path,
                heartbeat_path=heartbeat_path,
                heartbeat_interval_seconds=heartbeat_interval_seconds,
                quiet_warning_seconds=quiet_warning_seconds,
                quiet_blocker_seconds=quiet_blocker_seconds,
                command=command,
                returncode=None,
            )
            write_run_heartbeat(heartbeat_path, heartbeat_summary)
        return LaunchResult(
            command=command,
            returncode=None,
            dry_run=True,
            log_path=str(log_path) if log_path else None,
            heartbeat_path=str(heartbeat_path) if heartbeat_path else None,
            terminal_path=str(log_path.parent / "terminal-summary.json") if log_path else None,
            heartbeat_summary=heartbeat_summary,
            started_at=_utc_now(),
            finished_at=_utc_now(),
        )
    if log_path is None:
        run_kwargs: dict[str, Any] = {
            "text": True,
            "capture_output": True,
            "check": False,
            "cwd": str(cwd) if cwd else None,
        }
        if stdin_text is None:
            run_kwargs["stdin"] = subprocess.DEVNULL
        else:
            run_kwargs["input"] = stdin_text
        completed = subprocess.run(command, **run_kwargs)
        return LaunchResult(command=command, returncode=completed.returncode, output=completed.stdout + completed.stderr)

    log_path.parent.mkdir(parents=True, exist_ok=True)
    output_parts: list[str] = []
    line_queue: Queue[str | None] = Queue()
    started_at = _utc_now()
    started_monotonic = time.monotonic()

    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            text=True,
            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            cwd=str(cwd) if cwd else None,
        )
        process_group_id = _process_group_id(process.pid)
        assert process.stdout is not None
        if stdin_text is not None and process.stdin is not None:
            process.stdin.write(stdin_text)
            process.stdin.close()
        reader = threading.Thread(target=_read_lines, args=(process.stdout, line_queue), daemon=True)
        reader.start()
        last_heartbeat = 0.0
        timed_out = False
        interrupted = False
        cleanup_evidence: dict[str, Any] | None = None
        while True:
            try:
                try:
                    line = line_queue.get(timeout=1)
                except Empty:
                    line = None
                if line:
                    output_parts.append(line)
                    log.write(line)
                    log.flush()
                    if stream_output:
                        print(line, end="", flush=True)
                now = time.monotonic()
                if heartbeat_path is not None and now - last_heartbeat >= heartbeat_interval_seconds:
                    heartbeat = run_heartbeat_summary(
                        log_path=log_path,
                        heartbeat_path=heartbeat_path,
                        pid=process.pid,
                        started_monotonic=started_monotonic,
                        started_at=started_at,
                        heartbeat_interval_seconds=heartbeat_interval_seconds,
                        quiet_warning_seconds=quiet_warning_seconds,
                        quiet_blocker_seconds=quiet_blocker_seconds,
                        command=command,
                        returncode=process.poll(),
                    )
                    write_run_heartbeat(heartbeat_path, heartbeat)
                    if stream_output and heartbeat.get("quiet_level") != "active":
                        print(
                            "heartbeat:",
                            f"pid={heartbeat.get('pid')}",
                            f"elapsed={heartbeat.get('elapsed_seconds')}s",
                            f"quiet={heartbeat.get('seconds_since_log_update')}s",
                            f"level={heartbeat.get('quiet_level')}",
                            flush=True,
                        )
                    last_heartbeat = now
                if timeout_seconds is not None and now - started_monotonic >= timeout_seconds and process.poll() is None:
                    timed_out = True
                    cleanup_evidence = _cleanup_process_group(process, process_group_id, reason="timeout")
                if process.poll() is not None:
                    while True:
                        try:
                            line = line_queue.get_nowait()
                        except Empty:
                            break
                        if line:
                            output_parts.append(line)
                            log.write(line)
                            log.flush()
                            if stream_output:
                                print(line, end="", flush=True)
                    break
            except KeyboardInterrupt:
                interrupted = True
                cleanup_evidence = _cleanup_process_group(process, process_group_id, reason="interrupt")
                break
        returncode = process.wait()
        reader.join(timeout=1)
        try:
            process.stdout.close()
        except OSError:
            pass
    heartbeat_summary = None
    if heartbeat_path is not None:
        heartbeat_summary = run_heartbeat_summary(
            log_path=log_path,
            heartbeat_path=heartbeat_path,
            pid=process.pid,
            started_monotonic=started_monotonic,
            started_at=started_at,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            quiet_warning_seconds=quiet_warning_seconds,
            quiet_blocker_seconds=quiet_blocker_seconds,
            command=command,
            returncode=returncode,
        )
        write_run_heartbeat(heartbeat_path, heartbeat_summary)
    return LaunchResult(
        command=command,
        returncode=returncode,
        output="".join(output_parts),
        log_path=str(log_path),
        heartbeat_path=str(heartbeat_path) if heartbeat_path else None,
        terminal_path=str(log_path.parent / "terminal-summary.json"),
        heartbeat_summary=heartbeat_summary,
        process_pid=process.pid,
        process_group_id=process_group_id,
        started_at=started_at,
        finished_at=_utc_now(),
        timed_out=timed_out,
        interrupted=interrupted,
        cleanup_evidence=cleanup_evidence,
    )


def _stub_command(request: LaunchRequest, reason: str) -> list[str]:
    phase = request.phase or "UNKNOWN"
    return [
        request.executor,
        "--phase-loop-stub",
        request.action,
        phase,
        request.prompt_bundle.workflow_command,
        reason,
    ]


def _claude_uses_context_file(prompt_bundle: PromptBundle) -> bool:
    return prompt_bundle.injection_mode == "inline" and prompt_bundle.fallback_mode == "context_file" and bool(prompt_bundle.expected_skill_pack)


def _claude_permission_mode(action: str, bypass_approvals: bool) -> str:
    if bypass_approvals:
        return "bypassPermissions"
    if action == "review":
        return "plan"
    if action in {"execute", "repair"}:
        return "bypassPermissions"
    return "acceptEdits"


def _gemini_approval_mode(action: str, bypass_approvals: bool) -> str:
    if bypass_approvals:
        return "yolo"
    if action == "review":
        return "plan"
    if action in {"plan", "execute", "repair"}:
        return "yolo"
    return "auto_edit"


def _parse_json_object(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _looks_like_help_surface(text: str) -> bool:
    lowered = text.lower()
    return "usage:" in lowered or "options:" in lowered or "--help" in lowered


def _find_json_object(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    decoder = json.JSONDecoder()
    for index, char in enumerate(raw):
        if char != "{":
            continue
        try:
            data, _end = decoder.raw_decode(raw[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _find_json_object_with_any_key(raw: str | None, keys: tuple[str, ...]) -> dict[str, Any] | None:
    if not raw:
        return None
    decoder = json.JSONDecoder()
    for index, char in enumerate(raw):
        if char != "{":
            continue
        try:
            data, _end = decoder.raw_decode(raw[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and any(key in data for key in keys):
            return data
    return None


def _extract_codex_stream_json_text(raw: str) -> str:
    agent_messages: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        payload = _parse_json_object(line)
        if not payload:
            continue
        item = payload.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message":
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                agent_messages.append(text.strip())
            continue
        if payload.get("type") in {"agent_message", "message"}:
            text = payload.get("text") or payload.get("content")
            if isinstance(text, str) and text.strip():
                agent_messages.append(text.strip())
    return "\n\n".join(agent_messages).strip()


def _extract_gemini_stream_json_text(raw: str) -> str:
    assistant_chunks: list[str] = []
    assistant_messages: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        payload = _parse_json_object(line)
        if not payload or payload.get("type") != "message" or payload.get("role") != "assistant":
            continue
        content = payload.get("content")
        if not isinstance(content, str):
            continue
        if payload.get("delta"):
            assistant_chunks.append(content)
        else:
            assistant_messages.append(content)
    if assistant_chunks:
        return "".join(assistant_chunks).strip()
    if assistant_messages:
        return assistant_messages[-1].strip()
    return ""


def _extract_claude_stream_json_text(raw: str) -> str:
    result_text = ""
    assistant_chunks: list[str] = []
    assistant_messages: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        payload = _parse_json_object(line)
        if not payload:
            continue
        if payload.get("type") == "result":
            text = payload.get("result")
            if isinstance(text, str):
                result_text = text.strip()
            continue
        if payload.get("type") != "assistant":
            continue
        message = payload.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                assistant_messages.append(content)
            elif isinstance(content, list):
                message_text = "".join(
                    item.get("text", "") for item in content if isinstance(item, dict) and isinstance(item.get("text"), str)
                )
                if message_text:
                    assistant_messages.append(message_text)
        content = payload.get("content")
        if isinstance(content, str):
            if payload.get("delta"):
                assistant_chunks.append(content)
            else:
                assistant_messages.append(content)
    if result_text:
        return result_text
    if assistant_chunks:
        return "".join(assistant_chunks).strip()
    if assistant_messages:
        return assistant_messages[-1].strip()
    return ""


def _extract_opencode_json_lines(raw: str) -> str:
    assistant_chunks: list[str] = []
    assistant_messages: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        payload = _parse_json_object(line)
        if not payload:
            continue
        content = payload.get("content")
        if isinstance(content, str) and payload.get("role") == "assistant":
            if payload.get("delta"):
                assistant_chunks.append(content)
            else:
                assistant_messages.append(content)
            continue
        message = payload.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                assistant_messages.append(content)
    if assistant_chunks:
        return "".join(assistant_chunks).strip()
    if assistant_messages:
        return assistant_messages[-1].strip()
    return ""


def _resolve_command_context(spec: LaunchSpec, log_path: Path | None) -> list[str]:
    if spec.executor == "claude" and any(
        placeholder in " ".join(spec.command)
        for placeholder in (
            CLAUDE_CONTEXT_PLACEHOLDER,
            CLAUDE_PLUGIN_DIR_PLACEHOLDER,
            CLAUDE_SETTINGS_PLACEHOLDER,
            CLAUDE_AGENTS_PLACEHOLDER,
            CLAUDE_MCP_CONFIG_PLACEHOLDER,
        )
    ):
        context_path = _phase_loop_context_path(log_path, spec.prompt_bundle.render_context())
        if context_path is None:
            return spec.command
        bundle_paths = _claude_bundle_paths(spec, log_path)
        if bundle_paths is None:
            return spec.command
        resolved: list[str] = []
        for part in spec.command:
            resolved.append(
                part.replace(
                    CLAUDE_CONTEXT_PLACEHOLDER,
                    _claude_context_prompt(context_path, spec.claude_execution_mode or "solo"),
                )
                .replace(CLAUDE_PLUGIN_DIR_PLACEHOLDER, bundle_paths["plugin_dir"])
                .replace(CLAUDE_SETTINGS_PLACEHOLDER, bundle_paths["settings_path"])
                .replace(CLAUDE_AGENTS_PLACEHOLDER, bundle_paths["agents_json"])
                .replace(CLAUDE_MCP_CONFIG_PLACEHOLDER, bundle_paths["mcp_config_path"])
            )
        return resolved
    if spec.executor == "gemini" and GEMINI_CONTEXT_PLACEHOLDER in " ".join(spec.command):
        repo_path = _command_repo_path(spec.command, "--include-directories")
        if repo_path is None:
            return spec.command
        context_path = _gemini_workspace_context_path(repo_path, log_path, spec.prompt_bundle.render_context())
        if context_path is None:
            return spec.command
        command = [part.replace(GEMINI_CONTEXT_PLACEHOLDER, context_path) for part in spec.command]
        include_idx = command.index("--include-directories") + 1
        mirror_root = str(Path(context_path).parent)
        if mirror_root not in command[include_idx]:
            command[include_idx] = f"{command[include_idx]},{mirror_root}"
        return command
    if spec.executor == "opencode" and OPENCODE_CONTEXT_PLACEHOLDER in " ".join(spec.command):
        context_path = _phase_loop_context_path(log_path, spec.prompt_bundle.render_context())
        if context_path is None:
            return spec.command
        return [part.replace(OPENCODE_CONTEXT_PLACEHOLDER, context_path) for part in spec.command]
    if spec.executor == "pi" and PI_CONTEXT_PLACEHOLDER in " ".join(spec.command):
        context_path = _phase_loop_context_path(log_path, spec.prompt_bundle.render_context())
        if context_path is None:
            return spec.command
        return [part.replace(PI_CONTEXT_PLACEHOLDER, context_path) for part in spec.command]
    if spec.executor == "command" and COMMAND_CONTEXT_PLACEHOLDER in " ".join(spec.command):
        context_path = _phase_loop_context_path(log_path, spec.prompt_bundle.render_context())
        if context_path is None:
            return spec.command
        return [part.replace(COMMAND_CONTEXT_PLACEHOLDER, context_path) for part in spec.command]
    return spec.command


def _command_repo_path(command: list[str], flag: str) -> Path | None:
    try:
        include_idx = command.index(flag) + 1
    except ValueError:
        return None
    raw = command[include_idx].split(",", 1)[0].strip()
    return Path(raw) if raw else None


def _gemini_workspace_context_path(repo: Path, log_path: Path | None, context_text: str) -> str | None:
    if log_path is None:
        return None
    run_slug = log_path.parent.name
    target = Path.home() / ".gemini" / "tmp" / repo.name / "phase-loop" / run_slug / "context.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(context_text.rstrip() + "\n", encoding="utf-8")
    return str(target)


def _phase_loop_context_path(log_path: Path | None, context_text: str) -> str | None:
    if log_path is None:
        return None
    target = log_path.parent / "context.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(context_text.rstrip() + "\n", encoding="utf-8")
    return str(target)


def _claude_bundle_paths(spec: LaunchSpec, log_path: Path | None) -> dict[str, str] | None:
    if log_path is None or spec.wrapped_cwd is None:
        return None
    run_root = log_path.parent
    inventory = materialize_claude_plugin_bundle(
        repo=Path(spec.wrapped_cwd),
        run_root=run_root,
        prompt_bundle=spec.prompt_bundle,
    )
    if not inventory:
        return None
    agents_path = Path(str(inventory["agents_path"]))
    agents_json = json.dumps(json.loads(agents_path.read_text(encoding="utf-8")), separators=(",", ":"))
    return {
        "plugin_dir": str(inventory["plugin_dir"]),
        "settings_path": str(inventory["settings_path"]),
        "agents_json": agents_json,
        "mcp_config_path": str(inventory["mcp_config_path"]),
    }


def _claude_context_prompt(context_path: str, execution_mode: str = "solo") -> str:
    if execution_mode == "solo":
        collaboration_guidance = (
            "ignore any embedded workflow step that asks for delegation, teams, advisor review, "
            "user questions, worktree entry, plan-mode approval, or slash-command reentry such as "
            "`/clear`, `/claude-plan-phase`, or `/claude-execute-phase`. Work solo inside the provided workspace."
        )
    else:
        collaboration_guidance = (
            f"the selected Claude execution mode is `{execution_mode}`. Native subagents, task lists, "
            "teammate messaging, and team/worktree coordination may be used only within the command-line "
            "tool allowlist and the generated TEAMGOV/task-ledger artifacts. Cross-harness delegation must "
            "still be reduced into the typed phase-loop delegation request contract."
        )
    return (
        f"Read the workflow command at the top of `{context_path}` first, then follow that file exactly. "
        f"This is a non-interactive phase-loop adapter launch: {collaboration_guidance} "
        "Write only repo-local phase artifacts or phase-owned files, never "
        "`~/.claude/**` reflection/handoff files or `.phase-loop/` or legacy `.codex/phase-loop/` runner artifacts. Run the applicable checks "
        "and finish by printing exactly one shared automation closeout that uses these keys: "
        "`status`, `next_skill`, `next_command`, `human_required`, `blocker_class`, `blocker_summary`, "
        "`required_human_inputs`, and `verification_status`."
    )


def _normalize_claude_output_text(text: str) -> str:
    if not text or _has_canonical_shared_automation_fields(text):
        return text
    legacy_fields = _legacy_claude_automation_fields(text)
    if not legacy_fields or not _should_normalize_legacy_claude_closeout(legacy_fields):
        return text
    normalized = _legacy_claude_shared_automation_lines(legacy_fields)
    if not normalized:
        return text
    return "Normalized shared automation closeout:\n" + "\n".join(normalized) + "\n\nOriginal child output:\n" + text.rstrip() + "\n"


def _has_canonical_shared_automation_fields(text: str) -> bool:
    text = _last_automation_scope(text)
    required = (
        "status",
        "next_skill",
        "next_command",
        "human_required",
        "blocker_class",
        "blocker_summary",
        "verification_status",
    )
    if not all(re.search(rf"^\s*{re.escape(key)}:\s*.+$", text, re.MULTILINE) for key in required):
        return False
    status_match = re.search(r"^\s*status:\s*(.+?)\s*$", text, re.MULTILINE)
    verification_match = re.search(r"^\s*verification_status:\s*(.+?)\s*$", text, re.MULTILINE)
    if status_match is None or verification_match is None:
        return False
    status = status_match.group(1).strip().strip("'\"").lower()
    verification_status = verification_match.group(1).strip().strip("'\"").lower()
    if status == "executed" and verification_status == "passed" and re.search(
        r"^\s*(?:next_skill|next_command):\s*.*phase-loop.*$", text, re.MULTILINE
    ):
        return False
    return status in {"unplanned", "planned", "executing", "executed", "awaiting_phase_closeout", "complete", "blocked", "unknown"} and verification_status in {"not_run", "passed", "failed", "blocked"}


def _legacy_claude_automation_fields(text: str) -> dict[str, str]:
    text = _last_automation_scope(text)
    fields: dict[str, str] = {}
    for key in ("status", "skill", "next_skill", "phase", "phase_id", "next_phase", "next_command", "verification_status"):
        match = re.search(rf"^\s*{re.escape(key)}:\s*(.+?)\s*$", text, re.MULTILINE)
        if match:
            fields[key] = match.group(1).strip().strip("'\"")
    return fields


def _last_automation_scope(text: str) -> str:
    matches = list(re.finditer(r"(?m)^(?P<indent>[ \t]*)automation:\s*$", text))
    if not matches:
        return text
    match = matches[-1]
    base_indent = len(match.group("indent"))
    selected: list[str] = []
    for index, line in enumerate(text[match.start() :].splitlines()):
        if index == 0 or not line.strip():
            selected.append(line)
            continue
        indent = len(line) - len(line.lstrip())
        if indent > base_indent:
            selected.append(line)
            continue
        break
    return "\n".join(selected)


def _should_normalize_legacy_claude_closeout(fields: dict[str, str]) -> bool:
    if fields.get("skill") or fields.get("next_phase"):
        return True
    raw_status = fields.get("status", "").strip().lower()
    if raw_status in {"planned", "executed", "complete"}:
        return bool(fields.get("verification_status") or fields.get("next_command"))
    if raw_status in {
        "success",
        "ok",
        "pass",
        "passed",
        "done",
        "completed",
        "cleared",
        "ready_for_closeout",
        "repaired",
        "error",
        "failure",
        "fail",
    }:
        return bool(fields.get("verification_status") or fields.get("next_command"))
    if raw_status.endswith("_complete"):
        return bool(fields.get("verification_status") or fields.get("next_command"))
    return False


def _legacy_claude_shared_automation_lines(fields: dict[str, str]) -> list[str]:
    skill = fields.get("skill", "").strip() or fields.get("next_skill", "").strip()
    next_phase = fields.get("next_phase", "").strip()
    next_command = fields.get("next_command", "none").strip() or "none"
    verification_status = _canonical_verification_status(fields.get("verification_status", "not_run"))
    status = _canonical_phase_status(
        raw_status=fields.get("status", ""),
        skill=skill,
        next_phase=next_phase,
        next_command=next_command,
        verification_status=verification_status,
    )
    next_skill = _legacy_next_skill(next_command)

    blocker_summary = "none"
    blocker_class = "none"
    if status == "blocked":
        blocker_class = "repeated_verification_failure"
        blocker_summary = _legacy_blocker_summary(next_phase) or (
            "Legacy Claude closeout reported a blocked outcome without the shared blocker metadata."
        )

    return [
        "automation:",
        f"  status: {status}",
        f"  next_skill: {next_skill}",
        f"  next_command: {next_command}",
        "  human_required: false",
        f"  blocker_class: {blocker_class}",
        f"  blocker_summary: {blocker_summary}",
        "  required_human_inputs: []",
        f"  verification_status: {verification_status}",
    ]


def _legacy_next_skill(next_command: str) -> str:
    if next_command == "none":
        return "none"
    if next_command.startswith("/"):
        return next_command[1:].split()[0]
    return next_command.split()[0] if next_command.strip() else "none"


def _legacy_blocker_summary(next_phase: str) -> str:
    blocked_marker = " - blocked:"
    if blocked_marker not in next_phase.lower():
        return ""
    prefix, _sep, suffix = next_phase.partition(" - ")
    details = suffix.split(":", 1)[1].strip() if ":" in suffix else suffix.strip()
    if prefix and details:
        return f"{prefix.strip()} blocked: {details}"
    return details


def _canonical_verification_status(raw_status: str) -> str:
    lowered = raw_status.strip().lower()
    first_token = re.split(r"[\s:;,\-—–]+", lowered, maxsplit=1)[0] if lowered else ""
    if lowered in {"passed", "not_run", "failed", "blocked"}:
        return lowered
    if first_token in {"passed", "success", "ok", "pass"}:
        return "passed"
    if first_token in {"partial", "limited", "skipped"}:
        return "passed"
    if lowered.startswith("plan-only") or first_token in {"plan", "evidence_checked", "evidence-checked"}:
        return "passed"
    if first_token in {"not_run", "not-run", "notrun"}:
        return "not_run"
    if first_token in {"failed", "error", "failure", "fail"}:
        return "failed"
    if first_token == "blocked":
        return "blocked"
    if lowered in {"success", "ok", "pass"}:
        return "passed"
    if lowered in {"error", "failure", "fail"}:
        return "failed"
    return "not_run"


def _canonical_phase_status(
    *,
    raw_status: str,
    skill: str,
    next_phase: str,
    next_command: str,
    verification_status: str,
) -> str:
    lowered = raw_status.strip().lower()
    next_phase_lower = next_phase.lower()
    next_command_lower = next_command.lower()

    if lowered in {"unplanned", "planned", "executing", "executed", "awaiting_phase_closeout", "complete", "blocked", "unknown"}:
        if lowered == "complete" and verification_status == "passed" and _looks_like_phase_plan_next_command(next_command_lower):
            return "planned"
        if lowered == "executed" and verification_status == "passed" and (
            skill.endswith("phase-loop") or "phase-loop" in next_command_lower
        ):
            return "complete"
        return lowered
    if lowered in {"error", "failure", "fail"}:
        return "blocked"
    if "blocked" in next_phase_lower:
        return "blocked"
    if lowered in {"cleared", "ready_for_closeout"}:
        return "complete" if verification_status == "passed" else "blocked"
    if lowered in {"success", "ok", "pass", "passed", "done", "completed"}:
        if lowered != "completed" and skill.endswith("execute-phase") and _looks_like_phase_plan_next_command(next_command_lower):
            return "planned"
        if verification_status != "passed":
            return "blocked"
        if (
            "execution ready" in next_phase_lower
            or skill.endswith("plan-phase")
            or (
                lowered != "completed"
                and skill.endswith("execute-phase")
                and _looks_like_phase_plan_next_command(next_command_lower)
            )
        ):
            return "planned"
        if (
            "roadmap complete" in next_phase_lower
            or "planning ready" in next_phase_lower
            or skill.endswith("phase-roadmap-builder")
            or skill.endswith("phase-loop")
            or "phase-roadmap-builder" in next_command_lower
            or "phase-loop" in next_command_lower
            or next_command_lower == "none"
            or skill.endswith("execute-phase")
        ):
            return "complete"
        return "executed"
    if lowered.endswith("_complete"):
        return "complete" if verification_status == "passed" else "blocked"
    if lowered == "repaired":
        if verification_status != "passed":
            return "blocked"
        if next_command_lower == "none" or "roadmap complete" in next_phase_lower:
            return "complete"
        return "executed"
    if skill.endswith("plan-phase"):
        return "planned" if verification_status == "passed" else "blocked"
    if skill.endswith("execute-phase"):
        if verification_status == "passed" and (
            "roadmap complete" in next_phase_lower
            or "planning ready" in next_phase_lower
            or next_command_lower == "none"
        ):
            return "complete"
        return "executed" if verification_status == "passed" else "blocked"
    return "executed" if verification_status == "passed" else "blocked"


def _looks_like_phase_plan_next_command(next_command_lower: str) -> bool:
    return "phase-plan" in next_command_lower and "execute-phase" in next_command_lower


def _result_with_spec(result: LaunchResult, spec: LaunchSpec) -> LaunchResult:
    return LaunchResult(
        command=result.command,
        returncode=result.returncode,
        output=result.output,
        dry_run=result.dry_run,
        log_path=result.log_path,
        heartbeat_path=result.heartbeat_path,
        terminal_path=result.terminal_path,
        heartbeat_summary=result.heartbeat_summary,
        executor=spec.executor,
        injection_mode=spec.delivery_mode,
        context_sha256=spec.injection_metadata.context_sha256,
        expected_skill_pack=spec.injection_metadata.expected_skill_pack,
        available=spec.available,
        dry_run_only=spec.dry_run_only,
        unavailable_reason=spec.reason,
        live_proof_gate=spec.live_proof_gate,
        promotion_status=spec.promotion_status,
        promotion_requirements=spec.promotion_requirements,
        auth_preflight_mode=spec.auth_preflight_mode,
        auth_preflight_probes=spec.auth_preflight_probes,
        timeout_posture=spec.timeout_posture,
        output_capture_format=spec.output_capture_format,
        terminal_summary_artifact=spec.terminal_summary_artifact,
        permission_posture=spec.permission_posture,
        selected_agent=spec.selected_agent,
        selected_model=spec.selected_model,
        selected_variant=spec.selected_variant,
        process_pid=result.process_pid,
        process_group_id=result.process_group_id,
        started_at=result.started_at,
        finished_at=result.finished_at,
        timed_out=result.timed_out,
        interrupted=result.interrupted,
        cleanup_evidence=result.cleanup_evidence,
    )


def _read_lines(stdout, line_queue: Queue[str | None]) -> None:
    try:
        for line in stdout:
            line_queue.put(line)
    finally:
        line_queue.put(None)


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _process_group_id(pid: int) -> int | None:
    try:
        return os.getpgid(pid)
    except OSError:
        return None


def _cleanup_process_group(process: subprocess.Popen, process_group_id: int | None, *, reason: str) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "reason": reason,
        "requested_at": _utc_now(),
        "process_pid": process.pid,
        "process_group_id": process_group_id,
        "cleanup_scope": "process_group" if process_group_id is not None else "process_only",
        "signals_sent": [],
    }
    if process.poll() is not None:
        evidence["result"] = "already_exited"
        evidence["final_returncode"] = process.returncode
        evidence["process_alive_after_cleanup"] = False
        return evidence
    if process_group_id is not None:
        try:
            os.killpg(process_group_id, signal.SIGTERM)
            evidence["signals_sent"].append("SIGTERM")
        except OSError:
            evidence["result"] = "already_exited"
            evidence["final_returncode"] = process.poll()
            evidence["process_alive_after_cleanup"] = process.poll() is None
            return evidence
    else:
        process.terminate()
        evidence["signals_sent"].append("SIGTERM")
    try:
        final_returncode = process.wait(timeout=2)
        evidence["result"] = "terminated"
        evidence["final_returncode"] = final_returncode
        evidence["process_alive_after_cleanup"] = False
        return evidence
    except subprocess.TimeoutExpired:
        pass
    if process_group_id is not None:
        try:
            os.killpg(process_group_id, signal.SIGKILL)
        except OSError:
            evidence["result"] = "already_exited"
            evidence["final_returncode"] = process.poll()
            evidence["process_alive_after_cleanup"] = process.poll() is None
            return evidence
    else:
        process.kill()
    evidence["signals_sent"].append("SIGKILL")
    try:
        final_returncode = process.wait(timeout=2)
        evidence["result"] = "killed"
        evidence["final_returncode"] = final_returncode
        evidence["process_alive_after_cleanup"] = False
    except subprocess.TimeoutExpired:
        evidence["result"] = "cleanup_failed"
        evidence["final_returncode"] = process.poll()
        evidence["process_alive_after_cleanup"] = process.poll() is None
    return evidence


def _opencode_agent(action: str) -> str:
    if action in {"roadmap", "plan", "review"}:
        return "build"
    return "build"


def _opencode_model(model: str) -> str:
    if "/" in model:
        return model
    normalized = model.strip().lower()
    if normalized.startswith("gpt-"):
        return f"openai/{model}"
    if normalized.startswith("claude-") or normalized == "sonnet":
        return f"anthropic/{model}"
    if normalized.startswith("gemini-"):
        return f"google/{model}"
    return model


def _opencode_variant(action: str, effort: str) -> str | None:
    if action == "review":
        return None
    if effort in {"xhigh", "max"}:
        return "max"
    if effort == "high":
        return "high"
    if effort == "low":
        return "minimal"
    return None
