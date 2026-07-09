---
phase_loop_plan_version: 1
phase: OARELEASE
roadmap: specs/phase-plans-v7.md
roadmap_sha256: f2c28a7abd11f72d01ae1961951fa7e9cceb03d5c217ac1de3a469803d0fcaae
---

# OARELEASE: Release Prep And Dispatch Handoff

## Context

OARELEASE is the fifth phase for roadmap v7. Canonical `.phase-loop/` state for `specs/phase-plans-v7.md` shows OACONTRACT, OACORE, OAMOCK, and OAREAL complete, `OARELEASE` as the current unplanned phase, HEAD `7c3d3ee59d69d8cd207a4ad939a64e400b33daaa`, and clean git topology before this plan artifact is written. Legacy `.codex/phase-loop/` files are compatibility artifacts only and must not block or supersede canonical `.phase-loop/` state.

This phase prepares the release handoff for the outside-agent conformance runtime. It consumes the completed advisory preflight (`IF-0-OAMOCK-1`), real governed-pipeline validator (`IF-0-OAREAL-1`), shared core (`IF-0-OACORE-1`), and contract import pin (`IF-0-OACONTRACT-1`) surfaces, then freezes package/check evidence and downstream pin instructions. It does not publish to PyPI, cut a tag, dispatch a GitHub release workflow, edit governed-pipeline, edit Consiliency/spec, or claim production merge enforcement is live.

Completion of this phase means the maintainer has a metadata-only handoff that names the exact package version or git SHA, validator version, contract pin, vector manifest hash, package surface inventory, release-check evidence, governed-pipeline authoritative pin instructions, and outside-agent advisory usage instructions. Actual release dispatch remains maintainer-owned and separate.

## Interface Freeze Gates

- [ ] IF-0-OARELEASE-1 - Outside-agent release handoff contract: `docs/releases/outside-agent-release-handoff.md`, `README.md`, and `CHANGELOG.md` record metadata-only release preparation evidence for the outside-agent conformance runtime, including exact `phase-loop-runtime` package version from `phase-loop-runtime/pyproject.toml` and `phase_loop_runtime.__version__`, current git SHA or tag ref, validator version, `EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN` fields (`contract_package`, `contract_version`, `contract_git_sha`, `schema_version`, `verdict_schema_version`, `vector_manifest_name`, `vector_manifest_hash`, `source_owner`, `redaction_posture`), package surface inventory from a temp wheel/sdist build, focused release-check command evidence, governed-pipeline authoritative validator pinning instructions, outside-agent advisory preflight usage, and an explicit maintainer-owned publish/tag/workflow-dispatch boundary; the handoff must not contain secrets, provider payloads, local env values, raw logs, copied canonical schemas, copied vector bodies, absolute local paths, `accepted_for_merge`, `merge_verdict`, or a claim that publish/production enforcement already happened.

## Lane Index & Dependencies

SL-0 — Release surface test and version evidence
  Depends on: (none)
  Blocks: SL-1, SL-2, SL-3, SL-4
  Parallel-safe: no
SL-1 — Release checks and package inventory evidence
  Depends on: SL-0
  Blocks: SL-2, SL-3, SL-4
  Parallel-safe: no
SL-2 — Maintainer release handoff and downstream pin instructions
  Depends on: SL-0, SL-1
  Blocks: SL-3, SL-4
  Parallel-safe: no
SL-3 — Public docs release language and readiness boundary
  Depends on: SL-0, SL-1, SL-2
  Blocks: SL-4
  Parallel-safe: no
SL-4 — OARELEASE verification reducer
  Depends on: SL-0, SL-1, SL-2, SL-3
  Blocks: (none)
  Parallel-safe: no

## Lanes

### SL-0 — Release Surface Test And Version Evidence

