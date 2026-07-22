from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
import fnmatch
import hashlib
import os
import re
from pathlib import Path
from typing import Any, Callable, ClassVar, Iterable, Mapping


PHASE_STATUSES = (
    "unplanned",
    "planned",
    "executing",
    "executed",
    "awaiting_phase_closeout",
    "complete",
    "blocked",
    "unknown",
)
EVENT_STATUSES = PHASE_STATUSES + ("plan_skipped",)

COMMANDS = ("run", "resume", "status", "dry-run", "maintain-skills", "monitor", "hotfix")
MODEL_PROFILES = ("roadmap", "plan", "execute", "repair", "review", "skill-maintenance")
EXECUTORS = ("codex", "claude", "gemini", "grok", "opencode", "pi", "command", "manual")
# Vendor-agnostic model roles (model-routing-v1). "class" not "tier" — tier
# already denotes evidence-audit budgets (--tier-2/--tier-3).
MODEL_CLASSES = ("planner", "implementer", "worker")
WORK_UNIT_KINDS = (
    "roadmap_build",
    "phase_plan",
    "lane_execute",
    "lane_review",
    "phase_reducer",
    "phase_verify",
    "repair",
    "closeout",
)
HOTFIX_WORK_UNITS = ("hotfix",)
HARNESS_WORK_UNIT_PROMPT_KINDS = (
    "implementation",
    "review",
    "reducer",
    "verify",
    "closeout",
)
WORK_UNIT_STATUSES = (
    "pending",
    "running",
    "complete",
    "blocked",
    "skipped",
    "superseded",
    "awaiting-closeout",
)
WORK_UNIT_METRIC_SCHEMA_VERSION = "work_unit_metric.v1"
NORMALIZED_EFFORT_LEVELS = ("minimal", "low", "medium", "high", "xhigh", "max")
UNSUPPORTED_POLICY_BEHAVIORS = ("block", "fallback", "inherit_default")
CLOSEOUT_MODES = ("manual", "commit", "push")
# v45 IF-0-FOUND-1: cross-phase scheduler surface. "off"/"serialized" preserve
# today's one-phase-at-a-time behavior; "concurrent" dispatches the full ready
# wave. Wiring into the runner main-loop lands in v45 Phase 3 (SCHED).
PHASE_SCHEDULER_MODES = ("off", "serialized", "concurrent")
# Closeout-exception vocabulary (roadmap v40 PROTO freeze). GATE/BREAKGLASS import
# these; they do not redefine them. Sensitivity classes drive the graduated gate:
# SAFE classes may auto-pass as a recorded soft exception; UNSAFE classes require an
# explicit break-glass reason. Any path that matches no class is treated as UNSAFE
# (deny-by-default).
SAFE_SENSITIVITY_CLASSES = ("docs", "plans", "handoffs", "config_nonsource")
UNSAFE_SENSITIVITY_CLASSES = ("source", "ci", "secrets", "lockfile")
SENSITIVITY_CLASSES = SAFE_SENSITIVITY_CLASSES + UNSAFE_SENSITIVITY_CLASSES
CLOSEOUT_EXCEPTION_KINDS = ("soft", "break_glass")
CLOSEOUT_EXCEPTIONS_METADATA_KEY = "closeout_exceptions"
PERMISSION_POSTURES = ("explicit", "permissive", "manual", "unknown")
SUBAGENT_POSTURES = ("native", "limited", "none", "unknown")
CLAUDE_EXECUTION_MODES = ("solo", "subagent", "agent_team")
CLAUDE_WORKTREE_POSTURES = ("phase_loop_managed", "manual_only")
DISPATCH_HINT_FIELDS = (
    "preferred_executors",
    "allowed_executors",
    "fallback_executors",
    "disabled_executors",
    "required_capabilities",
)
DISPATCH_CAPABILITIES = (
    "live_launch",
    "dry_run",
    "skill_bundle_injection",
    "inline_instructions",
    "context_file_instructions",
    "manual_handoff",
    "subagents",
    "explicit_approval_controls",
    "structured_output",
    "browser_automation",
)
DISPATCH_SELECTION_PATHS = ("fixed_action_policy", "preferred", "fallback")
DELEGATION_PRIORITIES = ("low", "normal", "high", "urgent")
DELEGATION_STATUSES = ("approved", "denied")
LIVE_PROOF_GATES = ("none", "disposable_proof_required", "disposable_proof_recorded")
AUTH_PREFLIGHT_MODES = ("none", "metadata_only")
TIMEOUT_POSTURES = ("runner_managed", "executor_managed", "unknown")
OUTPUT_CAPTURE_FORMATS = ("combined_output", "json_stream", "terminal_summary")
PROMOTION_STATUSES = ("live", "proof_gated", "manual_only")
FAILURE_KINDS = ("adapter_failure", "phase_failure")
BLOCKER_POSTURES = ("human_required", "repairable_non_human")
OPERATOR_MATURITY_LABELS = ("live_supported", "proof_blocked", "experimental", "manual_only")

BLOCKER_CLASSES = (
    "missing_secret",
    "account_or_billing_setup",
    "admin_approval",
    "destructive_operation",
    "ambiguous_roadmap_selection",
    "product_decision_missing",
    "dirty_worktree_conflict",
    "branch_sync_conflict",
    "stalled_child_observation",
    "repeated_verification_failure",
    "sandbox_command_restriction",
    "upstream_phase_unmet",
    "contract_bug",
    "gold_record_amendment",
    "closeout_evidence_drift",
    "closeout_scope_violation",
    "unretryable_external_outage",
    "stuck_loop",
    "merge_conflict",
    "operator_override_missing_reason",
    "concurrent_dispatch",
    "verification_evidence_missing",
    "review_gate_block",
    "docs_freshness_stale",
    "consiliency_gate_blocked",
)

LANE_IR_DIAGNOSTIC_KINDS = (
    "cycle",
    "overlapping_write_ownership",
    "unsafe_concurrent_lane",
    "stale_worktree_assignment",
    "active_work_unit",
    "human_required_blocked_work_unit",
    "missing_producer_dependency",
    "missing_owned_files",
    "malformed_owned_files",
    "malformed_dependencies",
    "unsupported_lane_policy",
    "missing_lane_sections",
)

LANE_REDUCER_KINDS = (
    "none",
    "acceptance_reducer",
    "compatibility_reducer",
    "verification_reducer",
    "summary_reducer",
)

LANE_SCHEDULER_MODES = ("off", "serialized", "concurrent")
LANE_WAVE_STATUSES = ("ready", "blocked", "empty")
WORKTREE_ISOLATION_MODES = ("main_worktree", "git_worktree")
DIRTY_PATH_CLASSIFICATIONS = (
    "pre_existing",
    "lane_owned",
    "peer_owned",
    "reducer_owned",
    "unowned",
)
PIPELINE_MODE_LITERALS = ("standalone", "pipeline_optional", "pipeline_required")
# LEGACY FALLBACK ONLY. The canonical protected_source_category vocabulary is
# owned by the contract SoT (consiliency-contract >= 0.6.5,
# `core/registries/protected-source-categories.json`, registry name
# `protected_source_categories`). When the installed contract ships that
# registry, its `coarse_categories` enum is AUTHORITATIVE and is what
# `PipelineProtectedSource` validates against -- see
# `protected_source_categories()` below. This tuple is retained only as the
# contract-absent degrade path (older contract with no registry): the six
# pre-existing coarse buckets the loop has always accepted. The registry is a
# strict superset (same six + `governance_contracts`), so a category valid under
# the fallback is never rejected under the registry. This is migration glue, not
# a permanent shim -- registry-present is the SoT.
PIPELINE_PROTECTED_SOURCE_CATEGORIES = (
    "specs",
    "diagrams",
    "adapter_config",
    "definition_files",
    "portal_contracts",
    "phase_artifacts",
)
PIPELINE_PROTECTED_SOURCE_ROLES = (
    "seed_spec",
    "predecessor_spec",
    "active_canonical_spec",
    "archived_spec",
    "managed_mirror_file",
    "unmanaged_spec_input",
    "legacy_specs_bundle",
    "root_specs_intake",
    "pipeline_specs_canonical",
    "adapter_configured_intake_root",
    "mirror_manifest",
    "archive_manifest",
)
PIPELINE_PROTECTED_SOURCE_LEGACY_ROLES = ("protected",)
PIPELINE_METADATA_DIAGNOSTIC_KINDS = (
    "invalid_pipeline_mode",
    "missing_source_bundle",
    "missing_source_bundle_sha256",
    "missing_source_bundle_file",
    "mismatched_source_bundle_sha256",
    "malformed_source_bundle",
    "unknown_phase_id",
    "missing_protected_source_entries",
    "missing_protected_source_file",
    "mismatched_protected_source_sha256",
)
PHASE_SOURCE_BUNDLE_SCHEMA = "phase-source-bundle.v1"
PIPELINE_CLOSEOUT_SCHEMA = "phase_loop_closeout.v1"
PIPELINE_CLOSEOUT_OUTCOMES = (
    "complete",
    "blocked",
    "stale_input",
    "failed_verification",
    "human_required",
)
CHANGED_PATH_CATEGORIES = (
    "code",
    "tests",
    "docs",
    "specs",
    "active_canonical_spec",
    "managed_root_mirror_spec",
    "mirror_manifest",
    "archive_manifest",
    "archived_spec",
    "unmanaged_spec",
    "pipeline_sources",
    "portal_contract_refs",
    "greenfield_authority_refs",
    "unknown",
)
CANONICAL_REFRESH_REASON_CODES = (
    "docs_source_truth_touched",
    "specs_source_truth_touched",
    "active_specs_touched",
    "managed_mirror_specs_touched",
    "mirror_manifests_touched",
    "archive_manifests_touched",
    "archived_specs_touched",
    "unmanaged_specs_touched",
    "adoption_contracts_touched",
    "contract_refs_touched",
    "pipeline_sources_touched",
    "portal_contract_refs_touched",
    "greenfield_authority_refs_touched",
)
REDACTION_POSTURES = ("metadata_only", "rejected_forbidden_metadata")
SPEC_DELTA_CLOSEOUT_SCHEMA = "spec_delta_closeout.v1"
SPEC_DELTA_DECISIONS = (
    "no_spec_delta",
    "roadmap_amendment",
    "canonical_spec_update",
    "governed_pipeline_refresh",
    "mirror_cutover_required",
    "dotfiles_skill_source_update",
    "human_source_judgment_required",
)
SPEC_DELTA_TARGET_SURFACES = (
    "shared/phase-loop/protocol.md",
    "vendor/phase-loop-runtime/protocol/protocol.md",
    "vendor/phase-loop-runtime/src/phase_loop_runtime/baml_src/emit_phase_closeout.baml",
    "codex-config/skills/**",
    "claude-config/claude-skills/**",
    "gemini-config/skills/**",
    "opencode-config/skills/**",
    "vendor/phase-loop-skills/**",
    "vendor/phase-loop-runtime/tests/**",
)

# --- rigor-v1 P1: shared review-gate contracts -----------------------------
# One canonical term for a phase's definition of done, replacing the fragmented
# "Acceptance criteria" / "Exit criteria" vocabulary across skills/validators.
DEFINITION_OF_DONE_TERM = "acceptance_criteria"

# doc_delta_closeout.v1 — code↔doc currency decision, mirroring spec_delta.
DOC_DELTA_CLOSEOUT_SCHEMA = "doc_delta_closeout.v1"
DOC_DELTA_DECISIONS = (
    "no_doc_delta",          # public surface unchanged, or change needs no docs
    "docs_updated",          # docs updated to match the change
    "docs_follow_up_filed",  # tracked for a later phase
)

# Globs whose change implies a user-visible public surface that docs may track.
PUBLIC_SURFACE_GLOBS = (
    "**/cli.py",
    "**/*.proto",
    "**/openapi*.json",
    "**/openapi*.yaml",
    "**/*.openapi.*",
    "**/schema*.json",
    "README.md",
    "**/README.md",
    "CHANGELOG.md",
    "CHANGELOG*",
    "**/CHANGELOG.md",
    "RELEASE_NOTES*",
    "RELEASES*",
    "**/_contract_docs/**",
)

# Verification-evidence policy: OFF by default (warn-only) to preserve autonomy;
# opt-in promotes a missing-evidence finding to `block`. Declining evidence when
# opted in records one of these typed reason codes.
VERIFICATION_EVIDENCE_REQUIRED_DEFAULT = False
VERIFICATION_EVIDENCE_OPT_OUT_REASONS = (
    "no_executable_verification",
    "verification_deferred_to_later_phase",
    "operator_attested_manual",
)

# UI/visual surfaces: a change here means a screenshot/visual check is *expected*
# (absence is a `warn` finding by default).
UI_GLOBS = (
    "**/*.tsx",
    "**/*.jsx",
    "**/*.vue",
    "**/*.svelte",
    "**/*.css",
    "**/*.scss",
    "**/components/**",
)

# FAV (issue #91): visual-avatar/browser-media closeout evidence. OFF by
# default (warn-only) to preserve autonomy, opt-in promotes a missing/blank
# visual-evidence finding to `block`. Declining evidence when opted in
# records one of these typed reason codes -- mirrors
# VERIFICATION_EVIDENCE_OPT_OUT_REASONS above.
VISUAL_EVIDENCE_OPT_OUT_REASONS = (
    "no_visible_media_surface",
    "visual_deferred_to_later_phase",
    "operator_attested_manual",
)

