---
phase_loop_plan_version: 1
phase: INTEG
roadmap: specs/phase-plans-convergence-v1.md
roadmap_sha256: f98b53d9a8be483cc4278e35ce26e6cf52b3556c223b1418eb14e2a08d522112
---

# INTEG: Coordinator Integration

## Context

INTEG wires the committed RUNTIME and BROKER substrates into the cross-repo train coordinator. The phase must replace transcript- and legacy-ledger-led action decisions with reconcile-before-act decisions over the coordinator event log and exact live authority, route every external mutation through the credential broker, add machine-checked DAG concurrency, and make downstream refresh, verification, review invalidation, and resume behavior crash-safe.

The live branch is `phase/conv-integ` at `1f633a538feca8f31d4f537520318a1cc4ab1311`. It contains the completed FREEZE, RUNTIME, and BROKER commits, including the merged BROKER branch at `ab128a0`. Canonical `.phase-loop/state.json` has the matching roadmap digest and records FREEZE, RUNTIME, and BROKER complete with INTEG unplanned, but its repo/branch topology still describes the prior RUNTIME worktree. The active canonical INTEG launch record plus live Git identify this worktree, so the stale snapshot fields are reconciled rather than treated as a blocker. Legacy `.codex/phase-loop/` state is absent and has no authority.

The roadmap names four concerns whose implementation would otherwise overlap on `train_runner.py`: DISPATCH, FENCING, INVALIDATE, and REFRESH. This plan isolates each concern behind one disjoint convergence module and focused test surface, then gives a terminal integration/documentation reducer sole ownership of `train_runner.py`, CLI wiring, shared exports, legacy train regression tests, and operator docs. This preserves the roadmap design while satisfying the phase-loop single-writer and lane-IR ownership contracts.

## Interface Freeze Gates

- [ ] IF-0-INTEG-1 — `run_train` and the `phase-loop run-train` entrypoint consume a coordinator-owned `CoordinatorRuntime` containing `train_id`, `coordinator_root`, canonical roadmap path/digest, workspace identity, supported event/transition/invalidation versions, exact Git/GitHub/provider/registry probes, and the credential-free `BrokerClient` boundary. Before each dispatch, resume, publish, review, merge, release, or package action the coordinator must recover the append-only event log and reconcile it against exact live authority; unsupported or mixed versions, missing authority, invalid verification evidence, ambiguous/pending provider outcomes, stale attempts/epochs/fences, and digest-invalid approvals block before action. Every admitted action binds one immutable `(attempt_id, lease_epoch, fence_token, approval_digest, expected_version_predicate, authority_domain_scope, idempotency_key)` request; its durable intent precedes dispatch and its durable terminal outcome follows dispatch. Independent nodes may execute concurrently only after `evaluate_resource_isolation` proves different repositories, disjoint non-empty owned paths, and frozen shared interfaces, with the decision persisted; same-repo work, predicate-false/unknown pairs, topological merges, and release publication serialize. After an upstream merge, each affected downstream branch is refreshed to the exact merged SHA or returns a typed conflict, its verification and prior approval are invalidated, the bound verification suite runs and emits a digest-addressed artifact, and only then may a broker-admitted republish/review/merge occur. Resume is event-log-first and idempotent, never double-acts a stale worker, never accepts legacy ledger state over live authority, preserves the autonomous `drafts_open` stop, and retains existing result statuses and INV-1 through INV-6.

## Lane Index & Dependencies

SL-0 — DAG dispatch and repository isolation
  Depends on: (none)
  Blocks: SL-4
  Parallel-safe: yes
SL-1 — Attempt fencing and approval binding
  Depends on: (none)
  Blocks: SL-2, SL-3, SL-4
  Parallel-safe: yes
SL-2 — Exact-state invalidation and action admission
  Depends on: SL-1
  Blocks: SL-3, SL-4
  Parallel-safe: yes
SL-3 — Downstream refresh and bound re-verification
  Depends on: SL-1, SL-2
  Blocks: SL-4
  Parallel-safe: yes
SL-4 — Coordinator integration and documentation reducer
  Depends on: SL-0, SL-1, SL-2, SL-3
  Blocks: SL-5
  Parallel-safe: no
