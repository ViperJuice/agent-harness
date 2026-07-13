# Phase Plans — Convergence: Crash-Safe Cross-Repo Train + Credential Broker (v1)

## Context

Long-lived interactive coordinator sessions (esp. multi-repo Codex trains) fail to
converge because a single conversation is forced to act as the source of operational
truth. Once conversation context, live Git, repo-local `.phase-loop` state, PR state,
provider state, and child-agent state diverge, recovery means reconstructing intent
from transcripts instead of reconciling a durable execution record.

This roadmap productionizes the cross-repo release train (`train_runner.py`) and all
side-effecting operations (publish / merge / release / package) into a **crash-safe,
credential-isolated, fail-closed coordinator**. It is the ratified output of a 5-pass,
4-seat panel (Grok 4.5 / Fable 5 / Sol / Gemini 3.1 Pro all AGREE) whose reconciliations
are recorded under `.phase-loop/reviews/convergence-analysis-*.md` +
`convergence-panel-ratification.md`. Later reconciliations supersede earlier language;
the terminal-outcome rule (v5) is authoritative on in-flight provider operations.

This is the **coordinator-infrastructure track**. It is designed to run in parallel with
the spec-canonicalization domain (SPECCONFORM / spec-engine / GP ingestion), which
touches mostly disjoint files. SPECPKGMIN — a real 3-repo slice already built and
verified (wheel 9/9, GP interchange 42/42) — is the first cross-repo PILOT that
exercises the broker's publish-committed path end to end.

## Architecture North Star

```
        human-approved versioned roadmaps  ── intent
                     │
        ┌────────────▼─────────────┐        durable, append-only
        │  COORDINATOR             │        coordinator EVENT LOG  ◄── authority for
        │  (no mutation creds)     │        (train id, node, base/head SHA, digests,   active
        │  reconcile-before-act    │         seat outcomes, attempt id, epoch, …)      op state
        └───┬───────────────┬──────┘
            │ read-only      │ typed side-effect requests (linearizable admission)
   reconcile against         ▼
   exact live state   ┌──────────────────────────┐
   (git/gh/provider/  │  CREDENTIAL BROKER epoch  │  ── the ONLY holder of mutation
    registry)         │  publish/merge/release/   │     credentials; policy-ordered;
            │         │  package + publish_       │     emits TERMINAL effect/no-effect
            ▼         │  committed_branch         │     evidence; fail-closed on ambiguity
   authority split:   └──────────────────────────┘
   git commits/PR heads = impl · merged SHA = merged · registry = released
```

## Assumptions (fail-loud if wrong)

1. The train ledger is already coordinator-owned and outside every repo's `.phase-loop/`
   (`train_ledger.py`) — the correct starting point to extend, not replace.
2. `run_train` already injects immutable upstream SHAs, re-verifies downstream against
   the merged SHA, and blocks on `upstream_changed_downstream_pr_open` — strong
   false-green protections to preserve, never weaken.
3. Providers differ in completion semantics; some irreversible operations have neither
   terminal-success nor terminal-no-effect observability. Those remain human-executed.
4. Claude native Agent/TUI and Codex share ONE durable convergence protocol and return
   the same structured result envelope; neither is exempt from exact-state reconciliation.
5. The existing `test_train_invariants.py` INV-1..INV-6 (merge-SHA false-green killer,
   drafts-open stop, zero-PR-on-preflight-failure) remain green throughout.

## Non-Goals

- Not diagnosing a Codex model regression; the runtime durability gap is the target
  regardless of trigger.
- No automatic human-override that labels an ambiguous accepted provider request as
  proven no-effect (explicitly forbidden by v5).
- No production enablement before the adversarial fault suite passes (this roadmap is
  roadmap-input readiness → implementation; production is gated at RELEASE).
- Not re-implementing the spec-canonicalization domain (separate track/roadmap).
- No standalone migration/rollback phase — the coordinator event log is greenfield. If
  pre-roadmap durable ledger state is discovered at FREEZE, migrate it as a FREEZE lane-A
  scope item, not a new phase (panel nit).

