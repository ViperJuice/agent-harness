---
phase_loop_plan_version: 1
phase: RUNTIME
roadmap: specs/phase-plans-convergence-v1.md
roadmap_sha256: f98b53d9a8be483cc4278e35ce26e6cf52b3556c223b1418eb14e2a08d522112
---

# RUNTIME: Runtime Substrate Lanes

## Context

RUNTIME implements the non-broker substrate against the committed FREEZE contracts: a coordinator-owned append-only convergence event log, exact-state reconciliation, bounded Codex/Claude/outside-agent adapters, durable advisor-seat outcomes, and transcript-free status/recovery tooling. It does not wire DAG dispatch, downstream refresh, merges, releases, package publication, or any credential-holding operation; INTEG and BROKER own those later boundaries.

The live branch is `phase/conv-runtime` at `e67190e21ad5f534acb88dff9cbd1a30df837c4d`. Canonical `.phase-loop/state.json` identifies RUNTIME as current and FREEZE as complete, although its stored worktree path and branch topology describe the prior convergence worktree. Live Git is clean and the roadmap digest matches the canonical runner digest, so this plan reconciles the stale topology fields from live Git without treating them as a blocker. No legacy `.codex/phase-loop/` state is present or authoritative.

The existing `phase_loop_runtime.events` log remains repo-local phase-loop runner state and is not reused. The new convergence log is coordinator-owned, lives outside every repository's `.phase-loop/`, stores metadata-only records, and preserves the frozen `CoordinatorEvent` / `ConvergenceResultEnvelope` shapes from FREEZE. Existing `phase-loop train-status --train ...` behavior remains a compatibility surface while event-log-only recovery is added.

## Interface Freeze Gates

- [ ] IF-0-RUNTIME-1 — INTEG consumes the exact runtime API: `default_convergence_event_log_path(coordinator_root: Path, train_id: str) -> Path`; `record_intent(path: Path, event: CoordinatorEvent) -> None`; `record_outcome(path: Path, event: CoordinatorEvent) -> None`; `read_convergence_events(path: Path) -> tuple[CoordinatorEvent, ...]`; `recover_train_state(events: Iterable[CoordinatorEvent]) -> RecoveredTrainState`; and `reconcile_train_state(state: RecoveredTrainState, probes: ExactStateProbes) -> ReconciliationVerdict`. `RecoveredTrainState` exposes `train_id`, `node_states`, `pending_attempts`, `latest_epoch`, `verification_valid`, `approval_valid`, `ambiguities`, and `last_event_offset`; `ReconciliationVerdict` exposes the frozen `ReconciliationBinding`, exact live authority observations, normative `InvalidationTrigger` values, a non-secret blocker reason, and `checked_at`. Intent must be durable before dispatch, outcome must match a prior intent by `(train_id, node_id, attempt_id, epoch)`, mixed versions and unknown authority are fail-closed, and no mutation credential or provider side effect crosses this API.

## Lane Index & Dependencies

SL-0 — Convergence event log and exact-state reconciliation
  Depends on: (none)
  Blocks: SL-4, SL-5
  Parallel-safe: yes
SL-1 — Bounded execution adapters
  Depends on: (none)
  Blocks: SL-4, SL-5
  Parallel-safe: yes
SL-2 — Advisor-seat lifecycle persistence
  Depends on: (none)
  Blocks: SL-4, SL-5
  Parallel-safe: yes
SL-3 — Transcript-free status projection
  Depends on: SL-0
  Blocks: SL-4, SL-5
  Parallel-safe: yes
SL-4 — CLI, import surface, and documentation reducer
  Depends on: SL-0, SL-1, SL-2, SL-3
  Blocks: SL-5
  Parallel-safe: no
SL-5 — Whole-phase verification reducer
  Depends on: SL-0, SL-1, SL-2, SL-3, SL-4
  Blocks: (none)
  Parallel-safe: no

## Lanes

### SL-0 — Convergence Event Log And Exact-State Reconciliation

