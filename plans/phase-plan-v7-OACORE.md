---
phase_loop_plan_version: 1
phase: OACORE
roadmap: specs/phase-plans-v7.md
roadmap_sha256: f2c28a7abd11f72d01ae1961951fa7e9cceb03d5c217ac1de3a469803d0fcaae
---

# OACORE: Shared Conformance Core

## Context

OACORE is the second phase for roadmap v7. Canonical `.phase-loop/` state reports `OACONTRACT` complete at commit `36df412651c40080db13ce9e60f2a6b83009b1e9` and selects `OACORE` as the current unplanned phase for `specs/phase-plans-v7.md`. Legacy `.codex/phase-loop/` files are compatibility artifacts only and must not block or supersede `.phase-loop/` state.

This phase builds the pure deterministic validator core over the pinned Consiliency/spec contract and vector manifest. It owns shared runtime machinery inside `phase_loop_runtime.conformance` only; it does not add the advisory CLI, governed-pipeline adapter, Portal projection, credentialed package publishing, or any external-provider access.

OACORE consumes `IF-0-OACONTRACT-1` from the previous phase: `OutsideAgentContractPin`, `EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN`, `load_outside_agent_contract_pin()`, and `OutsideAgentContractError`. The core must keep inputs and outputs metadata-only, fail closed for unsafe refs or raw payloads, and produce a stable typed verdict surface that OAMOCK and OAREAL can consume without redefining schema semantics.

## Interface Freeze Gates

- [ ] IF-0-OACORE-1 - Shared outside-agent conformance core API: `phase_loop_runtime.conformance.outside_agent_core.validate_outside_agent_submission()` accepts deterministic local inputs only, covers submission kinds `work_request`, `implementation_submission`, and `ambiguity_report`, and returns an `OutsideAgentConformanceVerdict` containing `verdict_schema_version`, `submission_kind`, `status`, typed `OutsideAgentBlocker` entries, `contract_pin`, `input_digest`, repo-relative `provenance_refs`, `redaction_posture="metadata_only"`, and metadata-only `evidence_refs`; schema, vector, redaction, and provenance helpers fail closed on unknown fields, unsupported versions, absolute paths, missing digests, raw payloads, and path traversal without network or credential access.

## Lane Index & Dependencies

SL-0 — Core API preamble and typed verdict model
  Depends on: (none)
  Blocks: SL-1, SL-2, SL-3, SL-4, SL-5
  Parallel-safe: no
SL-1 — Submission schema validation
  Depends on: SL-0
  Blocks: SL-4, SL-5
  Parallel-safe: yes
SL-2 — Provenance refs and digest checks
  Depends on: SL-0
  Blocks: SL-4, SL-5
  Parallel-safe: yes
SL-3 — Metadata-only redaction guard
  Depends on: SL-0
  Blocks: SL-4, SL-5
  Parallel-safe: yes
SL-4 — Vector runner and expected outcomes
  Depends on: SL-0, SL-1, SL-2, SL-3
  Blocks: SL-5
  Parallel-safe: no
SL-5 — Consumer docs and OACORE verification reducer
  Depends on: SL-0, SL-1, SL-2, SL-3, SL-4
  Blocks: (none)
  Parallel-safe: no

## Lanes

### SL-0 — Core API Preamble And Typed Verdict Model

- **Scope**: Define the shared outside-agent conformance input/result model and single core entry point that later advisory and real-validator phases consume.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/conformance/__init__.py`, `phase-loop-runtime/src/phase_loop_runtime/conformance/outside_agent_core.py`, `phase-loop-runtime/tests/test_outside_agent_core_api.py`
- **Interfaces provided**: `OutsideAgentSubmissionKind`, `OutsideAgentVerdictStatus`, `OutsideAgentBlocker`, `OutsideAgentConformanceVerdict`, `validate_outside_agent_submission()`, `IF-0-OACORE-1 typed verdict fields`
- **Interfaces consumed**: `IF-0-OACONTRACT-1 (pre-existing)`, `OutsideAgentContractPin (pre-existing)`, `EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN (pre-existing)`, `load_outside_agent_contract_pin() (pre-existing)`
- **Parallel-safe**: no; this is the single writer for the public core API and package export surface consumed by every OACORE lane.
- **Tasks**:
  - test: Add focused API tests proving the public entry point is deterministic, returns typed verdict objects, preserves the previous OACONTRACT import helpers, and never requires network, provider credentials, or environment secrets.
  - impl: Add `outside_agent_core.py` with frozen dataclasses/enums for submission kind, status, blockers, provenance refs, evidence refs, and conformance verdicts; expose only the stable core symbols from `conformance/__init__.py`.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_core_api.py tests/test_consiliency_ingest.py::ConformanceNamedLibraryTest -q`