- **Scope**: Add the focused release-surface regression that makes package identity, validator version, public conformance exports, release workflow posture, and contract/vector pin evidence machine-checkable.
- **Owned files**: `phase-loop-runtime/tests/test_outside_agent_release_surface.py`
- **Interfaces provided**: release-surface pytest coverage, package-version equality check, validator-version identity check, release workflow static guard, outside-agent public export inventory, contract/vector pin field inventory
- **Interfaces consumed**: `phase-loop-runtime/pyproject.toml (pre-existing)`, `phase-loop-runtime/src/phase_loop_runtime/__init__.py (pre-existing)`, `phase-loop-runtime/src/phase_loop_runtime/conformance/__init__.py (pre-existing)`, `phase-loop-runtime/src/phase_loop_runtime/conformance/outside_agent_pin.py (pre-existing)`, `.github/workflows/release-consistency.yml (pre-existing)`, `.github/workflows/publish-pypi.yml (pre-existing)`, `IF-0-OACONTRACT-1`, `IF-0-OACORE-1`, `IF-0-OAMOCK-1`, `IF-0-OAREAL-1`
- **Parallel-safe**: no; this lane freezes the release-surface evidence contract consumed by every later OARELEASE lane.
- **Tasks**:
  - test: Add tests that assert `phase-loop-runtime/pyproject.toml` `[project].version` equals `phase_loop_runtime.__version__`, real-validator/advisory outputs report that version as validator evidence, `phase_loop_runtime.conformance` exports advisory and real validator entry points, `EXPECTED_OUTSIDE_AGENT_CONTRACT_PIN` has the expected package/version/git-sha/schema/vector/redaction fields with a 64-character vector manifest hash, and release workflows keep tag-version/build/publish boundaries explicit without storing a repo secret token.
  - impl: Implement only the focused release-surface test file; do not bump versions, change package metadata, alter release workflows, or create release artifacts in the repo.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_release_surface.py tests/test_outside_agent_contract_pin.py tests/test_outside_agent_real_output.py tests/test_outside_agent_advisory.py -q`

### SL-1 — Release Checks And Package Inventory Evidence

- **Scope**: Run release-prep verification and collect metadata-only package inventory evidence without writing build artifacts into the repository.
- **Owned files**: none
- **Interfaces provided**: release-check command evidence, temp wheel/sdist package inventory, version/git-sha evidence, clean-worktree evidence, package surface evidence for `IF-0-OARELEASE-1`
- **Interfaces consumed**: SL-0 release-surface pytest coverage, `phase-loop-runtime/pyproject.toml (pre-existing)`, `phase-loop-runtime/src/phase_loop_runtime/__init__.py (pre-existing)`, `.github/workflows/release-consistency.yml (pre-existing)`, `.github/workflows/publish-pypi.yml (pre-existing)`, outside-agent focused pytest suites, `IF-0-OAREAL-1`
- **Parallel-safe**: no; this read-only evidence lane must run after SL-0 and before any document reduces its outputs.
- **Tasks**:
  - test: Run roadmap validation, focused outside-agent release suites, the release consistency/version checks, and a temp sdist/wheel build whose artifact names and metadata are later copied into the handoff.
  - impl: Capture only non-secret metadata for the downstream handoff: command names, pass/fail status, package version, validator version, git SHA, built artifact filenames from `/tmp/phase-loop-runtime-oarelease-dist`, contract package/version/git SHA, vector manifest name/hash, and `git status --short` output.
  - verify: `PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v7.md && cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_release_surface.py tests/test_outside_agent_contract_pin.py tests/test_outside_agent_vectors.py tests/test_outside_agent_advisory.py tests/test_outside_agent_advisory_cli.py tests/test_outside_agent_real_runtime.py tests/test_outside_agent_real_output.py tests/test_outside_agent_real_cli.py tests/test_outside_agent_real_ci.py -q && cd .. && python -m pip install --upgrade build && python -m build --sdist --wheel --outdir /tmp/phase-loop-runtime-oarelease-dist phase-loop-runtime && git status --short`

### SL-2 — Maintainer Release Handoff And Downstream Pin Instructions

- **Scope**: Write the metadata-only release handoff that maintainers, governed-pipeline, and outside-agent producers can consume without treating an agent as the publisher.
- **Owned files**: `docs/releases/outside-agent-release-handoff.md`
- **Interfaces provided**: OARELEASE release handoff document, governed-pipeline authoritative pin instructions, outside-agent advisory preflight instructions, package/check evidence summary, maintainer-owned dispatch boundary
- **Interfaces consumed**: SL-0 release-surface pytest coverage, SL-1 release-check evidence, `docs/outside-agent-conformance.md (pre-existing)`, `IF-0-OACONTRACT-1`, `IF-0-OACORE-1`, `IF-0-OAMOCK-1`, `IF-0-OAREAL-1`, `IF-0-OARELEASE-1`
- **Parallel-safe**: no; this synthesized handoff consumes all package/check evidence and is the single writer for the release handoff path.
- **Tasks**:
  - test: Add or update checks that fail if the handoff omits package version/git SHA, validator version, contract pin, vector manifest hash, release-check commands, temp build artifact inventory, governed-pipeline authoritative pin instructions, advisory preflight usage, or the explicit no-publish/no-tag/no-workflow-dispatch boundary.
  - impl: Create `docs/releases/outside-agent-release-handoff.md` with sections for package identity, validator identity, contract/vector pin, release-check evidence, package surface inventory, governed-pipeline pinning, outside-agent advisory preflight, and maintainer dispatch instructions; avoid placeholders such as pending/unknown/TBD once the phase closes.
  - verify: `rg -n "phase-loop-runtime|validator_version|contract_pin|vector_manifest_hash|governed-pipeline|outside-agent-preflight|outside-agent-validate|maintainer-owned|not published|not dispatched" docs/releases/outside-agent-release-handoff.md`

### SL-3 — Public Docs Release Language And Readiness Boundary

- **Scope**: Update public-facing release language so users can find the handoff while readiness claims stay limited to local release preparation.
- **Owned files**: `README.md`, `CHANGELOG.md`
- **Interfaces provided**: public docs release language, changelog readiness language, README handoff pointer, advisory-vs-authoritative release boundary, no-publish claim boundary
- **Interfaces consumed**: SL-0 release-surface pytest coverage, SL-1 release-check evidence, SL-2 handoff content, `docs/outside-agent-conformance.md (pre-existing)`, `IF-0-OARELEASE-1`
- **Parallel-safe**: no; README and changelog are public single-writer release surfaces and this lane depends on all producer lanes it summarizes.
- **Tasks**:
  - test: Check `CHANGELOG.md` and `README.md` mention the outside-agent release handoff, distinguish advisory preflight from governed-pipeline validator evidence, and do not claim PyPI publish, tag creation, workflow dispatch, or production merge enforcement already occurred.
  - impl: Add a concise Unreleased changelog entry for OARELEASE release prep and add a README pointer to `docs/releases/outside-agent-release-handoff.md` plus the existing outside-agent conformance doc; keep readiness wording to package/check evidence and downstream pin instructions.
  - verify: `rg -n "outside-agent|release handoff|governed-pipeline|advisory|not published|maintainer" README.md CHANGELOG.md`

### SL-4 — OARELEASE Verification Reducer

- **Scope**: Run the final OARELEASE verification set and reduce the phase evidence without writing another synthesized artifact.
- **Owned files**: none
- **Interfaces provided**: final OARELEASE verification evidence, `IF-0-OARELEASE-1`
- **Interfaces consumed**: release-surface pytest coverage, release-check command evidence, OARELEASE release handoff document, public docs release language, OACONTRACT/OACORE/OAMOCK/OAREAL focused test suites (pre-existing), roadmap validation (pre-existing)
- **Parallel-safe**: no; this terminal reducer depends on every OARELEASE producer lane before deciding the phase can close.
- **Tasks**:
  - test: Run the whole OARELEASE focused verification command, confirm no release artifact was written under repo-local `dist/`, and confirm no docs contain stale placeholder tokens for package version, git SHA, validator version, contract pin, vector manifest hash, command evidence, or dispatch status.
  - impl: Reduce command output and dirty-path ownership into the closeout only; do not edit files in this lane unless a verification failure requires a repair pass through the owning lane.
  - verify: `PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v7.md && cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_contract_pin.py tests/test_outside_agent_contract_imports.py tests/test_outside_agent_contract_drift.py tests/test_outside_agent_core_api.py tests/test_outside_agent_schema_validation.py tests/test_outside_agent_provenance.py tests/test_outside_agent_redaction.py tests/test_outside_agent_vectors.py tests/test_outside_agent_advisory.py tests/test_outside_agent_advisory_cli.py tests/test_outside_agent_advisory_fixtures.py tests/test_outside_agent_authority_boundary.py tests/test_outside_agent_real_runtime.py tests/test_outside_agent_real_output.py tests/test_outside_agent_real_cli.py tests/test_outside_agent_real_ci.py tests/test_outside_agent_release_surface.py -q && cd .. && python -m build --sdist --wheel --outdir /tmp/phase-loop-runtime-oarelease-dist phase-loop-runtime && test ! -e dist && git diff --check`

## Dispatch Hints

- preferred executors: `codex`
- allowed executors: `codex`
- required capabilities: `live_launch`, `structured_output`

## Execution Policy

- work-unit defaults: work-unit=`lane_execute`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`
- execute: executor=`codex`, model=`gpt-5.5`, effort=`high`, work-unit=`lane_execute`, reason=`phase-plan policy`
- SL-1: executor=`codex`, model=`gpt-5.5`, effort=`medium`, work-unit=`phase_verify`, reason=`release check and package inventory evidence`
- SL-4: executor=`codex`, model=`gpt-5.5`, effort=`medium`, work-unit=`phase_verify`, reason=`OARELEASE final verification reducer`