- **Scope**: Implement IF-0-RUNTIME-1 as a crash-safe metadata-only log plus injected read-only Git, GitHub, provider, and registry authority probes.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/convergence/event_log.py`, `phase-loop-runtime/src/phase_loop_runtime/convergence/reconcile.py`, `phase-loop-runtime/tests/test_convergence_event_log.py`, `phase-loop-runtime/tests/test_convergence_reconcile.py`
- **Interfaces provided**: `IF-0-RUNTIME-1`, `RecoveredTrainState`, `ExactStateProbes`, `ReconciliationVerdict`, `default_convergence_event_log_path`, `record_intent`, `record_outcome`, `read_convergence_events`, `recover_train_state`, `reconcile_train_state`
- **Interfaces consumed**: `CoordinatorEvent`, `CoordinatorEventKind`, `AuthoritySource`, `InvalidationTrigger`, `ReconciliationBinding` (pre-existing)
- **Parallel-safe**: yes; the lane owns only the two new runtime modules and their focused tests.
- **Tasks**:
  - test: Prove coordinator-root path derivation cannot place a convergence log under any repository `.phase-loop/`, and prove every append is one canonical JSON record with an explicit event/transition/invalidation version.
  - test: Kill or fault-inject between intent and outcome writes; require `record_intent` durability before return, tolerate only a malformed trailing record, fail on mid-log corruption, and recover the unmatched attempt without transcript input.
  - test: Reject an outcome with no matching intent or mismatched train/node/attempt/epoch, keep duplicate identical records idempotent, and fail closed on conflicting duplicates, mixed versions, epoch regression, or an unresolved ambiguous provider outcome.
  - test: Drive injected Git, GitHub, provider, and registry probes through matching, stale-head, changed-roadmap, changed-base, changed-dependency, changed-verification-plan, unavailable-authority, merged-SHA, and released-identity cases; assert the exact frozen authority and invalidation enums.
  - impl: Serialize appends with a process/thread-safe single-writer boundary, one canonical UTF-8 JSON line, flush plus `fsync` before success, metadata-only size bounds, and tolerant-final-record read semantics without importing repo-local `phase_loop_runtime.events`.
  - impl: Fold last valid intent/outcome state deterministically by `(train_id, node_id, attempt_id, epoch)` and expose pending attempts, latest epochs, approval/verification validity, ambiguity, and source offsets in `RecoveredTrainState`.
  - impl: Implement read-only authority probes behind `ExactStateProbes`; probe only authority domains named by the recovered events, never mutate, never infer success from transcripts or `.phase-loop`, and return a fail-closed `ReconciliationVerdict` when required authority is missing or stale.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_event_log.py tests/test_convergence_reconcile.py tests/test_convergence_event_contracts.py tests/test_convergence_coordination_contracts.py -q`

### SL-1 — Bounded Execution Adapters

- **Scope**: Add three non-coordinating adapters that carry the frozen expected-version fence and always return the shared result envelope.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/convergence/adapters/__init__.py`, `phase-loop-runtime/src/phase_loop_runtime/convergence/adapters/base.py`, `phase-loop-runtime/src/phase_loop_runtime/convergence/adapters/codex.py`, `phase-loop-runtime/src/phase_loop_runtime/convergence/adapters/claude.py`, `phase-loop-runtime/src/phase_loop_runtime/convergence/adapters/outside_agent.py`, `phase-loop-runtime/tests/test_convergence_adapters.py`
- **Interfaces provided**: `AdapterExecutionRequest`, `run_codex_adapter`, `run_claude_adapter`, `run_outside_agent_adapter`
- **Interfaces consumed**: `AdmissionRequest`, `ConvergenceResultEnvelope`, `ConvergenceResultStatus`, `validate_outside_agent_submission` (pre-existing)
- **Parallel-safe**: yes; the new adapter package and its focused contract test are disjoint from every other producer lane.
- **Tasks**:
  - test: Freeze `AdapterExecutionRequest` around `attempt_id`, the seven-field `AdmissionRequest`, bounded argv/cwd/timeout, allowed action, metadata-only evidence references, and no raw credential or coordinator-state field.
  - test: For Codex, Claude, and one real outside-agent conformance path, cover success, verified success, non-zero exit, timeout, malformed/unknown envelope, expected-version mismatch, capability overclaim, and action-outside-bounds; every path returns one of the six frozen result statuses.
  - test: Assert process groups are bounded and reclaimed, output/detail is size-limited and metadata-only, provider API-key environment variables are not forwarded, and adapters never append coordinator state or invoke publish/merge/release/package verbs.
  - impl: Reuse existing launcher/liveness termination seams where their contracts match, while keeping provider-specific command construction isolated in the three adapter modules.
  - impl: Validate outside-agent submissions through the existing metadata-only conformance validator and map validation failures to `blocked` or `failed` without accepting forged completion evidence.
  - impl: Return `ConvergenceResultEnvelope` for every terminal path, preserving `attempt_id` and the request's expected-version predicate; adapters execute one bounded unit and never coordinate a train.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_adapters.py tests/test_launcher_liveness.py tests/test_outside_agent_core_api.py tests/test_outside_agent_schema_validation.py tests/test_outside_agent_authority_boundary.py -q`