TERMINAL_SUMMARY_FIELDS = (
    "terminal_status",
    "terminal_blocker",
    "verification_status",
    "next_action",
    "dirty_paths",
    "phase_owned_dirty",
    "phase_owned_dirty_paths",
    "previous_phase_owned_paths",
    "unowned_dirty_paths",
    "pre_existing_dirty_paths",
    "artifact_paths",
    # FAV (issue #91) Fix 1: visual-avatar evidence must SURVIVE from the native
    # closeout into the terminal summary the closeout validator inspects.
    # Whitelisted here (and emitted only when populated -- see
    # observability.build_terminal_summary) so they are no longer silently
    # discarded in the real runner flow.
    "visual_evidence_path",
    "visual_evidence_observed",
    "visual_evidence_opt_out",
)

from .baml_modular import export_function_schema


CLOSEOUT_SCHEMA: dict[str, Any] = export_function_schema("EmitPhaseCloseout")

INJECTION_MODES = ("prompt_only", "inline", "stdin", "context_file", "manual")
PRODUCT_LOOP_ACTIONS = ("roadmap", "plan", "execute", "repair", "review", "maintain-skills")
COMMAND_ADAPTER_SUPPORTED_ACTIONS = ("roadmap", "plan", "execute", "repair", "review")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def require_literal(value: str, allowed: tuple[str, ...], label: str) -> str:
    if value not in allowed:
        raise ValueError(f"invalid {label}: {value}")
    return value


# --- protected_source_category vocabulary: sourced from the contract SoT -------
#
# The coarse `protected_source_category` enum is owned by consiliency-contract
# (>= 0.6.5), registry `protected_source_categories`. We read it through the same
# `consiliency_contract.load_registry` loader the git-discipline / consiliency
# gates use, mirroring the lazy-cached `available()` / contract-absent degrade
# pattern in `gate_posture.py`. When the registry is present it is AUTHORITATIVE;
# when the installed contract predates it (no registry / load error) we degrade
# to the legacy six-tuple rather than hard-crashing at import time.
_PROTECTED_CATEGORY_CACHE: tuple[str, ...] | None = None
_PROTECTED_CATEGORY_REGISTRY_PRESENT = False
_PROTECTED_CATEGORY_LOADED = False
_PROTECTED_FINE_SUBTYPES_CACHE: tuple[str, ...] = ()


def _load_protected_source_registry() -> None:
    global _PROTECTED_CATEGORY_CACHE, _PROTECTED_CATEGORY_REGISTRY_PRESENT
    global _PROTECTED_CATEGORY_LOADED, _PROTECTED_FINE_SUBTYPES_CACHE
    if _PROTECTED_CATEGORY_LOADED:
        return
    coarse: tuple[str, ...] | None = None
    fine: tuple[str, ...] = ()
    try:
        from consiliency_contract import load_registry

        reg = load_registry("protected_source_categories")
        entries = reg.get("coarse_categories") if isinstance(reg, dict) else None
        if isinstance(entries, list):
            ids = tuple(
                str(item["id"])
                for item in entries
                if isinstance(item, dict) and item.get("id")
            )
            if ids:
                coarse = ids
        raw_fine = reg.get("fine_subtypes") if isinstance(reg, dict) else None
        if isinstance(raw_fine, list):
            fine = tuple(str(x) for x in raw_fine if isinstance(x, str))
    except Exception:
        # Installed contract predates the registry (< 0.6.5) or read failed:
        # degrade to the legacy tuple. Not a hard crash -- see gate_posture.py.
        coarse = None
    _PROTECTED_CATEGORY_REGISTRY_PRESENT = coarse is not None
    _PROTECTED_CATEGORY_CACHE = coarse if coarse is not None else PIPELINE_PROTECTED_SOURCE_CATEGORIES
    _PROTECTED_FINE_SUBTYPES_CACHE = fine
    _PROTECTED_CATEGORY_LOADED = True


def _reset_protected_source_registry_cache() -> None:
    """Test-only: clear the memoized registry read (present/absent isolation)."""
    global _PROTECTED_CATEGORY_CACHE, _PROTECTED_CATEGORY_REGISTRY_PRESENT
    global _PROTECTED_CATEGORY_LOADED, _PROTECTED_FINE_SUBTYPES_CACHE
    _PROTECTED_CATEGORY_CACHE = None
    _PROTECTED_CATEGORY_REGISTRY_PRESENT = False
    _PROTECTED_CATEGORY_LOADED = False
    _PROTECTED_FINE_SUBTYPES_CACHE = ()


def protected_source_categories() -> tuple[str, ...]:
    """The authoritative coarse `protected_source_category` enum.

    Prefers the distributed contract registry (>= 0.6.5); falls back to the
    legacy six-tuple when the contract lacks the registry.
    """
    _load_protected_source_registry()
    assert _PROTECTED_CATEGORY_CACHE is not None  # set by loader
    return _PROTECTED_CATEGORY_CACHE


def protected_source_category_registry_available() -> bool:
    """True when the installed contract ships the coarse-category registry."""
    _load_protected_source_registry()
    return _PROTECTED_CATEGORY_REGISTRY_PRESENT


def protected_source_fine_subtypes() -> tuple[str, ...]:
    """Registered fine `subtype` names (soft/warn signal only, never a gate)."""
    _load_protected_source_registry()
    return _PROTECTED_FINE_SUBTYPES_CACHE


@dataclass(frozen=True)
class ModelSelection:
    profile: str
    model: str
    effort: str
    source: str = "default"
    override_reason: str | None = None
    # model-routing-v1 P4: the resolved role (planner/implementer/worker) when a
    # model_class policy chose the model; None on the legacy/empty-policy path.
    model_class: str | None = None

    def __post_init__(self) -> None:
        require_literal(self.profile, MODEL_PROFILES, "model profile")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


@dataclass(frozen=True)
class CloseoutException:
    """A recorded, visible exception to the ownership gate (roadmap v40).

    Frozen by PROTO so GATE (soft) and BREAKGLASS (break_glass) construct the same
    record. Recorded under the ``closeout.<CLOSEOUT_EXCEPTIONS_METADATA_KEY>``
    metadata key, never counted as a clean pass.
    """

    paths: tuple[str, ...]
    exception_kind: str
    sensitivity_class: str
    reason: str | None = None
    verification_status: str = "passed"

    def __post_init__(self) -> None:
        require_literal(self.exception_kind, CLOSEOUT_EXCEPTION_KINDS, "closeout exception kind")
        require_literal(self.sensitivity_class, SENSITIVITY_CLASSES, "sensitivity class")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


@dataclass(frozen=True)
class PromptBundle:
    workflow_command: str
    body: str
    injection_mode: str
    context_body: str | None = None
    expected_skill_pack: tuple[str, ...] = ()
    product_action: str | None = None
    skill_bundle_id: str | None = None
    skill_bundle_sha256: str | None = None
    fallback_mode: str | None = None
    context_path: str | None = None
    recommended_installed_roots: tuple[str, ...] = ()
    installed_skill_roots: tuple[str, ...] = ()
    installed_skill_warnings: tuple[str, ...] = ()
    bridge_skill_inventory: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        require_literal(self.injection_mode, INJECTION_MODES, "injection mode")
        if self.product_action is not None:
            require_literal(self.product_action, PRODUCT_LOOP_ACTIONS, "product action")

    def render_prompt(self) -> str:
        body = self.body.strip()
        if not body:
            return self.workflow_command
        return f"{self.workflow_command}\n\n{body}"

    def render_context(self) -> str:
        body = (self.context_body if self.context_body is not None else self.body).strip()
        if not body:
            return self.workflow_command
        return f"{self.workflow_command}\n\n{body}"

    def body_sha256(self) -> str:
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()

    def context_sha256(self) -> str:
        return hashlib.sha256(self.render_context().encode("utf-8")).hexdigest()

    def body_line_count(self) -> int:
        return len(self.body.splitlines())

    def body_char_count(self) -> int:
        return len(self.body)

    def context_line_count(self) -> int:
        return len(self.render_context().splitlines())

    def context_char_count(self) -> int:
        return len(self.render_context())

    def to_json(self) -> dict[str, Any]:
        return clean_dict(
            {
                "workflow_command": self.workflow_command,
                "injection_mode": self.injection_mode,
                "context_sha256": self.context_sha256(),
                "expected_skill_pack": self.expected_skill_pack,
                "product_action": self.product_action,
                "skill_bundle_id": self.skill_bundle_id,
                "skill_bundle_sha256": self.skill_bundle_sha256,
                "fallback_mode": self.fallback_mode,
                "context_path": self.context_path,
                "recommended_installed_roots": self.recommended_installed_roots,
                "installed_skill_roots": self.installed_skill_roots,
                "installed_skill_warnings": self.installed_skill_warnings,
                "bridge_skill_inventory": self.bridge_skill_inventory,
                "body_sha256": self.body_sha256(),
                "body_line_count": self.body_line_count(),
                "body_char_count": self.body_char_count(),
                "context_line_count": self.context_line_count(),
                "context_char_count": self.context_char_count(),
            }
        )


@dataclass(frozen=True)
class InjectionMetadata:
    harness_target: str
    injection_mode: str
    context_sha256: str | None = None
    context_line_count: int | None = None
    context_char_count: int | None = None
    expected_skill_pack: tuple[str, ...] = ()
    skill_bundle_id: str | None = None
    skill_bundle_sha256: str | None = None
    context_path: str | None = None
    fallback_mode: str | None = None
    recommended_installed_roots: tuple[str, ...] = ()
    installed_skill_roots: tuple[str, ...] = ()
    installed_skill_warnings: tuple[str, ...] = ()
    bridge_skill_inventory: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        require_literal(self.harness_target, EXECUTORS, "harness target")
        require_literal(self.injection_mode, INJECTION_MODES, "injection mode")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


@dataclass(frozen=True)
class DispatchHints:
    preferred_executors: tuple[str, ...] = ()
    allowed_executors: tuple[str, ...] = ()
    fallback_executors: tuple[str, ...] = ()
    disabled_executors: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    source: str = "default"
    action: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "preferred_executors",
            "allowed_executors",
            "fallback_executors",
            "disabled_executors",
        ):
            values = getattr(self, field_name)
            for value in values:
                require_literal(value, EXECUTORS, field_name)
        for capability in self.required_capabilities:
            require_literal(capability, DISPATCH_CAPABILITIES, "required capability")
        if self.action is not None:
            require_literal(self.action, PRODUCT_LOOP_ACTIONS, "product action")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))

    def is_empty(self) -> bool:
        return not any(getattr(self, field_name) for field_name in DISPATCH_HINT_FIELDS)


@dataclass(frozen=True)
class ProviderPolicyCapability:
    provider: str
    executor: str
    supported_work_units: tuple[str, ...]
    supported_efforts: tuple[str, ...]
    unsupported_policy_behavior: str = "block"
    named_fallback: str | None = None
    default_effort: str | None = None
    effort_map: dict[str, str] = field(default_factory=dict)
    model_aliases: dict[str, str] = field(default_factory=dict)
    requires_run_local_user_scope: bool = False
    # ah#231: eligibility to be the max-effort PLANNER OF RECORD, decoupled from run-level
    # effort translation. `None` (the default) means "derive from supported_efforts" —
    # i.e. `"max" in supported_efforts` — preserving the historical behavior for every
    # provider that does not set it. A provider sets this explicitly to break that coupling:
    # grok keeps a broad `supported_efforts` (so an explicit `max` stays a valid, CLI-clamped
    # request) yet declares `planner_max_class=False` so it is not represented as a max-effort
    # planner. See `profiles.max_effort_planner_eligible`.
    planner_max_class: bool | None = None
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_literal(self.executor, EXECUTORS, "executor")
        for work_unit in self.supported_work_units:
            require_literal(work_unit, WORK_UNIT_KINDS, "work-unit kind")
        for effort in self.supported_efforts:
            require_literal(effort, NORMALIZED_EFFORT_LEVELS, "normalized effort")
        require_literal(self.unsupported_policy_behavior, UNSUPPORTED_POLICY_BEHAVIORS, "unsupported policy behavior")
        if self.default_effort is not None:
            require_literal(self.default_effort, NORMALIZED_EFFORT_LEVELS, "default effort")
        for effort in self.effort_map:
            require_literal(effort, NORMALIZED_EFFORT_LEVELS, "effort map key")
            require_literal(self.effort_map[effort], NORMALIZED_EFFORT_LEVELS, "effort map value")
        for work_unit in self.model_aliases:
            require_literal(work_unit, WORK_UNIT_KINDS, "model alias work-unit kind")
        if self.unsupported_policy_behavior == "fallback" and not self.named_fallback:
            raise ValueError("unsupported fallback policy requires named_fallback")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


@dataclass(frozen=True)
class WorkUnitPolicy:
    work_unit_kind: str
    effort: str | None = None
    unsupported_policy_behavior: str = "block"
    fallback: str | None = None
    inherit_default: bool = False

    def __post_init__(self) -> None:
        require_literal(self.work_unit_kind, WORK_UNIT_KINDS, "work-unit kind")
        if self.effort is not None:
            require_literal(self.effort, NORMALIZED_EFFORT_LEVELS, "normalized effort")
        require_literal(self.unsupported_policy_behavior, UNSUPPORTED_POLICY_BEHAVIORS, "unsupported policy behavior")
        if self.unsupported_policy_behavior == "fallback" and not self.fallback:
            raise ValueError("unsupported fallback policy requires fallback")
        if self.unsupported_policy_behavior == "inherit_default" and not self.inherit_default:
            raise ValueError("inherit_default policy must set inherit_default")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


