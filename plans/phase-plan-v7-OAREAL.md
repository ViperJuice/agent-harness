---
phase_loop_plan_version: 1
phase: OAREAL
roadmap: specs/phase-plans-v7.md
roadmap_sha256: f2c28a7abd11f72d01ae1961951fa7e9cceb03d5c217ac1de3a469803d0fcaae
---

# OAREAL: Real Validator Runtime Surface

## Context

OAREAL is the fourth phase for roadmap v7. Canonical `.phase-loop/` event-ledger state shows OACONTRACT, OACORE, and OAMOCK completed for `specs/phase-plans-v7.md`; `.phase-loop/state.json` and `.phase-loop/tui-handoff.md` lag behind the final OAMOCK closeout, so this plan reconciles from `.phase-loop/events.jsonl`, HEAD `2662aee5ee43cf8e3bfd17dc05f389e49b0ff2e0`, and clean git topology. Legacy `.codex/phase-loop/` files are compatibility artifacts only and must not block or supersede canonical `.phase-loop/` state.

This phase exposes the governed-pipeline-consumable real validator surface over the shared outside-agent conformance core. It consumes `IF-0-OACORE-1` and the completed OAMOCK advisory boundary, but it does not reuse advisory authority language. Governed-pipeline remains the acceptance and merge authority; agent-harness supplies deterministic conformance verdicts, stable exit codes, metadata-only JSON, contract/vector pin evidence, and CI/release vector evidence.

OAREAL does not edit governed-pipeline workflows, Portal projection, credentialed package publishing, or the canonical Consiliency/spec contract. Runtime submission validation evaluates only the submitted refs passed by governed-pipeline; canonical vectors are run by agent-harness CI/release verification and are not executed on every live submission.

## Interface Freeze Gates

- [ ] IF-0-OAREAL-1 - Real outside-agent validator runtime surface: `phase_loop_runtime.conformance.outside_agent_real.build_outside_agent_validation_verdict()` and `serialize_outside_agent_validation_verdict()` plus the `phase-loop outside-agent-validate <submission-file> --output <verdict-file> [--submitted-ref <repo-relative-ref>]...` command accept one local metadata-only outside-agent submission JSON file and governed-pipeline-submitted repo-relative refs, run `validate_outside_agent_submission()` exactly once for the live submission, do not run canonical vectors during live submission validation, and emit deterministic JSON containing `validator_version`, `authority="governed_pipeline_validator"`, `verdict_schema_version`, `contract_pin`, `vector_manifest_hash`, `input_digest`, `submitted_refs`, typed `status`, typed `blockers`, repo-relative `evidence_refs`, `redaction_posture="metadata_only"`, `vectors_executed=false`, and stable exit codes `0` for conformant submission, `2` for malformed input, `3` for redaction violation, `4` for provenance failure, `5` for contract/vector pin failure, `6` for other conformance blockers, and `1` for internal error; output must not include advisory-only labels, `accepted_for_merge`, `merge_verdict`, raw payloads, secrets, local env values, absolute paths, or copied vector bodies.

## Lane Index & Dependencies

SL-0 — Real validator SDK and public exports
  Depends on: (none)
  Blocks: SL-1, SL-2, SL-3, SL-4
  Parallel-safe: no
SL-1 — JSON verdict shape and exit-code mapping
  Depends on: SL-0
  Blocks: SL-2, SL-3, SL-4
  Parallel-safe: yes
SL-2 — Phase-loop CLI entrypoint and governed-pipeline invocation
  Depends on: SL-0, SL-1
  Blocks: SL-3, SL-4
  Parallel-safe: no
SL-3 — CI-style fixtures and vector evidence boundary
  Depends on: SL-0, SL-1, SL-2
  Blocks: SL-4
  Parallel-safe: no
SL-4 — Downstream docs and OAREAL verification reducer
  Depends on: SL-0, SL-1, SL-2, SL-3
  Blocks: (none)
  Parallel-safe: no