### SL-2 — Advisor-Seat Lifecycle Persistence

- **Scope**: Extend the existing panel lifecycle so each requested seat yields a complete, metadata-only outcome record that can be durably appended to the convergence event log.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py`, `phase-loop-runtime/tests/test_convergence_seat_lifecycle.py`
- **Interfaces provided**: `SeatOutcomeRecord`, `serialize_seat_outcome`, `persist_seat_outcome`
- **Interfaces consumed**: `PanelResult`, `PanelLegResult`, `CoordinatorEvent`, `ConvergenceResultEnvelope` (pre-existing)
- **Parallel-safe**: yes; `panel_invoker.py` remains a single-writer surface and no other RUNTIME lane owns it.
- **Tasks**:
  - test: Freeze `SeatOutcomeRecord` fields for `seat_key`, vendor leg, required/optional posture, terminal status, timeout/degraded reason, attempt/epoch binding, artifact digest, completion timestamp, and evidence digest; raw review text is excluded.
  - test: Prove every requested seat produces exactly one outcome for OK, EMPTY, TIMEOUT, ERROR, DEGRADED, UNAVAILABLE, native-fill, raised callback, and reclaimed-liveness cases, preserving requested seat order and same-vendor `seat_key` identity.
  - test: Persist each terminal callback through an injected append sink as an outcome `CoordinatorEvent` whose `seat_outcomes` contains canonical metadata-only JSON; sink failure is surfaced as degraded/blocked evidence and is never silently dropped.
  - impl: Add outcome serialization and a persistence callback at the existing per-seat completion boundary without changing default panel result bytes, leg scheduling, liveness timeouts, model routing, or advisor verdict semantics.
  - impl: Keep the event-log writer injected so this lane compiles and tests against the plan-frozen interface independently; the reducer integration test binds it to `record_outcome` after producer lanes merge.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_seat_lifecycle.py tests/test_panel_invoker.py tests/test_panel_invoker_spawn.py tests/test_panel_tui_liveness_188.py -q`

### SL-3 — Transcript-Free Status Projection

- **Scope**: Build deterministic status and recovery projections from `RecoveredTrainState` without reading transcripts, repo-local runner state, or mutation credentials.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/convergence/status.py`, `phase-loop-runtime/tests/test_convergence_status.py`
- **Interfaces provided**: `TrainStatusSnapshot`, `build_train_status`, `render_train_status`
- **Interfaces consumed**: `RecoveredTrainState`
- **Parallel-safe**: yes; the pure status projection module consumes the plan-frozen recovered-state shape and does not import unmerged producer modules at module load.
- **Tasks**:
  - test: Project empty, intent-only, running, completed, verified, blocked, degraded-seat, invalidated, ambiguous-provider, mixed-version, and multi-node/multi-epoch histories into deterministic human and JSON views.
  - test: Assert the projection names event-log path/digest, last offset, pending attempts, exact authority/invalidation state, seat completeness, and non-secret next action while excluding transcript text, environment values, credentials, and repo-local `.phase-loop` claims.
  - test: Reconstruct identical output from identical recovered events after process restart and prove no filesystem or subprocess mutation occurs.
  - impl: Add immutable `TrainStatusSnapshot`, `build_train_status`, and stable human/JSON rendering over `RecoveredTrainState`; unknown or unreconciled facts stay explicit rather than becoming success.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_status.py -q`