SL-5 — Whole-phase verification reducer
  Depends on: SL-0, SL-1, SL-2, SL-3, SL-4
  Blocks: (none)
  Parallel-safe: no

## Lanes

### SL-0 — DAG Dispatch And Repository Isolation

- **Scope**: Implement a bounded scheduler that admits ready train nodes concurrently only when the frozen resource-isolation predicate proves the pair safe and otherwise serializes them with a persisted reason.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/convergence/dispatch.py`, `phase-loop-runtime/tests/test_convergence_dispatch.py`
- **Interfaces provided**: `RepositoryDispatchRequest`, `RepositoryLockKey`, `DispatchDecision`, `dispatch_ready_nodes`
- **Interfaces consumed**: `ResourceIsolationDecision` (pre-existing), `evaluate_resource_isolation` (pre-existing), `ConvergenceResultEnvelope` (pre-existing), `TrainRoadmap` (pre-existing)
- **Parallel-safe**: yes; this lane owns one new scheduler module and its focused tests and does not touch the train coordinator entrypoint.
- **Tasks**:
  - test: Prove independent ready DAG nodes overlap in execution only when their repositories differ, owned paths are complete and disjoint, shared interfaces are frozen, and scheduler-owned repository locks are held for the complete work unit.
  - test: Prove same-repo work, overlapping or missing owned paths, unknown isolation evidence, predicate-false pairs, topological merges, and release publication serialize without starvation, while every allow/serialize decision exposes a deterministic metadata-only reason for the event log.
  - test: Prove bounded worker failure, cancellation, and a stale completion produce one structured result per attempted node and cannot release another node's lock or cause a second dispatch.
  - impl: Add immutable dispatch request/decision types, a per-repository lock table, deterministic ready-node selection, a bounded executor seam, and fail-closed pairwise isolation evaluation using the committed FREEZE predicate.
  - impl: Keep event persistence and `run_train` mutation outside this module; accept injected intent/outcome callbacks so SL-4 can durably bind scheduler decisions without sharing writable files.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_dispatch.py tests/test_convergence_coordination_contracts.py -q`

### SL-1 — Attempt Fencing And Approval Binding

- **Scope**: Build immutable attempt leases and approval bindings that generate the sole shared broker admission request and reject stale or digest-mismatched work before mutation.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/convergence/fencing.py`, `phase-loop-runtime/tests/test_convergence_fencing.py`
- **Interfaces provided**: `AttemptLease`, `ApprovalBinding`, `FencedAdmissionFactory`, `compute_approval_digest`, `validate_attempt_lease`
- **Interfaces consumed**: `AdmissionRequest` (pre-existing), `CoordinatorEvent` (pre-existing), `InvalidationTrigger` (pre-existing)
- **Parallel-safe**: yes; this lane owns only the fencing module and focused tests and consumes the frozen admission shape without modifying it.
- **Tasks**:
  - test: Prove each node/action attempt receives a unique attempt id and monotonic lease epoch, the fence token is bound to the exact train/node/action/epoch, and a resumed or late worker cannot admit after a newer epoch exists.
  - test: Prove `approval_digest` binds canonical roadmap digest, effective code/head, base SHA, ordered dependency SHAs, verification-plan digest, and verification-artifact digest; changing any component invalidates approval and produces a different idempotency key.
  - test: Prove `FencedAdmissionFactory` emits all seven IF-0-FREEZE-7 fields, refuses absent/empty evidence, preserves identical replay, rejects conflicting key reuse, and never serializes secret values.
  - impl: Add immutable lease/binding types plus deterministic canonical hashing and validation helpers that can be reconstructed from metadata-only event records after restart.
  - impl: Construct `AdmissionRequest` only after the current lease, exact-version predicate, authority scope, verification evidence, and approval binding validate; do not import provider credentials or call the broker here.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_fencing.py tests/test_convergence_broker_admission.py tests/test_convergence_coordination_contracts.py -q`

### SL-2 — Exact-State Invalidation And Action Admission

