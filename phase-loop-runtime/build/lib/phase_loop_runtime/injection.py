from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

from .baml_modular import BamlValidationError, build_baml_request
from .closeout_validation import extract_plan_produces as _extract_closeout_plan_produces
from .models import HarnessLaneAssignment, PromptBundle
from .runtime_paths import (
    phase_loop_claude_agents_file,
    phase_loop_claude_mcp_config_file,
    phase_loop_claude_plugin_dir,
    phase_loop_claude_settings_file,
)
from .skill_inventory import inspect_skill_parity, resolve_source_skill_dir


class SkillBundleResolutionError(RuntimeError):
    """The workflow-skill bundle was expected but NO skill source resolved.

    Raised (CR #1a) when a context that EXPECTS skills (a non-empty
    ``expected_skill_pack``) resolves ZERO sources across the whole pack. Emptying
    the generic ``HARNESS_SOURCE_ROOTS`` (EXTRACTSKILLS SL-2) means the dotfiles
    overlay roots now arrive only through the ``phase_loop_runtime.skill_sources``
    seam; if that seam is not live (e.g. the entry point is not yet registered after
    an upgrade), resolution silently yields nothing and the runner would inject an
    EMPTY bundle. This fails loud with an actionable message instead. The legitimate
    clean-standalone case (no skills expected -> empty pack) never reaches here.
    """


# Harnesses whose workflow skills are delivered by COPYING resolved source dirs
# (the bundle path that resolve_source_skill_dir feeds). `pi` is deliberately
# EXCLUDED: it delivers skills via a repo-local package-root contract
# (`phase-loop-pi/**`, see _render_pi_context_body), so resolving zero source dirs
# for pi is expected, not a fail-open. Single-sourced from build_bundle.ACTIVE_HARNESSES.
def _bundle_source_harnesses() -> frozenset[str]:
    from .build_bundle import ACTIVE_HARNESSES

    return frozenset(ACTIVE_HARNESSES)


def _resolve_pack_skill_dirs(
    repo: Path, harness_target: str, expected_skill_pack: tuple[str, ...]
) -> dict[str, Path]:
    """Resolve a skill pack to source dirs, failing loud if NONE resolve.

    Returns ``{skill_name: source_dir}`` for the skills that resolved (a partial miss
    is tolerated -- some skills may legitimately be absent). Raises
    :class:`SkillBundleResolutionError` only when the pack is non-empty AND every skill
    resolved to ``None`` AND the harness is a bundle-source harness -- the fail-open
    window the disentangle opened (CR #1a/#3). An empty pack (standalone, no skills
    expected) returns ``{}`` without raising; a non-bundle harness like ``pi`` (skills
    delivered via package-root, not resolve_source_skill_dir) is exempt from the raise.
    """
    if not expected_skill_pack:
        return {}
    resolved: dict[str, Path] = {}
    for skill_name in expected_skill_pack:
        source_dir = resolve_source_skill_dir(repo, harness_target, skill_name)
        if source_dir is not None:
            resolved[skill_name] = source_dir
    if not resolved and harness_target in _bundle_source_harnesses():
        raise SkillBundleResolutionError(
            f"no skill sources resolved for {harness_target} bundle "
            f"(expected {len(expected_skill_pack)}: {', '.join(expected_skill_pack)}). "
            "The phase_loop_runtime.skill_sources entry-point is not registered for "
            "this runtime -- re-run bootstrap (./bootstrap.sh) or "
            "`pip install -e vendor/phase-loop-runtime` so the dotfiles skill-source "
            "overlay is live, or set PHASE_LOOP_SKILL_SOURCE_PLUGINS for a source-mode run."
        )
    return resolved


