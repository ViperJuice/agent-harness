# agent-harness — Cross-Repo Release-Train Orchestration — Phase Plan (cross-repo-v1)

> How to use this document: save to `specs/phase-plans-cross-repo-v1.md`, then run `/claude-plan-phase <ALIAS>` to produce the lane-level plan for each phase (→ `plans/phase-plan-cross-repo-v1-<alias>.md`), then `/claude-execute-phase <alias>` to build it.

> Resolves GitHub issue #29 (cross-repo publication; follow-up to #28). Design + binding reconciliation: `plans/cross-repo-orchestration-design-29.md` + `plans/cross-repo-orchestration-29-panel-reconciliation.md` (3-model advisor panel).

---

## Context

#28 (shipped, v0.1.10) made a verified **single-repo** implementation default to a worktree→branch→PR. #29 is the **cross-repo** case: one logical change spanning N repos with ordering dependencies (the example: `consiliency-portal` SQL+UI must land before `message-board` consumes its schema).

The advisor panel (unanimous PARTIALLY AGREE) confirmed the **topology** — a runtime **coordinator** that composes the *unchanged* per-repo `run_loop` — but invalidated the original "DAG-of-DAGs via lifted IF-gates" primitive: cross-repo IF-gates **do not exist** (tokens are hardwired to a phase alias, there is no consume-side edge, and no content identity). The reconciled design ("**thinner Option C**"):

- Edges are `Depends on: <upstream node>`; the cross-repo contract is a **gate pinned to the upstream merge-commit SHA** — which *is* the content identity, and gating downstream on the upstream **merge** (not "produced") kills a designed-in **false-green** bug (a downstream worktree must test against a *real* merged upstream).
- **Train-level governed review is MVP, not optional** (2-of-3): per-repo merging creates the *partial-merge rollback disaster*, and a single-repo reviewer can't catch a change that's wrong *for* the consumer. So: hold **linked draft PRs → one train-level review → sequential merge**, re-verifying each downstream against the upstream merged SHA.
- The #28 publish flow lives in **skill prose**, not the runtime — so a runtime-owned coordinator needs publishing **factored into a runtime primitive** first (the prerequisite).
- **The cross-repo dependency is not a git relationship — it is a consumption channel** (a package/version pin, a `git submodule`, or a workspace path). Two repos have *unrelated git histories*, so a downstream "rebase onto the upstream commit" is meaningless. Because the per-repo `run_loop` is unchanged, **the coordinator must inject** the upstream ref into the downstream workspace *through that channel* before invoking `run_loop` — the upstream **draft** branch during execution (P3) and the upstream **merged SHA** during the pre-merge re-verify (P4). This injection primitive (IF-0-P2-2) is the load-bearing mechanism that makes both the build and the false-green guard real.

---

## Assumptions (fail-loud if wrong)

1. `run_loop(repo, roadmap, run_mode)` (`runner.py:1088`) is a clean per-repo atom: all state under `repo/.phase-loop/`, returns `(StateSnapshot, results)`. The coordinator threads only `(repo, roadmap, run_mode)` and reads back the snapshot + PR URL; distinct repos do not collide.
2. The #28 worktree→branch→verify→commit→push→PR logic currently lives in the execute-skill SKILL.md prose (not runtime). It is mechanizable into a runtime function with the same safety invariant (never commit to main/protected or from a dirty checkout; scoped staged-diff audit; draft/ready; publication-blocked stop).
3. "never commit to main/protected" holds, so every upstream landing yields a **merge-commit SHA** usable as the cross-repo content identity.
4. The governed pre-merge gate is single-repo (inside closeout, `git diff --cached` of one worktree); per-repo `--governed` composes. A **train-level** review is net-new.
5. Cross-repo train state cannot live in any repo's `.phase-loop/`; it needs a coordinator-side durable store.
6. Each cross-repo edge has a determinable **consumption channel** (a package/version pin, a `git submodule`, or a workspace path) that the coordinator can re-resolve to a given upstream ref. If an edge declares no channel the train **fails loud at `validate-roadmap` (P2)** — never silently at execution. The MVP supports that fixed small set of channel kinds (no plugin system).

