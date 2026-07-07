---
phase_loop_plan_version: 1
phase: CTXDOCS
roadmap: specs/phase-plans-v6.md
roadmap_sha256: c4d6532b3b64a22e5d453a68a2d5579e8d1933b8cd29ed3c1f2d3e436d92c308
---

# CTXDOCS: Docs, Skills, And Migration Language

## Context

CTXDOCS is the roadmap v6 documentation and migration-language phase. Canonical `.phase-loop/` state and `phase-loop status` show CTXFREEZE, CTXIMPL, and CTXRELY complete on branch `fix/panel-nits-post-115`, with CTXDOCS as the next unplanned phase. `.phase-loop/state.json` and `.phase-loop/tui-handoff.md` may lag the event ledger, so executors must continue to treat `.phase-loop/events.jsonl`, `phase-loop status`, and clean live git topology as the authoritative runner state; legacy `.codex/phase-loop/` files are compatibility artifacts only.

This phase consumes the frozen CTXFREEZE contract and the finalized CTXIMPL API names. It is docs-only: align the contract docs, bundled advisor-panel/advisor-board skills, source skill docs, capability card, changelog, and migration examples so callers choose between inline text, read-file-and-stage refs, and true by-reference `context_refs` without overclaiming privacy. It must not include private EZBidPro, PWA, PBS, or NavBlue content in examples.

## Interface Freeze Gates

- [ ] IF-0-CTXDOCS-1 - Documentation and skill contract: advisor-panel/board skills and contract docs distinguish inline, read-file-and-stage, and true by-reference modes without implying private bytes are safe to paste.

## Lane Index & Dependencies

SL-0 — Contract docs alignment
  Depends on: (none)
  Blocks: SL-1, SL-2, SL-3, SL-4
  Parallel-safe: no
SL-1 — Bundled advisor skill docs
  Depends on: SL-0
  Blocks: SL-2, SL-4
  Parallel-safe: yes
SL-2 — Source advisor skill docs
  Depends on: SL-0, SL-1
  Blocks: SL-3, SL-4
  Parallel-safe: no
SL-3 — Capability card, changelog, and migration notes
  Depends on: SL-0, SL-2
  Blocks: SL-4
  Parallel-safe: no
SL-4 — Docs freshness reducer
  Depends on: SL-0, SL-1, SL-2, SL-3
  Blocks: (none)
  Parallel-safe: no

## Lanes

### SL-0 - Contract Docs Alignment