- **Scope**: Extend reconciliation from a status verdict into an action-specific, supported-version gate that invalidates verification and approval on every frozen trigger before creating an admission request.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/convergence/reconcile.py`, `phase-loop-runtime/tests/test_convergence_reconcile.py`, `phase-loop-runtime/tests/test_convergence_invalidation.py`
- **Interfaces provided**: `ActionReconciliation`, `SupportedConvergenceVersions`, `reconcile_before_action`, `invalidate_action_evidence`
- **Interfaces consumed**: `RecoveredTrainState` (pre-existing), `ExactStateProbes` (pre-existing), `ReconciliationVerdict` (pre-existing), `InvalidationTrigger` (pre-existing), `ApprovalBinding`
- **Parallel-safe**: yes; this lane is the sole INTEG writer for reconciliation and its matching focused tests.
- **Tasks**:
  - test: Prove every dispatch/resume/publish/review/merge/release/package action invokes Git, GitHub, provider, and registry probes against the current recovered event-log state and blocks on any absent, unknown, or conflicting authority.
  - test: Prove unsupported or mixed event-schema, transition-model, or invalidation-model versions reject rather than coerce, while a fully supported homogeneous log remains replayable.
  - test: Parameterize `effective_code_changed`, `roadmap_changed`, `base_sha_changed`, `dependency_sha_changed`, and `verification_plan_digest_changed`; each must clear both verification and approval, name the trigger, and prevent action until fresh bound evidence exists.
  - test: Prove a verification artifact path without a matching digest, a changed artifact, pending intent, ambiguous terminal outcome, or stale approval binding blocks with metadata-only diagnostics before broker admission.
  - impl: Add supported-version and action-verdict types, extend `reconcile_train_state` without weakening its current fail-closed semantics, and make verification/approval invalidation explicit and reconstructable.
  - impl: Provide a pure `reconcile_before_action` gate consumed by SL-4; do not perform Git mutation, broker execution, dispatch, or downstream refresh in this lane.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_reconcile.py tests/test_convergence_invalidation.py tests/test_convergence_event_log.py tests/test_convergence_provider_contracts.py -q`

### SL-3 — Downstream Refresh And Bound Re-Verification

- **Scope**: Implement the post-merge downstream refresh state machine that pins exact merged SHAs, invalidates stale evidence, runs bound verification, and returns a broker-ready republish request or a typed conflict/blocker.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/convergence/refresh.py`, `phase-loop-runtime/tests/test_convergence_refresh.py`
- **Interfaces provided**: `DownstreamRefreshRequest`, `DownstreamRefreshResult`, `DownstreamRefreshStatus`, `refresh_downstream_after_merge`
- **Interfaces consumed**: `FencedAdmissionFactory`, `ActionReconciliation`, `invalidate_action_evidence`, `AdmissionRequest` (pre-existing), `BrokerClient` (pre-existing), `TrainEdge` (pre-existing)
- **Parallel-safe**: yes; this lane owns one new refresh module and test and operates through injected branch, verification, and broker seams.
- **Tasks**:
  - test: Prove an upstream merge refreshes every non-order-only downstream channel to the exact observed merge SHA, records ordered dependency SHAs, invalidates the prior verification/review, and never uses the draft head or a moving branch name.
  - test: Prove a refresh conflict returns a typed `conflict` result before verification or republish, preserves already-merged upstream state, and records enough non-secret metadata for deterministic resume.
  - test: Prove successful refresh runs the roadmap-bound verification suite, hashes the runner-produced artifact, creates a fresh approval/admission binding, and calls only `BrokerClient.execute` for republish; failed or missing verification evidence never reaches the broker.
  - test: Prove restart after refresh, verification, or broker intent replays idempotently; a prior review is never reused and an ambiguous in-flight provider request permanently blocks the epoch.
  - impl: Add a pure orchestration state machine over injected channel-refresh, verification, event, and broker seams, returning exact statuses for refreshed, conflict, verification-blocked, broker-blocked, and ambiguous outcomes.
  - impl: Preserve order-only dependency behavior and forward-only upstream merges; do not own `train_runner.py`, the CLI, publishing internals, shared exports, or documentation.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_refresh.py tests/test_convergence_fencing.py tests/test_convergence_invalidation.py tests/test_publishing.py -q`

### SL-4 — Coordinator Integration And Documentation Reducer

