---
phase_loop_plan_version: 1
phase: FAULTS
roadmap: specs/phase-plans-convergence-v1.md
roadmap_sha256: f98b53d9a8be483cc4278e35ce26e6cf52b3556c223b1418eb14e2a08d522112
---

# FAULTS: Adversarial Fault Suite

## Context

FAULTS is the hard pre-PILOT certification phase for the convergence coordinator. It converts the metadata-only fault fixtures preserved by FREEZE into executable adversarial proofs over the RUNTIME, BROKER, and INTEG contracts. The phase adds no coordinator feature: it authors deterministic fault tests first, permits only the smallest repairs required to satisfy already-frozen contracts, preserves the existing train invariants, and withholds IF-0-FAULTS-1 unless the complete suite passes with runner-bound verification evidence.

The live branch is `phase/conv-integ` at `ae8b1d8`. The roadmap is tracked and clean at the required digest. Canonical `.phase-loop/state.json` records INTEG complete and FAULTS unplanned; its `current_phase` field still names INTEG, but the phase map, the active canonical FAULTS planning launch, live HEAD, and clean phase-owned topology establish FAULTS as the planning target. That stale summary field is reconciled rather than treated as a blocker. Legacy `.codex/phase-loop/` state has no authority.

The roadmap requires five fault-family lanes. This plan places a shared deterministic harness ahead of five disjoint test-contract writers. A single terminal repair reducer owns all production and shared regression surfaces, so fault writers cannot collide while proving the same coordinator. A documentation reducer records the hard-gate semantics after all findings and repairs, and a final read-only reducer produces the only verification evidence eligible to certify the phase.

## Interface Freeze Gates

- [ ] IF-0-FAULTS-1 — The FAULTS certification is a runner-bound verification artifact for the exact `automation.suite_command` in this plan, tied to the active roadmap digest and implementation HEAD, proving all five fault families fail closed: crash at every durable coordinator transition resumes transcript-free and idempotently; partition and stale-worker schedules never double-act; every delayed-provider-commit matrix row obeys the terminal-outcome contract without wall-clock escape; unsupported or mixed schema/transition/invalidation versions and stale base/roadmap/dependency/head/review/verification identities are rejected; and forged or malformed outside-agent outcomes, capability overclaims, stale/delayed seat writes, mixed-version envelopes, and out-of-bounds actions cannot authorize progress. PILOT and RELEASE may consume this gate only when the phase closeout records verification passed and lists `IF-0-FAULTS-1`; collection-only results, partial family passes, proxy evidence, or a closeout missing the gate do not certify the phase.

## Lane Index & Dependencies

SL-0 — Deterministic fault harness
  Depends on: (none)
  Blocks: SL-1, SL-2, SL-3, SL-4, SL-5
  Parallel-safe: no
SL-1 — Crash and transcript-free resume contract
  Depends on: SL-0
  Blocks: SL-6
  Parallel-safe: yes
SL-2 — Partition and stale-worker contract
  Depends on: SL-0
  Blocks: SL-6
  Parallel-safe: yes
SL-3 — Delayed provider commit contract
  Depends on: SL-0
  Blocks: SL-6
  Parallel-safe: yes
SL-4 — Mixed-version and exact-head contract
  Depends on: SL-0
  Blocks: SL-6
  Parallel-safe: yes
SL-5 — Outside-agent adversarial contract
  Depends on: SL-0
  Blocks: SL-6
  Parallel-safe: yes
SL-6 — Minimal fault repair reducer
  Depends on: SL-1, SL-2, SL-3, SL-4, SL-5
  Blocks: SL-7, SL-8
  Parallel-safe: no
SL-7 — Documentation and certification sweep
  Depends on: SL-0, SL-1, SL-2, SL-3, SL-4, SL-5, SL-6
  Blocks: SL-8
  Parallel-safe: no
SL-8 — Whole-phase verification reducer
  Depends on: SL-0, SL-1, SL-2, SL-3, SL-4, SL-5, SL-6, SL-7
  Blocks: (none)
  Parallel-safe: no

