---
phase_loop_plan_version: 1
phase: OAMOCK
roadmap: specs/phase-plans-v7.md
roadmap_sha256: f2c28a7abd11f72d01ae1961951fa7e9cceb03d5c217ac1de3a469803d0fcaae
---

# OAMOCK: Actor-Side Advisory Mock

## Context

OAMOCK is the third phase for roadmap v7. Canonical `.phase-loop/` ledger events show OACORE completed at commit `2d66c7d6060eabe89b11adc7f779a948a024cbd2` with verification passed, even though `.phase-loop/state.json` still lags at `current_phase: OACORE`; this plan reconciles from `.phase-loop/events.jsonl`, HEAD, and clean git state. Legacy `.codex/phase-loop/` files are compatibility artifacts only and must not block or supersede `.phase-loop/` state.

This phase wraps the shared outside-agent conformance core in an actor-side preflight for producers and outside agents. It consumes `IF-0-OACORE-1`, especially `validate_outside_agent_submission()`, `OutsideAgentConformanceVerdict`, typed blockers, contract pin metadata, provenance refs, evidence refs, and `redaction_posture="metadata_only"`.

OAMOCK does not add governed-pipeline CI wiring, Portal projection, merge authority, credentialed provider calls, or a package publish. Its output is advisory evidence only: useful for producer readiness and GitHub issue/PR attachment, never an acceptance or merge verdict.

## Interface Freeze Gates

- [ ] IF-0-OAMOCK-1 - Actor-side advisory mock CLI/SDK: `phase_loop_runtime.conformance.outside_agent_advisory.build_outside_agent_advisory_evidence()` and the `phase-loop outside-agent-preflight <submission-file> [--output <path>]` command accept one local metadata-only outside-agent submission JSON file, run `validate_outside_agent_submission()`, and emit advisory-only JSON with `authority="advisory"`, `accepted_for_merge` absent, `merge_verdict` absent, `redaction_posture="metadata_only"`, the core typed status and blockers, contract pin metadata, input digest, repo-relative provenance/evidence refs, and exit codes `0` for clean advisory pass, `2` for malformed input, `3` for redaction violation, `4` for provenance failure, and `1` for internal error.

## Lane Index & Dependencies

SL-0 - Advisory evidence SDK and serialization
  Depends on: (none)
  Blocks: SL-1, SL-2, SL-3, SL-4
  Parallel-safe: no
SL-1 - Phase-loop CLI entrypoint and exit codes
  Depends on: SL-0
  Blocks: SL-3, SL-4
  Parallel-safe: no
SL-2 - Advisory evidence fixtures and stable output cases
  Depends on: SL-0
  Blocks: SL-3, SL-4
  Parallel-safe: yes
SL-3 - Authority boundary regression tests
  Depends on: SL-0, SL-1, SL-2
  Blocks: SL-4
  Parallel-safe: no
SL-4 - Producer docs and OAMOCK verification reducer
  Depends on: SL-0, SL-1, SL-2, SL-3
  Blocks: (none)
  Parallel-safe: no

## Lanes

### SL-0 - Advisory Evidence SDK And Serialization

- **Scope**: Define the advisory evidence object and serialization helper that wrap OACORE verdicts without introducing acceptance authority.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/conformance/__init__.py`, `phase-loop-runtime/src/phase_loop_runtime/conformance/outside_agent_advisory.py`, `phase-loop-runtime/tests/test_outside_agent_advisory.py`
- **Interfaces provided**: `OutsideAgentAdvisoryEvidence`, `OutsideAgentAdvisoryExitCode`, `build_outside_agent_advisory_evidence()`, `serialize_outside_agent_advisory_evidence()`, advisory JSON fields for `IF-0-OAMOCK-1`
- **Interfaces consumed**: `IF-0-OACORE-1`, `validate_outside_agent_submission()`, `OutsideAgentConformanceVerdict`, `OutsideAgentVerdictStatus`, `OutsideAgentBlocker`, `OutsideAgentEvidenceRef`, `EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN`
- **Parallel-safe**: no; this is the single writer for the public advisory SDK/export surface consumed by CLI, fixtures, and authority tests.
- **Tasks**:
  - test: Add focused SDK tests that build advisory evidence for clean, malformed, redaction-blocked, and provenance-blocked submissions and assert the serialized JSON is deterministic, metadata-only, and advisory-labeled.
  - impl: Add `outside_agent_advisory.py` with frozen dataclasses/enums for advisory evidence and exit-code classification; export the advisory SDK symbols from `conformance/__init__.py` without changing OACORE semantics.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_advisory.py tests/test_outside_agent_core_api.py -q`

