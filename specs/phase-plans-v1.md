# agent-harness — Planning & Execution Rigor — Phase Plan v1

> How to use this document: save to `specs/phase-plans-v1.md`, then run `/claude-plan-phase <ALIAS>` to produce the lane-level plan for each phase (→ `plans/phase-plan-v1-<alias>.md`), then `/claude-execute-phase <alias>` to build it.

---

## Context

A repo-wide assessment of the phase-loop skills and runtime surfaced six gaps between what the skills *say* and what the runtime *enforces*. The methodology used the repo's own skills/runtime as the source of truth; every gap is cited to a file.

The thesis: the planning skills already carry strong prose (acceptance criteria must be testable; a no-opt-out docs lane; verification commands must be concrete), but the **mechanical floor under that prose is thin** — `validate_plan_doc.py` checks heading presence, not substance; closeout trusts a self-reported `verification_status=passed` for generic phases; and entire categories (visual verification, code↔doc currency, difficulty-aware model selection) are absent. This roadmap raises the enforcement floor to match the prose, and closes two correctness gaps (an inert `/clear` instruction in the autonomous loop; the always-highest-model default).

The load-bearing structural decision: today three of the proposed gates (doc-delta, verification-evidence, visual-evidence) all hinge on the single pass/fail decision in `closeout.py` (`build_closeout_status`, ~`closeout.py:496`). Rather than have three phases edit that one site, **Phase 1 installs a pluggable closeout-validator hook** and the schema seams; every later enforcement phase adds its validator in a disjoint module the hook calls. This keeps `closeout.py`/`models.py` single-writer (P1) and lets P2/P5/P6 run in parallel without cross-phase dirty conflicts.

---

## Assumptions (fail-loud if wrong)

1. The closeout pass/fail decision can be refactored to dispatch to a list of registered validators without changing existing event/schema semantics for phases that register no new validator (back-compat preserved).
2. Public-interface change detection (for the doc-delta gate) can be approximated from the phase diff + owned-file globs without a full type/AST analysis — a heuristic with an explicit opt-out is acceptable.
3. The harness exposes browser/screenshot tooling at execution time (claude-in-chrome MCP and Playwright-via-PMCP are available), so a mandated visual check is runnable, not aspirational.
4. The runtime already resolves model+effort per action/lane via `resolve_execution_policy()` (`profiles.py:155`) and consumes an "Execution policy" plan section — so planner-driven tiering needs no new runtime routing, only emitted policy lines.

## Non-Goals

- **No gate blocks the autonomous loop by default.** Every new gate (doc-delta, verification-evidence, visual-evidence) ships at `warn` severity: record and continue. Promoting any of them to `block` is an explicit operator opt-in. We are **not** making verification-evidence required-for-all-phases-by-default — that strictness is available via opt-in, off by default. `PHASE_LOOP_VERIFY_ENFORCE` semantics are left intact.
- **No new `human_required` conditions.** None of the review gates may set `human_required=true`. Human attention is solicited only through bounded runs and the end-of-run findings summary.
- **No difficulty auto-router in the runtime (this version).** P4 puts the difficulty judgment in the planner (which already reasons about complexity) and emits existing-format policy lines. A runtime `difficulty` field on `ExecutionPolicyRule` is an explicitly deferred stretch lane, not a required deliverable.
- No new model providers or executors; we tune selection across the tiers already in `profiles.py` / `capability_registry.py`.
- No change to the interactive-TUI human handoff UX beyond branching it away from the autonomous path (P3).
- No full Markdown parser for the validators — heuristic regex checks consistent with the existing `roadmap_lint.py` / `validate_plan_doc.py` style.

---

## Cross-Cutting Principles

