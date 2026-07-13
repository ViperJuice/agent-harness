---
phase_loop_plan_version: 1
phase: FREEZE
roadmap: specs/phase-plans-convergence-v1.md
roadmap_sha256: f98b53d9a8be483cc4278e35ce26e6cf52b3556c223b1418eb14e2a08d522112
---

# FREEZE: Freeze, Preserve, and Measure

## Context

FREEZE is the contract-only preamble for the convergence roadmap. It freezes the seven interfaces consumed by the parallel RUNTIME and BROKER phases, records the provider-completion classification that controls automation admission, and preserves metadata-only fault fixtures and baseline measurements before coordinator behavior changes.

The existing `train_ledger.py` ledger is pre-roadmap durable state. SL-0 must preserve its current reader/writer behavior while freezing an explicit versioned mapping into the new coordinator event schema; this handles discovered durable ledger state inside FREEZE rather than inventing a migration phase.

Canonical runner state under `.phase-loop/` is authoritative. Legacy `.codex/phase-loop/` files are compatibility artifacts only. The roadmap-referenced `.phase-loop/reviews/convergence-*.md` and `prebuilt-publish-slice-design.md` sources are absent from this worktree at planning time; execution must use the ratified tracked roadmap plus metadata-only live repository evidence, record unavailable sources as unavailable, and never fabricate provider guarantees or motivating-session measurements.

## Interface Freeze Gates

- [ ] IF-0-FREEZE-1 — `CoordinatorEvent`, `CoordinatorEventKind`, and the legacy-ledger normalization contract freeze all roadmap-required event fields, distinct intent/outcome records, and explicit `event_schema_version`, `transition_model_version`, and `invalidation_model_version`.
- [ ] IF-0-FREEZE-2 — `ConvergenceResultEnvelope` and `ConvergenceResultStatus` freeze one adapter-neutral envelope with exactly `completed`, `verified`, `blocked`, `needs_clarification`, `degraded`, and `failed`.
- [ ] IF-0-FREEZE-3 — `ProviderCompletionContract`, `ProviderCompletionClassification`, `ProviderAutomationDisposition`, `TerminalOutcomeState`, and `PROVIDER_COMPLETION_CLASSIFICATIONS` freeze the completion schema, transition model, and populated verb-by-provider matrix.
- [ ] IF-0-FREEZE-4 — `BrokerVerb`, `BrokerRequest`, `BrokerTerminalEvidence`, and `PublishCommittedBranchResult` freeze linearizable admission, credential isolation, and all broker verbs including `publish_committed_branch(repo, branch, head_sha, owned_paths, draft, pr_body) -> {branch, head_sha, pr_url}`.
- [ ] IF-0-FREEZE-5 — `AuthoritySource`, `InvalidationTrigger`, and `ReconciliationBinding` freeze the exact-state authority split and five normative invalidation values with version binding.
- [ ] IF-0-FREEZE-6 — `ResourceIsolationDecision` and `evaluate_resource_isolation` freeze the fail-closed concurrency predicate and mandatory serialization for same-repo mutation, topological merges, and release publication.
- [ ] IF-0-FREEZE-7 — `AdmissionRequest` freezes the one RUNTIME/BROKER fencing shape with exactly `attempt_id`, `lease_epoch`, `fence_token`, `approval_digest`, `expected_version_predicate`, `authority_domain_scope`, and `idempotency_key`.

## Lane Index & Dependencies

SL-0 — Event schema and result envelope
  Depends on: (none)
  Blocks: SL-3
  Parallel-safe: yes
SL-1 — Provider completion contracts and classification
  Depends on: (none)
  Blocks: SL-3
  Parallel-safe: yes
SL-2 — Broker, reconciliation, isolation, and fencing contracts
  Depends on: SL-1
  Blocks: SL-3
  Parallel-safe: yes
SL-3 — Documentation, fixtures, import surface, and evidence reducer
  Depends on: SL-0, SL-1, SL-2
  Blocks: SL-4
  Parallel-safe: no
SL-4 — Documentation and whole-phase verification reducer
  Depends on: SL-0, SL-1, SL-2, SL-3
  Blocks: (none)
  Parallel-safe: no

## Lanes

### SL-0 — Event Schema And Result Envelope

