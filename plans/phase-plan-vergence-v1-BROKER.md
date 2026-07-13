---
phase_loop_plan_version: 1
phase: BROKER
roadmap: specs/phase-plans-convergence-v1.md
roadmap_sha256: f98b53d9a8be483cc4278e35ce26e6cf52b3556c223b1418eb14e2a08d522112
---

# BROKER: Credential Broker Epoch

## Context

BROKER implements the sole credential-capable side-effect epoch against the seven contracts completed by FREEZE at commit `e67190e21ad5f534acb88dff9cbd1a30df837c4d`. It provides linearizable, policy-checked admission; durable idempotency and terminal evidence; credential-separated provider adapters; and broker-routed `publish`, `merge`, `release`, `package`, and `publish_committed_branch` operations. Coordinator DAG wiring remains INTEG-owned.

The committed FREEZE provider matrix currently classifies every discovered GitHub verb/provider pair as `human-executed`. That is an intentional fail-closed runtime posture: BROKER must build and test the complete API, but its default classification map must reject every current GitHub mutation before the provider linearization point. Tests may inject an explicit `supported` fixture contract to exercise terminal effect/no-effect behavior; production code must not infer support from adapter availability.

The parked `feat/runtrain-prebuilt-publish` reference at commit `8df828da06e1563baa344ec85f64859c14d8b0e4` supplies the test-green no-recommit/by-name-push behavior to re-home. Only its publication primitive is consumed here: `train_runner.py`, train-roadmap parsing, CLI wiring, and prebuilt coordinator mode remain outside BROKER. Canonical runner state is `.phase-loop/`; the active BROKER launch plus live Git topology supersede stale repo/branch fields in the copied state snapshot, and legacy `.codex/phase-loop/` state has no authority.

## Interface Freeze Gates

- [ ] IF-0-BROKER-1 — `phase_loop_runtime.convergence.broker` exports `LinearizableAdmissionStore`, `BrokerAdmissionPolicy`, `AdmissionRecord`, `BrokerEvidenceStore`, `EvidenceRecord`, `BrokerClient`, `BrokerService`, `BrokerExecutionResult`, `BrokerProviderAdapter`, `BrokerEnvironmentBoundary`, `GitHubBrokerAdapter`, and `publish_committed_branch_idempotency_key`. `BrokerClient.execute(BrokerRequest) -> BrokerExecutionResult` is the only mutation boundary: admission assigns one durable total order per epoch under policy and fencing checks; evidence is replayable and keyed by idempotency key; only `ProviderCompletionClassification.SUPPORTED` may reach an adapter; `provider_call_in_flight` exits only through `effect_terminal_observed`, `no_effect_terminal_proven`, or permanently blocking `outcome_ambiguous_blocked`; coordinator/worker environments contain no mutation credentials; and `publish_committed_branch` re-verifies `(repo, branch, head_sha)`, pushes `refs/heads/<branch>` by name without force, re-asserts protected-branch policy, creates the requested draft PR without committing, and returns `PublishCommittedBranchResult` plus `BrokerTerminalEvidence`.

## Lane Index & Dependencies

SL-0 — Linearizable admission and epoch ordering
  Depends on: (none)
  Blocks: SL-4
  Parallel-safe: yes
SL-1 — Durable terminal evidence and idempotency
  Depends on: (none)
  Blocks: SL-4
  Parallel-safe: yes
SL-2 — Broker verbs and publishing route
  Depends on: (none)
  Blocks: SL-4
  Parallel-safe: yes
SL-3 — Credential separation and GitHub adapter
  Depends on: SL-2
  Blocks: SL-4
  Parallel-safe: yes
SL-4 — Broker package, documentation, and integration reducer
  Depends on: SL-0, SL-1, SL-2, SL-3
  Blocks: SL-5
  Parallel-safe: no
SL-5 — Documentation and whole-phase verification reducer
  Depends on: SL-0, SL-1, SL-2, SL-3, SL-4
  Blocks: (none)
  Parallel-safe: no

## Lanes

### SL-0 — Linearizable Admission And Epoch Ordering