### SL-1 - Phase-Loop CLI Entrypoint And Exit Codes

- **Scope**: Add the producer-facing phase-loop command that reads one local submission file, runs the advisory SDK, writes advisory JSON, and returns stable preflight exit codes.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/cli.py`, `phase-loop-runtime/tests/test_outside_agent_advisory_cli.py`
- **Interfaces provided**: `phase-loop outside-agent-preflight <submission-file> [--output <path>]`, command-line JSON output shape, exit-code mapping `0`, `2`, `3`, `4`, `1`
- **Interfaces consumed**: `OutsideAgentAdvisoryEvidence`, `OutsideAgentAdvisoryExitCode`, `build_outside_agent_advisory_evidence()`, `serialize_outside_agent_advisory_evidence()`, `IF-0-OAMOCK-1`
- **Parallel-safe**: no; `cli.py` is a single-writer entrypoint file and must not be edited concurrently with another lane.
- **Tasks**:
  - test: Add CLI tests using temporary local JSON files for clean pass, malformed JSON, redaction violation, provenance failure, and output-file writing; assert no network, credential, or provider access is required.
  - impl: Register `outside-agent-preflight` on the existing `phase-loop` CLI entrypoint, parse a local submission path plus optional `--output`, map advisory classifications to the frozen exit codes, and print/write only serialized metadata-only advisory JSON.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_advisory_cli.py tests/test_phase_loop_runtime_boundary.py::TestPhaseLoopRuntimeBoundary::test_pyproject_console_scripts_share_cli_main -q`

### SL-2 - Advisory Evidence Fixtures And Stable Output Cases

- **Scope**: Add metadata-only fixture submissions and expected advisory evidence snapshots for producer preflight examples.
- **Owned files**: `phase-loop-runtime/tests/fixtures/outside_agent_advisory/**`, `phase-loop-runtime/tests/test_outside_agent_advisory_fixtures.py`
- **Interfaces provided**: advisory fixture cases for clean pass, malformed input classification, redaction violation, and provenance failure; stable evidence JSON examples for docs and CLI tests
- **Interfaces consumed**: `OutsideAgentAdvisoryEvidence`, `serialize_outside_agent_advisory_evidence()`, `IF-0-OAMOCK-1`
- **Parallel-safe**: yes; writes only advisory fixtures and fixture-focused tests after SL-0 freezes the advisory JSON contract.
- **Tasks**:
  - test: Add fixture tests that load every advisory fixture, run it through the SDK, and compare stable JSON fields including `authority`, `classification`, `exit_code`, typed blockers, input digest, contract pin, and metadata-only evidence refs.
  - impl: Add small metadata-only fixture submissions and expected evidence snippets; do not add canonical schema JSON, raw vector bodies, provider payloads, raw logs, secrets, or local env values except as redaction-negative metadata markers.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_advisory_fixtures.py -q`

### SL-3 - Authority Boundary Regression Tests

- **Scope**: Prove advisory SDK and CLI output cannot drift into authoritative merge or acceptance language.
- **Owned files**: `phase-loop-runtime/tests/test_outside_agent_authority_boundary.py`
- **Interfaces provided**: authority-boundary regression evidence for `IF-0-OAMOCK-1`
- **Interfaces consumed**: `phase-loop outside-agent-preflight`, advisory fixture evidence, `serialize_outside_agent_advisory_evidence()`, `IF-0-OAMOCK-1`
- **Parallel-safe**: no; this reducer consumes the SDK, CLI, and fixture outputs before asserting the authority boundary.
- **Tasks**:
  - test: Add assertions that serialized SDK output, CLI stdout, and CLI output files never contain `accepted_for_merge`, `merge_verdict`, `authoritative`, or acceptance-status fields, even for a clean advisory pass.
  - impl: Keep any authority-filtering logic in the advisory serialization layer rather than duplicating it in tests; the CLI should only emit the SDK serialization.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_authority_boundary.py -q`