- **Scope**: Make the live train entrypoint event-log-first and broker-only by composing every producer interface, preserving legacy result shapes/invariants, and updating the complete operator documentation surface.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/train_runner.py`, `phase-loop-runtime/src/phase_loop_runtime/train_ledger.py`, `phase-loop-runtime/src/phase_loop_runtime/cli.py`, `phase-loop-runtime/src/phase_loop_runtime/convergence/__init__.py`, `phase-loop-runtime/tests/test_convergence_train_integration.py`, `phase-loop-runtime/tests/test_convergence_cli_integration.py`, `phase-loop-runtime/tests/test_convergence_event_contracts.py`, `phase-loop-runtime/tests/test_convergence_runtime_imports.py`, `phase-loop-runtime/tests/test_train_runner.py`, `phase-loop-runtime/tests/test_train_merge.py`, `phase-loop-runtime/tests/test_train_e2e.py`, `phase-loop-runtime/tests/test_train_invariants.py`, `phase-loop-runtime/tests/test_train_order_only_deps_47.py`, `phase-loop-runtime/tests/test_cli_train_status_45.py`, `docs/phase-loop/convergence-runtime.md`, `docs/phase-loop/cross-repo-train-authoring.md`
- **Interfaces provided**: `IF-0-INTEG-1`, `CoordinatorRuntime`, `event-log-first run_train resume contract`, `broker-only run-train CLI wiring`, `INTEG operator documentation`
- **Interfaces consumed**: `RepositoryDispatchRequest`, `DispatchDecision`, `dispatch_ready_nodes`, `AttemptLease`, `ApprovalBinding`, `FencedAdmissionFactory`, `ActionReconciliation`, `SupportedConvergenceVersions`, `reconcile_before_action`, `invalidate_action_evidence`, `DownstreamRefreshRequest`, `DownstreamRefreshResult`, `refresh_downstream_after_merge`, `IF-0-RUNTIME-1` (pre-existing), `IF-0-BROKER-1` (pre-existing)
- **Parallel-safe**: no; this terminal synthesized writer alone owns every shared coordinator, schema, CLI, regression, export, and documentation surface after all producer APIs are frozen.
- **Tasks**:
  - test: Add an end-to-end fake-authority/fake-provider train proving event-log recovery, reconcile-before-every-action ordering, intent-before-dispatch/outcome-after-dispatch durability, broker-only external mutation, and idempotent resume without transcript or legacy-ledger authority.
  - test: Prove independent nodes dispatch concurrently only after persisted isolation approval, same-repo and predicate-false work serializes, topological merges remain serial, autonomous mode stops at `drafts_open`, and stale workers/epochs cannot double-publish or double-merge.
  - test: Prove an upstream merge automatically refreshes, verifies, republishes, and re-reviews each affected downstream against the exact merged SHA; a conflict, missing artifact digest, stale approval, unknown version, or ambiguous provider outcome returns an existing fail-closed result status without direct provider mutation.
  - test: Extend CLI tests so `phase-loop run-train` creates/uses the coordinator event-log location, constructs a credential-free `CoordinatorRuntime`, refuses any live side effect when broker admission or exact authority is unavailable, and keeps `phase-loop train-status --event-log` deterministic and read-only.
  - test: Keep `test_train_runner.py`, `test_train_merge.py`, `test_train_e2e.py`, `test_train_order_only_deps_47.py`, and INV-1 through INV-6 green, including zero-PR preflight failure, drafts-open autonomy, exact merged-SHA re-verification, crash resume, and forward-only merges.
  - impl: Add `CoordinatorRuntime`, extend additive metadata-only coordinator event fields needed to replay fencing/isolation/approval bindings, and compose dispatch, reconciliation, fencing, refresh, verification, and broker calls inside `run_train` while preserving injectable non-live test seams.
  - impl: Remove live coordinator defaults that call push, PR-create, merge, release, or package providers directly; the CLI path must supply the event log, exact probes, supported versions, and credential-free broker client, while provider credentials remain confined to the broker boundary.
  - impl: Export INTEG types only after all producer modules land and update convergence/runtime plus train-authoring docs with action order, authority/version failure modes, lock/isolation behavior, event-log resume, approval invalidation, downstream refresh, broker-only mutation, autonomous stop, and typed conflict recovery.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_train_integration.py tests/test_convergence_cli_integration.py tests/test_convergence_dispatch.py tests/test_convergence_fencing.py tests/test_convergence_invalidation.py tests/test_convergence_refresh.py tests/test_convergence_event_contracts.py tests/test_convergence_runtime_imports.py tests/test_train_runner.py tests/test_train_merge.py tests/test_train_e2e.py tests/test_train_invariants.py tests/test_train_order_only_deps_47.py tests/test_cli_train_status_45.py -q`