@dataclass(frozen=True)
class ExecutionPolicyRule:
    selector: str = "default"
    action: str | None = None
    lane: str | None = None
    executor: str | None = None
    model: str | None = None
    model_class: str | None = None
    effort: str | None = None
    work_unit_kind: str | None = None
    unsupported_policy_behavior: str = "block"
    fallback: str | None = None
    inherit_default: bool = False
    source: str = "default"
    override_reason: str | None = None

    def __post_init__(self) -> None:
        if self.action is not None:
            require_literal(self.action, PRODUCT_LOOP_ACTIONS, "execution policy action")
        if self.executor is not None:
            require_literal(self.executor, EXECUTORS, "execution policy executor")
        if self.model_class is not None:
            require_literal(self.model_class, MODEL_CLASSES, "execution policy model class")
        if self.effort is not None:
            require_literal(self.effort, NORMALIZED_EFFORT_LEVELS, "execution policy effort")
        if self.work_unit_kind is not None:
            require_literal(self.work_unit_kind, WORK_UNIT_KINDS, "execution policy work-unit kind")
        require_literal(self.unsupported_policy_behavior, UNSUPPORTED_POLICY_BEHAVIORS, "unsupported policy behavior")
        if self.model is not None and not self.model.strip():
            raise ValueError("execution policy model must not be empty")
        if self.unsupported_policy_behavior == "fallback" and not self.fallback:
            raise ValueError("execution policy fallback requires fallback")
        if self.unsupported_policy_behavior == "inherit_default" and not self.inherit_default:
            raise ValueError("execution policy inherit_default requires inherit_default")

    def work_unit_policy(self) -> WorkUnitPolicy | None:
        if self.work_unit_kind is None:
            return None
        return WorkUnitPolicy(
            work_unit_kind=self.work_unit_kind,
            effort=self.effort,
            unsupported_policy_behavior=self.unsupported_policy_behavior,
            fallback=self.fallback,
            inherit_default=self.inherit_default,
        )

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


@dataclass(frozen=True)
class ExecutionPolicyParseError:
    path: str
    line_number: int
    raw_line: str
    detail: str

    def to_json(self) -> dict[str, Any]:
        return {"path": self.path, "line_number": self.line_number, "raw_line": self.raw_line, "detail": self.detail}


@dataclass(frozen=True)
class ExecutionPolicyDocument:
    rules: tuple[ExecutionPolicyRule, ...] = ()
    source: str = "default"
    parse_error: ExecutionPolicyParseError | None = None

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"source": self.source, "rules": tuple(rule.to_json() for rule in self.rules)}
        if self.parse_error is not None:
            payload["parse_error"] = self.parse_error.to_json()
        return clean_dict(payload)

    def is_empty(self) -> bool:
        return not self.rules


@dataclass(frozen=True)
class PipelinePlanMetadata:
    source_bundle: str | None = None
    source_bundle_sha256: str | None = None
    pipeline_phase_id: str | None = None
    pipeline_mode: str | None = None

    def __post_init__(self) -> None:
        if self.pipeline_mode is not None:
            require_literal(self.pipeline_mode, PIPELINE_MODE_LITERALS, "pipeline mode")

    @property
    def required(self) -> bool:
        return self.pipeline_mode == "pipeline_required"

    @property
    def empty(self) -> bool:
        return not any((self.source_bundle, self.source_bundle_sha256, self.pipeline_phase_id, self.pipeline_mode))

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


@dataclass(frozen=True)
class PipelineProtectedSource:
    path: str
    category: str
    sha256: str | None = None
    role: str | None = None
    # PSCAT-PL: the producer's OPTIONAL fine governance granularity, carried
    # alongside the coarse `category`. Free-form by contract design (the fine
    # vocabulary is deliberately NOT re-coupled across repos) -- accepted as any
    # string and never enum-gated. `protected_source_fine_subtypes()` is a soft
    # reference set for tooling only, never a hard reject.
    subtype: str | None = None

    def __post_init__(self) -> None:
        if not self.path.strip():
            raise ValueError("protected source path must not be empty")
        # Validate the coarse category against the contract-owned enum
        # (registry-authoritative >= 0.6.5; legacy six-tuple when absent).
        require_literal(self.category, protected_source_categories(), "protected source category")
        if self.role is not None and self.role not in PIPELINE_PROTECTED_SOURCE_LEGACY_ROLES:
            require_literal(self.role, PIPELINE_PROTECTED_SOURCE_ROLES, "protected source role")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


@dataclass(frozen=True)
class PipelineMetadataDiagnostic:
    kind: str
    message: str
    metadata: PipelinePlanMetadata | None = None
    expected_sha256: str | None = None
    actual_sha256: str | None = None
    blocker_class: str = "contract_bug"
    human_required: bool = False

    def __post_init__(self) -> None:
        require_literal(self.kind, PIPELINE_METADATA_DIAGNOSTIC_KINDS, "pipeline metadata diagnostic")
        require_literal(self.blocker_class, BLOCKER_CLASSES, "blocker class")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(
            {
                "kind": self.kind,
                "message": self.message,
                "metadata": self.metadata.to_json() if self.metadata else None,
                "expected_sha256": self.expected_sha256,
                "actual_sha256": self.actual_sha256,
                "blocker_class": self.blocker_class,
                "human_required": self.human_required,
            }
        )


@dataclass(frozen=True)
class PhaseSourceBundle:
    path: str
    sha256: str
    phase_id: str
    phase_alias: str
    phase_plan_path: str
    roadmap_path: str
    roadmap_sha256: str
    protected_sources: tuple[PipelineProtectedSource, ...]
    delegated_write_policy: dict[str, Any] = field(default_factory=dict)
    source_files: tuple[dict[str, Any], ...] = ()
    artifact_target_root: str | None = None
    freshness: dict[str, Any] = field(default_factory=dict)
    pipeline_mode: str = "pipeline_optional"

    def __post_init__(self) -> None:
        if not self.path.strip():
            raise ValueError("source bundle path must not be empty")
        if not self.sha256.strip():
            raise ValueError("source bundle sha256 must not be empty")
        if not self.phase_id.strip():
            raise ValueError("source bundle phase_id must not be empty")
        if not self.phase_alias.strip():
            raise ValueError("source bundle phase_alias must not be empty")
        require_literal(self.pipeline_mode, PIPELINE_MODE_LITERALS, "pipeline mode")

    def plan_metadata(self) -> PipelinePlanMetadata:
        return PipelinePlanMetadata(
            source_bundle=self.path,
            source_bundle_sha256=self.sha256,
            pipeline_phase_id=self.phase_id,
            pipeline_mode=self.pipeline_mode,
        )

    def to_json(self) -> dict[str, Any]:
        return clean_dict(
            {
                "path": self.path,
                "sha256": self.sha256,
                "phase_id": self.phase_id,
                "phase_alias": self.phase_alias,
                "phase_plan_path": self.phase_plan_path,
                "roadmap_path": self.roadmap_path,
                "roadmap_sha256": self.roadmap_sha256,
                "protected_sources": tuple(source.to_json() for source in self.protected_sources),
                "delegated_write_policy": self.delegated_write_policy,
                "source_files": self.source_files,
                "artifact_target_root": self.artifact_target_root,
                "freshness": self.freshness,
                "pipeline_mode": self.pipeline_mode,
            }
        )


@dataclass(frozen=True)
class PhaseLoopAutomation:
    status: str
    next_skill: str | None = None
    next_command: str | None = None
    next_model_hint: str | None = None
    next_effort_hint: str | None = None
    human_required: bool = False
    blocker_class: str | None = None
    blocker_summary: str | None = None
    required_human_inputs: tuple[str, ...] = ()
    verification_status: str = "not_run"
    artifact: str | None = None
    artifact_state: str | None = None

    def __post_init__(self) -> None:
        require_literal(self.status, PHASE_STATUSES, "automation status")
        if self.blocker_class is not None:
            require_literal(self.blocker_class, BLOCKER_CLASSES, "automation blocker class")
        if self.next_effort_hint is not None:
            require_literal(self.next_effort_hint, NORMALIZED_EFFORT_LEVELS, "automation effort hint")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


@dataclass(frozen=True)
class PhaseLoopArtifacts:
    plan_path: str
    plan_sha256: str
    artifact_paths: dict[str, str] = field(default_factory=dict)
    changed_paths: tuple[str, ...] = ()
    evidence_refs: tuple[dict[str, Any], ...] = ()

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


@dataclass(frozen=True)
class PhaseLoopVerification:
    status: str
    commands: tuple[str, ...] = ()
    results: tuple[dict[str, Any], ...] = ()
    agent_reported_verification_status: str | None = None

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


@dataclass(frozen=True)
class PhaseLoopBlocker:
    human_required: bool = False
    blocker_class: str | None = None
    blocker_summary: str | None = None
    required_human_inputs: tuple[str, ...] = ()
    access_attempts: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.blocker_class is not None:
            require_literal(self.blocker_class, BLOCKER_CLASSES, "blocker class")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


@dataclass(frozen=True)
class PhaseLoopSourceBundle:
    path: str | None = None
    sha256: str | None = None
    phase_id: str | None = None
    pipeline_mode: str = "standalone"
    protected_sources: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        require_literal(self.pipeline_mode, PIPELINE_MODE_LITERALS, "pipeline mode")

    def to_json(self) -> dict[str, Any]:
        data = {
            "path": self.path,
            "sha256": self.sha256,
            "phase_id": self.phase_id,
            "pipeline_mode": self.pipeline_mode,
            "protected_sources": self.protected_sources or None,
        }
        return clean_dict(data)


@dataclass(frozen=True)
class SourceTruthImpact:
    changed_path_boundaries: tuple[dict[str, str], ...] = ()
    canonical_refresh_recommended: bool = False
    canonical_refresh_reason_codes: tuple[str, ...] = ()
    redaction_posture: str = "metadata_only"

    def __post_init__(self) -> None:
        require_literal(self.redaction_posture, REDACTION_POSTURES, "redaction posture")
        for boundary in self.changed_path_boundaries:
            require_literal(str(boundary.get("category")), CHANGED_PATH_CATEGORIES, "changed path category")
        for reason in self.canonical_refresh_reason_codes:
            require_literal(reason, CANONICAL_REFRESH_REASON_CODES, "canonical refresh reason code")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


@dataclass(frozen=True)
class SpecDeltaCloseout:
    decision: str
    target_surfaces: tuple[str, ...] = ()
    evidence_paths: tuple[str, ...] = ()
    redaction_posture: str = "metadata_only"
    blocker_class: str | None = None
    schema: str = SPEC_DELTA_CLOSEOUT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != SPEC_DELTA_CLOSEOUT_SCHEMA:
            raise ValueError(f"invalid spec delta closeout schema: {self.schema}")
        require_literal(self.decision, SPEC_DELTA_DECISIONS, "spec delta decision")
        require_literal(self.redaction_posture, REDACTION_POSTURES, "redaction posture")
        if self.blocker_class is not None:
            require_literal(self.blocker_class, BLOCKER_CLASSES, "blocker class")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


VISUAL_EVIDENCE_OBSERVED_SCHEMA = "visual_evidence_observed.v1"


@dataclass(frozen=True)
class VisualEvidenceObservation:
    """``visual_evidence_observed.v1`` (FAV, issue #91) -- automated pixel-level
    observations attached to a runner-owned visual artifact (screenshot/video
    path, recorded separately as ``visual_evidence_path``) for an
    avatar/browser-media phase. Strong enough to reject a black or
    uniform-gray frame masquerading as passing visual evidence: a genuine
    frame has at least one non-black pixel AND some pixel variance (min !=
    max). An all-black frame (``non_black_pixels == 0``) or a uniform frame
    (``pixel_min == pixel_max``, e.g. a solid ``#f3f3f3`` gray with
    ``pixelMin == pixelMax == 243``) both FAIL ``is_valid()``.
    """

    non_black_pixels: int
    pixel_min: int
    pixel_max: int
    schema: str = VISUAL_EVIDENCE_OBSERVED_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != VISUAL_EVIDENCE_OBSERVED_SCHEMA:
            raise ValueError(f"invalid visual evidence observation schema: {self.schema}")
        # Fix 4 (agent-harness#91 CR): a well-formed observation must carry
        # IN-RANGE integer pixel values -- non_black_pixels is a non-negative
        # PIXEL COUNT (no fixed upper bound), pixel_min/pixel_max are 8-bit
        # channel intensities (0..255). Out-of-range values (e.g. a negative
        # count or pixel_max=300) are malformed, not merely a black/blank
        # frame, so they raise here and `from_mapping` treats them identically
        # to "no evidence attached" (its except clause already catches
        # ValueError).
        if self.non_black_pixels < 0:
            raise ValueError(f"invalid non_black_pixels (must be >= 0): {self.non_black_pixels}")
        if not (0 <= self.pixel_min <= 255):
            raise ValueError(f"invalid pixel_min (must be 0..255): {self.pixel_min}")
        if not (0 <= self.pixel_max <= 255):
            raise ValueError(f"invalid pixel_max (must be 0..255): {self.pixel_max}")
        # agent-harness#91 round-2 (codex Finding 3): an impossible observation
        # (pixel_min > pixel_max) is malformed the same way an out-of-range value
        # is -- e.g. {"nonBlackPixels": 1, "pixelMin": 0, "pixelMax": ...} with the
        # min/max swapped can't describe any real frame. Reject it here so
        # `from_mapping`'s catch-all treats it identically to "no evidence
        # attached", instead of `is_valid()` silently accepting a self-inconsistent
        # observation because `pixel_min != pixel_max` still (accidentally) holds.
        if self.pixel_min > self.pixel_max:
            raise ValueError(
                f"invalid pixel_min/pixel_max (min must be <= max): {self.pixel_min} > {self.pixel_max}"
            )

    def is_valid(self) -> bool:
        """True iff the frame shows real, non-uniform content (not black/blank)."""
        if self.non_black_pixels is None or self.non_black_pixels <= 0:
            return False
        if self.pixel_min == self.pixel_max:
            return False
        return True

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))

    @classmethod
    def from_mapping(cls, data: Any) -> "VisualEvidenceObservation | None":
        """Tolerant parse: accepts either the runtime's snake_case keys
        (``non_black_pixels``/``pixel_min``/``pixel_max``) or the camelCase
        keys a browser-automation tool naturally emits
        (``nonBlackPixels``/``pixelMin``/``pixelMax``). Returns ``None`` (never
        raises) on anything malformed or absent -- the caller treats that
        identically to "no evidence attached"."""
        if not isinstance(data, Mapping):
            return None
        non_black = data.get("non_black_pixels", data.get("nonBlackPixels"))
        pixel_min = data.get("pixel_min", data.get("pixelMin"))
        pixel_max = data.get("pixel_max", data.get("pixelMax"))
        if non_black is None or pixel_min is None or pixel_max is None:
            return None
        try:
            return cls(non_black_pixels=int(non_black), pixel_min=int(pixel_min), pixel_max=int(pixel_max))
        except (TypeError, ValueError):
            return None