- **Scope**: Make the canonical advisor-board contract docs the source of truth for the three ingestion modes, provider limits, and privacy boundaries.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/advisor_board/CONTRACTS.md`
- **Interfaces provided**: `IF-0-CTXDOCS-1 contract-docs source`, `CTXDOCS ingestion-mode wording`
- **Interfaces consumed**: `IF-0-CTXFREEZE-1` (pre-existing), `IF-0-CTXFREEZE-2` (pre-existing), `IF-0-CTXFREEZE-3` (pre-existing), CTXIMPL final API names (pre-existing), CTXRELY timeout and retry wording (pre-existing)
- **Parallel-safe**: no
- **Tasks**:
  - test: Read the CTXFREEZE section and any older ABDREF wording in `CONTRACTS.md` for statements that call `artifact_ref` or `brief_ref` true by-reference or true non-inlining.
  - impl: Update contract wording so `artifact` is inline text, `artifact_ref` and `brief_ref` are read-file-and-stage/read-file-and-inline conveniences, and `context_refs` is the only true by-reference metadata-manifest mode; include path/hash sensitivity, provider/local-filesystem limits, and runtime-only non-inlining scope.
  - verify: `rg -n "context_refs|artifact_ref|brief_ref|runtime non-inlining|pathnames|hashes|local-filesystem|local filesystem" phase-loop-runtime/src/phase_loop_runtime/advisor_board/CONTRACTS.md`

### SL-1 - Bundled Advisor Skill Docs

- **Scope**: Align every runtime-bundled advisor-panel and advisor-board skill with the frozen docs contract.
- **Owned files**: `phase-loop-runtime/src/phase_loop_runtime/skills_bundle/*-advisor-panel/SKILL.md`, `phase-loop-runtime/src/phase_loop_runtime/skills_bundle/*-advisor-board/SKILL.md`
- **Interfaces provided**: `CTXDOCS bundled-skill wording`
- **Interfaces consumed**: `IF-0-CTXDOCS-1 contract-docs source`, `CTXDOCS ingestion-mode wording`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Inspect all bundled advisor-panel and advisor-board skill copies for stale "reference, don't inline" wording that could make `artifact_ref` sound metadata-only.
  - impl: Lead with `context_refs` for large or private materials; keep `artifact_ref` and `brief_ref` described as read-file-and-stage behavior; warn that pathnames/hashes disclose metadata and that a leg may disclose content after intentionally reading a referenced file.
  - verify: `for f in phase-loop-runtime/src/phase_loop_runtime/skills_bundle/*-advisor-panel/SKILL.md phase-loop-runtime/src/phase_loop_runtime/skills_bundle/*-advisor-board/SKILL.md; do rg -q "context_refs" "$f" && rg -q "Read-file" "$f" && rg -q "path" "$f"; done`

### SL-2 - Source Advisor Skill Docs

- **Scope**: Keep source skill docs consistent with bundled advisor skill docs before future sync or install operations copy them outward.
- **Owned files**: `skills-src/*/*-advisor-board*/SKILL.md`, `phase-loop-skills/advisor-board/SKILL.md`
- **Interfaces provided**: `CTXDOCS source-skill wording`
- **Interfaces consumed**: `IF-0-CTXDOCS-1 contract-docs source`, `CTXDOCS bundled-skill wording`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Inspect source advisor-board skills and the legacy tracked advisor-board skill source for stale by-reference wording or missing `context_refs` guidance.
  - impl: Match the source docs to the bundled skill language: `context_refs` first for large/private local materials, `artifact_ref` and `brief_ref` as staged-byte paths, provider/backing limits explicit, no private examples.
  - verify: `for f in skills-src/*/*-advisor-board*/SKILL.md phase-loop-skills/advisor-board/SKILL.md; do rg -q "context_refs" "$f" && rg -q "Read-file" "$f" && rg -q "provider" "$f"; done`

### SL-3 - Capability Card, Changelog, And Migration Notes

- **Scope**: Update the user-facing capability card, release note, and migration examples so public docs describe #114 without exposing private examples or overclaiming privacy.
- **Owned files**: `docs/advisor-board-capabilities-card.md`, `CHANGELOG.md`
- **Interfaces provided**: `CTXDOCS public-doc wording`, `CTXDOCS migration-note wording`
- **Interfaces consumed**: `IF-0-CTXDOCS-1 contract-docs source`, `CTXDOCS source-skill wording`, CTXIMPL final API names
- **Parallel-safe**: yes
- **Tasks**:
  - test: Inspect the current capability card and changelog for `artifact_ref` headings or examples that still imply true non-inlining.
  - impl: Add or revise examples that show `context_refs=[...]` for path/metadata-only use, `artifact_ref` and `brief_ref` for staged-byte use, provider/backing limitations for local files, and path/hash metadata sensitivity; keep examples generic and non-private.
  - verify: `rg -n "context_refs|artifact_ref|brief_ref|pathnames|hashes|local file|provider|backing" docs/advisor-board-capabilities-card.md CHANGELOG.md`

### SL-4 - Docs Freshness Reducer

- **Scope**: Reduce all docs changes into a single consistency check and produce the CTXDOCS interface gate for CTXVERIFY.
- **Owned files**: none
- **Interfaces provided**: `IF-0-CTXDOCS-1`, CTXDOCS phase verification evidence
- **Interfaces consumed**: `CTXDOCS ingestion-mode wording`, `CTXDOCS bundled-skill wording`, `CTXDOCS source-skill wording`, `CTXDOCS public-doc wording`, `CTXDOCS migration-note wording`
- **Parallel-safe**: no
- **Tasks**:
  - test: Compare all CTXDOCS-owned docs for the same three-mode vocabulary, provider limitation language, metadata sensitivity warning, and absence of private EZBidPro/PWA/PBS/NavBlue examples.
  - impl: Record `dotfiles_skill_source_update` closeout evidence with metadata-only target surfaces and evidence paths; do not write additional synthesized docs from this reducer.
  - verify: `PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v6.md && git diff --check && ! rg -n "EZBidPro|PWA|PBS|NavBlue" phase-loop-runtime/src/phase_loop_runtime/advisor_board/CONTRACTS.md phase-loop-runtime/src/phase_loop_runtime/skills_bundle/*advisor*/SKILL.md skills-src/*/*advisor-board*/SKILL.md phase-loop-skills/advisor-board/SKILL.md docs/advisor-board-capabilities-card.md CHANGELOG.md`

## Dispatch Hints

- preferred executors: `codex`
- allowed executors: `codex`
- required capabilities: `live_launch`, `structured_output`

## Execution Policy

- work-unit defaults: work-unit=`lane_execute`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`
- execute: executor=`codex`, model=`gpt-5.5`, effort=`high`, work-unit=`lane_execute`, reason=`phase-plan policy`
- SL-4: executor=`codex`, model=`gpt-5.5`, effort=`medium`, work-unit=`phase_verify`, reason=`docs freshness reducer`