## Execution Notes

- Execute SL-0 first so the release-surface assertions exist before release checks or docs reduce their evidence. Run SL-1 after SL-0, SL-2 after SL-1, SL-3 after SL-2, and SL-4 last.
- This is a release-prep and dispatch-handoff phase, not a release-dispatch phase. Do not add `phase_loop_mutation: release_dispatch`, do not push tags, do not invoke `gh workflow run`, and do not claim PyPI publish or production enforcement is complete.
- Build wheel/sdist artifacts only under `/tmp/phase-loop-runtime-oarelease-dist` or another non-repo temp directory; repo-local `dist/` must not exist after verification.
- Keep all evidence metadata-only. Do not include secrets, provider payloads, local env values, raw logs, copied canonical schemas, copied vector bodies, absolute local paths, or acceptance/merge-verdict fields.
- Policy precedence is CLI/operator override, phase-plan policy, roadmap policy, `Dispatch Hints`, then registry defaults; no executor/model downgrade is allowed without explicit fallback or inherited default behavior.

## Spec Closeout Plan

- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `phase-loop-runtime/tests/test_outside_agent_release_surface.py`, `docs/releases/outside-agent-release-handoff.md`, `README.md`, `CHANGELOG.md`
- evidence paths: `plans/phase-plan-v7-OARELEASE.md`, `phase-loop-runtime/tests/test_outside_agent_release_surface.py`, `docs/releases/outside-agent-release-handoff.md`, `README.md`, `CHANGELOG.md`
- redaction posture: `metadata_only`
- downstream handling: `none`