## Lanes

### SL-0 — Deterministic Fault Harness

- **Scope**: Build metadata-only test controls that replay the preserved FREEZE fixture cases with deterministic killpoints, partitions, provider observations, epochs, clocks, exact-state probes, and outside-agent submissions.
- **Owned files**: `phase-loop-runtime/tests/convergence/conftest.py`, `phase-loop-runtime/tests/convergence/test_fault_harness.py`
- **Interfaces provided**: `FaultInjectionHarness`, `FrozenFaultFixtureSelection`, `DeterministicFaultClock`
- **Interfaces consumed**: `phase-loop-runtime/tests/fixtures/convergence/freeze-metadata.json` (pre-existing), `CoordinatorEvent` (pre-existing), `AdmissionRequest` (pre-existing), `ConvergenceResultEnvelope` (pre-existing)
- **Parallel-safe**: no; this preamble is the sole shared test-harness writer and must land before the five fault-family files are dispatched.
- **Tasks**:
  - test: Prove the harness selects every roadmap-required FREEZE case, never reads transcripts or credentials, emits stable metadata-only observations, and can stop/restart at named durable transitions without sleeping or consulting wall-clock time.
  - impl: Add reusable pytest fixtures and fakes for event-log storage, killpoints, provider status/idempotency/horizon responses, branch and review identities, partitioned callbacks, monotonic epochs, and bounded outside-agent submissions.
  - impl: Keep the harness under `tests/`; do not add runtime fault-injection switches or mutate the preserved `freeze-metadata.json` provenance record.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/convergence/test_fault_harness.py tests/test_convergence_fixture_contracts.py -q`

### SL-1 — Crash And Transcript-Free Resume Contract

- **Scope**: Author the crash-transition matrix proving intent/outcome durability and exact-once, transcript-free resume across every coordinator state boundary.
- **Owned files**: `phase-loop-runtime/tests/convergence/test_faults_crash_resume.py`
- **Interfaces provided**: `CRASH-RESUME fault contract`
- **Interfaces consumed**: `FaultInjectionHarness`, `RecoveredTrainState` (pre-existing), `build_train_status` (pre-existing), `CoordinatorRuntime` (pre-existing)
- **Parallel-safe**: yes; this lane writes only its fault-family test file in a scheduler-assigned worktree.
- **Tasks**:
  - test: Parameterize kills immediately before and after each durable intent, dispatch, provider call, terminal outcome, verification, review, merge, release, and downstream-refresh transition; on restart reconstruct only from the coordinator event log and exact authority.
  - test: Prove a torn final record is ignored, earlier corruption fails loud, identical replay produces no second side effect, pending intent reconciles before action, and status reconstruction uses no transcript or repo-local `.phase-loop/` authority.
  - impl: Encode the crash matrix and exact call-count/order assertions using SL-0 fakes; production repairs are deliberately deferred to the sole SL-6 reducer.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest --collect-only tests/convergence/test_faults_crash_resume.py -q`

### SL-2 — Partition And Stale-Worker Contract