## Cross-Cutting Principles

- **Reconcile before every act** (dispatch, resume, publish, review, merge, release)
  against exact live Git / GitHub / provider / registry — never trust a repo-local cache
  as authority.
- **Persist intent before dispatch, outcome after dispatch**; a coordinator crash at
  either boundary must be recoverable without reading its transcript.
- **No mutation credentials in worker or conversational-coordinator environments**; all
  side effects go through the single broker epoch with linearizable admission.
- **Terminal-outcome rule (v5):** an operation that reaches `provider_call_in_flight`
  leaves only via `effect_terminal_observed`, `no_effect_terminal_proven`, or
  `outcome_ambiguous_blocked`. Ambiguity is durable and permanently fail-closed; no
  timeout escape, no human override to progress.
- **Invalidate verification + approval** whenever effective code, roadmap, base,
  dependency SHA, or verification-plan digest changes.
- **Safe parallelism is machine-checked:** run independent DAG nodes / seats / suites
  concurrently; serialize same-repo state mutation unless owned-paths + frozen interfaces
  prove it safe; always serialize topological merges and release publication.
- **Single-writer rule (this track):** `train_runner.py`, `publishing.py`, and the new
  broker module have one writer at a time; coordinate against the spec-canonicalization
  track and any in-flight lane.

## Top Interface-Freeze Gates

- **IF-0-FREEZE-1** — Coordinator append-only **event schema**: each event binds at least
  `{train_id, node_id, roadmap_path, roadmap_digest, workspace_id, branch, base_ref,
  base_sha, head_sha, phase, action, owned_paths, executor, model, upstream_dep_shas[],
  verification_artifact, verification_digest, seat_outcomes[], pr_identity, merge_sha,
  release_identity, attempt_id, epoch, timestamp, blocker_reason}`. Append-only; intent
  and outcome are separate records. Carries explicit `event_schema_version`,
  `transition_model_version`, and `invalidation_model_version` so mixed-version records
  are detectable and reconciled (D4).
- **IF-0-FREEZE-2** — Structured **result envelope** enum `{completed, verified, blocked,
  needs_clarification, degraded, failed}` returned identically by Codex, Claude, and
  outside-agent adapters.
- **IF-0-FREEZE-3** — **Provider completion-contract matrix** schema + **terminal-outcome
  state machine**: fields per operation `{idempotency_key_supported, status_endpoint,
  terminal_success_states+object/version, terminal_no_effect_states+non-late-commit
  guarantee, guaranteed_processing_horizon?, expected_version_predicate,
  revocation_affects_accepted?, stabilization_drain_interval}`; states
  `provider_call_in_flight → {effect_terminal_observed | no_effect_terminal_proven |
  outcome_ambiguous_blocked}`; `rejected_before_start` reserved for pre-linearization-point
  proof only.
- **IF-0-FREEZE-4** — **Broker admission + verb contract**: linearizable admission +
  policy-ordering boundary; verbs `publish`, `merge`, `release`, `package`, and
  `publish_committed_branch(repo, branch, head_sha, owned_paths, draft, pr_body) ->
  {branch, head_sha, pr_url}` (folds in the prebuilt-publish slice — see
  `.phase-loop/reviews/prebuilt-publish-slice-design.md`); every verb returns terminal
  effect/no-effect evidence keyed by `(idempotency_key)`; credential-isolation boundary
  (coordinator holds no mutation credential).
- **IF-0-FREEZE-5** — **Exact-state reconciliation contract**: authority split (versioned
  roadmap = intent; event log = active op state; Git commit/PR head = impl; merged SHA =
  merged; registry/manifest = released; transcripts/`.phase-loop` = recovery evidence) +
  the invalidation-trigger set as **normative enums** (not prose): `{effective_code_changed,
  roadmap_changed, base_sha_changed, dependency_sha_changed, verification_plan_digest_changed}`,
  each versioned (D4).