### SL-4 — CLI, Import Surface, And Documentation Reducer

- **Scope**: Integrate all producer APIs into the package and `train-status` command, document the runtime boundary, and prove the event-log-only path end to end.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/convergence/__init__.py`, `phase-loop-runtime/src/phase_loop_runtime/cli.py`, `phase-loop-runtime/tests/test_convergence_runtime_imports.py`, `phase-loop-runtime/tests/test_cli_train_status_45.py`, `docs/phase-loop/convergence-runtime.md`
- **Interfaces provided**: `RUNTIME CLI and import surface`, `RUNTIME documentation`, `RUNTIME acceptance evidence`
- **Interfaces consumed**: `IF-0-RUNTIME-1`, `AdapterExecutionRequest`, `run_codex_adapter`, `run_claude_adapter`, `run_outside_agent_adapter`, `SeatOutcomeRecord`, `persist_seat_outcome`, `TrainStatusSnapshot`, `build_train_status`, `render_train_status`
- **Parallel-safe**: no; this is the terminal writer reducer for shared package, CLI, integration-test, and documentation surfaces.
- **Tasks**:
  - test: Extend `test_cli_train_status_45.py` so `phase-loop train-status --event-log PATH [--json]` works without `--train`, is mutually exclusive with legacy ledger selection, is read-only, and leaves the existing `--train` output/tests unchanged.
  - test: Add an integration test that records intent, persists required/optional seat outcomes, records outcome, restarts from disk, recovers state, renders status, and reconciles injected exact authority without transcripts or `.phase-loop`.
  - test: Assert the public package surface imports every IF-0-RUNTIME-1 symbol and adapter/status/seat contract only after all producer modules exist.
  - impl: Add the event-log CLI mode and package exports, preserving the Python console-script entrypoint and legacy train-ledger path; no `pyproject.toml`, lockfile, migration, or environment-example change is needed.
  - impl: Document log placement, atomicity and corruption handling, authority split, invalidation behavior, adapter bounds, seat completeness, event-log-only recovery, CLI examples, non-broker boundary, and the explicit absence of mutation credentials.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_runtime_imports.py tests/test_cli_train_status_45.py tests/test_convergence_event_log.py tests/test_convergence_reconcile.py tests/test_convergence_adapters.py tests/test_convergence_seat_lifecycle.py tests/test_convergence_status.py -q`

### SL-5 — Whole-Phase Verification Reducer

- **Scope**: Run the complete RUNTIME proof set and emit closeout evidence for IF-0-RUNTIME-1 without repairing producer-owned files.
- **Owned files**: none
- **Interfaces provided**: `RUNTIME verification evidence`
- **Interfaces consumed**: `IF-0-RUNTIME-1`, `RUNTIME CLI and import surface`, `RUNTIME documentation`, `RUNTIME acceptance evidence`
- **Parallel-safe**: no; this read-only reducer runs only after every writer and documentation reducer completes.
- **Tasks**:
  - test: Run roadmap/plan validators, every focused RUNTIME module, the preserved FREEZE contract tests, panel liveness regressions, legacy train-status tests, and train INV-1 through INV-7.
  - impl: Do not edit files in this lane; route a failure to the sole owning producer lane and rerun the full reducer.
  - verify: `PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-convergence-v1.md && python skills-src/claude/claude-plan-phase/scripts/validate_plan_doc.py plans/phase-plan-vergence-v1-RUNTIME.md && cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_event_log.py tests/test_convergence_reconcile.py tests/test_convergence_adapters.py tests/test_convergence_seat_lifecycle.py tests/test_convergence_status.py tests/test_convergence_runtime_imports.py tests/test_cli_train_status_45.py tests/test_convergence_event_contracts.py tests/test_convergence_provider_contracts.py tests/test_convergence_coordination_contracts.py tests/test_convergence_fixture_contracts.py tests/test_panel_invoker.py tests/test_panel_invoker_spawn.py tests/test_panel_tui_liveness_188.py tests/test_train_roadmap.py tests/test_train_invariants.py -q`

