# Design brief — cross-repo phase-loop orchestration (#29)

Follow-up to #28 (single-repo publish default shipped in v0.1.10). This designs the multi-repo
case: a single logical change spanning N repositories with ordering dependencies between them —
the #28 triggering example was `consiliency-portal` (SQL migration + UI/server) + `message-board`
(schema-version/client expectation), where the portal change must land before the message-board
change consumes it.

## Goal & non-goals
- **Goal:** orchestrate one logical change across multiple repos — each repo getting its own
  branch/PR, in dependency order, with cross-links and partial-success handling — reusing the
  existing per-repo phase-loop machinery, not reimplementing it.
- **Non-goals:** a monorepo merge; synchronous atomic cross-repo commits; rewriting the
  single-repo runner; replacing the per-repo roadmap/plan/execute model.

## What the current architecture already provides (verified)
- **`run_loop(repo, roadmap, run_mode=…)`** (runner.py:1088) is a self-contained per-repo state
  machine — the natural composition atom. `phase-loop run --repo R --roadmap M [--governed]`.
- **Interface-freeze (IF) gates are first-class:** a phase declares `**Produces**: IF-0-X`;
  closeout validates `produced_if_gates` (`closeout_validation.extract_plan_produces`). Today
  they couple phases *within* a repo.
- **Multi-repo aggregation precedent exists (read-only):** `phase_loop_drift_audit` models
  `repos: tuple[…]`; the audit `--repo` is "repeatable for cross-repo aggregation." So "a set of
  repos" is already modeled — the orchestration (write) side is the gap.
- **`run_mode` is per-run** → governed composes per-repo for free.
- **#28 publish flow** (v0.1.10): worktree → branch → verify → scoped-audit → commit → push → PR,
  with the never-commit-to-main/protected invariant and draft/ready policy — reusable per repo.

## Recommended architecture — Option C: a runtime coordination layer over the unchanged per-repo runner

### 1. Cross-repo "release-train" roadmap (a DAG-of-DAGs)
A top-level schema where each **node = `(repo, phase-roadmap)`** and each **edge = an inter-repo
interface-freeze gate**: repo A's roadmap *produces* `IF-PORTAL-SCHEMA-v2`; repo B's roadmap
*consumes* it. This lifts the existing phase-DAG + IF-gate primitives one tier up — the same
produce/consume mechanism across repo boundaries. The roadmap-builder skill gains a "train" mode
to author it; `validate-roadmap` gains a train validator (acyclic across repos, every consumed
cross-repo gate is produced by some upstream node).

### 2. Deterministic coordinator (runtime, not a skill)
A `train_runner` module / `phase-loop run-train` entry that:
- topo-sorts the repo nodes by the cross-repo IF-gate DAG;
- runs each repo's existing `run_loop` **unchanged**, threading `run_mode`;
- gates a downstream repo's start on the upstream node's IF-gate being satisfied (policy:
  upstream PR *merged*, or upstream IF-gate *produced+verified* with the downstream PR opened as
  draft/blocked-on);
- reuses the #28 publish flow → **one PR per repo**, cross-linked (downstream draft until
  upstream merges; PR bodies carry the dependency + merge order);
- handles **partial success**: upstream fails/blocks → downstream never starts; the train
  reports a structured state (which repos merged / open / blocked) and is resumable.
- each repo keeps its own worktree tree under `/mnt/workspace/worktrees/<project>-<branch>`.

### 3. Governed integration (two tiers)
- **(a) Per-repo governed** — each node's `run_loop` runs `--governed`; the existing pre-merge
  panel gate composes immediately (the MVP).
- **(b) Cross-repo governed gate (extension)** — an optional train-level pre-merge review where
  the panel reviews the *linked set of PRs as one logical change* before any merges, so a
  reviewer sees the portal+message-board change together, not piecemeal.

### 4. Skill ↔ runtime split
The **coordinator lives in the runtime** (it's a gated state machine with retries/partial-success
— and the #28 lesson is that a control living only in a skill is bypassed or degrades silently).
A thin **`run-train` skill** (or a roadmap-builder "train" mode) is the human entry point, the same
way `plan-phase`/`execute-phase` front `run_loop`.

### Where cross-repo state lives
A per-repo `.phase-loop/` cannot own train state. Proposal: a **coordinator-side train ledger**
(its own state dir, e.g. on the operator's machine or a designated coordination repo) recording
node status, produced cross-repo gates, PR URLs, and merge order — resumable, and the single
source of truth for partial-success/resume.

## Alternatives rejected
- **Deep runner integration:** the runner's closeout, start-gate, dirty-path classification, and
  `EXECUTE_MERGE_TARGET` are saturated with single-repo assumptions; threading multi-repo through
  that dense code is the highest-risk path. Avoid.
- **Pure orchestration skill:** sits outside the runtime's guarantees; #28 proved skill-only
  controls silently fail. Coordination logic must be deterministic runtime code.
- **Greenfield multi-repo engine:** discards the per-repo runner, IF-gates, governed, worktrees,
  and the #28 publish flow — the opposite of leveraging existing tooling.

## Open questions / decision points (for the panel to stress-test)
1. **IF-gate as a cross-repo edge** — is the produce/consume contract robust across repo
   boundaries given *async* merge timing (repo A's gate "produced" on a branch vs merged to its
   main)? Does a consumed cross-repo gate need a **version/identity** (content hash of the
   contract) so repo B verifies it consumed the *actual* shipped contract, not a stale one?
2. **Partial-success / rollback semantics** — repo A merges, repo B then fails governed review.
   A is already on main. Options: leave A (forward-only; B retried later), or require A to be
   *revertable* / B to land as a follow-up. What's the right default, and does the coordinator
   need a revert/compensation story or is forward-only + resume sufficient?
3. **Gate granularity for downstream start** — does downstream start when upstream's PR is
   *merged*, or when the upstream IF-gate is *produced+verified* (PR still open)? The former is
   safe-but-serial; the latter is faster-but-couples to unmerged work.
4. **Governed: per-repo vs a true cross-repo gate** — is composing per-repo governed enough, or is
   the train-level review (b) load-bearing for safety (a reviewer must see the whole change)?
5. **Train state location & ownership** — coordinator-side ledger vs a designated coordination
   repo vs per-repo cross-references. What survives a coordinator crash / enables resume?
6. **Roadmap authoring ergonomics** — is a DAG-of-DAGs train roadmap authorable by hand / by the
   roadmap-builder, or does the cross-repo IF-gate wiring become unmanageable past ~3 repos?
7. **Is Option C right at all** — or is there a simpler shape (e.g. a convention-only "open N
   linked PRs in order" with no new runtime engine) that captures 80% of the value for the common
   2-3 repo case, deferring the full DAG engine?
