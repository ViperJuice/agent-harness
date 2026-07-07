---
phase_loop_plan_version: 1
phase: CTXVERIFY
roadmap: specs/phase-plans-v6.md
roadmap_sha256: c4d6532b3b64a22e5d453a68a2d5579e8d1933b8cd29ed3c1f2d3e436d92c308
---

# CTXVERIFY: Verification, PR Closeout, And Release Prep

## Context

CTXVERIFY is the serial release-proof phase for roadmap v6. Canonical `.phase-loop/` state currently selects CTXVERIFY as the unplanned downstream phase for `specs/phase-plans-v6.md`; CTXFREEZE, CTXIMPL, CTXRELY, and CTXDOCS are complete on branch `fix/panel-nits-post-115` at clean live HEAD `b4bd9a06541f48ecec995270e2460635946ce0e0`. Legacy `.codex/phase-loop/` files are compatibility artifacts only and must not block or supersede `.phase-loop/` state.

This phase consumes the completed ingestion, manifest, reliability, implementation, and docs contracts. It produces the release proof gate for issue #114 by hardening or confirming sentinel non-inlining tests, `invoke_board` behavior, manifest/filesystem edge coverage, golden back-compat, timeout/retry bounds, docs freshness, clean-room install readiness, skill parity, generated-skill no-op evidence, worktree hygiene, and release-prep notes. It does not publish a release, run a release workflow, force push, delete unrelated files, or treat green local proof as an already-dispatched release.

## Interface Freeze Gates

- [ ] IF-0-CTXVERIFY-1 - Release proof contract: regression proof covers sentinel absence, `invoke_board(..., context_refs=[p])` metadata-only behavior, manifest field ordering and escaping, heterogeneous missing/unreadable entries, filesystem edge behavior, golden back-compat, timeout/retry bounds, docs freshness, worktree hygiene, clean-room install, skill canon parity, generated-skill no-op evidence, and release-dispatch separation.

## Lane Index & Dependencies

SL-0 — Issue #114 regression proof hardening
  Depends on: (none)
  Blocks: SL-1, SL-2, SL-3
  Parallel-safe: no
SL-1 — PR checklist and release-prep notes
  Depends on: SL-0
  Blocks: SL-2, SL-3
  Parallel-safe: yes
SL-2 — Release-grade verification sweep
  Depends on: SL-0, SL-1
  Blocks: SL-3
  Parallel-safe: no
SL-3 — CTXVERIFY evidence and docs freshness reducer
  Depends on: SL-0, SL-1, SL-2
  Blocks: (none)
  Parallel-safe: no

## Lanes

### SL-0 — Issue #114 Regression Proof Hardening

- **Scope**: Ensure the focused test suite proves the frozen #114 ingestion, manifest, filesystem, entry-point, timeout, retry, and golden back-compat contracts before release prep.
- **Owned files**: `phase-loop-runtime/tests/test_panel_context_refs_114.py`, `phase-loop-runtime/tests/test_panel_invoker.py`, `phase-loop-runtime/tests/test_panel_invoker_timeout_argv.py`, `phase-loop-runtime/tests/test_panel_invoker_spawn.py`, `phase-loop-runtime/tests/test_advisor_board_ingestion.py`
- **Interfaces provided**: `CTXVERIFY #114 targeted proof`, `IF-0-CTXVERIFY-1 test evidence`
- **Interfaces consumed**: `IF-0-CTXFREEZE-1` (pre-existing), `IF-0-CTXFREEZE-2` (pre-existing), `IF-0-CTXFREEZE-3` (pre-existing), CTXIMPL runtime implementation (pre-existing), CTXRELY reliability evidence (pre-existing), CTXDOCS docs contract (pre-existing)
- **Parallel-safe**: no
- **Tasks**:
  - test: Inspect the focused #114 tests for explicit assertions covering sentinel absence, `invoke_board(..., context_refs=[p])`, deterministic manifest ordering, structured escaping, missing-path fail-closed behavior, soft-warning missing/unreadable entries, relative path and `..` behavior, symlink-to-file and symlink-to-directory behavior, non-regular-file rejection, spoofed extension/MIME hints, optional PDF page counts, large-file streamed hashing, golden no-context-ref byte identity, per-leg timeout propagation, and retry bounds.
  - impl: Add or repair only the missing focused assertions in the owned test files; do not change runtime behavior in CTXVERIFY unless a test exposes a small contract bug that must be fixed before the release gate, in which case stop and amend the active plan ownership before touching source files.
  - verify: `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_panel_context_refs_114.py tests/test_panel_invoker.py tests/test_panel_invoker_timeout_argv.py tests/test_panel_invoker_spawn.py tests/test_advisor_board_ingestion.py -q`

### SL-1 — PR Checklist And Release-Prep Notes