HARNESS_ACTION_SKILLS = {
    "codex": {
        "roadmap": ("codex-phase-roadmap-builder",),
        "plan": ("codex-plan-phase",),
        "execute": ("codex-execute-phase",),
        "repair": ("codex-phase-loop", "codex-execute-phase"),
        "review": ("codex-phase-loop",),
        "maintain-skills": ("codex-skill-improvement-planner",),
    },
    "claude": {
        "roadmap": (
            "claude-phase-roadmap-builder",
            "claude-plan-phase",
            "claude-execute-phase",
            "claude-phase-loop",
        ),
        "plan": (
            "claude-phase-roadmap-builder",
            "claude-plan-phase",
            "claude-execute-phase",
            "claude-phase-loop",
        ),
        "execute": (
            "claude-phase-roadmap-builder",
            "claude-plan-phase",
            "claude-execute-phase",
            "claude-phase-loop",
        ),
        "repair": (
            "claude-phase-roadmap-builder",
            "claude-plan-phase",
            "claude-execute-phase",
            "claude-phase-loop",
        ),
        "review": (
            "claude-phase-roadmap-builder",
            "claude-plan-phase",
            "claude-execute-phase",
            "claude-phase-loop",
        ),
    },
    "gemini": {
        "roadmap": ("gemini-phase-roadmap-builder",),
        "plan": ("gemini-plan-phase",),
        "execute": ("gemini-execute-phase",),
        "repair": ("gemini-execute-phase",),
        "review": ("gemini-plan-phase",),
    },
    "opencode": {
        "roadmap": ("opencode-phase-roadmap-builder",),
        "plan": ("opencode-plan-phase",),
        "execute": ("opencode-execute-phase",),
        "repair": ("opencode-execute-phase",),
        "review": ("opencode-plan-phase",),
    },
    "pi": {
        "roadmap": ("phase-loop-supervisor", "phase-loop-closeout"),
        "plan": ("phase-loop-supervisor", "phase-loop-closeout"),
        "execute": ("phase-loop-supervisor", "phase-loop-closeout"),
        "repair": ("phase-loop-repair", "phase-loop-closeout"),
        "review": ("phase-loop-supervisor", "phase-loop-closeout"),
    },
    "command": {},
    "manual": {},
}

HARNESS_WORKFLOW_COMMANDS = {
    "codex": {
        "roadmap": lambda roadmap, phase, plan: f"codex-phase-roadmap-builder {roadmap}",
        "plan": lambda roadmap, phase, plan: f"codex-plan-phase {roadmap} {phase}",
        "execute": lambda roadmap, phase, plan: f"codex-execute-phase {plan}",
    },
    "claude": {
        "roadmap": lambda roadmap, phase, plan: f"claude-phase-roadmap-builder {roadmap}",
        "plan": lambda roadmap, phase, plan: f"claude-plan-phase {roadmap} {phase}",
        "execute": lambda roadmap, phase, plan: f"claude-execute-phase {plan}",
    },
    "gemini": {
        "roadmap": lambda roadmap, phase, plan: f"gemini-phase-roadmap-builder {roadmap}",
        "plan": lambda roadmap, phase, plan: f"gemini-plan-phase {roadmap} {phase}",
        "execute": lambda roadmap, phase, plan: f"gemini-execute-phase {plan}",
    },
    "opencode": {
        "roadmap": lambda roadmap, phase, plan: f"opencode-phase-roadmap-builder {roadmap}",
        "plan": lambda roadmap, phase, plan: f"opencode-plan-phase {roadmap} {phase}",
        "execute": lambda roadmap, phase, plan: f"opencode-execute-phase {plan}",
    },
    "pi": {
        "roadmap": lambda roadmap, phase, plan: f"pi-agent-watch --roadmap {roadmap} --max-phases 1 --closeout-mode manual",
        "plan": lambda roadmap, phase, plan: f"pi-agent-watch --roadmap {roadmap} --phase {phase} --max-phases 1 --closeout-mode manual",
        "execute": lambda roadmap, phase, plan: f"pi-agent-watch --phase-plan {plan} --max-phases 1 --closeout-mode manual",
        "repair": lambda roadmap, phase, plan: f"pi-agent-watch --roadmap {roadmap} --phase {phase} --max-phases 1 --closeout-mode manual",
        "review": lambda roadmap, phase, plan: f"pi-agent-watch --roadmap {roadmap} --phase {phase or 'STATUS'} --max-phases 1 --closeout-mode manual",
    },
    "command": {
        "roadmap": lambda roadmap, phase, plan: f"phase-loop roadmap {roadmap}",
        "plan": lambda roadmap, phase, plan: f"phase-loop plan {roadmap} {phase}",
        "execute": lambda roadmap, phase, plan: f"phase-loop execute {plan}",
        "repair": lambda roadmap, phase, plan: f"phase-loop repair {roadmap} {phase}",
        "review": lambda roadmap, phase, plan: f"phase-loop review {roadmap} {phase or 'STATUS'}",
    },
}