## Verification

```bash
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_release_surface.py tests/test_outside_agent_contract_pin.py tests/test_outside_agent_real_output.py tests/test_outside_agent_advisory.py -q
PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v7.md
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_release_surface.py tests/test_outside_agent_contract_pin.py tests/test_outside_agent_vectors.py tests/test_outside_agent_advisory.py tests/test_outside_agent_advisory_cli.py tests/test_outside_agent_real_runtime.py tests/test_outside_agent_real_output.py tests/test_outside_agent_real_cli.py tests/test_outside_agent_real_ci.py -q
python -m pip install --upgrade build
python -m build --sdist --wheel --outdir /tmp/phase-loop-runtime-oarelease-dist phase-loop-runtime
rg -n "phase-loop-runtime|validator_version|contract_pin|vector_manifest_hash|governed-pipeline|outside-agent-preflight|outside-agent-validate|maintainer-owned|not published|not dispatched" docs/releases/outside-agent-release-handoff.md
rg -n "outside-agent|release handoff|governed-pipeline|advisory|not published|maintainer" README.md CHANGELOG.md
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_contract_pin.py tests/test_outside_agent_contract_imports.py tests/test_outside_agent_contract_drift.py tests/test_outside_agent_core_api.py tests/test_outside_agent_schema_validation.py tests/test_outside_agent_provenance.py tests/test_outside_agent_redaction.py tests/test_outside_agent_vectors.py tests/test_outside_agent_advisory.py tests/test_outside_agent_advisory_cli.py tests/test_outside_agent_advisory_fixtures.py tests/test_outside_agent_authority_boundary.py tests/test_outside_agent_real_runtime.py tests/test_outside_agent_real_output.py tests/test_outside_agent_real_cli.py tests/test_outside_agent_real_ci.py tests/test_outside_agent_release_surface.py -q
cd .. && test ! -e dist && git diff --check
```