1. **Autonomy-first — soft by default, strict by opt-in.** The phase-loop runner must be able to drive every phase unattended. Every new quality gate in this roadmap defaults to **`warn` severity**: it records a finding to the ledger (an audit trail for later human spot-check) and the loop **continues** — it does not halt and never sets `human_required`. Escalating a gate to **`block`** (refuse closeout) is an explicit operator opt-in, per-gate or globally. `human_required=true` stays reserved for the existing genuinely-human-only conditions (missing secret, destructive op, product decision) — new review gates never raise it. This mirrors the existing `PHASE_LOOP_VERIFY_ENFORCE=warn|hard` precedent, but makes the **soft mode the default**.
2. **Human review is a cadence knob, not an in-loop stall.** Operators control how often a human looks by *bounding the run* (`--max-phases` / `--full-phase`), reviewing the ledger between runs — not by gates that block mid-loop waiting for a human. A clean end-of-run summary (findings raised at `warn`) is the review surface.
3. **Enforcement lives in the runtime; guidance lives in the skills.** Every new rule gets a mechanical check (validator or closeout finding), not just prose — prose that the linter cannot see is treated as undone. "Mechanical" includes `warn`-level findings; a check need not block to be real.
4. **Single pass/fail seam.** No phase other than P1 edits `closeout.py`'s status decision; downstream phases register validators through the P1 hook, which carries each finding's severity.
5. **Every gate is overridable with an audited reason — and no gate is a dead-end.** When a gate is dialed to `block`, it must offer a recovery/opt-out path that records *why* (mirroring `spec_delta_closeout.v1` and the `--allow-cross-phase-dirty` pattern). At the default `warn` severity there is nothing to escape — the loop already continued.
6. **Mode-awareness.** Instructions that assume a human operator (e.g. `/clear`) must be gated to the interactive path and never emitted in autonomous/adapter mode.
7. **Back-compat by default.** A phase that adopts none of the new fields behaves exactly as before; strictness is opt-in, never retroactive breakage.

---

## Phase Dependency DAG

```
  P1  Closeout & verification contract (hook + schema seams)
   │
   ├──────────────┬──────────────┐
   ▼              ▼              ▼
  P2  Doc        P5  DoD &      P6  Visual /
  enforcement    verification   browser
   │              │              │
   └──────────────┴──────────────┘
                  ▼
  P7  Canonicalization & cross-cutting cleanup

  P3  Mode-aware handoff & subagent dispatch   parallel (independent root)
  P4  Task-aware model/effort selection        parallel (independent root)
```

---

## Top Interface-Freeze Gates

These gates are the narrowest contracts that unblock downstream phases. `/claude-plan-phase` concretizes each (exact signature/schema) when it plans the owning phase.

1. **IF-0-P1-1** — Closeout validator-hook registry **with a severity model**: a registration interface (e.g. `register_closeout_validator(fn, severity)`) where each validator receives the closeout context (phase, diff/owned paths, terminal_summary, plan metadata) and returns `pass` or a **finding** carrying `code` + `reason`. Each finding has a severity of `warn` (record to the ledger, loop continues, `human_required` untouched — the default) or `block` (refuse `complete`, with an audited recovery path). A global/per-gate strictness control (flag/env, e.g. `PHASE_LOOP_REVIEW=off|warn|block`, default `warn`) sets the effective severity. `build_closeout_status` iterates validators and downgrades/blocks per the resolved severity. **No validator may set `human_required`.**
2. **IF-0-P1-2** — `doc_delta_closeout.v1` decision schema (decision literal + target-surface globs + evidence/justification fields) plus the public-surface change-detector contract it keys on (CLI flags, exported symbols, config/openapi schema, contract docs).
3. **IF-0-P1-3** — Verification-evidence policy: a `verification_evidence_required` knob whose **default is off (warn-only)**; when opted in, missing evidence becomes a `block` finding carrying a typed reason code. The existing `RG` / `--verification-log` opt-in paths and `PHASE_LOOP_VERIFY_ENFORCE` continue to work.
4. **IF-0-P1-4** — `visual_evidence` closeout field: screenshot/artifact path(s) + a visually-observable assertion, with a `ui_change_detected` predicate (UI-glob heuristic) that determines when it is *expected*. Absence is a `warn` finding by default.
5. **IF-0-P1-5** — Canonical definition-of-done literal: one stable term (e.g. `acceptance_criteria`) and its presence/shape contract, replacing the fragmented "Acceptance criteria / Acceptance Criteria / Exit criteria" vocabulary across skills and validators.

---

## Phases

### Phase 1 — Closeout & Verification Contract (P1)

**Objective**
Install the shared runtime seams every enforcement phase depends on: a pluggable closeout-validator hook with a `warn`/`block` severity model, a global strictness control defaulting to `warn`, and the schema additions — landed as back-compatible stubs with no behavior change until a validator registers.