## Execution Policy

- work-unit defaults: work-unit=`lane_execute`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`
- SL-4: executor=`codex`, effort=`high`, work-unit=`phase_reducer`, unsupported=`inherit_default`, inherit-default=`true`, reason=`cross-lane CLI import and documentation synthesis`
- SL-5: executor=`codex`, effort=`high`, work-unit=`phase_verify`, unsupported=`inherit_default`, inherit-default=`true`, reason=`whole-phase verification reducer`

## Execution Notes

- SL-0 through SL-3 may run in one writer wave only when the scheduler assigns separate worktrees and machine-verifies the declared file sets are disjoint. SL-4 and SL-5 are excluded from producer waves.
- The complete phase-owned write set is the union of SL-0 through SL-4 `Owned files`; no lane may widen scope to `train_runner.py`, `publishing.py`, `convergence/broker/**`, provider mutation code, release manifests, dependency files, migrations, or environment examples.
- Cross-lane runtime calls use the IF-0-RUNTIME-1 signatures and injected callback/probe seams during producer work. No producer may create a second shared types file or edit `convergence/__init__.py` early to make an isolated lane pass.
- `phase_loop_runtime.events` and legacy `train_ledger.py` remain behaviorally intact. The new log is a distinct coordinator-owned surface outside every repo `.phase-loop/`; legacy `.codex/phase-loop/` compatibility state is neither an authority nor a blocker.
- Seat persistence records status and evidence digests, never raw advisor prose. Adapter and reconciliation detail fields remain non-secret and metadata-only.
- Public-surface decision: `docs/phase-loop/convergence-runtime.md` is required because RUNTIME adds a new operator-visible `train-status --event-log` mode and recovery contract; `README.md`, changelog, and release notes remain unchanged until RELEASE.
- Closeout must list `IF-0-RUNTIME-1` in `produced_if_gates`. Passing tests without the gate or with unresolved required-seat outcomes, pending authority checks, or ambiguous provider state is not complete.
- Policy precedence is CLI/operator override, this plan, roadmap policy, `Dispatch Hints`, then registry defaults. Silent model/effort downgrade is forbidden; declared `inherit_default` is the only default inheritance here.

## Spec Closeout Plan

- schema: `spec_delta_closeout.v1`
- decision: `canonical_spec_update`
- target surfaces: `phase-loop-runtime/src/phase_loop_runtime/convergence/event_log.py`, `phase-loop-runtime/src/phase_loop_runtime/convergence/reconcile.py`, `phase-loop-runtime/src/phase_loop_runtime/convergence/adapters/**`, `phase-loop-runtime/src/phase_loop_runtime/convergence/status.py`, `phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py`, `phase-loop-runtime/src/phase_loop_runtime/cli.py`, `phase-loop-runtime/tests/test_convergence_*.py`, `phase-loop-runtime/tests/test_cli_train_status_45.py`, `docs/phase-loop/convergence-runtime.md`
- evidence paths: `plans/phase-plan-vergence-v1-RUNTIME.md`, `docs/phase-loop/convergence-runtime.md`, `phase-loop-runtime/tests/test_convergence_event_log.py`, `phase-loop-runtime/tests/test_convergence_reconcile.py`, `phase-loop-runtime/tests/test_convergence_adapters.py`, `phase-loop-runtime/tests/test_convergence_seat_lifecycle.py`, `phase-loop-runtime/tests/test_convergence_status.py`, `phase-loop-runtime/tests/test_convergence_runtime_imports.py`, `phase-loop-runtime/tests/test_cli_train_status_45.py`
- redaction posture: `metadata_only`
- downstream handling: `none`

## Verification

```bash
PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-convergence-v1.md
python skills-src/claude/claude-plan-phase/scripts/validate_plan_doc.py plans/phase-plan-vergence-v1-RUNTIME.md
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_event_log.py tests/test_convergence_reconcile.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_adapters.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_seat_lifecycle.py tests/test_panel_invoker.py tests/test_panel_invoker_spawn.py tests/test_panel_tui_liveness_188.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_status.py tests/test_convergence_runtime_imports.py tests/test_cli_train_status_45.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_event_contracts.py tests/test_convergence_provider_contracts.py tests/test_convergence_coordination_contracts.py tests/test_convergence_fixture_contracts.py tests/test_train_roadmap.py tests/test_train_invariants.py -q
```

automation:
  suite_command: cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_event_log.py tests/test_convergence_reconcile.py tests/test_convergence_adapters.py tests/test_convergence_seat_lifecycle.py tests/test_convergence_status.py tests/test_convergence_runtime_imports.py tests/test_cli_train_status_45.py tests/test_convergence_event_contracts.py tests/test_convergence_provider_contracts.py tests/test_convergence_coordination_contracts.py tests/test_convergence_fixture_contracts.py tests/test_panel_invoker.py tests/test_panel_invoker_spawn.py tests/test_panel_tui_liveness_188.py tests/test_train_roadmap.py tests/test_train_invariants.py -q

## Acceptance Criteria

- [ ] `phase-loop-runtime/tests/test_convergence_event_log.py` proves durable intent-before/outcome-after ordering, restart recovery, final-line crash tolerance, mid-log fail-loud behavior, idempotent duplicate handling, epoch fencing, mixed-version blocking, and a coordinator-owned path outside `.phase-loop/`.
- [ ] `phase-loop-runtime/tests/test_convergence_reconcile.py` proves exact Git/GitHub/provider/registry authority selection and all five frozen invalidation triggers, with unknown, stale, or ambiguous state fail-closed.
- [ ] `phase-loop-runtime/tests/test_convergence_adapters.py` proves Codex, Claude, and outside-agent adapters are bounded, credential-free, non-coordinating, expected-version-bound, and return only the six `ConvergenceResultStatus` values.
- [ ] `phase-loop-runtime/tests/test_convergence_seat_lifecycle.py` proves every required and optional seat produces exactly one metadata-only outcome, including timeout/degraded/unavailable/native-fill cases, and persistence failure cannot disappear.
- [ ] `phase-loop-runtime/tests/test_convergence_status.py` and `phase-loop-runtime/tests/test_cli_train_status_45.py` prove deterministic transcript-free recovery and `phase-loop train-status --event-log PATH`, while all legacy `--train` behavior remains green and read-only.
- [ ] `phase-loop-runtime/tests/test_convergence_runtime_imports.py` proves the integrated IF-0-RUNTIME-1 package surface, intent → seat outcomes → outcome restart path, and exact-state reconciliation without transcript or repo-local runner-state input.
- [ ] `docs/phase-loop/convergence-runtime.md` documents operator usage, event durability/corruption semantics, authority and invalidation rules, adapter/seat bounds, metadata-only redaction, and the RUNTIME/BROKER/INTEG boundary.
- [ ] `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_event_log.py tests/test_convergence_reconcile.py tests/test_convergence_adapters.py tests/test_convergence_seat_lifecycle.py tests/test_convergence_status.py tests/test_convergence_runtime_imports.py tests/test_cli_train_status_45.py tests/test_convergence_event_contracts.py tests/test_convergence_provider_contracts.py tests/test_convergence_coordination_contracts.py tests/test_convergence_fixture_contracts.py tests/test_panel_invoker.py tests/test_panel_invoker_spawn.py tests/test_panel_tui_liveness_188.py tests/test_train_roadmap.py tests/test_train_invariants.py -q` passes and closeout lists `IF-0-RUNTIME-1`.
