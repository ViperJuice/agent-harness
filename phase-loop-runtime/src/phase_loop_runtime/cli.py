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
from .consiliency_ingest import ingest
from .consiliency_layout import ARCHETYPE_IDS, MODIFIER_IDS
from .consiliency_scaffold import ScaffoldError, scaffold
from .docs_freshness import scan_docs_freshness
from .discovery import AmbiguousRoadmapError, find_plan_artifact, phase_source_bundle_diagnostic, resolve_python_pin, resolve_repo, resolve_suite_command, select_roadmap
from .events import append_event, read_events
from .roadmap_authority import RoadmapAuthorityError, assert_roadmap_authorized
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
from . import repo_validation
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
    # ah#84: EVERY common arg here is ALSO declared on the top-level parser
    # (build_parser), which owns the before-subcommand position. Without
    # `default=argparse.SUPPRESS` the subparser's copy re-defaults the attribute and
    # `_SubParsersAction` copies it back OVER a value the operator placed before the
    # subcommand (e.g. `phase-loop --phase ROOM run` was silently reset to phase=None,
    # so the runner fell through to repairing a blocked phase). SUPPRESS makes the
    # subparser omit the attribute unless supplied AFTER the subcommand, so the
    # top-level value survives in BOTH positions — the same fix already applied to
    # `--closeout-mode` / `--pipeline-mode` / `--lane-scheduler`. Safe (no AttributeError)
    # because the top-level parser always provides the default.
    _S = argparse.SUPPRESS
    if name in {"closeout-drift-audit", "fleet-map"}:
        sub.add_argument("--repo", action="append", help="Repo to audit. Repeat for cross-repo aggregation.", default=_S)
    else:
        sub.add_argument("--repo", default=_S)
    sub.add_argument("--roadmap", default=_S)
    sub.add_argument("--phase", default=_S)
    sub.add_argument(
        "--max-phases",
        type=int,
        help="Maximum dispatched actions by default; combine with --full-phase to count complete phase cycles.",
        default=_S,
    )
    sub.add_argument("--model-profile", choices=tuple(DEFAULT_PROFILES), default=_S)
    sub.add_argument("--model", default=_S)
    sub.add_argument("--effort", default=_S)
    sub.add_argument(
        "--executor",
        choices=EXECUTORS,
        help=(
            "Explicit executor override (Layer 1). When omitted, the default is "
            "resolved by AUTOSEL: run-from harness -> single-available -> codex. "
            "Set EXECDISPATCH_DISABLE_AUTOSEL=1 to force the legacy codex default."
        ),
        default=_S,
    )
    sub.add_argument("--command-name", default=_S)
    sub.add_argument("--command-template", default=_S)
    sub.add_argument("--claude-execution-mode", choices=CLAUDE_EXECUTION_MODES, default=_S)
    sub.add_argument("--allow-executor", action="append", default=_S)
    sub.add_argument("--fallback-executor", action="append", default=_S)
    sub.add_argument("--disable-executor", action="append", default=_S)
    sub.add_argument("--require-capability", action="append", default=_S)
    sub.add_argument("--json", action="store_true", default=_S)
    sub.add_argument("--dry-run", action="store_true", default=_S)
    sub.add_argument("--observe", action="store_true", help="Accepted for compatibility; launch artifacts are written by default.", default=_S)
    sub.add_argument("--no-observe", action="store_true", help="Disable launch log and heartbeat artifacts.", default=_S)
    sub.add_argument("--stream-output", action="store_true", default=_S)
    sub.add_argument("--bypass-approvals", action="store_true", default=_S)
    sub.add_argument("--heartbeat-interval-seconds", type=int, default=_S)
    sub.add_argument("--quiet-warning-seconds", type=int, default=_S)
    sub.add_argument("--quiet-blocker-seconds", type=int, default=_S)
    sub.add_argument("--no-heartbeat", action="store_true", default=_S)
    sub.add_argument("--work-unit-mode", action="store_true", default=_S)
    sub.add_argument("--source-bundle", default=_S)
    sub.add_argument("--pipeline-mode", choices=("standalone", "pipeline_optional", "pipeline_required"), default=_S)
    sub.add_argument(
        "--lane-scheduler",
        choices=LANE_SCHEDULER_MODES,
        dest="lane_scheduler_mode",
        default=_S,
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


def _branchgov_parent_parser() -> argparse.ArgumentParser:
    """Shared parent carrying --allow-branchgov (issue #83). Used by BOTH the
    top-level parser and the run/resume/dry-run subparsers via `parents=[...]`
    with `default=argparse.SUPPRESS`, so the flag works in either position
    (`phase-loop --allow-branchgov run` AND `phase-loop run --allow-branchgov`)
    without a subparser default clobbering a value set before the subcommand."""
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--allow-branchgov",
        action="store_true",
        default=argparse.SUPPRESS,
        help=(
            "Issue #83 opt-in: switch to the convention branch even when it would orphan a "
            "locally-committed roadmap (exports PHASE_LOOP_BRANCHGOV_ENABLE=true). Without it, "
            "the runtime refuses cleanly (branch_sync_conflict) rather than crashing."
        ),
    )
    return parent


