from __future__ import annotations

import argparse
import json
import logging
import os
import shlex
import sys
import time
from pathlib import Path

_LOGGER = logging.getLogger("phase_loop_runtime.cli")

from .closeout import build_phase_loop_closeout
from .discovery import find_plan_artifact, phase_source_bundle_diagnostic, resolve_repo, resolve_suite_command, select_roadmap
from .events import append_event, read_events
from .git_topology import collect_git_topology
from .handoff import handoff_metadata, write_tui_handoff
from .install_status import build_install_status
from .models import CLAUDE_EXECUTION_MODES, CLOSEOUT_MODES, EXECUTORS, LANE_IR_DIAGNOSTIC_KINDS, LANE_SCHEDULER_MODES, LoopEvent, PHASE_SCHEDULER_MODES, PipelinePlanMetadata, StateSnapshot, utc_now
from .events_migration import MigrationError, migrate_ledger
from .migrate_handoffs import migrate_handoffs, records_to_json
from .observability import append_work_unit_metric, build_notification_payload, build_terminal_summary, build_work_unit_metric, hotfix_run_artifacts, run_notification_command
from .pipeline_adapter.flag import allow_lane_ir_override_enabled, dispatch_lock_enabled, parallel_dispatch_enabled
from .profiles import DEFAULT_PROFILES
from .provenance import ValidationFinding, event_provenance, snapshot_provenance, validate_roadmap_phase_headings
from .reconcile import reconcile
from .render import render_archive_result, render_skill_sync_result, render_state_inspection, render_status
from .runner import run_loop, status_snapshot
from .skill_install import actions_to_json, install_skills
# DECOUPLE SL-1: the dotfiles-domain modules (adoption_bundle, build_bundle,
# maintenance) and runtime_projection are NOT imported at module level. The
# generic CLI registers no dotfiles-domain command at import; those commands are
# registered by the dotfiles-profile plugin (loaded via the
# phase_loop_runtime.profile_commands entry-point group or the
# PHASE_LOOP_PROFILE_PLUGINS opt-in), and the handlers that remain in this module
# import their dotfiles dependencies lazily.
from .state import write_state
from .state_degradation import clear as clear_degradation
from .state_ops import archive_state, inspect_state
from .verification_evidence import ARTIFACT_NAME, LOG_NAME, detect_changed_dependency_manifests, resolve_install_command, run_verification, validate_verification_artifact
from . import __version__


def _add_common_subparser_args(sub: argparse.ArgumentParser, *, name: str) -> None:
    """Add the shared per-subcommand arguments.

    Factored out of build_parser() so the dotfiles-profile plugin (DECOUPLE SL-1)
    can attach the identical common args to the commands it registers.
    """
    if name == "closeout-drift-audit":
        sub.add_argument("--repo", action="append", help="Repo to audit. Repeat for cross-repo aggregation.")
    else:
        sub.add_argument("--repo")
    sub.add_argument("--roadmap")
    sub.add_argument("--phase")
    sub.add_argument(
        "--max-phases",
        type=int,
        help="Maximum dispatched actions by default; combine with --full-phase to count complete phase cycles.",
    )
    sub.add_argument("--model-profile", choices=tuple(DEFAULT_PROFILES))
    sub.add_argument("--model")
    sub.add_argument("--effort")
    sub.add_argument("--executor", choices=EXECUTORS)
    sub.add_argument("--command-name")
    sub.add_argument("--command-template")
    sub.add_argument("--claude-execution-mode", choices=CLAUDE_EXECUTION_MODES)
    sub.add_argument("--allow-executor", action="append", default=[])
    sub.add_argument("--fallback-executor", action="append", default=[])
    sub.add_argument("--disable-executor", action="append", default=[])
    sub.add_argument("--require-capability", action="append", default=[])
    sub.add_argument("--json", action="store_true")
    sub.add_argument("--dry-run", action="store_true")
    sub.add_argument("--observe", action="store_true", help="Accepted for compatibility; launch artifacts are written by default.")
    sub.add_argument("--no-observe", action="store_true", help="Disable launch log and heartbeat artifacts.")
    sub.add_argument("--stream-output", action="store_true")
    sub.add_argument("--bypass-approvals", action="store_true")
    sub.add_argument("--heartbeat-interval-seconds", type=int)
    sub.add_argument("--quiet-warning-seconds", type=int)
    sub.add_argument("--quiet-blocker-seconds", type=int)
    sub.add_argument("--no-heartbeat", action="store_true")
    sub.add_argument("--work-unit-mode", action="store_true")
    sub.add_argument("--source-bundle")
    sub.add_argument("--pipeline-mode", choices=("standalone", "pipeline_optional", "pipeline_required"), default=argparse.SUPPRESS)
    sub.add_argument(
        "--lane-scheduler",
        choices=LANE_SCHEDULER_MODES,
        dest="lane_scheduler_mode",
        default=argparse.SUPPRESS,
    )


def _profile_command_registrars():
    """Yield profile-command registrar callables.

    Sources (DECOUPLE SL-1):
      1. the ``phase_loop_runtime.profile_commands`` entry-point group (declared by
         an installed profile distribution); and
      2. the explicit ``PHASE_LOOP_PROFILE_PLUGINS`` opt-in -- a comma-separated
         list of ``module:callable`` specs -- used in source-mode runs and tests
         where no distribution metadata declares the group. (Comma, not the path
         separator, since ``module:callable`` already contains a colon.)

    With neither configured (e.g. a clean wheel install with no profile plugin),
    this yields nothing and the generic CLI exposes no dotfiles-domain command.
    """
    import importlib
    import importlib.metadata

    # Dedupe by the loaded callable's identity: the same registrar can be reachable
    # via BOTH the entry-point group (installed dist-info) and the
    # PHASE_LOOP_PROFILE_PLUGINS opt-in (e.g. the in-tree dotfiles profile is
    # registered under the group AND opted in by the test conftest). Without this,
    # the registrar would run twice and add each subparser twice.
    registrars: list = []
    seen: set = set()

    def _add(registrar) -> None:
        key = id(registrar)
        if key in seen:
            return
        seen.add(key)
        registrars.append(registrar)

    try:
        entry_points = importlib.metadata.entry_points(group="phase_loop_runtime.profile_commands")
    except TypeError:  # pragma: no cover - py<3.10 selectable API
        entry_points = importlib.metadata.entry_points().get("phase_loop_runtime.profile_commands", [])
    for entry_point in entry_points:
        try:
            _add(entry_point.load())
        except Exception as exc:  # a broken plugin must not break the CLI -- but be loud
            _LOGGER.warning("failed to load profile-command plugin %r: %s", getattr(entry_point, "name", entry_point), exc)

    opt_in = os.environ.get("PHASE_LOOP_PROFILE_PLUGINS", "")
    for spec in opt_in.split(","):
        spec = spec.strip()
        if not spec or ":" not in spec:
            continue
        module_name, _, attr = spec.partition(":")
        try:
            module = importlib.import_module(module_name)
            _add(getattr(module, attr))
        except Exception as exc:  # a bad opt-in spec must not break the CLI -- but be loud
            _LOGGER.warning("failed to load profile-command plugin from opt-in %r: %s", spec, exc)
    return tuple(registrars)


def _register_profile_commands(subparsers) -> None:
    for registrar in _profile_command_registrars():
        registrar(subparsers)