HARNESS_INJECTION_MODES = {
    "codex": ("prompt_only", None),
    "claude": ("inline", "context_file"),
    "gemini": ("context_file", "inline"),
    "opencode": ("context_file", "inline"),
    "pi": ("context_file", "manual"),
    "command": ("context_file", "manual"),
    "manual": ("manual", "manual"),
}

CLAUDE_PLUGIN_BUNDLE_VERSION = "1.0.0"
CLAUDE_CONTEXT_MAX_LINES = 250
CLAUDE_CONTEXT_MAX_CHARS = 16000
CLAUDE_TASKLEDGER_HOOK_EVENTS = (
    "TaskCreated",
    "TaskCompleted",
    "TeammateIdle",
    "SubagentStop",
    "PostToolBatch",
    "WorktreeCreate",
)


def _extract_plan_produces(plan: Path | None) -> tuple[str, ...]:
    if plan is None or not plan.exists():
        return ()
    return _extract_closeout_plan_produces(plan)


def build_prompt_bundle(
    *,
    repo: Path,
    harness_target: str,
    action: str,
    roadmap: Path,
    phase: str | None = None,
    plan: Path | None = None,
    body: str = "",
    injection_mode_override: str | None = None,
    fallback_mode_override: str | None = None,
) -> PromptBundle:
    expected_skill_pack = HARNESS_ACTION_SKILLS.get(harness_target, {}).get(action, ())
    workflow_command = _workflow_command(harness_target, action, roadmap, phase, plan)
    default_injection_mode, default_fallback_mode = HARNESS_INJECTION_MODES[harness_target]
    injection_mode = injection_mode_override or default_injection_mode
    fallback_mode = fallback_mode_override or default_fallback_mode
    parity = inspect_skill_parity(repo, harness_target, expected_skill_pack)
    skill_bundle_id = _skill_bundle_id(harness_target, action, expected_skill_pack)
    closeout_instruction = _render_baml_closeout_instruction(
        phase_alias=phase or "unknown",
        plan_produces=_extract_plan_produces(plan),
        plan_owned_files=(),
        include_schema_description=action in {"execute", "repair", "review"},
    )
    bundle_body = "\n\n".join(part for part in (body.strip(), closeout_instruction) if part)
    bundle_sha = _bundle_sha256(
        repo=repo,
        harness_target=harness_target,
        action=action,
        workflow_command=workflow_command,
        body=bundle_body,
        expected_skill_pack=expected_skill_pack,
    )
    return PromptBundle(
        workflow_command=workflow_command,
        body=bundle_body,
        context_body=_render_context_body(
            repo=repo,
            harness_target=harness_target,
            action=action,
            body=bundle_body,
            expected_skill_pack=expected_skill_pack,
            injection_mode=injection_mode,
        ),
        injection_mode=injection_mode,
        expected_skill_pack=expected_skill_pack,
        product_action=action,
        skill_bundle_id=skill_bundle_id,
        skill_bundle_sha256=bundle_sha,
        fallback_mode=fallback_mode,
        recommended_installed_roots=parity.recommended_installed_roots,
        installed_skill_roots=parity.installed_skill_roots,
        installed_skill_warnings=parity.installed_skill_warnings,
        bridge_skill_inventory=parity.bridge_skill_inventory,
    )


