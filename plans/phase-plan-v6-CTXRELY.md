---
phase_loop_plan_version: 1
phase: CTXRELY
roadmap: specs/phase-plans-v6.md
roadmap_sha256: c4d6532b3b64a22e5d453a68a2d5579e8d1933b8cd29ed3c1f2d3e436d92c308
---

# CTXRELY: Reliability Bounds

## Context

CTXRELY is the roadmap v6 reliability hardening phase. CTXFREEZE completed at `9490bddbc4ccdeadabc60addb5a755d8ae24b02b`, and live git plus the canonical `.phase-loop/events.jsonl` ledger show CTXIMPL completed at `4b5f3cc9b2fbf613bc0420a23c56be62821d40f7`. `.phase-loop/state.json` and `.phase-loop/tui-handoff.md` lag behind that later CTXIMPL closeout, so this plan reconciles current phase readiness from the event ledger and clean live HEAD; legacy `.codex/phase-loop/` files are compatibility artifacts only.

This phase consumes the frozen reliability contract and the CTXIMPL runtime implementation. It produces no new interface-freeze gate. The work is limited to bounding panel hangs and transient leg loss: per-leg timeout overrides must reach every supported entry point, default input-scaled timeout behavior must remain intact for unspecified legs, and the Gemini/agy transient retry path must retry exactly once only for fast soft failures.

## Interface Freeze Gates

No new interface-freeze gates are produced by CTXRELY. This phase consumes the completed CTXFREEZE reliability contract as a pre-existing interface and produces runtime/test evidence for CTXVERIFY.

## Lane Index & Dependencies

SL-0 — Reliability source boundary
  Depends on: (none)
  Blocks: SL-1, SL-2, SL-3
  Parallel-safe: no
SL-1 — Timeout override and default-bound proof
  Depends on: SL-0
  Blocks: SL-3
  Parallel-safe: yes
SL-2 — Transient retry and elapsed-guard proof
  Depends on: SL-0
  Blocks: SL-3
  Parallel-safe: yes
SL-3 — Phase verification reducer
  Depends on: SL-0, SL-1, SL-2
  Blocks: (none)
  Parallel-safe: no

## Lanes

### SL-0 — Reliability Source Boundary

- **Scope**: Keep all runtime timeout and retry semantics in the single panel execution boundary while preserving CTXIMPL context-ref behavior.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py`
- **Interfaces provided**: `CTXRELY reliability runtime boundary`
- **Interfaces consumed**: `frozen reliability contract`, `CTXIMPL runtime context-ref implementation`, `PanelRequest`, `invoke_panel`, `invoke_board`, `_exec_leg`, `_default_spawn_via_provider` (pre-existing)
- **Parallel-safe**: no
- **Tasks**:
  - test: Run the timeout and retry focused tests before edits to identify any current branch drift from the frozen reliability contract.
  - impl: Thread per-leg timeout overrides through `invoke_panel`, `invoke_board`, `PanelRequest`, `_default_spawn_via_provider`, `_default_spawn`, and `_exec_leg`; keep unset legs on the input-scaled default; keep Gemini/agy soft-empty or transient-stall retry to one fast retry only; keep hard subprocess timeouts and hard non-transient errors non-retryable.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m py_compile src/phase_loop_runtime/panel_invoker.py`

### SL-1 — Timeout Override And Default-Bound Proof

- **Scope**: Prove override propagation and default timeout scaling for panel, board, request, argv, and spawn boundaries.
- **Owned files**: `phase-loop-runtime/tests/test_panel_invoker.py`, `phase-loop-runtime/tests/test_panel_invoker_timeout_argv.py`
- **Interfaces provided**: `CTXRELY timeout proof`
- **Interfaces consumed**: `CTXRELY reliability runtime boundary`, `frozen reliability contract` (pre-existing)
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add or align failing assertions that `invoke_panel(..., timeouts_by_leg=...)`, `invoke_board(..., timeouts_by_leg=...)`, and `PanelRequest.timeout_seconds_by_leg` reach the real leg timeout, while unspecified legs still use the input-scaled floor and cap.
  - impl: Keep edits test-only in the owned files; if runtime changes outside SL-0 ownership are required, stop and amend this plan before touching them.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_panel_invoker.py tests/test_panel_invoker_timeout_argv.py -q`

### SL-2 — Transient Retry And Elapsed-Guard Proof

- **Scope**: Prove Gemini/agy retry classification, hard-timeout non-retry behavior, and elapsed-time guard bounds without live provider calls.
- **Owned files**: `phase-loop-runtime/tests/test_panel_context_refs_114.py`, `phase-loop-runtime/tests/test_panel_invoker_spawn.py`
- **Interfaces provided**: `CTXRELY retry proof`
- **Interfaces consumed**: `CTXRELY reliability runtime boundary`, `frozen reliability contract` (pre-existing)
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add or align assertions for Gemini/agy transient marker retry once, soft-empty retry once, substantial output no-retry, hard non-transient error no-retry, hard `TimeoutExpired` no-retry, and slow soft-empty or stall attempts not retrying after the elapsed guard.
  - impl: Keep edits test-only in the owned files; use monkeypatched subprocess and monotonic clocks instead of live CLI calls.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_panel_context_refs_114.py tests/test_panel_invoker_spawn.py -q`