### SL-1 — Submission Schema Validation

- **Scope**: Validate the three canonical outside-agent submission kinds against the pinned contract shape and fail closed on unsupported versions or unknown fields.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/conformance/outside_agent_schema.py`, `phase-loop-runtime/tests/test_outside_agent_schema_validation.py`
- **Interfaces provided**: `validate_outside_agent_submission_schema()`, typed schema blocker codes `unsupported_schema_version`, `unsupported_submission_kind`, `unknown_field`, `schema_validation_failed`
- **Interfaces consumed**: `OutsideAgentSubmissionKind`, `OutsideAgentBlocker`, `OutsideAgentConformanceVerdict`, `IF-0-OACONTRACT-1 (pre-existing)`, pinned Consiliency/spec schema resources (pre-existing)
- **Parallel-safe**: yes; writes only the schema helper and schema-focused tests after SL-0 freezes the core model.
- **Tasks**:
  - test: Add positive and negative tests for `work_request`, `implementation_submission`, and `ambiguity_report`, plus unsupported schema version, unsupported submission kind, unknown top-level field, and missing required metadata cases.
  - impl: Implement schema validation as a local helper that consumes the pinned contract loader or caller-supplied schema documents and returns typed blockers rather than raising untyped exceptions for malformed submissions.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_schema_validation.py -q`

### SL-2 — Provenance Refs And Digest Checks

- **Scope**: Validate submission source refs, repo-relative evidence refs, and digests without accepting absolute paths, traversal, or missing digest metadata.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/conformance/outside_agent_provenance.py`, `phase-loop-runtime/tests/test_outside_agent_provenance.py`
- **Interfaces provided**: `validate_outside_agent_provenance()`, `normalize_outside_agent_ref()`, typed provenance blocker codes `absolute_path_ref`, `path_traversal_ref`, `missing_digest`, `digest_mismatch`, `unsafe_source_ref`
- **Interfaces consumed**: `OutsideAgentSubmissionKind`, `OutsideAgentBlocker`, `OutsideAgentConformanceVerdict`, `IF-0-OACORE-1 typed verdict fields`
- **Parallel-safe**: yes; writes only provenance helpers and provenance tests after SL-0 freezes the shared blocker model.
- **Tasks**:
  - test: Add tests for repo-relative refs, digest presence, digest mismatch, absolute paths, `..` traversal, empty refs, and metadata-only provenance refs that do not read arbitrary local files.
  - impl: Implement provenance normalization and digest checks so the core records only repo-relative refs and digests, and returns typed blockers for unsafe or incomplete provenance.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_provenance.py -q`

### SL-3 — Metadata-Only Redaction Guard

- **Scope**: Enforce metadata-only inputs and outputs so raw provider payloads, local env values, raw logs, and copied vector bodies cannot enter conformance verdicts.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/conformance/outside_agent_redaction.py`, `phase-loop-runtime/tests/test_outside_agent_redaction.py`
- **Interfaces provided**: `assert_outside_agent_metadata_only()`, `sanitize_outside_agent_verdict()`, typed redaction blocker codes `raw_payload_present`, `secret_like_value_present`, `local_env_value_present`, `raw_log_present`
- **Interfaces consumed**: `OutsideAgentBlocker`, `OutsideAgentConformanceVerdict`, `IF-0-OACORE-1 typed verdict fields`
- **Parallel-safe**: yes; writes only redaction helpers and redaction tests after SL-0 freezes the shared verdict model.
- **Tasks**:
  - test: Add tests proving clean metadata-only submissions and verdicts pass while raw payload fields, provider response bodies, local env shaped keys, raw logs, and copied vector bodies fail closed.
  - impl: Implement a conservative redaction guard used by the core before returning verdict output; it must report typed blockers without printing or storing secret-shaped values.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_redaction.py -q`

### SL-4 — Vector Runner And Expected Outcomes

