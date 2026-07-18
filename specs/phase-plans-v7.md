# Phase roadmap v7 - Outside-Agent Conformance Runtime

## Context

This roadmap is the Consiliency/agent-harness half of the outside-agent production program. Agent-harness owns shared conformance machinery that can be consumed by governed-pipeline as an authoritative merge fence and by outside-agent producers as an advisory preflight. It does not own the canonical contract, the merge verdict, or Portal projection truth.

Companion roadmaps:

- `Consiliency/spec/specs/phase-plans-v3.md` - canonical outside-agent contract and vector corpus.
- `Consiliency/governed-pipeline/specs/phase-plans-v33.md` - coordinator roadmap, GitHub intake, merge fence, ambiguity routing, review path, production bake.
- `Consiliency/consiliency-portal/specs/phase-plans-v51.md` - digest-bound Portal projection and operator UX.

## Architecture North Star

Agent-harness supplies one conformance core with two consumers:

- advisory mock/preflight for producers and outside agents
- real validator runtime for governed-pipeline CI and review

The advisory path can explain readiness and catch cheap mistakes. The real path is the only validator governed-pipeline may use for acceptance. Both paths consume the same schema and vector corpus pinned from Consiliency/spec. The existing `phase_loop_runtime.conformance` surface is not assumed sufficient for this program; this roadmap builds the outside-agent submission/vector validator explicitly.

## Assumptions

1. Consiliency/spec publishes the canonical outside-agent schemas and conformance vectors before this roadmap can close its pinning phase.
2. Governed-pipeline remains the acceptance authority and consumes the real validator output.
3. Advisory mock output is evidence only. It cannot become an acceptance or merge verdict.
4. The conformance runtime must be deterministic and usable without external provider credentials.
5. Secret values, provider payloads, local env values, and raw logs are never emitted by the validator.

## Non-Goals

- No canonical schema invention in this repo.
- No governed-pipeline CI workflow changes.
- No Portal status UI or projection ingestion.
- No credentialed package publish by an agent.
- No outside-agent identity provider integration.

## Cross-Cutting Principles

1. One validator core, two roles: advisory and authoritative.
2. Same vectors everywhere. Producer preflight and merge fence must disagree only by authority, not by schema semantics.
3. Metadata-only inputs and outputs.
4. Fail closed on unknown schema version, unknown route verdict, missing digest, stale source refs, and raw payload presence.
5. Keep release preparation separate from release dispatch.

## Top Interface-Freeze Gates

- IF-0-OACONTRACT-1 - spec contract import pin: schema version, vector manifest hash, and package/git sha.
- IF-0-OACORE-1 - shared conformance core API: deterministic inputs, typed verdict, typed blockers, redaction posture, and provenance refs.
- IF-0-OAMOCK-1 - advisory mock CLI/SDK: producer-facing preflight that cannot emit authoritative acceptance.
- IF-0-OAREAL-1 - real validator runtime surface: governed-pipeline-consumable CLI/API, stable exit codes, JSON verdict, and validator CI vector evidence.
- IF-0-OARELEASE-1 - release handoff: package/check evidence and downstream pin instructions.

## Phases

### Phase 1 - Contract Import Pin (OACONTRACT)

**Objective**

Pin the canonical outside-agent contract and vector corpus from Consiliency/spec without copying or redefining schema truth.

**Exit criteria**
- [ ] Import metadata records schema version, package version or git sha, and vector manifest hash.
- [ ] Unknown or missing contract version fails closed.
- [ ] Local fixtures prove this repo does not silently accept drift from the spec package.
- [ ] Docs state that Consiliency/spec owns contract truth and agent-harness consumes it.

**Scope notes**

Parallel lanes:
- **SL-PIN** owns package/git pin metadata.
- **SL-IMPORT** owns import helpers and version checks.
- **SL-DRIFT** owns negative drift fixtures.
- **SL-DOCS** owns consumer-facing contract language.

**Non-goals**

No validator implementation beyond import/version checks.

**Key files**