def build_lane_prompt_bundle(
    *,
    repo: Path,
    harness_target: str,
    action: str,
    roadmap: Path,
    assignment: HarnessLaneAssignment,
    plan: Path | None = None,
    injection_mode_override: str | None = None,
    fallback_mode_override: str | None = None,
) -> PromptBundle:
    if assignment.prompt_kind == "review":
        body = render_harness_review_context(assignment)
    elif assignment.prompt_kind == "reducer":
        body = render_harness_reducer_context(assignment)
    else:
        body = render_harness_lane_context(assignment)
    return build_prompt_bundle(
        repo=repo,
        harness_target=harness_target,
        action=action,
        roadmap=roadmap,
        phase=assignment.phase,
        plan=plan,
        body=body,
        injection_mode_override=injection_mode_override,
        fallback_mode_override=fallback_mode_override,
    )


def render_harness_lane_context(assignment: HarnessLaneAssignment) -> str:
    return _render_harness_assignment_context(
        assignment,
        title="Harness Lane Work Unit",
        scope="Execute exactly one runner-selected lane work unit.",
        extra=(
            "Implement only the selected lane. Do not widen into whole-phase implementation authority, "
            "peer lanes, or downstream roadmap planning unless the shared closeout reports a typed blocker."
        ),
    )


def render_harness_review_context(assignment: HarnessLaneAssignment) -> str:
    return _render_harness_assignment_context(
        assignment,
        title="Harness Review Work Unit",
        scope="Review exactly one runner-selected lane work unit.",
        extra=(
            "Review prompts must not make production edits. If repair or execute work should move to another "
            "harness, emit a typed delegation request instead of spawning it directly."
        ),
    )


def render_harness_reducer_context(assignment: HarnessLaneAssignment) -> str:
    return _render_harness_assignment_context(
        assignment,
        title="Harness Reducer Work Unit",
        scope="Reduce the selected lane or phase reducer work unit against its frozen acceptance contract.",
        extra=(
            "Reducer work may summarize producer outputs and closeout state, but it must not claim write "
            "authority for producer-owned files."
        ),
    )


def _render_harness_assignment_context(
    assignment: HarnessLaneAssignment,
    *,
    title: str,
    scope: str,
    extra: str,
) -> str:
    policy = json.dumps(assignment.execution_policy, sort_keys=True) if assignment.execution_policy else "{}"
    worktree = assignment.worktree_assignment.to_json() if assignment.worktree_assignment else {}
    worktree_text = json.dumps(worktree, sort_keys=True) if worktree else "{}"
    closeout_instruction = _render_baml_closeout_instruction(
        phase_alias=assignment.phase,
        plan_produces=tuple(
            item for item in assignment.consumed_interfaces if isinstance(item, str) and item.startswith("IF-")
        ),
        plan_owned_files=assignment.owned_files,
    )
    return "\n".join(
        [
            f"## {title}",
            "",
            scope,
            "",
            f"- phase: `{assignment.phase}`",
            f"- lane_id: `{assignment.lane_id}`",
            f"- work_unit_kind: `{assignment.work_unit_kind}`",
            f"- prompt_kind: `{assignment.prompt_kind}`",
            f"- owned_files: `{', '.join(assignment.owned_files) if assignment.owned_files else 'none'}`",
            f"- consumed_interfaces: `{', '.join(assignment.consumed_interfaces) if assignment.consumed_interfaces else 'none'}`",
            f"- depends_on: `{', '.join(assignment.depends_on) if assignment.depends_on else 'none'}`",
            f"- reducer_kind: `{assignment.reducer_kind}`",
            f"- harness_route: `{assignment.harness_route or 'none'}`",
            f"- model: `{assignment.model or 'none'}`",
            f"- effort: `{assignment.effort or 'none'}`",
            f"- fallback_reason: `{assignment.fallback_reason or 'none'}`",
            f"- execution_policy: `{policy}`",
            f"- worktree_assignment: `{worktree_text}`",
            "",
            extra,
            "",
            "BAML closeout schema instruction:",
            closeout_instruction,
            "",
            "Delegation broker contract:",
            "- Do not spawn peer harnesses directly.",
            "- Native Claude team tasks stay internal unless they emit a typed runner delegation request.",
            "- Approved cross-harness child work is limited to `codex` or `claude` for `execute`, `repair`, and `review`.",
        ]
    )