## Non-Goals

- **No arbitrary parallel DAG-of-DAGs scheduling.** MVP is serial, ordered, 2-3 repos.
- **No automatic cross-repo revert/compensation engine.** Forward-only + resume; the ledger may record a revert *candidate*, but the coordinator does not auto-revert.
- **No monorepo merge / synchronous atomic cross-repo commit.**
- **No change to the single-repo `run_loop`** beyond calling the new publishing primitive.
- **No content-hash / consumer-verified-hash hardening** (merge-SHA identity is the MVP; richer contract identity is a follow-up).
- **No resurrection of the lifted-IF-gate DAG engine.**

---

## Cross-Cutting Principles

1. **Compose, don't rewrite.** The per-repo `run_loop` is called unchanged; all new behavior lives in the coordinator + a runtime publishing primitive + a train ledger.
2. **Gate on merge, not on "produced."** Downstream starts/verifies against the upstream **merged** SHA — never an unmerged upstream artifact (the false-green invariant).
3. **No partial merge without train approval.** Nothing merges until the train-level governed review passes; a downstream failure leaves upstream legitimately on `main` (forward-only) — never a half-merged broken cross-repo state mid-train.
4. **Durable, resumable, coordinator-owned state.** The train ledger is append-only/atomic (like `events.jsonl`); resume re-reads live PR + SHA state before acting; `.phase-loop/` stays per-repo authoritative.
5. **Runtime owns the controls; a thin skill fronts them.** The coordinator (gated state machine) and publishing are runtime code; `run-train` is the human entry point — the #28 lesson (skill-only controls degrade silently).

---

## Phase Dependency DAG

```
  P1  Runtime publishing primitive (factor #28 publish out of skill prose)
   │
   ├──────────────┐
   ▼              ▼
  P3  Coordinator: serial draft-PR execution + train ledger
   │   (also depends on P2)
   ▲
  P2  Train roadmap schema + merge-SHA gate identity + ledger format + validator
   │
   ▼
  P4  Train-level governed review + sequential merge + downstream re-verify
   │
   ▼
  P5  End-to-end, invariants & docs
```

(P1 and P2 are independent roots; P3 needs both; P4 needs P3; P5 needs P4. Serial in practice — P3/P4 edit the coordinator region.)

---

## Top Interface-Freeze Gates

1. **IF-0-P1-1** — Publishing-primitive contract: a runtime function that takes a repo + worktree + owned-paths + draft/ready intent and performs the #28 flow (invariant-guarded worktree/branch selection, scoped staged-diff audit, commit, push, `gh pr create`), returning `{branch, head_sha, pr_url, status}` or a `publication_blocked` reason — the **`branch` and `head_sha` are load-bearing**: the coordinator injects them into downstream nodes (IF-0-P2-2). Reuse `git_topology.resolve_closeout_push_target` / `_gh_pr_metadata` (already in runtime). Consumed by P3/P4 and re-consumed by the execute skills.
2. **IF-0-P2-1** — Train contract: the release-train roadmap schema (nodes = `(repo, roadmap)`, `Depends on: <upstream node>` edges), the **merge-SHA-pinned cross-repo gate identity**, and the durable append-only **train-ledger** record shape (node status `pending|running|pr_open|approved|merged(sha)|blocked`, branch, PR URL, upstream merge SHA, merge order). Consumed by P3/P4.
3. **IF-0-P2-2** — Cross-repo **consumption channel + injection primitive** (the load-bearing mechanism the panel found missing): a per-edge **channel descriptor** declaring HOW a downstream node references an upstream node — a package/version pin, a `git submodule` path, or a workspace/path override (NOT a git rebase: separate repos have unrelated histories) — plus a runtime **re-resolution operation** `set_upstream_ref(downstream_workspace, channel, ref)` that the **coordinator** runs to point the channel at a given upstream ref/SHA **before invoking the unchanged `run_loop`**. This is how an unchanged per-repo `run_loop` consumes the upstream at all (draft branch in P3, merged SHA in P4). Frozen in P2; consumed by P3 (inject draft) and P4 (re-inject merged + re-verify).