- **Scope**: Reconcile roadmap and issue #114 acceptance into release-prep notes without dispatching a release or exposing private examples.
- **Owned files**: `CHANGELOG.md`
- **Interfaces provided**: `CTXVERIFY PR checklist`, `CTXVERIFY release-prep notes`
- **Interfaces consumed**: `CTXVERIFY #114 targeted proof`, `IF-0-CTXDOCS-1` (pre-existing), completed CTXFREEZE/CTXIMPL/CTXRELY/CTXDOCS closeout evidence (pre-existing)
- **Parallel-safe**: yes
- **Tasks**:
  - test: Compare the roadmap exit criteria, issue #114 scope, current changelog entry, and completed CTX phase evidence for missing release-prep bullets or stale claims that imply release dispatch already happened.
  - impl: Update `CHANGELOG.md` only if it is missing concise #114 release-prep language, merge-now limits, separate release-dispatch language, or private-content safety wording; otherwise record a no-source-delta release-prep decision in closeout evidence.
  - verify: `rg -n "#114|context_refs|artifact_ref|brief_ref|release|dispatch|private|pathnames|hashes" CHANGELOG.md && ! rg -n "EZBidPro|PWA|PBS|NavBlue" CHANGELOG.md`

### SL-2 — Release-Grade Verification Sweep

- **Scope**: Run the release-prep verification ladder across roadmap validation, targeted regression tests, standalone tests, clean-room install, skill parity, generated-skill no-op evidence, and diff hygiene.
- **Owned files**: none
- **Interfaces provided**: `CTXVERIFY release verification evidence`
- **Interfaces consumed**: `CTXVERIFY #114 targeted proof`, `CTXVERIFY PR checklist`, `CTXVERIFY release-prep notes`, all completed upstream CTX phase gates (pre-existing)
- **Parallel-safe**: no
- **Tasks**:
  - test: Run the full proof ladder from a clean worktree and classify any dirty paths before staging, including local indices, generated bundles, lockfiles, or phase-loop artifacts.
  - impl: Do not write source files in this lane; if a verification command generates a diff, classify it as planned CTXVERIFY output only if this plan owns the path, otherwise stop with a dirty-worktree conflict or amend the active plan before continuing.
  - verify: `PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v6.md && (cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_panel_context_refs_114.py tests/test_panel_invoker.py tests/test_panel_invoker_timeout_argv.py tests/test_panel_invoker_spawn.py tests/test_advisor_board_ingestion.py tests/test_advisor_board_golden.py -q && PYTHONPATH=src python -m pytest tests/test_skills_canon_parity.py tests/test_skills_bundle_drift.py -q && PYTHONPATH=src python -m pytest -m "not dotfiles_integration" -q && bash scripts/gate_a_cleanroom.sh) && git diff --check && git status --short --untracked-files=all`

### SL-3 — CTXVERIFY Evidence And Docs Freshness Reducer

- **Scope**: Reduce the focused tests, release notes, docs freshness check, and broad verification sweep into the final CTXVERIFY gate and next-action evidence.
- **Owned files**: none
- **Interfaces provided**: `IF-0-CTXVERIFY-1`, CTXVERIFY phase verification evidence, release-dispatch separation evidence
- **Interfaces consumed**: `CTXVERIFY #114 targeted proof`, `CTXVERIFY PR checklist`, `CTXVERIFY release-prep notes`, `CTXVERIFY release verification evidence`
- **Parallel-safe**: no
- **Tasks**:
  - test: Confirm every CTXVERIFY exit criterion is covered by a passing command or by a non-secret closeout evidence item, and confirm no active release-dispatch mutation ran.
  - impl: Record `no_spec_delta` closeout evidence with metadata-only target surfaces and evidence paths; identify merge readiness versus release-dispatch follow-up in the closeout/handoff rather than adding a new release artifact.
  - verify: `PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v6.md && git diff --check`

## Dispatch Hints

- preferred executors: `codex`
- allowed executors: `codex`
- required capabilities: `live_launch`, `structured_output`

## Execution Policy