## Lanes

### SL-0 — Real Validator SDK And Public Exports

- **Scope**: Define the real validator API that wraps OACORE verdicts for governed-pipeline without changing shared core semantics or advisory output.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/conformance/__init__.py`, `phase-loop-runtime/src/phase_loop_runtime/conformance/outside_agent_real.py`, `phase-loop-runtime/tests/test_outside_agent_real_runtime.py`
- **Interfaces provided**: `OutsideAgentValidationVerdict`, `OutsideAgentValidationExitCode`, `OutsideAgentSubmittedRef`, `build_outside_agent_validation_verdict()`, runtime API fields for `IF-0-OAREAL-1`
- **Interfaces consumed**: `IF-0-OACORE-1 (pre-existing)`, `validate_outside_agent_submission() (pre-existing)`, `OutsideAgentConformanceVerdict (pre-existing)`, `OutsideAgentVerdictStatus (pre-existing)`, `OutsideAgentBlocker (pre-existing)`, `OutsideAgentEvidenceRef (pre-existing)`, `EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN (pre-existing)`, `phase_loop_runtime.__version__ (pre-existing)`
- **Parallel-safe**: no; this is the single writer for the public real-validator SDK/export surface consumed by serialization, CLI, fixtures, and docs.
- **Tasks**:
  - test: Add focused runtime API tests that pass clean, malformed, redaction-blocked, provenance-blocked, and contract-pin-failed submissions through the real validator API and assert it calls the shared core once, records validator version, contract pin, vector manifest hash, input digest, submitted refs, typed blockers, and metadata-only evidence.
  - impl: Add `outside_agent_real.py` with frozen dataclasses/enums for the real validation verdict, submitted refs, and exit-code classification; export the real-validator SDK symbols from `conformance/__init__.py` without changing OACORE or OAMOCK behavior.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_real_runtime.py tests/test_outside_agent_core_api.py tests/test_outside_agent_advisory.py -q`

### SL-1 — JSON Verdict Shape And Exit-Code Mapping

- **Scope**: Freeze the governed-pipeline JSON verdict shape and stable real-validator exit-code mapping.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/conformance/outside_agent_real_output.py`, `phase-loop-runtime/tests/test_outside_agent_real_output.py`
- **Interfaces provided**: `serialize_outside_agent_validation_verdict()`, `digest_outside_agent_validation_bytes()`, real-validator JSON fields, exit-code mapping `0`, `2`, `3`, `4`, `5`, `6`, `1`
- **Interfaces consumed**: `OutsideAgentValidationVerdict`, `OutsideAgentValidationExitCode`, `OutsideAgentSubmittedRef`, `OutsideAgentBlocker (pre-existing)`, `OutsideAgentConformanceVerdict (pre-existing)`, `IF-0-OAREAL-1`
- **Parallel-safe**: yes; writes only real-validator serialization and serialization-focused tests after SL-0 freezes the real validation model.
- **Tasks**:
  - test: Add serializer tests that compare deterministic JSON for conformant and blocked verdicts, assert sorted stable fields, assert `vectors_executed` remains false for live submissions, and assert advisory-only and merge-verdict fields are absent.
  - impl: Implement real-validator serialization that emits only metadata, digests, repo-relative refs, typed blockers, contract pin metadata, validator version, vector manifest hash, and metadata-only evidence paths.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_real_output.py -q`

### SL-2 — Phase-Loop CLI Entrypoint And Governed-Pipeline Invocation