---

## Phases

### Phase 1 — Runtime Publishing Primitive (P1)

**Objective**
Factor the #28 worktree→branch→verify→commit→push→PR flow out of the execute-skill prose into a runtime module both the coordinator and the execute skills call — so the controls are runtime-owned (not skill-only).

**Exit criteria**
- [ ] A runtime `publishing` module exposes a function performing the #28 flow for one repo/worktree: invariant-guarded workspace selection (never `main`/protected/dirty/unowned), fresh base ref, scoped **staged-diff audit** (owned paths only, no ignored/secret paths, `--check`, fail-closed), commit (no `-A`), push (no force; rejected → stop), `gh pr create` draft/ready — returning `{branch, pr_url, status}` or a structured `publication_blocked` reason (IF-0-P1-1).
- [ ] The function is pure-of-prose: it encodes the safety invariant in code, callable headlessly by the coordinator (no skill interpretation required).
- [ ] The execute skills are repointed to invoke it (or documented to), so the publish behavior has one source of truth; the #28 single-repo behavior is preserved (existing publication semantics unchanged).
- [ ] Tests: each invariant branch (main/protected/dirty/unowned → stop; scoped-audit catches an out-of-scope/secret path; push-rejected → stop; draft vs ready) with a stubbed git/`gh` boundary — no live pushes.

**Scope notes**
- Decompose into 2 lanes: (a) the `publishing` runtime module + IF-0-P1-1 contract + tests (stubbed git/gh); (b) repoint the execute-skill prose to the primitive (source-first in `skills-src/`, regenerate; parity/drift green). Lane (a) is the integrator owning the new contract.

**Non-goals**
- No cross-repo logic; single-repo publishing only. No new PR-review behavior.

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/publishing.py
- phase-loop-runtime/src/phase_loop_runtime/cli.py
- skills-src/claude/claude-execute-detailed/SKILL.md
- phase-loop-runtime/tests/test_publishing.py

**Depends on**
- (none)

**Produces**
- IF-0-P1-1

---

### Phase 2 — Train Roadmap Schema, Merge-SHA Gate & Ledger (P2)

**Objective**
Define the data model: the cross-repo release-train roadmap (nodes + `Depends on` edges), the merge-SHA-pinned cross-repo gate identity, the durable resumable train ledger, and a `validate-roadmap` extension for trains.