### SL-4 - Producer Docs And OAMOCK Verification Reducer

- **Scope**: Document producer preflight usage and run the focused OAMOCK verification set before handing OAREAL the shared core and advisory boundary.
- **Owned files**: `docs/outside-agent-conformance.md`
- **Interfaces provided**: producer-facing advisory preflight instructions, GitHub issue/PR attachment guidance, OAMOCK phase verification evidence
- **Interfaces consumed**: `IF-0-OAMOCK-1`, `phase-loop outside-agent-preflight`, advisory SDK evidence, advisory fixtures, authority-boundary test results, OACORE shared core verification results
- **Parallel-safe**: no; this reducer depends on every producing lane before updating synthesized consumer-facing docs.
- **Tasks**:
  - test: Confirm roadmap validation passes and focused OACONTRACT/OACORE/OAMOCK tests cover contract pinning, shared core validation, advisory evidence, CLI exit codes, fixtures, and authority-boundary failures.
  - impl: Update `docs/outside-agent-conformance.md` with producer instructions for running advisory preflight locally and attaching the resulting metadata-only evidence JSON to GitHub issues or PRs, while preserving the governed-pipeline authority boundary.
  - verify: `PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v7.md && cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_contract_pin.py tests/test_outside_agent_contract_imports.py tests/test_outside_agent_contract_drift.py tests/test_outside_agent_core_api.py tests/test_outside_agent_schema_validation.py tests/test_outside_agent_provenance.py tests/test_outside_agent_redaction.py tests/test_outside_agent_vectors.py tests/test_outside_agent_advisory.py tests/test_outside_agent_advisory_cli.py tests/test_outside_agent_advisory_fixtures.py tests/test_outside_agent_authority_boundary.py -q && cd .. && git diff --check`

## Dispatch Hints

- preferred executors: `codex`
- allowed executors: `codex`
- required capabilities: `live_launch`, `structured_output`

## Execution Policy