- `phase-loop-runtime/src/phase_loop_runtime/conformance/`
- `phase-loop-runtime/tests/`
- `docs/`

**Spec closeout policy**

- schema: `spec_delta_closeout.v1`
- expected_decision: `no_spec_delta`
- target_surfaces: conformance import pin and docs
- evidence_paths: contract pin file, import tests, drift fixture output
- redaction_posture: `metadata_only`
- blocker_class: `contract_bug` for copied schema truth or unpinned contract input

**Depends on**

- (none)

**Produces**

- IF-0-OACONTRACT-1

### Phase 2 - Shared Conformance Core (OACORE)

**Objective**

Build the pure deterministic validator core over the pinned contract and vector corpus.

**Exit criteria**
- [ ] Core validates all outside-agent submission kinds: `work_request`, `implementation_submission`, and `ambiguity_report`.
- [ ] Core emits typed verdicts and blockers without external network or credential access.
- [ ] Positive and negative vectors from spec pass with expected outcomes.
- [ ] Outputs include only metadata, digests, repo-relative refs, and typed failure information.
- [ ] Unknown fields, unsupported versions, absolute paths, missing digests, raw payloads, and path traversal fail closed.

**Scope notes**

Parallel lanes:
- **SL-SCHEMA** owns schema validation.
- **SL-VECTORS** owns vector runner and expected-outcome checks.
- **SL-REDACTION** owns metadata-only output assertions.
- **SL-PROVENANCE** owns source refs and digest checks.

These lanes can run concurrently after OACONTRACT pins field names and vector manifest shape.

**Non-goals**

No advisory CLI and no governed-pipeline adapter.

**Key files**

- `phase-loop-runtime/src/phase_loop_runtime/conformance/`
- `phase-loop-runtime/tests/test_*conformance*.py`
- `docs/`

**Spec closeout policy**

- schema: `spec_delta_closeout.v1`
- expected_decision: `no_spec_delta`
- target_surfaces: shared conformance core and vector runner
- evidence_paths: vector test output, redaction tests, provenance tests
- redaction_posture: `metadata_only`
- blocker_class: `contract_bug` for accepting unsafe refs or failing to run canonical vectors

**Depends on**

- OACONTRACT

**Produces**

- IF-0-OACORE-1

### Phase 3 - Actor-Side Advisory Mock (OAMOCK)

**Objective**

Expose a producer-facing advisory preflight that helps outside agents prepare submissions without granting acceptance authority.

**Exit criteria**
- [ ] CLI or SDK accepts a local outside-agent submission file and runs the shared conformance core.
- [ ] Output labels itself as advisory evidence, not authoritative acceptance.
- [ ] Exit codes distinguish clean advisory pass, malformed input, redaction violation, provenance failure, and internal error.
- [ ] Docs show how producers attach advisory evidence to GitHub issue/PR submissions.
- [ ] Tests prove advisory output cannot emit `accepted_for_merge`.

**Scope notes**

Parallel lanes:
- **SL-CLI** owns command shape and exit codes.
- **SL-EVIDENCE** owns advisory evidence JSON.
- **SL-DOCS** owns producer instructions.
- **SL-AUTHORITY** owns tests preventing authority language drift.

Can run in parallel with OAREAL after OACORE.

**Non-goals**

No governed-pipeline CI wiring and no Portal projection.

**Key files**

- phase-loop CLI entrypoint files
- `phase-loop-runtime/src/phase_loop_runtime/conformance/`
- `phase-loop-runtime/tests/`
- `docs/`

**Spec closeout policy**

- schema: `spec_delta_closeout.v1`
- expected_decision: `no_spec_delta`
- target_surfaces: advisory conformance CLI/SDK and docs
- evidence_paths: CLI tests, advisory output fixtures
- redaction_posture: `metadata_only`
- blocker_class: `contract_bug` for advisory output claiming acceptance authority

**Depends on**

- OACORE

**Produces**

- IF-0-OAMOCK-1

### Phase 4 - Real Validator Runtime Surface (OAREAL)