- **IF-0-FREEZE-6** — **Resource-isolation contract**: the machine-checked predicate that
  proves two units may run concurrently (disjoint owned-paths + frozen shared interfaces),
  and the serialization rule for same-repo mutation / topo merges / release publication.
- **IF-0-FREEZE-7** — **Shared admission-request / fencing binding** (D5): the ONE typed
  request shape both RUNTIME and BROKER produce/consume — `{attempt_id, lease_epoch,
  fence_token, approval_digest, expected_version_predicate, authority_domain_scope,
  idempotency_key}`. Frozen here so the two parallel lanes cannot diverge into a hidden
  serial dependency at INTEG.

## Phases

### Phase 0 — Freeze, Preserve, and Measure (FREEZE)

**Objective**
Freeze every cross-phase interface (event schema, result envelope, provider
completion-contract matrix + terminal-outcome state machine, broker verb/admission
contract, reconciliation authority split, resource-isolation predicate); preserve
representative failing-session evidence and baseline convergence measures before any
coordinator behavior changes.

**Exit criteria**
- [ ] IF-0-FREEZE-1..7 are published as concrete typed schemas (dataclasses / JSON schema)
      with docstrings, committed, and importable — no behavior change yet.
- [ ] **Provider Completion-Contract Classification committed (D1):** every automated
      verb×provider pair is classified `supported | human-executed | unsupported`, with
      `status_endpoint`, `idempotency_key`, `terminal_success/no_effect states`, and
      `guaranteed_processing_horizon` filled or explicit `N/A`. This POPULATED classification
      (not just the IF-3 schema) is what BROKER and FAULTS consume.
- [ ] Failure taxonomy documented (crash, partition, stale-worker, delayed-commit,
      mixed-version, exact-head, degraded-seat, ambiguous-outcome, **outside-agent-adversarial**).
- [ ] Representative session evidence + current heads/roadmap bindings from the motivating
      trains are checkpointed as fixtures for the FAULTS suite, **including adversarial
      outside-agent fixtures (D2):** forged completion evidence, malformed result envelope,
      capability overclaim, stale/delayed seat write, mixed-version envelope, action-outside-bounds.
- [ ] Baseline measures captured (time-to-converge, transcript-dependence) for later
      comparison.

**Scope notes**
Preamble / interface-freeze phase; decompose into freeze lanes: **lane A** event-schema +
result-envelope (IF-1,2); **lane B** provider completion-contract matrix +
terminal-outcome state machine (IF-3); **lane C** broker verb/admission + reconciliation +
resource-isolation contracts (IF-4,5,6). Partition by schema file — disjoint. No runtime
wiring here; freezing wrong is the expensive failure this phase exists to prevent.

**Non-goals**
No implementation of ledger reconciliation, broker, or adapters — contracts only.

**Key files**
- `phase-loop-runtime/src/phase_loop_runtime/train_ledger.py` (extend event schema)
- `phase-loop-runtime/src/phase_loop_runtime/convergence/contracts.py` (new: schemas + state machine)
- `.phase-loop/reviews/convergence-*.md` (source), `prebuilt-publish-slice-design.md` (broker verb input)

**Depends on**
- (none)

**Produces**
- IF-0-FREEZE-1
- IF-0-FREEZE-2
- IF-0-FREEZE-3
- IF-0-FREEZE-4
- IF-0-FREEZE-5
- IF-0-FREEZE-6
- IF-0-FREEZE-7

### Phase 1 — Runtime Substrate Lanes (RUNTIME)

**Objective**
Implement, against the frozen contracts, the non-broker runtime substrate: the
append-only event log + exact-state reconciliation engine; bounded Codex/Claude/
outside-agent execution adapters returning the result envelope; advisor-seat lifecycle;
and train-status / observability / transcript-free recovery tooling.

**Exit criteria**
- [ ] Append-only event log persists intent-before / outcome-after records per
      IF-0-FREEZE-1; a crash between the two boundaries is recoverable without transcripts.