def build_parser_with_profile(opt_in: str) -> argparse.ArgumentParser:
    """Build a parser with a specific profile plugin opt-in (test/tooling helper)."""
    previous = os.environ.get("PHASE_LOOP_PROFILE_PLUGINS")
    os.environ["PHASE_LOOP_PROFILE_PLUGINS"] = opt_in
    try:
        return build_parser()
    finally:
        if previous is None:
            os.environ.pop("PHASE_LOOP_PROFILE_PLUGINS", None)
        else:
            os.environ["PHASE_LOOP_PROFILE_PLUGINS"] = previous


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="phase-loop",
        description="Neutral phase-loop runner. codex-phase-loop remains a Codex bridge alias.",
        allow_abbrev=False,
    )
    parser.add_argument("--version", action="version", version=f"phase-loop {__version__}")
    parser.add_argument("--repo", default=".")

    parser.add_argument("--roadmap")
    parser.add_argument("--phase")
    parser.add_argument(
        "--max-phases",
        type=int,
        help="Maximum dispatched actions by default; combine with --full-phase to count complete phase cycles.",
    )
    parser.add_argument("--full-phase", action="store_true", help="Count --max-phases as complete plan-plus-execute phase cycles.")
    parser.add_argument(
        "--phase-scheduler",
        dest="phase_scheduler_mode",
        choices=PHASE_SCHEDULER_MODES,
        default="off",
        help="Cross-phase scheduling mode (v45): 'off'/'serialized' dispatch one ready phase at a time; 'concurrent' dispatches the full ready wave.",
    )
    parser.add_argument("--no-deprecation-hints", action="store_true", help="Suppress legacy --max-phases action-count hints.")
    parser.add_argument("--model-profile", choices=tuple(DEFAULT_PROFILES))
    parser.add_argument("--model")
    parser.add_argument("--effort")
    parser.add_argument("--executor", choices=EXECUTORS)
    parser.add_argument("--command-name")
    parser.add_argument("--command-template")
    parser.add_argument("--claude-execution-mode", choices=CLAUDE_EXECUTION_MODES)
    parser.add_argument("--allow-executor", action="append", default=[])
    parser.add_argument("--fallback-executor", action="append", default=[])
    parser.add_argument("--disable-executor", action="append", default=[])
    parser.add_argument("--require-capability", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--observe", action="store_true", help="Accepted for compatibility; launch artifacts are written by default.")
    parser.add_argument("--no-observe", action="store_true", help="Disable launch log and heartbeat artifacts.")
    parser.add_argument("--stream-output", action="store_true")
    parser.add_argument("--bypass-approvals", action="store_true")
    parser.add_argument("--heartbeat-interval-seconds", type=int, default=30)
    parser.add_argument("--quiet-warning-seconds", type=int, default=600)
    parser.add_argument("--quiet-blocker-seconds", type=int, default=1800)
    parser.add_argument("--no-heartbeat", action="store_true")
    parser.add_argument("--closeout-mode", choices=CLOSEOUT_MODES, default="manual")
    parser.add_argument("--work-unit-mode", action="store_true")
    parser.add_argument("--rotate-executors")
    parser.add_argument("--rotation-mode", choices=("phase", "work_unit"), default="phase")
    parser.add_argument("--rotation-on-policy-pin", choices=("skip", "fallback-next"), default="skip")
    parser.add_argument("--lane-scheduler", choices=LANE_SCHEDULER_MODES, dest="lane_scheduler_mode")
    parser.add_argument("--source-bundle")
    parser.add_argument("--pipeline-mode", choices=("standalone", "pipeline_optional", "pipeline_required"), default="standalone")
    parser.add_argument("--force-replan", action="store_true")
    parser.add_argument(
        "--allow-cross-phase-dirty",
        help="Explicitly bypass the cross-phase dirty start gate. Requires a non-empty operator reason.",
    )
    subparsers = parser.add_subparsers(dest="command")
    # DECOUPLE SL-1: the dotfiles-domain commands (adoption-bundle, sync-skills,
    # build-bundle, hotfix) are NOT in this loop. They are registered only by the
    # dotfiles-profile plugin (see _register_profile_commands below), so the
    # generic CLI exposes none of them at import.
    for name in ("run", "resume", "status", "dry-run", "maintain-skills", "install", "state", "handoff", "archive-state", "monitor", "version", "execute", "reconcile", "reopen", "migrate-handoffs", "migrate-events", "init", "evidence-audit", "closeout-drift-audit", "validate-roadmap", "export-schema"):
        sub = subparsers.add_parser(name)
        if name == "execute":
            sub.add_argument("phase_arg", metavar="phase", help="The phase alias to execute.")
            sub.add_argument("--bundle", help="Path to a phase-source-bundle.v1 artifact.")
            sub.add_argument("--output", help="Path where exactly one closeout JSON file must be written.")
            sub.add_argument("--mode", help="The execution mode: execute, repair, or review.")
        _add_common_subparser_args(sub, name=name)
        if name in {"run", "resume", "dry-run"}:
            sub.add_argument("--closeout-mode", choices=CLOSEOUT_MODES)
            sub.add_argument("--force-replan", action="store_true")
            sub.add_argument("--no-dispatch-lock", action="store_true", help="Disable the per-roadmap dispatch lock for this run.")
            if parallel_dispatch_enabled():
                sub.add_argument("--parallel-dispatch", action="store_true", help="Run roadmap phases through the serial coordinator wave loop.")
            sub.add_argument(
                "--allow-cross-phase-dirty",
                help="Explicitly bypass the cross-phase dirty start gate. Requires a non-empty operator reason.",
            )
            sub.add_argument(
                "--closeout-allow-unowned",
                help=(
                    "Break-glass: force-commit the verified UNSAFE-unowned closeout remainder "
                    "(source/ci/lockfile) under the supplied operator reason, recorded as a "
                    "break_glass exception. secrets are NEVER break-glassable. Requires a "
                    "non-empty reason; pair with --phase to bound the blast radius."
                ),
            )
            if allow_lane_ir_override_enabled():
                sub.add_argument(
                    "--allow-lane-ir-override",
                    help="Comma-separated lane-IR diagnostic kinds to override. Requires --reason.",
                )
                sub.add_argument("--reason", help="Operator-supplied reason for the lane-IR override audit trail.")
            sub.add_argument("--reset-capability", action="store_true")
            sub.add_argument("--rotate-executors")
            sub.add_argument("--rotation-mode", choices=("phase", "work_unit"))
            sub.add_argument("--rotation-on-policy-pin", choices=("skip", "fallback-next"))
            sub.add_argument("--enable-tier-3", action="store_true", help="Enable default-off closeout-time Tier 3 evidence audit.")
            sub.add_argument("--tier-3-budget", type=int, default=3, help="Maximum Tier 3 evidence-audit calls per closeout. Default 3.")
        if name in {"run", "resume"}:
            sub.add_argument("--full-phase", action="store_true", help="Count --max-phases as complete plan-plus-execute phase cycles.")
            sub.add_argument("--no-deprecation-hints", action="store_true", help="Suppress legacy --max-phases action-count hints.")
        if name == "maintain-skills":
            sub.description = "Skill Maintenance: planner-only by default; edits require --apply-skill-edits and --allow-skill."
            sub.add_argument("--min-reflections", type=int, default=2)
            sub.add_argument("--apply-skill-edits", action="store_true")
            sub.add_argument("--allow-skill", action="append", default=())
            sub.add_argument("--improvement-plan")
        if name == "validate-roadmap":
            sub.description = "Mechanically lint a phase-plan roadmap spec (headings, aliases, IF-gates, DAG, lane hints)."
            sub.add_argument("roadmap_path", nargs="?", help="Path to the roadmap spec. Falls back to --roadmap / auto-detection.")
        if name == "install":
            sub.description = "Install harness-prefixed workflow skills from a harness-neutral phase-loop skills bundle."
            sub.add_argument("--harness", choices=("codex", "claude", "gemini", "opencode"))
            sub.add_argument("--source", default="vendor/phase-loop-skills")
            sub.add_argument("--destination")
            sub.add_argument("--status", action="store_true")
            mode = sub.add_mutually_exclusive_group()
            mode.add_argument("--symlink", action="store_true")
            mode.add_argument("--copy", action="store_true")
            sub.add_argument("--apply", action="store_true")
        if name == "status":
            sub.add_argument("--runtime-projection", action="store_true")
            sub.add_argument("--tier-3-history", action="store_true", help="Print recent Tier 3 evidence-audit summaries without raw prompts or responses.")
            sub.add_argument("--ledger-debug", action="store_true", help="Print redacted rejected ledger event diagnostics.")
        if name == "migrate-handoffs":
            sub.description = "Move current-repo legacy skill handoffs into repo-local .dev-skills storage."
            sub.add_argument("--apply", action="store_true")
        if name == "migrate-events":
            sub.description = "Migrate legacy DEF-4 executor closeout event action tokens in .phase-loop/events.jsonl."
            sub.add_argument("--backup-suffix", default=".bak-before-def4-migrate")
        if name == "init":
            sub.add_argument("--install-hooks", action="store_true", help="Install opt-in local git hooks for this repo.")
        if name == "archive-state":
            sub.add_argument("--reason")
        if name == "reconcile":
            sub.description = (
                "Synthesize a v28-shape manual_repair completion event, or recover a blocked "
                "dirty-state phase to planned/unplanned without marking verification passed."
            )
            sub.add_argument("--closeout-commit", help="Commit SHA to record as the closeout commit. Defaults to current HEAD.")
            sub.add_argument("--repair-summary", help="Optional human-authored note explaining the repair.")
            reconcile_transition = sub.add_mutually_exclusive_group()
            reconcile_transition.add_argument(
                "--to-status",
                choices=("planned",),
                help=(
                    "Recover a dirty-state-derived blocked phase back to planned when a current "
                    "plan exists, or unplanned when no plan exists. Requires --reason and records "
                    "verification_status=not_run."
                ),
            )
            # phase-loop reconcile always records status=complete (line 756);
            # per the field-pair invariants in BAML/closeout schema, complete
            # requires verification_status=passed. Other values are silently
            # accepted today and create state-machine contradictions (see #11
            # part B4). Restrict to passed to reject invalid combos at parse time.
            reconcile_transition.add_argument(
                "--verification-status",
                choices=("passed",),
                help=(
                    "Must be 'passed'. phase-loop reconcile records status=complete; "
                    "the field-pair invariants require verification_status=passed. "
                    "If your phase is a dirty-state blocked recovery, use "
                    "--to-status planned --reason <text> instead."
                ),
            )
            sub.add_argument("--verification-log", help="Path to the runner-owned verification artifact required with --verification-status passed.")
            sub.add_argument("--reason", help="Required with --to-status planned. Recorded on manual_recovery.")
            sub.add_argument("--allow-dirty", action="store_true", help="Override the refuse-if-dirty guard. Not recommended.")
            sub.add_argument("--recovery-mode", action="store_true", help="Allow dirty recovery-state reconciliation with explicit audit fields.")
            sub.add_argument(
                "--force",
                action="store_true",
                help=(
                    "With --to-status planned: bypass the sticky-blocker allowlist on operator "
                    "attestation. Recorded as forced_recovery=true in the manual_recovery event. "
                    "Required --reason becomes the audit trail. Use only when you know the underlying "
                    "blocker condition has been resolved."
                ),
            )
        if name == "reopen":
            sub.description = (
                "Reverse a spurious closeout: append a typed phase_reopen event so the reducer "
                "flips the named phase from complete back to planned. Use when an executor reported "
                "complete + verification_status=passed but the underlying IF gates were not actually "
                "satisfied (e.g., zero-diff repair iteration that did not produce the phase's work)."
            )
            sub.add_argument("--reason", required=True, help="Operator-supplied reason for reopening. Recorded on the event.")
            sub.add_argument("--allow-dirty", action="store_true", help="Override the refuse-if-dirty guard. Not recommended.")
        if name == "monitor":
            sub.add_argument("--poll-seconds", type=int, default=60)
            sub.add_argument("--timeout-seconds", type=int)
            sub.add_argument("--notify-command")
            sub.add_argument(
                "--notify-on",
                action="append",
                default=[],
                choices=("blocked", "stale", "complete", "awaiting_phase_closeout", "operator_halt", "terminal_exit"),
            )
            sub.add_argument("--once", action="store_true")
        if name == "evidence-audit":
            sub.description = (
                "Operator-callable spot-check for fake-evidence patterns in dirty-tree artifacts. "
                "Detects: (1) duplicate-content — N or more files share the same sha256 "
                "(e.g., 19 \"distinct\" PNGs all the same placeholder); (2) uniform-numeric — "
                "numeric arrays >= 4 elements where all values are within epsilon "
                "(e.g., 19/19 similarity scores at 0.999999); (3) missing-references — JSON "
                "artifacts cite path-shaped strings that don't exist on disk. "
                "Run before `phase-loop reconcile` on phases producing comparison/verdict evidence. "
                "Exits 0 if clean, 5 if suspect findings."
            )
            sub.add_argument("--dirty-only", action="store_true", default=True, help="Audit only currently-modified/untracked paths (default).")
            sub.add_argument("--full-tree", dest="dirty_only", action="store_false", help="Audit every tracked file in the repo with strict missing-reference scanning (slow).")
            sub.add_argument("--full-tree-loose", action="store_true", help="Audit every tracked file and use loose forensic missing-reference scanning.")
            sub.add_argument("--min-duplicates", type=int, default=3, help="Min number of files sharing a sha256 before flagging. Default 3.")
            sub.add_argument("--uniform-epsilon", type=float, default=1e-6, help="Numeric uniformity tolerance. Default 1e-6.")
            sub.add_argument("--uniform-min-length", type=int, default=4, help="Min array length to check for uniformity. Default 4.")
            sub.add_argument("--tier-2", action="store_true", help="Enable Tier 2 fuzzy detectors: loose-uniform, boilerplate-text, and size-distribution.")
            sub.add_argument("--loose-uniform-stdev-threshold", type=float, default=1e-3, help="Tier 2 coefficient-of-variation threshold for near-uniform numeric arrays. Default 1e-3.")
            sub.add_argument("--boilerplate-token-overlap-threshold", type=float, default=0.80, help="Tier 2 token overlap threshold for boilerplate text groups. Default 0.80.")
            sub.add_argument("--boilerplate-min-group-size", type=int, default=3, help="Tier 2 minimum text file group size for boilerplate detection. Default 3.")
            sub.add_argument("--size-distribution-variance-threshold", type=float, default=0.05, help="Tier 2 coefficient-of-variation threshold for sibling file sizes. Default 0.05.")
            sub.add_argument("--size-distribution-min-group-size", type=int, default=3, help="Tier 2 minimum sibling file group size for size-distribution detection. Default 3.")
            sub.add_argument("--enable-tier-3", action="store_true", help="Enable default-off Tier 3 LLM judgment for Tier 2 uncertain findings only.")
        if name == "closeout-drift-audit":
            sub.description = "Audit phase-loop closeout literals for drift from runtime allowlists."
            sub.add_argument("--days", type=int, default=7, help="Lookback window in days. Default 7.")
            sub.add_argument("--scope", choices=("closeout", "all-events"), default="closeout", help="Audit closeout payloads by default; use all-events for forensic scans.")
        if name == "export-schema":
            sub.description = (
                "Emit (or --check) the canonical phase-loop closeout schema derived from "
                "PhaseLoopCloseout. Repo-independent; the bundled artifact is the parity "
                "source consumers (gp) diff against."
            )
            sub.add_argument("--output", help="Path to write the emitted schema/field-list. Defaults to stdout.")
            sub.add_argument(
                "--check",
                metavar="PATH",
                help="Compare a supplied artifact against the in-package canonical schema; exit non-zero on any divergence.",
            )
            sub.add_argument(
                "--format",
                choices=("json-schema", "field-list"),
                default="json-schema",
                help="Output format: a declared JSON-Schema (default) or the flat field-list gp consumes.",
            )
    # DECOUPLE SL-1: dotfiles-domain commands are added here, only when a profile
    # plugin is installed/opted-in. A clean wheel registers none.
    _register_profile_commands(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or ("dry-run" if args.dry_run else "run")
    allow_cross_phase_dirty_reason = getattr(args, "allow_cross_phase_dirty", None)
    if allow_cross_phase_dirty_reason is not None:
        allow_cross_phase_dirty_reason = str(allow_cross_phase_dirty_reason).strip()
        if not allow_cross_phase_dirty_reason:
            parser.error("--allow-cross-phase-dirty requires a non-empty reason")
    if command not in {"run", "resume", "dry-run"} and allow_cross_phase_dirty_reason is not None:
        parser.error("--allow-cross-phase-dirty is only valid for run, resume, and dry-run")

    allow_unowned_reason = getattr(args, "closeout_allow_unowned", None)
    if allow_unowned_reason is not None:
        allow_unowned_reason = str(allow_unowned_reason).strip()
        if not allow_unowned_reason:
            parser.error(
                "--closeout-allow-unowned requires a non-empty reason "
                "(blocker_class=operator_override_missing_reason)"
            )
    if command not in {"run", "resume", "dry-run"} and allow_unowned_reason is not None:
        parser.error("--closeout-allow-unowned is only valid for run, resume, and dry-run")
    lane_ir_override_kinds = _parse_lane_ir_override(parser, args, command)
    if command not in {"run", "resume"} and (
        bool(getattr(args, "full_phase", False)) or bool(getattr(args, "no_deprecation_hints", False))
    ):
        parser.error("--full-phase and --no-deprecation-hints are only valid for run and resume")
    if command == "version":
        print(f"phase-loop {__version__}")
        return 0
    if command == "export-schema":
        return _export_schema_command(args=args)
    if command == "validate-roadmap":
        from . import roadmap_lint

        candidate = getattr(args, "roadmap_path", None) or args.roadmap
        if not candidate:
            repo = resolve_repo(args.repo or ".")
            candidate = select_roadmap(repo, None)
        if not candidate:
            parser.error("validate-roadmap requires a roadmap path (positional, --roadmap, or auto-detectable)")
        return roadmap_lint.main(["validate-roadmap", str(candidate)])
    as_json = bool(args.json)
    if command == "closeout-drift-audit":
        if args.roadmap:
            repo_args = args.repo or ["."]
            if isinstance(repo_args, str):
                repo_args = [repo_args]
            for repo_arg in repo_args:
                audit_repo = resolve_repo(repo_arg)
                _warn_roadmap_validation(select_roadmap(audit_repo, args.roadmap))
        return _closeout_drift_audit_command(args=args, as_json=as_json)
    repo = resolve_repo(args.repo or ".")
    # DECOUPLE SL-1: profile-plugin commands (adoption-bundle, sync-skills,
    # build-bundle, hotfix) register a `func` default and are dispatched here,
    # so this generic dispatcher never names a dotfiles-domain command.
    profile_func = getattr(args, "func", None)
    if profile_func is not None:
        return profile_func(repo=repo, args=args, as_json=as_json)
    if command == "init":
        return _init_command(repo=repo, dry_run=bool(args.dry_run), as_json=as_json, install_hooks=bool(getattr(args, "install_hooks", False)))
    if command == "evidence-audit":
        if args.roadmap:
            _warn_roadmap_validation(select_roadmap(repo, args.roadmap))
        return _evidence_audit_command(repo=repo, args=args, as_json=as_json)
    if command in {"run", "resume", "dry-run"} and bool(getattr(args, "reset_capability", False)):
        clear_degradation(repo)

    if command == "execute":
        phase = args.phase_arg
        output_path = args.output
        execute_roadmap = select_roadmap(repo, args.roadmap)
        _warn_roadmap_validation(execute_roadmap)
        if not output_path:
            snapshot = StateSnapshot(
                timestamp=utc_now(),
                repo=str(repo),
                roadmap=str(args.roadmap or ""),
                phases={},
                current_phase=phase,
                last_action=command,
                human_required=False,
                blocker_class="contract_bug",
                blocker_summary="Direct invocation 'execute' requires --output <path>.",
                **snapshot_provenance(execute_roadmap),
                )

            write_state(repo, snapshot)
            print(render_status(snapshot, as_json=as_json))
            return 1
        source_bundle_path = args.bundle or args.source_bundle
        mode = args.mode or "execute"
        if mode not in {"execute", "repair", "review"}:
            return _direct_invocation_blocker(
                repo=repo,
                args=args,
                phase=phase,
                output_path=Path(output_path),
                blocker_summary=f"Direct invocation 'execute' does not support --mode {mode!r}.",
                diagnostic_kind="invalid_pipeline_mode",
                as_json=as_json,
            )
        effective_pipeline_mode = args.pipeline_mode or ("pipeline_optional" if source_bundle_path else "standalone")
        bundle_diagnostic = phase_source_bundle_diagnostic(
            repo,
            source_bundle_path,
            phase=phase,
            roadmap=execute_roadmap,
            pipeline_mode=effective_pipeline_mode,
        )
        if bundle_diagnostic is not None:
            return _direct_invocation_blocker(
                repo=repo,
                args=args,
                phase=phase,
                output_path=Path(output_path),
                blocker_summary=f"Pipeline source bundle validation failed: {bundle_diagnostic.kind}",
                diagnostic_kind=bundle_diagnostic.kind,
                as_json=as_json,
            )
        # Map 'execute' subcommand to the requested mode action
        command = mode

    if command == "install":
        if bool(getattr(args, "status", False)):
            payload = build_install_status(repo, harnesses=(args.harness,) if args.harness else None)
            if as_json:
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(f"install_status: {payload.get('summary', 'unknown')}")
            return 0
        if not args.harness:
            print("phase-loop install: --harness is required unless --status is used", file=sys.stderr)
            return 2
        source = Path(args.source)
        if not source.is_absolute():
            source = repo / source
        destination = Path(args.destination).expanduser() if args.destination else None
        mode = "copy" if bool(args.copy) else "symlink"
        actions = install_skills(
            harness=args.harness,
            source=source,
            destination=destination,
            mode=mode,
            apply=bool(args.apply),
        )
        if as_json:
            print(actions_to_json(actions))
        else:
            verb = "applied" if args.apply else "planned"
            for action in actions:
                print(
                    f"{verb}\t{action.harness}\t{action.installed_name}\t"
                    f"{action.mode}\t{action.action}\t{action.destination}"
                )
        return 0
    if command == "migrate-handoffs":
        records = migrate_handoffs(repo, apply=bool(getattr(args, "apply", False)))
        if as_json:
            print(records_to_json(records))
        else:
            for record in records:
                print(f"{record.status}\t{record.action}\t{record.skill_name}\t{record.source}\t{record.target}")
        return 1 if any(record.action == "blocked" for record in records) else 0
    if command == "migrate-events":
        return _migrate_events_command(repo=repo, dry_run=bool(args.dry_run), backup_suffix=args.backup_suffix)
    if command == "archive-state":
        print(render_archive_result(
            archive_state(repo, reason=getattr(args, "reason", None), dry_run=bool(getattr(args, "dry_run", False))),
            as_json=as_json,
        ))
        return 0
    try:
        roadmap = select_roadmap(repo, args.roadmap)
        _warn_roadmap_validation(roadmap)
    except RuntimeError as exc:
        if "ambiguous roadmap selection" not in str(exc):
            raise
        if command == "state":
            print(render_state_inspection(inspect_state(repo), as_json=as_json))
            return 0
        snapshot = StateSnapshot(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(args.roadmap or ""),
            phases={},
            current_phase=None,
            last_action=command,
            human_required=True,
            blocker_class="ambiguous_roadmap_selection",
            blocker_summary="Multiple roadmap artifacts are plausible; pass --roadmap or create valid active state/handoff evidence.",
            required_human_inputs=("explicit roadmap path or valid roadmap handoff",),
        )
        write_state(repo, snapshot)
        print(render_status(snapshot, as_json=as_json))
        return 2

    if command == "state":
        print(render_state_inspection(inspect_state(repo, roadmap), as_json=as_json))
        return 0
    if command == "monitor":
        result = monitor_loop(
            repo,
            roadmap,
            poll_seconds=getattr(args, "poll_seconds", 60) or 60,
            timeout_seconds=getattr(args, "timeout_seconds", None),
            notify_command=getattr(args, "notify_command", None),
            notify_on=tuple(getattr(args, "notify_on", ()) or ()),
            once=bool(getattr(args, "once", False)),
            as_json=as_json,
        )
        print(result["rendered"])
        return int(result["returncode"])
    if command == "status":
        snapshot = status_snapshot(repo, roadmap, pipeline_mode=args.pipeline_mode or "standalone")
        write_state(repo, snapshot)
        write_tui_handoff(repo, roadmap, snapshot, action="status")
        if bool(getattr(args, "tier_3_history", False)):
            print(_tier_3_history(repo, as_json=as_json))
            return 0
        if bool(getattr(args, "runtime_projection", False)):
            # DECOUPLE SL-1: lazy import keeps runtime_projection out of
            # `import phase_loop_runtime.cli`.
            from .runtime_projection import build_runtime_projection

            projection = build_runtime_projection(
                repo,
                roadmap,
                snapshot=snapshot,
                pipeline_mode=args.pipeline_mode or "standalone",
            )
            print(json.dumps(projection, indent=2, sort_keys=True) if as_json else json.dumps(projection, sort_keys=True))
            return 0
        print(render_status(snapshot, as_json=as_json, ledger_debug=bool(getattr(args, "ledger_debug", False))))
        return 0
    if command == "handoff":
        snapshot = reconcile(repo, roadmap)
        write_state(repo, snapshot)
        path = write_tui_handoff(repo, roadmap, snapshot, action="handoff")
        if as_json:
            print(render_handoff_json(path, repo))
        else:
            print(path.read_text(encoding="utf-8"), end="")
        return 0
    if command == "reconcile":
        return _reconcile_command(repo=repo, roadmap=roadmap, args=args, as_json=as_json)
    if command == "reopen":
        return _reopen_command(repo=repo, roadmap=roadmap, args=args, as_json=as_json)

    dry_run = command == "dry-run" or bool(args.dry_run)
    model_profile = args.model_profile or ("skill-maintenance" if command == "maintain-skills" else None)

    # Use local variables for parameters that might come from the 'execute' shim
    effective_phase = getattr(args, "phase_arg", None) or args.phase
    effective_source_bundle = getattr(args, "bundle", None) or args.source_bundle
    effective_output_path = getattr(args, "output", None)
    if lane_ir_override_kinds:
        if not effective_phase:
            parser.error("--allow-lane-ir-override requires --phase")
        _append_lane_ir_override_event(
            repo=repo,
            roadmap=roadmap,
            phase=str(effective_phase).upper(),
            diagnostic_kinds=lane_ir_override_kinds,
            reason=str(getattr(args, "reason", "")).strip(),
        )
    if allow_unowned_reason:
        # Bound the blast radius structurally: break-glass is single-phase only, and the
        # attestation event reconcile reads is phase-scoped.
        if not effective_phase:
            parser.error("--closeout-allow-unowned requires --phase (single-phase break-glass scope)")
        _append_closeout_allow_unowned_event(
            repo=repo,
            roadmap=roadmap,
            phase=str(effective_phase).upper(),
            reason=allow_unowned_reason,
        )

    # --parallel-dispatch is the legacy serial coordinator-wave control; it maps
    # onto --phase-scheduler serialized so the two controls don't overlap.
    effective_phase_scheduler_mode = getattr(args, "phase_scheduler_mode", "off") or "off"
    if bool(getattr(args, "parallel_dispatch", False)):
        print(
            "warning: --parallel-dispatch is deprecated; use --phase-scheduler serialized.",
            file=sys.stderr,
        )
        if effective_phase_scheduler_mode == "off":
            effective_phase_scheduler_mode = "serialized"

    # DECOUPLE SL-1: lazy import keeps the maintenance module out of
    # `import phase_loop_runtime.cli` (used here only to carry skill-maintenance
    # options through to the run loop).
    from .maintenance import MaintenanceOptions

    snapshot, results = run_loop(
        repo=repo,
        roadmap=roadmap,
        phase=effective_phase,
        max_phases=args.max_phases or 1,
        max_phases_explicit=args.max_phases is not None,
        full_phase=bool(getattr(args, "full_phase", False)),
        no_deprecation_hints=bool(getattr(args, "no_deprecation_hints", False)),
        model_profile=model_profile,
        model=args.model,
        effort=args.effort,
        dry_run=dry_run,
        json_output=as_json,
        action=command,
        executor=args.executor,
        allowed_executors=tuple(args.allow_executor or ()),
        fallback_executors=tuple(args.fallback_executor or ()),
        disabled_executors=tuple(args.disable_executor or ()),
        required_capabilities=tuple(args.require_capability or ()),
        observe=not bool(args.no_observe),
        stream_output=bool(args.stream_output),
        bypass_approvals=bool(args.bypass_approvals),
        heartbeat_interval_seconds=args.heartbeat_interval_seconds or 30,
        quiet_warning_seconds=args.quiet_warning_seconds or 600,
        quiet_blocker_seconds=args.quiet_blocker_seconds or 1800,
        heartbeat_enabled=not bool(args.no_heartbeat),
        closeout_mode=args.closeout_mode or "manual",
        enable_tier_3=bool(getattr(args, "enable_tier_3", False)),
        tier_3_budget=3 if getattr(args, "tier_3_budget", 3) is None else getattr(args, "tier_3_budget", 3),
        command_adapter_name=args.command_name,
        command_template=args.command_template,
        claude_execution_mode=args.claude_execution_mode,
        work_unit_mode=bool(args.work_unit_mode),
        rotate_executors=getattr(args, "rotate_executors", None),
        rotation_mode=getattr(args, "rotation_mode", None) or "phase",
        rotation_on_policy_pin=getattr(args, "rotation_on_policy_pin", None) or "skip",
        lane_scheduler_mode=args.lane_scheduler_mode or "off",
        source_bundle_path=effective_source_bundle,
        pipeline_mode=getattr(args, "pipeline_mode", None),
        output_path=effective_output_path,
        stuck_loop_iterations=getattr(args, "stuck_loop_iterations", 5),
        stuck_loop_minutes=getattr(args, "stuck_loop_minutes", 30),
        force_replan=bool(getattr(args, "force_replan", False)),
        dispatch_lock_enabled=dispatch_lock_enabled() and not bool(getattr(args, "no_dispatch_lock", False)),
        parallel_dispatch=bool(getattr(args, "parallel_dispatch", False)),
        phase_scheduler_mode=effective_phase_scheduler_mode,
        allow_cross_phase_dirty_reason=allow_cross_phase_dirty_reason,
        allow_unowned_reason=allow_unowned_reason,
        product_action_override=command if command in {"execute", "repair", "review"} else None,
        maintenance_options=MaintenanceOptions(
            min_reflections=getattr(args, "min_reflections", 2) or 2,
            apply_skill_edits=bool(getattr(args, "apply_skill_edits", False)),
            allow_skills=tuple(getattr(args, "allow_skill", ()) or ()),
            improvement_plan=Path(args.improvement_plan).expanduser().resolve() if getattr(args, "improvement_plan", None) else None,
        ),
    )
    if as_json:
        print(render_status(snapshot, as_json=True))
    else:
        for result in results:
            if result.executor:
                print("Executor:", result.executor)
            if result.injection_mode:
                print("Injection mode:", result.injection_mode)
            if result.expected_skill_pack:
                print("Expected skill pack:", ", ".join(result.expected_skill_pack))
            print("Command:", " ".join(result.command))
            if result.log_path:
                print("Log:", result.log_path)
        print(render_status(snapshot, as_json=False))
    return _run_returncode(snapshot, results)


def _parse_lane_ir_override(parser: argparse.ArgumentParser, args: argparse.Namespace, command: str) -> tuple[str, ...]:
    raw = getattr(args, "allow_lane_ir_override", None)
    if raw is None:
        return ()
    if command not in {"run", "resume", "dry-run"}:
        parser.error("--allow-lane-ir-override is only valid for run, resume, and dry-run")
    reason = str(getattr(args, "reason", "") or "").strip()
    if not reason:
        parser.error("--allow-lane-ir-override requires --reason (blocker_class=operator_override_missing_reason)")
    kinds = tuple(kind.strip() for kind in str(raw).split(",") if kind.strip())
    if not kinds:
        parser.error("--allow-lane-ir-override requires at least one diagnostic kind")
    unsupported = tuple(kind for kind in kinds if kind not in LANE_IR_DIAGNOSTIC_KINDS)
    if unsupported:
        parser.error(f"unsupported lane-IR diagnostic override kind(s): {', '.join(unsupported)}")
    return tuple(dict.fromkeys(kinds))


def _append_lane_ir_override_event(
    *,
    repo: Path,
    roadmap: Path,
    phase: str,
    diagnostic_kinds: tuple[str, ...],
    reason: str,
) -> None:
    plan = find_plan_artifact(repo, phase, roadmap=roadmap)
    append_event(
        repo,
        LoopEvent(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phase=phase,
            action="lane_ir_override",
            status="planned",
            model="operator",
            reasoning_effort="manual",
            source="cli",
            override_reason=reason,
            metadata={
                "runner.lane_ir_override_invoked": {
                    "diagnostic_kinds_overridden": list(diagnostic_kinds),
                    "plan_path": str(plan) if plan else None,
                    "operator_reason": reason,
                }
            },
            **event_provenance(roadmap, phase),
        ),
    )


def _append_closeout_allow_unowned_event(
    *,
    repo: Path,
    roadmap: Path,
    phase: str,
    reason: str,
) -> None:
    # BREAKGLASS attestation (IF-0-BREAKGLASS-1c): reconcile (SL-2) reads this to lift
    # the unowned_dirty_paths bail, scoped by roadmap_sha256/phase_sha256 (via
    # event_provenance) + phase + non-empty operator_reason — exactly like the lane-IR
    # override. A stale attestation (content drifted) no longer matches and does not
    # authorize a later closeout.
    plan = find_plan_artifact(repo, phase, roadmap=roadmap)
    append_event(
        repo,
        LoopEvent(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phase=phase,
            action="closeout_allow_unowned",
            status="planned",
            model="operator",
            reasoning_effort="manual",
            source="cli",
            override_reason=reason,
            metadata={
                "runner.closeout_allow_unowned_invoked": {
                    "plan_path": str(plan) if plan else None,
                    "operator_reason": reason,
                }
            },
            **event_provenance(roadmap, phase),
        ),
    )


def _format_roadmap_validation_warning(finding: ValidationFinding) -> str:
    return (
        "phase-loop roadmap warning: "
        f"line {finding.line_number}: {finding.reason}; "
        f"raw heading: {finding.raw_text!r}; "
        f"suggested fix: {finding.suggested_fix}"
    )


def _warn_roadmap_validation(roadmap: Path) -> None:
    for finding in validate_roadmap_phase_headings(roadmap):
        print(_format_roadmap_validation_warning(finding), file=sys.stderr)


def _tier_3_history(repo: Path, *, as_json: bool = False, limit: int = 10) -> str:
    records: list[dict[str, object]] = []
    for event in read_events(repo):
        if event.get("action") != "evidence_audit_tier3":
            continue
        metadata = event.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        records.append(
            {
                "timestamp": event.get("timestamp"),
                "phase": event.get("phase"),
                "verdict": metadata.get("verdict"),
                "confidence": metadata.get("confidence"),
                "estimated_cost_usd": metadata.get("estimated_cost_usd"),
                "latency_ms": metadata.get("latency_ms"),
            }
        )
    records = records[-limit:]
    if as_json:
        return json.dumps({"tier_3_history": records}, indent=2, sort_keys=True)
    if not records:
        return "Tier 3 history: no evidence_audit_tier3 events recorded."
    lines = ["Tier 3 history:"]
    for record in records:
        cost = record["estimated_cost_usd"]
        cost_text = "unknown" if cost is None else str(cost)
        latency = record["latency_ms"]
        latency_text = "unknown" if latency is None else f"{latency}ms"
        lines.append(
            "  "
            f"{record['timestamp']} phase={record['phase']} verdict={record['verdict']} "
            f"confidence={record['confidence']} cost_usd={cost_text} latency={latency_text}"
        )
    return "\n".join(lines)


def _run_returncode(snapshot: StateSnapshot, results: list) -> int:
    """Exit-code policy for `run` / `dry-run` / `execute` / `repair` / `review`.

    0 = success (all phases advanced cleanly).
    1 = at least one child launch failed (auth, sandbox, exec error).
    5 = no launch failure, but the run terminated with a blocker — distinct
        so bash rotation wrappers can stop polling stdout for
        `Terminal status: blocked`.
    """
    if any(result.failed for result in results):
        return 1
    terminal_summary = snapshot.terminal_summary if isinstance(snapshot.terminal_summary, dict) else None
    if terminal_summary and terminal_summary.get("terminal_status") == "blocked":
        return 5
    if snapshot.blocker_class:
        return 5
    return 0


def _adoption_bundle_command(*, repo: Path, action: str, as_json: bool) -> int:
    # DECOUPLE SL-1: dotfiles-domain import is lazy so `import phase_loop_runtime.cli`
    # does not pull in adoption_bundle.
    from .adoption_bundle import adoption_bundle_status, refresh_adoption_bundle

    try:
        if action == "status":
            payload = adoption_bundle_status(repo)
            code = 0 if payload["status"] == "fresh" else 1
        elif action == "refresh":
            payload = refresh_adoption_bundle(repo)
            code = 0
        else:
            raise ValueError(f"unknown adoption-bundle action: {action}")
    except Exception as exc:
        payload = {
            "status": "error",
            "bundle": "docs/adoption/dotfiles-adoption-bundle.json",
            "error": str(exc),
        }
        code = 2 if action == "status" else 1
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"adoption-bundle {action}: {payload['status']} {payload['bundle']}")
        if payload.get("error"):
            print(f"error: {payload['error']}", file=sys.stderr)
    return code


def _sync_skills_command(*, repo: Path, args: argparse.Namespace, as_json: bool) -> int:
    # DECOUPLE SL-1: lazy dotfiles-domain import.
    from .maintenance import SyncSkillsOptions, sync_bridge_skills

    harnesses = tuple(args.harness or ("codex", "claude", "gemini", "opencode"))
    summary = sync_bridge_skills(repo, SyncSkillsOptions(harnesses=harnesses, apply=bool(args.apply)))
    print(render_skill_sync_result(summary, as_json=as_json))
    blocker = summary.get("blocker")
    return 1 if isinstance(blocker, dict) and blocker.get("blocker_class") else 0


def _build_bundle_command(*, repo: Path, args: argparse.Namespace, as_json: bool) -> int:
    # DECOUPLE SL-1: lazy dotfiles-domain import.
    from .build_bundle import DEFAULT_SOURCES, build_bundle

    sources = list(args.source or DEFAULT_SOURCES.values())
    resolved_sources = [repo / source if not Path(source).is_absolute() else Path(source) for source in sources]
    destination = Path(args.destination)
    if not destination.is_absolute():
        destination = repo / destination
    dry_run = bool(args.dry_run or not args.apply)
    result = build_bundle(
        resolved_sources,
        destination,
        dry_run=dry_run,
        apply=bool(args.apply),
        force=bool(args.force),
    )
    if as_json or dry_run:
        print(result.to_json())
    else:
        verb = "applied" if result.applied else "planned"
        print(f"build-bundle {verb}: {len(result.files_written)} file changes")
        for skill in result.skills_regenerated:
            print(f"regenerated\t{skill}")
        for path in result.overrides_written:
            print(f"override\t{path}")
        for skipped in result.skills_skipped:
            print(f"skipped\t{skipped.skill}\tmissing={','.join(skipped.missing_harnesses)}")
        for warning in result.warnings:
            print(f"warning\t{warning.skill}\t{warning.message}")
    return 0


def _hotfix_command(*, repo: Path, args: argparse.Namespace, as_json: bool) -> int:
    init_stub = getattr(args, "init_stub", None)
    if init_stub:
        path = _repo_relative_path(repo, init_stub)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "objective: TODO\n"
            "verification_command: TODO\n",
            encoding="utf-8",
        )
        payload = {"status": "stub_initialized", "plan_stub": str(path), "executed": False}
        print(json.dumps(payload, indent=2, sort_keys=True) if as_json else f"hotfix stub initialized: {path}")
        return 0
    reason = str(getattr(args, "reason", "") or "").strip()
    if not reason:
        print("phase-loop hotfix: --reason is required unless --init-stub is used", file=sys.stderr)
        return 2
    plan_arg = getattr(args, "plan", None)
    if not plan_arg:
        print("phase-loop hotfix: --plan is required unless --init-stub is used", file=sys.stderr)
        return 2
    plan_stub = _repo_relative_path(repo, plan_arg)
    if not plan_stub.exists():
        print(f"phase-loop hotfix: plan stub not found: {plan_stub}", file=sys.stderr)
        return 2
    roadmap = select_roadmap(repo, args.roadmap)
    _warn_roadmap_validation(roadmap)
    commands = _hotfix_verification_commands(plan_stub)
    artifacts = hotfix_run_artifacts(repo, reason, plan_stub)
    manifests = detect_changed_dependency_manifests(repo, "HEAD")
    install_argv = resolve_install_command(repo, manifests) if manifests else None
    env_refresh = (
        {"triggered": True, "manifests": manifests, "install_argv": install_argv or [], "exit_code": 127}
        if manifests and install_argv is None
        else ({"triggered": True, "manifests": manifests, "install_argv": install_argv} if manifests else None)
    )
    suite_command = resolve_suite_command(repo, roadmap, None)
    run_verification(
        repo,
        artifacts["root"],
        commands,
        suite_command,
        env_refresh,
        float(os.environ.get("PHASE_LOOP_VERIFY_TIMEOUT_SECONDS", "1200")),
    )
    validation = validate_verification_artifact(artifacts["verification_artifact"])
    validation_json = validation.to_json()
    status = "complete" if validation.ok else "blocked"
    verification_status = "passed" if validation.ok else "blocked"
    blocker = None if validation.ok else {"blocker_class": "verification_evidence_missing", "blocker_summary": f"Hotfix verification failed: {validation.code}"}
    terminal_summary = build_terminal_summary(
        terminal_status=status,
        terminal_blocker=blocker,
        verification_status=verification_status,
        next_action="hotfix verification complete" if validation.ok else "repair hotfix verification failure",
        artifact_paths={
            "root": str(artifacts["root"]),
            "verification_artifact_path": str(artifacts["verification_artifact"]),
            "verification_log_path": str(artifacts["verification_log"]),
        },
        work_unit={"work_unit": "hotfix", "plan_stub": str(plan_stub)},
    )
    append_event(
        repo,
        LoopEvent(
            timestamp=utc_now(),
            repo=str(repo),
            roadmap=str(roadmap),
            phase="HOTFIX",
            action="hotfix.closeout",
            status=status,
            model="command",
            reasoning_effort="manual",
            source="cli",
            selected_executor="command",
            blocker=blocker,
            metadata={
                "work_unit": "hotfix",
                "hotfix_closeout": {
                    "work_unit": "hotfix",
                    "reason": _redact_hotfix_reason(reason),
                    "plan_stub": str(plan_stub),
                    "verification_artifact_path": str(artifacts["verification_artifact"]),
                    "verification_log_path": str(artifacts["verification_log"]),
                    "verification_exit_summary": validation_json.get("exit_summary", {}),
                    "artifact_validation": validation_json,
                },
                "terminal_summary": terminal_summary,
            },
            **event_provenance(roadmap, "HOTFIX"),
        ),
    )
    metric = build_work_unit_metric(
        repo=repo,
        phase="HOTFIX",
        action="execute",
        launch_metadata={
            "executor": "command",
            "selected_model": "phase-loop",
            "execution_policy": {"work_unit_kind": "lane_execute", "effort": "medium", "execution_policy_source": "hotfix cli"},
        },
        terminal_summary=terminal_summary,
        artifact_paths={
            "verification_artifact_path": str(artifacts["verification_artifact"]),
            "verification_log_path": str(artifacts["verification_log"]),
        },
    )
    append_work_unit_metric(repo, metric)
    payload = {
        "status": status,
        "verification_status": verification_status,
        "work_unit": "hotfix",
        "run_root": str(artifacts["root"]),
        "plan_stub": str(plan_stub),
        "verification_artifact_path": str(artifacts["verification_artifact"]),
        "verification_log_path": str(artifacts["verification_log"]),
        "artifact_validation": validation_json,
    }
    print(json.dumps(payload, indent=2, sort_keys=True) if as_json else f"hotfix {status}: {artifacts['root']}")
    return 0 if validation.ok else 1


