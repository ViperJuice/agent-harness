---
phase_loop_plan_version: 1
phase: CTXFREEZE
roadmap: specs/phase-plans-v6.md
roadmap_sha256: 439d6e51ea362d8464e738f221c2cf5c65eb82be85c7d74ea369b4c1a8a95e10
---

# CTXFREEZE: Contract Audit And Freeze

## Context

CTXFREEZE is the serial first phase for roadmap v6. The current branch already contains candidate #114 work for `context_refs`, `artifact_ref`/`brief_ref`, Gemini retry-once behavior, and per-leg timeout overrides, but this phase treats that work as untrusted until the contract is frozen and the candidate behavior is audited against it.

The phase is contract-first. Runtime behavior changes are out of scope except for comments or docstrings needed to keep the frozen public contract accurate. The executor should correct `CONTRACTS.md`, align narrow contract tests with the frozen semantics, and leave implementation cleanup for CTXIMPL or CTXRELY unless a change is purely documentation in an already-owned file.

Canonical runner state exists under `.phase-loop/`; legacy `.codex/phase-loop/` files are compatibility-only and must not supersede canonical `.phase-loop/` state.

## Interface Freeze Gates

- [ ] IF-0-CTXFREEZE-1 - Ingestion API contract: `artifact`, `artifact_ref`, `brief_ref`, and true by-reference `context_refs` semantics are frozen for `invoke_panel`, `invoke_board`, and `PanelRequest` / `invoke_panel_request`, including precedence, default fail-closed behavior, and explicit soft-warning opt-in.
- [ ] IF-0-CTXFREEZE-2 - Context-ref manifest contract: metadata fields, deterministic ordering, contents-only non-inlining claim, relative-path base, path normalization, root/symlink/TOCTOU policy, non-regular-file rejection, structured escaping, bounded metadata extraction, untrusted MIME/extension labeling, optional PDF page count, and local-tool instruction text are frozen.
- [ ] IF-0-CTXFREEZE-3 - Panel reliability contract: per-leg timeout override names, request threading, Gemini/agy transient retry-once behavior, elapsed retry guard, and non-retry hard-timeout semantics are frozen, with any CTXRELY follow-on split explicitly listed.

## Lane Index & Dependencies

SL-0 — Contract surface freeze
  Depends on: (none)
  Blocks: SL-1, SL-2, SL-3
  Parallel-safe: no
SL-1 — Context-ref manifest contract tests
  Depends on: SL-0
  Blocks: SL-3
  Parallel-safe: yes
SL-2 — Entry-point and reliability contract tests
  Depends on: SL-0
  Blocks: SL-3
  Parallel-safe: yes
SL-3 — Documentation sweep, branch audit, and phase verification reducer
  Depends on: SL-0, SL-1, SL-2
  Blocks: (none)
  Parallel-safe: no

## Lanes

### SL-0 — Contract Surface Freeze

- **Scope**: Freeze the public #114 ingestion, manifest, and reliability contract in the canonical advisor-board contract surface without implementing runtime cleanup.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py`, `phase-loop-runtime/src/phase_loop_runtime/advisor_board/CONTRACTS.md`
- **Interfaces provided**: `IF-0-CTXFREEZE-1`, `IF-0-CTXFREEZE-2`, `IF-0-CTXFREEZE-3`, `PanelRequest.context_refs`, `PanelRequest.context_refs_soft_warn`, `PanelRequest.timeout_seconds_by_leg`, `invoke_panel(..., context_refs=..., context_refs_soft_warn=..., timeouts_by_leg=...)`, `invoke_board(..., context_refs=..., context_refs_soft_warn=..., timeouts_by_leg=...)`, `invoke_panel_request(request)`
- **Interfaces consumed**: `PanelRequest`, `invoke_panel`, `invoke_board`, `_resolve_artifact`, `_resolve_brief`, `_render_context_refs_manifest`, `_exec_leg` (pre-existing)
- **Parallel-safe**: no; this is the single writer for the public contract and any code-level comments/docstrings.
- **Tasks**:
  - test: Inspect the current branch contract comments and `CONTRACTS.md` for any statement that describes `artifact_ref` or `brief_ref` as true non-inlining by-reference ingestion.
  - impl: Add or correct the CTXFREEZE contract section so it distinguishes inline artifact text, read-file-and-stage refs, and true metadata-only `context_refs`; record provider/local-filesystem limitations and the chosen output-boundary claim.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m py_compile src/phase_loop_runtime/panel_invoker.py`

### SL-1 — Context-Ref Manifest Contract Tests

- **Scope**: Freeze the metadata-only context-ref manifest and filesystem boundary with focused tests that fail before any implementation is trusted.
- **Owned files**: `phase-loop-runtime/tests/test_panel_context_refs_114.py`
- **Interfaces provided**: `CTXFREEZE context-ref test results`
- **Interfaces consumed**: `IF-0-CTXFREEZE-1`, `IF-0-CTXFREEZE-2`
- **Parallel-safe**: yes; writes only the context-ref contract test file after consuming SL-0.
- **Tasks**:
  - test: Add or align assertions for sentinel absence, path and metadata presence, deterministic multi-file order, missing-path fail-closed behavior, soft-warning unreadable entries, JSON-escaped hostile filenames, optional PDF page count, and heterogeneous missing metadata.
  - impl: Keep tests contract-shaped; do not repair runtime behavior in this lane except by documenting an explicit mismatch for CTXIMPL.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_panel_context_refs_114.py -q`

