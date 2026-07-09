---
phase_loop_plan_version: 1
phase: OACONTRACT
roadmap: specs/phase-plans-v7.md
roadmap_sha256: f2c28a7abd11f72d01ae1961951fa7e9cceb03d5c217ac1de3a469803d0fcaae
---

# OACONTRACT: Contract Import Pin

## Context

OACONTRACT is the first phase for roadmap v7. It pins the canonical outside-agent schema and vector manifest published by Consiliency/spec so later agent-harness phases can build the advisory mock and real validator over the same contract input.

Agent-harness does not own outside-agent schema truth, merge authority, or Portal projection truth. This phase may record contract metadata, package/git identity, and vector manifest digest, but it must not copy schema JSON or redefine outside-agent submission semantics locally.

The current runtime already exposes `phase_loop_runtime.conformance` as a stable single-file module for the existing `.consiliency/` gate surface. Because roadmap v7 names a future conformance package, this phase includes a small compatibility preamble that preserves the existing public imports while creating room for outside-agent pin modules under `phase_loop_runtime.conformance.*`.

Canonical runner state exists under `.phase-loop/`; legacy `.codex/phase-loop/` files are compatibility-only and must not supersede canonical `.phase-loop/` state.

## Interface Freeze Gates

- [ ] IF-0-OACONTRACT-1 - Outside-agent contract import pin: `phase_loop_runtime.conformance` remains backward-compatible for the existing `.consiliency/` gate API, while `phase_loop_runtime.conformance.outside_agent_pin.OutsideAgentContractPin` records `schema_version`, `verdict_schema_version`, `contract_package`, `contract_version`, `contract_git_sha`, `vector_manifest_hash`, `vector_manifest_name`, `source_owner`, and `redaction_posture`; `phase_loop_runtime.conformance.outside_agent_imports.load_outside_agent_contract_pin()` fails closed on missing or unknown contract version and vector manifest digest drift, and loads schema/vector truth only from the pinned `consiliency_spec` package or immutable Consiliency/spec git checkout rather than copied JSON in this repo.

## Lane Index & Dependencies

SL-0 — Conformance package compatibility preamble
  Depends on: (none)
  Blocks: SL-1, SL-2, SL-3, SL-4
  Parallel-safe: no
SL-1 — Spec package pin metadata
  Depends on: SL-0
  Blocks: SL-2, SL-3, SL-4
  Parallel-safe: yes
SL-2 — Import helpers and version checks
  Depends on: SL-0, SL-1
  Blocks: SL-3, SL-4
  Parallel-safe: yes
SL-3 — Negative drift fixtures
  Depends on: SL-0, SL-1, SL-2
  Blocks: SL-4
  Parallel-safe: yes
SL-4 — Consumer docs and phase verification reducer
  Depends on: SL-0, SL-1, SL-2, SL-3
  Blocks: (none)
  Parallel-safe: no

## Lanes

### SL-0 — Conformance Package Compatibility Preamble

- **Scope**: Convert the existing `phase_loop_runtime.conformance` module into a package-compatible import surface without changing the existing `.consiliency/` gate behavior.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/conformance.py`, `phase-loop-runtime/src/phase_loop_runtime/conformance/__init__.py`
- **Interfaces provided**: backward-compatible `phase_loop_runtime.conformance` imports, `scan_consiliency_gates`, `resolve_consiliency_gates_mode`, `CONSILIENCY_GATES_ENV`, `CONSILIENCY_GATES_MODES`, `DEFAULT_CONSILIENCY_GATES_MODE`, `evaluate_git_discipline`, `self_heal_partition`, `evaluate_governance_scope`
- **Interfaces consumed**: (pre-existing) `phase_loop_runtime.consiliency_gates`, (pre-existing) `phase_loop_runtime.consiliency_ingest`, (pre-existing) `phase_loop_runtime.git_discipline`
- **Parallel-safe**: no; this is the single writer for the import-surface migration needed by all later OACONTRACT lanes.
- **Tasks**:
  - test: Re-run the existing named entrypoint tests in `phase-loop-runtime/tests/test_consiliency_ingest.py` before and after the package migration to prove public imports still bind to the identical implementation functions.
  - impl: Replace the single-file `conformance.py` surface with `conformance/__init__.py` that preserves the existing re-export contract and does not add outside-agent validator semantics.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_consiliency_ingest.py::ConformanceNamedLibraryTest -q`

### SL-1 — Spec Package Pin Metadata