**Exit criteria**
- [ ] `register_closeout_validator(fn, severity)` exists and `build_closeout_status` iterates registered validators before returning `complete`; with zero validators registered, every existing closeout test passes unchanged (`PYTHONPATH=src python -m pytest tests/ -k closeout`).
- [ ] The severity model exists: a finding at `warn` records to the ledger and the loop continues; a finding at `block` refuses `complete`; resolved severity is governed by a strictness control (e.g. `PHASE_LOOP_REVIEW=off|warn|block`) defaulting to `warn`. A test asserts a `warn` finding does NOT block and does NOT set `human_required`.
- [ ] No validator path can set `human_required=true` (asserted by a test that a registered blocking validator yields `human_required=false`).
- [ ] `doc_delta_closeout.v1` schema and the public-surface detector contract are defined and exported via `schema_export.py` (`export-schema` round-trips without error).
- [ ] `verification_evidence_required` knob exists in `models.py` defaulting to off (warn-only), with a typed reason-code enum; behavior is unchanged this phase (the opt-in path lands in P5).
- [ ] `visual_evidence` field and a `ui_change_detected` predicate exist in `models.py` with unit coverage of the UI-glob heuristic.
- [ ] A single canonical definition-of-done literal is defined in `models.py` and referenced by a schema constant.

**Scope notes**
- Decompose into 3 lanes, each owning disjoint files: (a) `closeout.py` + the validator-hook registry + canonical-DoD literal in `models.py`; (b) `verification_evidence.py` + `closeout_validation.py` evidence-policy plumbing; (c) `visual_evidence` field + `ui_change_detected` + `schema_export.py`. Lane (a) is the single writer of `closeout.py` for the whole roadmap and publishes IF-0-P1-1 and IF-0-P1-5 first so the others compile against them.
- Single-writer files: `closeout.py` and `models.py` are owned by lane (a); lanes (b)/(c) extend via new modules and imports, never editing the status decision directly.

**Non-goals**
- No enforcement behavior changes this phase — validators are registered by P2/P5/P6. P1 only proves the seam compiles and is back-compatible.

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/closeout.py
- phase-loop-runtime/src/phase_loop_runtime/models.py
- phase-loop-runtime/src/phase_loop_runtime/verification_evidence.py
- phase-loop-runtime/src/phase_loop_runtime/closeout_validation.py
- phase-loop-runtime/src/phase_loop_runtime/schema_export.py

**Depends on**
- (none)

**Produces**
- IF-0-P1-1
- IF-0-P1-2
- IF-0-P1-3
- IF-0-P1-4
- IF-0-P1-5

---

### Phase 2 — Documentation-Update Enforcement (P2)

**Objective**
Turn the strongest prose rule (the no-opt-out docs lane) into a real gate: a doc-delta closeout validator, a plan-doc check that the terminal SL-docs lane exists, and an execution-skill rule that docs are updated as part of completing work.

**Exit criteria**
- [ ] A doc-delta closeout validator (registered via IF-0-P1-1 at default `warn`, consuming the IF-0-P1-2 schema) **records a finding** when the diff touches a public surface and no doc-delta decision is recorded; at default severity the loop continues (the finding surfaces in the run summary), and only at `block` does it refuse closeout. The agent can self-satisfy it by updating docs or recording an explicit `no_doc_delta` justification — no human needed.
- [ ] `validate_plan_doc.py` **warns** when a plan lacks a terminal SL-docs lane (new check + a test); promotion to a hard failure is opt-in, consistent with the autonomy-first default.
- [ ] `execute-detailed`/`execute-phase` skills state that a public-interface change should update the corresponding doc surface or record a `no_doc_delta` decision before closeout (so the agent self-satisfies the warn finding autonomously).
- [ ] `phase-loop-runtime/tests/` adds coverage: public-surface diff → finding raised; doc updated → clean; justified opt-out → clean; and at default `warn` the phase still reaches `complete`.

**Scope notes**
- Decompose into 3 lanes owning disjoint files: (a) runtime doc-delta validator module + tests (registers through the P1 hook); (b) `validate_plan_doc.py` SL-docs-lane check + tests; (c) skill-text edits in `execute-detailed`/`execute-phase`. Lane (c) owns the execute skill files; coordinate the docs-rule wording so P7's canonical sweep does not re-touch them.