- [ ] Reconciliation engine resolves exact live Git/GitHub/provider/registry state and
      emits the IF-0-FREEZE-5 authority verdicts + invalidation triggers.
- [ ] Codex, Claude, and ≥1 outside-agent adapter return the IF-0-FREEZE-2 envelope and
      carry expected-version predicates; none coordinates the train.
- [ ] Advisor-seat lifecycle persists complete per-seat outcomes (required/optional,
      timeout, degraded) into the event log.
- [ ] `train-status` + transcript-free recovery tooling reconstruct train state from the
      event log alone.

**Scope notes**
Decompose into 4 disjoint lanes: **LEDGER-RECON** (`convergence/event_log.py`,
`reconcile.py`); **ADAPTERS** (`convergence/adapters/*`); **SEATS**
(`panel_invoker.py` seat-lifecycle + persistence); **OBSERV** (`convergence/status.py`,
recovery CLI). All four share only the FREEZE contracts → run concurrently. SEATS reuses
existing `panel_invoker` liveness (0.7.4 #188) — do not regress it.

**Non-goals**
No DAG dispatch / downstream refresh / merge wiring (that is INTEG). No credential-holding
operations (that is BROKER).

**Key files**
- `phase-loop-runtime/src/phase_loop_runtime/convergence/{event_log,reconcile,status}.py` (new)
- `phase-loop-runtime/src/phase_loop_runtime/convergence/adapters/{codex,claude,outside_agent}.py` (new)
- `phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py` (seat lifecycle)

**Depends on**
- FREEZE

**Produces**
- IF-0-RUNTIME-1 — event-log read/write API + reconciliation-verdict API consumed by INTEG.

### Phase 2 — Credential Broker Epoch (BROKER)

**Objective**
Build the single credential-capable side-effect broker epoch: linearizable admission +
policy-ordering; sole holder of mutation credentials; verbs `publish` / `merge` /
`release` / `package` / `publish_committed_branch`; each emitting terminal effect/no-effect
evidence keyed by idempotency key; permanent fail-closed on `outcome_ambiguous_blocked`.

**Exit criteria**
- [ ] Broker is the only process with mutation credentials; workers/coordinator envs are
      credential-stripped (machine-checked).
- [ ] Admission is linearizable and policy-ordered; concurrent side-effect requests admit
      in a single total order.
- [ ] Each verb returns IF-0-FREEZE-3 terminal evidence; `publish_committed_branch` pushes
      a committed branch by-name/non-force + opens a draft PR without re-commit, idempotent
      on `(repo, branch, head_sha)` (per the prebuilt-publish design dump).
- [ ] An in-flight op with neither terminal proof is recorded `outcome_ambiguous_blocked`
      and blocks all further privileged epoch progress; no timeout/override escape.
- [ ] Providers lacking a completion contract are marked human-executed/unsupported.
- [ ] **Broker refuses to automate any verb×provider not classified `supported` in the
      FREEZE Provider Completion-Contract Classification (D1)** — fail-closed, no automated
      side effect for `human-executed`/`unsupported`/unclassified operations.

**Scope notes**
Decompose into 4 disjoint lanes: **ADMISSION** (linearizable admission + policy-ordering + epoch);
**VERBS** (publish/merge/release/package + `publish_committed_branch` fold-in);
**EVIDENCE** (terminal effect/no-effect capture + idempotency-key store + fail-closed
ambiguity); **CREDSEP** (strip creds from worker/coordinator; broker-side non-force +
protected-branch re-assertion). Parallel with RUNTIME (disjoint files); both consume only
FREEZE. `publish_committed_branch` must NOT use `resolve_closeout_push_target` (a
worktree-created branch has no upstream) — by-name push, remote rejects non-fast-forward.
The 4 lanes map to distinct files `broker/{admission,verbs,evidence,credsep}.py`, so the
single-writer rule here is **per-file** — the lanes stay genuinely parallel. `publishing.py`
is WRITTEN in this phase (push+PR-create routed through the broker) and only CONSUMED/extended
in INTEG (serial, no real collision).

**Non-goals**
No P4-for-prebuilt governed-merge re-verify (follow-up); coordinator DAG wiring is INTEG.

**Key files**
- `phase-loop-runtime/src/phase_loop_runtime/convergence/broker/{admission,verbs,evidence,credsep}.py` (new)
- `phase-loop-runtime/src/phase_loop_runtime/publishing.py` (route push+PR through broker)

**Depends on**
- FREEZE

**Produces**
- IF-0-BROKER-1 — broker client API (admission + verbs + evidence) consumed by INTEG.

### Phase 3 — Coordinator Integration (INTEG)

**Objective**
Wire the substrate + broker into `run_train`: DAG-parallel dispatch with per-repo locks
and explicit action bounds; attempt-ids / lease-epochs / fencing / approval-binding;
automatic downstream-branch refresh + bound re-verify against the merged upstream SHA +
stale-review invalidation; verification-artifact binding.

**Exit criteria**
- [ ] `run_train` reconciles the event log against live state before dispatch/resume/
      publish/review/merge/release; every side effect flows through the broker.
- [ ] Independent DAG nodes dispatch concurrently under per-repo locks; **every concurrent
      unit-pair passes the IF-0-FREEZE-6 isolation predicate (disjoint owned-paths + frozen
      interfaces) before admission, else is serialized — fail-closed on predicate-false, and
      the decision is persisted to the event log (D3).** Topo merges + release publication
      are always serialized.
- [ ] **Supported-version reconciliation (D4):** records carrying a schema/transition/
      invalidation version the coordinator does not support are rejected (not silently
      coerced); mixed-version state is detected and reconciled, never accepted as-is.
- [ ] After an upstream merge, each affected downstream branch is auto-refreshed (or a
      typed conflict is raised), re-verified against the merged SHA, republished, and its
      prior review invalidated.
- [ ] Attempt-ids/epochs/fencing prevent a stale worker or resumed coordinator from
      double-acting; approval binds to exact (code, base, dep-SHA, verification) digest.
- [ ] INV-1..INV-6 remain green; drafts-open stop (autonomous) preserved.

**Scope notes**
Lanes: **DISPATCH** (DAG + per-repo locks + bounds); **REFRESH** (downstream refresh +
merged-SHA re-verify + review invalidation); **FENCING** (attempt/epoch/fencing/approval-
binding); **INVALIDATE** (verification/approval invalidation triggers). Single-writer on
`train_runner.py` across these lanes — sequence lane merges even though design is parallel.

**Non-goals**
No pilot trains; no production enable.

**Key files**
- `phase-loop-runtime/src/phase_loop_runtime/train_runner.py`
- `phase-loop-runtime/src/phase_loop_runtime/convergence/{event_log,reconcile}.py`

**Depends on**
- RUNTIME
- BROKER

**Produces**
- IF-0-INTEG-1 — integrated coordinator entrypoint + resume contract consumed by FAULTS/PILOT.

### Phase 4 — Adversarial Fault Suite (FAULTS)

**Objective**
Prove crash-safety and fail-closed behavior with an adversarial fault-injection suite that
MUST pass before any pilot or production enablement.

**Exit criteria**
- [ ] Killing the coordinator at each state transition permits transcript-free, idempotent
      resume (crash-injection).
- [ ] Partition / stale-worker faults never double-act (fencing proven).
- [ ] The **delayed-provider-commit matrix** (v5) passes: ambiguous-blocked never times out
      into progress; a late effect is observed + reconciled exactly once; provider terminal
      rejection binds evidence to the op id before epoch advancement; a guaranteed horizon
      permits progress only after horizon + drain + stable expected-version; an op without
      status/idempotency is unsupported from the outset.
- [ ] Mixed-version + exact-head faults: no stale base/roadmap/dep/head/review/verification
      artifact is ever accepted; a record whose schema/transition/invalidation version is
      unsupported is rejected (D4).
- [ ] **Outside-agent adversarial faults (D2) pass BEFORE any pilot runs:** forged
      completion evidence, malformed result envelope, capability overclaim, stale/delayed
      seat write, mixed-version envelope, and action-outside-bounds are each detected and
      fail-closed (not just the happy-path admission tested in PILOT).

**Scope notes**
Decompose into 5 fault-family lanes: **CRASH-RESUME**; **PARTITION-STALE**; **DELAYED-COMMIT**;
**MIXED-VERSION-EXACT-HEAD**; **OUTSIDE-AGENT-ADVERSARIAL** (D2 — req 11 lists outside-agent
as a fault family; it must be adversarially tested here, not only exercised as PILOT
happy-path admission). Uses the FREEZE-phase preserved fixtures (incl. the adversarial
outside-agent fixtures). This is a HARD GATE — PILOT/RELEASE depend on it.

**Non-goals**
No new coordinator features; tests + minimal fixes only.

**Key files**
- `phase-loop-runtime/tests/convergence/test_faults_*.py` (new)
- `phase-loop-runtime/tests/test_train_invariants.py` (extend)

**Depends on**
- INTEG

**Produces**
- IF-0-FAULTS-1 — green adversarial-fault certification gating PILOT.

### Phase 5 — Parallel Pilots (PILOT)

**Objective**
Run real cross-repo trains through the productionized coordinator, stopping at
`drafts_open` for human merge. The keystone pilot is **SPECPKGMIN**: land the already-built
3-repo slice (spec wheel / GP allowlist+interchange / agent-harness dogfood) via the broker
`publish_committed_branch` path → 3 coordinated draft PRs.

**Exit criteria**
- [ ] SPECPKGMIN pilot: 3 draft PRs opened via the broker publish-committed path, ledger +
      `train-status` consistent, interchange verification preserved (no fresh re-execution),
      stops at `drafts_open`.
- [ ] ≥1 representative multi-node train + the admin/outside-agent pilot (with capability
      admission + `needs_clarification`/`review_candidate`/`reject`) converge without a long
      coordinator transcript.
- [ ] Time-in-ambiguous-block is tracked; no auto-failover for providers lacking terminal
      completion semantics.
- [ ] Pilot product work stays in its owning repo; cross-repo train state stays with the
      coordinator.

**Scope notes**
Decompose into 3 disjoint lanes: **SPECPKGMIN-PILOT** (the built slice — uses the preserved worktrees); **REPR-TRAIN**
(a representative multi-repo train); **OUTSIDE-AGENT-PILOT** (ambiguous-agent admission).
Draft-PR opening is outward — human merges (INV-5). Coordinate with the maintainer before
opening pilot PRs.

**Non-goals**
No production fleet upgrade (that is RELEASE); no auto-merge.

**Key files**
- `specs/phase-plans-specconform-v2.md` (SPECPKGMIN slice as train nodes)
- preserved worktrees: `spec-specconform2`, `governed-pipeline-specpkgmin`, `agent-harness-specpkgmin`

**Depends on**
- FAULTS

**Produces**
- IF-0-PILOT-1 — pilot evidence bundle (converged trains, ambiguous-block metrics) gating RELEASE.

### Phase 6 — Governed Production Release (RELEASE)

**Objective**
Review exact heads through the required cross-vendor board, resolve substantive dissent,
release `phase-loop-runtime`, upgrade the installed `phase-loop` command across the fleet,
remove HEAD-install workarounds, and observe multiple real trains before claiming
production readiness.

**Exit criteria**
- [ ] Required board reviews exact merged heads; substantive dissent resolved.
- [ ] `phase-loop-runtime` released (version/tag/CHANGELOG/RELEASE_PIN lockstep, OIDC
      publish); fleet pin upgraded; HEAD-install workarounds removed.
- [ ] Released package identity == installed command identity == fleet pin.
- [ ] Multiple real trains observed converging post-release before production is claimed.

**Scope notes**
Single lane — this is a serialized governed release checkpoint (release publication is never
parallel). Reuses the release-accounting gate lessons (version lives in `pyproject` AND
`__init__` AND `RELEASE_PIN` AND the outside-agent release-handoff doc AND CHANGELOG).

**Non-goals**
No new features; release + observation only.

**Key files**
- `phase-loop-runtime/pyproject.toml`, `phase-loop-runtime/src/phase_loop_runtime/__init__.py`,
  `RELEASE_PIN`, `CHANGELOG.md`, `docs/releases/outside-agent-release-handoff.md`

**Depends on**
- PILOT

**Produces**
- IF-0-RELEASE-1 — production-ready convergence coordinator.

## Phase Dependency DAG

```
FREEZE ──┬──► RUNTIME ──┐
         └──► BROKER  ──┴──► INTEG ──► FAULTS ──► PILOT ──► RELEASE

Parallel after FREEZE:   RUNTIME ∥ BROKER   (disjoint files; both consume only FREEZE gates)
Serial spine:            FREEZE → {RUNTIME,BROKER} → INTEG → FAULTS → PILOT → RELEASE
Within phases:           FREEZE(3 lanes) · RUNTIME(4 lanes) · BROKER(4 lanes) ·
                         INTEG(4 lanes, single-writer-serialized on train_runner.py) ·
                         FAULTS(5 fault-family lanes) · PILOT(3 pilot lanes) · RELEASE(1 lane)
```

## Execution Notes

- Plan each phase with `/claude-plan-phase <ALIAS>` then execute with
  `/claude-execute-phase <alias>`. RUNTIME and BROKER can be planned + executed
  concurrently once FREEZE lands (they share no file and depend only on the freeze gates).
- **Single-writer rule:** `train_runner.py`, `publishing.py`, and `convergence/broker/**`
  have one writer at a time. INTEG's lanes all touch `train_runner.py` → serialize their
  merges even though the design is parallel. Coordinate against the spec-canonicalization
  track (which should not touch these files).
- **This track runs parallel to the spec-canonicalization domain** (spec-engine / GP
  ingestion / the SPECCONFORM roadmap) on disjoint files. The only coupling is that
  SPECPKGMIN (a canonicalization slice) is the PILOT consumer here — its 3 built worktrees
  are inputs to Phase 5, not re-executed.
- **Prebuilt-publish is folded in** as the broker `publish_committed_branch` verb
  (Phase 2 / BROKER, EVIDENCE+VERBS lanes). The fully-designed, test-green reference lives
  on branch `feat/runtrain-prebuilt-publish` and `.phase-loop/reviews/
  prebuilt-publish-slice-design.md`; re-home its push+PR-create into the broker (its
  read-only preflight/owned-paths/head-sha stay coordinator-side).
- **The panel was explicit:** this roadmap is roadmap-input readiness, NOT implementation /
  pilot / release / production readiness. FAULTS is the hard gate before PILOT; PILOT before
  RELEASE. Do not shortcut the delayed-commit matrix.

## Verification

End-to-end, the roadmap has delivered when:

- `python -m pytest phase-loop-runtime/tests/convergence -q` passes, including the full
  adversarial fault suite (crash / partition / stale-worker / delayed-commit / mixed-version
  / exact-head) and `test_train_invariants.py` INV-1..INV-6.
- A coordinator killed at any state transition resumes idempotently from the event log with
  no transcript (`phase-loop train-status` reconstructs state).
- No stale base / roadmap / dependency / head / review / verification artifact is ever
  accepted; an upstream merge refreshes + re-verifies an already-open downstream PR.
- An accepted provider request with neither terminal effect nor terminal no-effect proof
  leaves the train permanently blocked (never times out into progress).
- The SPECPKGMIN pilot opens 3 coordinated draft PRs via the broker publish-committed path
  with interchange verification intact, stopping at `drafts_open`.
- Released package identity == installed `phase-loop` command identity == fleet pin.