def build_parser() -> argparse.ArgumentParser:
    branchgov_parent = _branchgov_parent_parser()
    parser = argparse.ArgumentParser(
        prog="phase-loop",
        description="Neutral phase-loop runner. codex-phase-loop remains a Codex bridge alias.",
        allow_abbrev=False,
        parents=[branchgov_parent],
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
    parser.add_argument(
        "--executor",
        choices=EXECUTORS,
        help=(
            "Explicit executor override (Layer 1). When omitted, the default is "
            "resolved by AUTOSEL: run-from harness -> single-available -> codex. "
            "Set EXECDISPATCH_DISABLE_AUTOSEL=1 to force the legacy codex default."
        ),
    )
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
    # PUSHFLOW: SUPPRESS (not default="manual") so an unset closeout mode leaves NO
    # attribute — `_resolve_run_closeout_mode` then applies the command-aware default
    # (push for the outer run loop, manual elsewhere). A value passed in THIS
    # before-subcommand position survives the subcommand parse (the run/resume/dry-run
    # subparser also uses SUPPRESS), so an explicit `--closeout-mode` wins in BOTH
    # option positions (was clobbered to the subparser default before).
    parser.add_argument("--closeout-mode", choices=CLOSEOUT_MODES, default=argparse.SUPPRESS)
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
    for name in ("run", "resume", "status", "dry-run", "maintain-skills", "install", "state", "handoff", "archive-state", "monitor", "version", "execute", "reconcile", "reopen", "migrate-handoffs", "migrate-events", "init", "evidence-audit", "closeout-drift-audit", "validate-roadmap", "docs-audit", "export-schema", "fleet-map", "worktree-index", "consiliency-scaffold", "consiliency-ingest", "consiliency-lease"):
        # #83: run/resume/dry-run inherit --allow-branchgov via the shared parent so
        # the flag works after the subcommand too (the top-level parser owns the
        # before-subcommand position); SUPPRESS keeps neither default clobbering.
        sub_parents = [branchgov_parent] if name in {"run", "resume", "dry-run"} else []
        sub = subparsers.add_parser(name, parents=sub_parents)
        if name == "execute":
            sub.add_argument("phase_arg", metavar="phase", help="The phase alias to execute.")
            sub.add_argument("--bundle", help="Path to a phase-source-bundle.v1 artifact.")
            sub.add_argument("--output", help="Path where exactly one closeout JSON file must be written.")
            sub.add_argument("--mode", help="The execution mode: execute, repair, or review.")
        _add_common_subparser_args(sub, name=name)
        if name in {"run", "resume", "dry-run"}:
            # PUSHFLOW: closeout pushes by DEFAULT for run/resume/dry-run (the outer
            # orchestration loop). An explicit --closeout-mode always wins; when none
            # is given the default is `push` (was `manual`), so phase-owned work lands
            # on origin instead of accumulating locally. --no-push restores the prior
            # `manual` default for operators who want to withhold the push. The push
            # itself degrades gracefully with no push remote (recorded as push_refused
            # by the runner, never an error). See _resolve_run_closeout_mode.
            # SUPPRESS so an unset value does NOT overwrite a before-subcommand
            # `--closeout-mode` with None, and so the absent case falls through to the
            # command-aware push default in _resolve_run_closeout_mode.
            sub.add_argument("--closeout-mode", choices=CLOSEOUT_MODES, default=argparse.SUPPRESS)
            sub.add_argument(
                "--no-push",
                action="store_true",
                help="Suppress the push-by-default closeout: fall back to manual "
                "closeout (the prior default) instead of pushing phase-owned work to "
                "origin. Ignored when --closeout-mode is given explicitly.",
            )
            sub.add_argument(
                "--governed",
                action="store_true",
                help="Opt into governed mode: a bounded pre-merge panel review before each "
                "implementation phase commits (model-routing-v2). Default is autonomous (no panel).",
            )
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
            sub.description = "Mechanically lint a phase-plan roadmap spec (headings, aliases, IF-gates, DAG, lane hints).  Pass --train for cross-repo release-train roadmaps."
            sub.add_argument("roadmap_path", nargs="?", help="Path to the roadmap spec. Falls back to --roadmap / auto-detection.")
            sub.add_argument("--train", action="store_true", default=False, help="Validate as a cross-repo release-train roadmap (P2 train mode).")
        if name == "docs-audit":
            sub.description = "Pipeline-independent docs-freshness backstop over a git diff (no .phase-loop state); fails loud on a release surface changed without its required doc."
            sub.add_argument("--base", help="Diff base ref (auto-resolved from CI env if omitted: PR base / prior tag / push before-SHA).")
            sub.add_argument("--decisions", help="Path to the repo-visible doc-decisions artifact (default: .doc-decisions.json).")
        if name == "worktree-index":
            sub.description = (
                "Read-only, git-derived freshness pointer: which active worktree (or origin/main) "
                "holds the freshest copy of a path, and whether origin/main is behind on it. "
                "Never writes repo state."
            )
            sub.add_argument("--path", help="Report freshness holders for a single repo-relative path. Omit to report every path touched by an active worktree.")
            sub.add_argument("--base", help="Diff base ref (default: origin/<default-branch>, falling back to origin/main).")
            sub.add_argument(
                "--fail-on-ahead",
                action="store_true",
                help="PUSHFLOW opt-in soft-block: exit non-zero when any worktree is more than "
                "AHEAD_WARN_THRESHOLD commits ahead of the base ref (unpushed local divergence). "
                "Default is WARN-only; never human_required.",
            )
        if name == "consiliency-lease":
            sub.description = (
                "CS-0.10c: local-file LeaseStore -- soft, TTL+heartbeat path-set leases so parallel "
                "local agents don't collide. SOFT MODE ONLY (hard degrades to soft: no cross-machine "
                "atomic acquire locally). Give-way = reroute: a conflicting acquire never blocks, it "
                "returns the blocking lease so the caller can go work something else. The current-lease "
                "view is a projection of the append-only .consiliency/leases/events.jsonl log only."
            )
            sub.add_argument("action", choices=("acquire", "renew", "release", "query"), help="The LeaseStore operation to perform.")
            sub.add_argument("--lease-id", help="Required for acquire/renew/release.")
            sub.add_argument("--holder", help="Required for acquire/renew/release.")
            sub.add_argument("--ttl-seconds", type=int, default=300, help="acquire only. Default 300.")
            sub.add_argument("--mode", choices=("soft", "hard"), default="soft", help="acquire only. Always degrades to soft on this backend.")
            sub.add_argument(
                "--granularity", choices=("repo", "path-set", "symbol"), default="path-set", help="acquire/query scope granularity."
            )
            sub.add_argument("--scope", action="append", default=[], help="acquire/query. Repeatable path-set/symbol selector entry.")
            sub.add_argument("--lease-phase", default="", help="acquire only. The declaring phase/step label. (--phase is the roadmap-phase common arg.)")
            sub.add_argument("--path", help="query only. Shorthand for --granularity path-set --scope <path>.")
            sub.add_argument("--now", help="Override the current time (ISO 8601 UTC, e.g. 2026-01-01T00:00:00Z) for deterministic testing.")
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
        if name == "consiliency-scaffold":
            sub.description = (
                "CS-0.5: first-writer scaffolder for a schema-valid `.consiliency/` layout "
                "(manifest, contract-version status, interface declaration, and L0 presence-stub "
                "docs for the declared archetype). Additive only: never touches `.phase-loop/` or "
                "`.pipeline/`, and never overwrites a file that already exists."
            )
            sub.add_argument(
                "--archetype",
                action="append",
                default=[],
                choices=ARCHETYPE_IDS,
                help="Repeatable. Declares an archetype (product/service/library/infra/tooling-meta/experiment/document).",
            )
            sub.add_argument(
                "--modifier",
                action="append",
                default=[],
                choices=MODIFIER_IDS,
                help="Repeatable. Declares a modifier (data-bearing/public/regulated/user-facing).",
            )
            sub.add_argument(
                "--baseline-only",
                action="store_true",
                help="Declare baseline-only mode (no archetype). Mutually exclusive with --archetype/--modifier.",
            )
            sub.add_argument("--repo-id", help="Override the manifest's repo.id (default: derived from the repo directory name).")
            sub.add_argument("--display-name", help="Override the manifest's repo.display_name (default: the repo directory name).")
        if name == "consiliency-ingest":
            sub.description = (
                "CS-0.11: brownfield ingestion for an existing repo. Shape-to-conform on the "
                "first pass (delegates the base `.consiliency/` layout to consiliency-scaffold, "
                "then adds a CS-0.12 adoption profile and a proposed governed-set allowlist) -- "
                "verify-only on every subsequent pass (never rewrites; runs the CS-0.6 L0 gates "
                "and labels declared documents governed/foreign/present-nonconforming). "
                "A repo with no `.consiliency/manifest` is untouched unless --adopt is passed."
            )
            sub.add_argument(
                "--adopt",
                action="store_true",
                help="Consent to shape an unmanaged repo (no-op without this flag when no manifest exists yet).",
            )
            sub.add_argument(
                "--check-only",
                action="store_true",
                help=(
                    "Run the conformance check only; never shape (ignores --adopt). "
                    "On an adopted repo this is the verify pass. On an UN-ADOPTED repo "
                    "it emits an explicit 'not adopted / nothing to verify' signal and "
                    "exits non-zero (3), so a no-op is never mistaken for a pass."
                ),
            )
            sub.add_argument(
                "--archetype",
                action="append",
                default=[],
                choices=ARCHETYPE_IDS,
                help="Repeatable. Declares an archetype for the shape pass (ignored on a verify pass).",
            )
            sub.add_argument(
                "--modifier",
                action="append",
                default=[],
                choices=MODIFIER_IDS,
                help="Repeatable. Declares a modifier for the shape pass (ignored on a verify pass).",
            )
            sub.add_argument(
                "--baseline-only",
                action="store_true",
                help="Declare baseline-only mode for the shape pass. Mutually exclusive with --archetype/--modifier.",
            )
            sub.add_argument("--repo-id", help="Override the manifest's repo.id (shape pass only).")
            sub.add_argument("--display-name", help="Override the manifest's repo.display_name (shape pass only).")
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
        if name == "fleet-map":
            sub.description = (
                "CS-0.7: extract the realized cross-repo interface graph (git+ref pins, "
                "copied-literal contract/schema drift, hard-coded host-path refs) across "
                "--repo paths — NOT a package-lockfile scan. Also reports the lockfile-only "
                "baseline (typically empty) alongside the realized edges for comparison."
            )
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
    # run-train: cross-repo release-train coordinator (P3, #29).
    # Registered outside the common-args loop because it has its own argument
    # set (--train, --governed, --workspace-root, --ledger-dir) and does NOT
    # use the per-repo --repo/--roadmap/--phase/--max-phases args.
    run_train_sub = subparsers.add_parser(
        "run-train",
        help=(
            "Run a cross-repo release train: topo-sort, preflight, per-node "
            "draft-PR execution via the unchanged per-repo run_loop. Without "
            "--governed, opens all draft PRs and stops. With --governed, holds "
            "for a train-level review then merges sequentially, re-verifying "
            "each downstream against the upstream merged SHA."
        ),
    )
    run_train_sub.add_argument(
        "--train",
        dest="train_file",
        required=False,
        metavar="FILE",
        help="Path to the cross-repo release-train roadmap (train-roadmap format).",
    )
    run_train_sub.add_argument(
        "--governed",
        action="store_true",
        default=False,
        help=(
            "Pass run_mode='governed' to each per-repo run_loop "
            "(per-repo governed panel review before merge)."
        ),
    )
    run_train_sub.add_argument(
        "--workspace-root",
        default=".",
        metavar="DIR",
        help=(
            "Root directory under which each node's repo is found. "
            "A node with repo='my-service' resolves to <workspace-root>/my-service. "
            "Default: current directory."
        ),
    )
    run_train_sub.add_argument(
        "--workspace",
        dest="workspace_overrides",
        action="append",
        default=None,
        metavar="repo=PATH",
        help=(
            "Per-node workspace override: map a node's repo to an arbitrary "
            "absolute checkout path (e.g. a different volume). Repeatable: "
            "--workspace my-service=/mnt/vol/my-service. Takes precedence over "
            "a node's **Workspace:** attribute and the --workspace-root default."
        ),
    )
    run_train_sub.add_argument(
        "--ledger-dir",
        default=None,
        metavar="DIR",
        help=(
            "Directory for the coordinator-side train ledger. "
            "Must not be inside any repo's .phase-loop/. "
            "Default: <train-file-parent>/.train-ledger/."
        ),
    )
    run_train_sub.add_argument(
        "--json",
        action="store_true",
        help="Emit the result as JSON.",
        default=argparse.SUPPRESS,  # ah#84: survive the before-subcommand position
    )
    # repo-validate: the harness-neutral local-first validation contract resolver
    # (docs/repo-validation-contract.md). Registered outside the common-args loop
    # because it takes a single positional target and reuses only the top-level
    # --repo/--json; it is a NEUTRAL capability (not a dotfiles-domain command),
    # so it belongs in the base CLI, not the profile plugin.
    repo_validate_sub = subparsers.add_parser(
        "repo-validate",
        help=(
            "Resolve and run a repo's EXPLICIT agent validation contract "
            "(just agent::<t> or package.json agent:<t>); fail closed on "
            "unmigrated repos. Never guesses npm test / pytest / make test."
        ),
    )
    repo_validate_sub.add_argument(
        "repo_validate_target",
        metavar="target",
        choices=repo_validation.ALL_TARGET_TOKENS,
        help="One of: fast, gate, full, fix, affected, doctor (check == doctor).",
    )
    # doctor: the front-door adoptability report (IF-0-AHADOPT-2). A NEW top-level
    # command and strict SUPERSET of `repo-validate doctor` — it adds the two
    # install surfaces and the multi-registry BOM, emitting the checked-in
    # `phase-loop-doctor.v1` schema. Own --json/--fail-on-stale/--bom-fixture so
    # they can follow the subcommand (`phase-loop doctor --json`); reuses the
    # top-level --repo.
    doctor_sub = subparsers.add_parser(
        "doctor",
        help=(
            "Adoptability report: which tools/CLIs are installed+authed and what "
            "each unlocks, across BOTH install surfaces, plus a pin-vs-registry "
            "BOM (npm+PyPI) with stale|current|unknown verdicts."
        ),
    )
    doctor_sub.add_argument("--json", action="store_true", help="Emit the phase-loop-doctor.v1 payload as JSON.", default=argparse.SUPPRESS)  # ah#84
    doctor_sub.add_argument(
        "--fail-on-stale",
        action="store_true",
        help="Exit non-zero if any GATING (repo-owned) BOM target is stale.",
    )
    doctor_sub.add_argument(
        "--bom-fixture",
        metavar="PATH",
        help="Load the BOM from a fixture JSON instead of live registries (CI/test wiring).",
    )
    # train-status: non-mutating inspection of the cross-repo train ledger (#45).
    # Reads the SAME default ledger path as run-train; opens no PRs, writes nothing.
    train_status_sub = subparsers.add_parser(
        "train-status",
        help=(
            "Inspect the cross-repo release-train ledger WITHOUT modifying state: "
            "per-node status, branch, PR URL, merge order, and merged SHA."
        ),
    )
    train_status_sub.add_argument(
        "--train",
        dest="train_file",
        required=False,
        metavar="FILE",
        help="Path to the cross-repo release-train roadmap (same file as run-train).",
    )
    train_status_sub.add_argument(
        "--event-log",
        metavar="PATH",
        help="Read a coordinator-owned convergence event log instead of a legacy train ledger.",
    )
    train_status_sub.add_argument(
        "--ledger-dir",
        default=None,
        metavar="DIR",
        help=(
            "Directory of the coordinator-side train ledger. "
            "Default: <train-file-parent>/.train-ledger/ (same as run-train)."
        ),
    )
    train_status_sub.add_argument(
        "--json",
        action="store_true",
        help="Emit the ledger state as JSON.",
        default=argparse.SUPPRESS,  # ah#84: survive the before-subcommand position
    )
    outside_agent_sub = subparsers.add_parser(
        "outside-agent-preflight",
        help="Emit advisory metadata-only outside-agent preflight evidence.",
    )
    outside_agent_sub.add_argument("submission_file", metavar="submission-file")
    outside_agent_sub.add_argument("--output", help="Path to write advisory evidence JSON.")
    outside_agent_validate_sub = subparsers.add_parser(
        "outside-agent-validate",
        help="Emit governed-pipeline outside-agent validation verdict JSON.",
    )
    outside_agent_validate_sub.add_argument("submission_file", metavar="submission-file")
    outside_agent_validate_sub.add_argument(
        "--output",
        required=True,
        help="Path to write governed-pipeline validation verdict JSON.",
    )
    outside_agent_validate_sub.add_argument(
        "--submitted-ref",
        action="append",
        default=[],
        help="Repo-relative ref submitted to governed-pipeline; may be repeated.",
    )
    # advisor-board (LEGACY / CLEANSHIP P7): the RUNNABLE agent-facing default for
    # the 4-vendor board. Composes availability-aware via compose_review_board
    # (REVIEWGOV IF-0-REVIEWGOV-1: is_available ∧ auth_ok) and dispatches via
    # invoke_board — the legacy invoke_panel is untouched. Registered outside the
    # common-args loop: it takes a single positional artifact and its own --json.
    advisor_board_sub = subparsers.add_parser(
        "advisor-board",
        help=(
            "Run an availability-aware cross-vendor advisor board over an artifact "
            "(composes via compose_review_board; dispatches via invoke_board)."
        ),
    )
    advisor_board_sub.add_argument(
        "artifact", metavar="artifact",
        help="Path to the review material staged into the board bundle.",
    )
    advisor_board_sub.add_argument("--json", action="store_true", help="Emit the board verdicts as JSON.", default=argparse.SUPPRESS)  # ah#84
    for name in ("task-message-probe", "task-message-resolve"):
        task_message_sub = subparsers.add_parser(
            name,
            help="Probe or resolve an exact authenticated Codex task-message source.",
        )
        task_message_transport = task_message_sub.add_mutually_exclusive_group(required=True)
        task_message_transport.add_argument("--endpoint", help="Authenticated Codex app-server ws:// or wss:// endpoint.")
        task_message_transport.add_argument("--broker-url", help="Authenticated task-message broker HTTPS endpoint.")
        task_message_transport.add_argument(
            "--control-socket",
            help="Absolute local managed app-server control socket; use only on the source host over an independently authenticated channel.",
        )
        task_message_sub.add_argument("--authority", required=True, help="Pinned source authority identity.")
        task_message_sub.add_argument(
            "--token-env",
            required=False,
            help="Environment variable containing the app-server bearer token; the token is never printed.",
        )
        task_message_sub.add_argument("--timeout-seconds", type=float, default=10.0)
        task_message_sub.add_argument("--heartbeat-timeout-seconds", type=float, default=15.0)
        if name == "task-message-resolve":
            task_message_sub.add_argument("--thread-id", required=True)
            task_message_sub.add_argument("--message-id", required=True)
            task_message_sub.add_argument("--max-source-age-seconds", type=int, default=900)
    task_message_broker_sub = subparsers.add_parser(
        "task-message-broker-serve",
        help="Serve the loopback-only authenticated task-message broker.",
    )
    task_message_broker_sub.add_argument("--host", default="127.0.0.1")
    task_message_broker_sub.add_argument("--port", type=int, default=18765)
    task_message_broker_sub.add_argument("--control-socket", required=True)
    task_message_broker_sub.add_argument("--authority", required=True)
    task_message_broker_sub.add_argument("--token-sha256", required=True)
    task_message_broker_sub.add_argument("--agent-harness-sha", required=True)
    task_message_broker_sub.add_argument("--heartbeat-seconds", type=float, default=5.0)
    task_message_broker_sub.add_argument("--timeout-seconds", type=float, default=10.0)
    # DECOUPLE SL-1: dotfiles-domain commands are added here, only when a profile
    # plugin is installed/opted-in. A clean wheel registers none.
    _register_profile_commands(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or ("dry-run" if args.dry_run else "run")
    try:
        # Issue #83: --allow-branchgov opts into the convention-branch switch even when
        # it would orphan a locally-committed roadmap, by exporting the explicit
        # override the runtime preflight reads (flag.branchgov_override_explicit). Scope
        # the env mutation to this invocation (restore the prior value) so it does not
        # leak process-globally — the override applies for THIS run, not the process.
        if getattr(args, "allow_branchgov", False):
            _previous_branchgov = os.environ.get("PHASE_LOOP_BRANCHGOV_ENABLE")
            os.environ["PHASE_LOOP_BRANCHGOV_ENABLE"] = "true"
            try:
                return _main(parser, args, command)
            finally:
                if _previous_branchgov is None:
                    os.environ.pop("PHASE_LOOP_BRANCHGOV_ENABLE", None)
                else:
                    os.environ["PHASE_LOOP_BRANCHGOV_ENABLE"] = _previous_branchgov
        return _main(parser, args, command)
    except AmbiguousRoadmapError as exc:
        # LEGACY (CLEANSHIP P7) safety net: ANY command that auto-selects a roadmap and
        # finds >1 candidate with nothing to disambiguate degrades to a RECOVERABLE,
        # actionable error (exit 2) — never an uncaught traceback. This is load-bearing
        # with the completed-skip: once a frozen/all-completed manifest stops resolving,
        # commands like `execute`/`validate-roadmap`/`fleet-map`/`reconcile` (which call
        # select_roadmap outside the run-path handler) would otherwise crash. Commands
        # with a RICHER blocker snapshot (run/resume/dry-run/status/state/monitor/handoff)
        # catch it earlier in `_main` and still emit their snapshot; this covers the rest.
        candidates = ", ".join(str(c) for c in exc.candidates)
        print(
            f"phase-loop {command}: ambiguous roadmap selection — pass --roadmap"
            + (f" (candidates: {candidates})" if candidates else ""),
            file=sys.stderr,
        )
        return 2
    except RoadmapAuthorityError as exc:
        print(f"phase-loop {command}: roadmap authority refusal — {exc}", file=sys.stderr)
        return 2


def _resolve_run_closeout_mode(args: argparse.Namespace, command: str) -> str:
    """PUSHFLOW: resolve the effective closeout mode for the outer run loop.

    Precedence (explicit-wins in EITHER option position, then the flipped default):

    1. An explicit ``--closeout-mode`` always wins. Both the top-level and the
       ``run``/``resume``/``dry-run`` subparser use ``argparse.SUPPRESS``, so the
       attribute exists only when the operator passed it (before OR after the
       subcommand); a value given before the subcommand is no longer clobbered.
    2. Otherwise, for the outer orchestration loop (``run``/``resume``/``dry-run``,
       including the implicit no-subcommand forms) the default flips to ``"push"``
       so phase-owned work lands on origin instead of accumulating unpushed — unless
       ``--no-push`` restores ``"manual"``. The runner degrades to ``push_refused``
       when there is no push remote (never an error).
    3. Every other command that reaches ``run_loop`` (``execute``, ``maintain-skills``)
       keeps the prior ``"manual"`` default — the push flip is scoped to the outer
       loop and must never turn an inner ``execute`` leg into a pusher.
    """
    explicit = getattr(args, "closeout_mode", None)
    if explicit:
        return explicit
    if command in {"run", "resume", "dry-run"}:
        return "manual" if getattr(args, "no_push", False) else "push"
    return "manual"


def _main(parser: argparse.ArgumentParser, args: argparse.Namespace, command: str) -> int:
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
    if command == "repo-validate":
        # Neutral local-first validation contract resolver. Resolves the git
        # work-tree root itself (worktree-aware) rather than via resolve_repo, so
        # it honors the same --show-toplevel semantics as the dotfiles wrapper.
        return repo_validation.cli_main(
            target=args.repo_validate_target,
            cwd=args.repo or ".",
            as_json=bool(getattr(args, "json", False)),
        )
    if command == "doctor":
        # DECOUPLE SL-1: lazy import keeps the doctor module (and its reuse of
        # repo_validation / install_status) off the bare `import cli` graph, and
        # the doctor graph itself pulls NO dotfiles-domain module.
        from . import doctor as _doctor

        fixture = getattr(args, "bom_fixture", None)
        return _doctor.run_doctor(
            repo=Path(args.repo or "."),
            as_json=bool(getattr(args, "json", False)),
            fail_on_stale=bool(getattr(args, "fail_on_stale", False)),
            bom_fixture=Path(fixture) if fixture else None,
        )
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
        argv_extra = ["--train"] if getattr(args, "train", False) else []
        return roadmap_lint.main(["validate-roadmap"] + argv_extra + [str(candidate)])
    if command == "run-train":
        return _run_train_command(parser=parser, args=args)
    if command == "train-status":
        return _run_train_status_command(parser=parser, args=args)
    if command == "outside-agent-preflight":
        return _outside_agent_preflight_command(args=args)
    if command == "outside-agent-validate":
        return _outside_agent_validate_command(args=args)
    if command in {"task-message-probe", "task-message-resolve"}:
        return _task_message_command(args=args, resolve=command == "task-message-resolve")
    if command == "task-message-broker-serve":
        return _task_message_broker_serve_command(args=args)
    if command == "advisor-board":
        return _advisor_board_command(args=args)
    if command == "docs-audit":
        from . import docs_audit

        repo_arg = args.repo or "."
        if isinstance(repo_arg, list):
            repo_arg = repo_arg[0] if repo_arg else "."
        repo = resolve_repo(repo_arg)
        report = docs_audit.run_audit(repo, getattr(args, "base", None), getattr(args, "decisions", None))
        if bool(args.json):
            print(json.dumps(report.to_json(), indent=2))
        else:
            print(f"docs_freshness: {report.docs_freshness}")
            for finding in report.findings:
                print(f"  [{finding['klass']}] {finding.get('surface') or '-'}: {finding['reason']}")
            if report.docs_freshness == "blocked":
                print(
                    "\nRemediation: update the required doc surface(s), or record a doc decision in "
                    ".doc-decisions.json (a release-class surface needs a real, relevant doc change)."
                )
        return report.exit_code
    if command == "worktree-index":
        from . import worktree_index

        repo_arg = args.repo or "."
        if isinstance(repo_arg, list):
            repo_arg = repo_arg[0] if repo_arg else "."
        repo = resolve_repo(repo_arg)
        report = worktree_index.build_index(repo, base_ref=getattr(args, "base", None), path=getattr(args, "path", None))
        if bool(args.json):
            print(json.dumps(report.to_json(), indent=2))
        else:
            print(worktree_index.render_human(report))
        # PUSHFLOW: opt-in soft-block on unpushed local divergence. WARN-only by
        # default (the count is already in the report/render); --fail-on-ahead makes
        # it exit non-zero. Never human_required.
        if getattr(args, "fail_on_ahead", False):
            over = worktree_index.worktrees_ahead_over_threshold(report)
            if over:
                names = ", ".join(f"{wt.branch or wt.path} (+{wt.commits_ahead_of_origin})" for wt in over)
                print(
                    f"FAIL: {len(over)} worktree(s) more than {worktree_index.AHEAD_WARN_THRESHOLD} "
                    f"commits ahead of {report.base_ref} (unpushed local divergence): {names}",
                    file=sys.stderr,
                    flush=True,
                )
                return 1
        return 0
    if command == "consiliency-lease":
        repo_arg = args.repo or "."
        if isinstance(repo_arg, list):
            repo_arg = repo_arg[0] if repo_arg else "."
        repo = resolve_repo(repo_arg)
        return _consiliency_lease_command(repo=repo, args=args, as_json=bool(args.json))
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
    if command == "fleet-map":
        return _fleet_map_command(args=args, as_json=as_json)
    repo = resolve_repo(args.repo or ".")
    # DECOUPLE SL-1: profile-plugin commands (adoption-bundle, sync-skills,
    # build-bundle, hotfix) register a `func` default and are dispatched here,
    # so this generic dispatcher never names a dotfiles-domain command.
    profile_func = getattr(args, "func", None)
    if profile_func is not None:
        return profile_func(repo=repo, args=args, as_json=as_json)
    if command == "init":
        return _init_command(repo=repo, dry_run=bool(args.dry_run), as_json=as_json, install_hooks=bool(getattr(args, "install_hooks", False)))
    if command == "consiliency-scaffold":
        return _consiliency_scaffold_command(repo=repo, args=args, as_json=as_json)
    if command == "consiliency-ingest":
        return _consiliency_ingest_command(repo=repo, args=args, as_json=as_json)
    if command == "evidence-audit":
        if args.roadmap:
            _warn_roadmap_validation(select_roadmap(repo, args.roadmap))
        return _evidence_audit_command(repo=repo, args=args, as_json=as_json)
    if command in {"run", "resume", "dry-run"} and bool(getattr(args, "reset_capability", False)):
        assert_roadmap_authorized(repo, args.roadmap)
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
        assert_roadmap_authorized(repo, args.roadmap)
        records = migrate_handoffs(repo, apply=bool(getattr(args, "apply", False)))
        if as_json:
            print(records_to_json(records))
        else:
            for record in records:
                print(f"{record.status}\t{record.action}\t{record.skill_name}\t{record.source}\t{record.target}")
        return 1 if any(record.action == "blocked" for record in records) else 0
    if command == "migrate-events":
        assert_roadmap_authorized(repo, args.roadmap)
        return _migrate_events_command(repo=repo, dry_run=bool(args.dry_run), backup_suffix=args.backup_suffix)
    if command == "archive-state":
        assert_roadmap_authorized(repo, args.roadmap)
        print(render_archive_result(
            archive_state(repo, reason=getattr(args, "reason", None), dry_run=bool(getattr(args, "dry_run", False))),
            as_json=as_json,
        ))
        return 0
    try:
        roadmap = select_roadmap(repo, args.roadmap)
        _warn_roadmap_validation(roadmap)
    except AmbiguousRoadmapError:
        # LEGACY (CLEANSHIP P7): a bare run with >1 specs/phase-plans-v*.md and no
        # state/manifest/handoff to disambiguate is a RECOVERABLE blocker, not an
        # uncaught RuntimeError traceback. agent-harness itself ships v1–v9, so once
        # the frozen-at-v4 manifest stops resolving (completed-skip), a bare run
        # reaches exactly this branch — it must surface an actionable "specify
        # --roadmap" blocker (blocker_class in BLOCKER_CLASSES), never crash.
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
        snapshot = status_snapshot(repo, roadmap, pipeline_mode=args.pipeline_mode or "standalone", read_only=True)
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
        # #62: handoff is a read/render path — reconcile read-only so it cannot
        # dirty plans/manifest.json (matches the status guarantee).
        snapshot = reconcile(repo, roadmap, read_only=True)
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
    from .governed_review import resolve_run_mode

    # model-routing-v2 P1: operator surfacing of run_mode. `--governed` (or
    # PHASE_LOOP_RUN_MODE=governed) opts into governed pre-merge review; default
    # is autonomous (no panel, byte-identical to today).
    run_mode = "governed" if bool(getattr(args, "governed", False)) else resolve_run_mode()

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
        closeout_mode=_resolve_run_closeout_mode(args, command),
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
        run_mode=run_mode,
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


def _native_agent_request_json(leg: object) -> dict | None:
    """Serialize a leg's ``needs_native_agent`` request (ABDNATIVE / #183) to a
    JSON-safe dict, or ``None`` when the seat is not deferred to a native Agent.

    Structural (``getattr`` + ``to_dict``) so it does not force a ``panel_invoker``
    import onto the bare CLI path."""
    request = getattr(leg, "needs_native_agent", None)
    if request is None:
        return None
    to_dict = getattr(request, "to_dict", None)
    return to_dict() if callable(to_dict) else None


def _advisor_board_command(*, args: argparse.Namespace) -> int:
    """LEGACY (CLEANSHIP P7): run the 4-vendor advisor board as the RUNNABLE
    agent-facing default. Composes availability-aware seats via
    ``compose_review_board`` (REVIEWGOV IF-0-REVIEWGOV-1: ``is_available ∧ auth_ok``,
    so an unauthed vendor is dropped and backfilled) and dispatches them through
    ``invoke_board``. The load-bearing legacy ``invoke_panel`` is NOT used here — this
    is the additive board surface. Function-local imports keep the advisor_board /
    panel_invoker graph off the bare ``import cli`` path and let tests patch the
    composition/dispatch seams without shelling out to real vendor CLIs."""
    import tempfile

    from .advisor_board.composition import FLOOR_SEATS, board_independence, compose_review_board
    from .panel_invoker import invoke_board

    artifact_path = Path(args.artifact)
    # Accept ONLY a regular file: a directory passes exists() then tracebacks in the
    # artifact resolver. Fail closed with a recoverable exit, never a traceback.
    if not artifact_path.is_file():
        print(f"advisor-board: artifact not found (not a file): {artifact_path}", file=sys.stderr)
        return 2
    # Auth-aware production composition (REVIEWGOV IF-0-REVIEWGOV-1): the BARE call is
    # already auth-aware — with no args, ``compose_review_board`` defaults
    # ``auth_ok`` to ``default_board_auth_ok``, so a vendor is seated only when it is
    # BOTH on PATH and authenticated (a PATH-present-but-unauthed vendor is dropped at
    # compose and backfilled). Do NOT pass a predicate here: the PATH-only
    # pass-through is a test affordance that activates ONLY when ``is_available`` is
    # injected alone. (Pinned by test: bare compose drops an unauthed vendor + the
    # call takes no kwargs.)
    board = compose_review_board()
    if not board.seats:
        print(
            "advisor-board: no vendor is both available and authenticated — nothing to compose.",
            file=sys.stderr,
        )
        return 2
    # Constrain the spawn cwd (write boundary): the native/claude route otherwise
    # gets Write access to the process CWD. A dedicated scratch dir bounds the blast
    # radius for this standalone entrypoint. The artifact is passed by ABSOLUTE ref so
    # the constrained cwd never hides it.
    try:
        with tempfile.TemporaryDirectory(prefix="advisor-board-") as scratch:
            result = invoke_board(
                board, "", artifact_ref=str(artifact_path.resolve()), repo_dir=scratch
            )
    except (OSError, ValueError) as exc:
        # Artifact staging / resolution failures fail closed with a recoverable exit,
        # not a traceback.
        print(f"advisor-board: could not stage the artifact: {exc}", file=sys.stderr)
        return 2
    independence = board_independence(board)
    usable_count = len(result.usable_legs)
    # A runnable review command must signal when the result is NOT a usable review.
    # Tie the exit code to the board's own contract: it targets 4 independent
    # reviewers with a HARD FLOOR of ``FLOOR_SEATS`` (3). If fewer than the floor of
    # legs returned an OK verdict with text (the rest DEGRADED / ERROR / TIMEOUT /
    # EMPTY / UNAVAILABLE — e.g. the claude leg deferring under Claude Code is one
    # expected non-OK), the board is below its independence floor → exit nonzero.
    usable = usable_count >= FLOOR_SEATS
    exit_code = 0 if usable else 1
    # #183 / ABDNATIVE: LOUD requested-vs-delivered shortfall. A floor-satisfying
    # board can still be SHORT an explicitly-requested seat (the claude/Fable seat
    # deferred to a native Agent), and a bare `usable:true` masks that. Report
    # requested (every composed seat), delivered (usable OK+text), and — the
    # affordance — the seats a native harness can FILL itself (each carries the
    # typed `needs_native_agent` request). Exit stays floor-based (unchanged): the
    # shortfall is a REPORTING signal, not a gate flip.
    # CR F3: derive unfilled seats PER-LEG (`not leg.usable`), never by seat_key
    # membership. Seats are collision-aware LABELS (schema permits byte-identical
    # seats with the SAME key), so a key-set difference would let a duplicate key —
    # one seat OK, its twin failed — hide the failed twin: requested=2/delivered=1
    # but zero unfilled reported = a silent drop. Per-leg is positional and exact.
    requested_seats = len(board.seats)
    unfilled_legs = [leg for leg in result.legs if not leg.usable]
    fillable_legs = [leg for leg in unfilled_legs if leg.needs_native_agent is not None]
    shortfall = {
        "requested_seats": requested_seats,
        "delivered_seats": usable_count,
        "unfilled_seats": [
            {
                "seat_key": leg.seat_key,
                "leg": leg.leg,
                "status": leg.status,
                "needs_native_agent": _native_agent_request_json(leg),
            }
            for leg in unfilled_legs
        ],
        "natively_fillable_seats": len(fillable_legs),
    }
    if bool(getattr(args, "json", False)):
        payload = {
            "board": board.name,
            "usable": usable,
            # Requested-vs-delivered so a Bash-invoking harness sees a dropped seat.
            "requested_seats": requested_seats,
            "delivered_seats": usable_count,
            "shortfall": shortfall,
            "independence": {
                "level": independence.level,
                "distinct_vendors": independence.distinct_vendors,
                "seats": independence.seats,
            },
            # ``text`` is the leg's actual review (findings + AGREE/PARTIALLY
            # AGREE/DISAGREE verdict) — the whole point of running a board — so it
            # MUST be in the payload, not just status/detail metadata.
            "legs": [
                {
                    "seat_key": leg.seat_key,
                    "leg": leg.leg,
                    "status": leg.status,
                    "detail": leg.detail,
                    "text": leg.text,
                    # ABDNATIVE (#183): a deferred claude/Fable seat carries the
                    # typed native-fill request the harness must run; None otherwise.
                    "needs_native_agent": _native_agent_request_json(leg),
                }
                for leg in result.legs
            ],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return exit_code
    print(
        f"advisor-board: {board.name} — independence={independence.level} "
        f"({independence.distinct_vendors} distinct vendors / {independence.seats} seats)"
    )
    for leg in result.legs:
        detail = f" — {leg.detail}" if leg.detail else ""
        print(f"  [{leg.status}] {leg.seat_key}{detail}")
        # Print each reviewer's actual verdict text so the board can be reconciled
        # from the command's output (not just leg statuses).
        text = (leg.text or "").strip()
        if text:
            for line in text.splitlines():
                print(f"      {line}")
    # The statuses/verdicts are ADVISORY evidence — the operator reconciles them; a
    # non-OK leg (DEGRADED/UNAVAILABLE/…) is a gap to fill, not a passed review.
    print("advisor-board: verdicts are advisory — reconcile the legs; check each leg status.")
    # #183: surface the requested-vs-delivered shortfall LOUDLY (stderr) so a
    # floor-satisfying board is never mistaken for the full requested board, and
    # name the seats a native harness can fill itself.
    if unfilled_legs:
        print(
            f"advisor-board: delivered {usable_count}/{requested_seats} requested seats — "
            f"{len(unfilled_legs)} unfilled ({len(fillable_legs)} natively fillable):",
            file=sys.stderr,
        )
        for leg in unfilled_legs:
            fill = " → run a native Fable Agent to fill this seat" if leg.needs_native_agent else ""
            print(f"advisor-board:   [{leg.status}] {leg.seat_key}{fill}", file=sys.stderr)
    if not usable:
        print(
            f"advisor-board: only {usable_count} usable review leg(s) < floor {FLOOR_SEATS} "
            "— below the board's independence floor, not a usable board.",
            file=sys.stderr,
        )
    return exit_code


def _outside_agent_preflight_command(args: argparse.Namespace) -> int:
    from .conformance.outside_agent_advisory import (
        build_malformed_outside_agent_advisory_evidence,
        build_outside_agent_advisory_evidence,
        digest_outside_agent_submission_bytes,
        serialize_outside_agent_advisory_evidence,
    )

    submission_path = Path(args.submission_file)
    try:
        raw = submission_path.read_bytes()
    except OSError as exc:
        evidence = build_malformed_outside_agent_advisory_evidence(
            input_digest=digest_outside_agent_submission_bytes(str(submission_path).encode("utf-8")),
            message=f"outside-agent submission JSON could not be read: {exc.__class__.__name__}",
        )
    else:
        try:
            submission = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            evidence = build_malformed_outside_agent_advisory_evidence(
                input_digest=digest_outside_agent_submission_bytes(raw),
            )
        else:
            evidence = build_outside_agent_advisory_evidence(submission)

    payload = serialize_outside_agent_advisory_evidence(evidence)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    print(text, end="")
    return int(evidence.exit_code)


def _outside_agent_validate_command(args: argparse.Namespace) -> int:
    from .conformance.outside_agent_real import (
        build_malformed_outside_agent_validation_verdict,
        build_outside_agent_validation_verdict,
    )
    from .conformance.outside_agent_real_output import (
        digest_outside_agent_validation_bytes,
        serialize_outside_agent_validation_verdict,
    )

    submission_path = Path(args.submission_file)
    try:
        raw = submission_path.read_bytes()
    except OSError as exc:
        validation = build_malformed_outside_agent_validation_verdict(
            input_digest=digest_outside_agent_validation_bytes(str(submission_path).encode("utf-8")),
            message=f"outside-agent submission JSON could not be read: {exc.__class__.__name__}",
        )
    else:
        try:
            submission = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            validation = build_malformed_outside_agent_validation_verdict(
                input_digest=digest_outside_agent_validation_bytes(raw),
            )
        else:
            validation = build_outside_agent_validation_verdict(
                submission,
                submitted_refs=tuple(args.submitted_ref or ()),
            )

    payload = serialize_outside_agent_validation_verdict(validation)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    Path(args.output).write_text(text, encoding="utf-8")
    print(text, end="")
    return int(validation.exit_code)


def _task_message_command(args: argparse.Namespace, *, resolve: bool) -> int:
    from .task_message_resolver import CodexAppServerTaskMessageResolver, TaskMessageResolverError

    if args.control_socket and args.token_env:
        error = TaskMessageResolverError(
            "attestation_invalid",
            authority=args.authority,
            thread_id=getattr(args, "thread_id", None),
            message_id=getattr(args, "message_id", None),
        )
        print(json.dumps(error.metadata(), indent=2, sort_keys=True))
        return 2
    remote_transport = bool(args.endpoint or args.broker_url)
    token = os.environ.get(args.token_env, "") if remote_transport and args.token_env else ""
    if remote_transport and not token:
        error = TaskMessageResolverError(
            "attestation_invalid",
            authority=args.authority,
            thread_id=getattr(args, "thread_id", None),
            message_id=getattr(args, "message_id", None),
        )
        print(json.dumps(error.metadata(), indent=2, sort_keys=True))
        return 2
    try:
        if args.broker_url:
            from .task_message_broker_client import TaskMessageBrokerClient

            broker = TaskMessageBrokerClient(
                broker_url=args.broker_url,
                bearer_token=token,
                authority=args.authority,
                heartbeat_timeout_seconds=args.heartbeat_timeout_seconds,
            )
            payload = (
                broker.resolve(
                    thread_id=args.thread_id,
                    message_id=args.message_id,
                    max_source_age_seconds=args.max_source_age_seconds,
                )
                if resolve
                else broker.probe()
            )
        else:
            resolver = CodexAppServerTaskMessageResolver(
                endpoint=args.endpoint,
                bearer_token=token or None,
                control_socket=args.control_socket,
                authority=args.authority,
                max_source_age_seconds=getattr(args, "max_source_age_seconds", 900),
                timeout_seconds=args.timeout_seconds,
            )
            payload = (
                resolver.resolve(thread_id=args.thread_id, message_id=args.message_id).payload()
                if resolve
                else resolver.probe()
            )
    except ValueError:
        error = TaskMessageResolverError(
            "attestation_invalid",
            authority=args.authority,
            thread_id=getattr(args, "thread_id", None),
            message_id=getattr(args, "message_id", None),
        )
        print(json.dumps(error.metadata(), indent=2, sort_keys=True))
        return 2
    except TaskMessageResolverError as exc:
        print(json.dumps(exc.metadata(), indent=2, sort_keys=True))
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _task_message_broker_serve_command(args: argparse.Namespace) -> int:
    from .task_message_broker import BrokerConfig, TaskMessageBroker, build_server, verified_installed_agent_harness_sha
    from .task_message_resolver import CodexAppServerTaskMessageResolver

    try:
        agent_harness_sha = verified_installed_agent_harness_sha(args.agent_harness_sha)
        config = BrokerConfig(
            authority=args.authority,
            token_sha256=args.token_sha256,
            agent_harness_sha=agent_harness_sha,
            heartbeat_seconds=args.heartbeat_seconds,
        )

        def resolver_factory(max_source_age_seconds: int) -> CodexAppServerTaskMessageResolver:
            return CodexAppServerTaskMessageResolver(
                control_socket=args.control_socket,
                authority=args.authority,
                max_source_age_seconds=max_source_age_seconds,
                timeout_seconds=args.timeout_seconds,
            )

        server = build_server(args.host, args.port, TaskMessageBroker(config, resolver_factory))
    except ValueError:
        return 2
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 130
    finally:
        server.server_close()
    return 0


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
    if isinstance(blocker, dict) and blocker.get("blocker_class"):
        return 1
    # #14: `--apply` must never mimic `--check` with a silent exit 0. When it
    # could not repair some bridge skills, always print the loud remediation (the
    # per-skill listing is already emitted by render_skill_sync_result above);
    # fail loud (exit 1) only on a genuine TOTAL no-op — nothing repaired — so a
    # partial repair doesn't hard-fail pipelines on a host that uses only some of
    # the default harnesses.
    unrepaired = summary.get("unrepaired") or []
    if bool(args.apply) and isinstance(unrepaired, list) and unrepaired:
        repaired_n = len(summary.get("changed") or [])
        print(
            f"sync-skills --apply: repaired {repaired_n}, could NOT repair {len(unrepaired)} "
            "bridge skill(s) (see the listing above). A normal pinned `pip install` ships the "
            "assembled skill bundle and resolves it automatically. Otherwise re-run "
            "`bootstrap.sh`, or `pip install -e ~/code/agent-harness/phase-loop-runtime`, or "
            "set PHASE_LOOP_SKILL_SOURCE_PLUGINS together with PHASE_LOOP_RUNNER_REPO_ROOT "
            "(the anchor for the built-in provider's relative roots).",
            file=sys.stderr,
        )
        if repaired_n == 0:
            return 1
    return 0


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
        # CR round-2 (codex): honor an automation.python pin on the hotfix path
        # too (was execute-only). Auto requires-python resolution already runs
        # inside run_verification regardless of the pin.
        python_pin=resolve_python_pin(roadmap, plan_stub),
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


def _consiliency_scaffold_command(*, repo: Path, args: argparse.Namespace, as_json: bool) -> int:
    archetypes = tuple(dict.fromkeys(getattr(args, "archetype", None) or ()))
    modifiers = tuple(dict.fromkeys(getattr(args, "modifier", None) or ()))
    baseline_only = bool(getattr(args, "baseline_only", False))
    if baseline_only and (archetypes or modifiers):
        print("phase-loop consiliency-scaffold: --baseline-only is mutually exclusive with --archetype/--modifier", file=sys.stderr)
        return 2
    if not baseline_only and not archetypes:
        print("phase-loop consiliency-scaffold: pass --archetype <name> (repeatable) or --baseline-only", file=sys.stderr)
        return 2
    try:
        result = scaffold(
            repo,
            mode="baseline-only" if baseline_only else "archetyped",
            archetypes=archetypes,
            modifiers=modifiers,
            repo_id=getattr(args, "repo_id", None),
            display_name=getattr(args, "display_name", None),
            dry_run=bool(args.dry_run),
        )
    except ScaffoldError as exc:
        print(f"phase-loop consiliency-scaffold: {exc}", file=sys.stderr)
        return 2
    payload = result.to_json()
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        mode = "would scaffold" if result.dry_run else ("already present" if result.already_present else "scaffolded")
        print(f"phase-loop consiliency-scaffold: {mode} {result.manifest_path}")
        for created in result.created_paths:
            print(f"  created: {created}")
        for referenced in result.referenced_paths:
            print(f"  referenced (already existed): {referenced}")
        for missing in result.declared_missing_paths:
            print(f"  declared, not authored (no fake content -- see presence gate): {missing}")
    return 0


def _consiliency_ingest_command(*, repo: Path, args: argparse.Namespace, as_json: bool) -> int:
    archetypes = tuple(dict.fromkeys(getattr(args, "archetype", None) or ()))
    modifiers = tuple(dict.fromkeys(getattr(args, "modifier", None) or ()))
    baseline_only = bool(getattr(args, "baseline_only", False))
    check_only = bool(getattr(args, "check_only", False))
    if baseline_only and (archetypes or modifiers):
        print("phase-loop consiliency-ingest: --baseline-only is mutually exclusive with --archetype/--modifier", file=sys.stderr)
        return 2
    try:
        result = ingest(
            repo,
            adopt=bool(getattr(args, "adopt", False)),
            check_only=check_only,
            mode="baseline-only" if baseline_only else "archetyped",
            archetypes=archetypes,
            modifiers=modifiers,
            repo_id=getattr(args, "repo_id", None),
            display_name=getattr(args, "display_name", None),
            dry_run=bool(args.dry_run),
        )
    except ScaffoldError as exc:
        print(f"phase-loop consiliency-ingest: {exc}", file=sys.stderr)
        return 2
    payload = result.to_json()
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"phase-loop consiliency-ingest: {result.mode} {result.manifest_path}")
        if result.mode == "skipped":
            print("  no .consiliency/manifest and --adopt was not passed; repo left untouched")
        if result.mode == "not-adopted":
            print("  NOT ADOPTED: no .consiliency/manifest -- nothing to verify (a no-op is not a pass)")
        if result.mode == "shape":
            created = (result.scaffold or {}).get("created_paths", [])
            print(f"  scaffolded {len(created)} doc(s); proposed governed_set entries: {len(result.governed_set)}")
        if result.mode == "verify" and result.gate_scan is not None:
            print(f"  gate scan: {result.gate_scan.get('status')}")
            for finding in result.findings:
                print(f"  finding: {finding.get('code')} ({finding.get('path')})")
    # --check-only makes the exit code verdict-significant, so a pre-PR actor is
    # never misled into reading a no-op OR a failing verify as a pass:
    #   3 -> not adopted / nothing to verify (distinct from a pass)
    #   1 -> adopted but the gate scan is BLOCKED (a real conformance failure)
    #   0 -> adopted + verify clean (warn stays 0: soft by default)
    # 2 remains the usage-error code. The plain (non --check-only) path is
    # UNCHANGED -- it keeps returning 0 exactly as before (see report note on
    # the pre-existing blocked->0 gap on the plain command).
    if check_only:
        if result.mode == "not-adopted":
            return 3
        if result.gate_scan is not None and result.gate_scan.get("status") == "blocked":
            return 1
    return 0


def _consiliency_lease_command(*, repo: Path, args: argparse.Namespace, as_json: bool) -> int:
    from .lease_store import LeaseStore

    action = args.action
    store = LeaseStore(repo)
    now = getattr(args, "now", None)

    if action in ("acquire", "renew", "release") and not (args.lease_id and args.holder):
        print(f"phase-loop consiliency-lease {action}: --lease-id and --holder are required", file=sys.stderr)
        return 2

    if action == "acquire":
        scope = {"granularity": args.granularity, "selector": list(args.scope)}
        result = store.acquire(
            lease_id=args.lease_id,
            holder=args.holder,
            ttl_seconds=args.ttl_seconds,
            mode=args.mode,
            scope=scope,
            phase=args.lease_phase,
            now=now,
        )
        payload = result.to_json()
        if as_json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        elif result.granted:
            note = " (degraded to soft)" if result.degraded else ""
            print(f"phase-loop consiliency-lease acquire: granted{note} {args.lease_id} to {args.holder}")
        else:
            conflict = result.conflict or {}
            print(
                f"phase-loop consiliency-lease acquire: conflict -- give_way={result.give_way} "
                f"held by {conflict.get('holder')} (lease {conflict.get('lease_id')})"
            )
        return 0 if result.granted else 1

    if action == "renew":
        result = store.renew(lease_id=args.lease_id, holder=args.holder, now=now)
        payload = result.to_json()
        if as_json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        elif result.renewed:
            print(f"phase-loop consiliency-lease renew: renewed {args.lease_id}")
        else:
            print(f"phase-loop consiliency-lease renew: rejected ({result.reason})")
        return 0 if result.renewed else 1

    if action == "release":
        result = store.release(lease_id=args.lease_id, holder=args.holder, now=now)
        payload = result.to_json()
        if as_json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        elif result.released:
            print(f"phase-loop consiliency-lease release: released {args.lease_id}")
        else:
            print(f"phase-loop consiliency-lease release: rejected ({result.reason})")
        return 0 if result.released else 1

    # action == "query"
    scope = {"granularity": args.granularity, "selector": list(args.scope)} if args.scope else None
    if not (args.lease_id or args.path or scope):
        print("phase-loop consiliency-lease query: pass --lease-id, --path, or --scope", file=sys.stderr)
        return 2
    current = store.query(lease_id=args.lease_id, path=args.path, scope=scope, now=now)
    if as_json:
        print(json.dumps(current, indent=2, sort_keys=True))
    elif current is None:
        print("phase-loop consiliency-lease query: free")
    else:
        print(f"phase-loop consiliency-lease query: held by {current['holder']} (lease {current['lease_id']}, mode {current['mode']})")
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


def _fleet_map_command(*, args: argparse.Namespace, as_json: bool) -> int:
    from .fleet_map import build_fleet_map

    repo_args = args.repo or ["."]
    if isinstance(repo_args, str):
        repos = [repo_args]
    else:
        repos = repo_args
    result = build_fleet_map(repos)
    if as_json:
        print(json.dumps(result.to_json(), indent=2))
    else:
        print(result.render_text())
    # Informational extractor, not a gate: edges are the expected, useful
    # output, so only a setup problem (missing repo path) is an error.
    return 2 if result.has_setup_errors() else 0


def _run_train_command(*, parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """Handle the 'run-train' subcommand (#29 P3+P4).

    Topo-sorts the release train, preflights ALL repos, then per node (in
    order): injects upstream draft ref → runs the unchanged per-repo
    run_loop → publishes a draft PR → appends to the coordinator ledger.

    After all draft PRs are open:
    - ``autonomous`` (default): stops at ``drafts_open`` terminal.  Cross-repo
      merges are never auto-merged; the operator reviews and re-runs with
      ``--governed``.
    - ``governed`` (``--governed`` flag): runs the train-level governed review
      panel (one round), then merges sequentially in topo order with downstream
      re-verify against each upstream's merged SHA before merging.
    """
    from .train_roadmap import load_train_roadmap
    from .train_ledger import default_ledger_path
    from . import train_runner

    train_file = getattr(args, "train_file", None)
    if not train_file:
        parser.error("run-train requires --train <file>")
        return 1  # unreachable but makes mypy happy

    train_path = Path(train_file)
    if not train_path.exists():
        print(f"run-train: train file not found: {train_path}", file=sys.stderr)
        return 1

    try:
        roadmap = load_train_roadmap(train_path)
    except ValueError as exc:
        print(f"run-train: failed to parse train roadmap: {exc}", file=sys.stderr)
        return 1

    # Validate train schema (T-A/B/C/D) before touching any repo.
    # A malformed train (e.g. a none-channel dependency edge) must open zero
    # PRs.  run_train also validates internally, but running it here gives a
    # cleaner CLI error message.
    from .train_roadmap import validate_train_loud
    try:
        validate_train_loud(roadmap)
    except ValueError as exc:
        print(
            f"run-train: train validation failed — zero PRs will be opened:\n{exc}",
            file=sys.stderr,
        )
        return 1

    # run_mode mirrors how 'run' handles --governed (cli.py:796)
    run_mode = "governed" if bool(getattr(args, "governed", False)) else "autonomous"

    # Workspace resolution precedence (highest first):
    #   1. --workspace <repo>=<path> CLI override (arbitrary absolute paths)
    #   2. the node's **Workspace:** attribute (from the train roadmap)
    #   3. <workspace-root>/<node.repo>  (the default)
    workspace_root = Path(getattr(args, "workspace_root", None) or ".")
    workspace_overrides: dict = {}
    for spec in getattr(args, "workspace_overrides", None) or []:
        if "=" not in spec:
            parser.error(f"--workspace expects 'repo=PATH', got {spec!r}")
        repo_key, _, path_val = spec.partition("=")
        repo_key = repo_key.strip()
        path_val = path_val.strip()
        if not repo_key or not path_val:
            parser.error(f"--workspace expects a non-empty 'repo=PATH', got {spec!r}")
        workspace_overrides[repo_key] = Path(path_val)

    def _resolve_workspace(node) -> Path:
        override = workspace_overrides.get(node.repo)
        if override is not None:
            return override
        node_ws = getattr(node, "workspace", None)
        if node_ws:
            return Path(node_ws)
        return workspace_root / node.repo

    # Ledger path — must not be inside any repo's .phase-loop/
    ledger_dir_arg = getattr(args, "ledger_dir", None)
    if ledger_dir_arg:
        ledger_dir = Path(ledger_dir_arg)
    else:
        ledger_dir = train_path.parent / ".train-ledger"
    ledger_path = default_ledger_path(ledger_dir, train_path.stem)

    as_json = bool(getattr(args, "json", False))

    # Build a broker-authoritative coordinator runtime so publish actually opens PRs.
    # Without a broker_client, publish_from_worktree fail-closes `broker_required` and
    # the train opens ZERO PRs.  The routing broker binds per BrokerRequest.repo (the
    # node's resolved workspace) AND keeps a PER-REPO admission/evidence store, so one
    # node's ambiguous outcome fail-closes only that repo.  The broker root is namespaced
    # by the roadmap's RESOLVED PATH hash (not the bare filename stem): two distinct
    # roadmap files — even same-stemmed, even under one explicit `--ledger-dir` — get
    # distinct broker roots, so an ambiguous outcome in one train can never fail-close a
    # different train.  Keying on the stable path (not the content digest) keeps a
    # resumed train on its own epoch across roadmap edits.  It lives under the ledger dir,
    # which the roadmap author keeps outside any repo's .phase-loop/ (INV-4); it is NOT
    # machine-enforced to be outside every node worktree, so keep train roadmaps + their
    # ledger dir out of the checkouts.
    import hashlib

    from .convergence.broker import build_routing_broker_client

    train_key = hashlib.sha256(str(train_path.resolve()).encode("utf-8")).hexdigest()[:16]
    coordinator_root = ledger_dir / "broker" / train_key
    coordinator_root.mkdir(parents=True, exist_ok=True)
    roadmap_digest = hashlib.sha256(train_path.read_bytes()).hexdigest()
    coordinator_runtime = train_runner.CoordinatorRuntime(
        train_id=train_path.stem,
        coordinator_root=coordinator_root,
        roadmap_path=str(train_path),
        roadmap_digest=roadmap_digest,
        workspace_id=train_path.stem,
        broker_client=build_routing_broker_client(broker_root=coordinator_root),
    )

    result = train_runner.run_train(
        roadmap,
        ledger_path,
        run_mode=run_mode,
        resolve_workspace=_resolve_workspace,
        coordinator_runtime=coordinator_runtime,
        _merge_phase_enabled=True,  # P4 gate: autonomous→drafts_open, governed→merge
    )

    if as_json:
        print(json.dumps(result, indent=2))

    if result["status"] == "preflight_failed":
        print("run-train: preflight failed — zero PRs opened:", file=sys.stderr)
        for err in result.get("errors", []):
            print(f"  {err}", file=sys.stderr)
        return 1

    if result["status"] == "blocked":
        node_id = result.get("node_id", "?")
        detail = result.get("detail", {})
        print(
            f"run-train: blocked at node '{node_id}': {detail.get('reason', detail)}",
            file=sys.stderr,
        )
        print(
            "  Prior nodes' draft PRs remain open. Re-run to resume.",
            file=sys.stderr,
        )
        return 1

    if result["status"] == "drafts_open":
        # Autonomous mode terminal: all draft PRs open, awaiting governed review.
        nodes = result.get("nodes", {})
        if not as_json:
            print(
                f"run-train: {len(nodes)} draft PR(s) open — "
                f"train held for governed review. Re-run with --governed to merge."
            )
            for node_id, info in nodes.items():
                print(f"  {node_id}: {info.get('pr_url', '?')}")
        return 0

    if result["status"] == "review_halted":
        # Panel rejected the train — ZERO merges (partial-merge-disaster guard).
        blocker = result.get("terminal_blocker") or {}
        reason = result.get("reason", "unknown")
        print(
            f"run-train: train-level review rejected — ZERO merges "
            f"(reason: {reason}; human_required: {blocker.get('human_required', False)})",
            file=sys.stderr,
        )
        return 1

    if result["status"] == "merge_halted":
        # Downstream re-verify failed — upstream stays merged (forward-only).
        node_id = result.get("node_id", "?")
        reason = result.get("reason", "unknown")
        print(
            f"run-train: merge halted at node '{node_id}': {reason}",
            file=sys.stderr,
        )
        print(
            "  Upstream nodes remain merged (forward-only). "
            "Use expand/contract upstream contracts to prevent this.",
            file=sys.stderr,
        )
        return 1

    if result["status"] == "merged":
        # All nodes merged successfully in topo order.
        nodes = result.get("nodes", {})
        if not as_json:
            print(f"run-train: merged — {len(nodes)} node(s) landed on main")
            for node_id, info in nodes.items():
                print(f"  {node_id}: {info.get('merged_sha', '?')}")
        return 0

    # status == "completed" (legacy / direct run_train calls with _merge_phase_enabled=False)
    nodes = result.get("nodes", {})
    if not as_json:
        print(f"run-train: completed — {len(nodes)} draft PR(s) open")
        for node_id, info in nodes.items():
            print(f"  {node_id}: {info.get('pr_url', '?')}")
    return 0


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
        docs_freshness=scan_docs_freshness(repo, plan_path=plan),
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


def _run_train_status_command(*, parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """train-status (#45): non-mutating inspection of the cross-repo train ledger.

    Reads the SAME default ledger path as ``run-train`` and prints per-node status
    (status, branch, PR URL, merge order, merged SHA). Never writes — safe to run
    between draft-PR creation, review, and merge/reverify.
    """
    import json as _json

    event_log = getattr(args, "event_log", None)
    if event_log:
        if getattr(args, "train_file", None) or getattr(args, "ledger_dir", None):
            parser.error("train-status --event-log is mutually exclusive with --train and --ledger-dir")
            return 2
        from .convergence import build_train_status, read_convergence_events, recover_train_state, render_train_status
        path = Path(event_log)
        try:
            snapshot = build_train_status(recover_train_state(read_convergence_events(path)), path)
        except Exception as exc:
            print(f"train-status: failed to read event log at {path}: {exc}", file=sys.stderr)
            return 1
        print(render_train_status(snapshot, as_json=bool(getattr(args, "json", False))))
        return 0

    from .train_ledger import default_ledger_path, read_ledger

    train_file = getattr(args, "train_file", None)
    if not train_file:
        parser.error("train-status requires --train <file>")
        return 2
    train_path = Path(train_file)
    if not train_path.exists():
        print(f"train-status: train file not found: {train_path}", file=sys.stderr)
        return 1

    ledger_dir_arg = getattr(args, "ledger_dir", None)
    ledger_dir = Path(ledger_dir_arg) if ledger_dir_arg else train_path.parent / ".train-ledger"
    ledger_path = default_ledger_path(ledger_dir, train_path.stem)

    try:
        state = read_ledger(ledger_path)
    except Exception as exc:
        print(f"train-status: failed to read ledger at {ledger_path}: {exc}", file=sys.stderr)
        return 1

    # Prefer the train roadmap's topo order (also surfaces not-yet-run nodes as
    # 'pending'); fall back to ledger order if the train can't be parsed.
    node_order: list[str] = []
    try:
        from .train_roadmap import parse_train_roadmap

        roadmap = parse_train_roadmap(train_path.read_text(encoding="utf-8"))
        node_order = [node.node_id for node in roadmap.topo_order()]
    except Exception:
        node_order = sorted(
            state.keys(),
            key=lambda nid: (
                state[nid].merge_order if state[nid].merge_order is not None else 1 << 30,
                nid,
            ),
        )
    for nid in state:  # defensive: any ledger node absent from the roadmap
        if nid not in node_order:
            node_order.append(nid)

    rows = []
    for nid in node_order:
        rec = state.get(nid)
        rows.append(
            {
                "node_id": nid,
                "status": rec.status if rec else "pending",
                "branch": rec.branch if rec else None,
                "pr_url": rec.pr_url if rec else None,
                "merge_order": rec.merge_order if rec else None,
                "merged_sha": rec.upstream_merge_sha if rec else None,
            }
        )

    if bool(getattr(args, "json", False)):
        print(_json.dumps({"ledger_path": str(ledger_path), "nodes": rows}, indent=2))
        return 0

    print(f"train-status: {ledger_path}")
    if not rows:
        print("  (no ledger records yet — run-train has not run, or the ledger path differs)")
        return 0
    for row in rows:
        line = f"  [{row['status']}] {row['node_id']}"
        if row["merge_order"] is not None:
            line += f" (order {row['merge_order']})"
        print(line)
        if row["pr_url"]:
            print(f"        PR: {row['pr_url']}  branch={row['branch']}")
        if row["merged_sha"]:
            print(f"        merged: {row['merged_sha']}")
    return 0


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
