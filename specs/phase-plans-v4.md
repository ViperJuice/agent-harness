# agent-harness — Docs-Freshness Enforcement (release & public-surface) — Phase Plan v4

> How to use this document: save to `specs/phase-plans-v4.md`, then run `/claude-plan-phase <ALIAS>` to produce the lane-level plan for each phase (→ `plans/phase-plan-v4-<alias>.md`), then `/claude-execute-phase <alias>` to build it.

> Resolves GitHub issue #18 (+ its follow-up comment). Builds on the rigor-v1 doc-delta gate already on `main` (`closeout_validators.py` doc-delta finding; `validate_plan_doc.py` SL-docs WARN).

---

## Context

Issue #18: a release roadmap completed green while public docs (README / package READMEs / CHANGELOG / operations runbooks) were stale, because the plan's docs lane was **scoped too narrowly** (README/CHANGELOG weren't owned files). The follow-up comment generalizes the root cause to a second, worse occurrence: a multi-phase build run via **direct `Agent()` briefs**, so the execute-phase pipeline — and its SL-docs lane — **never ran at all**. (The codex-on-Claw "plan-manifest unavailable, used fallback, noted it" report is a third instance of the same shape: a control that silently no-ops when its dependency is absent.)

**The unifying defect: every existing gate is a *code* gate, and the doc/freshness control lives *inside* the plan/execute skills.** That gives it two evasion paths — (1) under-scope the docs lane, (2) bypass the pipeline so the lane never executes — and in both cases the dropped step *is* the enforcement, so its absence is invisible (a gate that silently `SKIP`s itself). rigor-v1's doc-delta validator helps but shares the flaw: it runs in the closeout path, which the bypass defeats; it is `warn`-default; it is not release-aware; and it has no stale-text scan.

**The fix is a control that is pipeline-INDEPENDENT and fail-loud** — an audit that runs at merge/closeout regardless of *how* the work was produced: "public/release surfaces changed on this branch with no corresponding doc decision → **fail loud**, not skip." That closes both evasion paths and removes doc-freshness from per-run operator discretion (the stated intent of the SL-docs "no opt-out" rule).

**Autonomy-first is preserved.** The new fail-loud control is a **CI / merge gate** (Layer A) — a different surface from the *autonomous run-loop's* in-loop closeout gates, which stay `warn`-default and never set `human_required` (the rigor-v1 invariant). Blocking a *merge* on stale release docs does not add an in-loop human stall. The in-pipeline strengthening (Layer B) stays warn-by-default with block opt-in, except release/public-surface-class phases, which fail-loud (the user's "soft by default, hard for high-assurance" principle).

---

## Assumptions (fail-loud if wrong)

1. The closeout doc-delta seam is `closeout_validators.py` (the rigor-v1 `doc_delta` finding) reached from `build_phase_loop_closeout`; `validate_plan_doc.py` is the planner-side lint. Both already exist on `main`.
2. A branch/PR diff against a base ref is a sufficient, pipeline-agnostic input for the independent audit (`git diff <base>...HEAD`); the audit needs no `.phase-loop/` state to run.
3. "Public/release surface" is detectable from the diff: package manifest version/`version` changes, package count, CLI/MCP/runtime install-posture files, release-tag/workflow files, roadmap-completion markers, and the durable docs set (README, package READMEs, CHANGELOG/release-notes, operations runbooks). A configurable surface map (with explicit opt-out) is acceptable.
4. A "doc decision" is satisfiable EITHER by a doc change in the diff (README/CHANGELOG/module-README/operations) OR by a recorded decision token (the rigor-v1 `doc_delta_decision` / a `spec_delta_closeout` lifecycle event) discoverable without the pipeline having run.
5. The audit is a CLI subcommand wired into CI as a required check; it never sets `human_required` and never blocks the autonomous run-loop.

## Non-Goals

- **No change to the autonomous run-loop's in-loop gates.** They stay `warn`-default, never `human_required`. The fail-loud surface is CI/merge only.
- No auto-writing of docs. The audit detects and blocks/reports; it does not generate doc content.
- No new providers/executors; no rework of the rigor-v1 severity model beyond adding the release-aware surface + the stale-text scan.
- The audit is diff-scoped; it is not a whole-repo doc linter.