def _render_baml_closeout_instruction(
    *,
    phase_alias: str,
    plan_produces: tuple[str, ...] | list[str],
    plan_owned_files: tuple[str, ...] | list[str],
    closeout_commit_sha: str | None = None,
    include_schema_description: bool = True,
) -> str:
    payload = {
        "phase_alias": phase_alias,
        "plan_produces": list(plan_produces),
        "plan_owned_files": list(plan_owned_files),
        "closeout_commit_sha": closeout_commit_sha,
    }
    try:
        prompt = build_baml_request("EmitPhaseCloseout", payload).prompt
    except BamlValidationError as exc:
        prompt = f"Emit one closeout conforming to emit_phase_closeout.baml / EmitPhaseCloseout. BAML prompt render failed: {exc}"
    if not include_schema_description:
        marker = "\n\nPhase-loop closeout JSON schema description:\n"
        if marker in prompt:
            prompt = prompt[: prompt.index(marker)].rstrip()
    return "EmitPhaseCloseout (`vendor/phase-loop-runtime/src/phase_loop_runtime/baml_src/emit_phase_closeout.baml`):\n" + prompt


def materialize_claude_plugin_bundle(*, repo: Path, run_root: Path, prompt_bundle: PromptBundle) -> dict[str, object]:
    if prompt_bundle.product_action is None:
        return {}
    bundle_root = phase_loop_claude_plugin_dir(run_root).parent
    plugin_dir = phase_loop_claude_plugin_dir(run_root)
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    manifest = _claude_plugin_manifest(prompt_bundle.product_action, prompt_bundle.expected_skill_pack)
    plugin_manifest_path = plugin_dir / ".claude-plugin" / "plugin.json"
    plugin_manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    skills_root = plugin_dir / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    copied_skill_dirs: list[str] = []
    resolved_skill_dirs = _resolve_pack_skill_dirs(repo, "claude", prompt_bundle.expected_skill_pack)
    for skill_name in prompt_bundle.expected_skill_pack:
        source_dir = resolved_skill_dirs.get(skill_name)
        if source_dir is None:
            continue
        target_dir = skills_root / skill_name
        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(source_dir, target_dir)
        copied_skill_dirs.append(str(target_dir))

    settings_path = phase_loop_claude_settings_file(run_root)
    settings_payload = _claude_settings_payload()
    settings_path.write_text(json.dumps(settings_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    agents_path = phase_loop_claude_agents_file(run_root)
    agents_payload = _claude_agents_payload()
    agents_path.write_text(json.dumps(agents_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    mcp_config_path = phase_loop_claude_mcp_config_file(run_root)
    mcp_payload = _claude_mcp_config_payload()
    mcp_config_path.write_text(json.dumps(mcp_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return {
        "root": str(bundle_root),
        "plugin_dir": str(plugin_dir),
        "plugin_manifest_path": str(plugin_manifest_path),
        "settings_path": str(settings_path),
        "agents_path": str(agents_path),
        "mcp_config_path": str(mcp_config_path),
        "expected_skill_pack": list(prompt_bundle.expected_skill_pack),
        "artifact_names": ["plugin-dir", "settings", "agents", "mcp-config"],
        "bundle_sha256": prompt_bundle.skill_bundle_sha256,
        "plugin_manifest_sha256": _file_sha256(plugin_manifest_path),
        "settings_sha256": _file_sha256(settings_path),
        "agents_sha256": _file_sha256(agents_path),
        "mcp_config_sha256": _file_sha256(mcp_config_path),
        "skill_dirs": copied_skill_dirs,
    }


def _workflow_command(harness_target: str, action: str, roadmap: Path, phase: str | None, plan: Path | None) -> str:
    commands = HARNESS_WORKFLOW_COMMANDS.get(harness_target, {})
    if action in commands:
        return commands[action](roadmap, phase, plan)
    if action == "maintain-skills" and harness_target == "codex":
        return "codex-skill-improvement-planner"
    if action == "repair":
        return f"Repair the non-human phase-loop blocker for {phase} using {roadmap}."
    return f"Review phase-loop status for {roadmap}."


def _render_context_body(
    *,
    repo: Path,
    harness_target: str,
    action: str,
    body: str,
    expected_skill_pack: tuple[str, ...],
    injection_mode: str,
) -> str:
    if harness_target == "claude" and expected_skill_pack:
        return _render_claude_compact_context_body(
            action=action,
            body=body,
            expected_skill_pack=expected_skill_pack,
        )
    if harness_target == "pi" and expected_skill_pack:
        return _render_pi_context_body(
            repo=repo,
            action=action,
            body=body,
            expected_skill_pack=expected_skill_pack,
        )
    skills = _skill_bodies(repo, harness_target, expected_skill_pack)
    sections: list[str] = []
    adapter_preamble = _adapter_context_preamble(harness_target, action)
    if adapter_preamble:
        sections.append(adapter_preamble)
    if body.strip():
        sections.append(body.strip())
    if skills:
        sections.append(skills)
    return "\n\n".join(sections).strip()


def _render_claude_compact_context_body(
    *,
    action: str,
    body: str,
    expected_skill_pack: tuple[str, ...],
) -> str:
    sections: list[str] = []
    adapter_preamble = _adapter_context_preamble("claude", action)
    if adapter_preamble:
        sections.append(adapter_preamble)
    if body.strip():
        sections.append(body.strip())
    sections.append(_claude_bundle_manifest(action, expected_skill_pack))
    return "\n\n".join(sections).strip()


def _render_pi_context_body(
    *,
    repo: Path,
    action: str,
    body: str,
    expected_skill_pack: tuple[str, ...],
) -> str:
    skills = "\n".join(f"- `{skill_name}`" for skill_name in expected_skill_pack) or "- `none`"
    sections = [
        "## Repo-owned Pi Agent bundle",
        "",
        f"- product_action: `{action}`",
        f"- package_root: `{repo.resolve() / 'phase-loop-pi'}`",
        f"- config_root: `{repo.resolve() / 'pi-config'}`",
        "- installed_root: `~/.pi/agent/skills`",
        "- source_authority: repo-local `phase-loop-pi/**` and `pi-config/**`, not installed skill bodies",
        "- expected skill pack:",
        skills,
        "",
        "Pi Agent lane contract:",
        "- Accept an explicit system prompt from this context file.",
        "- Enforce the runner-provided tool policy before using tools.",
        "- Write only allowed writes from the lane assignment or active phase plan.",
        "- Treat read-only refs, forbidden refs, private paths, raw data, credentials, and evidence sources as protected unless explicitly allowlisted.",
        "- Keep outputs under runner-provided output roots.",
        "- Preserve verification intent and report every command actually run.",
        "- Cite Greenfield authority when the assignment includes authority citations.",
        "- Preserve governed-pipeline assignment fields when present: lane id, wave id, worktree path, base SHA, isolation mode, owned files, read-only refs, harness route, model, effort, and fallback reason.",
        "- Emit one shared `automation:` closeout and compatible `phase_loop_closeout.v1` metadata.",
        "- Do not own global scheduling, runtime ledger updates, worktree allocation, or merge reduction.",
    ]
    if body.strip():
        sections.extend(["", "## Work Unit Context", "", body.strip()])
    return "\n".join(sections).strip()


def _adapter_context_preamble(harness_target: str, action: str) -> str:
    if harness_target == "gemini":
        return (
            "## Phase-loop adapter constraints\n\n"
            f"This is a non-interactive Gemini CLI `{action}` launch controlled by phase-loop. "
            "The command line at the top of this context is authoritative. Use only tools exposed by the active "
            "Gemini CLI session; do not assume a tool named `run_shell_command` exists. If shell execution is "
            "unavailable, use the exposed read/search/edit tools where sufficient. If the phase cannot be completed "
            "without shell execution, stop with one shared `automation:` closeout using "
            "`status: blocked`, `human_required: false`, `blocker_class: sandbox_command_restriction`, and an "
            "actionable blocker summary. Do not loop on unavailable tools. Do not read installed `~/.gemini/**` "
            "handoffs or write Gemini reflection/handoff files. The only permitted writes are repo-local phase "
            "artifacts or phase-owned files plus optional git staging of those repo files. Do not edit `.phase-loop/` "
            "or claim runner-ledger mutation unless a provided command explicitly permits and confirms it; for repair "
            "launches, the parent runner owns durable blocker clearing from your shared closeout. End with one shared "
            "`automation:` closeout on stdout."
        )
    if harness_target != "claude":
        return ""
    hook_events = ", ".join(CLAUDE_TASKLEDGER_HOOK_EVENTS)
    repo_state_constraint = ""
    if action in {"roadmap", "plan", "review", "repair"}:
        repo_state_constraint = (
            " For non-execute phase-loop launches, do not create or edit repo-local `.claude/**` state such as "
            "`.claude/docs-catalog.json`, `.claude/claude-execute-phase-state.json`, or `.claude/worktrees/`; "
            "if an installed workflow skill suggests those writes, skip that step and continue with only the "
            "phase artifact required by this run."
        )
    return (
        "## Phase-loop adapter constraints\n\n"
        f"This is a non-interactive Claude Code `{action}` launch controlled by phase-loop. "
        "The command line at the top of this context is authoritative. Do not read installed "
        "`~/.claude/skills/**` handoffs. Native Claude collaboration tools such as `Agent`, "
        "`TaskCreate`, `TaskUpdate`, `TaskList`, `TeamCreate`, `TeamDelete`, `SendMessage`, "
        "`EnterWorktree`, `ExitWorktree`, and `ToolSearch` are allowed only when the phase-loop launch "
        "selects a non-solo Claude execution mode and the command-line tool allowlist exposes them; otherwise "
        "work solo. Do not call `AskUserQuestion`, `ExitPlanMode`, or `advisor()`. If any workflow "
        "instruction conflicts with this non-interactive adapter posture, this adapter constraint wins and "
        "the launch command plus TEAMGOV policy are authoritative. "
        f"Documented hook-event guardrails are limited to {hook_events}; do not assume hidden task-list "
        "or teammate-management surfaces beyond those exported artifacts. "
        "The only permitted writes are repo-local phase "
        "artifacts or phase-owned files plus optional git staging of those repo files; do not read "
        "or write `~/.claude/**` reflection or handoff files, and do not edit `.phase-loop/` or legacy "
        f"`.codex/phase-loop/` runner artifacts.{repo_state_constraint} End with one shared `automation:` closeout on stdout."
    )


def claude_hook_guardrail_inventory() -> tuple[str, ...]:
    return CLAUDE_TASKLEDGER_HOOK_EVENTS


def _skill_bodies(repo: Path, harness_target: str, expected_skill_pack: tuple[str, ...]) -> str:
    blocks: list[str] = []
    resolved_skill_dirs = _resolve_pack_skill_dirs(repo, harness_target, expected_skill_pack)
    for skill_name in expected_skill_pack:
        skill_dir = resolved_skill_dirs.get(skill_name)
        if skill_dir is None:
            continue
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            continue
        blocks.append(f"## Skill: {skill_name}\n\n{skill_file.read_text(encoding='utf-8').strip()}")
    return "\n\n".join(blocks)


def _claude_bundle_manifest(action: str, expected_skill_pack: tuple[str, ...]) -> str:
    skills = "\n".join(f"- `{skill_name}`" for skill_name in expected_skill_pack) or "- `none`"
    return (
        "## Repo-owned Claude bundle\n\n"
        "The repo-owned Claude plugin bundle is the source of truth for workflow skills in this run. "
        "The launch already materializes the bundle beside `launch.json` and passes it through Claude's "
        "plugin, settings, agents, and MCP config flags, so do not re-enumerate or restate raw `SKILL.md` bodies here. "
        "Do not list or read `claude-bundle/plugin/skills/**`, bundled `SKILL.md`, `references/`, `assets/`, "
        "or `reflections/` during the launch; the bundle is already loaded and rereading it wastes the launch budget.\n\n"
        f"- bundle id: `{_claude_plugin_bundle_name(action)}`\n"
        "- bundle provenance: repo-owned `claude-bundle/` artifacts recorded in `launch.json`\n"
        f"- expected skill pack count: `{len(expected_skill_pack)}`\n"
        "- expected skill pack:\n"
        f"{skills}\n"
        f"- context budget: <= `{CLAUDE_CONTEXT_MAX_LINES}` lines and <= `{CLAUDE_CONTEXT_MAX_CHARS}` characters\n"
        "- fallback delivery: run-local `context.md` plus the materialized Claude bundle"
    )


def _bundle_sha256(
    *,
    repo: Path,
    harness_target: str,
    action: str,
    workflow_command: str,
    body: str,
    expected_skill_pack: tuple[str, ...],
) -> str:
    manifest = {
        "harness_target": harness_target,
        "action": action,
        "workflow_command": workflow_command,
        "body": body,
        "skills": [],
    }
    if harness_target == "claude":
        manifest["claude_plugin"] = {
            "plugin": _claude_plugin_manifest(action, expected_skill_pack),
            "settings": _claude_settings_payload(),
            "agents": _claude_agents_payload(),
            "mcp_config": _claude_mcp_config_payload(),
        }
    # CR #1a/#3: fail loud if the pack is non-empty but NOTHING resolves, instead of
    # recording {source:None, sha256:None} entries that hash an empty bundle as valid.
    resolved_skill_dirs = _resolve_pack_skill_dirs(repo, harness_target, expected_skill_pack)
    for skill_name in expected_skill_pack:
        skill_dir = resolved_skill_dirs.get(skill_name)
        if skill_dir is None:
            # A partial miss (some skills resolved, this one did not) stays a recorded
            # null entry -- only the all-None case raises (handled above).
            manifest["skills"].append({"name": skill_name, "source": None, "sha256": None})
            continue
        skill_file = skill_dir / "SKILL.md"
        manifest["skills"].append(
            {
                "name": skill_name,
                "source": _skill_source_label(repo, skill_dir),
                "sha256": hashlib.sha256(skill_file.read_bytes()).hexdigest() if skill_file.exists() else None,
            }
        )
    return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode("utf-8")).hexdigest()


def _skill_bundle_id(harness_target: str, action: str, expected_skill_pack: tuple[str, ...]) -> str:
    if harness_target == "claude":
        return _claude_plugin_bundle_name(action)
    return f"{harness_target}:{action}:{','.join(expected_skill_pack) or 'none'}"


def _claude_plugin_bundle_name(action: str) -> str:
    return f"phase-loop-claude-{action}"


def _claude_plugin_manifest(action: str, expected_skill_pack: tuple[str, ...]) -> dict[str, object]:
    return {
        "name": _claude_plugin_bundle_name(action),
        "version": CLAUDE_PLUGIN_BUNDLE_VERSION,
        "description": "Repo-owned Claude phase-loop workflow bundle",
        "skills": "./skills",
        "metadata": {
            "product_action": action,
            "expected_skill_pack": list(expected_skill_pack),
        },
    }


def _claude_settings_payload() -> dict[str, object]:
    return {}


def _claude_agents_payload() -> dict[str, object]:
    return {}


def _claude_mcp_config_payload() -> dict[str, object]:
    return {"mcpServers": {}}


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _skill_source_label(repo: Path, skill_dir: Path) -> str:
    for base in (repo.resolve(), Path(__file__).resolve().parents[4]):
        try:
            return str(skill_dir.relative_to(base))
        except ValueError:
            continue
    return str(skill_dir)