**Non-goals**
- No new doc *content*; this phase enforces that doc updates happen, it does not write product docs.

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/closeout.py (read-only; register via IF-0-P1-1 hook, do not edit the status decision)
- phase-loop-skills/plan-phase/scripts/validate_plan_doc.py
- phase-loop-skills/execute-detailed/SKILL.md
- phase-loop-skills/execute-phase/SKILL.md

**Depends on**
- P1

**Produces**
- (none)

---

### Phase 3 — Mode-Aware Handoff & Subagent Dispatch (P3)

**Objective**
Fix the inert `/clear` instruction that leaks into the autonomous loop and give the loop a real continuity story: branch closeout guidance by mode, and allow dispatching a fresh-context subagent for the next plan/work when skill/tool-driven.

**Exit criteria**
- [ ] In autonomous/adapter mode, the closeout no longer emits the `/clear` recommendation (today at `execute-phase/_overrides/claude/SKILL.md:575`); a test or fixture asserts the autonomous path does not surface human-only reset wording.
- [ ] The interactive/human path retains the "recommend next step + clean handoff" guidance, explicitly labeled as the interactive path.
- [ ] The autonomous-mode guidance documents the actual continuity mechanism (the runner re-invokes the next phase in fresh context) and permits dispatching a subagent (Task/Agent) to carry the next plan or piece of work when the loop is skill/tool-driven, rather than instructing the agent to clear itself.

**Scope notes**
- Single lane (skill-text + adapter-branch wording, narrow and tightly coupled); owns the execute-phase skill files and any adapter-mode conditional. Justified single lane: the change is one coherent edit to mode-branching guidance with no disjoint partition.

**Non-goals**
- No change to the file-based handoff format or the runner's per-phase process model; this phase aligns the *guidance* with the existing mechanism.

**Key files**
- phase-loop-skills/execute-phase/_overrides/claude/SKILL.md
- phase-loop-skills/execute-phase/SKILL.md

**Depends on**
- (none)

**Produces**
- (none)

---

### Phase 4 — Task-Aware Model/Effort Selection (P4)

**Objective**
Stop defaulting every phase to the highest tier. Have the planner pick the cheapest model/effort that will succeed per lane, emitted as the Execution-policy lines the runtime already consumes.

**Exit criteria**
- [ ] `plan-phase`/`plan-detailed` skills instruct the planner to assign per-lane model+effort by complexity and to emit matching "Execution policy" lines (the format parsed at `discovery.py:812`), defaulting to the cheapest viable tier and escalating only with a stated reason.
- [ ] The skills document the harness model/effort ladder per executor (from `profiles.py` / `capability_registry.py`) so the planner chooses from real, supported values.
- [ ] A worked example plan demonstrates a trivial lane assigned a lower effort/tier and a hard lane escalated, and `phase-loop`-resolved selection reflects the emitted policy (manual verification command included in the plan).

**Scope notes**
- Decompose into 2 lanes owning disjoint files: (a) `plan-phase` skill + override; (b) `plan-detailed` skill + override. Each owns its skill directory; the shared model-ladder reference text is authored once in lane (a) and linked from lane (b).

**Non-goals**
- No runtime difficulty auto-router; the optional `ExecutionPolicyRule.difficulty` field (`models.py:525`, consumed at `profiles.py:155`) is explicitly deferred.

**Key files**
- phase-loop-skills/plan-phase/SKILL.md
- phase-loop-skills/plan-phase/_overrides/claude/SKILL.md
- phase-loop-skills/plan-detailed/SKILL.md
- phase-loop-skills/plan-detailed/_overrides/claude/SKILL.md

**Depends on**
- (none)

**Produces**
- (none)

---

### Phase 5 — Definition-of-Done Rigor & Verification Linkage (P5)

**Objective**
Make the definition of done *checkable* without sacrificing autonomy: a verification-evidence validator that is warn-by-default (opt-in to block), a planner rule linking each acceptance criterion to its proving command, and a testability check.

