# Roadmap Template (`specs/phase-plans-v<N>.md`)

Emit this exact structure. Heading names are stable IDs that `/claude-plan-phase` parses — do not rename, reorder, or drop required sections.

```markdown
# <Project Name> — Phase Plan v<N>

> How to use this document: save to `specs/phase-plans-v<N>.md`, then run `/claude-plan-phase <ALIAS>` to produce the lane-level plan for each phase (→ `plans/phase-plan-v<N>-<alias>.md`), then `/claude-execute-phase <alias>` to build it.

---

## Context

<One-page synthesis: the problem, the current state, the refactor/build thesis. Lead with the *why*. If existing code already contains raw material for the refactor, name it — future lane teammates will reuse rather than rewrite.>

---

## Architecture North Star

<ASCII diagram of the target architecture. Optional but load-bearing for structural work. Omit for pure-feature roadmaps where architecture isn't changing.>

```
  ┌─────────────┐
  │   Layer A   │
  └──────┬──────┘
         │
  ┌──────▼──────┐
  │   Layer B   │
  └─────────────┘
```

---

## Assumptions (fail-loud if wrong)

1. <Precondition that, if false, invalidates the plan>
2. ...

---

## Non-Goals

- <Explicitly deferred>
- ...

---

## Cross-Cutting Principles

1. <Rule that applies across every phase>
2. ...

---

## Phase Dependency DAG

```
  P1  <short phase name>
   │
   ▼
  P2A  <name>
   │
   ▼
  P2B  <name>
   │
   ▼
  P3   <name>
   │
   ▼
  P4   <name>

  P6A  <parallel branch>   parallel after P1
  P6B  <parallel branch>   parallel after P4
```

---

## Top Interface-Freeze Gates

These gates are the narrowest contracts that unblock downstream phases. `/claude-plan-phase` concretizes each (exact signature/schema) when it plans the owning phase.

1. **IF-0-P1-1** — <symbol / type / endpoint / migration with enough detail to be unambiguous>
2. **IF-0-P2A-1** — ...

---

## Phases

### Phase 1 — <Name> (P1)

**Objective**
<One or two sentences stating what this phase achieves.>

**Exit criteria**
- [ ] <Testable assertion. Checkable by shell command or integration test, not vibes.>
- [ ] <Testable assertion.>

**Scope notes**
- <Lane decomposition hint: e.g., "decompose into 3 lanes: (a) identity module, (b) registry updates, (c) walker changes — each owns disjoint files".>
- <Parallelism advice: e.g., "lane (b) depends on lane (a)'s `compute_repo_id` signature; lane (a) publishes it as IF-0-P1-1 before the others start".>
- <Single-writer files: any shared file where only one lane may write. List the owner lane here so downstream claude-plan-phase runs respect it.>

**Non-goals**
- <Explicitly deferred within this phase>

**Key files**
- <path/to/file.py>
- ...

**Depends on**
- (none)

**Produces**
- IF-0-P1-1
- IF-0-P1-2

---

### Phase 2A — <Name> (P2A)

**Objective**
...

**Exit criteria**
- [ ] ...

**Scope notes**
- ...

**Non-goals**
- ...

**Key files**
- ...

**Depends on**
- P1

**Produces**
- IF-0-P2A-1

---

<... additional phases following the same template ...>

---

## Execution Notes

- **Planning**: `/claude-plan-phase <ALIAS>` for each phase. Phases with no shared DAG ancestor can be planned concurrently (e.g., `<alias-x>` and `<alias-y>`).
- **Execution**: `/claude-execute-phase <alias>` after each plan is approved. Same cross-phase parallelism applies.
- **Critical path**: <longest DAG path, e.g., `P1 → P2A → P2B → P3 → P4 → P5`> — wall-clock minimum without speedups.
- **Parallel branches**: <e.g., `P6A` can start after P1 merge; `P6B` after P4 merge; both finish before final verification>.
- **Single-writer files across phases**: <list any file that multiple phases might want to touch; name the owning phase for each to prevent cross-phase serialization>.

---

## Acceptance Criteria

- [ ] <End-to-end assertion covering the whole roadmap's outcome>
- [ ] ...

---

## Verification

<Concrete shell/test commands that prove the roadmap delivered its goals. Runnable end-to-end by the user after the last phase merges.>

```bash
# Integration test
<command>

# Performance check
<command>
```
```

## Notes on filling in the template

- The `> How to use` callout at the top is boilerplate — keep it so collaborators know where the file lives and what consumes it.
- **Delete `## Architecture North Star` entirely if the work isn't structural.** An empty or placeholder diagram is worse than no diagram.
- `## Assumptions` should be small (3–7 items). If you have 15, some are really constraints — move them to `## Cross-Cutting Principles`.
- `## Non-Goals` is the scope-creep filter. Be specific: "no new transport layer" > "keep it simple".
- In the DAG, one node per phase. Use `│ ▼` for serial edges and the `parallel after <X>` label for concurrent branches.
- Every `IF-0-<ALIAS>-<N>` gate listed in `## Top Interface-Freeze Gates` must also appear in its owning phase's `**Produces**` block, and vice versa. Keep them in sync.
- Phase aliases are case-insensitive when consumed (`P1` = `p1`), but write them uppercase in the spec and lowercase in filenames (`phase-plan-v1-p1.md`).