- **Scope**: Author adversarial schedules proving partitions, concurrent admissions, lease replacement, and delayed stale completions cannot double-dispatch or double-mutate.
- **Owned files**: `phase-loop-runtime/tests/convergence/test_faults_partition_stale.py`
- **Interfaces provided**: `PARTITION-STALE fault contract`
- **Interfaces consumed**: `FaultInjectionHarness`, `dispatch_ready_nodes` (pre-existing), `FencedAdmissionFactory` (pre-existing), `LinearizableAdmissionStore` (pre-existing)
- **Parallel-safe**: yes; this lane writes only its fault-family test file in a scheduler-assigned worktree.
- **Tasks**:
  - test: Partition coordinator-to-worker, coordinator-to-broker, broker-to-provider, and status-observation paths around the admission linearization point; prove uncertain work blocks and safe independent work alone may continue.
  - test: Race old and new lease epochs, duplicate idempotency keys, lock release, cancellation, and delayed worker outcomes; prove one total admission order, monotonic fencing, deterministic replay, and no second provider action.
  - impl: Encode deterministic interleavings and call-count assertions without sleeps, live network, credentials, or direct mutation; production repairs are deferred to SL-6.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest --collect-only tests/convergence/test_faults_partition_stale.py -q`

### SL-3 — Delayed Provider Commit Contract

- **Scope**: Author the complete v5 delayed-provider-commit matrix against the frozen provider classification and terminal-outcome state machine.
- **Owned files**: `phase-loop-runtime/tests/convergence/test_faults_delayed_commit.py`
- **Interfaces provided**: `DELAYED-COMMIT fault contract`
- **Interfaces consumed**: `FaultInjectionHarness`, `ProviderCompletionContract` (pre-existing), `TerminalOutcomeState` (pre-existing), `BrokerEvidenceStore` (pre-existing), `BrokerService` (pre-existing)
- **Parallel-safe**: yes; this lane writes only its fault-family test file in a scheduler-assigned worktree.
- **Tasks**:
  - test: Prove `outcome_ambiguous_blocked` survives restart and never times out, overrides, or revocation into progress; a later observed effect reconciles exactly once without reissuing the accepted request.
  - test: Prove terminal rejection evidence binds the provider operation identity before epoch advancement, and a guaranteed processing horizon permits no-effect progress only after horizon plus stabilization drain plus a stable expected-version predicate.
  - test: Prove a verb/provider lacking status observability or idempotency is classified unsupported or human-executed before dispatch and never reaches admission or the provider adapter.
  - impl: Encode every matrix row with the deterministic clock and provider fake; production repairs are deferred to SL-6.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest --collect-only tests/convergence/test_faults_delayed_commit.py -q`

### SL-4 — Mixed-Version And Exact-Head Contract

- **Scope**: Author fail-closed proofs for unsupported/mixed record versions and every stale exact-state or bound-verification identity.
- **Owned files**: `phase-loop-runtime/tests/convergence/test_faults_mixed_version_exact_head.py`
- **Interfaces provided**: `MIXED-VERSION-EXACT-HEAD fault contract`
- **Interfaces consumed**: `FaultInjectionHarness`, `SupportedConvergenceVersions` (pre-existing), `reconcile_before_action` (pre-existing), `refresh_downstream_after_merge` (pre-existing)
- **Parallel-safe**: yes; this lane writes only its fault-family test file in a scheduler-assigned worktree.
- **Tasks**:
  - test: Parameterize unsupported and mixed event-schema, transition-model, invalidation-model, and result-envelope versions; prove rejection occurs before dispatch or broker admission with no coercion.
  - test: Change base SHA, roadmap digest, dependency SHA, head SHA, PR/review identity, verification-plan digest, verification-artifact bytes/digest, and upstream merged SHA one at a time; prove stale approval and verification are cleared before action.
  - test: Prove exact merged-SHA refresh precedes downstream re-verification and republish, conflicts are typed, stale reviews are never reused, and idempotent restart cannot accept a draft or moving ref as merged authority.
  - impl: Encode the full version and identity matrix using exact-state probes from SL-0; production repairs are deferred to SL-6.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest --collect-only tests/convergence/test_faults_mixed_version_exact_head.py -q`

### SL-5 — Outside-Agent Adversarial Contract

- **Scope**: Author the six mandatory D2 outside-agent attacks and prove each fails closed before its output can authorize coordinator progress.
- **Owned files**: `phase-loop-runtime/tests/convergence/test_faults_outside_agent_adversarial.py`
- **Interfaces provided**: `OUTSIDE-AGENT-ADVERSARIAL fault contract`
- **Interfaces consumed**: `FaultInjectionHarness`, `AdapterExecutionRequest` (pre-existing), `run_outside_agent_adapter` (pre-existing), `ConvergenceResultEnvelope` (pre-existing)
- **Parallel-safe**: yes; this lane writes only its fault-family test file in a scheduler-assigned worktree.
- **Tasks**:
  - test: Inject forged completion evidence, malformed result envelopes, capability overclaim, stale/delayed seat writes, mixed-version envelopes, and action-outside-bounds; assert a typed blocked/failed result, no evidence promotion, no broker admission, and no state advance.
  - test: Prove attempt, epoch, fence, allowed action, declared capabilities, schema version, and evidence references bind to the active seat; late or mismatched output cannot satisfy a newer attempt.
  - impl: Encode attacks from the preserved fixture inventory using bounded local submissions only; production repairs are deferred to SL-6.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest --collect-only tests/convergence/test_faults_outside_agent_adversarial.py -q`