- **Scope**: Run canonical outside-agent vectors from the pinned Consiliency/spec manifest through the shared core and compare expected outcomes deterministically.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/conformance/outside_agent_vectors.py`, `phase-loop-runtime/tests/test_outside_agent_vectors.py`
- **Interfaces provided**: `run_outside_agent_vectors()`, `OutsideAgentVectorResult`, canonical vector pass/fail evidence for `IF-0-OACORE-1`
- **Interfaces consumed**: `validate_outside_agent_submission()`, `validate_outside_agent_submission_schema()`, `validate_outside_agent_provenance()`, `assert_outside_agent_metadata_only()`, `IF-0-OACONTRACT-1 (pre-existing)`
- **Parallel-safe**: no; this lane consumes schema, provenance, and redaction helpers before reducing canonical vector outcomes.
- **Tasks**:
  - test: Add vector-runner tests that create metadata-only temporary vector manifests, cover positive and negative expected outcomes, and prove unknown vector schema version, missing expected outcome, and manifest digest drift fail closed.
  - impl: Implement a local vector runner that loads only the pinned Consiliency/spec manifest or caller-supplied test manifest, invokes the shared core, and returns metadata-only vector results.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_vectors.py -q`

### SL-5 — Consumer Docs And OACORE Verification Reducer

- **Scope**: Document the shared core contract and run the focused OACORE verification set before handing OAMOCK and OAREAL a stable interface.
- **Owned files**: `docs/outside-agent-conformance.md`
- **Interfaces provided**: `IF-0-OACORE-1`, OACORE phase verification evidence, consumer-facing shared-core contract language
- **Interfaces consumed**: `OutsideAgentConformanceVerdict`, `validate_outside_agent_submission()`, schema validation evidence, provenance validation evidence, redaction evidence, vector runner evidence
- **Parallel-safe**: no; this reducer depends on every producing lane before updating the synthesized consumer-facing doc.
- **Tasks**:
  - test: Confirm roadmap validation passes and focused OACONTRACT/OACORE tests cover API compatibility, schema validation, provenance failures, redaction failures, vector outcomes, and metadata-only output.
  - impl: Update `docs/outside-agent-conformance.md` to describe the shared core API, typed verdict fields, fail-closed blockers, metadata-only guarantees, and the boundary between OACORE, OAMOCK, and OAREAL.
  - verify: `PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v7.md && cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_contract_pin.py tests/test_outside_agent_contract_imports.py tests/test_outside_agent_contract_drift.py tests/test_outside_agent_core_api.py tests/test_outside_agent_schema_validation.py tests/test_outside_agent_provenance.py tests/test_outside_agent_redaction.py tests/test_outside_agent_vectors.py -q`

## Dispatch Hints

- preferred executors: `codex`
- allowed executors: `codex`
- required capabilities: `live_launch`, `structured_output`

## Execution Policy

