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
3. "Public/release surface" is **already partly defined in code** — `models.PUBLIC_SURFACE_GLOBS` (general public: `cli.py`, `*.proto`, `schema*.json`, README, CHANGELOG, `_contract_docs/**`) and `release_guard.RELEASE_AFFECTING_PATTERNS` (release-class: `.github/workflows/**`, `pyproject.toml`, `VERSION`, lockfiles, `docs/release/**`). The audit **unifies / re-exports these two existing sets** plus the durable docs set (package READMEs, operations runbooks); it does **not** introduce a third parallel taxonomy. A configurable surface map (with explicit opt-out) is acceptable.
4. The "doc decision" contract is **per-surface and relevance-bound** (panel-reconciled — a bare diff-touch or boilerplate token is rubber-stampable):
   - **Every** changed public surface must carry a decision — a relevant doc change OR a recorded `doc_delta_decision` token (`no_doc_delta` + a constrained reason / `docs_updated` / `docs_follow_up_filed`). A changed public surface with **no** decision → fail (this closes the general-surface silent-absence leak, not just release-class).
   - **Release-class** surfaces are stricter: the decision must be `docs_updated` with **relevance binding** — the changed release surface maps to its required doc surfaces (e.g. a version bump → CHANGELOG + the relevant package README) and *those exact surfaces* changed. `docs_follow_up_filed` and bare `no_doc_delta` do **not** satisfy a release-class gate.
   - The decision record must be repo-recoverable WITHOUT `.phase-loop/` state (today `doc_delta_decision` is a terminal-summary field — P1 defines a versioned, repo-visible decision artifact with per-surface bindings, base/head identity, reason codes, and evidence paths). Recording silence as an *auditable, relevance-checked* decision is the win; P2's stale-text scan is the content-freshness anti-gaming layer on top. (A semantic/LLM freshness check is a noted future option, not required.)
5. **The autonomous run-loop pushes directly to `main`** (`runner.py:7715`, `closeout_mode=="push"` → `git push HEAD:{push_ref}`); there is **no `gh pr create`** in the runtime. Therefore (per the resolved decision) the audit runs as a CI check on **BOTH `pull_request` (blocks the merge) AND `push: branches:[main]` (red-marks the commit post-hoc)** — matching the existing `test.yml`/`scrub.yml` trigger pattern. The push-on-main coverage is **detect-and-alert, not prevent** — a deliberate choice to preserve the autonomy model (no forced PRs / no branch-protection precondition). The audit never sets `human_required` and never blocks the in-loop autonomous run.
6. Diff input reuses the existing helper pattern (`verification_evidence.py:280` `git diff --name-only <base_ref>`, `git_topology.py:119` `{base}...HEAD`) rather than a new git wrapper. `--base` must also resolve for the **tag-push / no-PR** context (`release-consistency.yml` triggers on `push: tags:['v*']`, which has no PR base ref).

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
6. **Reuse, don't duplicate (panel-reconciled).** The surface taxonomy unifies the two EXISTING code definitions (`models.PUBLIC_SURFACE_GLOBS`, `release_guard.RELEASE_AFFECTING_PATTERNS`); P4 EXTENDS `release_guard.py`; the diff helper reuses `verification_evidence.py`. No greenfield module duplicates existing prior art. The one consumer that cannot import (the vendored `validate_plan_doc.py` skill script) gets a drift-guard test, not an import — the only drift-proof option for a vendored copy.
7. **Coverage matches how work actually lands (panel-reconciled).** The autonomous loop pushes directly to `main` (no PR), so the audit triggers on BOTH `pull_request` (pre-merge block) and `push:main` (post-hoc detect-and-alert). Direct-push autonomy is preserved by choice; main-push coverage is detect, not prevent.

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

1. **IF-0-P1-1** — Surface & decision contract: (a) the public/release-surface taxonomy as the **single canonical module that `models.PUBLIC_SURFACE_GLOBS` and `release_guard.RELEASE_AFFECTING_PATTERNS` are refactored to / re-exported from** (release/package/install-posture/roadmap-completion = release-class; the rest = general public) — NOT a greenfield third copy; (b) the doc-decision record (the diff-doc-change set + the recoverable `doc_delta_decision`/`spec_delta_closeout` token format); (c) the `docs_freshness: passed|skipped|blocked` report schema with evidence paths; and (d) the `--base` resolution rule across the three CI contexts (PR base, `push:main` → previous commit / merge-base, `push:tags:['v*']` → prior tag, since there is no PR base). Frozen in P1; consumed by the stale scanner (P2), the in-pipeline gate (P3, via a **vendored copy + drift-guard** for the standalone plan-lint), and the release_guard extension (P4).

---

## Phases

### Phase 1 — Pipeline-Independent `docs-audit` Gate (P1)