# agent-harness#91 round-2 (codex Finding 3): magic-number headers for the
# image formats a visual-evidence screenshot/frame artifact is realistically
# encoded as. This is a FLOOR, not full pixel-decoding (no image-library
# dependency is available here) -- it rejects a directory, an empty file, and
# a plain-text file merely renamed to ``.png`` (the exact codex probe:
# ``write_text("png")`` at a ``.png`` path), which the pre-existing
# exists()-only check accepted as valid evidence. True decode/derive
# (verifying the observation actually matches the pixel data) is a follow-up;
# tracked as a known gap, not silently claimed here.
_IMAGE_MAGIC_HEADERS: tuple[bytes, ...] = (
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"\xff\xd8\xff",  # JPEG
    b"GIF87a",  # GIF
    b"GIF89a",  # GIF
    b"BM",  # BMP
    # WEBP: RIFF????WEBP -- checked specially below (variable-length RIFF size).
)


def _has_valid_image_header(path: Path) -> bool:
    """True iff ``path`` starts with a recognized image magic-number header."""
    try:
        with open(path, "rb") as handle:
            header = handle.read(12)
    except OSError:
        return False
    if not header:
        return False
    if any(header.startswith(magic) for magic in _IMAGE_MAGIC_HEADERS):
        return True
    # WEBP: 4-byte "RIFF", 4-byte little-endian chunk size, then "WEBP".
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return True
    return False


def resolve_visual_evidence_artifact(repo_root: "str | Path | None", path_str: "str | None") -> "Path | None":
    """Fix 4 (agent-harness#91 CR) + Fix 3 round-2 (codex): a runner-owned
    visual-evidence artifact must EXIST, be CONTAINED inside the repo,
    resolve to a REGULAR FILE (not a directory/symlink-to-dir/etc.), be
    non-empty, and start with a recognized image magic-number header --
    mirrors the #238 breakglass path-safety posture
    (``cli._validate_reconcile_verification_log``): fail closed on anything
    that can't be proven both inside the repo and a genuine image file on
    disk. Without the regular-file + magic-number checks, ``visual_evidence_
    path="."`` (the repo directory itself) or a plain-text file renamed to
    ``.png`` both passed as "valid" evidence -- codex's Finding 3 probe.

    ``repo_root=None`` means the caller could not determine a repo root at
    all -- containment can never be proven, so every candidate fails closed.
    An absolute path is resolved as-is (still must land inside ``repo_root``);
    a relative path is resolved against ``repo_root``. Returns the resolved
    ``Path`` when valid, else ``None`` -- callers treat ``None`` identically
    to "no evidence attached"."""
    if not path_str or repo_root is None:
        return None
    root = Path(repo_root).resolve()
    candidate = Path(path_str)
    resolved = candidate if candidate.is_absolute() else root / candidate
    try:
        resolved = resolved.resolve()
    except OSError:
        return None
    try:
        inside = resolved.is_relative_to(root)
    except AttributeError:  # pragma: no cover - py3.8 compatibility fallback
        inside = str(resolved).startswith(str(root) + os.sep)
    if not inside:
        return None
    try:
        if not resolved.is_file():
            return None  # rejects a directory (e.g. path="."), symlink-to-dir, etc.
        if resolved.stat().st_size <= 0:
            return None  # rejects an empty file
    except OSError:
        return None
    if not _has_valid_image_header(resolved):
        return None  # rejects a non-image / text-renamed-.png
    return resolved


# agent-harness#91 round-3 (codex CR): the magic-header check above is a
# FLOOR (rejects a directory / empty file / text-renamed-.png), not a full
# decode -- it does NOT prove the referenced artifact is a genuine,
# non-blank image. Pairing that floor with SELF-REPORTED pixel observations
# (`VisualEvidenceObservation` taken verbatim from the agent's terminal
# summary/CLI flags) reopens the exact hole #91 exists to close: a
# valid-header 24-byte "PNG signature + zeros" file, paired with a
# fabricated `{"nonBlackPixels": 19200, "pixelMin": 0, "pixelMax": 255}`,
# passes the gate with no relationship to the actual pixel data (codex's
# round-3 probe). `derive_visual_observation` below closes that hole by
# DECODING the artifact and computing the observation from the real pixels;
# it is the AUTHORITATIVE source of truth wherever it can run at all --
# callers must never let a self-reported observation override a derived
# one, and must FAIL CLOSED (never silently accept self-reported numbers)
# when derivation itself is impossible.
_DERIVED_NON_BLACK_LUMINANCE_THRESHOLD = 8  # 0..255 grayscale; tolerates lossy-compression noise near true black


class VisualEvidenceDerivationError(Exception):
    """Base class: raised by `derive_visual_observation` when the referenced
    artifact cannot be authoritatively verified. Callers MUST treat this as
    a hard failure of the evidence contract -- never catch it and quietly
    fall back to trusting the agent-supplied `visual_evidence_observed`,
    which would reopen the #91 hole this exception exists to surface."""

    #: stable finding code a caller can attach to a ReviewFinding/manual_repair
    #: field without re-deriving it from the exception message.
    code = "visual_evidence_undecodable"


class VisualEvidenceDecoderUnavailable(VisualEvidenceDerivationError):
    """Pillow is not installed in this environment -- derivation cannot run
    at all, so the artifact can NEVER be authoritatively verified here."""

    code = "visual_evidence_cannot_verify"


class VisualEvidenceUndecodable(VisualEvidenceDerivationError):
    """Pillow is available but the file at `path` could not be decoded as an
    image (corrupt/truncated body, zero dimensions, unrecognized format)."""

    code = "visual_evidence_undecodable"


def derive_visual_observation(path: "str | Path") -> VisualEvidenceObservation:
    """FAV (agent-harness#91 round-3 CR): decode the image at `path` and
    compute `non_black_pixels`/`pixel_min`/`pixel_max` from its ACTUAL
    pixels (grayscale/luminance), rather than trusting the agent's
    self-report. This is the AUTHORITATIVE observation -- a genuinely
    blank/uniform/all-black decoded image fails `VisualEvidenceObservation.
    is_valid()` even if the agent supplied fabricated "good" numbers.

    Uses Pillow (`PIL.Image`), imported LAZILY here -- Pillow is an OPTIONAL
    dependency (the `visual` extra in pyproject.toml), never a hard core
    dependency of phase-loop-runtime.

    Raises `VisualEvidenceDerivationError` (never returns `None`) when:
    - Pillow is not installed in this environment, or
    - the file at `path` cannot be opened/decoded as an image (corrupt,
      truncated, zero-dimension, or a format Pillow doesn't recognize).

    Callers decide how to fail closed for their posture (opt-in ``block`` ->
    BLOCK with a decoder-unavailable/undecodable finding code; warn-default
    -> record the finding, never silently treat it as "gate does not
    apply"). This function itself never silently passes.
    """
    try:
        from PIL import Image  # lazy import: optional `visual` extra (Pillow)
    except ImportError as exc:
        raise VisualEvidenceDecoderUnavailable(f"Pillow is not available to decode visual evidence: {exc}") from exc
    try:
        with Image.open(path) as img:
            img.load()  # force full decode now (Image.open is lazy) so a truncated/corrupt body raises here
            # Fix (agent-harness#91 round-4 CR / codex): converting straight to
            # grayscale IGNORES alpha, so a fully-transparent RGBA/LA/P-with-
            # transparency image with varied *hidden* RGB decodes as if those
            # hidden colors were visible -- non_black_pixels>0 and pixel_min !=
            # pixel_max, so is_valid() wrongly PASSES a visually blank frame
            # (fail-open). Composite onto a DETERMINISTIC opaque black RGBA
            # canvas first so the derived stats reflect what a viewer actually
            # SEES: fully-transparent pixels become black (matching the
            # existing "black == blank" rejection), partially-transparent
            # pixels blend toward black, and fully-opaque pixels are
            # unaffected.
            #
            # Fix (agent-harness#91 round-5 CR / codex): the round-4 check only
            # covered modes that carry an explicit alpha CHANNEL (RGBA/LA) or
            # palette transparency on mode P. Pillow also decodes a grayscale
            # or RGB PNG that carries a ``tRNS`` chunk as plain mode L/RGB,
            # surfacing the transparency purely via ``img.info["transparency"]``
            # -- no alpha channel at all. That bypassed compositing entirely: a
            # 1x1-visible / 1x1-"transparent-but-decoded-white" L/RGB tRNS image
            # read non_black_pixels>0 and passed is_valid() even though a viewer
            # sees only the opaque (black) pixels. Treat ANY mode carrying
            # ``img.info["transparency"]`` (L, RGB, or P) the SAME as an
            # explicit alpha channel: ``img.convert("RGBA")`` applies
            # palette/tRNS transparency into a real alpha channel for every one
            # of these modes, so routing them through the same composite path
            # is correct and uniform. Modes with neither an alpha channel NOR
            # transparency info (opaque RGB/L/...) are unaffected --
            # convert("RGBA") on those is already fully opaque.
            if img.mode in ("RGBA", "LA", "PA") or img.info.get("transparency") is not None:
                rgba = img.convert("RGBA")
                black_background = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
                composited = Image.alpha_composite(black_background, rgba)
                grayscale = composited.convert("L")
            else:
                grayscale = img.convert("L")
            width, height = grayscale.size
            if width <= 0 or height <= 0:
                raise VisualEvidenceUndecodable(f"decoded image has zero dimensions: {path}")
            pixel_min, pixel_max = grayscale.getextrema()
            histogram = grayscale.histogram()  # 256-bucket luminance histogram
            non_black_pixels = sum(histogram[_DERIVED_NON_BLACK_LUMINANCE_THRESHOLD + 1 :])
    except VisualEvidenceDerivationError:
        raise
    except Exception as exc:  # noqa: BLE001 -- Pillow raises varied types (UnidentifiedImageError/OSError/...)
        raise VisualEvidenceUndecodable(f"failed to decode visual evidence artifact at {path}: {exc}") from exc
    return VisualEvidenceObservation(
        non_black_pixels=int(non_black_pixels),
        pixel_min=int(pixel_min),
        pixel_max=int(pixel_max),
    )


def derive_visual_observation_or_error(path: "str | Path") -> "tuple[VisualEvidenceObservation | None, str | None]":
    """Convenience wrapper the three call sites (closeout validator, live
    runner, reconcile guard) share: `(observation, None)` on a successful
    decode, or `(None, error_code)` when derivation failed --
    ``"visual_evidence_cannot_verify"`` (Pillow unavailable) or
    ``"visual_evidence_undecodable"`` (image present but not decodable).
    Never raises -- centralizes the try/except so every caller applies the
    SAME fail-closed contract instead of re-implementing it."""
    try:
        return derive_visual_observation(path), None
    except VisualEvidenceDerivationError as exc:
        return None, exc.code


def visual_evidence_terminal_fields(payload: Any) -> dict[str, Any]:
    """FAV (issue #91) Fix 1: reconstruct the ``ctx.terminal``-shaped visual
    evidence view (``visual_evidence_path`` / ``visual_evidence_observed`` /
    ``visual_evidence_opt_out``) from a closeout payload that may carry the
    BAML-schema-friendly FLAT encoding
    (``visual_evidence_non_black_pixels``/``visual_evidence_pixel_min``/
    ``visual_evidence_pixel_max``) instead of a nested
    ``visual_evidence_observed`` mapping. Idempotent -- a payload that
    already carries the nested/short-form keys is returned unchanged for
    those keys, so this is safe to layer on top of any terminal-summary
    shape. Returns only the keys it can actually populate (never clobbers
    with ``None``)."""
    if not isinstance(payload, Mapping):
        return {}
    fields: dict[str, Any] = {}
    path = payload.get("visual_evidence_path")
    if path:
        fields["visual_evidence_path"] = path
    observed = payload.get("visual_evidence_observed")
    if isinstance(observed, Mapping):
        fields["visual_evidence_observed"] = dict(observed)
    else:
        non_black = payload.get("visual_evidence_non_black_pixels")
        pixel_min = payload.get("visual_evidence_pixel_min")
        pixel_max = payload.get("visual_evidence_pixel_max")
        if non_black is not None and pixel_min is not None and pixel_max is not None:
            fields["visual_evidence_observed"] = {
                "non_black_pixels": non_black,
                "pixel_min": pixel_min,
                "pixel_max": pixel_max,
            }
    opt_out = payload.get("visual_evidence_opt_out")
    if opt_out:
        fields["visual_evidence_opt_out"] = opt_out
    return fields


def _path_matches_glob(path: str, pattern: str) -> bool:
    if fnmatch.fnmatchcase(path, pattern):
        return True
    # `**/x` should also match a top-level `x`.
    return pattern.startswith("**/") and fnmatch.fnmatchcase(path, pattern[3:])


