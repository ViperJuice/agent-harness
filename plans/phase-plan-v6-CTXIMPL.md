---
phase_loop_plan_version: 1
phase: CTXIMPL
roadmap: specs/phase-plans-v6.md
roadmap_sha256: c4d6532b3b64a22e5d453a68a2d5579e8d1933b8cd29ed3c1f2d3e436d92c308
---

# CTXIMPL: Runtime Implementation And Back-Compat

## Context

CTXIMPL is the runtime implementation phase for roadmap v6 after CTXFREEZE completed at commit `9490bddbc4ccdeadabc60addb5a755d8ae24b02b`. The canonical runner state is `.phase-loop/`, which currently selects CTXIMPL as the unplanned downstream phase for `specs/phase-plans-v6.md`; legacy `.codex/phase-loop/` files are compatibility-only and must not supersede canonical state.

This phase consumes the frozen CTXFREEZE ingestion, manifest, and reliability contracts. The implementation target is narrow: make true by-reference `context_refs` work through every supported entry point while preserving existing inline, `artifact_ref`, `brief_ref`, default board, and golden panel behavior. Reliability retry and timeout semantics already frozen by CTXFREEZE should be preserved here only where they are necessary for entry-point threading and back-compat; broader reliability hardening remains CTXRELY scope.

## Interface Freeze Gates

No new interface-freeze gates are produced by CTXIMPL. This phase consumes the completed CTXFREEZE contracts as pre-existing interfaces and produces runtime/test evidence for downstream CTXDOCS and CTXVERIFY.

## Lane Index & Dependencies

SL-0 — Runtime API threading implementation
  Depends on: (none)
  Blocks: SL-1, SL-2, SL-3
  Parallel-safe: no
SL-1 — Context-ref manifest and filesystem proof
  Depends on: SL-0
  Blocks: SL-3
  Parallel-safe: yes
SL-2 — Entry-point and back-compat proof
  Depends on: SL-0
  Blocks: SL-3
  Parallel-safe: yes
SL-3 — Documentation sweep and phase verification reducer
  Depends on: SL-0, SL-1, SL-2
  Blocks: (none)
  Parallel-safe: no

## Lanes

### SL-0 — Runtime API Threading Implementation

- **Scope**: Repair the single runtime boundary so `context_refs`, read-file-and-stage refs, brief refs, and timeout overrides are threaded according to the frozen contract without changing legacy no-ref behavior.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py`
- **Interfaces provided**: context-ref runtime threading, context-ref manifest implementation, artifact-ref and brief-ref back-compat runtime
- **Interfaces consumed**: frozen ingestion API contract (pre-existing), frozen manifest contract (pre-existing), `PanelRequest` (pre-existing), `invoke_panel` (pre-existing), `invoke_board` (pre-existing), `_default_spawn_via_provider` (pre-existing)
- **Parallel-safe**: no
- **Tasks**:
  - test: Run the focused context-ref and panel-invoker tests before edits to identify current contract mismatches.
  - impl: Ensure `invoke_panel`, `invoke_board`, and `invoke_panel_request` all apply `context_refs` as metadata-only manifests; preserve `artifact_ref` and `brief_ref` read-file-and-stage behavior; keep missing paths fail-closed by default with explicit soft-warning metadata; implement or explicitly preserve the frozen path, non-regular-file, symlink, root-jail, and TOCTOU policy in the runtime code.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m py_compile src/phase_loop_runtime/panel_invoker.py`

### SL-1 — Context-Ref Manifest And Filesystem Proof

- **Scope**: Prove metadata-only context-ref rendering, bounded metadata extraction, and filesystem failure behavior with focused tests.
- **Owned files**: `phase-loop-runtime/tests/test_panel_context_refs_114.py`
- **Interfaces provided**: context-ref manifest proof
- **Interfaces consumed**: context-ref manifest implementation, frozen manifest contract (pre-existing)
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add or update failing assertions for sentinel absence, deterministic multi-file ordering, JSON-escaped hostile filenames, missing and non-regular paths, unreadable soft-warning entries, streamed hashing, untrusted MIME/extension hints, optional PDF page counts, and no-context-ref byte identity.
  - impl: Keep the test file aligned with the frozen runtime-only non-inlining claim and avoid turning soft-warning entries into implied content review.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_panel_context_refs_114.py -q`

### SL-2 — Entry-Point And Back-Compat Proof

- **Scope**: Prove every supported entry point consumes the repaired runtime while golden/default behavior remains byte-stable for existing callers.
- **Owned files**: `phase-loop-runtime/tests/test_panel_invoker.py`, `phase-loop-runtime/tests/test_advisor_board_golden.py`, `phase-loop-runtime/tests/test_panel_invoker_spawn.py`
- **Interfaces provided**: entry-point and golden back-compat proof
- **Interfaces consumed**: context-ref runtime threading, artifact-ref and brief-ref back-compat runtime, frozen ingestion API contract (pre-existing), default board golden parity (pre-existing)
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add or update assertions that `invoke_panel`, `invoke_board`, and `PanelRequest` thread `context_refs` and timeout overrides; `artifact_ref` wins over inline artifact; `brief_ref` remains read-file-and-stage for spawn paths; `PanelRequest.brief_ref` is deliberately excluded unless the frozen contract is amended; default board golden parity remains unchanged when no context refs are supplied.
  - impl: Keep test changes restricted to entry-point and back-compat proof files, using mocked spawns and staged-bundle captures rather than live provider calls.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_panel_invoker.py tests/test_advisor_board_golden.py tests/test_panel_invoker_spawn.py -q`