**Objective**
The load-bearing fix: a `phase-loop docs-audit --base <ref>` CLI that runs on the branch/PR diff alone, independent of whether plan/execute-phase ran — freezing the shared surface/decision/report contract (IF-0-P1-1) and failing loud on an unsatisfied release surface.

**Exit criteria**
- [ ] `phase-loop docs-audit --base <ref>` computes `git diff <base>...HEAD`, classifies changed paths against the IF-0-P1-1 surface taxonomy, and detects release/package/install-posture/roadmap-completion changes.
- [ ] For each changed public surface it enforces the per-surface, relevance-bound decision contract (Assumption 4): a **release-class** surface unsatisfied (no relevance-bound `docs_updated`) → **fail-loud (non-zero exit)** with the specific surfaces + remediation; a **general** public surface with **no recorded decision at all** → fail (closing the silent-absence leak), with a recorded `no_doc_delta`+reason → pass. A README whitespace edit does NOT satisfy a release-class surface (relevance binding: the changed surface must map to its required doc surfaces).
- [ ] Emits a `docs_freshness: passed|skipped|blocked` record with evidence paths (the surfaces changed, the decisions found/missing). `blocked` whenever a surface can't be evaluated (missing base ref / unreadable manifest) — never a silent `passed`.
- [ ] A CI workflow runs `docs-audit` on **`pull_request` (required check — blocks the merge) AND `push: branches:[main]` (red-marks the commit post-hoc)** — matching `test.yml`/`scrub.yml` — so the autonomous direct-to-main path (which never opens a PR) is still covered (detect-and-alert), with no forced PRs / branch protection.
- [ ] Tests: a release-surface change with no doc decision → non-zero + named surface; with a README/CHANGELOG change → pass; with a recorded decision token → pass; an un-evaluable surface → `blocked` non-zero; `--base` resolves in the PR, `push:main`, and `push:tags` contexts. No `.phase-loop/` state required for any of these.

**Scope notes**
- Decompose into 2 lanes owning disjoint regions: (a) **refactor `models.PUBLIC_SURFACE_GLOBS` + `release_guard.RELEASE_AFFECTING_PATTERNS` into the single canonical IF-0-P1-1 taxonomy** + the doc-decision record + `docs_freshness` schema, and the `docs-audit` CLI logic over them (reusing the `verification_evidence.py` diff helper); (b) the CI workflow (both triggers) + the diff-driven, no-`.phase-loop`-state tests. Lane (a) is the integrator owning the unified shared modules consumed by P2–P4.

**Non-goals**
- No in-pipeline strengthening (P3), no stale scanner internals (P2) — P1 ships the audit skeleton + frozen contract; a missing decision token simply fails/warns by surface class.
- **No greenfield surface module and no new git wrapper** — unify the two existing taxonomies and reuse the existing diff helper.

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/cli.py
- phase-loop-runtime/src/phase_loop_runtime/docs_audit.py
- phase-loop-runtime/src/phase_loop_runtime/models.py
- phase-loop-runtime/src/phase_loop_runtime/release_guard.py
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
- [ ] The `closeout_validators.py` doc-delta finding becomes release-aware: a phase that changes package versions/count/install-posture/release tags/roadmap-completion/operations must own/update README + relevant package READMEs + CHANGELOG/release-notes + operations docs, OR record an explicit per-surface decision. Release-class emits a `block`-**severity** finding; **but it stays `warn`-effective in-loop under the default `PHASE_LOOP_REVIEW=warn` and never sets `human_required`** — Layer B is advisory early feedback; the actual blocking is the Layer A CI gate. A test asserts an autonomous release-class closeout records the finding yet never terminal-blocks.
- [ ] `validate_plan_doc.py` gains a WARN when a plan's lanes touch manifests/release artifacts but no docs lane covers the README/package-README/CHANGELOG/operations surfaces (the #18 under-scope case, caught at plan time). **NOTE: `validate_plan_doc.py` is a stdlib-only standalone script vendored into the skill bundles from two canonical sources (`phase-loop-skills/{plan,execute}-phase/scripts/`), built by `build_bundle.py` — it CANNOT import the P1 runtime taxonomy.** It therefore carries a **vendored copy of the surface set + a drift-guard test** (the #12 `skills_bundle` drift-guard pattern) asserting it matches the canonical IF-0-P1-1 set — not an import.
- [ ] The closeout report carries `docs_freshness: passed|skipped|blocked` with evidence paths (shared schema with P1), so a clean worktree alone never implies docs are current.
- [ ] The `closeout_validators.py` consumer imports the unified P1 taxonomy + P2 scanner; the `validate_plan_doc.py` consumer uses the vendored copy guarded by the drift test — no silent divergence.
- [ ] Tests: a release-class closeout with stale/absent public docs → block finding + `docs_freshness: blocked`; a recorded no-doc decision → pass; an under-scoped plan → validate_plan_doc WARN; the surface-set drift-guard fails if the vendored copy diverges from canonical.