### SL-5 — Whole-Phase Verification Reducer

- **Scope**: Run the complete INTEG, RUNTIME, BROKER, FREEZE, publisher, CLI, and legacy train proof set and emit closeout evidence for IF-0-INTEG-1 without owning or repairing producer files.
- **Owned files**: none
- **Interfaces provided**: `INTEG verification evidence`
- **Interfaces consumed**: `IF-0-INTEG-1`, `INTEG operator documentation`
- **Parallel-safe**: no; this read-only reducer runs only after all writers and the documentation reducer complete.
- **Tasks**:
  - test: Run the roadmap and plan validators, all focused INTEG tests, preserved RUNTIME/BROKER/FREEZE contracts, publisher regressions, CLI status/entrypoint tests, legacy train suites, and INV-1 through INV-6.
  - impl: Do not edit files in this lane; route any failure to the sole owning producer lane and rerun the complete reducer after repair.
  - verify: `PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-convergence-v1.md && python skills-src/claude/claude-plan-phase/scripts/validate_plan_doc.py plans/phase-plan-vergence-v1-INTEG.md && cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_dispatch.py tests/test_convergence_fencing.py tests/test_convergence_invalidation.py tests/test_convergence_refresh.py tests/test_convergence_train_integration.py tests/test_convergence_cli_integration.py tests/test_convergence_event_log.py tests/test_convergence_reconcile.py tests/test_convergence_runtime_imports.py tests/test_convergence_event_contracts.py tests/test_convergence_provider_contracts.py tests/test_convergence_coordination_contracts.py tests/test_convergence_broker_admission.py tests/test_convergence_broker_evidence.py tests/test_convergence_broker_verbs.py tests/test_convergence_broker_credsep.py tests/test_convergence_broker_api.py tests/test_publishing.py tests/test_cli_train_status_45.py tests/test_train_roadmap.py tests/test_train_runner.py tests/test_train_merge.py tests/test_train_e2e.py tests/test_train_order_only_deps_47.py tests/test_train_invariants.py -q`

## Execution Policy