### SL-6 — Minimal Fault Repair Reducer

- **Scope**: Run all five authored fault contracts, apply only contract-preserving production repairs, extend the shared train invariant suite, and make every behavioral fault test green.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/train_runner.py`, `phase-loop-runtime/src/phase_loop_runtime/train_ledger.py`, `phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py`, `phase-loop-runtime/src/phase_loop_runtime/convergence/__init__.py`, `phase-loop-runtime/src/phase_loop_runtime/convergence/event_log.py`, `phase-loop-runtime/src/phase_loop_runtime/convergence/status.py`, `phase-loop-runtime/src/phase_loop_runtime/convergence/reconcile.py`, `phase-loop-runtime/src/phase_loop_runtime/convergence/dispatch.py`, `phase-loop-runtime/src/phase_loop_runtime/convergence/fencing.py`, `phase-loop-runtime/src/phase_loop_runtime/convergence/refresh.py`, `phase-loop-runtime/src/phase_loop_runtime/convergence/provider_contracts.py`, `phase-loop-runtime/src/phase_loop_runtime/convergence/adapters/__init__.py`, `phase-loop-runtime/src/phase_loop_runtime/convergence/adapters/base.py`, `phase-loop-runtime/src/phase_loop_runtime/convergence/adapters/outside_agent.py`, `phase-loop-runtime/src/phase_loop_runtime/convergence/broker/__init__.py`, `phase-loop-runtime/src/phase_loop_runtime/convergence/broker/admission.py`, `phase-loop-runtime/src/phase_loop_runtime/convergence/broker/evidence.py`, `phase-loop-runtime/src/phase_loop_runtime/convergence/broker/verbs.py`, `phase-loop-runtime/tests/test_train_invariants.py`
- **Interfaces provided**: `fault-suite contract repairs`, `INV-1 through INV-7 regression evidence`
- **Interfaces consumed**: `CRASH-RESUME fault contract`, `PARTITION-STALE fault contract`, `DELAYED-COMMIT fault contract`, `MIXED-VERSION-EXACT-HEAD fault contract`, `OUTSIDE-AGENT-ADVERSARIAL fault contract`, `IF-0-INTEG-1` (pre-existing)
- **Parallel-safe**: no; this is the sole production writer and integration reducer after every fault-family contract is frozen.
- **Tasks**:
  - test: Run all five fault files together first and preserve exact failing parameter IDs as the repair checklist; extend `test_train_invariants.py` only for cross-family exact-once, exact-head, transcript-free, and broker-only regressions not already asserted in the family files.
  - impl: Repair only existing convergence semantics: durable intent/outcome replay, supported-version rejection, exact-state invalidation, monotonic fencing/admission, terminal provider evidence, bounded adapter validation, stale seat rejection, and run-train ordering/idempotency.
  - impl: Do not add production fault switches, new provider automation classifications, timeout/override escapes, direct coordinator mutation paths, new statuses, dependencies, migrations, generated files, environment shapes, snapshots, or public features; if a fault contract requires a frozen-interface change, stop for a roadmap amendment instead of widening this lane.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/convergence tests/test_train_invariants.py tests/test_convergence_event_log.py tests/test_convergence_reconcile.py tests/test_convergence_dispatch.py tests/test_convergence_fencing.py tests/test_convergence_refresh.py tests/test_convergence_broker_admission.py tests/test_convergence_broker_evidence.py tests/test_convergence_broker_verbs.py tests/test_convergence_adapters.py tests/test_convergence_seat_lifecycle.py tests/test_convergence_train_integration.py -q`