- **Scope**: Implement a crash-replayable admission store that evaluates broker policy and epoch/fencing constraints atomically before assigning one durable total order.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/convergence/broker/admission.py`, `phase-loop-runtime/tests/test_convergence_broker_admission.py`
- **Interfaces provided**: `LinearizableAdmissionStore`, `BrokerAdmissionPolicy`, `AdmissionRecord`
- **Interfaces consumed**: `AdmissionRequest` (pre-existing), `BrokerRequest` (pre-existing), `IF-0-FREEZE-4` (pre-existing), `IF-0-FREEZE-7` (pre-existing)
- **Parallel-safe**: yes; this lane owns only the admission module and its focused test and implements against the plan-frozen structural backend contracts.
- **Tasks**:
  - test: Race independent store instances against one state root and assert every admitted request receives a unique monotonically increasing sequence in one epoch, records are append-only/replayable, and no partially written record becomes admissible after restart.
  - test: Prove policy evaluation, lease epoch, fence token, approval digest, expected-version predicate, authority scope, and idempotency binding are checked inside the same lock/transaction that appends the admission record.
  - test: Prove an identical idempotency replay returns the original record without a second admission, conflicting key reuse is rejected, stale epochs cannot admit, and the injected durable epoch-block predicate prevents all later privileged admissions.
  - impl: Add metadata-only `AdmissionRecord` persistence with an OS-backed cross-process lock and deterministic replay; never persist credential values, provider payloads, or local environment values.
  - impl: Define `BrokerAdmissionPolicy` as the sole policy callback evaluated under the admission lock, and fail closed on absent/invalid policy evidence instead of inventing an ordering decision.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_broker_admission.py -q`

### SL-1 — Durable Terminal Evidence And Idempotency

- **Scope**: Implement the append-only operation/evidence store that enforces the FREEZE terminal state machine and permanently fences an ambiguous epoch.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/convergence/broker/evidence.py`, `phase-loop-runtime/tests/test_convergence_broker_evidence.py`
- **Interfaces provided**: `BrokerEvidenceStore`, `EvidenceRecord`, `epoch_blocked`
- **Interfaces consumed**: `BrokerRequest` (pre-existing), `BrokerTerminalEvidence` (pre-existing), `TerminalOutcomeState` (pre-existing), `validate_terminal_transition` (pre-existing), `IF-0-FREEZE-3` (pre-existing)
- **Parallel-safe**: yes; this lane owns only the evidence module and its focused test and consumes frozen FREEZE types.
- **Tasks**:
  - test: Record intent before provider dispatch, reload after a simulated crash, and prove replay reconstructs `provider_call_in_flight` without transcripts and never fabricates terminal evidence.
  - test: Prove in-flight operations accept only `effect_terminal_observed`, `no_effect_terminal_proven`, or `outcome_ambiguous_blocked`; timeout, cancellation, credential revocation, or human override cannot advance an unresolved operation.
  - test: Prove identical terminal evidence is idempotent, conflicting evidence for one idempotency key fails closed, evidence references are metadata-only, and `outcome_ambiguous_blocked` survives restart and permanently makes `epoch_blocked` true.
  - test: Prove `rejected_before_start` is recordable only with explicit pre-linearization proof and cannot be used after a provider call was accepted.
  - impl: Add an append-only, lock-protected `BrokerEvidenceStore` with deterministic projection, explicit in-flight reconciliation, and no deletion/reset path for an ambiguous block.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_broker_evidence.py -q`

### SL-2 — Broker Verbs And Publishing Route

