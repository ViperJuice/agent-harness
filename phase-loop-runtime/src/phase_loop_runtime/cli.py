from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .closeout import build_phase_loop_closeout
from .discovery import find_plan_artifact, phase_source_bundle_diagnostic, resolve_repo, select_roadmap
from .events import append_event, read_events
from .git_topology import collect_git_topology
from .handoff import handoff_metadata, write_tui_handoff
from .install_status import build_install_status
from .models import CLAUDE_EXECUTION_MODES, CLOSEOUT_MODES, EXECUTORS, LANE_SCHEDULER_MODES, LoopEvent, PipelinePlanMetadata, StateSnapshot, utc_now
from .maintenance import MaintenanceOptions, SyncSkillsOptions, sync_bridge_skills
from .migrate_handoffs import migrate_handoffs, records_to_json
from .observability import build_notification_payload, run_notification_command
from .profiles import DEFAULT_PROFILES
from .provenance import event_provenance, snapshot_provenance
from .reconcile import reconcile
from .render import render_archive_result, render_skill_sync_result, render_state_inspection, render_status
from .runner import run_loop, status_snapshot
from .runtime_projection import build_runtime_projection
from .skill_install import actions_to_json, install_skills
from .state import write_state
from .state_degradation import clear as clear_degradation
from .state_ops import archive_state, inspect_state
from . import __version__


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
    parser.add_argument("--max-phases", type=int, default=1)
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
    subparsers = parser.add_subparsers(dest="command")
    for name in ("run", "resume", "status", "dry-run", "maintain-skills", "sync-skills", "install", "state", "handoff", "archive-state", "monitor", "version", "execute", "reconcile", "reopen", "migrate-handoffs", "init", "evidence-audit", "closeout-drift-audit"):
        sub = subparsers.add_parser(name)
        if name == "execute":
            sub.add_argument("phase_arg", metavar="phase", help="The phase alias to execute.")
            sub.add_argument("--bundle", help="Path to a phase-source-bundle.v1 artifact.")
            sub.add_argument("--output", help="Path where exactly one closeout JSON file must be written.")
            sub.add_argument("--mode", help="The execution mode: execute, repair, or review.")
        if name == "closeout-drift-audit":
            sub.add_argument("--repo", action="append", help="Repo to audit. Repeat for cross-repo aggregation.")
        else:
            sub.add_argument("--repo")
        sub.add_argument("--roadmap")
        sub.add_argument("--phase")
        sub.add_argument("--max-phases", type=int)
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
        if name in {"run", "resume", "dry-run"}:
            sub.add_argument("--closeout-mode", choices=CLOSEOUT_MODES)
            sub.add_argument("--force-replan", action="store_true")
            sub.add_argument("--reset-capability", action="store_true")
            sub.add_argument("--rotate-executors")
            sub.add_argument("--rotation-mode", choices=("phase", "work_unit"))
            sub.add_argument("--rotation-on-policy-pin", choices=("skip", "fallback-next"))
            sub.add_argument("--enable-tier-3", action="store_true", help="Enable default-off closeout-time Tier 3 evidence audit.")
            sub.add_argument("--tier-3-budget", type=int, default=3, help="Maximum Tier 3 evidence-audit calls per closeout. Default 3.")
        if name == "maintain-skills":
            sub.description = "Skill Maintenance: planner-only by default; edits require --apply-skill-edits and --allow-skill."
            sub.add_argument("--min-reflections", type=int, default=2)
            sub.add_argument("--apply-skill-edits", action="store_true")
            sub.add_argument("--allow-skill", action="append", default=())
            sub.add_argument("--improvement-plan")
        if name == "sync-skills":
            sub.description = "Audit or repair harness-local phase-loop bridge skills for manual reentry."
            sub.add_argument("--harness", action="append", default=[], choices=("codex", "claude", "gemini", "opencode"))
            sub.add_argument("--check", action="store_true")
            sub.add_argument("--apply", action="store_true")
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
        if name == "migrate-handoffs":
            sub.description = "Move current-repo legacy skill handoffs into repo-local .dev-skills storage."
            sub.add_argument("--apply", action="store_true")
        if name == "archive-state":
            sub.add_argument("--reason")
        if name == "reconcile":
            sub.description = (
                "Synthesize a v28-shape manual_repair event for the named phase using current "
                "git state, then re-reconcile so status reflects the cleared blocker."
            )
            sub.add_argument("--closeout-commit", help="Commit SHA to record as the closeout commit. Defaults to current HEAD.")
            sub.add_argument("--repair-summary", help="Optional human-authored note explaining the repair.")
            sub.add_argument("--verification-status", choices=("not_run", "passed", "failed"), default="not_run")
            sub.add_argument("--allow-dirty", action="store_true", help="Override the refuse-if-dirty guard. Not recommended.")
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or ("dry-run" if args.dry_run else "run")
    if command == "version":
        print(f"phase-loop {__version__}")
        return 0
    as_json = bool(args.json)
    if command == "closeout-drift-audit":
        return _closeout_drift_audit_command(args=args, as_json=as_json)
    repo = resolve_repo(args.repo or ".")
    if command == "init":
        return _init_command(repo=repo, dry_run=bool(args.dry_run), as_json=as_json)
    if command == "evidence-audit":
        return _evidence_audit_command(repo=repo, args=args, as_json=as_json)
    if command in {"run", "resume", "dry-run"} and bool(getattr(args, "reset_capability", False)):
        clear_degradation(repo)

    if command == "execute":
        phase = args.phase_arg
        output_path = args.output
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
                **snapshot_provenance(select_roadmap(repo, args.roadmap) if args.roadmap else Path(".")),
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
            roadmap=select_roadmap(repo, args.roadmap),
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

    if command == "sync-skills":
        harnesses = tuple(args.harness or ("codex", "claude", "gemini", "opencode"))
        summary = sync_bridge_skills(repo, SyncSkillsOptions(harnesses=harnesses, apply=bool(args.apply)))
        print(render_skill_sync_result(summary, as_json=as_json))
        blocker = summary.get("blocker")
        return 1 if isinstance(blocker, dict) and blocker.get("blocker_class") else 0
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
    if command == "archive-state":
        print(render_archive_result(archive_state(repo, reason=getattr(args, "reason", None)), as_json=as_json))
        return 0
    try:
        roadmap = select_roadmap(repo, args.roadmap)
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
            projection = build_runtime_projection(
                repo,
                roadmap,
                snapshot=snapshot,
                pipeline_mode=args.pipeline_mode or "standalone",
            )
            print(json.dumps(projection, indent=2, sort_keys=True) if as_json else json.dumps(projection, sort_keys=True))
            return 0
        print(render_status(snapshot, as_json=as_json))
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
    if command == "resume":
        snapshot = reconcile(repo, roadmap)
        write_state(repo, snapshot)
        write_tui_handoff(repo, roadmap, snapshot, action="resume")
        print(render_status(snapshot, as_json=as_json))
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

    snapshot, results = run_loop(
        repo=repo,
        roadmap=roadmap,
        phase=effective_phase,
        max_phases=args.max_phases or 1,
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


def _init_command(*, repo: Path, dry_run: bool, as_json: bool) -> int:
    gitignore = repo / ".gitignore"
    handoffs = repo / ".dev-skills" / "handoffs"
    entry = "/.dev-skills/"
    existing = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.exists() else []
    needs_entry = entry not in existing
    needs_handoffs = not handoffs.is_dir()
    actions = {
        "repo": str(repo),
        "dry_run": dry_run,
        "gitignore": str(gitignore),
        "gitignore_entry": entry,
        "gitignore_changed": needs_entry,
        "handoffs": str(handoffs),
        "handoffs_created": needs_handoffs,
    }
    if not dry_run:
        if needs_entry:
            lines = list(existing)
            lines.append(entry)
            gitignore.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        handoffs.mkdir(parents=True, exist_ok=True)
    if as_json:
        print(json.dumps(actions, indent=2, sort_keys=True))
    else:
        mode = "would update" if dry_run else "updated"
        if not needs_entry and not needs_handoffs:
            mode = "already initialized"
        print(f"phase-loop init: {mode} {repo}")
        print(f"gitignore_entry: {entry} ({'needed' if needs_entry else 'present'})")
        print(f"handoffs: {handoffs} ({'needed' if needs_handoffs else 'present'})")
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

    if not topology.get("clean") and not bool(getattr(args, "allow_dirty", False)):
        print(
            "phase-loop reconcile: working tree is dirty. Commit or stash recovery work "
            "before reconciling (or pass --allow-dirty to override).",
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
        "pre_existing_dirty_paths": [],
        "unowned_dirty_paths": [],
        "verification_status": getattr(args, "verification_status", None) or "not_run",
    }
    repair_summary = getattr(args, "repair_summary", None)
    if repair_summary:
        manual_repair["repair_summary"] = repair_summary

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


def _reopen_command(*, repo: Path, roadmap: Path, args: argparse.Namespace, as_json: bool) -> int:
    """Reverse a spurious closeout: append a typed phase_reopen event for --phase.

    Use when an executor reported a phase as complete + verification_status=passed
    but the underlying IF gates were not actually satisfied (e.g., a repair iteration
    that reported done with zero diff and no real work). Appending a phase_reopen
    event flips the phase back to planned in the reducer; the next phase-loop run
    will re-execute it.

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
    if prior_status != "complete":
        print(
            f"phase-loop reopen: phase {phase!r} is currently {prior_status!r}, not 'complete'. "
            "Only complete phases can be reopened. Use `phase-loop reconcile` for blocked phases.",
            file=sys.stderr,
        )
        return 2

    head = topology.get("head")
    prior_closeout = snapshot_before.closeout_summary.get("closeout_commit") if isinstance(snapshot_before.closeout_summary, dict) else None

    phase_reopen = {
        "reason": reason,
        "prior_status": "complete",
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