**Objective**

Expose the authoritative validator surface that governed-pipeline can pin and run in CI.

**Exit criteria**
- [ ] Runtime interface is stable enough for governed-pipeline pinning: command/API name, input path, output path, exit codes, and JSON verdict shape.
- [ ] Runtime evaluates the specific submitted refs passed by governed-pipeline; canonical vectors are run in agent-harness CI/release checks, not on every live submission.
- [ ] Output includes validator version, contract pin, vector manifest hash, input digest, typed verdict, typed blockers, and metadata-only evidence paths.
- [ ] Tests cover CI-style invocation and fail-closed behavior for malformed or unsafe submissions.
- [ ] Docs distinguish this real validator from the advisory mock.

**Scope notes**

Parallel lanes:
- **SL-RUNTIME** owns command/API surface.
- **SL-OUTPUT** owns JSON verdict shape.
- **SL-CI-FIXTURES** owns CI-style fixture invocation.
- **SL-DOCS** owns downstream pin guidance.

Can run in parallel with OAMOCK after OACORE.

**Non-goals**

No governed-pipeline workflow edits in this repo.

**Key files**

- phase-loop CLI entrypoint files
- `phase-loop-runtime/src/phase_loop_runtime/conformance/`
- `phase-loop-runtime/tests/`
- `docs/`

**Spec closeout policy**

- schema: `spec_delta_closeout.v1`
- expected_decision: `governed_pipeline_refresh`
- target_surfaces: real validator runtime and output verdict
- evidence_paths: runtime tests, CI vector run output, docs
- redaction_posture: `metadata_only`
- blocker_class: `contract_bug` for missing pin metadata or non-deterministic output

**Depends on**

- OACORE

**Produces**

- IF-0-OAREAL-1

### Phase 5 - Release Prep And Dispatch Handoff (OARELEASE)

**Objective**

Prepare a maintainer-owned release handoff so governed-pipeline can pin the real validator and outside agents can use the advisory preflight.

**Exit criteria**
- [ ] Release checks and package surface inventory pass.
- [ ] Handoff names exact package version or git sha, validator version, contract pin, and vector manifest hash.
- [ ] Downstream instructions cover governed-pipeline authoritative pinning and outside-agent advisory usage.
- [ ] Publish dispatch remains maintainer-owned and is not claimed complete until performed.
- [ ] Changelog distinguishes advisory availability from production merge enforcement.

**Scope notes**

Parallel lanes:
- **SL-CHECKS** owns package/release verification.
- **SL-HANDOFF** owns downstream pin instructions.
- **SL-CHANGELOG** owns release notes and readiness language.

**Non-goals**

No credentialed publish by an agent.

**Key files**

- package/release manifests
- `CHANGELOG.md`
- `docs/`

**Spec closeout policy**

- schema: `spec_delta_closeout.v1`
- expected_decision: `no_spec_delta`
- target_surfaces: release handoff, package surface, changelog
- evidence_paths: release check output, package manifest, handoff docs
- redaction_posture: `metadata_only`
- blocker_class: `contract_bug` for claiming a release before maintainer dispatch

**Depends on**

- OAMOCK
- OAREAL

**Produces**

- IF-0-OARELEASE-1

## Phase Dependency DAG

```
OACONTRACT
  --> OACORE
  --> OAMOCK
  --> OARELEASE
OACORE
  --> OAREAL
  --> OARELEASE
```

## Execution Notes

- OACONTRACT can begin as soon as the spec roadmap freezes an initial schema/vector alias.
- OACORE splits cleanly across schema, vector, redaction, and provenance lanes.
- OAMOCK and OAREAL should run in parallel after OACORE because they share the core but have different authority posture.
- OARELEASE must not close until both advisory and real surfaces have explicit downstream pin evidence.

## Verification

```bash
phase-loop validate-roadmap specs/phase-plans-v7.md
python -m pytest phase-loop-runtime/tests/test_*conformance*.py
# phase-level plans should add the repo-local tests once file names are finalized
```