- work-unit defaults: work-unit=`lane_execute`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`
- execute: executor=`codex`, model=`gpt-5.5`, effort=`high`, work-unit=`lane_execute`, reason=`phase-plan policy`
- SL-4: executor=`codex`, model=`gpt-5.5`, effort=`medium`, work-unit=`phase_verify`, reason=`producer docs and focused OAMOCK verification reducer`

## Execution Notes

- Execute SL-0 first so the advisory JSON contract and exit-code classifier are frozen before CLI, fixture, and authority-boundary work begins. SL-1 and SL-2 may run after SL-0 because their owned files are disjoint. Run SL-3 after CLI and fixtures, and run SL-4 last.
- Preserve OACORE as the only shared conformance decision engine. OAMOCK may classify advisory exit codes from typed blockers, but it must not alter core verdict semantics or add merge authority.
- The advisory command must be deterministic and credential-free. Do not introduce network calls, provider clients, Vercel/Supabase/GitHub calls, local env reads for secrets, or runtime dependencies on unpublished external services.
- Keep output metadata-only. Do not emit raw provider payloads, local env values, raw logs, copied canonical schema JSON, copied vector bodies, or secret-shaped values.
- Documentation sweep decision: `docs/outside-agent-conformance.md` is the consumer-facing boundary doc for this phase; `no_doc_delta` for `README.md`, `CHANGELOG.md`, and release notes because OAMOCK is not a release/package phase.
- Policy precedence is CLI/operator override, phase-plan policy, roadmap policy, `Dispatch Hints`, then registry defaults; no executor/model downgrade is allowed without explicit fallback or inherited default behavior.

## Spec Closeout Plan

- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `phase-loop-runtime/src/phase_loop_runtime/conformance/__init__.py`, `phase-loop-runtime/src/phase_loop_runtime/conformance/outside_agent_advisory.py`, `phase-loop-runtime/src/phase_loop_runtime/cli.py`, `docs/outside-agent-conformance.md`
- evidence paths: `plans/phase-plan-v7-OAMOCK.md`, `phase-loop-runtime/tests/test_outside_agent_advisory.py`, `phase-loop-runtime/tests/test_outside_agent_advisory_cli.py`, `phase-loop-runtime/tests/test_outside_agent_advisory_fixtures.py`, `phase-loop-runtime/tests/test_outside_agent_authority_boundary.py`, `phase-loop-runtime/tests/fixtures/outside_agent_advisory/**`, `docs/outside-agent-conformance.md`
- redaction posture: `metadata_only`
- downstream handling: `none`

## Verification

```bash
PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v7.md
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_advisory.py tests/test_outside_agent_core_api.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_advisory_cli.py tests/test_phase_loop_runtime_boundary.py::TestPhaseLoopRuntimeBoundary::test_pyproject_console_scripts_share_cli_main -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_advisory_fixtures.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_authority_boundary.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_contract_pin.py tests/test_outside_agent_contract_imports.py tests/test_outside_agent_contract_drift.py tests/test_outside_agent_core_api.py tests/test_outside_agent_schema_validation.py tests/test_outside_agent_provenance.py tests/test_outside_agent_redaction.py tests/test_outside_agent_vectors.py tests/test_outside_agent_advisory.py tests/test_outside_agent_advisory_cli.py tests/test_outside_agent_advisory_fixtures.py tests/test_outside_agent_authority_boundary.py -q
git diff --check
```

automation:
  suite_command: PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v7.md && cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_contract_pin.py tests/test_outside_agent_contract_imports.py tests/test_outside_agent_contract_drift.py tests/test_outside_agent_core_api.py tests/test_outside_agent_schema_validation.py tests/test_outside_agent_provenance.py tests/test_outside_agent_redaction.py tests/test_outside_agent_vectors.py tests/test_outside_agent_advisory.py tests/test_outside_agent_advisory_cli.py tests/test_outside_agent_advisory_fixtures.py tests/test_outside_agent_authority_boundary.py -q && cd .. && git diff --check

## Acceptance Criteria

- [ ] `build_outside_agent_advisory_evidence()` accepts metadata-only outside-agent submissions, invokes `validate_outside_agent_submission()`, and returns deterministic advisory evidence without network, provider, credential, or secret env access.
- [ ] `phase-loop outside-agent-preflight <submission-file> [--output <path>]` accepts a local outside-agent submission JSON file and emits the same advisory evidence JSON as the SDK serialization.
- [ ] Advisory output labels itself with `authority="advisory"`, preserves `redaction_posture="metadata_only"`, includes the core typed status/blockers, contract pin, input digest, and repo-relative evidence/provenance refs, and never emits `accepted_for_merge` or `merge_verdict`.
- [ ] CLI exit codes distinguish clean advisory pass (`0`), malformed input (`2`), redaction violation (`3`), provenance failure (`4`), and internal error (`1`) with tests for each non-internal user-facing classification.
- [ ] `phase-loop-runtime/tests/test_outside_agent_authority_boundary.py` proves clean advisory output cannot claim authoritative acceptance or merge readiness.
- [ ] `docs/outside-agent-conformance.md` shows producers how to run advisory preflight and attach metadata-only advisory evidence to GitHub issue or PR submissions while preserving governed-pipeline as the real acceptance authority.
- [ ] Focused OACONTRACT/OACORE/OAMOCK verification, roadmap validation, and `git diff --check` pass before the phase can close as `complete`.
