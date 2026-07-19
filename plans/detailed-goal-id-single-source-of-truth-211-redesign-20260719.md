# Scoping & plan: goal-ID single source of truth (redefines Consiliency/agent-harness#211)

## Problem reframe

#211 was scoped as "audit that the plan's acceptance criteria still cover the
roadmap's exit-criteria" — a text-diff between two lists. Three cross-vendor CR
rounds proved that audit undecidable: catching semantic *weakening* by comparing
words has, at every tuning, either a false-positive (blocks a valid refinement) or
a fail-open (misses a real weakening). The failure is structural, not a bug: there
are **two sources of truth** (the roadmap goal and the plan's re-statement of it),
and the gap between the copies is where weakening hides.

**This redesign removes the duplication instead of policing it.** The roadmap goal
becomes the single source of truth with a stable ID; the plan's acceptance item
*references* the goal ID + names its proving evidence, rather than re-stating the
goal in (potentially weaker) words. Drift-by-restatement then cannot occur — the
plan never rewrites the goal.

## What this does and does NOT guarantee (honest scoping — read first)

- **Guarantees COMPLETENESS (decidable):** every roadmap goal ID is referenced by
  ≥1 plan acceptance item. A goal that is silently *dropped/forgotten* is caught
  with certainty — the whole "dropped criterion" class is eliminated.
- **Does NOT guarantee ADEQUACY:** it does not verify that the referenced evidence
  actually discharges the goal. `EC-P1-1` ("publish **non-silent** audio")
  referenced by `proven by test_audio_track_exists` passes the completeness check
  even though the test only checks a track exists. What the reference *buys* for
  the weakening class is that the goal is now pinned **next to its claimed
  evidence**, so weak evidence is **human-reviewable at the point of reference** (a
  CR reviewer / the #91 evidence-authenticity gate), instead of hidden in a
  reworded paraphrase. Weak-evidence detection is explicitly **out of scope** and
  stays with CR + #91.
- It also does **not** verify that N plan items *sum to* a coarse goal — only that
  the goal is referenced at all.

State this plainly in every downstream artifact; overselling "drift is impossible"
is the exact framing that treadmilled the fuzzy audit.

## Empirical grounding (sampled real roadmaps)

Sampling exit-criteria across `specs/phase-plans-v{1,2}.md` + the sourcebroker
roadmap: **real exit-criteria are already concrete, testable assertions, and many
already cite their proving command** (roadmap-builder enforces "testable assertion,
checkable by shell command, not vibes"). So the plan's acceptance criteria today
are largely near-duplicate restatements — validating both that the duplication is
the drift surface and that a **reference-only** model fits the common case. A
minority are coarse or bundle multiple checks (sourcebroker "…are tested"; v2's
"Routing-invariant tests pass: [6 invariants]"), so the design supports **1:many**
(one goal ID referenced by several plan items) but treats refinement as the
exception, not the norm.

## Design

### The `EC-<ALIAS>-<N>` scheme (mirrors the proven `IF-0-<ALIAS>-<N>` gates)

Each roadmap phase exit-criterion gets a stable ID `EC-<ALIAS>-<N>` (alias = the
phase alias, N = 1-based), mirroring the IF-gate scheme and its reconciliation
invariant (`roadmap_lint.check_if_gates`): alias-scoped, unique, contiguous.

```
### Phase 1 — Closeout gates (P1)
**Exit criteria**
- [ ] EC-P1-1 — `register_closeout_validator(fn, severity)` exists and … (`pytest -k closeout`)
- [ ] EC-P1-2 — no validator path can set `human_required=true` (test asserts …)
```

Recon-confirmed this is non-breaking: `roadmap_lint._checkbox_items` returns the
whole post-`- [ ] ` remainder, so `EC-P1-1 — <assertion>` parses today; only a new
validator enforces the ID. No frozen roadmap-format contract exists.

### The plan references, never restates

```
## Acceptance Criteria
- [ ] EC-P1-1 — proven by `pytest tests/test_closeout.py -k register_validator`
- [ ] EC-P1-2 — proven by `tests/test_closeout.py::test_no_human_required`
- [ ] (plan-internal) no new lint errors — `ruff check`
```

A plan acceptance item either **references** one or more `EC-<ALIAS>-<N>` IDs (the
goal is canonical; the item adds only the proving command) or is a **plan-internal**
item (no EC-ref — plan-local done conditions). This reuses the existing "each
acceptance criterion names the command that proves it" contract; the only change is
the item cites a **goal ID** instead of a reworded assertion.

### The decidable completeness check (replaces the fuzzy audit)

Mirrors the IF-gate `Produces` closeout precedent
(`closeout_validation.extract_plan_produces` vs closeout-reported `produced_if_gates`
→ `contract_bug` block): scrape the plan's declared `EC-<ALIAS>-<N>` references, load
the roadmap phase's declared EC-IDs, and block (`contract_bug`) if any roadmap EC-ID
for the phase is **not referenced** by ≥1 plan acceptance item. 1:many is fine (a
goal referenced by many items; an item referencing many goals). This is a set
membership check — no word-matching.

### Home: plan-time + preflight, NOT the frozen closeout schema

The `EmitPhaseCloseout` BAML contract is frozen (golden-hash + 5-harness parity), so
the check does **not** enter the closeout payload. It runs where roadmap + plan are
already in hand: a new `validate_plan_doc.py` check (plan-time) and the existing
phase-loop preflight (`runner.py:3010`, which already receives `roadmap`). The
preflight stays **warn-default, opt-in block** via `PHASE_LOOP_ACCEPTANCE_ENFORCE`
(autonomy-first, unchanged).

### Opt-in per roadmap (migration-safe)

A roadmap that declares `EC-<ALIAS>-<N>` IDs opts into enforcement; a legacy roadmap
with no EC-IDs keeps today's behavior untouched (no new gate). This is fully
additive — existing roadmaps and plans (including downstream repos) do not break.

## Increments (this is bigger than #211 — scoped as a decision gate)

This spans a grammar, a parser, a validator, 3 planner skills × 4 harnesses (behind
the CANON parity regen gate), a preflight, and migration. It will **dwarf** the
remaining backlog (#177/#202 are hours; this is the largest single initiative in the
cleanup). So it is cut into increments with a **go/no-go after Increment 1**.

### Increment 1 — the mechanism (bounded; NO skill-fleet edits) — the decision gate

Ships the full decidable capability on hand-authored fixtures, touching only runtime
code (not the parity-gated skill bundle):

- `roadmap_lint.py` — parse `EC-<ALIAS>-<N>` from `**Exit criteria**` items; add a
  reconciliation check (alias-scoped, unique, contiguous) modeled on
  `check_if_gates`; expose `Phase.exit_criteria` with parsed `(id, text)` (additive;
  bare-prose criteria still parse, `id=None`).
- A new `goal_coverage.py` module — `extract_plan_goal_refs(plan)` (scrape
  `EC-<ALIAS>-<N>` refs from acceptance items) + `check_goal_coverage(repo, plan,
  roadmap)` → the decidable completeness result (every declared EC-ID referenced).
  Opt-in: if the roadmap phase declares no EC-IDs → `not_applicable` (no gate).
- Wire it into the existing preflight (**replacing** the fuzzy
  `run_acceptance_coverage_audit` call at `runner.py:3010`) and add a plan-time CLI
  path (reuse/repurpose the `acceptance-coverage-audit` subcommand as a decidable
  check, or a new `goal-coverage-audit`).
- **Retire** `acceptance_coverage_audit.py` (the fuzzy tool) and abandon the
  unmerged `feat/acceptance-coverage-audit-211` branch — this redesign supersedes it.
- Tests: all on fixtures — EC-ID parse + reconciliation; every-ID-referenced → clean;
  a dropped EC-ID → `contract_bug`; 1:many (one ID, two items; one item, two IDs) →
  clean; legacy roadmap (no IDs) → not_applicable; preflight warn-default vs
  `PHASE_LOOP_ACCEPTANCE_ENFORCE=block`.

**Decision gate:** after I1, the user greenlights (or not) the fleet-wide skill
change before any parity-gated edits. I1 delivers real value alone (dropped-goal
detection, decidably) without the highest-risk surface.

### Increment 2+ — skills emit/reference by default, then migrate (only on GO)

- roadmap-builder skill (×4 harness `skills-src/`) — emit `EC-<ALIAS>-<N>` on each
  exit-criterion; document the ID convention. Regen past CANON parity.
- plan-phase + plan-detailed skills (×4) — author acceptance items as **EC-ID
  references + proving command**, not restatements; update the templates + rigor
  rules; `validate_plan_doc.py` gains the reference check.
- Migration — opt-in per roadmap; a helper to add EC-IDs to an existing roadmap;
  migrate in-repo `specs/phase-plans-v*.md` deliberately (not a blind sweep). Legacy
  roadmaps keep working until migrated.

## Open product decisions (for the plan review / user)

1. **Coarse goals:** some exit-criteria genuinely bundle several checks. Allowed
   (referenced once, adequacy human-reviewed)? Or must roadmap-builder decompose
   them into per-check EC-IDs at authoring? (Recommendation: allow, with the coverage
   check completeness-only; adequacy is CR/#91.)
2. **Enforcement default:** warn-default + opt-in block (consistent with the rest of
   the phase-loop) — confirm.
3. **CLI naming:** repurpose `acceptance-coverage-audit` vs add `goal-coverage-audit`.

## Documentation impact

- Increment 1: `CHANGELOG.md` (the decidable goal-coverage check replaces the fuzzy
  audit); a short `EC-<ALIAS>-<N>` grammar note (where the IF-gate scheme is
  documented). No `_contract_docs` freeze touched.
- Increment 2: the roadmap-template + planner-skill docs; `roadmap-template.md`.

## Verification (Increment 1)

```bash
cd phase-loop-runtime
PYTHONPATH=src:tests python3 -m pytest tests/test_goal_coverage.py tests/test_phase_loop_roadmap_validate.py -q
# fixtures: dropped EC-ID -> contract_bug; all referenced -> clean; 1:many -> clean;
# legacy roadmap (no EC-IDs) -> not_applicable; preflight warn vs block.
```

## Acceptance criteria (Increment 1)

- [ ] `roadmap_lint` parses `EC-<ALIAS>-<N>` from exit-criteria and reconciles them
  (alias-scoped, unique, contiguous), non-breaking on legacy bare-prose criteria.
- [ ] `check_goal_coverage` blocks (`contract_bug`) when a declared roadmap EC-ID is
  unreferenced by any plan acceptance item, passes when all are referenced (incl.
  1:many), and returns `not_applicable` for a roadmap with no EC-IDs.
- [ ] The check runs at plan-time (CLI) + phase-loop preflight (warn-default; opt-in
  block via `PHASE_LOOP_ACCEPTANCE_ENFORCE`; never `human_required`), replacing the
  fuzzy `run_acceptance_coverage_audit` call.
- [ ] The fuzzy `acceptance_coverage_audit.py` is removed and the redesign explicitly
  supersedes the unmerged `feat/acceptance-coverage-audit-211` branch.
- [ ] No frozen contract changed (roadmap format additive; `EmitPhaseCloseout` BAML
  untouched); full non-dotfiles suite green.

## Scale statement & go/no-go

Increment 1 is a bounded runtime change (~1 parser extension + 1 new module + 1
preflight rewire + fixtures) delivering **decidable dropped-goal detection**.
Increment 2+ is a **fleet-wide skill change** (3 skills × 4 harnesses + CANON regen +
in-repo roadmap migration) that is the single largest item in the cleanup backlog —
larger than #177 + #202 + the org-rename sweep combined. **Recommendation:** land
Increment 1, then explicitly decide whether to commit to Increment 2+ now or after
the smaller concrete items (#177, #202) ship. This plan is the scoping deliverable;
implementation waits on approval.