- work-unit defaults: work-unit=`lane_execute`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`
- execute: executor=`codex`, model=`gpt-5.5`, effort=`high`, work-unit=`lane_execute`, reason=`phase-plan policy`
- SL-2: executor=`codex`, model=`gpt-5.5`, effort=`high`, work-unit=`phase_verify`, reason=`release-grade verification sweep`
- SL-3: executor=`codex`, model=`gpt-5.5`, effort=`medium`, work-unit=`phase_reducer`, reason=`CTXVERIFY evidence reducer`

## Execution Notes

- Execute SL-0 first so the release-prep notes and verification sweep consume the actual focused proof surface. SL-1 may run after SL-0 because it owns only `CHANGELOG.md`. Run SL-2 after SL-0 and SL-1, and run SL-3 last.
- Keep CTXVERIFY as release prep only. Do not tag, publish, trigger `gh workflow run`, delete remote branches, force push, or claim release dispatch completed in this phase.
- Treat generated-skill commands as verification-only unless the active plan is amended to own generated bundle paths. `test_skills_canon_parity.py` and `test_skills_bundle_drift.py` are the preferred no-write parity checks.
- If the full standalone or clean-room gate fails for an environment issue, record the exact non-secret failure class and whether equivalent green GitHub checks can satisfy the roadmap release-prep proof before merge.
- Policy precedence is CLI/operator override, phase-plan policy, roadmap policy, `Dispatch Hints`, then registry defaults; no executor/model downgrade is allowed without explicit fallback or inherited default behavior.

## Spec Closeout Plan

- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `phase-loop-runtime/tests/test_panel_context_refs_114.py`, `phase-loop-runtime/tests/test_panel_invoker.py`, `phase-loop-runtime/tests/test_panel_invoker_timeout_argv.py`, `phase-loop-runtime/tests/test_panel_invoker_spawn.py`, `phase-loop-runtime/tests/test_advisor_board_ingestion.py`, `CHANGELOG.md`, `.phase-loop/**`, `.dev-skills/handoffs/**`
- evidence paths: `plans/phase-plan-v6-CTXVERIFY.md`, `phase-loop-runtime/tests/test_panel_context_refs_114.py`, `phase-loop-runtime/tests/test_panel_invoker.py`, `phase-loop-runtime/tests/test_panel_invoker_timeout_argv.py`, `phase-loop-runtime/tests/test_panel_invoker_spawn.py`, `phase-loop-runtime/tests/test_advisor_board_ingestion.py`, `phase-loop-runtime/tests/test_advisor_board_golden.py`, `phase-loop-runtime/tests/test_skills_canon_parity.py`, `phase-loop-runtime/tests/test_skills_bundle_drift.py`, `CHANGELOG.md`, `.phase-loop`, `.dev-skills/handoffs`
- redaction posture: `metadata_only`
- downstream handling: `none`

## Verification

```bash
PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v6.md
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_panel_context_refs_114.py tests/test_panel_invoker.py tests/test_panel_invoker_timeout_argv.py tests/test_panel_invoker_spawn.py tests/test_advisor_board_ingestion.py tests/test_advisor_board_golden.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_skills_canon_parity.py tests/test_skills_bundle_drift.py -q
cd phase-loop-runtime && PYTHONPATH=src python -m pytest -m "not dotfiles_integration" -q
cd phase-loop-runtime && bash scripts/gate_a_cleanroom.sh
git diff --check
git status --short --untracked-files=all
```

automation:
  suite_command: PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v6.md && (cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_panel_context_refs_114.py tests/test_panel_invoker.py tests/test_panel_invoker_timeout_argv.py tests/test_panel_invoker_spawn.py tests/test_advisor_board_ingestion.py tests/test_advisor_board_golden.py -q && PYTHONPATH=src python -m pytest tests/test_skills_canon_parity.py tests/test_skills_bundle_drift.py -q && PYTHONPATH=src python -m pytest -m "not dotfiles_integration" -q && bash scripts/gate_a_cleanroom.sh) && git diff --check && git status --short --untracked-files=all

## Acceptance Criteria

- [ ] Sentinel non-inlining tests pass and prove referenced file contents are absent from staged bundles.
- [ ] `invoke_board(..., context_refs=[p])` has a behavior test proving sentinel bytes are absent while path and metadata follow the frozen manifest contract.
- [ ] Manifest snapshot or golden-style assertions pin field ordering, structured escaping, and heterogeneous missing/unreadable entries.
- [ ] Filesystem edge tests cover relative paths, `..`, symlink handling, symlink escape or explicit normal-OS symlink policy, non-regular files, spoofed extension/MIME hints, and large/sparse file metadata behavior as frozen in CTXFREEZE.
- [ ] Golden advisor-board and panel-invoker tests pass for back-compat.
- [ ] Timeout/retry tests pass and cover retry bounds, hard-timeout non-retry behavior, and elapsed retry guards.
- [ ] Roadmap and issue #114 acceptance criteria are reconciled into concise PR/release-prep closeout evidence.
- [ ] Any unrelated worktree changes, including generated lockfiles, skill bundles, local indices, or phase-loop artifacts, are classified before staging.
- [ ] `PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v6.md` passes.
- [ ] `cd phase-loop-runtime && PYTHONPATH=src python -m pytest -m "not dotfiles_integration" -q` passes, or equivalent green GitHub checks are named in non-secret closeout evidence before merge.
- [ ] `cd phase-loop-runtime && bash scripts/gate_a_cleanroom.sh` passes, or equivalent green clean-room install evidence is named before merge.
- [ ] `cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_skills_canon_parity.py tests/test_skills_bundle_drift.py -q` passes as skill canon parity and generated-skill no-op/diff evidence.
- [ ] `git diff --check` passes and `git status --short --untracked-files=all` is reviewed before closeout.
- [ ] Release-prep notes identify what can merge now and what requires a separate release-dispatch phase; CTXVERIFY does not tag, publish, or dispatch a release workflow.