- work-unit defaults: work-unit=`lane_execute`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`
- SL-4: executor=`codex`, effort=`high`, work-unit=`phase_reducer`, unsupported=`inherit_default`, inherit-default=`true`, reason=`single-writer coordinator integration and documentation synthesis`
- SL-5: executor=`codex`, effort=`high`, work-unit=`phase_verify`, unsupported=`inherit_default`, inherit-default=`true`, reason=`whole-phase verification reducer`

## Execution Notes

- SL-0 and SL-1 may run in one producer wave only when the scheduler assigns distinct worktrees and machine-verifies their owned files are disjoint. SL-2 follows SL-1; SL-3 follows SL-1 and SL-2. SL-4 and SL-5 are reducers and are excluded from writer waves.
- The complete phase-owned write set is the union of SL-0 through SL-4 `Owned files`. No lane may widen scope to `publishing.py`, `convergence/broker/**`, provider adapters, dependency manifests, lockfiles, migrations, snapshots, generated artifacts, environment examples, release manifests, or fleet pins.
- `train_runner.py`, `train_ledger.py`, `cli.py`, `convergence/__init__.py`, the legacy train regression files, and operator docs are single-writer surfaces owned only by SL-4. Producer lanes must use structural protocols and injected seams until that reducer lands.
- Current provider classifications remain fail-closed as committed by BROKER. INTEG wires the complete broker boundary but must not promote a `human-executed` or unsupported provider/verb pair merely because an adapter or CLI command exists.
- Legacy train-ledger reads may remain a compatibility projection, but they cannot authorize or supersede a convergence event-log/live-authority decision. Canonical `.phase-loop/` is runner state only and is not the coordinator event log; legacy `.codex/phase-loop/` is never authoritative.
- Verification artifacts must be runner-produced and digest-bound. A path, command exit, prior review, or stale digest alone is not verification evidence, and proxy evidence requires a roadmap amendment before downstream reliance.
- Documentation impact is required: both `docs/phase-loop/convergence-runtime.md` and `docs/phase-loop/cross-repo-train-authoring.md` change because the operator-visible `run-train` action order, resume authority, concurrency, broker, and conflict behavior change; README, changelog, release notes, and release pins remain unchanged until RELEASE.
- Closeout must list `IF-0-INTEG-1` in `produced_if_gates`. Passing tests without the gate, with a pending/ambiguous provider attempt, stale verification/approval, unsupported version, direct coordinator mutation path, or incomplete downstream refresh is not complete.
- Policy precedence is CLI/operator override, this plan, roadmap policy, `Dispatch Hints`, then registry defaults. Silent model or effort downgrade is forbidden; declared `inherit_default` is the only default inheritance here.

## Spec Closeout Plan

- schema: `spec_delta_closeout.v1`
- decision: `canonical_spec_update`
- target surfaces: `phase-loop-runtime/src/phase_loop_runtime/convergence/dispatch.py`, `phase-loop-runtime/src/phase_loop_runtime/convergence/fencing.py`, `phase-loop-runtime/src/phase_loop_runtime/convergence/reconcile.py`, `phase-loop-runtime/src/phase_loop_runtime/convergence/refresh.py`, `phase-loop-runtime/src/phase_loop_runtime/convergence/__init__.py`, `phase-loop-runtime/src/phase_loop_runtime/train_runner.py`, `phase-loop-runtime/src/phase_loop_runtime/train_ledger.py`, `phase-loop-runtime/src/phase_loop_runtime/cli.py`, `phase-loop-runtime/tests/test_convergence_*.py`, `phase-loop-runtime/tests/test_train_*.py`, `phase-loop-runtime/tests/test_cli_train_status_45.py`, `docs/phase-loop/convergence-runtime.md`, `docs/phase-loop/cross-repo-train-authoring.md`
- evidence paths: `plans/phase-plan-vergence-v1-INTEG.md`, `phase-loop-runtime/tests/test_convergence_dispatch.py`, `phase-loop-runtime/tests/test_convergence_fencing.py`, `phase-loop-runtime/tests/test_convergence_invalidation.py`, `phase-loop-runtime/tests/test_convergence_refresh.py`, `phase-loop-runtime/tests/test_convergence_train_integration.py`, `phase-loop-runtime/tests/test_convergence_cli_integration.py`, `docs/phase-loop/convergence-runtime.md`, `docs/phase-loop/cross-repo-train-authoring.md`
- redaction posture: `metadata_only`
- downstream handling: `none`

## Verification

```bash
PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-convergence-v1.md
python skills-src/claude/claude-plan-phase/scripts/validate_plan_doc.py plans/phase-plan-vergence-v1-INTEG.md
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_dispatch.py tests/test_convergence_fencing.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_reconcile.py tests/test_convergence_invalidation.py tests/test_convergence_refresh.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_train_integration.py tests/test_convergence_cli_integration.py tests/test_train_runner.py tests/test_train_merge.py tests/test_train_e2e.py tests/test_train_order_only_deps_47.py tests/test_train_invariants.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_event_log.py tests/test_convergence_runtime_imports.py tests/test_convergence_event_contracts.py tests/test_convergence_provider_contracts.py tests/test_convergence_coordination_contracts.py tests/test_convergence_broker_admission.py tests/test_convergence_broker_evidence.py tests/test_convergence_broker_verbs.py tests/test_convergence_broker_credsep.py tests/test_convergence_broker_api.py tests/test_publishing.py tests/test_cli_train_status_45.py tests/test_train_roadmap.py -q
```

automation:
  suite_command: cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_dispatch.py tests/test_convergence_fencing.py tests/test_convergence_invalidation.py tests/test_convergence_refresh.py tests/test_convergence_train_integration.py tests/test_convergence_cli_integration.py tests/test_convergence_event_log.py tests/test_convergence_reconcile.py tests/test_convergence_runtime_imports.py tests/test_convergence_event_contracts.py tests/test_convergence_provider_contracts.py tests/test_convergence_coordination_contracts.py tests/test_convergence_broker_admission.py tests/test_convergence_broker_evidence.py tests/test_convergence_broker_verbs.py tests/test_convergence_broker_credsep.py tests/test_convergence_broker_api.py tests/test_publishing.py tests/test_cli_train_status_45.py tests/test_train_roadmap.py tests/test_train_runner.py tests/test_train_merge.py tests/test_train_e2e.py tests/test_train_order_only_deps_47.py tests/test_train_invariants.py -q

## Acceptance Criteria

- [ ] `phase-loop-runtime/tests/test_convergence_dispatch.py` proves DAG-ready nodes overlap only after a persisted IF-0-FREEZE-6 allow decision, while same-repo, unsafe/unknown, topological-merge, and release-publication work serializes under repository locks.
- [ ] `phase-loop-runtime/tests/test_convergence_fencing.py` proves monotonic epochs, stale-worker rejection, exact seven-field admission requests, deterministic idempotent replay, and approval digests bound to roadmap, code, base, dependency, verification-plan, and verification-artifact identities.
- [ ] `phase-loop-runtime/tests/test_convergence_reconcile.py` and `phase-loop-runtime/tests/test_convergence_invalidation.py` prove reconcile-before-every-action, supported-version rejection, all five normative invalidation triggers, artifact-digest validation, and fail-closed pending/ambiguous authority.
- [ ] `phase-loop-runtime/tests/test_convergence_refresh.py` proves exact merged-SHA downstream refresh, typed conflict handling, stale review/verification invalidation, bound re-verification, broker-only republish, and idempotent crash resume.
- [ ] `phase-loop-runtime/tests/test_convergence_train_integration.py` proves `run_train` persists intent before dispatch and outcome after dispatch, reconciles against live authority before every action, uses the broker for every external mutation, admits safe independent nodes concurrently, serializes merges, and resumes without a transcript or double action.
- [ ] `phase-loop-runtime/tests/test_convergence_cli_integration.py` and `phase-loop-runtime/tests/test_cli_train_status_45.py` prove the phase-loop CLI entrypoint creates/uses the coordinator event-log contract, fails closed when broker or exact-state evidence is unavailable, preserves the autonomous `drafts_open` stop, and renders transcript-free status read-only.
- [ ] `phase-loop-runtime/tests/test_train_runner.py`, `phase-loop-runtime/tests/test_train_merge.py`, `phase-loop-runtime/tests/test_train_e2e.py`, `phase-loop-runtime/tests/test_train_order_only_deps_47.py`, and `phase-loop-runtime/tests/test_train_invariants.py` keep zero-PR preflight, draft-only autonomy, order-only dependencies, exact merged-SHA re-verification, forward-only merge, crash recovery, and INV-1 through INV-6 green.
- [ ] `docs/phase-loop/convergence-runtime.md` and `docs/phase-loop/cross-repo-train-authoring.md` document event-log/live-authority precedence, supported versions, lock/isolation decisions, attempt/fence/approval binding, broker-only mutation, downstream refresh/conflict recovery, verification artifacts, autonomous/governed boundaries, and metadata-only diagnostics.
- [ ] `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_dispatch.py tests/test_convergence_fencing.py tests/test_convergence_invalidation.py tests/test_convergence_refresh.py tests/test_convergence_train_integration.py tests/test_convergence_cli_integration.py tests/test_convergence_event_log.py tests/test_convergence_reconcile.py tests/test_convergence_runtime_imports.py tests/test_convergence_event_contracts.py tests/test_convergence_provider_contracts.py tests/test_convergence_coordination_contracts.py tests/test_convergence_broker_admission.py tests/test_convergence_broker_evidence.py tests/test_convergence_broker_verbs.py tests/test_convergence_broker_credsep.py tests/test_convergence_broker_api.py tests/test_publishing.py tests/test_cli_train_status_45.py tests/test_train_roadmap.py tests/test_train_runner.py tests/test_train_merge.py tests/test_train_e2e.py tests/test_train_order_only_deps_47.py tests/test_train_invariants.py -q` passes and closeout lists `IF-0-INTEG-1`.