### SL-3 — Documentation Sweep And Phase Verification Reducer

- **Scope**: Record the CTXIMPL `no_doc_delta` decision, reduce lane evidence, and run the whole CTXIMPL proof set without writing synthesized artifacts.
- **Owned files**: none
- **Interfaces provided**: CTXIMPL documentation impact decision, CTXIMPL phase verification evidence
- **Interfaces consumed**: context-ref runtime threading, context-ref manifest proof, entry-point and golden back-compat proof
- **Parallel-safe**: no
- **Tasks**:
  - test: Confirm lane-owned test files cover the CTXIMPL exit criteria, no CTXRELY-only reliability work is required for this phase closeout, and public docs stay CTXDOCS-owned.
  - impl: Record `no_doc_delta` or a CTXDOCS follow-on decision in closeout evidence only; do not edit docs or release notes in this reducer.
  - verify: `PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v6.md && cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_panel_context_refs_114.py tests/test_panel_invoker.py tests/test_advisor_board_golden.py tests/test_panel_invoker_spawn.py -q`

## Dispatch Hints

- preferred executors: `codex`
- allowed executors: `codex`
- required capabilities: `live_launch`, `structured_output`

## Execution Policy

- work-unit defaults: work-unit=`lane_execute`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`
- execute: executor=`codex`, model=`gpt-5.5`, effort=`high`, work-unit=`lane_execute`, reason=`phase-plan policy`
- SL-3: executor=`codex`, model=`gpt-5.5`, effort=`medium`, work-unit=`phase_verify`, reason=`documentation sweep and phase verification reducer`

## Execution Notes

- Execute SL-0 before SL-1 and SL-2. SL-1 and SL-2 may run after SL-0 because their write ownership is disjoint. Run SL-3 after both proof lanes.
- Keep implementation scope in `panel_invoker.py`; if additional source files are needed, stop and amend the active plan before touching them.
- Keep CTXRELY retry and timeout hardening out of CTXIMPL unless a threading fix in `panel_invoker.py` is required to satisfy the frozen entry-point contract.
- CTXDOCS owns public docs, skill prose, and migration language. CTXIMPL records `no_doc_delta` unless SL-3 names a CTXDOCS follow-on; it should not update docs except through closeout evidence.
- Policy precedence is CLI/operator override, phase-plan policy, roadmap policy, `Dispatch Hints`, then registry defaults; no executor/model downgrade is allowed without explicit fallback or inherited default behavior.

## Spec Closeout Plan

- schema: `spec_delta_closeout.v1`
- decision: `canonical_spec_update`
- target surfaces: `phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py`, `phase-loop-runtime/tests/test_panel_context_refs_114.py`, `phase-loop-runtime/tests/test_panel_invoker.py`, `phase-loop-runtime/tests/test_advisor_board_golden.py`, `phase-loop-runtime/tests/test_panel_invoker_spawn.py`
- evidence paths: `plans/phase-plan-v6-CTXIMPL.md`, `phase-loop-runtime/tests/test_panel_context_refs_114.py`, `phase-loop-runtime/tests/test_panel_invoker.py`, `phase-loop-runtime/tests/test_advisor_board_golden.py`, `phase-loop-runtime/tests/test_panel_invoker_spawn.py`
- redaction posture: `metadata_only`
- downstream handling: `none`

## Verification

```bash
PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v6.md
cd phase-loop-runtime && PYTHONPATH=src python -m py_compile src/phase_loop_runtime/panel_invoker.py
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_panel_context_refs_114.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_panel_invoker.py tests/test_advisor_board_golden.py tests/test_panel_invoker_spawn.py -q
```

automation:
  suite_command: cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_panel_context_refs_114.py tests/test_panel_invoker.py tests/test_advisor_board_golden.py tests/test_panel_invoker_spawn.py -q

## Acceptance Criteria

- [ ] `context_refs` reaches `invoke_panel`, `invoke_board`, and `PanelRequest` / `invoke_panel_request` without reading referenced file bodies into staged review bundles.
- [ ] `artifact_ref` and `brief_ref` keep their read-file-and-stage behavior and existing precedence.
- [ ] `tests/test_panel_context_refs_114.py` asserts deterministic, structured, escaped, metadata-only manifests with bounded size/hash/MIME/extension/PDF metadata where available.
- [ ] `tests/test_panel_context_refs_114.py` asserts missing, unreadable, non-regular, root/symlink, and TOCTOU-relevant filesystem cases follow the frozen fail-closed or explicit soft-warning behavior.
- [ ] `tests/test_panel_context_refs_114.py` asserts large referenced files are hashed with bounded memory behavior, and no context-ref path changes no-ref golden panel bytes.
- [ ] `SL-3` closeout evidence records `no_doc_delta` or a CTXDOCS follow-on and records the remote or non-local provider limitation decision without claiming unavailable file contents were reviewed.
- [ ] Existing golden/default panel and board behavior remains byte-stable for callers that do not pass `context_refs`.
- [ ] Focused tests in `test_panel_context_refs_114.py`, `test_panel_invoker.py`, `test_advisor_board_golden.py`, and `test_panel_invoker_spawn.py` pass.
- [ ] `PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v6.md` passes after CTXIMPL planning and before execution closeout.