- **Scope**: Implement the provider-agnostic broker service and replace `publishing.py` mutation calls with the typed broker-client boundary, including the parked prebuilt no-recommit path.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/convergence/broker/verbs.py`, `phase-loop-runtime/src/phase_loop_runtime/publishing.py`, `phase-loop-runtime/tests/test_convergence_broker_verbs.py`, `phase-loop-runtime/tests/test_publishing.py`
- **Interfaces provided**: `BrokerClient`, `BrokerService`, `BrokerExecutionResult`, `BrokerProviderAdapter`, `publish_committed_branch_idempotency_key`, broker-routed `publish_from_worktree`
- **Interfaces consumed**: `BrokerVerb` (pre-existing), `BrokerRequest` (pre-existing), `BrokerTerminalEvidence` (pre-existing), `PublishCommittedBranchResult` (pre-existing), `PROVIDER_COMPLETION_CLASSIFICATIONS` (pre-existing), `ProviderCompletionClassification` (pre-existing), `TerminalOutcomeState` (pre-existing)
- **Parallel-safe**: yes; the service defines structural admission/evidence/provider protocols locally, tests them with fakes, and shares no writable file with the other producer lanes.
- **Tasks**:
  - test: Parameterize all five `BrokerVerb` values and prove unclassified, `human-executed`, and `unsupported` pairs return a pre-linearization blocked result plus terminal no-effect evidence without invoking admission-side provider mutation.
  - test: Inject explicit `supported` fixture contracts and fake structural backends to prove `BrokerService.execute` performs policy admission, intent persistence, one provider invocation, terminal observation, and evidence persistence in that order.
  - test: Prove provider acceptance followed by missing/invalid terminal proof records `outcome_ambiguous_blocked` and prevents every subsequent verb in the epoch; adapter exceptions before and after the linearization point remain distinguishable.
  - test: Prove `publish_committed_branch_idempotency_key(repo, branch, head_sha)` binds the canonical triple, duplicate triples never push/open twice, a key reused for a different triple is rejected, and the returned branch/head/PR result is bound to terminal evidence.
  - test: Port the parked reference tests proving `publish_from_worktree(..., prebuilt=True)` does not stage or commit and preserves the existing HEAD; prove both normal post-commit and prebuilt paths construct a `BrokerRequest` and never call `git push` or `gh pr create` from `publishing.py`.
  - impl: Add the provider-agnostic `BrokerService` and `BrokerClient` protocol; enforce the committed provider classification before adapter invocation and route every terminal transition through the injected evidence backend.
  - impl: Refactor `publish_from_worktree` so local staging/audit/commit preparation remains local but all push/PR mutation is delegated as `BrokerVerb.PUBLISH_COMMITTED_BRANCH`; port the no-upstream prebuilt behavior without importing `train_runner.py` changes.
  - impl: Preserve the existing publisher call shape where it maps to the frozen request. Because IF-0-FREEZE-4 does not carry `pr_title`, a non-null custom title must fail closed before commit/admission rather than be silently discarded or smuggled into another field.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_broker_verbs.py tests/test_publishing.py -q`

### SL-3 — Credential Separation And GitHub Adapter

- **Scope**: Establish the process-environment boundary and the credential-holding GitHub adapter that rechecks exact branch/head and non-force policy inside the broker epoch.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/convergence/broker/credsep.py`, `phase-loop-runtime/tests/test_convergence_broker_credsep.py`
- **Interfaces provided**: `BrokerEnvironmentBoundary`, `strip_mutation_credentials`, `GitHubBrokerAdapter`, `build_non_force_branch_ref`
- **Interfaces consumed**: `BrokerProviderAdapter` (plan-frozen structural protocol), `BrokerRequest` (pre-existing), `PublishCommittedBranchResult` (pre-existing), `IF-0-FREEZE-4` (pre-existing)
- **Parallel-safe**: yes; the lane implements the plan-frozen structural adapter without importing another producer lane and owns only its module and test.
- **Tasks**:
  - test: Derive the mutation-credential key inventory from the repository's provider adapters/config, partition a sentinel parent environment, and prove broker subprocesses retain required keys while coordinator/worker subprocesses receive none of them and ordinary non-secret variables remain intact.
  - test: Prove environment objects, exceptions, logs, and evidence contain credential key names/presence metadata at most—never credential values—and that no broker credential is written to disk.
  - test: Prove `GitHubBrokerAdapter` re-resolves repository identity, current branch, and HEAD inside the broker; rejects detached/protected/wrong-branch/wrong-head requests; and does not trust coordinator preflight assertions.
  - test: Prove `publish_committed_branch` builds only a by-name `refs/heads/<branch>` non-force push, never includes `--force`/`--force-with-lease`, treats a non-fast-forward rejection as terminal no-effect only when the provider contract proves it, and otherwise returns ambiguity.
  - test: Prove the adapter opens the requested draft posture without re-commit, returns `PublishCommittedBranchResult` from observed provider state rather than command stdout, and exposes all mutation seams to focused tests without live network calls.
  - impl: Add `BrokerEnvironmentBoundary` and `strip_mutation_credentials` for broker versus coordinator/worker process launch, with a closed mutation-key inventory and no secret-value serialization.
  - impl: Add the credential-holding `GitHubBrokerAdapter` behind the structural provider protocol, preserving by-name/non-force push and protected-branch/head re-assertion from the parked reference.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_broker_credsep.py -q`

### SL-4 — Broker Package, Documentation, And Integration Reducer