**Exit criteria**
- [ ] A train-roadmap schema + parser (a **NEW parser**, not an extension of the `### Phase N (ALIAS)` regex — only `roadmap_lint`'s topo/cycle algorithm is reused): nodes = `(repo, roadmap)`; edges = `Depends on: <upstream node>`; a cross-repo gate identity **pinned to an upstream merge SHA** (NOT the in-repo `IF-0-<alias>-<n>` token — a new namespace) (IF-0-P2-1); plus a per-edge **consumption-channel descriptor** (package/version pin | submodule path | workspace override) and the runtime **injection/re-resolution primitive** `set_upstream_ref(workspace, channel, ref)` (IF-0-P2-2).
- [ ] A durable, append-only train **ledger** with a **self-consistent** durability model: atomic single-`write` append (`O_APPEND`) **plus a tolerant resume reader that DROPS a malformed trailing line** (note: `events.py:read_events` does a bare `json.loads` and crashes on a truncated final line — so this tolerant reader is **net-new**, not a mirror of `events.py`; no temp-rename). Records per-node `status`, `branch`, `pr_url`, `upstream_merge_sha`, `merge_order`; a resume reader that re-reads live PR/SHA state.
- [ ] `validate-roadmap` (train mode) validates: the cross-repo DAG is acyclic, every depended-on node exists, the train is serially orderable (topo-sort succeeds), **and every edge carries a valid consumption-channel descriptor**; a non-orderable/cyclic/channel-less train fails loud.
- [ ] Train state never touches any repo's `.phase-loop/`; a test proves ledger append/resume survives a simulated crash (a truncated trailing write is dropped, not crashed on).
- [ ] Tests: schema parse/validate (valid + cyclic + missing-node + missing-channel + invalid in-repo-token-reused); `set_upstream_ref` re-resolves each channel kind to a given ref (stubbed); ledger append/resume/atomicity incl. truncated-trailing-line.

**Scope notes**
- Decompose into 3 lanes: (a) the train-roadmap schema/parser + the merge-SHA gate identity + the `validate-roadmap` train extension (IF-0-P2-1); (b) the consumption-channel descriptor + the `set_upstream_ref` injection primitive (IF-0-P2-2); (c) the durable train-ledger module + tolerant resume + atomicity tests.

**Non-goals**
- No coordinator/execution (P3). No content-hash identity (merge SHA only). The channel kinds are a fixed small set (pin/submodule/workspace) — no plugin system.

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/train_roadmap.py
- phase-loop-runtime/src/phase_loop_runtime/train_ledger.py
- phase-loop-runtime/src/phase_loop_runtime/cross_repo_channel.py
- phase-loop-runtime/tests/test_train_roadmap.py

**Depends on**
- (none)

**Produces**
- IF-0-P2-1
- IF-0-P2-2

---

### Phase 3 — Coordinator: Serial Draft-PR Execution (P3)

**Objective**
The `train_runner` / `phase-loop run-train`: topo-sort the train, run each per-repo `run_loop` (unchanged, per-repo `--governed`) in order to open **linked draft PRs**, persist the ledger, and support partial-success/resume — without merging anything yet.

**Exit criteria**
- [ ] **Train-level preflight (entry-gate, before ANY PR is opened):** verify **all** repos in the train are clean, `gh` auth is valid, remotes are reachable, and each base branch exists — fail-loud and atomically up front. A preflight failure opens **zero** PRs (closes the partial-draft-train hole — the partial-PR analog of the partial-merge disaster).
- [ ] `phase-loop run-train --train <file> [--governed]` topo-sorts the train and, per node in order: (i) **inject the upstream's draft via `set_upstream_ref` (IF-0-P2-2)** — point this node's consumption channel at each upstream node's **draft branch/`head_sha`** in its workspace, so the unchanged `run_loop` builds/verifies against the actual upstream change-in-flight (an unchanged `run_loop` cannot otherwise see the upstream); (ii) invoke `run_loop(repo, roadmap, run_mode)`; (iii) call the P1 publishing primitive to open a **draft** PR; record `pr_open`, branch, `head_sha`, PR URL in the ledger; cross-link PRs (dependency + merge order in each body).
- [ ] **No merges occur in this phase** — execution stops at "all linked draft PRs open + ledgered." Partial failure (a node's `run_loop` or publish blocks/fails) leaves prior nodes' draft PRs open, the ledger marking the failed node `blocked`, and the train **resumable** (re-run skips completed nodes by re-reading the ledger + live PR state).
- [ ] Per-repo `--governed` composes (each node's own pre-merge gate runs inside its `run_loop`); the coordinator adds no `human_required`.
- [ ] Tests: preflight fails (dirty repo / bad auth) → zero PRs opened; a 2-3 node train (mocked `run_loop` + stubbed publishing/channel) injects each upstream draft via `set_upstream_ref` then opens linked draft PRs in order; a mid-train node failure → ledger `blocked` + resumable; no merge is attempted.

**Scope notes**
- Single lane: the `train_runner` module + the `run-train` CLI subcommand + the draft-PR-only execution loop over P1/P2, with ledger persistence and resume. Justified single lane — one coherent coordinator state machine; consumes IF-0-P1-1 + IF-0-P2-1.

**Non-goals**
- No train-level review, no sequential merge, no downstream re-verify (all P4).

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/train_runner.py
- phase-loop-runtime/src/phase_loop_runtime/cli.py
- phase-loop-runtime/tests/test_train_runner.py

**Depends on**
- P1
- P2

**Produces**
- (none)

---

### Phase 4 — Train-Level Review, Sequential Merge & Downstream Re-Verify (P4)

**Objective**
The safety-critical merge orchestration: hold the linked draft PRs for one **train-level governed review**, then merge sequentially — re-verifying each downstream node against the upstream's **merged** SHA before it merges. Closes the partial-merge rollback hole and the false-green hole together.

**Exit criteria**
- [ ] After P3 opens all linked draft PRs, the coordinator holds for **one train-level governed review** of the bundle (the linked PRs reviewed as one logical change). Reuses the governed panel machinery; a non-approval halts the train with a non-human terminal — **no PR is merged** (the partial-merge-disaster guard).
- [ ] On approval, merge **sequentially** in topo order. Before merging each downstream node, **re-verify it against the upstream's merge SHA — via the consumption channel, NOT a git rebase** (separate repos have unrelated histories): call `set_upstream_ref(downstream_workspace, channel, <upstream_merge_sha>)` (IF-0-P2-2) to re-resolve the downstream's dependency to the **merged** contract, then re-run that node's verification. A re-verify failure halts the train (downstream stays draft, upstream stays legitimately on `main`, ledger records the merged-SHA boundary) — never merge a downstream node that was only green against the *draft/unmerged* upstream (the false-green guard).
- [ ] The re-verify is a **real, testable** step: the P4 test (and the P5 CI invariant) assert the channel was actually **re-resolved to the upstream merged SHA** before the downstream verification ran — not merely that a re-verify function was called.
- [ ] The ledger records `approved`, then per node `merged(<sha>)` in order; resume after a crash mid-merge re-reads live PR/merge state and continues from the last `merged` node (idempotent).
- [ ] Forward-only: a downstream failure does NOT revert merged upstream nodes; the ledger may record a revert *candidate* but the coordinator does not auto-revert. Recommend (doc) expand/contract upstream contracts so sequential merges are low-risk.
- [ ] Tests (mocked panel + stubbed git/gh): train-review block → no merges; approve → sequential merges in order with a downstream re-verify gate that, when failed, halts before the downstream merge while upstream stays merged; crash-mid-merge resume is idempotent.

**Scope notes**
- Single lane: extend `train_runner` with the train-review hold + the sequential-merge loop + the per-downstream re-verify-against-merged-SHA gate + ledger merge-state transitions. Sequenced after P3; one coherent merge state machine.

**Non-goals**
- No auto-revert engine; no parallel merges; no content-hash identity.

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/train_runner.py
- phase-loop-runtime/src/phase_loop_runtime/governed_review.py
- phase-loop-runtime/src/phase_loop_runtime/cross_repo_channel.py
- phase-loop-runtime/src/phase_loop_runtime/pipeline_adapter/branch_ops.py
- phase-loop-runtime/tests/test_train_merge.py

(Note: `branch_ops.py`/`merge_policy.py` are the existing rebase/merge primitives but are **intra-repo only** — insufficient for the cross-repo re-verify, which goes through `set_upstream_ref`, not a rebase.)

**Depends on**
- P3

**Produces**
- (none)

---

### Phase 5 — End-to-End, Invariants & Docs (P5)

**Objective**
An end-to-end train test (mocked), the CI invariants that lock in the safety properties, and the docs — the honest, hardened finish.

**Exit criteria**
- [ ] An end-to-end test (mocked `run_loop` + panel + stubbed git/gh) exercises a 2-3 repo train: draft PRs → train review → sequential merge with downstream re-verify → all merged; plus the non-approval terminal and a mid-train resumable failure.
- [ ] CI invariants: **no node merges before train approval**; **no downstream merges without the consumption channel having been re-resolved to the upstream merged SHA and the node re-verified** (false-green guard — asserts the re-resolution *occurred*, not just that a function was called); a **preflight failure opens zero PRs**; train state never written under any `.phase-loop/`; per-repo autonomous/governed behavior unchanged (no `human_required` added by the coordinator).
- [ ] A `run-train` skill (or roadmap-builder "train" mode) fronts the coordinator as the human entry point — thin, deferring all gated logic to the runtime.
- [ ] Docs: `protocol.md` (the train ledger + merge-SHA gate + the merge/re-verify invariants), `README.md`, CHANGELOG (#29), and the cross-repo authoring guide (incl. the expand/contract recommendation). `validate-roadmap specs/phase-plans-cross-repo-v1.md` passes; full standalone suite green; #12 + skills-parity drift gates green.

**Scope notes**
- Single lane: the e2e train test + the CI invariant suite + the `run-train` skill (source-first in `skills-src/`, regenerate) + the docs sweep, landed atomically. Sequenced after P4.

**Non-goals**
- No new execution behavior beyond P1–P4; tests/skill/docs/invariants only.

**Key files**
- phase-loop-runtime/tests/test_train_e2e.py
- skills-src/claude/claude-run-train/SKILL.md
- phase-loop-runtime/src/phase_loop_runtime/_contract_docs/phase-loop/protocol.md
- CHANGELOG.md

**Depends on**
- P4

**Produces**
- (none)

---

## Execution Notes

- The MVP is the whole train (P1–P5) but **serial, ordered, 2-3 repos only** — defer arbitrary parallel DAG scheduling and content-hash identity (Non-Goals).
- **Gate on merge, never on "produced"** (Principle 2): the downstream re-verify against the upstream merged SHA (P4) is the load-bearing false-green guard — do not let a node merge that was only green against an unmerged upstream.
- **No partial merge without train approval** (Principle 3): all merge logic lives behind the P4 train-review hold.
- The coordinator + publishing + ledger are **runtime**; `run-train` is a thin skill (Principle 5). Reuse the per-repo `run_loop` and governed machinery unchanged.
- Keep the #28 publishing behavior single-source (P1) so the coordinator and the execute skills can't drift.

## Acceptance Criteria

- [ ] A runtime release-train coordinator serially runs the unchanged per-repo `run_loop` (per-repo `--governed`) to open linked **draft** PRs for 2-3 ordered repos, persisting a durable resumable train ledger.
- [ ] One **train-level governed review** holds before any merge; on approval, **sequential merge** with each downstream **re-verified against the upstream merged SHA**; forward-only on failure (no auto-revert).
- [ ] Cross-repo edges are `Depends on: <upstream node>` with gates pinned to the **upstream merge SHA** (no lifted IF-gate engine); train state never touches `.phase-loop/`.
- [ ] Publishing is a runtime primitive shared with the execute skills; full suite + `validate-roadmap` + parity/drift gates green; docs + `run-train` skill shipped.

## Verification

```bash
# Roadmap lints clean
PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-cross-repo-v1.md

# Publishing primitive (after P1)
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/ -k "publishing" -q

# Train schema + ledger (after P2)
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/ -k "train_roadmap or train_ledger" -q

# Coordinator draft-PR execution (after P3) + merge orchestration (after P4)
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/ -k "train_runner or train_merge" -q

# End-to-end + full suite (after P5)
cd phase-loop-runtime && PYTHONPATH=src python -m pytest -q
```