- work-unit defaults: work-unit=`lane_execute`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`
- execute: executor=`codex`, model=`gpt-5.5`, effort=`high`, work-unit=`lane_execute`, reason=`phase-plan policy`
- SL-5: executor=`codex`, model=`gpt-5.5`, effort=`medium`, work-unit=`phase_verify`, reason=`consumer docs and focused OACORE verification reducer`

## Execution Notes

- Execute SL-0 first so all helper lanes consume one typed verdict and blocker model. SL-1, SL-2, and SL-3 may run after SL-0 because their owned files are disjoint. Run SL-4 after those helper lanes, and run SL-5 last.
- Preserve the OACONTRACT import pin and compatibility surface. Do not edit contract metadata, copy canonical schema JSON, copy canonical vector bodies, or redefine Consiliency/spec schema truth in this phase.
- The core must be deterministic and credential-free. Do not introduce network calls, provider clients, Vercel/Supabase/GitHub calls, local env reads for secrets, or runtime dependencies on unpublished external services.
- Keep advisory and authoritative authority language out of the core. OACORE returns typed conformance facts only; OAMOCK adds advisory labeling later and OAREAL adds the governed-pipeline runtime surface later.
- Documentation sweep decision: `docs/outside-agent-conformance.md` is the consumer-facing boundary doc for this phase; `no_doc_delta` for `README.md`, `CHANGELOG.md`, and release notes because OACORE is not a release/package phase.
- Policy precedence is CLI/operator override, phase-plan policy, roadmap policy, `Dispatch Hints`, then registry defaults; no executor/model downgrade is allowed without explicit fallback or inherited default behavior.

## Spec Closeout Plan

- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `phase-loop-runtime/src/phase_loop_runtime/conformance/__init__.py`, `phase-loop-runtime/src/phase_loop_runtime/conformance/outside_agent_core.py`, `phase-loop-runtime/src/phase_loop_runtime/conformance/outside_agent_schema.py`, `phase-loop-runtime/src/phase_loop_runtime/conformance/outside_agent_provenance.py`, `phase-loop-runtime/src/phase_loop_runtime/conformance/outside_agent_redaction.py`, `phase-loop-runtime/src/phase_loop_runtime/conformance/outside_agent_vectors.py`, `docs/outside-agent-conformance.md`
- evidence paths: `plans/phase-plan-v7-OACORE.md`, `phase-loop-runtime/tests/test_outside_agent_core_api.py`, `phase-loop-runtime/tests/test_outside_agent_schema_validation.py`, `phase-loop-runtime/tests/test_outside_agent_provenance.py`, `phase-loop-runtime/tests/test_outside_agent_redaction.py`, `phase-loop-runtime/tests/test_outside_agent_vectors.py`, `phase-loop-runtime/tests/test_outside_agent_contract_pin.py`, `phase-loop-runtime/tests/test_outside_agent_contract_imports.py`, `phase-loop-runtime/tests/test_outside_agent_contract_drift.py`, `docs/outside-agent-conformance.md`
- redaction posture: `metadata_only`
- downstream handling: `none`

## Verification

```bash
PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v7.md
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_core_api.py tests/test_consiliency_ingest.py::ConformanceNamedLibraryTest -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_schema_validation.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_provenance.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_redaction.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_vectors.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_contract_pin.py tests/test_outside_agent_contract_imports.py tests/test_outside_agent_contract_drift.py tests/test_outside_agent_core_api.py tests/test_outside_agent_schema_validation.py tests/test_outside_agent_provenance.py tests/test_outside_agent_redaction.py tests/test_outside_agent_vectors.py -q
git diff --check
```

automation:
  suite_command: PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v7.md && cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_contract_pin.py tests/test_outside_agent_contract_imports.py tests/test_outside_agent_contract_drift.py tests/test_outside_agent_core_api.py tests/test_outside_agent_schema_validation.py tests/test_outside_agent_provenance.py tests/test_outside_agent_redaction.py tests/test_outside_agent_vectors.py -q && cd .. && git diff --check

## Acceptance Criteria

- [ ] `validate_outside_agent_submission()` returns an `OutsideAgentConformanceVerdict` with typed status, typed blockers, contract pin metadata, input digest, provenance refs, evidence refs, and `redaction_posture="metadata_only"`.
- [ ] `phase-loop-runtime/tests/test_outside_agent_schema_validation.py` covers `work_request`, `implementation_submission`, and `ambiguity_report` submission kinds.
- [ ] `phase-loop-runtime/tests/test_outside_agent_core_api.py` proves core validation runs without network access, provider credentials, or secret env values.
- [ ] `phase-loop-runtime/tests/test_outside_agent_vectors.py` proves positive and negative outside-agent vectors from the pinned Consiliency/spec manifest pass with expected outcomes.
- [ ] `phase-loop-runtime/tests/test_outside_agent_schema_validation.py`, `phase-loop-runtime/tests/test_outside_agent_provenance.py`, and `phase-loop-runtime/tests/test_outside_agent_redaction.py` prove unknown fields, unsupported schema versions, unsupported submission kinds, absolute paths, path traversal, missing digests, digest mismatch, raw payloads, and raw logs fail closed with typed blockers.
- [ ] `phase-loop-runtime/tests/test_outside_agent_redaction.py` proves OACORE outputs contain only metadata, digests, repo-relative refs, typed failure information, contract pin metadata, and metadata-only vector result evidence.
- [ ] `docs/outside-agent-conformance.md` documents the shared core API and preserves the authority boundary: OACORE is shared conformance machinery, OAMOCK is advisory later, and OAREAL is authoritative runtime later.
- [ ] Focused OACONTRACT/OACORE verification and roadmap validation pass before the phase can close as `complete`.