- **Scope**: Freeze IF-0-FREEZE-1 and IF-0-FREEZE-2 in the existing durable-ledger module without wiring new coordinator behavior.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/train_ledger.py`, `phase-loop-runtime/tests/test_convergence_event_contracts.py`
- **Interfaces provided**: `IF-0-FREEZE-1`, `IF-0-FREEZE-2`, `CoordinatorEvent`, `CoordinatorEventKind`, `ConvergenceResultEnvelope`, `ConvergenceResultStatus`, `normalize_legacy_ledger_record`
- **Interfaces consumed**: `LedgerRecord` (pre-existing), `append_record` (pre-existing), `read_ledger` (pre-existing)
- **Parallel-safe**: yes; no other lane writes the ledger module or its focused test.
- **Tasks**:
  - test: Enumerate every required event/version field, reject unknown status/kind values, prove intent and outcome are separate, and prove the six result statuses are exact.
  - test: Lock current `LedgerRecord` append/read behavior and specify deterministic `normalize_legacy_ledger_record` mapping so legacy records remain readable without silently acquiring false evidence.
  - impl: Add frozen dataclasses/enums and docstrings only; keep `append_record`, `read_ledger`, and all current runtime call sites behaviorally unchanged.
  - impl: Represent unavailable legacy fields explicitly as absent/unknown and never infer verification, merge, release, epoch, or provider outcome evidence.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_event_contracts.py tests/test_train_roadmap.py -q`

### SL-1 — Provider Completion Contracts And Classification

- **Scope**: Freeze IF-0-FREEZE-3 and commit a populated automation disposition for every currently automated verb-by-provider pair.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/convergence/provider_contracts.py`, `phase-loop-runtime/tests/test_convergence_provider_contracts.py`
- **Interfaces provided**: `IF-0-FREEZE-3`, `ProviderCompletionContract`, `ProviderCompletionClassification`, `ProviderAutomationDisposition`, `TerminalOutcomeState`, `PROVIDER_COMPLETION_CLASSIFICATIONS`, `validate_terminal_transition`
- **Interfaces consumed**: `publish_from_worktree` (pre-existing), `run_train` (pre-existing), `merge_train` (pre-existing), `release_dispatch_blocker` (pre-existing)
- **Parallel-safe**: yes; the lane owns one new schema module and focused test.
- **Tasks**:
  - test: Build the repository-derived verb/provider inventory first and fail when any automated pair is unclassified, duplicated, or lacks explicit `supported`, `human-executed`, or `unsupported` disposition.
  - test: Require `status_endpoint`, idempotency support/key semantics, terminal success evidence, terminal no-effect evidence plus non-late-commit guarantee, `guaranteed_processing_horizon`, `expected_version_predicate`, `revocation_affects_accepted`, and `stabilization_drain_interval`, using explicit `N/A` only where permitted.
  - test: Prove `provider_call_in_flight` leaves only through `effect_terminal_observed`, `no_effect_terminal_proven`, or `outcome_ambiguous_blocked`; permit `rejected_before_start` only with pre-linearization proof and forbid timeout/human-override progress from ambiguity.
  - impl: Populate `PROVIDER_COMPLETION_CLASSIFICATIONS` from inspected mutation call sites and authoritative provider evidence; classify unverifiable guarantees as `human-executed` or `unsupported` rather than guessing.
  - impl: Keep this module contract-only; BROKER owns enforcement and provider calls downstream.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_provider_contracts.py -q`

### SL-2 — Broker, Reconciliation, Isolation, And Fencing Contracts

- **Scope**: Freeze IF-0-FREEZE-4 through IF-0-FREEZE-7 as typed, behavior-neutral contracts shared by RUNTIME and BROKER.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/convergence/contracts.py`, `phase-loop-runtime/tests/test_convergence_coordination_contracts.py`
- **Interfaces provided**: `IF-0-FREEZE-4`, `IF-0-FREEZE-5`, `IF-0-FREEZE-6`, `IF-0-FREEZE-7`, `BrokerVerb`, `BrokerRequest`, `BrokerTerminalEvidence`, `PublishCommittedBranchResult`, `AuthoritySource`, `InvalidationTrigger`, `ReconciliationBinding`, `ResourceIsolationDecision`, `evaluate_resource_isolation`, `AdmissionRequest`
- **Interfaces consumed**: `ProviderCompletionContract`, `TerminalOutcomeState`
- **Parallel-safe**: yes; the lane owns one new schema module and focused test.
- **Tasks**:
  - test: Assert the broker verb enum is exactly `publish`, `merge`, `release`, `package`, and `publish_committed_branch`; freeze publish-committed request/result fields and terminal evidence keyed by idempotency key.
  - test: Assert `AdmissionRequest` has exactly the seven IF-0-FREEZE-7 fields and broker requests bind that same object rather than a duplicate fencing shape.
  - test: Assert the authority enum maps roadmap to intent, event log to active operation state, Git commit/PR head to implementation, merged SHA to merged state, registry/manifest to released state, and transcripts/`.phase-loop` to recovery evidence only.
  - test: Assert the invalidation enum is exactly `effective_code_changed`, `roadmap_changed`, `base_sha_changed`, `dependency_sha_changed`, and `verification_plan_digest_changed` with an explicit supported version.
  - test: Prove `evaluate_resource_isolation` is parallel-safe only for disjoint owned paths with frozen shared interfaces and serializes same-repo mutation, topological merge, release publication, overlap, or unknown evidence.
  - impl: Add frozen dataclasses/enums, pure validation/predicate helpers, and docstrings only; do not add admission storage, broker calls, reconciliation I/O, or coordinator wiring.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_coordination_contracts.py -q`