- **Scope**: Add the governed-pipeline-facing phase-loop command that validates one submitted outside-agent JSON file and writes the real validator verdict to an explicit output path.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/cli.py`, `phase-loop-runtime/tests/test_outside_agent_real_cli.py`
- **Interfaces provided**: `phase-loop outside-agent-validate`, `phase-loop outside-agent-validate <submission-file> --output <verdict-file> [--submitted-ref <repo-relative-ref>]...`, command-line JSON output shape, stable real-validator exit codes
- **Interfaces consumed**: `build_outside_agent_validation_verdict()`, `serialize_outside_agent_validation_verdict()`, `digest_outside_agent_validation_bytes()`, `OutsideAgentValidationExitCode`, `IF-0-OAREAL-1`
- **Parallel-safe**: no; `cli.py` is a single-writer entrypoint file and must not be edited concurrently with another lane.
- **Tasks**:
  - test: Add CLI tests for conformant validation, malformed JSON, redaction violation, provenance failure, missing required `--output`, output-file writing, submitted-ref echoing, and absence of advisory/merge-authority fields in stdout and files.
  - impl: Register `outside-agent-validate` on the existing phase-loop CLI entrypoint, require `--output`, accept repeated repo-relative `--submitted-ref` values, map real-validator classifications to the frozen exit codes, write the verdict JSON to the output path, and also print the same JSON to stdout for CI logs.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_real_cli.py tests/test_phase_loop_runtime_boundary.py::TestPhaseLoopRuntimeBoundary::test_pyproject_console_scripts_share_cli_main -q`

### SL-3 — CI-Style Fixtures And Vector Evidence Boundary

- **Scope**: Add metadata-only governed-pipeline invocation fixtures and prove live validation does not run canonical vectors while CI/release verification still exercises the pinned vector runner.
- **Owned files**: `phase-loop-runtime/tests/fixtures/outside_agent_real/**`, `phase-loop-runtime/tests/test_outside_agent_real_ci.py`
- **Interfaces provided**: real-validator CI fixture cases, live-submission invocation evidence, canonical vector evidence boundary for `IF-0-OAREAL-1`
- **Interfaces consumed**: `phase-loop outside-agent-validate`, `serialize_outside_agent_validation_verdict()`, `run_outside_agent_vectors() (pre-existing)`, `IF-0-OACORE-1 (pre-existing)`, `IF-0-OAREAL-1`
- **Parallel-safe**: no; this lane consumes the SDK, JSON serializer, and CLI before reducing CI-style fixture evidence.
- **Tasks**:
  - test: Add fixture tests that invoke the real CLI with clean, malformed, provenance-blocked, and redaction-blocked submissions; assert live outputs include `vectors_executed=false`; separately run the pinned vector runner in the test suite to provide CI/release vector evidence.
  - impl: Add small metadata-only fixture submissions and expected verdict snippets; do not add canonical schema JSON, raw vector bodies, provider payloads, raw logs, secrets, local env values, absolute paths, or copied vector corpus data.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_real_ci.py tests/test_outside_agent_vectors.py -q`

### SL-4 — Downstream Docs And OAREAL Verification Reducer

- **Scope**: Document the real validator runtime and run the focused OAREAL verification set before downstream governed-pipeline pinning.
- **Owned files**: `docs/outside-agent-conformance.md`
- **Interfaces provided**: governed-pipeline pin guidance, real-validator runtime docs, OAREAL phase verification evidence, `IF-0-OAREAL-1`
- **Interfaces consumed**: `IF-0-OAREAL-1`, real-validator SDK evidence, JSON verdict serializer evidence, CLI invocation evidence, CI fixture evidence, canonical vector runner evidence, OAMOCK advisory boundary evidence
- **Parallel-safe**: no; this reducer depends on every producing lane before updating synthesized downstream-facing docs and running the focused verification suite.
- **Tasks**:
  - test: Confirm roadmap validation passes and focused OACONTRACT/OACORE/OAMOCK/OAREAL tests cover contract pinning, shared core validation, advisory boundary, real-validator runtime, real JSON output, CLI exit codes, live-submission fixture invocation, vector CI evidence, and authority-language boundaries.
  - impl: Update `docs/outside-agent-conformance.md` to distinguish advisory preflight from the real governed-pipeline validator, name the stable command/API, input/output contract, exit codes, JSON fields, vector evidence boundary, and downstream pinning requirements.
  - verify: `PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v7.md && cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_contract_pin.py tests/test_outside_agent_contract_imports.py tests/test_outside_agent_contract_drift.py tests/test_outside_agent_core_api.py tests/test_outside_agent_schema_validation.py tests/test_outside_agent_provenance.py tests/test_outside_agent_redaction.py tests/test_outside_agent_vectors.py tests/test_outside_agent_advisory.py tests/test_outside_agent_advisory_cli.py tests/test_outside_agent_advisory_fixtures.py tests/test_outside_agent_authority_boundary.py tests/test_outside_agent_real_runtime.py tests/test_outside_agent_real_output.py tests/test_outside_agent_real_cli.py tests/test_outside_agent_real_ci.py -q && cd .. && git diff --check`

## Dispatch Hints

- preferred executors: `codex`
- allowed executors: `codex`
- required capabilities: `live_launch`, `structured_output`

## Execution Policy

- work-unit defaults: work-unit=`lane_execute`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`
- execute: executor=`codex`, model=`gpt-5.5`, effort=`high`, work-unit=`lane_execute`, reason=`phase-plan policy`
- SL-4: executor=`codex`, model=`gpt-5.5`, effort=`medium`, work-unit=`phase_verify`, reason=`downstream docs and focused OAREAL verification reducer`