### SL-2 — Entry-Point And Reliability Contract Tests

- **Scope**: Freeze request threading and reliability naming so downstream implementation work cannot drift between `timeouts_by_leg`, `timeout_seconds_by_leg`, retry-once, and hard-timeout behavior.
- **Owned files**: `phase-loop-runtime/tests/test_panel_invoker.py`
- **Interfaces provided**: `CTXFREEZE entry-point reliability test results`
- **Interfaces consumed**: `IF-0-CTXFREEZE-1`, `IF-0-CTXFREEZE-3`
- **Parallel-safe**: yes; writes only the entry-point contract test file after consuming SL-0.
- **Tasks**:
  - test: Add or align assertions for `PanelRequest` field names, request-to-entry-point threading, artifact/artifact_ref precedence, brief_ref inclusion or explicit exclusion, per-leg timeout override naming, and non-retry hard-timeout expectations.
  - impl: Keep runtime edits out of this lane; if current behavior diverges, record the mismatch for CTXIMPL or CTXRELY instead of silently changing behavior.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_panel_invoker.py -q`

### SL-3 — Documentation Sweep, Branch Audit, And Phase Verification Reducer

- **Scope**: Compare the candidate #114 branch behavior against the frozen gates, decide whether reliability work remains in CTXRELY or is split to follow-on scope, and run the phase proof set.
- **Owned files**: none
- **Interfaces provided**: phase closeout evidence for `IF-0-CTXFREEZE-1`, `IF-0-CTXFREEZE-2`, `IF-0-CTXFREEZE-3`
- **Interfaces consumed**: `IF-0-CTXFREEZE-1`, `IF-0-CTXFREEZE-2`, `IF-0-CTXFREEZE-3`, `CTXFREEZE context-ref test results`, `CTXFREEZE entry-point reliability test results`
- **Parallel-safe**: no; reducer depends on every producing lane.
- **Tasks**:
  - test: Diff the candidate branch against `origin/main` for the key files and list any contract mismatches, missing tests, or follow-on-only reliability items in the lane closeout.
  - impl: Do not write synthesized docs in this reducer; preserve mismatch details in the closeout/handoff evidence so CTXIMPL, CTXRELY, and CTXDOCS consume the same frozen gates.
  - verify: `PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v6.md && cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_panel_context_refs_114.py tests/test_panel_invoker.py tests/test_advisor_board_golden.py tests/test_panel_invoker_spawn.py -q`

## Dispatch Hints

- preferred executors: `codex`
- allowed executors: `codex`
- required capabilities: `live_launch`, `structured_output`

## Execution Policy

- work-unit defaults: work-unit=`lane_execute`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`
- execute: executor=`codex`, model=`gpt-5.5`, effort=`high`, work-unit=`lane_execute`, reason=`phase-plan policy`
- SL-3: executor=`codex`, model=`gpt-5.5`, effort=`medium`, work-unit=`phase_verify`, reason=`branch audit and phase verification reducer`

## Execution Notes