**Exit criteria**
- [ ] A verification-evidence validator (registered via IF-0-P1-1, consuming IF-0-P1-3) raises a `warn` finding when a phase reports `passed` with no evidence artifact; at default severity the loop continues, and only when opted in (`PHASE_LOOP_REVIEW=block` or the evidence-required knob) does it block. A test asserts default-warn does not stall and a phase declining evidence records a typed reason code.
- [ ] `validate_plan_doc.py` warns when an acceptance criterion names no proving command (or test file) and on non-testable bullets (heuristic: no command token / path / HTTP verb+status / comparison) — mirroring the existing grep/test-pairing check; promotion to hard failure is opt-in.
- [ ] Planning skills adopt the canonical definition-of-done term (IF-0-P1-5) and ask each `- [ ]` item to reference the verification command that checks it.
- [ ] The default-warn posture is documented alongside the opt-in path; `PHASE_LOOP_VERIFY_ENFORCE` semantics are unchanged (asserted by a regression test).

**Scope notes**
- Decompose into 3 lanes owning disjoint files: (a) runtime verification-evidence validator + tests (registers through the P1 hook); (b) `validate_plan_doc.py` testability + criterion↔command checks + tests; (c) plan-skill text adopting the canonical DoD term and linkage rule. Lane (c) is the single writer of the shared plan-skill files for P5; P7 sequences after P5 to avoid re-touching them.

**Non-goals**
- No removal of the `PHASE_LOOP_VERIFY_ENFORCE=warn` escape hatch; we make declining evidence explicit and audited, not impossible.

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/verification_evidence.py
- phase-loop-runtime/src/phase_loop_runtime/closeout_validation.py
- phase-loop-skills/plan-phase/scripts/validate_plan_doc.py
- phase-loop-skills/plan-detailed/SKILL.md
- phase-loop-skills/plan-phase/SKILL.md

**Depends on**
- P1

**Produces**
- (none)

---

### Phase 6 — Visual / Browser Verification for UI Work (P6)

**Objective**
Close the visual blind spot: when a change touches UI surfaces, the plan must include a browser/screenshot verification and a visually-observable acceptance criterion, and execution must capture and attach a screenshot before passing.

**Exit criteria**
- [ ] A visual-evidence validator (registered via IF-0-P1-1 at default `warn`, consuming IF-0-P1-4) raises a finding when `ui_change_detected` is true and no `visual_evidence` artifact is attached; at default severity the loop continues, and only at `block` does it refuse closeout. A test covers UI-diff → finding, screenshot-attached → clean, and default-warn → still `complete`. The agent self-satisfies by capturing the screenshot — no human eyeball required to pass.
- [ ] `plan-phase`/`plan-detailed`, when owned files match UI globs (`*.tsx`/`*.jsx`/`*.vue`/`*.svelte`, `components/**`, CSS), ask for a Verification step that drives a browser and an acceptance criterion phrased as an observable visual outcome.
- [ ] `execute-phase` instructs the agent to capture a screenshot (claude-in-chrome or Playwright-via-PMCP) for UI changes and record its path in the closeout, so a human can spot-check the artifact after a bounded run.
- [ ] `validate_plan_doc.py` warns when owned files match a UI glob but the Verification section names no browser/screenshot/Playwright step.

**Scope notes**
- Decompose into 3 lanes owning disjoint files: (a) runtime visual-evidence validator + tests (registers through the P1 hook); (b) plan-skill UI-trigger rule + `validate_plan_doc.py` UI-glob warning + tests; (c) `execute-phase` screenshot-capture mandate. Lanes own disjoint files; lane (b)'s skill edits target UI-rule sections distinct from P5's DoD sections.

**Non-goals**
- No visual-diff/perceptual-baseline tooling; a captured screenshot + observable assertion is the bar this phase sets.

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/verification_evidence.py
- phase-loop-skills/plan-phase/scripts/validate_plan_doc.py
- phase-loop-skills/plan-phase/_overrides/claude/SKILL.md
- phase-loop-skills/execute-phase/_overrides/claude/SKILL.md

**Depends on**
- P1

**Produces**
- (none)

---

### Phase 7 — Canonicalization & Cross-Cutting Cleanup (P7)

**Objective**
Finish the genuinely cross-cutting work that only makes sense once the enforcement phases land: a canonical definition-of-done sweep, the broken-reference fix, and documenting the new gates.

**Exit criteria**
- [ ] All skills use the single canonical definition-of-done term (IF-0-P1-5); a grep shows no remaining stray "Exit criteria"/"Acceptance Criteria" variants where the canonical term applies (command included in the plan).
- [ ] The broken reference at `task-contextualizer/SKILL.md:89` (`references/subagent-briefs.md`, which does not exist) is fixed — either the file is created or the pointer is corrected to `references/task-templates.md`.
- [ ] `README.md` / `CHANGELOG.md` document the new closeout gates (doc-delta, verification-evidence default, visual-evidence) and the planner model-tiering guidance.
- [ ] `phase-loop validate-roadmap` and the full standalone test suite pass on the merged result.