## Execution Notes

- Execute SL-0 first so the real-validator model and API are frozen before JSON output, CLI, fixture, and docs work. SL-1 can run after SL-0 because its owned files are disjoint. Run SL-2 after SL-0 and SL-1, SL-3 after the CLI exists, and SL-4 last.
- Preserve OACORE as the only conformance decision engine. OAREAL may classify real-validator exit codes from typed blockers and contract-pin failures, but it must not redefine schema semantics or mutate advisory behavior.
- Runtime validation must evaluate only the live submitted refs passed by governed-pipeline and must not run canonical vector manifests during every live submission. Canonical vector runner evidence belongs in CI/release verification and OAREAL test output.
- Keep output metadata-only. Do not emit raw provider payloads, local env values, raw logs, copied canonical schema JSON, copied vector bodies, absolute local paths, or secret-shaped values.
- Governed-pipeline remains the acceptance and merge authority. Real validator output may be authoritative conformance evidence, but it must not emit `accepted_for_merge`, `merge_verdict`, or Portal projection fields.
- Documentation sweep decision: `docs/outside-agent-conformance.md` is the consumer-facing boundary doc for this phase; `no_doc_delta` for `README.md`, `CHANGELOG.md`, and release notes because OAREAL is not the release-prep phase.
- Policy precedence is CLI/operator override, phase-plan policy, roadmap policy, `Dispatch Hints`, then registry defaults; no executor/model downgrade is allowed without explicit fallback or inherited default behavior.

## Spec Closeout Plan

- schema: `spec_delta_closeout.v1`
- decision: `governed_pipeline_refresh`
- target surfaces: `phase-loop-runtime/src/phase_loop_runtime/conformance/__init__.py`, `phase-loop-runtime/src/phase_loop_runtime/conformance/outside_agent_real.py`, `phase-loop-runtime/src/phase_loop_runtime/conformance/outside_agent_real_output.py`, `phase-loop-runtime/src/phase_loop_runtime/cli.py`, `docs/outside-agent-conformance.md`
- evidence paths: `plans/phase-plan-v7-OAREAL.md`, `phase-loop-runtime/tests/test_outside_agent_real_runtime.py`, `phase-loop-runtime/tests/test_outside_agent_real_output.py`, `phase-loop-runtime/tests/test_outside_agent_real_cli.py`, `phase-loop-runtime/tests/test_outside_agent_real_ci.py`, `phase-loop-runtime/tests/fixtures/outside_agent_real/**`, `phase-loop-runtime/tests/test_outside_agent_vectors.py`, `docs/outside-agent-conformance.md`
- redaction posture: `metadata_only`
- downstream handling: `Governed Pipeline refresh`