- Execute SL-0 before SL-1 and SL-2. SL-1 and SL-2 can run after SL-0 with disjoint write ownership.
- Keep CTXFREEZE contract-only. If the executor finds runtime behavior that cannot satisfy a frozen assertion without implementation work, record it as a CTXIMPL or CTXRELY mismatch instead of expanding this phase.
- The output-boundary claim must be explicit: either runtime-only non-inlining, or a stronger output/log/observer/handoff policy with named sentinel checks. Do not imply a general output-DLP guarantee unless tests and docs freeze that stronger claim.
- If CTXRELY is not required for the minimal #114 context-ref release, SL-3 must state which reliability criteria move to follow-on scope before CTXVERIFY consumes the plan family.
- Documentation sweep decision: `no_doc_delta` for README, CHANGELOG, and release notes in CTXFREEZE; this phase updates the contract surface only, while CTXDOCS and CTXVERIFY own public docs and release-prep notes.

## Spec Closeout Plan

- schema: `spec_delta_closeout.v1`
- decision: `canonical_spec_update`
- target surfaces: `phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py`, `phase-loop-runtime/src/phase_loop_runtime/advisor_board/CONTRACTS.md`, `phase-loop-runtime/tests/test_panel_context_refs_114.py`, `phase-loop-runtime/tests/test_panel_invoker.py`
- evidence paths: `plans/phase-plan-v6-CTXFREEZE.md`, `phase-loop-runtime/src/phase_loop_runtime/advisor_board/CONTRACTS.md`, `phase-loop-runtime/tests/test_panel_context_refs_114.py`, `phase-loop-runtime/tests/test_panel_invoker.py`
- redaction posture: `metadata_only`
- downstream handling: `none`

## Verification

```bash
PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v6.md
cd phase-loop-runtime && PYTHONPATH=src python -m py_compile src/phase_loop_runtime/panel_invoker.py
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_panel_context_refs_114.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_panel_invoker.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_advisor_board_golden.py tests/test_panel_invoker_spawn.py -q
```

automation:
  suite_command: cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_panel_context_refs_114.py tests/test_panel_invoker.py tests/test_advisor_board_golden.py tests/test_panel_invoker_spawn.py -q

## Acceptance Criteria

- [ ] `invoke_panel`, `invoke_board`, and `PanelRequest` / `invoke_panel_request` all have a frozen ingestion contract for `artifact`, `artifact_ref`, `brief_ref`, and `context_refs`.
- [ ] Precedence rules are documented for `artifact`, `artifact_ref`, `brief_ref`, and `context_refs`.
- [ ] Missing and unreadable `context_refs` behavior is fail-closed by default with explicit soft-warning opt-in.
- [ ] Provider and local-filesystem assumptions are documented in `phase-loop-runtime/src/phase_loop_runtime/advisor_board/CONTRACTS.md` for current homebrew legs and future backings.
- [ ] Candidate branch behavior is compared with `git diff origin/main...HEAD -- phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py phase-loop-runtime/tests/test_panel_context_refs_114.py phase-loop-runtime/tests/test_panel_invoker.py` and mismatches are listed before CTXIMPL or CTXRELY starts.
- [ ] The privacy claim is scoped in `CONTRACTS.md` to runtime non-inlining or expanded with named output/log/observer/handoff/verifier sentinel checks.
- [ ] The manifest contract in `CONTRACTS.md` defines escaping, relative base, normalization, symlink/root policy, non-regular files, TOCTOU sequencing, and very large/sparse/virtual file handling.
- [ ] `tests/test_panel_context_refs_114.py` proves bounded hashing, MIME, extension, and PDF page-count behavior and labels extension-derived values as untrusted hints unless magic-byte sniffing is implemented.
- [ ] `tests/test_panel_context_refs_114.py` proves soft-warning entries include a strict instruction not to infer or guess unavailable contents and define missing metadata behavior.
- [ ] `PanelRequest.brief_ref` is either added with tests or explicitly excluded from the entry-point contract and docs.
- [ ] `timeouts_by_leg` versus `timeout_seconds_by_leg` is reconciled or deliberately frozen with rationale.
- [ ] Non-local or remote provider behavior for `context_refs` is decided as explicit skip/degrade or documented limitation with visible warning.
- [ ] `CONTRACTS.md` no longer describes `artifact_ref` or `brief_ref` as true non-inlining by-reference ingestion.
- [ ] The roadmap validates and focused CTXFREEZE verification commands pass before phase closeout can be `complete`.