---

## Cross-Cutting Principles

1. **Pipeline-independent + fail-loud is the load-bearing property.** A control that only lives in `plan-phase`/`execute-phase` is defeated by not running them. Layer A runs from the diff alone and exits non-zero — its absence cannot be silent.
2. **No silent skip.** Wherever the audit cannot evaluate a surface (missing base ref, unreadable manifest), it reports `blocked` with a reason and exits non-zero — never a quiet `passed`. (Directly answers the codex-on-Claw class: a control's inability to run must be visible.)
3. **Soft by default, hard for release.** General public-surface changes warn (rigor-v1 default); release/package/install-posture changes fail-loud. One severity knob, two defaults.
4. **One surface taxonomy, two consumers.** The public/release-surface map, the doc-decision record, and the stale-text patterns are shared modules used by BOTH the independent audit (Layer A) and the in-pipeline gate (Layer B), so they can never drift (the bundle-vs-commit lesson from model-routing-v2).
5. **Honest reporting.** Final closeout carries `docs_freshness: passed|skipped|blocked` with evidence paths, so a clean worktree alone cannot imply docs are current.

---

## Phase Dependency DAG

```
  P1  Pipeline-independent fail-loud `docs-audit` gate (diff-driven) + docs_freshness report  ← the load-bearing fix
   │
   ▼
  P2  Stale-text scanner (shared module: placeholders / stale counts / unlabeled-historical versions)
   │
   ▼
  P3  Release-awareness in the in-pipeline layer (doc-delta validator + validate_plan_doc) + docs_freshness in closeout schema
   │
   ▼
  P4  Release-dispatch post-dispatch reducer + invariants, tests & docs
```

(Serial: P2's scanner and the P1 surface taxonomy are the shared contract P3/P4 consume; P1 ships the audit skeleton + the frozen surface/report schema first.)

---

## Top Interface-Freeze Gates

1. **IF-0-P1-1** — Surface & decision contract: (a) the public/release-surface taxonomy (what counts as a release/package/install-posture/roadmap-completion change vs a general public surface), (b) the doc-decision record (the diff-doc-change set + the recoverable `doc_delta_decision`/`spec_delta_closeout` token format), and (c) the `docs_freshness: passed|skipped|blocked` report schema with evidence paths. Frozen in P1; consumed by the stale scanner (P2), the in-pipeline gate (P3), and the reducer (P4).

---

## Phases

### Phase 1 — Pipeline-Independent `docs-audit` Gate (P1)

**Objective**
The load-bearing fix: a `phase-loop docs-audit --base <ref>` CLI that runs on the branch/PR diff alone, independent of whether plan/execute-phase ran — freezing the shared surface/decision/report contract (IF-0-P1-1) and failing loud on an unsatisfied release surface.

**Exit criteria**
- [ ] `phase-loop docs-audit --base <ref>` computes `git diff <base>...HEAD`, classifies changed paths against the IF-0-P1-1 surface taxonomy, and detects release/package/install-posture/roadmap-completion changes.
- [ ] For each changed public/release surface, it checks the doc-decision contract (a corresponding doc change in the diff OR a recoverable `doc_delta_decision`/`spec_delta_closeout` token); an unsatisfied **release-class** surface → **fail-loud (non-zero exit)** with the specific surfaces + remediation; a general public surface with no decision → warn (configurable).
- [ ] Emits a `docs_freshness: passed|skipped|blocked` record with evidence paths (the surfaces changed, the decisions found/missing). `blocked` whenever a surface can't be evaluated (missing base ref / unreadable manifest) — never a silent `passed`.
- [ ] A CI workflow runs `docs-audit` as a required check on PRs (the pipeline-independent enforcement point).
- [ ] Tests: a release-surface change with no doc decision → non-zero + named surface; with a README/CHANGELOG change → pass; with a recorded decision token → pass; an un-evaluable surface → `blocked` non-zero. No `.phase-loop/` state required for any of these.

**Scope notes**
- Decompose into 2 lanes owning disjoint regions: (a) the shared contract modules (IF-0-P1-1: the surface taxonomy + doc-decision record + `docs_freshness` report schema) and the `docs-audit` CLI logic over them; (b) the CI workflow wiring + the diff-driven, no-`.phase-loop`-state tests. Lane (a) is the integrator owning the new shared modules consumed by P2–P4.

**Non-goals**
- No in-pipeline strengthening (P3), no stale scanner internals (P2) — P1 ships the audit skeleton + frozen contract; a missing decision token simply fails/ warns by surface class.

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/cli.py
- phase-loop-runtime/src/phase_loop_runtime/docs_audit.py
- phase-loop-runtime/src/phase_loop_runtime/docs_surfaces.py
- .github/workflows/docs-audit.yml

**Depends on**
- (none)

**Produces**
- IF-0-P1-1

---

### Phase 2 — Stale-Text Scanner (P2)

**Objective**
A shared, side-effect-free stale-text scanner the audit (P1) and the closeout (P3) both call, so placeholders and stale counts can't survive a green release.

**Exit criteria**
- [ ] A `docs_stale_scan` module flags, within the changed durable-doc set: placeholders (`pending`, `TBD`, `recovery commit pending`, `coming soon`), stale package-count phrasing (e.g. `publishes three`/`N packages` inconsistent with the manifest set), and old release versions not explicitly labeled historical.
- [ ] The patterns + the historical-label exemption are configurable and unit-tested against fixtures (true positives + the labeled-historical negative).
- [ ] The scanner is consumed by `docs-audit` (P1) — a stale hit on a release phase contributes a fail-loud finding — exposed as a pure function for reuse by P3.
- [ ] Tests: each placeholder/stale-count/old-version case flags; a correctly `(historical)`-labeled version does not.

**Scope notes**
- Single lane: the `docs_stale_scan` pure-function module + its configurable patterns + fixtures/tests, wired into the P1 audit. Justified single lane — one coherent, side-effect-free scanner with no `runner.py` change.

**Non-goals**
- No new surfaces (P1 owns the taxonomy); no closeout wiring (P3).

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/docs_stale_scan.py
- phase-loop-runtime/src/phase_loop_runtime/docs_audit.py

**Depends on**
- P1

**Produces**
- (none)

---

### Phase 3 — Release-Awareness In-Pipeline + `docs_freshness` Closeout Field (P3)

**Objective**
Strengthen the rigor-v1 in-pipeline layer so early (pre-merge) feedback is release-aware and the closeout reports `docs_freshness` — Layer A (P1) remains the independent backstop.

**Exit criteria**
- [ ] The `closeout_validators.py` doc-delta finding becomes release-aware: a phase that changes package versions/count/install-posture/release tags/roadmap-completion/operations must own/update README + relevant package READMEs + CHANGELOG/release-notes + operations docs, OR record an explicit per-surface no-doc-change decision. Release-class → block; general public surface → warn (unchanged default).
- [ ] `validate_plan_doc.py` gains a WARN when a plan's lanes touch manifests/release artifacts but no docs lane covers the README/package-README/CHANGELOG/operations surfaces (the #18 under-scope case, caught at plan time).
- [ ] The closeout report carries `docs_freshness: passed|skipped|blocked` with evidence paths (shared schema with P1), so a clean worktree alone never implies docs are current.
- [ ] Both consume the P1 surface taxonomy and the P2 scanner — no parallel re-implementation.
- [ ] Tests: a release-class closeout with stale/absent public docs → block finding + `docs_freshness: blocked`; a recorded no-doc decision → pass; an under-scoped plan → validate_plan_doc WARN.

**Scope notes**
- Decompose into 2 lanes: (a) `closeout_validators.py` release-awareness + the `docs_freshness` closeout-report field (consuming the P1 schema + P2 scanner); (b) the `validate_plan_doc.py` release-aware under-scope WARN. Both reuse the shared modules — no parallel re-implementation.

**Non-goals**
- Does not replace the independent audit (P1) — this is early feedback, not the primary control; the autonomous in-loop default stays `warn` except release-class.

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/closeout_validators.py
- phase-loop-runtime/src/phase_loop_runtime/validate_plan_doc.py

**Depends on**
- P1
- P2

**Produces**
- (none)

---

### Phase 4 — Release-Dispatch Reducer, Invariants, Tests & Docs (P4)

**Objective**
Close the `recovery commit pending` class at its source, lock in the CI invariants, and land the docs/contract/skill sweep — the honest, hardened finish.

**Exit criteria**
- [ ] A release-dispatch post-dispatch reducer: for release recovery where a doc's exact commit SHA / workflow result could not be known before tag creation, a reducer lane revisits the pre-dispatch evidence docs after the tag/workflow result exists (closing the `recovery commit pending` placeholder class at the source).
- [ ] CI invariants: `docs-audit` is a required PR check; a release-surface change with no doc decision fails the check; the autonomous run-loop is unaffected (no `human_required`, no in-loop block from the audit).
- [ ] Docs: `protocol.md` (the `docs_freshness` record + the pipeline-independent audit), `README.md`, CHANGELOG (`#18` entry), and the plan/execute skill text (the release-aware docs lane + the audit). Fix the codex-comment doc nit in passing: clarify that `plan-manifest` is a Python helper (`phase_loop_runtime.plan_manifest.*`), not a CLI command.
- [ ] `validate-roadmap specs/phase-plans-v4.md` passes; full standalone suite green; #12 drift guard green.

**Scope notes**
- Single lane: the release-dispatch reducer + the CI invariant suite + the docs/contract/skill sweep (incl. the `plan-manifest` doc-nit fix), landed atomically. Sequenced after P1–P3.

**Non-goals**
- No new audit surfaces or scanner patterns beyond P1/P2; docs-only + reducer + invariants.

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/docs_audit.py
- phase-loop-runtime/src/phase_loop_runtime/_contract_docs/phase-loop/protocol.md
- CHANGELOG.md
- phase-loop-skills/plan-detailed/SKILL.md

**Depends on**
- P1
- P2
- P3

**Produces**
- (none)

---

## Execution Notes

- Layer A (P1) is the fix that closes both evasion paths; if scope must be cut, P1 alone is shippable value. P3 is the early-feedback complement, not the primary control.
- The audit must run with **no** `.phase-loop/` state (it's diff-driven) — that independence is the whole point; guard it in tests.
- Keep the surface taxonomy + decision record + stale patterns as single shared modules (Principle 4); do not let Layer A and Layer B drift.
- Autonomy-first: the fail-loud surface is CI/merge only; the in-loop autonomous gates stay warn-default. Verify the existing rigor-v1 autonomous-unchanged tests still pass.

## Acceptance Criteria

- [ ] `phase-loop docs-audit` runs from a diff alone (no pipeline state), fails loud on an unsatisfied release surface, and emits `docs_freshness` with evidence; wired as a required CI check.
- [ ] The stale-text scanner flags placeholders/stale-counts/unlabeled-historical versions and is shared by the audit + closeout.
- [ ] The in-pipeline doc-delta gate + `validate_plan_doc` are release-aware; closeout reports `docs_freshness`.
- [ ] A release-dispatch reducer closes the `recovery commit pending` class; the codex-comment `plan-manifest` doc nit is fixed.
- [ ] Autonomous run-loop unchanged (no `human_required`); full suite + `validate-roadmap specs/phase-plans-v4.md` green.

## Verification

```bash
# Roadmap lints clean
PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v4.md

# Pipeline-independent audit (after P1)
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/ -k "docs_audit or docs_freshness" -q

# Stale scanner (after P2)
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/ -k "docs_stale or stale_scan" -q

# Release-aware in-pipeline gate (after P3)
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/ -k "doc_delta or validate_plan or closeout" -q

# Full standalone suite (after P4)
cd phase-loop-runtime && PYTHONPATH=src python -m pytest -q
```