- **Scope**: Publish the broker package surface, compose all producer interfaces in metadata-only integration tests, and document the fail-closed runtime contract.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/convergence/broker/__init__.py`, `phase-loop-runtime/src/phase_loop_runtime/convergence/__init__.py`, `phase-loop-runtime/tests/test_convergence_broker_api.py`, `docs/phase-loop/convergence-contracts.md`
- **Interfaces provided**: `IF-0-BROKER-1`, `phase_loop_runtime.convergence.broker` public import surface, `BROKER integration evidence`
- **Interfaces consumed**: `LinearizableAdmissionStore`, `BrokerAdmissionPolicy`, `AdmissionRecord`, `BrokerEvidenceStore`, `EvidenceRecord`, `BrokerClient`, `BrokerService`, `BrokerExecutionResult`, `BrokerProviderAdapter`, `BrokerEnvironmentBoundary`, `GitHubBrokerAdapter`, `publish_committed_branch_idempotency_key`
- **Parallel-safe**: no; this synthesized writer runs only after SL-0 through SL-3 land and owns every shared package/docs surface.
- **Tasks**:
  - test: Assert the `phase_loop_runtime.convergence.broker` public symbols and call signatures match IF-0-BROKER-1 exactly and remain importable from the parent convergence package.
  - test: Compose the real admission and evidence stores with a supported fake adapter, exercise one terminal success, one proven no-effect, one denied current GitHub classification, one idempotent replay, and one durable ambiguous block across process restart.
  - test: Spawn a broker-role fixture and coordinator/worker fixtures through `BrokerEnvironmentBoundary` and prove only the broker can reach the mutation adapter; assert all persisted/logged artifacts stay metadata-only.
  - impl: Add package exports only after all producer modules exist; do not add coordinator, CLI, `train_runner.py`, or RUNTIME adapter wiring.
  - impl: Extend `docs/phase-loop/convergence-contracts.md` with the broker API, admission/evidence order, credential boundary, current provider-classification posture, publish-committed behavior, permanent ambiguity rule, and INTEG handoff.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_broker_admission.py tests/test_convergence_broker_evidence.py tests/test_convergence_broker_verbs.py tests/test_convergence_broker_credsep.py tests/test_convergence_broker_api.py tests/test_publishing.py -q`

### SL-5 — Documentation And Whole-Phase Verification Reducer

- **Scope**: Run the complete BROKER proof set, confirm the documentation/spec decision, and emit closeout evidence for IF-0-BROKER-1.
- **Owned files**: none
- **Interfaces provided**: `BROKER verification evidence`
- **Interfaces consumed**: `IF-0-BROKER-1`, `BROKER integration evidence`
- **Parallel-safe**: no; this read-only reducer runs after every writer and synthesized artifact.
- **Tasks**:
  - test: Run the roadmap/plan validators, all focused BROKER/FREEZE contract tests, publisher and train regressions, and `test_train_invariants.py` INV-1 through INV-6.
  - impl: Do not repair files here; route failures to the sole owning lane and rerun the complete reducer after the owner lands a fix.
  - verify: `PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-convergence-v1.md && python skills-src/claude/claude-plan-phase/scripts/validate_plan_doc.py plans/phase-plan-vergence-v1-BROKER.md && cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_broker_admission.py tests/test_convergence_broker_evidence.py tests/test_convergence_broker_verbs.py tests/test_convergence_broker_credsep.py tests/test_convergence_broker_api.py tests/test_convergence_coordination_contracts.py tests/test_convergence_provider_contracts.py tests/test_publishing.py tests/test_train_runner.py tests/test_train_invariants.py -q`

## Execution Policy