**Scope notes**
- Decompose into 2 lanes: (a) `closeout_validators.py` release-awareness + the `docs_freshness` closeout-report field (importing the P1 taxonomy + P2 scanner); (b) the `validate_plan_doc.py` release-aware under-scope WARN in the two canonical bundle scripts + the drift-guard test (vendored-copy consumer — it cannot import).

**Non-goals**
- Does not replace the independent audit (P1) — this is early feedback, not the primary control; the autonomous in-loop default stays `warn` except release-class.

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/closeout_validators.py
- phase-loop-skills/plan-phase/scripts/validate_plan_doc.py
- phase-loop-skills/execute-phase/scripts/validate_plan_doc.py
- phase-loop-runtime/scripts/sync_skills_bundle.py

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
- [ ] A release-dispatch post-dispatch reducer **extending the existing `release_guard.py`** (`is_release_dispatch_plan`, `release_dispatch_blocker`, `_release_base_ref`, `_is_release_affecting_path`) — NOT a greenfield module: for release recovery where a doc's exact commit SHA / workflow result could not be known before tag creation, revisit the pre-dispatch evidence docs after the tag/workflow result exists. **Framed as post-release evidence *repair*, not a freshness substitute** (panel: a reducer can't make the *tagged* commit's docs fresh) — so the **release/publish job runs `docs-audit` BEFORE publishing** and fails on placeholders, and the reducer only repairs residual evidence after the tag exists. Together these close the `recovery commit pending` class at the source.
- [ ] CI invariants: `docs-audit` is a required check on PRs (blocks merge) and runs post-hoc on `push:main` (red-marks); a release-surface change with no doc decision fails the check; the autonomous run-loop is unaffected (no `human_required`, no in-loop block from the audit; direct-push autonomy preserved — coverage is detect-and-alert on main).
- [ ] Docs: `protocol.md` (the `docs_freshness` record + the pipeline-independent audit), `README.md`, CHANGELOG (`#18` entry), and the plan/execute skill text (the release-aware docs lane + the audit). Fix the codex-comment doc nit in passing: clarify that `plan-manifest` is a Python helper (`phase_loop_runtime.plan_manifest.*`), not a CLI command.
- [ ] `validate-roadmap specs/phase-plans-v4.md` passes; full standalone suite green; #12 drift guard green.

**Scope notes**
- Single lane: the release-dispatch reducer + the CI invariant suite + the docs/contract/skill sweep (incl. the `plan-manifest` doc-nit fix), landed atomically. Sequenced after P1–P3.

**Non-goals**
- No new audit surfaces or scanner patterns beyond P1/P2; docs-only + reducer + invariants.

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/release_guard.py
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

- **The MVP is P1 + P2, not P1 alone** (panel-reconciled). P1's diff-presence/decision check without P2's stale-text scan is "some doc activity occurred," not "docs are fresh" — a release-class doc could be touched yet still say `recovery commit pending`. Ship P1+P2 as the freshness unit; P3/P4 are genuinely cuttable. If cutting, cut P3 (Layer B) before P1/P2.
- **All non-bypassable claims live on Layer A only.** Layer B (the closeout validator) is *advisory/early-feedback by construction* — the validator loader swallows import failures and `block` severity is forced to `warn` under the default `PHASE_LOOP_REVIEW=warn`. Do not assert Layer B is non-bypassable; it catches under-scoped planning earlier, nothing more.
- **Enforce on every shipping path, not just PRs.** The audit is a check on `pull_request` (blocks merge) + `push:main` (post-hoc red-mark) + a **dependency of any release/publish job** (a publish must not proceed on stale/placeholder release docs) + the `push:tags:['v*']` context. Note explicitly that "required check" is partly external **GitHub branch-protection** configuration, not just a repo file — track it as a stated invariant even under the detect-and-alert choice.
- The audit must run with **no** `.phase-loop/` state (it's diff-driven) — that independence is the whole point; guard it in tests.
- Keep the surface taxonomy + decision record + stale patterns as the single unified module (Principle 6); the one vendored consumer (`validate_plan_doc.py`) gets a drift-guard test, not an import.
- Autonomy-first: the fail-loud surface is CI only; the in-loop autonomous gates stay warn-effective by default (release-class produces a `block`-severity finding that is still `warn`-effective in-loop — the actual blocking is the CI gate). Verify the rigor-v1 autonomous-unchanged tests still pass; P3 tests assert an autonomous closeout never terminal-blocks or sets `human_required`.

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
