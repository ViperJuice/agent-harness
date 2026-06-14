# Parallelization Heuristics

Extended rules for phase/lane decomposition. The goal is to minimize wall-clock time to deliver the full roadmap. The main SKILL.md carries the seven core rules; this doc expands them for edge cases.

## Core model

- **Phases are serial checkpoints.** Each phase closes an interface freeze that the next phase consumes. Wall-clock cost of a phase = wall-clock cost of its slowest lane.
- **Lanes within a phase are parallel.** Multiple teammates, disjoint files, same wave.
- **Total wall time ≈ sum over critical path of (slowest lane per phase).** Minimize this sum.

## Rule 1 — Fewer phases, more lanes

If two bodies of work can both start from today's codebase and don't depend on each other's output, they go in the same phase as sibling lanes. Do not split them into sequential phases just because they touch different modules.

**Counter-example (don't do this):**

```
Phase 1 — Auth module
Phase 2 — Billing module   ← billing doesn't depend on auth; why is it a separate phase?
```

**Correct:**

```
Phase 1
  Lane 1 — Auth module
  Lane 2 — Billing module
```

## Rule 2 — A phase boundary exists only at a necessary interface freeze

Ask: "Can any work in the next phase start before this phase's output is final?" If yes, the phase boundary is arbitrary — collapse it.

The only legitimate reason to introduce a phase boundary:

- Downstream lanes need an interface (signature, schema, enum, migration number) that must be fixed before they can write code against it.
- That interface is not yet fixed (either doesn't exist or is subject to change).

If the interface is already stable (in the existing codebase, in a vendored library, in a finalized spec), no phase boundary is needed — parallelize now.

## Rule 3 — Prefer narrow, early freezes

A freeze on "the authentication module" blocks a lot. A freeze on "`login(email, password) -> Session`" blocks almost nothing. Write the tightest possible contract in the `**Produces**` block.

Narrow freezes let you push more lanes into earlier phases. Phase 1 can freeze the minimum surface; Phase 2 starts on the wider surface while Phase 1's lanes fill in the implementation.

**Anti-pattern**: `**Produces**: full implementation of X`. That's not a freeze, that's just done work. A freeze is a contract about shape, not content.

## Rule 4 — Split fat phases into parallel sibling phases

When a phase has two or more subtrees that don't share an interface dependency on each other, split them into parallel-safe sibling phases. Use the aliased-branch pattern:

```
P6A — Dep hygiene           parallel after P1
P6B — Docs alignment        parallel after P4
```

Sibling phases with distinct aliases let `/claude-execute-phase` schedule them concurrently instead of serially.

**Test**: if `Phase N` lists `Depends on: P3` and `Phase N+1` also lists `Depends on: P3`, and neither lists the other, they are siblings. Mark them as `NA` / `NB` aliases, not `N` / `N+1`.

## Rule 5 — ≥2 lanes per phase (with exceptions)

A single-lane phase is a code smell. Investigate:

- Is the work actually a single atomic unit? → Likely belongs as one lane inside an adjacent phase, not its own phase.
- Is the work too coarse? → Split the file-ownership further until ≥2 disjoint lanes emerge.
- Is it a pure interface-freeze phase (Phase 0 / Phase 1 preamble)? → This is the exception. Mark `Scope notes` with "preamble / interface-only phase — single lane justified".

The validator enforces this rule but allows the preamble escape hatch via explicit `Scope notes` marking.

## Rule 6 — Parallelism hints in Scope notes

Every phase's `Scope notes` must make the parallelization plan machine-readable to `/claude-plan-phase`:

- Suggested lane count (e.g., "decompose into 3 lanes").
- Disjoint-file partitions per lane (e.g., "lane A owns `auth/**`, lane B owns `billing/**`, lane C owns `shared/types.ts`").
- Single-writer files (e.g., "`routes/index.ts` is single-writer; lane A owns it, others append via imports").
- Test-file placement (e.g., "tests go in each lane's directory; no shared test fixture file").

Without these hints, `/claude-plan-phase` has to re-derive the partition from scratch, which it may do incorrectly.

## Rule 7 — Cross-phase parallelism in the DAG

Two phases can execute concurrently when the DAG shows no path between them. Identify these explicitly:

- Annotate the DAG with `parallel after <X>` on branch phases.
- List cross-phase parallelism in `## Execution Notes` so `/claude-execute-phase` doesn't serialize by default.

Critical path = longest path from root phases to the terminal phase. If you can split a node on the critical path into a sibling pair (Rule 4), the critical path shrinks.

## Anti-patterns

- **Waterfall in disguise**: `Phase 1 — Design; Phase 2 — Implement; Phase 3 — Test.` This is one phase with three task types, not three phases. Tests are written before impl (TDD) within each real phase.
- **Phase-per-module**: "Phase 1 = auth, Phase 2 = billing, Phase 3 = notifications." Almost always wrong — these should be sibling lanes in one phase unless they genuinely depend on each other's interfaces.
- **Vague freezes**: `**Produces**: improved database layer`. Not a freeze. Specify the symbols, types, or migration numbers that will be stable.
- **Too-wide freezes**: `**Produces**: full public API of module X`. Downstream lanes will need to wait for every function in X to be final, not just the ones they call. Freeze only what's consumed.
- **Phantom phase dependencies**: listing `Depends on: P2` in `P3` when P3 only needs one symbol from P2 that P2 freezes on day one. Either narrow the freeze so P3 can start immediately, or promote the freeze to P1 where it belongs.

## Diagnostic questions

When sizing phases, ask:

1. What's the minimum interface `Phase N+1` needs from `Phase N`?
2. Can that interface be frozen on `Phase N`'s first day instead of its last?
3. If yes — move the freeze earlier and parallelize. If no — you've found the real phase boundary.
4. Inside `Phase N`, what are the natural file-ownership partitions?
5. Are any of those partitions independent enough to be lanes that won't block each other?
6. Is there any file multiple lanes want to write? If yes, assign a single owner and have others append via imports or extension points.

Answering these yields a roadmap where the critical path is the actual minimum serialization inherent in the work — not arbitrary process overhead.