## Verification

```bash
PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v7.md
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_real_runtime.py tests/test_outside_agent_core_api.py tests/test_outside_agent_advisory.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_real_output.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_real_cli.py tests/test_phase_loop_runtime_boundary.py::TestPhaseLoopRuntimeBoundary::test_pyproject_console_scripts_share_cli_main -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_real_ci.py tests/test_outside_agent_vectors.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_contract_pin.py tests/test_outside_agent_contract_imports.py tests/test_outside_agent_contract_drift.py tests/test_outside_agent_core_api.py tests/test_outside_agent_schema_validation.py tests/test_outside_agent_provenance.py tests/test_outside_agent_redaction.py tests/test_outside_agent_vectors.py tests/test_outside_agent_advisory.py tests/test_outside_agent_advisory_cli.py tests/test_outside_agent_advisory_fixtures.py tests/test_outside_agent_authority_boundary.py tests/test_outside_agent_real_runtime.py tests/test_outside_agent_real_output.py tests/test_outside_agent_real_cli.py tests/test_outside_agent_real_ci.py -q
git diff --check
```

automation:
  suite_command: PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v7.md && cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_contract_pin.py tests/test_outside_agent_contract_imports.py tests/test_outside_agent_contract_drift.py tests/test_outside_agent_core_api.py tests/test_outside_agent_schema_validation.py tests/test_outside_agent_provenance.py tests/test_outside_agent_redaction.py tests/test_outside_agent_vectors.py tests/test_outside_agent_advisory.py tests/test_outside_agent_advisory_cli.py tests/test_outside_agent_advisory_fixtures.py tests/test_outside_agent_authority_boundary.py tests/test_outside_agent_real_runtime.py tests/test_outside_agent_real_output.py tests/test_outside_agent_real_cli.py tests/test_outside_agent_real_ci.py -q && cd .. && git diff --check

## Acceptance Criteria

- [ ] `build_outside_agent_validation_verdict()` accepts metadata-only outside-agent submissions and governed-pipeline-submitted repo-relative refs, invokes `validate_outside_agent_submission()` exactly once, records validator version and contract/vector pin metadata, and returns deterministic real-validator evidence without network, provider, credential, or secret env access.
- [ ] `serialize_outside_agent_validation_verdict()` emits JSON with `validator_version`, `authority="governed_pipeline_validator"`, `verdict_schema_version`, `contract_pin`, `vector_manifest_hash`, `input_digest`, `submitted_refs`, typed `status`, typed `blockers`, repo-relative `evidence_refs`, `redaction_posture="metadata_only"`, and `vectors_executed=false` for live submission validation.
- [ ] `phase-loop outside-agent-validate <submission-file> --output <verdict-file> [--submitted-ref <repo-relative-ref>]...` writes the same real-validator JSON to the output file and stdout, and returns stable exit codes for conformant, malformed, redaction, provenance, contract/vector pin, other conformance-blocked, and internal-error outcomes.
- [ ] Live real-validator runtime tests prove canonical vectors are not run during every governed-pipeline submission validation, while the focused OAREAL suite still runs `tests/test_outside_agent_vectors.py` as CI/release vector evidence.
- [ ] Real-validator output contains no advisory-only labels, `accepted_for_merge`, `merge_verdict`, Portal projection fields, raw provider payloads, local env values, raw logs, absolute paths, copied schema JSON, or copied vector bodies.
- [ ] `docs/outside-agent-conformance.md` distinguishes producer advisory preflight from the governed-pipeline real validator, names the command/API, input/output contract, exit codes, JSON fields, vector evidence boundary, and downstream pinning requirements.
- [ ] Focused OACONTRACT/OACORE/OAMOCK/OAREAL verification, roadmap validation, and `git diff --check` pass before the phase can close as `complete`.