def _glob_touched(paths: Iterable[str], globs: Iterable[str]) -> bool:
    """True if any path matches any glob (POSIX, case-sensitive)."""
    pats = tuple(globs)
    for raw in paths:
        path = str(raw).strip()
        if not path:
            continue
        if any(_path_matches_glob(path, pattern) for pattern in pats):
            return True
    return False


def glob_match_paths(paths: Iterable[str], patterns: Iterable[str]) -> list[str]:
    """Fix 3 (agent-harness#91 CR): resolve owned GLOB patterns to the REAL
    paths they match, using the same matching semantics as ``_glob_touched``.

    The reconcile guard feeds these RESOLVED paths (e.g. the real
    ``src/avatar_renderer.py`` that ``src/**`` covers) into the filename
    media-render heuristic -- which expects real paths, not raw glob patterns --
    so it evaluates the SAME resolved-path surface the closeout validator sees
    (which operates on the run's actual changed paths). Preserves input order,
    de-duplicated."""
    pats = tuple(patterns)
    out: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        path = str(raw).strip()
        if not path or path in seen:
            continue
        if any(_path_matches_glob(path, pattern) for pattern in pats):
            out.append(path)
            seen.add(path)
    return out


def public_surface_touched(changed_paths: Iterable[str]) -> bool:
    """True if a changed path looks like a user-visible public surface (rigor-v1 P1/P2)."""
    return _glob_touched(changed_paths, PUBLIC_SURFACE_GLOBS)


def ui_change_detected(changed_paths: Iterable[str]) -> bool:
    """True if a changed path is a UI/visual surface (rigor-v1 P1/P6)."""
    return _glob_touched(changed_paths, UI_GLOBS)


# --- FAV (issue #91): visual-avatar/browser-media detection contract ------
#
# A phase requires BLOCKING visual evidence only when BOTH hold (mirrored in
# ``visual_avatar_evidence_validator.py``'s module docstring; a bare keyword
# hit on only ONE axis is NOT enough -- exactly like #243's command-context
# scoping avoids prose false positives):
#
#   1. STRUCTURAL -- the phase owns/touches a visible-media-rendering
#      surface: a browser HTML fixture (e.g. ``tests/fixtures/*.html``), or a
#      file whose name indicates media rendering (``getUserMedia``,
#      ``MediaStreamTrack``, ``getDisplayMedia``, a
#      canvas/video/camera/session/track renderer, an avatar renderer).
#   2. EXPLICIT CLAIM -- the phase's plan text (title, objective, exit
#      criteria, IF-gate, lane scope, acceptance criteria) makes an explicit
#      USER-VISIBLE rendering claim as a DELIVERABLE, not an incidental
#      mention -- e.g. "visible avatar", "renders in the browser/meeting UI",
#      "browser call-in", "synthetic media"/"MediaStream target",
#      "getUserMedia target", "avatar/browser media".
#
# A phase with NO owned media surface, or that only mentions these words in
# passing with no visible-render deliverable claim, gets NO finding: e.g. a
# plan that merely says "tests video parsing" (no owned media file) or "runs
# in a browser" (no explicit render claim) is silent, and so is any legacy
# phase with no avatar/browser-media surface at all.

AVATAR_MEDIA_SURFACE_GLOBS = ("**/*.html",)

_AVATAR_MEDIA_SEP = r"[\s_\-]?"
_AVATAR_MEDIA_SURFACE_MARKERS = (
    r"getusermedia",
    r"mediastreamtrack",
    r"getdisplaymedia",
    rf"avatar{_AVATAR_MEDIA_SEP}renderer",
    rf"canvas{_AVATAR_MEDIA_SEP}renderer",
    rf"video{_AVATAR_MEDIA_SEP}renderer",
    rf"camera{_AVATAR_MEDIA_SEP}renderer",
    rf"media{_AVATAR_MEDIA_SEP}session",
    rf"media{_AVATAR_MEDIA_SEP}track",
    rf"avatar{_AVATAR_MEDIA_SEP}session",
    rf"session{_AVATAR_MEDIA_SEP}track",
)
_AVATAR_MEDIA_SURFACE_MARKER_RE = re.compile("|".join(_AVATAR_MEDIA_SURFACE_MARKERS), re.IGNORECASE)

_AVATAR_VISIBLE_CLAIM_PATTERNS = (
    r"visible\s+avatar",
    rf"avatar{_AVATAR_MEDIA_SEP}render(?:er|ing|s)?",
    r"renders?\s+(?:in|to|within)\s+(?:the\s+)?(?:browser|meeting)",
    r"browser\s+call-?in",
    r"synthetic\s+media",
    r"mediastream\s+target",
    r"getusermedia\s+target",
    r"avatar[/\s]+browser[\s-]+media",
    r"visual-avatar",
    r"user-visible\s+rendering",
)
_AVATAR_VISIBLE_CLAIM_RE = re.compile("|".join(_AVATAR_VISIBLE_CLAIM_PATTERNS), re.IGNORECASE)

# Fix 5 (agent-harness#91 CR): anchor the explicit-claim scan to AFFIRMATIVE
# deliverable/objective/exit-criteria/acceptance sections and reject NEGATION,
# so a Non-goals line like "must not render a visible avatar" does NOT match
# (which previously produced an opt-in FALSE BLOCK, contrary to the detection
# contract). A section heading that names a non-goal / out-of-scope block turns
# scanning OFF; an affirmative deliverable heading turns it back ON; and a
# negated claim on an in-scope line is skipped.
_AFFIRMATIVE_SECTION_RE = re.compile(
    r"objective|exit\s+criteria|acceptance|deliverable|definition\s+of\s+done|"
    r"requirement|scope|lane|summary|description|\bgoal",
    re.IGNORECASE,
)
_NONGOAL_SECTION_RE = re.compile(
    r"non[-\s]?goal|out[-\s]?of[-\s]?scope|explicitly\s+not|will\s+not|excluded",
    re.IGNORECASE,
)
_CLAIM_NEGATION_RE = re.compile(
    r"\b(?:must\s+not|must\s+never|shall\s+not|should\s+not|shouldn'?t|"
    r"does\s+not|doesn'?t|do\s+not|don'?t|cannot|can'?t|won'?t|will\s+not|"
    r"never|no\s+longer|non[-\s]?goal|not\s+render|no\s+visible)\b",
    re.IGNORECASE,
)
_MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s+(?P<title>.+?)\s*$")


def avatar_media_surface_touched(changed_paths: Iterable[str]) -> bool:
    """STRUCTURAL signal (detection contract #1): an owned/changed path is a
    browser HTML fixture, or its filename indicates media rendering."""
    paths = tuple(changed_paths or ())
    if _glob_touched(paths, AVATAR_MEDIA_SURFACE_GLOBS):
        return True
    return any(_AVATAR_MEDIA_SURFACE_MARKER_RE.search(str(p)) for p in paths)


def avatar_visible_render_claimed(text: str) -> bool:
    """EXPLICIT CLAIM signal (detection contract #2): the plan text makes an
    explicit user-visible rendering claim as a DELIVERABLE.

    Fix 5: the claim must appear in an AFFIRMATIVE section (objective / exit
    criteria / acceptance / deliverable / scope / lanes / preamble) and must
    NOT be negated. A ``Non-goals`` / ``Out of scope`` section is scanned OFF,
    and a line carrying a negation cue ("must not", "does not", "non-goal",
    "no visible", ...) never counts -- so "must not render a visible avatar"
    yields NO claim. A plan with no markdown headings at all is scanned whole
    (back-compat), still with the per-line negation filter.

    Fix (agent-harness#91 round-5 CR / codex): round-5 closes two
    false-NEGATIVE gaps in the fix-5 scoping and one over-negation bug:

    1. The phase TITLE (the ``# `` heading) and IF-gate / exit-gate headings
       (e.g. ``## IF-AV-1-1``, ``## Exit gate``) were never evaluated for a
       claim -- a title like ``# AV-1 -- Visible Avatar`` was invisible to
       the scan entirely. A heading's own title text is now scanned for a
       claim the same way a body line is (subject to the same claim-local
       negation check), so the claim can live IN the heading itself.
    2. An unrecognized heading previously turned scanning OFF (``in_scope =
       False`` in the ``else`` branch), silently swallowing body content
       under any heading that isn't a known affirmative keyword -- exactly
       what hides an IF-gate/exit-gate heading's body ("## IF-AV-1-1" /
       "## Exit gate" don't match the affirmative-keyword regex). Unknown
       headings are now NEUTRAL/affirmative-eligible (``in_scope = True``):
       only an explicit Non-goals/out-of-scope heading disables scanning.
    3. Negation is CLAIM-LOCAL, not qualifier-blind: ``without`` was removed
       from ``_CLAIM_NEGATION_RE`` because a "without <X>" qualifier on an
       otherwise-affirmative claim ("renders a visible avatar without
       operator intervention") is NOT a negation of the claim -- only an
       actual negation cue on the claim itself (Non-goals bullet, "must
       not render", "does not render", "no visible avatar", ...) suppresses
       it.
    """
    body = text or ""
    in_scope = True  # preamble before the first heading is in scope
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = _MARKDOWN_HEADING_RE.match(line)
        if heading:
            title = heading.group("title")
            if _NONGOAL_SECTION_RE.search(title):
                in_scope = False
                continue
            if _AFFIRMATIVE_SECTION_RE.search(title):
                in_scope = True
            else:
                # An unrecognized heading (phase title, IF-gate, exit-gate,
                # or any other unnamed section) is NEUTRAL/affirmative-
                # eligible -- only a Non-goals/out-of-scope heading (handled
                # above) disables scanning.
                in_scope = True
            # The heading's own title text can itself carry the claim (e.g.
            # the phase TITLE "# AV-1 -- Visible Avatar", or a claim written
            # directly into a gate heading) -- scan it like a body line.
            if _AVATAR_VISIBLE_CLAIM_RE.search(title) and not _CLAIM_NEGATION_RE.search(title):
                return True
            continue
        if not in_scope:
            continue
        if _AVATAR_VISIBLE_CLAIM_RE.search(line) and not _CLAIM_NEGATION_RE.search(line):
            return True
    return False


def avatar_visual_evidence_required(changed_paths: Iterable[str], plan_text: str) -> bool:
    """Full FAV detection contract: STRUCTURAL (``changed_paths``) AND
    EXPLICIT CLAIM (``plan_text``).

    Deliberately the SINGLE implementation of the contract -- both the
    closeout validator (``changed_paths`` = the run's actual dirty paths) and
    the ``reconcile`` CLI guard (``changed_paths`` = the files the phase's
    blocked/closeout commit actually changed, via
    ``cli._resolve_changed_paths_at_commit``, agent-harness#91 round-2) call
    this same function with the SAME structural surface -- no ownership-glob
    filtering on either side -- so the two enforcement points can never
    structurally diverge."""
    return avatar_media_surface_touched(changed_paths) and avatar_visible_render_claimed(plan_text)


@dataclass(frozen=True)
class DocDeltaCloseout:
    """doc_delta_closeout.v1 — records the code↔doc currency decision for a phase."""

    decision: str
    target_surfaces: tuple[str, ...] = ()
    evidence_paths: tuple[str, ...] = ()
    justification: str | None = None
    schema: str = DOC_DELTA_CLOSEOUT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != DOC_DELTA_CLOSEOUT_SCHEMA:
            raise ValueError(f"invalid doc delta closeout schema: {self.schema}")
        require_literal(self.decision, DOC_DELTA_DECISIONS, "doc delta decision")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


@dataclass(frozen=True)
class VisualEvidence:
    """Screenshot/artifact evidence that a UI change was visually verified (rigor-v1 P1/P6)."""

    artifact_paths: tuple[str, ...] = ()
    observed: str | None = None

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


@dataclass(frozen=True)
class PhaseLoopCloseout:
    phase: str
    terminal_status: str
    automation: PhaseLoopAutomation
    artifacts: PhaseLoopArtifacts
    verification: PhaseLoopVerification
    blocker: PhaseLoopBlocker
    source_bundle: PhaseLoopSourceBundle
    source_truth_impact: SourceTruthImpact | None = None
    spec_delta_closeout: SpecDeltaCloseout | None = None
    doc_delta_closeout: DocDeltaCloseout | None = None
    schema: str = PIPELINE_CLOSEOUT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != PIPELINE_CLOSEOUT_SCHEMA:
            raise ValueError(f"invalid closeout schema: {self.schema}")
        require_literal(self.terminal_status, PIPELINE_CLOSEOUT_OUTCOMES, "terminal status")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(
            {
                "schema": self.schema,
                "phase": self.phase,
                "terminal_status": self.terminal_status,
                "automation": self.automation.to_json(),
                "artifacts": self.artifacts.to_json(),
                "verification": self.verification.to_json(),
                "blocker": self.blocker.to_json(),
                "source_bundle": self.source_bundle.to_json(),
                "source_truth_impact": self.source_truth_impact.to_json() if self.source_truth_impact else None,
                "spec_delta_closeout": self.spec_delta_closeout.to_json() if self.spec_delta_closeout else None,
                "doc_delta_closeout": self.doc_delta_closeout.to_json() if self.doc_delta_closeout else None,
            }
        )