### SL-7 — Documentation And Certification Sweep

- **Scope**: Update the operator contract only after all fault families and repairs settle, documenting the certification command, evidence binding, hard gate, and fail-closed recovery posture without claiming PILOT or RELEASE readiness.
- **Owned files**: `docs/phase-loop/convergence-runtime.md`
- **Interfaces provided**: `FAULTS operator documentation`, `IF-0-FAULTS-1 certification definition`
- **Interfaces consumed**: `FaultInjectionHarness`, `CRASH-RESUME fault contract`, `PARTITION-STALE fault contract`, `DELAYED-COMMIT fault contract`, `MIXED-VERSION-EXACT-HEAD fault contract`, `OUTSIDE-AGENT-ADVERSARIAL fault contract`, `fault-suite contract repairs`, `INV-1 through INV-7 regression evidence`
- **Parallel-safe**: no; this synthesized documentation writer depends on every producer and must reflect the final passing behavior rather than anticipated results.
- **Tasks**:
  - test: Check every roadmap fault family, the exact suite command, runner-bound artifact requirement, gate name, recovery authority, and no-timeout/no-override rule is represented without secrets, transcript authority, or premature readiness claims.
  - impl: Add the FAULTS certification and troubleshooting section to the existing convergence runtime guide; state that only a passed runner closeout listing `IF-0-FAULTS-1` unlocks PILOT and that partial, stale, or proxy evidence does not.
  - verify: `rg -n "IF-0-FAULTS-1|tests/convergence|transcript-free|outcome_ambiguous_blocked|PILOT" docs/phase-loop/convergence-runtime.md && cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/convergence/test_fault_harness.py tests/convergence/test_faults_crash_resume.py tests/convergence/test_faults_delayed_commit.py -q`

### SL-8 — Whole-Phase Verification Reducer

- **Scope**: Run roadmap/plan validation plus the complete adversarial, convergence, CLI, train, broker, adapter, seat, and invariant regression surface and emit the sole closeout evidence for IF-0-FAULTS-1.
- **Owned files**: none
- **Interfaces provided**: `IF-0-FAULTS-1`
- **Interfaces consumed**: `CRASH-RESUME fault contract`, `PARTITION-STALE fault contract`, `DELAYED-COMMIT fault contract`, `MIXED-VERSION-EXACT-HEAD fault contract`, `OUTSIDE-AGENT-ADVERSARIAL fault contract`, `fault-suite contract repairs`, `INV-1 through INV-7 regression evidence`, `FAULTS operator documentation`, `IF-0-FAULTS-1 certification definition`
- **Parallel-safe**: no; this read-only reducer starts only after all writers and the documentation reducer complete.
- **Tasks**:
  - test: Run the roadmap and plan validators, then the exact `automation.suite_command`; require zero skipped fault-family rows unless the roadmap explicitly classifies the row unsupported from the outset.
  - impl: Do not edit files in this lane; route any failure to its sole owning lane, rerun the full reducer after repair, and withhold `IF-0-FAULTS-1` from closeout until the complete suite passes against the final HEAD.
  - verify: `PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-convergence-v1.md && python skills-src/claude/claude-plan-phase/scripts/validate_plan_doc.py plans/phase-plan-vergence-v1-FAULTS.md && cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/convergence tests/test_convergence_*.py tests/test_cli_train_status_45.py tests/test_train_roadmap.py tests/test_train_runner.py tests/test_train_merge.py tests/test_train_e2e.py tests/test_train_order_only_deps_47.py tests/test_train_invariants.py -q`

## Execution Policy