def _repo_relative_path(repo: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else repo / path


def _hotfix_verification_commands(plan_stub: Path) -> list[list[str]]:
    commands: list[list[str]] = []
    in_list = False
    for raw in plan_stub.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("verification_command:"):
            command = stripped.split(":", 1)[1].strip()
            if command and command != "TODO":
                commands.append(shlex.split(command))
            in_list = False
            continue
        if stripped.startswith("verification_commands:"):
            in_list = True
            continue
        if in_list and stripped.startswith("- "):
            command = stripped[2:].strip()
            if command:
                commands.append(shlex.split(command))
    return commands


def _redact_hotfix_reason(reason: str) -> str:
    return " ".join(reason.split())[:200]


def _migrate_events_command(*, repo: Path, dry_run: bool, backup_suffix: str) -> int:
    try:
        result = migrate_ledger(repo, dry_run=dry_run, backup_suffix=backup_suffix)
    except MigrationError as exc:
        print(f"phase-loop migrate-events: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result.to_json(), sort_keys=True))
    return 0


def _init_command(*, repo: Path, dry_run: bool, as_json: bool, install_hooks: bool) -> int:
    gitignore = repo / ".gitignore"
    handoffs = repo / ".dev-skills" / "handoffs"
    hook_source = repo / ".githooks" / "pre-commit-adoption-bundle"
    hook_target = repo / ".git" / "hooks" / "pre-commit"
    entry = "/.dev-skills/"
    existing = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.exists() else []
    needs_entry = entry not in existing
    needs_handoffs = not handoffs.is_dir()
    hook_installable = hook_source.exists()
    needs_hook = install_hooks and (not hook_target.exists() or hook_target.read_text(encoding="utf-8") != hook_source.read_text(encoding="utf-8"))
    actions = {
        "repo": str(repo),
        "dry_run": dry_run,
        "gitignore": str(gitignore),
        "gitignore_entry": entry,
        "gitignore_changed": needs_entry,
        "handoffs": str(handoffs),
        "handoffs_created": needs_handoffs,
        "install_hooks": install_hooks,
        "hook_source": str(hook_source),
        "hook_target": str(hook_target),
        "hook_installable": hook_installable,
        "hook_changed": needs_hook,
    }
    if install_hooks and not hook_installable:
        actions["error"] = f"hook source not found: {hook_source}"
        if as_json:
            print(json.dumps(actions, indent=2, sort_keys=True))
        else:
            print(f"phase-loop init: hook source not found: {hook_source}", file=sys.stderr)
        return 2
    if not dry_run:
        if needs_entry:
            lines = list(existing)
            lines.append(entry)
            gitignore.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        handoffs.mkdir(parents=True, exist_ok=True)
        if needs_hook:
            hook_target.parent.mkdir(parents=True, exist_ok=True)
            hook_target.write_text(hook_source.read_text(encoding="utf-8"), encoding="utf-8")
            hook_target.chmod(0o755)
    if as_json:
        print(json.dumps(actions, indent=2, sort_keys=True))
    else:
        mode = "would update" if dry_run else "updated"
        if not needs_entry and not needs_handoffs and not needs_hook:
            mode = "already initialized"
        print(f"phase-loop init: {mode} {repo}")
        print(f"gitignore_entry: {entry} ({'needed' if needs_entry else 'present'})")
        print(f"handoffs: {handoffs} ({'needed' if needs_handoffs else 'present'})")
        print(f"hooks: {hook_target} ({'needed' if needs_hook else 'not requested' if not install_hooks else 'present'})")
    return 0


def _reconcile_command(*, repo: Path, roadmap: Path, args: argparse.Namespace, as_json: bool) -> int:
    """Synthesize a v28-shape manual_repair event for --phase from current git state.

    Refuses by default if the working tree is dirty (override with --allow-dirty).
    The closeout commit defaults to HEAD; phase ownership/dirty fields are read
    from `git status --short` at invocation time, so the operator's recovery
    commit needs to be in place before calling.
    """
    phase = (args.phase or "").strip().upper()
    if not phase:
        print("phase-loop reconcile: --phase is required", file=sys.stderr)
        return 2

    topology = collect_git_topology(repo)
    if not topology.get("available"):
        print(f"phase-loop reconcile: {topology.get('reason') or 'git topology unavailable'}", file=sys.stderr)
        return 2

    if getattr(args, "to_status", None) == "planned":
        return _reconcile_to_planned_command(
            repo=repo,
            roadmap=roadmap,
            phase=phase,
            args=args,
            topology=topology,
            as_json=as_json,
        )

    recovery_mode = bool(getattr(args, "recovery_mode", False))
    allow_dirty = bool(getattr(args, "allow_dirty", False)) or recovery_mode
    if not topology.get("clean") and not allow_dirty:
        print(
            "phase-loop reconcile: working tree is dirty. Commit or stash recovery work "
            "before reconciling (or pass --allow-dirty to override).",
            file=sys.stderr,
        )
        return 2

    if recovery_mode:
        missing = []
        if not getattr(args, "closeout_commit", None):
            missing.append("--closeout-commit")
        if not getattr(args, "repair_summary", None):
            missing.append("--repair-summary")
        if not getattr(args, "verification_status", None):
            missing.append("--verification-status")
        if missing:
            print(f"phase-loop reconcile: --recovery-mode requires {', '.join(missing)}", file=sys.stderr)
            return 2

    verification_evidence = None
    if getattr(args, "verification_status", None) == "passed":
        verification_log = getattr(args, "verification_log", None)
        if verification_log or _reconcile_verification_log_required(repo, roadmap, phase):
            verification_evidence = _validate_reconcile_verification_log(repo, verification_log)
            if not verification_evidence.get("ok"):
                print(
                    "phase-loop reconcile: verification evidence invalid "
                    f"(code={verification_evidence.get('code')}, artifact={verification_evidence.get('artifact_path')})",
                    file=sys.stderr,
                )
                return 2

    closeout_commit = getattr(args, "closeout_commit", None) or topology.get("head")
    if not isinstance(closeout_commit, str) or not closeout_commit:
        print("phase-loop reconcile: cannot resolve closeout commit SHA", file=sys.stderr)
        return 2

    snapshot_before = reconcile(repo, roadmap)
    if phase not in snapshot_before.phases:
        print(f"phase-loop reconcile: phase {phase!r} not found in roadmap {roadmap}", file=sys.stderr)
        return 2

    manual_repair = {
        "clears_blocker": True,
        "closeout_commit": closeout_commit,
        "closeout_policy": "commit",
        "dirty_paths": [],
        "phase_owned_dirty": False,
        "phase_owned_dirty_paths": [],
        "previous_phase_owned_paths": [],
        "pre_existing_dirty_paths": [],
        "unowned_dirty_paths": [],
        "verification_status": getattr(args, "verification_status", None) or "not_run",
    }
    repair_summary = getattr(args, "repair_summary", None)
    if repair_summary:
        manual_repair["repair_summary"] = repair_summary
    if verification_evidence is not None:
        manual_repair["verification_evidence"] = verification_evidence
    if recovery_mode:
        manual_repair["recovery_mode"] = True

    event = LoopEvent(
        timestamp=utc_now(),
        repo=str(repo),
        roadmap=str(roadmap),
        phase=phase,
        action="manual_repair",
        status="complete",
        model="manual",
        reasoning_effort="manual",
        source="reconcile",
        metadata={"manual_repair": manual_repair},
        git_topology=dict(topology),
        **event_provenance(roadmap, phase),
    )
    append_event(repo, event)

    snapshot = reconcile(repo, roadmap)
    write_state(repo, snapshot)
    write_tui_handoff(repo, roadmap, snapshot, action="reconcile")
    print(render_status(snapshot, as_json=as_json))
    return 0


def _validate_reconcile_verification_log(repo: Path, value: str | None) -> dict[str, object]:
    if not value:
        return {"ok": False, "code": "missing_verification_log", "artifact_path": None}
    raw_path = Path(value)
    artifact_path = raw_path if raw_path.is_absolute() else repo / raw_path
    if artifact_path.name == LOG_NAME:
        artifact_path = artifact_path.parent / ARTIFACT_NAME
    artifact_path = artifact_path.resolve()
    repo_path = repo.resolve()
    phase_loop_runs = (repo_path / ".phase-loop" / "runs").resolve()
    try:
        inside_repo = artifact_path.is_relative_to(repo_path)
        inside_runs = artifact_path.is_relative_to(phase_loop_runs)
    except AttributeError:  # pragma: no cover - py3.8 compatibility for downstream packagers
        inside_repo = str(artifact_path).startswith(str(repo_path) + "/")
        inside_runs = str(artifact_path).startswith(str(phase_loop_runs) + "/")
    if not (inside_repo or inside_runs):
        return {"ok": False, "code": "artifact_outside_repo", "artifact_path": str(artifact_path)}
    validation = validate_verification_artifact(artifact_path).to_json()
    return validation


def _reconcile_verification_log_required(repo: Path, roadmap: Path, phase: str) -> bool:
    if phase.upper() == "RG":
        return True
    plan = find_plan_artifact(repo, phase, roadmap=roadmap)
    if plan is None:
        return False
    try:
        text = plan.read_text(encoding="utf-8")
    except OSError:
        return False
    return "--verification-log" in text or "IF-0-RG-1" in text


def _reconcile_to_planned_command(
    *,
    repo: Path,
    roadmap: Path,
    phase: str,
    args: argparse.Namespace,
    topology: dict,
    as_json: bool,
) -> int:
    reason = (getattr(args, "reason", None) or "").strip()
    if not reason:
        print("phase-loop reconcile: --to-status planned requires --reason", file=sys.stderr)
        return 2
    if bool(getattr(args, "recovery_mode", False)):
        print("phase-loop reconcile: --to-status planned cannot be combined with --recovery-mode", file=sys.stderr)
        return 2
    if not topology.get("clean") and not bool(getattr(args, "allow_dirty", False)):
        print(
            "phase-loop reconcile: working tree is dirty. Commit or stash current work "
            "before blocked-state recovery (or pass --allow-dirty to override).",
            file=sys.stderr,
        )
        return 2

    snapshot_before = reconcile(repo, roadmap)
    if phase not in snapshot_before.phases:
        print(f"phase-loop reconcile: phase {phase!r} not found in roadmap {roadmap}", file=sys.stderr)
        return 2
    prior_status = snapshot_before.phases.get(phase)
    if prior_status != "blocked":
        print(
            f"phase-loop reconcile: phase {phase!r} is currently {prior_status!r}, not 'blocked'. "
            "--to-status planned only recovers blocked phases.",
            file=sys.stderr,
        )
        return 2

    forced = bool(getattr(args, "force", False))
    allowed, refusal = _dirty_blocker_recovery_allowed(snapshot_before)
    if not allowed and not forced:
        print(
            f"phase-loop reconcile: cannot recover sticky blocker {refusal}. "
            "Pass --force if the underlying blocker condition has been resolved; "
            "your --reason will be the audit trail.",
            file=sys.stderr,
        )
        return 2

    target_status = "planned" if find_plan_artifact(repo, phase, roadmap=roadmap) is not None else "unplanned"
    current_dirty_paths = _topology_dirty_paths(topology)
    manual_recovery = {
        "from": "blocked",
        "to": target_status,
        "reason": reason,
        "trigger": "cli",
        "clears_blocker": True,
        "verification_status": "not_run",
        "blocker_class": snapshot_before.blocker_class,
        "current_dirty_paths": current_dirty_paths,
        "phase_owned_dirty_paths": list(snapshot_before.phase_owned_dirty_paths),
        "previous_phase_owned_paths": list(snapshot_before.previous_phase_owned_paths),
        "dirty_paths": list(snapshot_before.dirty_paths),
        "forced_recovery": forced,
    }
    event = LoopEvent(
        timestamp=utc_now(),
        repo=str(repo),
        roadmap=str(roadmap),
        phase=phase,
        action="manual_recovery",
        status=target_status,
        model="manual",
        reasoning_effort="manual",
        source="reconcile",
        metadata={"manual_recovery": manual_recovery},
        git_topology=dict(topology),
        **event_provenance(roadmap, phase),
    )
    append_event(repo, event)

    snapshot = reconcile(repo, roadmap)
    write_state(repo, snapshot)
    write_tui_handoff(repo, roadmap, snapshot, action="reconcile")
    print(render_status(snapshot, as_json=as_json))
    return 0


def _dirty_blocker_recovery_allowed(snapshot: StateSnapshot) -> tuple[bool, str]:
    blocker_class = snapshot.blocker_class or "unknown"
    if snapshot.human_required:
        return False, blocker_class
    sticky_blockers = {
        "missing_secret",
        "account_or_billing_setup",
        "admin_approval",
        "product_decision_missing",
        "destructive_operation",
    }
    if blocker_class in sticky_blockers:
        return False, blocker_class
    explicit_dirty_evidence = bool(
        snapshot.phase_owned_dirty_paths
        or snapshot.previous_phase_owned_paths
        or snapshot.phase_owned_dirty
        or snapshot.dirty_paths
    )
    if blocker_class == "dirty_worktree_conflict" or explicit_dirty_evidence:
        return True, ""
    # Issue #12 (filed post-v30 by sister session): the recovery command
    # excluded blocker_class='unknown' from its allowlist, leaving phases
    # that hit the pre-v29/v30 issue-#11 cascade quarantined. The v29/v30
    # runtime fixes prevent NEW genuine 'unknown' blockers from accumulating,
    # so legacy 'unknown' from pre-fix runs IS recoverable. Treat as
    # dirty-state-derived for recovery purposes; operator attestation via
    # --reason becomes the audit trail.
    if blocker_class == "unknown":
        return True, ""
    return False, blocker_class


def _topology_dirty_paths(topology: dict) -> list[str]:
    status = topology.get("status_short_branch")
    if not isinstance(status, str):
        return []
    paths: list[str] = []
    for line in status.splitlines():
        if not line or line.startswith("##"):
            continue
        path = line[3:].strip() if len(line) > 3 else line.strip()
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1]
        if path:
            paths.append(path)
    return paths


def _reopen_command(*, repo: Path, roadmap: Path, args: argparse.Namespace, as_json: bool) -> int:
    """Reverse a spurious closeout OR recover a blocked phase: append a typed
    phase_reopen event for --phase.

    Two recovery scenarios:
    1. Spurious complete: an executor reported complete + verification_status=passed
       but the IF gates were not actually satisfied.
    2. Recoverable blocked: an executor self-blocked (e.g., missing_secret) and the
       blocker has since been resolved (e.g., AWS SSO refreshed). Without reopen,
       blocked phases stick because `phase-loop run` reuses the prior terminal
       summary instead of re-dispatching.

    In both cases, the phase_reopen event flips the phase back to planned in the
    reducer; the next `phase-loop run` will re-execute it.

    Refuses by default if the working tree is dirty (override with --allow-dirty)
    so the recorded prior_closeout_commit corresponds to a clean state.
    """
    phase = (args.phase or "").strip().upper()
    if not phase:
        print("phase-loop reopen: --phase is required", file=sys.stderr)
        return 2
    reason = (args.reason or "").strip()
    if not reason:
        print("phase-loop reopen: --reason is required", file=sys.stderr)
        return 2

    topology = collect_git_topology(repo)
    if not topology.get("available"):
        print(f"phase-loop reopen: {topology.get('reason') or 'git topology unavailable'}", file=sys.stderr)
        return 2

    if not topology.get("clean") and not bool(getattr(args, "allow_dirty", False)):
        print(
            "phase-loop reopen: working tree is dirty. Commit or stash work "
            "before reopening (or pass --allow-dirty to override).",
            file=sys.stderr,
        )
        return 2

    snapshot_before = reconcile(repo, roadmap)
    if phase not in snapshot_before.phases:
        print(f"phase-loop reopen: phase {phase!r} not found in roadmap {roadmap}", file=sys.stderr)
        return 2
    prior_status = snapshot_before.phases.get(phase)
    reopen_allowed_statuses = ("complete", "blocked")
    if prior_status not in reopen_allowed_statuses:
        print(
            f"phase-loop reopen: phase {phase!r} is currently {prior_status!r}, "
            f"not one of {reopen_allowed_statuses}. Only complete or blocked phases "
            "can be reopened.",
            file=sys.stderr,
        )
        return 2

    head = topology.get("head")
    prior_closeout = snapshot_before.closeout_summary.get("closeout_commit") if isinstance(snapshot_before.closeout_summary, dict) else None

    phase_reopen = {
        "reason": reason,
        "prior_status": prior_status,
        "prior_closeout_commit": prior_closeout,
        "reopen_commit": head,
    }

    event = LoopEvent(
        timestamp=utc_now(),
        repo=str(repo),
        roadmap=str(roadmap),
        phase=phase,
        action="phase_reopen",
        status="planned",
        model="manual",
        reasoning_effort="manual",
        source="reopen",
        metadata={"phase_reopen": phase_reopen},
        git_topology=dict(topology),
        **event_provenance(roadmap, phase),
    )
    append_event(repo, event)

    snapshot = reconcile(repo, roadmap)
    write_state(repo, snapshot)
    write_tui_handoff(repo, roadmap, snapshot, action="reopen")
    print(render_status(snapshot, as_json=as_json))
    return 0


def _evidence_audit_command(*, repo: Path, args: argparse.Namespace, as_json: bool) -> int:
    """Spot-check dirty-tree artifacts for fake-evidence patterns.

    Operator-callable helper that codifies the v20 spot-check protocol:
    detects duplicate-content (sha256 dup across "distinct" files),
    uniform-numeric (epsilon-tight value arrays), and missing-references
    (cited paths that don't exist on disk).
    """
    from .evidence_audit import run_evidence_audit, render_text

    result = run_evidence_audit(
        repo,
        dirty_only=False if getattr(args, "full_tree_loose", False) else getattr(args, "dirty_only", True),
        min_duplicates=getattr(args, "min_duplicates", 3),
        uniform_epsilon=getattr(args, "uniform_epsilon", 1e-6),
        uniform_min_length=getattr(args, "uniform_min_length", 4),
        tier2_enabled=bool(getattr(args, "tier_2", False) or getattr(args, "enable_tier_3", False)),
        loose_uniform_stdev_threshold=getattr(args, "loose_uniform_stdev_threshold", 1e-3),
        boilerplate_token_overlap_threshold=getattr(args, "boilerplate_token_overlap_threshold", 0.80),
        boilerplate_min_group_size=getattr(args, "boilerplate_min_group_size", 3),
        size_distribution_variance_threshold=getattr(args, "size_distribution_variance_threshold", 0.05),
        size_distribution_min_group_size=getattr(args, "size_distribution_min_group_size", 3),
        enable_tier_3=getattr(args, "enable_tier_3", False),
        missing_references_strict=not getattr(args, "full_tree_loose", False),
    )
    if as_json:
        print(json.dumps(result.to_json(), indent=2))
    else:
        print(render_text(result))
    return 0 if result.is_clean() else 5


def _export_schema_command(*, args: argparse.Namespace) -> int:
    """Emit or --check the canonical closeout schema. Repo-independent."""
    from . import schema_export

    check_path = getattr(args, "check", None)
    if check_path:
        diffs = schema_export.check(Path(check_path))
        if diffs:
            print(f"export-schema --check FAILED: {check_path}", file=sys.stderr)
            for diff in diffs:
                print(f"  - {diff}", file=sys.stderr)
            return 1
        print(f"export-schema --check OK: {check_path}")
        return 0

    fmt = getattr(args, "format", "json-schema")
    if fmt == "field-list":
        payload = schema_export.build_field_list()
    else:
        payload = schema_export.build_schema()
    rendered = schema_export.render(payload)

    output = getattr(args, "output", None)
    if output:
        # utf-8 + explicit "\n" so the emitted artifact is byte-stable cross-platform.
        Path(output).write_text(rendered, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(rendered)
    return 0


def _closeout_drift_audit_command(*, args: argparse.Namespace, as_json: bool) -> int:
    from .phase_loop_drift_audit import run_drift_audit

    repo_args = args.repo or ["."]
    if isinstance(repo_args, str):
        repos = [repo_args]
    else:
        repos = repo_args
    result = run_drift_audit(repos, days=getattr(args, "days", 7), scope=getattr(args, "scope", "closeout"))
    if as_json:
        print(json.dumps(result.to_json(), indent=2))
    else:
        print(result.render_text())
    if result.has_setup_errors():
        return 2
    return 1 if result.has_drift() else 0


def _direct_invocation_blocker(
    *,
    repo: Path,
    args: argparse.Namespace,
    phase: str,
    output_path: Path,
    blocker_summary: str,
    diagnostic_kind: str,
    as_json: bool,
) -> int:
    roadmap = select_roadmap(repo, args.roadmap)
    blocker = {
        "human_required": False,
        "blocker_class": "contract_bug",
        "blocker_summary": blocker_summary,
        "required_human_inputs": (),
        "access_attempts": (),
    }
    snapshot = StateSnapshot(
        timestamp=utc_now(),
        repo=str(repo),
        roadmap=str(roadmap),
        phases={phase: "blocked"},
        current_phase=phase,
        last_action="execute",
        human_required=False,
        blocker_class="contract_bug",
        blocker_summary=blocker_summary,
        **snapshot_provenance(roadmap),
    )
    write_state(repo, snapshot)
    plan = find_plan_artifact(repo, phase, roadmap=roadmap)
    closeout = build_phase_loop_closeout(
        phase_alias=phase,
        plan_path=plan or "",
        plan_metadata=PipelinePlanMetadata(
            source_bundle=str(args.bundle or args.source_bundle or "none"),
            source_bundle_sha256="0" * 64,
            pipeline_phase_id=phase,
            pipeline_mode=args.pipeline_mode or "standalone",
        ),
        terminal_summary={
            "terminal_status": "blocked",
            "terminal_blocker": blocker,
            "verification_status": "blocked",
        },
        automation={
            "status": "blocked",
            "next_skill": "none",
            "next_command": f"none - {diagnostic_kind}",
            "next_model_hint": "none",
            "human_required": False,
            "blocker_class": "contract_bug",
            "blocker_summary": blocker_summary,
            "required_human_inputs": (),
            "verification_status": "blocked",
            "artifact": str(plan) if plan else "none",
            "artifact_state": "tracked" if plan else "none",
        },
        blocker=blocker,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(closeout, indent=2, sort_keys=True), encoding="utf-8")
    print(render_status(snapshot, as_json=as_json))
    return 1


def render_handoff_json(path: Path, repo: Path) -> str:
    import json

    return json.dumps(handoff_metadata(repo, path), indent=2, sort_keys=True)


def monitor_loop(
    repo: Path,
    roadmap: Path,
    *,
    poll_seconds: int,
    timeout_seconds: int | None,
    notify_command: str | None,
    notify_on: tuple[str, ...],
    once: bool,
    as_json: bool,
) -> dict[str, object]:
    started = time.monotonic()
    last_event_kind: str | None = None
    default_notify = ("blocked", "stale", "complete", "awaiting_phase_closeout", "operator_halt")
    watched = set(notify_on or default_notify)
    notifications: list[dict[str, object]] = []
    while True:
        summary = inspect_state(repo, roadmap)
        monitor_status = summary.get("monitor_status") if isinstance(summary.get("monitor_status"), dict) else {}
        event_kind = str(monitor_status.get("event_kind") or "heartbeat")
        if notify_command and event_kind in watched and event_kind != last_event_kind:
            payload = build_notification_payload(repo=repo, roadmap=roadmap, monitor_status=monitor_status, state_summary=summary)
            result = run_notification_command(notify_command, payload)
            if result:
                notifications.append(result)
        last_event_kind = event_kind
        terminal = event_kind in {"blocked", "stale", "complete", "operator_halt", "awaiting_phase_closeout"}
        timed_out = timeout_seconds is not None and time.monotonic() - started >= timeout_seconds
        if once or terminal or timed_out:
            if timed_out and event_kind not in {"blocked", "stale", "complete", "operator_halt", "awaiting_phase_closeout"}:
                monitor_status = dict(monitor_status)
                monitor_status["event_kind"] = "timeout"
                monitor_status["recommended_action"] = "Inspect state, heartbeat, and latest run log before deciding whether to resume."
            payload = {
                "repo": str(repo),
                "roadmap": str(roadmap),
                "monitor_status": monitor_status,
                "notifications": notifications,
                "state": summary,
            }
            rendered = json.dumps(payload, indent=2, sort_keys=True) if as_json else _render_monitor_payload(payload)
            return {"returncode": _monitor_returncode(str(monitor_status.get("event_kind")), bool(summary.get("human_required"))), "rendered": rendered}
        time.sleep(max(1, poll_seconds))


def _render_monitor_payload(payload: dict[str, object]) -> str:
    monitor_status = payload.get("monitor_status") if isinstance(payload.get("monitor_status"), dict) else {}
    lines = [
        f"Monitor event: {monitor_status.get('event_kind', 'unknown')}",
        f"Current phase: {monitor_status.get('current_phase') or 'none'}",
        f"Current status: {monitor_status.get('current_status') or 'unknown'}",
    ]
    if monitor_status.get("blocker_class"):
        lines.append(f"Blocker: {monitor_status['blocker_class']}")
    if monitor_status.get("recommended_action"):
        lines.append(f"Recommended action: {monitor_status['recommended_action']}")
    if payload.get("notifications"):
        lines.append(f"Notifications: {len(payload['notifications'])}")
    return "\n".join(lines)


def _monitor_returncode(event_kind: str, human_required: bool) -> int:
    if event_kind == "complete":
        return 0
    if event_kind in {"blocked", "stale", "operator_halt", "timeout"} or human_required:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