**Scope notes**
- Single lane (cross-cutting sweep + docs); owns the canonical-term edits across skills and the top-level docs. Justified single lane: the work is a coordinated terminology sweep that must be atomic to stay consistent. Sequenced after P2/P5/P6 so it documents gates that already exist and after P5 to avoid colliding on the shared plan-skill files.

**Non-goals**
- No new enforcement; P7 only canonicalizes vocabulary, fixes the dangling reference, and documents what P1–P6 built.

**Key files**
- phase-loop-skills/task-contextualizer/SKILL.md
- phase-loop-skills/task-contextualizer/references/task-templates.md
- README.md
- CHANGELOG.md

**Depends on**
- P2
- P5
- P6

**Produces**
- (none)

---

## Execution Notes

- **Planning**: `/claude-plan-phase <ALIAS>` for each phase. Independent roots `P1`, `P3`, `P4` can be planned concurrently; `P2`, `P5`, `P6` can be planned concurrently once `P1`'s interface gates are frozen.
- **Execution**: `/claude-execute-phase <alias>` after each plan is approved. `P2`, `P5`, `P6` execute in parallel after `P1` merges; `P3` and `P4` execute any time.
- **Critical path**: `P1 → {P2 | P5 | P6} → P7` — wall-clock minimum. `P3` and `P4` are off the critical path entirely.
- **Parallel branches**: `P3` (mode-aware handoff) and `P4` (model tiering) are independent roots with no dependency on the closeout contract; they can land first as quick wins.
- **Single-writer files across phases**:
  - `phase-loop-runtime/src/phase_loop_runtime/closeout.py` — **P1 only** (lane a). P2/P5/P6 register validators through the IF-0-P1-1 hook; none edit the status decision.
  - `phase-loop-runtime/src/phase_loop_runtime/models.py` — **P1 only** (lane a).
  - `phase-loop-skills/plan-phase/scripts/validate_plan_doc.py` — touched by P2, P5, P6; each adds a *separate* check function. Sequence the merges (P2 → P5 → P6) or assign one integrator lane to avoid edit collisions.
  - `phase-loop-skills/plan-detailed/SKILL.md` and `phase-loop-skills/plan-phase/SKILL.md` — **P5 owns** the DoD-linkage edits; **P7** sequences after P5 for the canonical-term sweep; P4 edits only model-ladder sections — keep the three edits in disjoint sections.

---

## Acceptance Criteria

- [ ] **Autonomy preserved by default:** a full unattended run with the new gates installed and no strictness opt-in reaches `complete` on every phase that the old runtime would have, raising the new gates only as `warn` findings in the run summary — zero new `human_required` halts, zero new blocks. (Regression: existing closeout/run tests pass unchanged.)
- [ ] **Strictness is there when asked:** with `PHASE_LOOP_REVIEW=block` (or the per-gate opt-in), a phase whose diff changes a public interface / declines verification evidence / changes UI without a visual artifact is refused `complete` with an audited, agent-recoverable finding (doc update, evidence log, screenshot, or justified opt-out) — and still never sets `human_required`.
- [ ] **Human cadence by bounding, not stalling:** a bounded run (`--max-phases N`) ends with a summary of `warn` findings a human can review between runs, without the loop ever blocking mid-run for human input.
- [ ] The autonomous loop emits no `/clear` instruction; the interactive path still does, labeled as such.
- [ ] A plan can assign a cheaper model/effort to a trivial lane and the runtime-resolved selection reflects it.
- [ ] `phase-loop validate-roadmap specs/phase-plans-v1.md` passes and the full standalone test suite is green.

---

## Verification

```bash
# Roadmap lints clean
PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v1.md

# Runtime gates and validators (after P1, P2, P5, P6)
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/ -k "closeout or verification or evidence or visual or doc_delta" -q

# Plan-doc validator checks (after P2, P5, P6)
python phase-loop-skills/plan-phase/scripts/validate_plan_doc.py <a-sample-plan>.md

# Full standalone suite (after P7)
cd phase-loop-runtime && PYTHONPATH=src python -m pytest -q
```