## Execution Notes

- Execute SL-0 first so every public doc lane consumes the same contract wording. SL-1, SL-2, and SL-3 may run after SL-0 because their write ownership is disjoint. Run SL-4 last.
- Keep this phase docs-only. If execution discovers runtime behavior or API names contradict the completed CTXIMPL implementation, stop and route a repair rather than inventing docs around ambiguous behavior.
- Do not claim global output-DLP. The correct claim is that the runtime does not automatically stage referenced file contents for `context_refs`; a leg with local tools may still disclose content after intentionally opening a referenced file unless an output policy forbids it.
- Do not use private EZBidPro, PWA, PBS, NavBlue, credential, or customer material in examples. Use generic local paths and metadata-only examples.
- Policy precedence is CLI/operator override, phase-plan policy, roadmap policy, `Dispatch Hints`, then registry defaults; no executor/model downgrade is allowed without explicit fallback or inherited default behavior.

## Spec Closeout Plan

- schema: `spec_delta_closeout.v1`
- decision: `dotfiles_skill_source_update`
- target surfaces: `phase-loop-runtime/src/phase_loop_runtime/advisor_board/CONTRACTS.md`, `phase-loop-runtime/src/phase_loop_runtime/skills_bundle/*-advisor-panel/SKILL.md`, `phase-loop-runtime/src/phase_loop_runtime/skills_bundle/*-advisor-board/SKILL.md`, `skills-src/*/*-advisor-board*/SKILL.md`, `phase-loop-skills/advisor-board/SKILL.md`, `docs/advisor-board-capabilities-card.md`, `CHANGELOG.md`
- evidence paths: `plans/phase-plan-v6-CTXDOCS.md`, `phase-loop-runtime/src/phase_loop_runtime/advisor_board/CONTRACTS.md`, `docs/advisor-board-capabilities-card.md`, `CHANGELOG.md`, `phase-loop-runtime/src/phase_loop_runtime/skills_bundle`, `skills-src`, `phase-loop-skills/advisor-board/SKILL.md`
- redaction posture: `metadata_only`
- downstream handling: `none`

## Verification