### SL-3 — Documentation, Fixtures, Import Surface, And Evidence Reducer

- **Scope**: Integrate the contracts, preserve metadata-only baseline/fault inputs, document the failure taxonomy, and prove all gates importable without runtime wiring.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/convergence/__init__.py`, `phase-loop-runtime/tests/test_convergence_fixture_contracts.py`, `phase-loop-runtime/tests/fixtures/convergence/**`, `docs/phase-loop/convergence-contracts.md`
- **Interfaces provided**: `FREEZE import surface`, `FREEZE failure taxonomy`, `FREEZE baseline evidence`, `FREEZE adversarial fixture set`
- **Interfaces consumed**: `IF-0-FREEZE-1`, `IF-0-FREEZE-2`, `IF-0-FREEZE-3`, `IF-0-FREEZE-4`, `IF-0-FREEZE-5`, `IF-0-FREEZE-6`, `IF-0-FREEZE-7`, `CoordinatorEvent`, `ConvergenceResultEnvelope`, `PROVIDER_COMPLETION_CLASSIFICATIONS`, `AdmissionRequest`
- **Parallel-safe**: no; this synthesized writer runs only after every producer lane lands.
- **Tasks**:
  - test: Define fixture assertions first: required provenance, roadmap digest, repository/head binding, redaction posture, capture timestamp, expected fail-closed classification, and no credential/transcript payload fields.
  - test: Require adversarial fixtures for forged completion evidence, malformed result envelope, capability overclaim, stale/delayed seat write, mixed-version envelope, and action-outside-bounds, plus crash, partition, stale-worker, delayed-commit, mixed-version, exact-head, degraded-seat, and ambiguous-outcome cases.
  - test: Require baseline evidence to record measured time-to-converge and transcript-dependence with source/method metadata; unavailable measurements must be marked unavailable with a reason and cannot be replaced by invented numbers.
  - impl: Add the package import surface and `docs/phase-loop/convergence-contracts.md` with the authority split, terminal-outcome rule, provider-classification summary, failure taxonomy, and no-runtime-wiring boundary.
  - impl: Checkpoint metadata-only fixtures using canonical `.phase-loop/`, Git, roadmap, and provider evidence; record absent review sources as provenance metadata, not inferred content.
  - impl: Record current repository head and recomputed roadmap digest at capture time so FAULTS can detect stale inputs.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_fixture_contracts.py tests/test_convergence_event_contracts.py tests/test_convergence_provider_contracts.py tests/test_convergence_coordination_contracts.py -q`

### SL-4 — Documentation And Whole-Phase Verification Reducer

- **Scope**: Run the complete FREEZE proof set, confirm ownership/documentation coverage, and emit closeout evidence for all seven gates.
- **Owned files**: none
- **Interfaces provided**: `FREEZE verification evidence`
- **Interfaces consumed**: `FREEZE import surface`, `FREEZE failure taxonomy`, `FREEZE baseline evidence`, `FREEZE adversarial fixture set`
- **Parallel-safe**: no; this read-only reducer runs after synthesized evidence.
- **Tasks**:
  - test: Run roadmap/plan validators, all focused FREEZE tests, the ledger regression module, and `test_train_invariants.py` INV-1 through INV-6.
  - impl: Do not repair files here; route failures to the sole owning lane and rerun the complete reducer.
  - verify: `PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-convergence-v1.md && python skills-src/claude/claude-plan-phase/scripts/validate_plan_doc.py plans/phase-plan-vergence-v1-FREEZE.md && cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_event_contracts.py tests/test_convergence_provider_contracts.py tests/test_convergence_coordination_contracts.py tests/test_convergence_fixture_contracts.py tests/test_train_roadmap.py tests/test_train_invariants.py -q`

## Execution Policy

- work-unit defaults: work-unit=`lane_execute`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`
- SL-3: executor=`codex`, effort=`high`, work-unit=`phase_reducer`, unsupported=`inherit_default`, inherit-default=`true`, reason=`cross-lane evidence and documentation synthesis`
- SL-4: executor=`codex`, effort=`high`, work-unit=`phase_verify`, unsupported=`inherit_default`, inherit-default=`true`, reason=`whole-phase verification reducer`

## Execution Notes

- SL-0, SL-1, and SL-2 may run in parallel only with machine-verified disjoint ownership, explicit dependencies, and scheduler-owned worktree assignments. SL-3 and SL-4 are excluded from writer waves.
- The complete phase-owned write set is the union of the four non-empty `Owned files` declarations. No lane may widen scope to `train_runner.py`, `publishing.py`, broker runtime modules, adapters, or coordinator CLI wiring.
- Public-surface decision: `no_doc_delta` for `README.md`, changelog, and release notes; FREEZE adds only the dedicated convergence contract reference, while later release phases own public release documentation.
- Contract helpers may validate or normalize data but must not dispatch, reconcile live state, hold credentials, call providers, or alter existing train execution behavior.
- Provider guarantees and baseline measurements require evidence. Missing source material produces an explicit unavailable/unsupported record or repairable blocker, never a guessed `supported` classification or fabricated metric.
- Closeout must list IF-0-FREEZE-1 through IF-0-FREEZE-7 in `produced_if_gates`. Passing tests with any omitted gate is not complete.
- Policy precedence is CLI/operator override, this plan, roadmap policy, `Dispatch Hints`, then registry defaults. Silent model/effort downgrade is forbidden; declared `inherit_default` is the only default inheritance here.

## Spec Closeout Plan

- schema: `spec_delta_closeout.v1`
- decision: `canonical_spec_update`
- target surfaces: `phase-loop-runtime/src/phase_loop_runtime/train_ledger.py`, `phase-loop-runtime/src/phase_loop_runtime/convergence/*.py`, `phase-loop-runtime/tests/test_convergence_*.py`, `phase-loop-runtime/tests/fixtures/convergence/**`, `docs/phase-loop/convergence-contracts.md`
- evidence paths: `plans/phase-plan-vergence-v1-FREEZE.md`, `docs/phase-loop/convergence-contracts.md`, `phase-loop-runtime/tests/test_convergence_event_contracts.py`, `phase-loop-runtime/tests/test_convergence_provider_contracts.py`, `phase-loop-runtime/tests/test_convergence_coordination_contracts.py`, `phase-loop-runtime/tests/test_convergence_fixture_contracts.py`
- redaction posture: `metadata_only`
- downstream handling: `none`

## Verification

```bash
PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-convergence-v1.md
python skills-src/claude/claude-plan-phase/scripts/validate_plan_doc.py plans/phase-plan-vergence-v1-FREEZE.md
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_event_contracts.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_provider_contracts.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_coordination_contracts.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_fixture_contracts.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_train_roadmap.py tests/test_train_invariants.py -q
```

automation:
  suite_command: cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_event_contracts.py tests/test_convergence_provider_contracts.py tests/test_convergence_coordination_contracts.py tests/test_convergence_fixture_contracts.py tests/test_train_roadmap.py tests/test_train_invariants.py -q

## Acceptance Criteria

- [ ] `phase-loop-runtime/tests/test_convergence_event_contracts.py` proves IF-0-FREEZE-1 and IF-0-FREEZE-2 field, enum, version, intent/outcome, and legacy-normalization contracts while `phase-loop-runtime/tests/test_train_roadmap.py` remains green.
- [ ] `phase-loop-runtime/tests/test_convergence_provider_contracts.py` proves every repository-discovered automated verb/provider pair is classified and only evidence-backed `supported` pairs are automatable.
- [ ] `phase-loop-runtime/tests/test_convergence_provider_contracts.py` proves `provider_call_in_flight` has exactly three terminal exits and ambiguity has no timeout/human-override progress path.
- [ ] `phase-loop-runtime/tests/test_convergence_coordination_contracts.py` proves broker verbs, exact-state authorities, all five invalidation triggers, fail-closed isolation, and the one seven-field `AdmissionRequest`.
- [ ] `phase-loop-runtime/tests/test_convergence_fixture_contracts.py` proves all required failure/adversarial fixtures exist, carry current roadmap/head provenance, are metadata-only, and declare fail-closed expected results.
- [ ] `phase-loop-runtime/tests/test_convergence_fixture_contracts.py` proves baseline records contain evidence-backed time-to-converge and transcript-dependence measurements or explicit unavailable reasons, with no fabricated values.
- [ ] `docs/phase-loop/convergence-contracts.md` documents the complete failure taxonomy, authority split, terminal-outcome rule, provider-classification summary, and contract-only boundary.
- [ ] `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_convergence_event_contracts.py tests/test_convergence_provider_contracts.py tests/test_convergence_coordination_contracts.py tests/test_convergence_fixture_contracts.py tests/test_train_roadmap.py tests/test_train_invariants.py -q` passes.