- **Scope**: Record the exact outside-agent contract identity and vector manifest digest consumed from Consiliency/spec without vendoring schema or vector truth into agent-harness.
- **Owned files**: `phase-loop-runtime/pyproject.toml`, `phase-loop-runtime/src/phase_loop_runtime/conformance/outside_agent_pin.py`, `phase-loop-runtime/tests/test_outside_agent_contract_pin.py`
- **Interfaces provided**: `OutsideAgentContractPin`, `EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN`, `IF-0-OACONTRACT-1` pin fields
- **Interfaces consumed**: Consiliency/spec git SHA and the `consiliency_spec` package data surface (`schemas/outside-agent-*.schema.json`, `test-vectors/outside-agent/manifest.json`)
- **Parallel-safe**: yes; writes only the dependency/pin metadata surface and its focused tests after SL-0 creates the package namespace.
- **Tasks**:
  - test: Add assertions that the expected pin records a schema version, a package version or git sha, a vector manifest name, a vector manifest sha256, `source_owner="Consiliency/spec"`, and `redaction_posture="metadata_only"`.
  - impl: Add a frozen pin metadata module for `consiliency-spec` / Consiliency/spec git SHA. Do not tighten the existing `consiliency-contract` dependency for outside-agent files; that package remains the `.consiliency/` gate contract, not the outside-agent schema package.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_contract_pin.py -q`

### SL-2 — Import Helpers And Version Checks

- **Scope**: Add the minimal import-time helper that reads the canonical package metadata/vector manifest and fails closed on missing, unknown, or drifted contract identity.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/conformance/outside_agent_imports.py`, `phase-loop-runtime/tests/test_outside_agent_contract_imports.py`
- **Interfaces provided**: `load_outside_agent_contract_pin()`, `OutsideAgentContractError`, typed error codes `missing_contract`, `unknown_contract_version`, `vector_manifest_hash_mismatch`
- **Interfaces consumed**: `OutsideAgentContractPin`, `EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN`, `consiliency_spec` package resources when installed, `OUTSIDE_AGENT_SPEC_ROOT` / explicit Consiliency/spec checkout path during pre-release trains, package metadata APIs
- **Parallel-safe**: yes; writes only import helpers and import-check tests after consuming the SL-1 pin.
- **Tasks**:
  - test: Add import-helper tests that simulate missing package metadata, unknown schema version, and vector manifest hash mismatch without network access or provider credentials.
  - impl: Implement the import helper so it returns the expected pin only after either the installed `consiliency_spec` package or a pre-release Consiliency/spec checkout matches the frozen git/package identity and vector manifest digest; it must raise `OutsideAgentContractError` instead of silently accepting drift.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_contract_imports.py -q`

### SL-3 — Negative Drift Fixtures

- **Scope**: Prove local fixtures cannot make this repo accept stale, unknown, or copied outside-agent contract truth.
- **Owned files**: `phase-loop-runtime/tests/fixtures/outside_agent_contract_drift/**`, `phase-loop-runtime/tests/test_outside_agent_contract_drift.py`
- **Interfaces provided**: OACONTRACT drift fixture evidence
- **Interfaces consumed**: `OutsideAgentContractPin`, `load_outside_agent_contract_pin()`, `OutsideAgentContractError`, `IF-0-OACONTRACT-1`
- **Parallel-safe**: yes; writes only drift fixtures and drift tests after import checks exist.
- **Tasks**:
  - test: Add negative fixtures for missing contract version, unknown future schema version, vector manifest digest mismatch, and local copied-schema attempts.
  - impl: Keep fixtures metadata-only; do not add full canonical schemas, raw provider payloads, or copied outside-agent vector bodies to this repo.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_contract_drift.py -q`

### SL-4 — Consumer Docs And Phase Verification Reducer

- **Scope**: Document the contract ownership boundary for downstream consumers and run the focused OACONTRACT verification set.
- **Owned files**: `docs/outside-agent-conformance.md`
- **Interfaces provided**: phase closeout evidence for `IF-0-OACONTRACT-1`, consumer-facing contract ownership language
- **Interfaces consumed**: `IF-0-OACONTRACT-1`, OACONTRACT pin test results, OACONTRACT import-helper test results, OACONTRACT drift fixture results
- **Parallel-safe**: no; this reducer depends on every producing lane before writing the synthesized consumer-facing doc.
- **Tasks**:
  - test: Confirm roadmap validation still passes and focused OACONTRACT tests cover compatibility, pin metadata, import checks, and drift failures.
  - impl: Add a consumer-facing doc that states Consiliency/spec owns outside-agent contract truth, agent-harness consumes a pinned metadata/digest view, advisory output is never acceptance authority, and the real governed-pipeline validator must rerun against the same pin.
  - verify: `PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v7.md && cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_consiliency_ingest.py::ConformanceNamedLibraryTest tests/test_outside_agent_contract_pin.py tests/test_outside_agent_contract_imports.py tests/test_outside_agent_contract_drift.py -q`

## Dispatch Hints

- preferred executors: `codex`
- allowed executors: `codex`
- required capabilities: `live_launch`, `structured_output`

## Execution Policy

- work-unit defaults: work-unit=`lane_execute`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`
- execute: executor=`codex`, model=`gpt-5.5`, effort=`high`, work-unit=`lane_execute`, reason=`phase-plan policy`
- SL-4: executor=`codex`, model=`gpt-5.5`, effort=`medium`, work-unit=`phase_verify`, reason=`consumer docs and focused phase verification reducer`

## Execution Notes

- Execute SL-0 first because Python cannot have both `phase_loop_runtime/conformance.py` and `phase_loop_runtime/conformance/` as the import owner.
- Preserve the existing `.consiliency/` gate API exactly; outside-agent additions live in submodules and do not change current gate semantics.
- If Consiliency/spec has not published the outside-agent package yet, validate the train-pinned Consiliency/spec git checkout by immutable SHA and vector manifest hash. Stop with `blocker_class=upstream_phase_unmet`, `human_required=false`, only when neither a matching checkout nor a matching installed `consiliency_spec` package is available.
- Do not copy canonical schema JSON, vector bodies, raw provider payloads, local env values, or secrets into agent-harness. OACONTRACT stores metadata and hashes only.
- Documentation sweep decision: `docs/outside-agent-conformance.md` is the consumer-facing boundary doc for this phase; no changelog edits are required.

## Spec Closeout Plan

- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `phase-loop-runtime/src/phase_loop_runtime/conformance.py`, `phase-loop-runtime/src/phase_loop_runtime/conformance/__init__.py`, `phase-loop-runtime/src/phase_loop_runtime/conformance/outside_agent_pin.py`, `phase-loop-runtime/src/phase_loop_runtime/conformance/outside_agent_imports.py`, `phase-loop-runtime/pyproject.toml`, `docs/outside-agent-conformance.md`
- evidence paths: `plans/phase-plan-v7-OACONTRACT.md`, `phase-loop-runtime/tests/test_consiliency_ingest.py`, `phase-loop-runtime/tests/test_outside_agent_contract_pin.py`, `phase-loop-runtime/tests/test_outside_agent_contract_imports.py`, `phase-loop-runtime/tests/test_outside_agent_contract_drift.py`, `phase-loop-runtime/tests/fixtures/outside_agent_contract_drift/**`
- redaction posture: `metadata_only`
- downstream handling: `none`

## Verification

```bash
PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v7.md
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_consiliency_ingest.py::ConformanceNamedLibraryTest -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_contract_pin.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_contract_imports.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_contract_drift.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_consiliency_ingest.py::ConformanceNamedLibraryTest tests/test_outside_agent_contract_pin.py tests/test_outside_agent_contract_imports.py tests/test_outside_agent_contract_drift.py -q
```

automation:
  suite_command: cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_consiliency_ingest.py::ConformanceNamedLibraryTest tests/test_outside_agent_contract_pin.py tests/test_outside_agent_contract_imports.py tests/test_outside_agent_contract_drift.py -q

## Acceptance Criteria

- [ ] `phase_loop_runtime.conformance` remains import-compatible with the existing `.consiliency/` named library tests after the package migration.
- [ ] `phase-loop-runtime/tests/test_outside_agent_contract_pin.py` asserts import metadata records outside-agent schema version, verdict schema version, package version or git sha, vector manifest name, vector manifest sha256, source owner, and metadata-only redaction posture.
- [ ] `phase-loop-runtime/tests/test_outside_agent_contract_imports.py` asserts the import helper fails closed for missing contract metadata, unknown schema version, and vector manifest digest mismatch.
- [ ] `phase-loop-runtime/tests/test_outside_agent_contract_drift.py` proves agent-harness does not silently accept stale, unknown, or copied outside-agent schema/vector truth.
- [ ] `phase-loop-runtime/tests/test_outside_agent_contract_drift.py` or a focused `rg` check proves the repo contains no copied canonical outside-agent schema JSON or raw vector corpus outside metadata-only negative fixtures.
- [ ] `docs/outside-agent-conformance.md` states that Consiliency/spec owns contract truth and agent-harness consumes a pinned metadata/digest view.
- [ ] `docs/outside-agent-conformance.md` states advisory output remains explicitly non-authoritative and governed-pipeline remains the real acceptance authority.
- [ ] Focused OACONTRACT verification and roadmap validation pass before the phase can close as `complete`.