```bash
PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v6.md
git diff --check
rg -n "context_refs|artifact_ref|brief_ref|pathnames|hashes|local file|provider|backing" phase-loop-runtime/src/phase_loop_runtime/advisor_board/CONTRACTS.md phase-loop-runtime/src/phase_loop_runtime/skills_bundle/*advisor*/SKILL.md skills-src/*/*advisor-board*/SKILL.md phase-loop-skills/advisor-board/SKILL.md docs/advisor-board-capabilities-card.md CHANGELOG.md
! rg -n "EZBidPro|PWA|PBS|NavBlue" phase-loop-runtime/src/phase_loop_runtime/advisor_board/CONTRACTS.md phase-loop-runtime/src/phase_loop_runtime/skills_bundle/*advisor*/SKILL.md skills-src/*/*advisor-board*/SKILL.md phase-loop-skills/advisor-board/SKILL.md docs/advisor-board-capabilities-card.md CHANGELOG.md
```

automation:
  suite_command: PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v6.md && git diff --check && ! rg -n "EZBidPro|PWA|PBS|NavBlue" phase-loop-runtime/src/phase_loop_runtime/advisor_board/CONTRACTS.md phase-loop-runtime/src/phase_loop_runtime/skills_bundle/*advisor*/SKILL.md skills-src/*/*advisor-board*/SKILL.md phase-loop-skills/advisor-board/SKILL.md docs/advisor-board-capabilities-card.md CHANGELOG.md

## Acceptance Criteria

- [ ] Advisor-panel and advisor-board skill docs lead with `context_refs` for large or private local materials.
- [ ] Docs stop describing `artifact_ref` as true non-inlining and instead call it read-file-and-stage or read-file-and-inline behavior.
- [ ] Capability docs list inline text, read-file-and-stage refs, and true by-reference `context_refs`, with when to use each mode.
- [ ] `rg -n "pathnames|hashes" phase-loop-runtime/src/phase_loop_runtime/advisor_board/CONTRACTS.md docs/advisor-board-capabilities-card.md CHANGELOG.md` proves docs warn that pathnames and hashes can disclose sensitive information.
- [ ] `rg -n "intentionally inspect|output policy|disclose" phase-loop-runtime/src/phase_loop_runtime/advisor_board/CONTRACTS.md docs/advisor-board-capabilities-card.md phase-loop-runtime/src/phase_loop_runtime/skills_bundle/*advisor*/SKILL.md skills-src/*/*advisor-board*/SKILL.md phase-loop-skills/advisor-board/SKILL.md` proves docs warn that agents may disclose file contents after they intentionally inspect referenced files unless an output policy forbids it.
- [ ] `rg -n "provider|backing|local file|local-filesystem|local filesystem" phase-loop-runtime/src/phase_loop_runtime/advisor_board/CONTRACTS.md docs/advisor-board-capabilities-card.md CHANGELOG.md phase-loop-runtime/src/phase_loop_runtime/skills_bundle/*advisor*/SKILL.md skills-src/*/*advisor-board*/SKILL.md phase-loop-skills/advisor-board/SKILL.md` proves docs explain provider and backing limitations for local file references.
- [ ] `rg -n "#114|context_refs" CHANGELOG.md docs/advisor-board-capabilities-card.md` and `! rg -n "EZBidPro|PWA|PBS|NavBlue" CHANGELOG.md docs/advisor-board-capabilities-card.md` prove changelog or release notes describe the #114 behavior without exposing private examples or raw file contents.
- [ ] Migration examples show `context_refs` path/metadata-only usage and warn that unsupported remote backings may not have local file access.
- [ ] `! rg -n "EZBidPro|PWA|PBS|NavBlue" phase-loop-runtime/src/phase_loop_runtime/advisor_board/CONTRACTS.md phase-loop-runtime/src/phase_loop_runtime/skills_bundle/*advisor*/SKILL.md skills-src/*/*advisor-board*/SKILL.md phase-loop-skills/advisor-board/SKILL.md docs/advisor-board-capabilities-card.md CHANGELOG.md` proves CTXDOCS-owned docs contain no private EZBidPro, PWA, PBS, or NavBlue examples.
- [ ] `PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v6.md` and `git diff --check` pass before CTXDOCS execution closeout.
