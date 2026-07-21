# agent-harness post-0.7.10 backlog — Phase Plan v9

> How to use this document: save to `specs/phase-plans-v9.md`, then run `/claude-plan-phase <ALIAS>` to produce the lane-level plan for each phase (→ `plans/phase-plan-v9-<alias>.md`), then `/claude-execute-phase <alias>` to build it.

---

## Context

Release 0.7.10 shipped the goal-ID single-source-of-truth work (#211 Inc-1/Inc-2), the
broker owned-scope reconciliation (#202), the codex staged-copy review leg (#177), and a
batch of CR/CLI hardening. This roadmap covers the **remaining open backlog** — the
follow-up issues those changes spawned plus two deferred features. The work is
*heterogeneous and mostly independent*: several bounded, single-subsystem hardening fixes
(each sonnet-dispatchable), two coupled correctness fixes with real internal structure, and
two lower-priority features. There is no single interface-freeze spine; the roadmap's value
is to (a) group the coupled work so it lands coherently, (b) express the priority ordering
(hardening before features), and (c) make the backlog executable through the phase-loop
pipeline instead of hand-driven. Every phase's work items are already tracked as GitHub
issues in `Consiliency/agent-harness`; phases reference issues rather than restating them.

---

## Assumptions (fail-loud if wrong)

1. Release 0.7.10 is published and on `main`; this roadmap starts from that baseline.
2. The bounded hardening items (#238, #243, #231) have no shared files or freeze
   dependencies — they can be built as fully parallel lanes.
3. The broker #250 hardening genuinely requires changing the broker AND #201's
   `_prebuilt_owned_paths` coordinator together (the `-z`/rename fixes must agree on path
   format across both), so they are a single coordinated phase, not two.
4. The live uncommitted advisor-board work is still wanted. It is spread across **FOUR**
   worktrees carrying uncommitted state — `agent-harness-abdreg`, `ah-abdreg-pkg`, and
   `ah-abdreg-rebase` (all on `feat/advisor-board-abdreg`, with *divergent* uncommitted
   mods to the same files — config.py/matrix.py/tests/MANIFEST.in), plus
   `agent-harness-abdresolve` (`phase/abdresolve`, incl. a deleted config.py). All four are
   reconciled (finish / commit-park / discard) before or as the first lane of the
   advisor-board feature phase; "reconcile abdreg/abdresolve" means all four, not two.
5. The public-repo cross-vendor-CR + green-CI merge gate is available for every phase.

---

## Non-Goals

- **#246 goal-coverage enforce-mode** (`PHASE_LOOP_ACCEPTANCE_ENFORCE=block` fail-closed
  completeness) — deferred: no production roadmap declares `EC-<ALIAS>-<N>` IDs yet, so the
  enforce path has no production inputs. Revisit when a real roadmap adopts goal IDs.
- **#248 fleet-wide IF-0-SANDBOX-1 external isolation** (spawn-cwd, live paths in prompts,
  full bwrap/container-level isolation) — deferred: a large cross-vendor redesign defending a
  bar *no* vendor's review leg currently meets. **Caveat (surfaced by the v9 roadmap CR):** the
  symlink-in-copy sub-case is NOT purely hypothetical — `launcher._stage_review_tree` copies
  the review tree with `copytree(..., symlinks=True)` + `copy2(..., follow_symlinks=False)`, so
  an absolute symlink in the repo is preserved into the staged tree and a write through it can
  escape to the live/external target. That concrete fail-open is tracked as its own issue
  (#259 — a minimal fail-closed symlink-containment slice), separate from the full #248
  isolation redesign; it is intentionally NOT folded into this hardening roadmap to keep v9
  converging.
- Bulk-migrating the existing `specs/phase-plans-v1..v8` roadmaps to goal IDs — opt-in per
  roadmap by design; not a backlog item.
- **This roadmap (v9) also opts OUT of `EC-<ALIAS>-<N>` goal IDs** — a conscious decision, not
  an omission. Adopting them here would hand parked #246 its first production input while its
  enforce-mode is still undecided (see the #246 bullet); the phases below are hardening/parity
  lanes whose Exit criteria are already testable without goal-ID coverage. Revisit only if a
  future append introduces work whose completeness genuinely needs goal-ID tracking.
- No new human-required gates: all new gates stay warn-default / opt-in-to-block.

---

## Cross-Cutting Principles

1. **Cross-vendor CR + green CI before every admin-merge.** This is a public repo: each
   phase's lanes merge only after a completed cross-vendor code review (codex + gemini +
   claude/Fable; backfill the third seat with grok when gemini is subscription-contended)
   and green CI. **The CR mechanism is READ-ONLY inlined-diff review** — each seat reviews a
   self-contained artifact (full diff + source inlined), NOT a write-capable staged working
   tree. The `launcher._stage_review_tree` write-capable review path (the agy governed-review
   leg) is therefore NOT on this roadmap's merge-gating path, so the #259 symlink-escape is
   off-path for v9's CRs as run. Guard rail: if any lane in this roadmap DOES invoke the
   write-capable staged review leg, **#259 (symlink containment) is a hard prerequisite** and
   must land first — do not rely on `_stage_review_tree` isolation until #259 is fixed.
2. **Bounded lanes are sonnet-dispatchable** in isolated git worktrees (implement + test +
   push a branch; the orchestrator runs the CR + merge).
3. **Hardening stays fail-closed.** No fix may convert a fail-closed gate into a fail-open
   one; a purely-local read failure must not poison a shared epoch.
4. **Autonomy-first:** new checks are warn-default and opt-in-to-block; never add a
   `human_required` gate.
5. **Match-the-runtime for any validator that must agree with a runtime check** — reuse the
   authoritative runtime functions, never re-implement a parser.

---

## Phase Dependency DAG

```
  P1   Bounded hardening        (root)
  PAR  Goal-coverage gate parity (root — parallel to P1)
  BRK  Broker #250 hardening    (root — parallel to P1/PAR)
   │
   ▼   (after all hardening lands — priority ordering, not a freeze)
  FAB  Advisor-board delta review   parallel after P1+PAR+BRK
  FAV  Avatar closeout evidence     parallel after P1+PAR+BRK

  P1, PAR, BRK have no shared ancestor -> plan + execute all three concurrently.
  FAB and FAV have no shared ancestor with each other -> concurrent once hardening lands.
```

---

## Top Interface-Freeze Gates

These gates are the narrowest contracts that unblock downstream lanes/phases.
`/claude-plan-phase` concretizes each (exact signature/format) when it plans the owning phase.

1. **IF-0-BRK-1** — the NUL-delimited (`-z --no-renames`) changed-path format shared by the
   broker `GitHubBrokerAdapter._branch_diff_paths` and the coordinator
   `train_runner._prebuilt_owned_paths`. The freeze is filename **byte-identity**, not merely
   symmetric parsing: both sides split on the NUL byte (`\0`), discard ONLY the terminal
   empty element (trailing NUL), and do NOT `.strip()`/trim any path element — a filename may
   legitimately carry leading/trailing whitespace or an embedded newline, and identically
   trimming it on both sides would still let the scope comparison approve the wrong path.
   `--no-renames` surfaces both endpoints of a rename. This guarantees the broker's coverage
   check and the coordinator's owned-paths derivation cannot desync on any path.
2. **IF-0-PAR-1** — the shared **preflight** helper both the direct path and the
   lane-scheduler / work-unit dispatch path invoke, so the verification-evidence +
   acceptance/goal-coverage PREFLIGHT gates run identically on every dispatch path (#244).
3. **IF-0-PAR-2** — the shared **closeout** helper both the direct path and the
   delegated-child completion path invoke, so the produced-gates + goal-coverage CLOSEOUT
   gates run at delegated completion exactly as on the direct path (#245). This is a separate
   gate family from IF-0-PAR-1: preflight gates `{verification, goal-coverage}` and closeout
   gates `{produced-gates, goal-coverage}` sit at different lifecycle points, so they need two
   helpers, not one.

> IF-0-BRK-1, IF-0-PAR-1, and IF-0-PAR-2 are the only *real* freezes (intra-phase, published
> day-1 so a phase's lanes build against the same contract). The `IF-0-P1-1`, `IF-0-FAB-1`,
> and `IF-0-FAV-1` tokens in those phases' `Produces` blocks are **synthetic phase-completion
> tokens** — they satisfy the roadmap's Produces↔gates reconciliation but freeze nothing and
> have no downstream consumers (those phases' work is independent, no cross-phase freeze).

---

## Phases

### Phase 1 — Bounded hardening (P1)

**Objective**
Land the independent, single-subsystem hardening follow-ups (#238, #243, #231) as parallel
lanes, each fail-closed and CR-gated. #241 is a deferred lowest-priority lane.

**Exit criteria**
- [ ] #238: the breakglass gates fail closed on an empty event repo/roadmap independent of
  CWD — a regression test drives an empty/missing event store and asserts the gate blocks.
- [ ] #243: verification-evidence hardening beyond #209 — whole-artifact integrity check +
  closeout-diagnostic redaction, with a test proving a tampered/oversized artifact is
  rejected and diagnostics are redacted to metadata-only.
- [ ] #231: `max_effort_planner_eligible("grok")` returns False (narrow-reject, matching
  gemini/pi), via a capability signal DECOUPLED from run-level effort translation (e.g.
  `planner_max_class`) so grok still honors an explicit `max` at its real ceiling (`high`)
  for effort purposes while not being a max-CLASS planner-of-record. A test asserts
  `max_effort_planner_eligible("grok") is False` and that grok remains usable as a
  panel/reviewer leg and as a non-max planner (codex stays eligible). *Representational, not
  runtime selection-gating:* `max_effort_planner_eligible` is consulted only in the effort
  max→high fallback (`profiles.py:344`), never in `resolve_dispatch_decision`
  (`capability_registry.py:762`), and grok is never the AUTOSEL default planner — so this
  lane makes the eligibility signal honest, it does NOT add a dispatch-selection gate (moot).
  The panel `_GROK_EFFORT` lookup is hardened to a `.get`-with-clamp (parity with
  `_grok_cli_effort`); a plain-English rationale note for reviewers/planners is added at the
  registry.
- [ ] Full non-dotfiles suite green; each lane merged via cross-vendor CR + green CI.

**Scope notes**
- Decompose into 3 concurrent lanes owning disjoint files: (a) `#238` — the two SL-2
  breakglass gates (`_lane_ir_override`, `_closeout_allow_unowned_attested`) in TOP-LEVEL
  `reconcile.py` (NOT `convergence/reconcile.py`); (b) `#243` — `verification_evidence.py`;
  (c) `#231` — `profiles.py` registry + `panel_invoker.py`/`harness_mapping.py` + the
  `launcher.py` clamp site. No shared files → no single-writer serialization; no
  intra-phase freeze.
- Deferred lane (d) `#241` (login-shell shim exotic bash forms) is lowest priority /
  adversary-equivalent to an already-accepted escape hatch — plan it last or skip this round.
- Each lane is sonnet-dispatchable in an isolated worktree.

**Non-goals**
- Any change that converts a fail-closed gate to fail-open.
- #231 does NOT reduce any vendor's own maximum effort — grok still runs at its real ceiling
  (`high`) everywhere, including when an explicit `max` is requested. It only marks grok as
  not a max-CLASS *planner-of-record* so the eligibility signal stops advertising a `max`
  planning class grok cannot actually deliver. It does NOT add a dispatch-selection gate.

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/reconcile.py (the two SL-2 breakglass gates — #238; NOT convergence/reconcile.py)
- phase-loop-runtime/src/phase_loop_runtime/verification_evidence.py
- phase-loop-runtime/src/phase_loop_runtime/profiles.py
- phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py
- phase-loop-runtime/src/phase_loop_runtime/advisor_board/harness_mapping.py
- phase-loop-runtime/src/phase_loop_runtime/launcher.py (#231 run-level effort clamp site)

**Depends on**
- (none)

**Produces**
- IF-0-P1-1

**Spec closeout policy**
- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `phase-loop-runtime/src/phase_loop_runtime/**`
- evidence paths: `phase-loop-runtime/tests/**`
- redaction posture: `metadata_only`

---

### Phase 2 — Goal-coverage gate parity (PAR)

**Objective**
Close the two gate-parity gaps the goal-ID work surfaced: preflight gates bypass the
lane-scheduler + work-unit path (#244), and closeout gates are not re-checked at delegated
-child completion (#245). Make both gate families run identically on every path.

**Exit criteria**
- [ ] #244: the verification-evidence + acceptance-coverage preflight gates run on the
  lane-scheduler and work-unit paths (not only the direct path); a test drives a
  lane-scheduler/work-unit run and asserts the preflight gate fires.
- [ ] #245: the produced-gates + goal-coverage closeout gates are re-checked when a
  delegated child completes; a test drives a delegated-child completion and asserts both
  gates evaluate (a missing gate → the same block as on the direct path).
- [ ] Preflight parity is a shared PREFLIGHT helper both the direct and lane-scheduler/
  work-unit paths invoke (IF-0-PAR-1); closeout parity is a *separate* shared CLOSEOUT helper
  both the direct and delegated-child paths invoke (IF-0-PAR-2). Two helpers, not one — the
  preflight gate family `{verification, goal-coverage}` and the closeout gate family
  `{produced-gates, goal-coverage}` sit at different lifecycle points. No path-specific
  duplication that could drift within either family.
- [ ] Full non-dotfiles suite green; merged via cross-vendor CR + green CI.

**Scope notes**
- Decompose into 2 lanes: (a) `#244` preflight-parity — factor the direct-path preflight
  gates into the IF-0-PAR-1 helper and invoke it from the lane-scheduler/work-unit dispatch
  path (`_launch_ready_lane_wave`, runner.py ~1864); (b) `#245` closeout-parity — factor the
  direct-path closeout gates into the IF-0-PAR-2 helper and invoke it from the delegated-child
  completion path (`launch_delegated_child`, runner.py ~3766). Publish both helper signatures
  (IF-0-PAR-1, IF-0-PAR-2) on day 1 so each lane builds against the frozen entry point.
- **Serialize the two `runner.py` integrations** — both lanes edit `runner.py`, so this is a
  single-writer file across the two lanes, NOT two fully-parallel single-writer lanes.
  Coordinate the edits (e.g. land the helper-extraction commit first, then wire each callsite)
  rather than racing two branches on the same module.

**Non-goals**
- #246 enforce-mode (parked — see Non-Goals).

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/runner.py
- phase-loop-runtime/src/phase_loop_runtime/goal_coverage.py
- phase-loop-runtime/src/phase_loop_runtime/ (lane-scheduler / work-unit dispatch + delegated-child closeout)

**Depends on**
- (none)

**Produces**
- IF-0-PAR-1
- IF-0-PAR-2

**Spec closeout policy**
- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `phase-loop-runtime/src/phase_loop_runtime/**`
- evidence paths: `phase-loop-runtime/tests/**`
- redaction posture: `metadata_only`

---

### Phase 3 — Broker #202 hardening (BRK)

**Objective**
Land the #250 broker-hardening cluster: `-z` path-quoting, `--no-renames` rename-escape
close, base revision-syntax guard, empty-owned+empty-diff push guard, `head_sha`-pinned
push, and `--base` on `gh pr create` — coordinated across the broker and the #201
coordinator so the two never desync on path format.

**Exit criteria**
- [ ] Broker `_branch_diff_paths` and coordinator `_prebuilt_owned_paths` both use
  `git diff --name-only -z --no-renames`, parse NUL-delimited, and agree on format
  (IF-0-BRK-1); a rename of an unowned file into owned space is now caught (both endpoints
  surface); a test proves the rename escape is closed.
- [ ] IF-0-BRK-1 is frozen as filename BYTE-identity, not symmetric parsing: both sides split
  on NUL, discard ONLY the terminal empty element, and do NOT trim any path element. A
  regression test asserts a filename with leading/trailing whitespace AND one with an embedded
  newline compare byte-for-byte between broker diff-paths and coordinator owned-paths, and that
  a wrong (whitespace-bearing) path is NOT approved — a rename-escape test alone is
  insufficient because both parsers could symmetrically trim and still pass it.
- [ ] `head_sha`-pinned push (`git push <url> <head_sha>:refs/heads/<branch>`) — a test
  proves a ref advancing between validation and push cannot publish unverified content.
- [ ] `gh pr create` passes `--base <request.base>`; base revision-syntax is guarded
  (reject `~ ^ @{ ..` / base==branch); empty-owned + empty-diff no longer reaches push.
- [ ] Full non-dotfiles suite green; merged via cross-vendor CR + green CI.

**Scope notes**
- Decompose into 2 lanes that SHARE the IF-0-BRK-1 path-format freeze: (a) broker-side
  (`convergence/broker/credsep.py` — `_branch_diff_paths`, push pinning, `gh pr create
  --base`, base guard); (b) coordinator-side (`train_runner.py` — `_prebuilt_owned_paths`
  `-z`/`--no-renames`). Publish IF-0-BRK-1 (the exact `-z --no-renames` command + NUL-split)
  before either lane implements, so both adopt it simultaneously — a broker-only change
  would desync from the coordinator and false-reject legitimate renames.
- Single-writer: the shared path-format helper (if extracted) is owned by lane (a).

**Non-goals**
- #248 external filesystem isolation (parked — different subsystem, see Non-Goals).

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/convergence/broker/credsep.py
- phase-loop-runtime/src/phase_loop_runtime/train_runner.py

**Depends on**
- (none)

**Produces**
- IF-0-BRK-1

**Spec closeout policy**
- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `phase-loop-runtime/src/phase_loop_runtime/convergence/**`
- evidence paths: `phase-loop-runtime/tests/**`
- redaction posture: `metadata_only`

---

### Phase 4A — Advisor-board first-class delta review (FAB)

**Objective**
Deliver #191 (advisor-board first-class delta review with reviewed-byte equivalence), after
first reconciling the live uncommitted advisor-board work stranded in the
`feat/advisor-board-abdreg` and `phase/abdresolve` worktrees.

**Exit criteria**
- [ ] ALL FOUR worktrees carrying uncommitted advisor-board state are reconciled —
  `agent-harness-abdreg`, `ah-abdreg-pkg`, `ah-abdreg-rebase` (divergent copies on
  `feat/advisor-board-abdreg`), and `agent-harness-abdresolve` (`phase/abdresolve`): each
  landed on a branch, committed-and-parked, or explicitly discarded with a recorded
  decision — no silent loss (including the divergent copies), working-tree state documented.
- [ ] #191: the advisor board supports a first-class delta review where the reviewed bytes
  are equivalent to the full-artifact review (reviewed-byte equivalence), with a test.
- [ ] Full non-dotfiles suite green; merged via cross-vendor CR + green CI.

**Scope notes**
- Decompose into 2 lanes: (a) **prerequisite** — reconcile ALL FOUR worktrees carrying
  uncommitted advisor-board state (the three `feat/advisor-board-abdreg` copies +
  `phase/abdresolve`), scoped BY BRANCH not by a fixed count so a divergent copy can't be
  dropped silently (a single-writer that must complete before lane (b) touches
  `advisor_board/`); (b) the #191 delta-review feature on the reconciled base. Lane (b)
  depends on lane (a).
- This is a FEATURE (on-demand), scheduled after the hardening phases land.

**Non-goals**
- Any advisor-board change that would strand more uncommitted worktree state.

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/advisor_board/
- phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py

**Depends on**
- P1
- PAR
- BRK

**Produces**
- IF-0-FAB-1

**Spec closeout policy**
- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `phase-loop-runtime/src/phase_loop_runtime/advisor_board/**`
- evidence paths: `phase-loop-runtime/tests/**`
- redaction posture: `metadata_only`

---

### Phase 4B — Avatar/browser-media closeout evidence (FAV)

**Objective**
Deliver #91 (require blocking visual-avatar evidence for phase-loop avatar/browser media
closeout) as an opt-in-to-block gate, autonomy-first.

**Exit criteria**
- [ ] A phase whose closeout produces avatar/browser media requires blocking visual-avatar
  evidence; absent evidence → the gate blocks only when opted in (warn-default otherwise),
  with a test covering both the warn and opt-in-block paths.
- [ ] Legacy phases with no avatar/browser media surface get no finding.
- [ ] Full non-dotfiles suite green; merged via cross-vendor CR + green CI.

**Scope notes**
- Single lane: this is one bounded gate addition in the closeout-evidence path; it shares no
  files with FAB and has no internal freeze, so it runs as its own leaf phase concurrently
  with FAB. Justified single lane (one cohesive gate, no disjoint partition).
- FEATURE (on-demand), scheduled after the hardening phases land.

**Non-goals**
- No new human-required gate; the block is opt-in.

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/ (closeout evidence gate)

**Depends on**
- P1
- PAR
- BRK

**Produces**
- IF-0-FAV-1

**Spec closeout policy**
- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `phase-loop-runtime/src/phase_loop_runtime/**`
- evidence paths: `phase-loop-runtime/tests/**`
- redaction posture: `metadata_only`

---

## Execution Notes

- **Planning**: `/claude-plan-phase P1`, `/claude-plan-phase PAR`, and `/claude-plan-phase
  BRK` can be planned concurrently (no shared DAG ancestor). Plan `FAB` and `FAV` after the
  three hardening phases land (they carry a priority dependency, not a freeze).
- **Execution**: `/claude-execute-phase p1`, `/claude-execute-phase par`,
  `/claude-execute-phase brk` in parallel; then `/claude-execute-phase fab` and
  `/claude-execute-phase fav` in parallel.
- **Critical path**: `P1|PAR|BRK → FAB` (FAB's reconcile-then-feature lane chain is the
  longest) — wall-clock minimum is the hardening round plus the advisor-board chain.
- **Parallel branches**: the three hardening phases are fully concurrent; the two feature
  phases are concurrent with each other once hardening lands.
- **Single-writer files across phases**: none — the phases own disjoint files (P1 gates,
  PAR runner/goal_coverage, BRK broker+train_runner, FAB advisor_board, FAV closeout gate).
  Within BRK, the broker/coordinator lanes share the IF-0-BRK-1 path-format freeze; within
  PAR, the two lanes share IF-0-PAR-1.

---

## Acceptance Criteria

- [ ] All open bounded-hardening issues (#238, #243, #231) are closed via merged PRs (with
  #241 either done or explicitly deferred), each cross-vendor CR'd.
- [ ] The gate-parity gaps are closed with TWO shared helpers: a preflight helper
  (IF-0-PAR-1, #244) reused by the direct + lane-scheduler/work-unit paths, and a *separate*
  closeout helper (IF-0-PAR-2, #245) reused by the direct + delegated-child paths. A conflated
  single helper does NOT satisfy this — preflight `{verification, goal-coverage}` and closeout
  `{produced-gates, goal-coverage}` are distinct gate families at distinct lifecycle points.
- [ ] The broker #250 hardening cluster is closed with the broker + #201 coordinator in
  sync on the `-z --no-renames` path format.
- [ ] The two features (#191, #91) are delivered, with the abdreg/abdresolve worktree work
  reconciled and no silent loss.
- [ ] Parked items (#246, #248) remain explicitly out of scope; no enforce-mode / external
  isolation work was pulled in.

## Verification

```bash
# Every open backlog issue targeted by this roadmap is closed:
for n in 238 243 231 244 245 250 191 91; do
  gh issue view $n --repo Consiliency/agent-harness --json state -q .state
done   # expect: all CLOSED (or #241 explicitly deferred)

# Full runtime suite green after the roadmap lands:
cd phase-loop-runtime && PYTHONPATH=src:tests python3 -m pytest tests/ -q -m "not dotfiles_integration"

# Parked items stayed open (not silently pulled in):
gh issue view 246 --repo Consiliency/agent-harness --json state -q .state  # OPEN
gh issue view 248 --repo Consiliency/agent-harness --json state -q .state  # OPEN
```