automation:
  suite_command: PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v7.md && cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_contract_pin.py tests/test_outside_agent_contract_imports.py tests/test_outside_agent_contract_drift.py tests/test_outside_agent_core_api.py tests/test_outside_agent_schema_validation.py tests/test_outside_agent_provenance.py tests/test_outside_agent_redaction.py tests/test_outside_agent_vectors.py tests/test_outside_agent_advisory.py tests/test_outside_agent_advisory_cli.py tests/test_outside_agent_advisory_fixtures.py tests/test_outside_agent_authority_boundary.py tests/test_outside_agent_real_runtime.py tests/test_outside_agent_real_output.py tests/test_outside_agent_real_cli.py tests/test_outside_agent_real_ci.py tests/test_outside_agent_release_surface.py -q && cd .. && python -m build --sdist --wheel --outdir /tmp/phase-loop-runtime-oarelease-dist phase-loop-runtime && test ! -e dist && git diff --check

## Acceptance Criteria

- [ ] `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_outside_agent_release_surface.py -q` passes and proves package version, validator version, conformance exports, release workflow posture, and contract/vector pin fields are machine-checkable.
- [ ] `python -m build --sdist --wheel --outdir /tmp/phase-loop-runtime-oarelease-dist phase-loop-runtime` succeeds, the package inventory is reduced into `docs/releases/outside-agent-release-handoff.md`, and no repo-local `dist/` path remains.
- [ ] `docs/releases/outside-agent-release-handoff.md` names exact package version or git SHA, validator version, contract pin, vector manifest hash, release-check commands, package surface inventory, governed-pipeline authoritative pin instructions, outside-agent advisory usage, and maintainer-owned dispatch boundaries.
- [ ] `README.md` and `CHANGELOG.md` point to the release handoff and distinguish advisory availability, governed-pipeline validator evidence, and not-yet-dispatched publish/production enforcement.
- [ ] The final focused OACONTRACT/OACORE/OAMOCK/OAREAL/OARELEASE pytest command, roadmap validation, temp package build, stale-doc check, and `git diff --check` pass before the phase can close as `complete`.
- [ ] OARELEASE closeout lists `IF-0-OARELEASE-1` in `produced_if_gates` and does not claim `terminal_status=complete` unless required verification passed and all active-plan owned dirty paths are accounted for.