@dataclass(frozen=True)
class WorkUnitIdentity:
    phase: str
    kind: str
    lane_id: str
    attempt: int

    def __post_init__(self) -> None:
        require_literal(self.kind, WORK_UNIT_KINDS, "work-unit kind")
        if not self.phase.strip():
            raise ValueError("work-unit phase must not be empty")
        if not self.lane_id.strip():
            raise ValueError("work-unit lane id must not be empty")
        if self.attempt < 1:
            raise ValueError("work-unit attempt must be positive")

    @property
    def work_unit_id(self) -> str:
        return f"{self.phase}.{self.kind}.{self.lane_id}.{self.attempt}"

    @classmethod
    def from_id(cls, work_unit_id: str) -> "WorkUnitIdentity":
        parts = work_unit_id.split(".")
        if len(parts) < 4:
            raise ValueError(f"invalid work-unit id: {work_unit_id}")
        phase = parts[0]
        kind = parts[1]
        lane_id = ".".join(parts[2:-1])
        try:
            attempt = int(parts[-1])
        except ValueError as exc:
            raise ValueError(f"invalid work-unit attempt: {parts[-1]}") from exc
        return cls(phase=phase, kind=kind, lane_id=lane_id, attempt=attempt)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "WorkUnitIdentity":
        if data.get("work_unit_id") and not all(key in data for key in ("phase", "kind", "lane_id", "attempt")):
            return cls.from_id(str(data["work_unit_id"]))
        return cls(
            phase=str(data["phase"]),
            kind=str(data["kind"]),
            lane_id=str(data["lane_id"]),
            attempt=int(data["attempt"]),
        )

    def to_json(self) -> dict[str, Any]:
        return clean_dict({**asdict(self), "work_unit_id": self.work_unit_id})


@dataclass(frozen=True)
class WorkUnitAttempt:
    identity: WorkUnitIdentity
    status: str = "pending"
    parent_phase_event_id: str | None = None
    policy: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    started_at: str | None = None
    finished_at: str | None = None
    heartbeat_path: str | None = None
    terminal_summary_path: str | None = None
    retry_of: str | None = None
    superseded_by: str | None = None

    def __post_init__(self) -> None:
        require_literal(self.status, WORK_UNIT_STATUSES, "work-unit status")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(
            {
                "identity": self.identity.to_json(),
                "status": self.status,
                "parent_phase_event_id": self.parent_phase_event_id,
                "policy": self.policy,
                "artifacts": self.artifacts,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "heartbeat_path": self.heartbeat_path,
                "terminal_summary_path": self.terminal_summary_path,
                "retry_of": self.retry_of,
                "superseded_by": self.superseded_by,
            }
        )


@dataclass(frozen=True)
class WorkUnitCloseout:
    identity: WorkUnitIdentity
    status: str
    automation: dict[str, Any] = field(default_factory=dict)
    terminal_summary: dict[str, Any] = field(default_factory=dict)
    closeout_summary: dict[str, Any] = field(default_factory=dict)
    wave_id: str | None = None
    worktree_path: str | None = None
    changed_paths: tuple[str, ...] = ()
    verification_status: str | None = None
    evidence_refs: tuple[dict[str, Any], ...] = ()
    human_required: bool = False
    blocker_class: str | None = None
    blocker_summary: str | None = None
    required_human_inputs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_literal(self.status, WORK_UNIT_STATUSES, "work-unit status")
        if self.blocker_class is not None:
            require_literal(self.blocker_class, BLOCKER_CLASSES, "blocker class")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(
            {
                "identity": self.identity.to_json(),
                "status": self.status,
                "automation": self.automation,
                "terminal_summary": self.terminal_summary,
                "closeout_summary": self.closeout_summary,
                "wave_id": self.wave_id,
                "worktree_path": self.worktree_path,
                "changed_paths": self.changed_paths,
                "verification_status": self.verification_status,
                "evidence_refs": self.evidence_refs,
                "human_required": self.human_required,
                "blocker_class": self.blocker_class,
                "blocker_summary": self.blocker_summary,
                "required_human_inputs": self.required_human_inputs,
            }
        )


@dataclass(frozen=True)
class WorkUnitEventMetadata:
    identity: WorkUnitIdentity
    status: str
    event_type: str = "status"
    timestamp: str = field(default_factory=utc_now)
    launch_metadata: dict[str, Any] = field(default_factory=dict)
    heartbeat_path: str | None = None
    terminal_summary_path: str | None = None
    closeout_summary: dict[str, Any] = field(default_factory=dict)
    retry_of: str | None = None
    superseded_by: str | None = None
    blocker: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        require_literal(self.status, WORK_UNIT_STATUSES, "work-unit status")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(
            {
                "event_kind": "work_unit",
                "event_type": self.event_type,
                "timestamp": self.timestamp,
                "work_unit": {
                    **self.identity.to_json(),
                    "status": self.status,
                    "launch_metadata": self.launch_metadata,
                    "heartbeat_path": self.heartbeat_path,
                    "terminal_summary_path": self.terminal_summary_path,
                    "closeout_summary": self.closeout_summary,
                    "retry_of": self.retry_of,
                    "superseded_by": self.superseded_by,
                    "blocker": self.blocker,
                },
            }
        )


@dataclass(frozen=True)
class WorkUnitState:
    identity: WorkUnitIdentity
    status: str = "pending"
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    parent_phase_event_id: str | None = None
    policy: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    heartbeat_path: str | None = None
    terminal_summary_path: str | None = None
    closeout_summary: dict[str, Any] = field(default_factory=dict)
    retry_of: str | None = None
    superseded_by: str | None = None
    blocker: dict[str, Any] | None = None
    human_required: bool = False

    def __post_init__(self) -> None:
        require_literal(self.status, WORK_UNIT_STATUSES, "work-unit status")

    @property
    def work_unit_id(self) -> str:
        return self.identity.work_unit_id

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "WorkUnitState":
        identity_data = data.get("identity") if isinstance(data.get("identity"), dict) else data
        identity = WorkUnitIdentity.from_json(identity_data)
        return cls(
            identity=identity,
            status=str(data.get("status", "pending")),
            created_at=str(data.get("created_at") or utc_now()),
            updated_at=str(data.get("updated_at") or data.get("created_at") or utc_now()),
            parent_phase_event_id=data.get("parent_phase_event_id"),
            policy=dict(data.get("policy", {})) if isinstance(data.get("policy"), dict) else {},
            artifacts={str(k): str(v) for k, v in dict(data.get("artifacts", {})).items()} if isinstance(data.get("artifacts"), dict) else {},
            heartbeat_path=data.get("heartbeat_path"),
            terminal_summary_path=data.get("terminal_summary_path"),
            closeout_summary=dict(data.get("closeout_summary", {})) if isinstance(data.get("closeout_summary"), dict) else {},
            retry_of=data.get("retry_of"),
            superseded_by=data.get("superseded_by"),
            blocker=dict(data.get("blocker")) if isinstance(data.get("blocker"), dict) else None,
            human_required=bool(data.get("human_required", False)),
        )

    def with_status(self, status: str, **updates: Any) -> "WorkUnitState":
        require_literal(status, WORK_UNIT_STATUSES, "work-unit status")
        data = {**asdict(self), **updates, "status": status, "updated_at": updates.get("updated_at", utc_now())}
        data["identity"] = self.identity
        return WorkUnitState(**data)

    def to_json(self) -> dict[str, Any]:
        return clean_dict(
            {
                "identity": self.identity.to_json(),
                "work_unit_id": self.work_unit_id,
                "status": self.status,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "parent_phase_event_id": self.parent_phase_event_id,
                "policy": self.policy,
                "artifacts": self.artifacts,
                "heartbeat_path": self.heartbeat_path,
                "terminal_summary_path": self.terminal_summary_path,
                "closeout_summary": self.closeout_summary,
                "retry_of": self.retry_of,
                "superseded_by": self.superseded_by,
                "blocker": self.blocker,
                "human_required": self.human_required,
            }
        )


@dataclass(frozen=True)
class LaneDependency:
    source_lane_id: str
    target_lane_id: str
    relation: str = "depends_on"

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


@dataclass(frozen=True)
class LaneTaskSet:
    test: tuple[str, ...] = ()
    impl: tuple[str, ...] = ()
    verify: tuple[str, ...] = ()
    other: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


@dataclass(frozen=True)
class LaneIRDiagnostic(Exception):
    kind: str
    message: str
    lane_id: str | None = None
    blocker_class: str = "contract_bug"
    human_required: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_literal(self.kind, LANE_IR_DIAGNOSTIC_KINDS, "lane IR diagnostic kind")
        require_literal(self.blocker_class, BLOCKER_CLASSES, "lane IR blocker class")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


@dataclass(frozen=True)
class PhasePlanLane:
    lane_id: str
    name: str
    heading: str
    owned_files: tuple[str, ...] = ()
    read_only: bool = False
    depends_on: tuple[str, ...] = ()
    blocks: tuple[str, ...] = ()
    interfaces_provided: tuple[str, ...] = ()
    interfaces_consumed: tuple[str, ...] = ()
    tasks: LaneTaskSet = field(default_factory=LaneTaskSet)
    verification_commands: tuple[str, ...] = ()
    parallel_safe: bool = False
    reducer_kind: str = "none"
    execution_policy: ExecutionPolicyRule | None = None

    def __post_init__(self) -> None:
        require_literal(self.reducer_kind, LANE_REDUCER_KINDS, "lane reducer kind")

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        if self.execution_policy is not None:
            data["execution_policy"] = self.execution_policy.to_json()
        return clean_dict(data)


@dataclass(frozen=True)
class PhasePlanIR:
    plan_path: str
    metadata: dict[str, str]
    lanes: tuple[PhasePlanLane, ...] = ()
    dependencies: tuple[LaneDependency, ...] = ()
    diagnostics: tuple[LaneIRDiagnostic, ...] = ()
    execution_policy: ExecutionPolicyDocument | None = None
    dispatch_hints: dict[str, DispatchHints] = field(default_factory=dict)
    merge_policy: Any | None = None

    @property
    def valid(self) -> bool:
        return not self.diagnostics

    def to_json(self) -> dict[str, Any]:
        return clean_dict(
            {
                "plan_path": self.plan_path,
                "metadata": self.metadata,
                "lanes": tuple(lane.to_json() for lane in self.lanes),
                "dependencies": tuple(dependency.to_json() for dependency in self.dependencies),
                "diagnostics": tuple(diagnostic.to_json() for diagnostic in self.diagnostics),
                "execution_policy": self.execution_policy.to_json() if self.execution_policy else None,
                "dispatch_hints": {
                    key: value.to_json() for key, value in self.dispatch_hints.items()
                },
                "merge_policy": self.merge_policy.to_json() if hasattr(self.merge_policy, "to_json") else self.merge_policy,
            }
        )


@dataclass(frozen=True)
class LaneWorktreeAssignment:
    lane_id: str
    worktree_path: str
    isolation_mode: str = "main_worktree"
    branch: str | None = None
    base_sha: str | None = None

    def __post_init__(self) -> None:
        if not self.lane_id.strip():
            raise ValueError("lane worktree assignment lane_id must not be empty")
        if not self.worktree_path.strip():
            raise ValueError("lane worktree assignment worktree_path must not be empty")
        require_literal(self.isolation_mode, WORKTREE_ISOLATION_MODES, "worktree isolation mode")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


@dataclass(frozen=True)
class LaneWave:
    wave_id: str
    lane_ids: tuple[str, ...]
    mode: str = "serialized"
    assignments: tuple[LaneWorktreeAssignment, ...] = ()

    def __post_init__(self) -> None:
        if not self.wave_id.strip():
            raise ValueError("lane wave id must not be empty")
        require_literal(self.mode, LANE_SCHEDULER_MODES, "lane scheduler mode")
        if self.mode == "off":
            raise ValueError("lane wave mode cannot be off")
        if not self.lane_ids:
            raise ValueError("lane wave must include at least one lane")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(
            {
                "wave_id": self.wave_id,
                "lane_ids": self.lane_ids,
                "mode": self.mode,
                "assignments": tuple(assignment.to_json() for assignment in self.assignments),
            }
        )