### SL-3 — Phase Verification Reducer

- **Scope**: Reduce reliability evidence, confirm no docs delta belongs in this phase, and run the whole CTXRELY proof set.
- **Owned files**: none
- **Interfaces provided**: CTXRELY phase verification evidence
- **Interfaces consumed**: `CTXRELY reliability runtime boundary`, `CTXRELY timeout proof`, `CTXRELY retry proof`
- **Parallel-safe**: no
- **Tasks**:
  - test: Confirm the timeout and retry proof files cover every CTXRELY exit criterion and that no CTXDOCS-owned user-facing language is required for this phase closeout.
  - impl: Record `no_doc_delta` and phase evidence in closeout only; do not write synthesized docs or release notes in this reducer.
  - verify: `PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v6.md && cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_panel_context_refs_114.py tests/test_panel_invoker.py tests/test_panel_invoker_timeout_argv.py tests/test_panel_invoker_spawn.py -q`

## Dispatch Hints

- preferred executors: `codex`
- allowed executors: `codex`
- required capabilities: `live_launch`, `structured_output`

## Execution Policy

- work-unit defaults: work-unit=`lane_execute`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`
- execute: executor=`codex`, model=`gpt-5.5`, effort=`high`, work-unit=`lane_execute`, reason=`phase-plan policy`
- SL-3: executor=`codex`, model=`gpt-5.5`, effort=`medium`, work-unit=`phase_verify`, reason=`phase verification reducer`

## Execution Notes

- Execute SL-0 before SL-1 and SL-2. SL-1 and SL-2 may run after SL-0 because their write ownership is disjoint. Run SL-3 after both proof lanes.
- Keep runtime edits inside `phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py`. If reliability work needs another source file, stop and amend the active plan before touching it.
- Keep public docs, bundled skill wording, and migration notes in CTXDOCS. CTXRELY records `no_doc_delta` unless the reducer finds a contradiction that must become a CTXDOCS follow-on.
- Retry behavior must distinguish soft transient loss from hard failure: exactly one retry for fast soft-empty or transient-stall signals, no retry for hard subprocess timeouts, no retry for hard non-transient errors, and no retry after an attempt consumes the elapsed guard threshold.
- Timeout naming must stay deliberate: public `invoke_panel` and `invoke_board` use `timeouts_by_leg`; `PanelRequest` uses `timeout_seconds_by_leg`.
- Policy precedence is CLI/operator override, phase-plan policy, roadmap policy, `Dispatch Hints`, then registry defaults; no executor/model downgrade is allowed without explicit fallback or inherited default behavior.

## Spec Closeout Plan

- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py`, `phase-loop-runtime/tests/test_panel_context_refs_114.py`, `phase-loop-runtime/tests/test_panel_invoker.py`, `phase-loop-runtime/tests/test_panel_invoker_timeout_argv.py`, `phase-loop-runtime/tests/test_panel_invoker_spawn.py`
- evidence paths: `plans/phase-plan-v6-CTXRELY.md`, `phase-loop-runtime/tests/test_panel_context_refs_114.py`, `phase-loop-runtime/tests/test_panel_invoker.py`, `phase-loop-runtime/tests/test_panel_invoker_timeout_argv.py`, `phase-loop-runtime/tests/test_panel_invoker_spawn.py`
- redaction posture: `metadata_only`
- downstream handling: `none`

## Verification

```bash
PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v6.md
cd phase-loop-runtime && PYTHONPATH=src python -m py_compile src/phase_loop_runtime/panel_invoker.py
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_panel_context_refs_114.py tests/test_panel_invoker.py tests/test_panel_invoker_timeout_argv.py tests/test_panel_invoker_spawn.py -q
```

automation:
  suite_command: cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_panel_context_refs_114.py tests/test_panel_invoker.py tests/test_panel_invoker_timeout_argv.py tests/test_panel_invoker_spawn.py -q

## Acceptance Criteria

- [ ] Per-leg timeout overrides thread through `invoke_panel(..., timeouts_by_leg=...)`, `invoke_board(..., timeouts_by_leg=...)`, and `PanelRequest.timeout_seconds_by_leg` to the real leg timeout boundary.
- [ ] Unspecified legs continue to use input-scaled timeout behavior with the existing floor and cap.
- [ ] Gemini/agy retries exactly once on fast soft-empty output or frozen transient stall markers.
- [ ] Hard subprocess timeouts and hard non-transient errors do not retry.
- [ ] Retry elapsed-time guards prevent slow soft-empty or transient-stall attempts from doubling a leg's wall-clock budget.
- [ ] Focused tests in `test_panel_context_refs_114.py`, `test_panel_invoker.py`, `test_panel_invoker_timeout_argv.py`, and `test_panel_invoker_spawn.py` pass without live provider calls.
- [ ] `PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v6.md` passes before CTXRELY execution closeout.
- [ ] SL-3 closeout evidence records `no_doc_delta`; CTXDOCS remains the owner for public docs and bundled skill wording.