- work-unit defaults: work-unit=`lane_execute`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`
- SL-4: executor=`codex`, effort=`high`, work-unit=`phase_reducer`, unsupported=`inherit_default`, inherit-default=`true`, reason=`cross-lane package and documentation synthesis`
- SL-5: executor=`codex`, effort=`high`, work-unit=`phase_verify`, unsupported=`inherit_default`, inherit-default=`true`, reason=`whole-phase verification reducer`

## Execution Notes

- SL-0 through SL-3 may run in one writer wave only with machine-verified disjoint ownership, the exact IF-0-BROKER-1 structural protocols above, and scheduler-owned worktree assignments. SL-4 and SL-5 are reducers and are excluded from writer waves.
- The complete phase-owned write set is the union of SL-0 through SL-4. There are no dependency, lockfile, migration, generated-artifact, snapshot, or environment-example changes in this phase.
- `publishing.py` may prepare committed state but must have no direct push/PR mutation path after this phase. `train_runner.py`, `cli.py`, release dispatch, merge wiring, and coordinator environment launch remain INTEG-owned and are not widened into BROKER.
- Current GitHub classifications remain `human-executed` unless authoritative completion evidence changes the committed FREEZE matrix in a separately reviewed amendment. Adapter presence, a successful command exit, or a timeout is not sufficient to mark a pair `supported`.
- Public-surface decision: update only `docs/phase-loop/convergence-contracts.md`; no doc change is required for README, CHANGELOG, release notes, or fleet pins because those public release surfaces remain RELEASE-owned.
- Closeout must list `IF-0-BROKER-1` in `produced_if_gates`. Passing tests without that gate or with any unresolved in-flight provider operation is not complete.
- Policy precedence is CLI/operator override, this plan, roadmap policy, `Dispatch Hints`, then registry defaults. Silent model/effort downgrade is forbidden; declared `inherit_default` is the only default inheritance here.

## Spec Closeout Plan

- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `phase-loop-runtime/src/phase_loop_runtime/convergence/broker/*.py`, `phase-loop-runtime/src/phase_loop_runtime/convergence/__init__.py`, `phase-loop-runtime/src/phase_loop_runtime/publishing.py`, `phase-loop-runtime/tests/test_convergence_broker_*.py`, `phase-loop-runtime/tests/test_publishing.py`, `docs/phase-loop/convergence-contracts.md`
- evidence paths: `plans/phase-plan-vergence-v1-BROKER.md`, `docs/phase-loop/convergence-contracts.md`, `phase-loop-runtime/tests/test_convergence_broker_api.py`, `phase-loop-runtime/tests/test_convergence_broker_admission.py`, `phase-loop-runtime/tests/test_convergence_broker_evidence.py`, `phase-loop-runtime/tests/test_convergence_broker_verbs.py`, `phase-loop-runtime/tests/test_convergence_broker_credsep.py`
- redaction posture: `metadata_only`
- downstream handling: `none`

## Verification

```bash
PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-convergence-v1.md
python skills-src/claude/claude-plan-phase/scripts/validate_plan_doc.py plans/phase-plan-vergence-v1-BROKER.md
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_broker_admission.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_broker_evidence.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_broker_verbs.py tests/test_publishing.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_broker_credsep.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_broker_api.py tests/test_convergence_coordination_contracts.py tests/test_convergence_provider_contracts.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_train_runner.py tests/test_train_invariants.py -q
```

automation:
  suite_command: cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_broker_admission.py tests/test_convergence_broker_evidence.py tests/test_convergence_broker_verbs.py tests/test_convergence_broker_credsep.py tests/test_convergence_broker_api.py tests/test_convergence_coordination_contracts.py tests/test_convergence_provider_contracts.py tests/test_publishing.py tests/test_train_runner.py tests/test_train_invariants.py -q

## Acceptance Criteria

- [ ] `phase-loop-runtime/tests/test_convergence_broker_admission.py` proves cross-process requests receive one replayable total order per epoch and that policy, fencing, approval, expected-version, scope, idempotency, stale-epoch, and durable-block checks occur atomically before admission.
- [ ] `phase-loop-runtime/tests/test_convergence_broker_evidence.py` proves intent-before-dispatch/outcome-after-dispatch persistence, exact terminal transitions, idempotent replay, crash recovery, and a restart-persistent `outcome_ambiguous_blocked` with no timeout or override escape.
- [ ] `phase-loop-runtime/tests/test_convergence_broker_verbs.py` proves all five broker verbs refuse unclassified/`human-executed`/`unsupported` provider pairs before mutation and return bound terminal evidence for explicitly `supported` fixtures.
- [ ] `phase-loop-runtime/tests/test_publishing.py` proves normal and prebuilt publication delegate through `BrokerClient`, prebuilt publication preserves HEAD without staging/commit, and `publishing.py` never invokes `git push` or `gh pr create`.
- [ ] `phase-loop-runtime/tests/test_convergence_broker_credsep.py` proves only the broker-role process receives mutation credentials and that exact-head, protected-branch, by-name non-force push, draft, and no-recommit checks are re-asserted inside the adapter.
- [ ] `phase-loop-runtime/tests/test_convergence_broker_api.py` proves the exact IF-0-BROKER-1 import surface and an integrated terminal-success/no-effect/denial/idempotent-replay/ambiguous-restart sequence with metadata-only evidence.
- [ ] `docs/phase-loop/convergence-contracts.md` documents admission/evidence ordering, credential separation, the current fail-closed provider matrix, `publish_committed_branch`, permanent ambiguity, and the INTEG ownership boundary.
- [ ] `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_broker_admission.py tests/test_convergence_broker_evidence.py tests/test_convergence_broker_verbs.py tests/test_convergence_broker_credsep.py tests/test_convergence_broker_api.py tests/test_convergence_coordination_contracts.py tests/test_convergence_provider_contracts.py tests/test_publishing.py tests/test_train_runner.py tests/test_train_invariants.py -q` passes.