- work-unit defaults: work-unit=`lane_execute`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`
- SL-6: executor=`codex`, effort=`high`, work-unit=`phase_reducer`, unsupported=`inherit_default`, inherit-default=`true`, reason=`single-writer contract-preserving fault repair synthesis`
- SL-7: executor=`codex`, effort=`medium`, work-unit=`phase_reducer`, unsupported=`inherit_default`, inherit-default=`true`, reason=`terminal documentation and certification synthesis`
- SL-8: executor=`codex`, effort=`high`, work-unit=`phase_verify`, unsupported=`inherit_default`, inherit-default=`true`, reason=`whole-phase hard-gate verification`

## Execution Notes

- SL-1 through SL-5 may run in one writer wave only when the scheduler assigns separate worktrees and machine-verifies their literal owned files are disjoint. SL-6, SL-7, and SL-8 are reducers and are excluded from that wave.
- Collection-only verification in SL-1 through SL-5 validates importability and test-contract construction, not acceptance. SL-6 must execute every parameterized assertion after repairs, and SL-8 must rerun the whole phase from a single final HEAD.
- The complete phase-owned write set is the union of SL-0 through SL-7 `Owned files`. No lane may widen scope to the roadmap, preserved FREEZE fixture, conformance schemas, provider credentials, `.phase-loop/**`, legacy `.codex/phase-loop/**`, dependency manifests, lockfiles, migrations, environment examples, snapshots, generated artifacts, release files, or fleet pins.
- `train_runner.py`, `train_ledger.py`, `panel_invoker.py`, shared convergence exports, all production convergence modules, broker modules, and `test_train_invariants.py` are single-writer surfaces owned only by SL-6. The five fault-family lanes must express findings through their disjoint tests until the reducer lands.
- The preserved `freeze-metadata.json` is a metadata-only source fixture and remains read-only. Test artifacts use synthetic identifiers and local temporary paths; no transcript, raw external payload, secret, credential, provider mutation, or live network call is permitted.
- Determinism is part of the gate: delayed commits, horizons, drains, partitions, crashes, and stale workers use injected clocks/barriers/call logs. Sleeping until a timeout or accepting timing-sensitive success is a failed proof.
- Existing provider classifications remain fail-closed. FAULTS may verify supported, human-executed, unsupported, and unclassified behavior but may not promote a provider/verb pair or invent terminal evidence.
- The runner-produced verification artifact and phase closeout are the certification evidence. A failing or incomplete suite leaves the phase blocked/executed as accurate; it must not list `IF-0-FAULTS-1`, and downstream PILOT planning cannot treat test collection, an earlier SHA, or a prose summary as a substitute.
- Documentation impact is limited to `docs/phase-loop/convergence-runtime.md`; README, changelog, train authoring guide, release notes, package metadata, and public API docs remain unchanged because this phase adds tests and contract-preserving repairs only.
- Policy precedence is CLI/operator override, this plan, roadmap policy, `Dispatch Hints`, then registry defaults. Silent model or effort downgrade is forbidden; declared `inherit_default` is the only default inheritance here.

## Spec Closeout Plan

- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `phase-loop-runtime/tests/convergence/*.py`, `phase-loop-runtime/tests/test_train_invariants.py`, `phase-loop-runtime/src/phase_loop_runtime/train_runner.py`, `phase-loop-runtime/src/phase_loop_runtime/train_ledger.py`, `phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py`, `phase-loop-runtime/src/phase_loop_runtime/convergence/**/*.py`, `docs/phase-loop/convergence-runtime.md`
- evidence paths: `plans/phase-plan-vergence-v1-FAULTS.md`, `phase-loop-runtime/tests/fixtures/convergence/freeze-metadata.json`, `phase-loop-runtime/tests/convergence/*.py`, `phase-loop-runtime/tests/test_train_invariants.py`, `docs/phase-loop/convergence-runtime.md`
- redaction posture: `metadata_only`
- downstream handling: `none`

## Verification

```bash
PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-convergence-v1.md
python skills-src/claude/claude-plan-phase/scripts/validate_plan_doc.py plans/phase-plan-vergence-v1-FAULTS.md
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/convergence/test_fault_harness.py tests/test_convergence_fixture_contracts.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/convergence tests/test_train_invariants.py tests/test_convergence_event_log.py tests/test_convergence_reconcile.py tests/test_convergence_dispatch.py tests/test_convergence_fencing.py tests/test_convergence_refresh.py tests/test_convergence_broker_admission.py tests/test_convergence_broker_evidence.py tests/test_convergence_broker_verbs.py tests/test_convergence_adapters.py tests/test_convergence_seat_lifecycle.py tests/test_convergence_train_integration.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/convergence tests/test_convergence_*.py tests/test_cli_train_status_45.py tests/test_train_roadmap.py tests/test_train_runner.py tests/test_train_merge.py tests/test_train_e2e.py tests/test_train_order_only_deps_47.py tests/test_train_invariants.py -q
```

automation:
  suite_command: cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/convergence tests/test_convergence_*.py tests/test_cli_train_status_45.py tests/test_train_roadmap.py tests/test_train_runner.py tests/test_train_merge.py tests/test_train_e2e.py tests/test_train_order_only_deps_47.py tests/test_train_invariants.py -q

## Acceptance Criteria

- [ ] `phase-loop-runtime/tests/convergence/test_fault_harness.py` proves the preserved metadata-only fixture inventory is complete, deterministic, credential-free, transcript-independent, and capable of named transition, partition, clock, provider, identity, and outside-agent injections.
- [ ] `phase-loop-runtime/tests/convergence/test_faults_crash_resume.py` kills the coordinator before and after every durable transition and proves event-log-only status reconstruction, torn-tail tolerance, fail-loud earlier corruption, idempotent restart, and exact-once action.
- [ ] `phase-loop-runtime/tests/convergence/test_faults_partition_stale.py` proves every partition and stale-worker schedule maintains one total admission order, monotonic epochs/fences, scheduler-owned locks, deterministic replay, and zero duplicate provider effects.
- [ ] `phase-loop-runtime/tests/convergence/test_faults_delayed_commit.py` proves every v5 matrix row: permanent ambiguity, exactly-once late-effect reconciliation, operation-bound terminal rejection, horizon plus drain plus stable-version no-effect proof, and pre-dispatch refusal without status/idempotency.
- [ ] `phase-loop-runtime/tests/convergence/test_faults_mixed_version_exact_head.py` rejects unsupported/mixed versions and every stale base, roadmap, dependency, head, review, verification, approval, and upstream-merge identity before action.
- [ ] `phase-loop-runtime/tests/convergence/test_faults_outside_agent_adversarial.py` detects all six D2 attacks and proves none can promote evidence, admit a broker request, mutate external state, or advance the active attempt.
- [ ] `phase-loop-runtime/tests/test_train_invariants.py` keeps INV-1 through INV-7 green and adds only cross-family assertions needed to bind crash, fencing, exact-head, broker-only, and transcript-free behavior to the live coordinator entrypoint.
- [ ] `docs/phase-loop/convergence-runtime.md` names the exact certification command, all five fault families, `IF-0-FAULTS-1`, runner-bound evidence, canonical recovery authority, permanent ambiguous blocking, and the FAULTS-before-PILOT/RELEASE gate without claiming those downstream phases are ready.
- [ ] `PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-convergence-v1.md` and `python skills-src/claude/claude-plan-phase/scripts/validate_plan_doc.py plans/phase-plan-vergence-v1-FAULTS.md` pass against the pinned roadmap digest and final plan.
- [ ] `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/convergence tests/test_convergence_*.py tests/test_cli_train_status_45.py tests/test_train_roadmap.py tests/test_train_runner.py tests/test_train_merge.py tests/test_train_e2e.py tests/test_train_order_only_deps_47.py tests/test_train_invariants.py -q` passes on the final HEAD, the runner binds its verification artifact to that HEAD and roadmap digest, and closeout lists `IF-0-FAULTS-1`.