@dataclass(frozen=True)
class LaneWaveDecision:
    status: str
    mode: str
    ready_wave: LaneWave | None = None
    pending_lane_ids: tuple[str, ...] = ()
    completed_lane_ids: tuple[str, ...] = ()
    blocked_lane_ids: tuple[str, ...] = ()
    diagnostics: tuple[LaneIRDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        require_literal(self.status, LANE_WAVE_STATUSES, "lane wave status")
        require_literal(self.mode, LANE_SCHEDULER_MODES, "lane scheduler mode")
        if self.status == "ready" and self.ready_wave is None:
            raise ValueError("ready lane wave decision requires ready_wave")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(
            {
                "status": self.status,
                "mode": self.mode,
                "ready_wave": self.ready_wave.to_json() if self.ready_wave else None,
                "pending_lane_ids": self.pending_lane_ids,
                "completed_lane_ids": self.completed_lane_ids,
                "blocked_lane_ids": self.blocked_lane_ids,
                "diagnostics": tuple(diagnostic.to_json() for diagnostic in self.diagnostics),
            }
        )


HARNESS_CLOSEOUT_REQUIRED_FIELDS = (
    "automation.status",
    "automation.next_skill",
    "automation.next_command",
    "automation.human_required",
    "automation.blocker_class",
    "automation.verification_status",
)


@dataclass(frozen=True)
class HarnessLaneAssignment:
    phase: str
    lane_id: str
    work_unit_kind: str
    prompt_kind: str = "implementation"
    wave_id: str | None = None
    owned_files: tuple[str, ...] = ()
    read_only_refs: tuple[str, ...] = ()
    consumed_interfaces: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    execution_policy: dict[str, Any] = field(default_factory=dict)
    worktree_assignment: LaneWorktreeAssignment | None = None
    harness_route: str | None = None
    model: str | None = None
    effort: str | None = None
    fallback_reason: str | None = None
    closeout_schema_required: tuple[str, ...] = HARNESS_CLOSEOUT_REQUIRED_FIELDS
    reducer_kind: str = "none"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.phase.strip():
            raise ValueError("harness lane assignment phase must not be empty")
        if not self.lane_id.strip():
            raise ValueError("harness lane assignment lane_id must not be empty")
        require_literal(self.work_unit_kind, WORK_UNIT_KINDS, "harness work-unit kind")
        require_literal(self.prompt_kind, HARNESS_WORK_UNIT_PROMPT_KINDS, "harness prompt kind")
        require_literal(self.reducer_kind, LANE_REDUCER_KINDS, "lane reducer kind")
        if self.work_unit_kind in {"lane_execute", "lane_review"} and not self.owned_files:
            raise ValueError("harness lane assignment requires owned_files for lane work")

    @classmethod
    def from_lane(
        cls,
        *,
        phase: str,
        lane: PhasePlanLane,
        work_unit_kind: str = "lane_execute",
        prompt_kind: str = "implementation",
        worktree_assignment: LaneWorktreeAssignment | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "HarnessLaneAssignment":
        return cls(
            phase=phase,
            lane_id=lane.lane_id,
            work_unit_kind=work_unit_kind,
            prompt_kind=prompt_kind,
            owned_files=lane.owned_files,
            read_only_refs=lane.interfaces_consumed,
            consumed_interfaces=lane.interfaces_consumed,
            depends_on=lane.depends_on,
            execution_policy=lane.execution_policy.to_json() if lane.execution_policy else {},
            worktree_assignment=worktree_assignment,
            reducer_kind=lane.reducer_kind,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "HarnessLaneAssignment":
        assignment_data = data.get("worktree_assignment")
        return cls(
            phase=str(data.get("phase") or ""),
            lane_id=str(data.get("lane_id") or ""),
            work_unit_kind=str(data.get("work_unit_kind") or ""),
            prompt_kind=str(data.get("prompt_kind") or "implementation"),
            wave_id=data.get("wave_id"),
            owned_files=tuple(str(item) for item in data.get("owned_files") or ()),
            read_only_refs=tuple(str(item) for item in data.get("read_only_refs") or ()),
            consumed_interfaces=tuple(str(item) for item in data.get("consumed_interfaces") or ()),
            depends_on=tuple(str(item) for item in data.get("depends_on") or ()),
            execution_policy=dict(data.get("execution_policy") or {}),
            worktree_assignment=(
                LaneWorktreeAssignment(
                    lane_id=str(assignment_data.get("lane_id") or ""),
                    worktree_path=str(assignment_data.get("worktree_path") or ""),
                    isolation_mode=str(assignment_data.get("isolation_mode") or "main_worktree"),
                    branch=assignment_data.get("branch"),
                    base_sha=assignment_data.get("base_sha"),
                )
                if isinstance(assignment_data, dict)
                else None
            ),
            harness_route=data.get("harness_route"),
            model=data.get("model"),
            effort=data.get("effort"),
            fallback_reason=data.get("fallback_reason"),
            closeout_schema_required=tuple(str(item) for item in data.get("closeout_schema_required") or HARNESS_CLOSEOUT_REQUIRED_FIELDS),
            reducer_kind=str(data.get("reducer_kind") or "none"),
            metadata=dict(data.get("metadata") or {}),
        )

    def to_json(self) -> dict[str, Any]:
        return clean_dict(
            {
                "phase": self.phase,
                "lane_id": self.lane_id,
                "work_unit_kind": self.work_unit_kind,
                "prompt_kind": self.prompt_kind,
                "wave_id": self.wave_id,
                "owned_files": self.owned_files,
                "read_only_refs": self.read_only_refs,
                "consumed_interfaces": self.consumed_interfaces,
                "depends_on": self.depends_on,
                "execution_policy": self.execution_policy,
                "worktree_assignment": self.worktree_assignment.to_json() if self.worktree_assignment else None,
                "harness_route": self.harness_route,
                "model": self.model,
                "effort": self.effort,
                "fallback_reason": self.fallback_reason,
                "closeout_schema_required": self.closeout_schema_required,
                "reducer_kind": self.reducer_kind,
                "metadata": self.metadata,
            }
        )


@dataclass(frozen=True)
class DirtyPathClassification:
    path: str
    classification: str
    lane_id: str | None = None

    def __post_init__(self) -> None:
        if not self.path.strip():
            raise ValueError("dirty path classification path must not be empty")
        require_literal(self.classification, DIRTY_PATH_CLASSIFICATIONS, "dirty path classification")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


@dataclass(frozen=True)
class ResolvedExecutionPolicy:
    action: str
    lane: str | None
    executor: str
    model: str
    effort: str
    work_unit_kind: str
    fallback: str | None = None
    unsupported_policy_behavior: str = "block"
    execution_policy_source: str = "registry defaults"
    execution_policy_override_reason: str | None = None
    executor_source: str = "registry defaults"
    model_source: str = "registry defaults"
    effort_source: str = "registry defaults"
    fallback_source: str = "registry defaults"
    fallback_applied: bool = False
    # model-routing-v1 P4: the role the winning policy requested, for route logs.
    model_class: str | None = None

    def __post_init__(self) -> None:
        require_literal(self.action, PRODUCT_LOOP_ACTIONS, "execution policy action")
        require_literal(self.executor, EXECUTORS, "execution policy executor")
        require_literal(self.effort, NORMALIZED_EFFORT_LEVELS, "execution policy effort")
        require_literal(self.work_unit_kind, WORK_UNIT_KINDS, "execution policy work-unit kind")
        require_literal(self.unsupported_policy_behavior, UNSUPPORTED_POLICY_BEHAVIORS, "unsupported policy behavior")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


@dataclass(frozen=True)
class WorkUnitMetric:
    metric_id: str
    schema_version: str
    timestamp: str
    work_unit_id: str | None
    work_unit_kind: str
    phase: str
    action: str
    executor: str
    provider: str
    model: str
    effort: str | None = None
    thinking_level: str | None = None
    lane_id: str | None = None
    wave_id: str | None = None
    policy_source: str | None = None
    policy_override_reason: str | None = None
    profile_source: str | None = None
    fallback_applied: bool = False
    fallback: str | None = None
    fallback_reason: str | None = None
    duration_seconds: float | None = None
    returncode: int | None = None
    terminal_status: str | None = None
    verification_status: str | None = None
    blocker_class: str | None = None
    artifact_paths: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != WORK_UNIT_METRIC_SCHEMA_VERSION:
            raise ValueError(f"invalid work-unit metric schema_version: {self.schema_version}")
        require_literal(self.work_unit_kind, WORK_UNIT_KINDS, "work-unit kind")
        require_literal(self.action, PRODUCT_LOOP_ACTIONS, "metric action")
        require_literal(self.executor, EXECUTORS, "metric executor")
        if self.effort is not None:
            require_literal(self.effort, NORMALIZED_EFFORT_LEVELS, "metric effort")
        if self.blocker_class is not None:
            require_literal(self.blocker_class, BLOCKER_CLASSES, "metric blocker class")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


@dataclass(frozen=True)
class ExecutorCapabilityRecord:
    executor: str
    supported_actions: tuple[str, ...]
    capabilities: tuple[str, ...]
    strengths: tuple[str, ...] = ()
    limits: tuple[str, ...] = ()
    injection_mode: str = "manual"
    permission_posture: str = "unknown"
    subagent_posture: str = "unknown"
    live_available: bool = False
    dry_run_available: bool = True
    # AUTOSEL (IF-0-AUTOSEL-2): can this executor be launched WITHOUT a controlling
    # terminal? The non-headless executors are ``claude`` (its panel/TUI leg needs a
    # real terminal) and ``manual`` (operator handoff, no spawned CLI at all);
    # codex/gemini/grok/opencode/pi run headless via ``-p``. The AUTO
    # default-resolution layers gate on this so they never auto-pick an executor the
    # runner cannot launch headlessly (e.g. under Claude Code / detached / CI).
    # Distinct from ``promotion_status`` — grok is ``proof_gated`` yet headless.
    headless_launchable: bool = True
    live_proof_gate: str = "disposable_proof_required"
    promotion_status: str = "proof_gated"
    promotion_requirements: tuple[str, ...] = ()
    auth_preflight_mode: str = "none"
    auth_preflight_probes: tuple[str, ...] = ()
    timeout_posture: str = "runner_managed"
    output_capture_format: str = "combined_output"
    terminal_summary_artifact: str = "terminal-summary.json"
    default_model_profiles: dict[str, str] = field(default_factory=dict)
    known_failure_cases: tuple[str, ...] = ()
    default_claude_execution_mode: str | None = None
    claude_execution_policies: tuple["ClaudeTeamPolicy", ...] = ()

    # EXECREG (IF-0-EXECREG-1): registry-driven launch / availability / auth /
    # provider-backing / session capture. These are runtime bindings, NOT dataclass
    # fields — declared as ClassVar so `dataclasses.fields()` / `asdict()` /
    # `replace()` / `__eq__` never see them (so `json.dumps(asdict(record))` can
    # never trip over a function object, and record equality is unaffected). Every
    # record exposes them as attributes defaulting to None; `capability_registry()`
    # attaches per-record callables via `bind_runtime()` (which uses
    # `object.__setattr__`, the frozen-dataclass escape hatch). The callables live
    # in `launcher` / `executor_availability`, bound lazily to avoid the
    # launcher<->capability_registry import cycle. `build_launch_spec` delegates to
    # `build_command`; adding an executor is a record addition, not an if-branch edit.
    build_command: ClassVar[Callable[["LaunchRequest", "ExecutorCapabilityRecord"], Any] | None] = None
    is_available: ClassVar[Callable[[], bool] | None] = None
    auth_ok: ClassVar[Callable[[], bool] | None] = None
    provider_backing: ClassVar[Any] = None
    get_session_transcript: ClassVar[Callable[["LaunchRequest"], Any] | None] = None

    _RUNTIME_BINDINGS: ClassVar[tuple[str, ...]] = (
        "build_command",
        "is_available",
        "auth_ok",
        "provider_backing",
        "get_session_transcript",
    )

    def bind_runtime(self, **bindings: Any) -> "ExecutorCapabilityRecord":
        """Return a copy of this record with EXECREG runtime callables attached as
        instance attributes (via ``object.__setattr__`` — the frozen escape hatch).
        They are not dataclass fields, so they never enter ``asdict`` / ``replace`` /
        equality. Bindings already present on ``self`` are CARRIED FORWARD (``replace``
        copies only fields, not these instance attrs), so a partial re-bind overrides
        only the named bindings and never silently drops the others. Unknown binding
        names are rejected loudly."""
        unknown = set(bindings) - set(self._RUNTIME_BINDINGS)
        if unknown:
            raise ValueError(f"unknown runtime binding(s): {sorted(unknown)}")
        clone = replace(self)
        for name in self._RUNTIME_BINDINGS:
            if name in bindings:
                object.__setattr__(clone, name, bindings[name])
            elif name in self.__dict__:  # a binding already set on self -> preserve it
                object.__setattr__(clone, name, self.__dict__[name])
        return clone

    def __post_init__(self) -> None:
        require_literal(self.executor, EXECUTORS, "executor")
        require_literal(self.injection_mode, INJECTION_MODES, "injection mode")
        require_literal(self.permission_posture, PERMISSION_POSTURES, "permission posture")
        require_literal(self.subagent_posture, SUBAGENT_POSTURES, "subagent posture")
        require_literal(self.live_proof_gate, LIVE_PROOF_GATES, "live proof gate")
        require_literal(self.promotion_status, PROMOTION_STATUSES, "promotion status")
        require_literal(self.auth_preflight_mode, AUTH_PREFLIGHT_MODES, "auth preflight mode")
        require_literal(self.timeout_posture, TIMEOUT_POSTURES, "timeout posture")
        require_literal(self.output_capture_format, OUTPUT_CAPTURE_FORMATS, "output capture format")
        if self.default_claude_execution_mode is not None:
            require_literal(self.default_claude_execution_mode, CLAUDE_EXECUTION_MODES, "Claude execution mode")
        for action in self.supported_actions:
            require_literal(action, PRODUCT_LOOP_ACTIONS, "supported action")
        for capability in self.capabilities:
            require_literal(capability, DISPATCH_CAPABILITIES, "capability")
        for action, profile in self.default_model_profiles.items():
            require_literal(action, PRODUCT_LOOP_ACTIONS, "default model profile action")
            require_literal(profile, MODEL_PROFILES, "default model profile")

    def to_json(self) -> dict[str, Any]:
        # The EXECREG runtime callables are ClassVar bindings, not dataclass fields,
        # so asdict() never includes them — the metadata JSON is exactly what it was
        # before those bindings existed (no function objects).
        return clean_dict(asdict(self))


@dataclass(frozen=True)
class ClaudeTeamPolicy:
    execution_mode: str
    maturity_label: str
    live_proof_gate: str
    promotion_status: str
    launch_default: bool = False
    max_teammates: int = 0
    max_native_tasks: int = 0
    max_delegation_depth: int = 0
    max_fanout: int = 0
    default_model: str | None = None
    default_effort: str | None = None
    budget_guidance: dict[str, Any] = field(default_factory=dict)
    allowed_actions: tuple[str, ...] = ()
    disallowed_actions: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()
    worktree_posture: str = "phase_loop_managed"
    requires_disjoint_owned_files: bool = False
    allows_read_only_lanes: bool = True
    direct_teammate_messaging_allowed: bool = False
    task_lifecycle_supported: bool = False

    def __post_init__(self) -> None:
        require_literal(self.execution_mode, CLAUDE_EXECUTION_MODES, "Claude execution mode")
        require_literal(self.maturity_label, OPERATOR_MATURITY_LABELS, "operator maturity label")
        require_literal(self.live_proof_gate, LIVE_PROOF_GATES, "live proof gate")
        require_literal(self.promotion_status, PROMOTION_STATUSES, "promotion status")
        require_literal(self.worktree_posture, CLAUDE_WORKTREE_POSTURES, "Claude worktree posture")
        for action in self.allowed_actions:
            require_literal(action, PRODUCT_LOOP_ACTIONS, "allowed action")
        for action in self.disallowed_actions:
            require_literal(action, PRODUCT_LOOP_ACTIONS, "disallowed action")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


@dataclass(frozen=True)
class PhaseTeamEligibility:
    allowed_execution_modes: tuple[str, ...]
    default_execution_mode: str
    eligible_for_native_team: bool
    has_disjoint_write_lanes: bool
    has_only_read_only_lanes: bool
    unmanaged_write_risk: bool
    reason: str
    invalid_reasons: tuple[str, ...] = ()
    lane_summaries: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        require_literal(self.default_execution_mode, CLAUDE_EXECUTION_MODES, "default Claude execution mode")
        for mode in self.allowed_execution_modes:
            require_literal(mode, CLAUDE_EXECUTION_MODES, "allowed Claude execution mode")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


@dataclass(frozen=True)
class DispatchDecision:
    action: str
    selected_executor: str | None
    source: str
    preferred_executors: tuple[str, ...] = ()
    allowed_executors: tuple[str, ...] = ()
    fallback_executors: tuple[str, ...] = ()
    disabled_executors: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    considered_executors: tuple[str, ...] = ()
    blocked_reason: str | None = None
    blocked_summary: str | None = None
    fallback_applied: bool = False
    selected_via: str | None = None

    def __post_init__(self) -> None:
        require_literal(self.action, PRODUCT_LOOP_ACTIONS, "product action")
        if self.selected_executor is not None:
            require_literal(self.selected_executor, EXECUTORS, "selected executor")
        if self.selected_via is not None:
            require_literal(self.selected_via, DISPATCH_SELECTION_PATHS, "dispatch selection path")
        for field_name in (
            "preferred_executors",
            "allowed_executors",
            "fallback_executors",
            "disabled_executors",
            "considered_executors",
        ):
            values = getattr(self, field_name)
            for value in values:
                require_literal(value, EXECUTORS, field_name)
        for capability in self.required_capabilities:
            require_literal(capability, DISPATCH_CAPABILITIES, "required capability")

    @property
    def blocked(self) -> bool:
        return self.selected_executor is None

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


@dataclass(frozen=True)
class PermissionPolicy:
    sandbox_mode: str
    bypass_approvals: bool = False

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


@dataclass(frozen=True)
class CommandAdapterConfig:
    name: str
    template: str
    delivery_mode: str = "context_file"
    supported_actions: tuple[str, ...] = COMMAND_ADAPTER_SUPPORTED_ACTIONS

    def __post_init__(self) -> None:
        require_literal(self.delivery_mode, INJECTION_MODES, "command adapter delivery mode")
        for action in self.supported_actions:
            require_literal(action, PRODUCT_LOOP_ACTIONS, "command adapter supported action")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


@dataclass(frozen=True)
class LaunchRequest:
    executor: str
    action: str
    repo: Path
    roadmap: Path
    phase: str | None
    plan: Path | None
    model_selection: ModelSelection
    prompt_bundle: PromptBundle
    injection_metadata: InjectionMetadata
    permission_policy: PermissionPolicy
    command_adapter: CommandAdapterConfig | None = None
    dispatch_decision: DispatchDecision | None = None
    harness_lane_assignment: HarnessLaneAssignment | None = None
    delegation_request: "DelegationRequest | None" = None
    parent_child_metadata: "ParentChildRunMetadata | None" = None
    claude_execution_mode: str | None = None
    claude_team_policy: ClaudeTeamPolicy | None = None
    phase_team_eligibility: PhaseTeamEligibility | None = None
    json_output: bool = False
    bypass_approvals: bool = False
    launch_timeout_seconds: int | None = None

    def __post_init__(self) -> None:
        require_literal(self.executor, EXECUTORS, "executor")
        if self.claude_execution_mode is not None:
            require_literal(self.claude_execution_mode, CLAUDE_EXECUTION_MODES, "Claude execution mode")

    def to_json(self) -> dict[str, Any]:
        return {
            "executor": self.executor,
            "action": self.action,
            "repo": str(self.repo),
            "roadmap": str(self.roadmap),
            "phase": self.phase,
            "plan": str(self.plan) if self.plan is not None else None,
            "model_selection": self.model_selection.to_json(),
            "prompt_bundle": self.prompt_bundle.to_json(),
            "injection_metadata": self.injection_metadata.to_json(),
            "permission_policy": self.permission_policy.to_json(),
            "command_adapter": self.command_adapter.to_json() if self.command_adapter else None,
            "dispatch_decision": self.dispatch_decision.to_json() if self.dispatch_decision else None,
            "harness_lane_assignment": self.harness_lane_assignment.to_json() if self.harness_lane_assignment else None,
            "delegation_request": self.delegation_request.to_json() if self.delegation_request else None,
            "parent_child_metadata": self.parent_child_metadata.to_json() if self.parent_child_metadata else None,
            "claude_execution_mode": self.claude_execution_mode,
            "claude_team_policy": self.claude_team_policy.to_json() if self.claude_team_policy else None,
            "phase_team_eligibility": self.phase_team_eligibility.to_json() if self.phase_team_eligibility else None,
            "json_output": self.json_output,
            "bypass_approvals": self.bypass_approvals,
            "launch_timeout_seconds": self.launch_timeout_seconds,
        }


@dataclass(frozen=True)
class DelegationBudget:
    max_tokens: int | None = None
    max_seconds: int | None = None
    max_cost_usd: float | None = None
    notes: str | None = None

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))

    def is_defined(self) -> bool:
        return any(value is not None for value in (self.max_tokens, self.max_seconds, self.max_cost_usd)) or bool(self.notes)


@dataclass(frozen=True)
class DelegationRequest:
    request_id: str
    product_action: str
    target_executor: str
    reason: str
    owned_files: tuple[str, ...]
    expected_output: str
    priority: str = "normal"
    review_context: str | None = None
    repair_context: str | None = None
    budget: DelegationBudget | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_literal(self.product_action, PRODUCT_LOOP_ACTIONS, "product action")
        require_literal(self.target_executor, EXECUTORS, "target executor")
        require_literal(self.priority, DELEGATION_PRIORITIES, "delegation priority")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(
            {
                "request_id": self.request_id,
                "product_action": self.product_action,
                "target_executor": self.target_executor,
                "reason": self.reason,
                "owned_files": self.owned_files,
                "expected_output": self.expected_output,
                "priority": self.priority,
                "review_context": self.review_context,
                "repair_context": self.repair_context,
                "budget": self.budget.to_json() if self.budget else None,
                "metadata": self.metadata,
            }
        )


@dataclass(frozen=True)
class DelegationDecision:
    request_id: str
    status: str
    reason_code: str
    summary: str
    selected_executor: str | None = None
    dispatch_decision: dict[str, Any] | None = None
    human_required: bool = False
    blocker_class: str | None = None
    observed_depth: int | None = None
    observed_fanout: int | None = None
    validation_order: tuple[str, ...] = (
        "active_loop_mode",
        "ownership_boundaries",
        "depth_limit",
        "fanout_limit",
        "budget_metadata",
        "dispatch_policy",
    )

    def __post_init__(self) -> None:
        require_literal(self.status, DELEGATION_STATUSES, "delegation status")
        if self.selected_executor is not None:
            require_literal(self.selected_executor, EXECUTORS, "selected executor")
        if self.blocker_class is not None:
            require_literal(self.blocker_class, BLOCKER_CLASSES, "blocker class")

    @property
    def approved(self) -> bool:
        return self.status == "approved"

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


@dataclass(frozen=True)
class ParentChildRunMetadata:
    parent_phase: str
    parent_action: str
    parent_executor: str | None = None
    parent_run_id: str | None = None
    child_phase: str | None = None
    child_action: str | None = None
    child_run_id: str | None = None
    request_id: str | None = None
    child_executor: str | None = None
    observed_launch_path: str | None = None
    child_artifact_root: str | None = None
    child_worktree_root: str | None = None
    child_closeout_result: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        require_literal(self.parent_action, PRODUCT_LOOP_ACTIONS, "parent action")
        if self.parent_executor is not None:
            require_literal(self.parent_executor, EXECUTORS, "parent executor")
        if self.child_action is not None:
            require_literal(self.child_action, PRODUCT_LOOP_ACTIONS, "child action")
        if self.child_executor is not None:
            require_literal(self.child_executor, EXECUTORS, "child executor")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


@dataclass(frozen=True)
class Blocker:
    human_required: bool = False
    blocker_class: str | None = None
    blocker_summary: str | None = None
    required_human_inputs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.blocker_class is not None:
            require_literal(self.blocker_class, BLOCKER_CLASSES, "blocker class")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


# CS-2.1 SA — fleet-metric ledger event types. These populate the three
# LEDGER-FAITHFUL named fleet-trend metrics (velocity / burn_down /
# promise_broken_duration) reserved by the CS-2.1 spine. They are emitted to a
# SEPARATE append-only sibling ledger (``.phase-loop/fleet-metrics.jsonl``) so
# the existing ``events.jsonl`` bytes and reconcile semantics stay untouched.
FLEET_METRIC_SCHEMA = "fleet_metric.v1"
FLEET_METRIC_KINDS = (
    "velocity",           # throughput: cumulative completed phases + timestamp
    "burn_down",          # remaining-vs-completed scope trajectory
    "promise_broken",     # an IF-gate promise declared-but-not-produced (break)
    "promise_repaired",   # a previously-broken IF-gate promise later produced
)


@dataclass(frozen=True)
class FleetMetricEvent:
    """One append to the fleet-metric ledger (CS-2.1 SA).

    ``event_kind`` is fixed to ``fleet_metric`` and ``metric_kind`` selects the
    series. ``payload`` carries only the small numeric/label fields that series
    needs (never a filesystem path, secret, or session internal — the export
    bridge re-asserts that before anything leaves the enforcement side).
    """

    metric_kind: str
    timestamp: str = field(default_factory=utc_now)
    phase: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    schema: str = FLEET_METRIC_SCHEMA

    def __post_init__(self) -> None:
        require_literal(self.metric_kind, FLEET_METRIC_KINDS, "fleet metric kind")
        if self.schema != FLEET_METRIC_SCHEMA:
            raise ValueError(f"invalid fleet metric schema: {self.schema}")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(
            {
                "event_kind": "fleet_metric",
                "schema": self.schema,
                "metric_kind": self.metric_kind,
                "timestamp": self.timestamp,
                "phase": self.phase,
                "payload": self.payload,
            }
        )


@dataclass(frozen=True)
class LoopEvent:
    timestamp: str
    repo: str
    roadmap: str
    phase: str
    action: str
    status: str
    model: str
    reasoning_effort: str
    source: str
    override_reason: str | None = None
    command: list[str] | None = None
    blocker: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    git_topology: dict[str, Any] | None = None
    selected_executor: str | None = None
    schema_version: int = 2
    roadmap_sha256: str | None = None
    phase_sha256: str | None = None

    def __post_init__(self) -> None:
        require_literal(self.status, EVENT_STATUSES, "event status")
        if self.selected_executor is not None:
            require_literal(self.selected_executor, EXECUTORS, "selected executor")

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


@dataclass(frozen=True)
class StateSnapshot:
    timestamp: str
    repo: str
    roadmap: str
    phases: dict[str, str] = field(default_factory=dict)
    current_phase: str | None = None
    last_action: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    source: str | None = None
    override_reason: str | None = None
    human_required: bool = False
    blocker_class: str | None = None
    blocker_summary: str | None = None
    required_human_inputs: tuple[str, ...] = ()
    access_attempts: tuple[dict[str, Any], ...] = ()
    dirty_paths: tuple[str, ...] = ()
    phase_owned_dirty_paths: tuple[str, ...] = ()
    previous_phase_owned_paths: tuple[str, ...] = ()
    unowned_dirty_paths: tuple[str, ...] = ()
    pre_existing_dirty_paths: tuple[str, ...] = ()
    phase_owned_dirty: bool = False
    terminal_summary: dict[str, Any] | None = None
    latest_metric: dict[str, Any] | None = None
    metrics_summary: dict[str, Any] | None = None
    closeout_terminal_status: str | None = None
    closeout_summary: dict[str, Any] | None = None
    work_units: dict[str, dict[str, Any]] = field(default_factory=dict)
    latest_work_unit: dict[str, Any] | None = None
    pipeline_mode: str = "standalone"
    schema_version: int = 2
    roadmap_sha256: str | None = None
    phase_sha256: dict[str, str] = field(default_factory=dict)
    ledger_warnings: tuple[dict[str, Any], ...] = ()
    ledger_duplicates_skipped: tuple[dict[str, Any], ...] = ()
    git_topology: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        for status in self.phases.values():
            require_literal(status, PHASE_STATUSES, "phase status")
        if self.blocker_class is not None:
            require_literal(self.blocker_class, BLOCKER_CLASSES, "blocker class")
        require_literal(self.pipeline_mode, PIPELINE_MODE_LITERALS, "pipeline mode")
        for key, value in self.work_units.items():
            if not isinstance(value, dict):
                raise ValueError(f"invalid work-unit state record: {key}")
            WorkUnitState.from_json(value)

    def to_json(self) -> dict[str, Any]:
        return clean_dict(asdict(self))


def clean_dict(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: clean_dict(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [clean_dict(v) for v in value]
    if isinstance(value, tuple):
        return [clean_dict(v) for v in value]
    return value


def metadata_command(command: list[str], prompt: str | None = None) -> list[str]:
    redacted = list(command)
    if prompt is not None and redacted and redacted[-1] == prompt:
        redacted[-1] = f"<prompt redacted sha256={hashlib.sha256(prompt.encode('utf-8')).hexdigest()}>"
    return redacted
