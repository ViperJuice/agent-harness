# agent-harness — Model Routing v2: Live Governed Loop — Phase Plan v3

> How to use this document: save to `specs/phase-plans-v3.md`, then run `/claude-plan-phase <ALIAS>` to produce the lane-level plan for each phase (→ `plans/phase-plan-v3-<alias>.md`), then `/claude-execute-phase <alias>` to build it.

> Companion design note: `docs/research/model-routing-v2-integration.md` (the runner deep-dive this roadmap implements). All `file:line` anchors below are against that note / the post-model-routing-v1 `main`.

---

## Context

model-routing-v1 (merged) shipped the governed-review machinery — `governed_review`, `governed_premerge`, `panel_invoker`, the `ReviewFinding` severity vocabulary, route logging — **fully unit-tested but not live**. The decisive finding from the integration research: `run_mode` reaches `run_loop` (`runner.py:1105`) and is then **never used**; `governed_premerge_for_run` and `next_escalation` are imported but never called; `panel_invoker._default_spawn` raises `NotImplementedError`. The governed path is a tested island with zero live edges.

This roadmap threads it in. It is **not** a rewrite: it is operator surfacing + three call sites + one real panel-spawn, each a thin additive branch guarded by `run_mode == "governed"` so the autonomous default stays byte-identical.

**The blocking design decision, now resolved — artifact identity.** The panel reviews a **review bundle**, not a bare diff: the staged diff over the phase's owned dirty paths, plus the plan's `## Acceptance Criteria`, the verification-command results, and a one-paragraph closeout summary — rendered to a file in the panel's read-only review dir. `apply_fix` re-dispatches `repair` with the panel's `block` findings folded into `repair_context`, re-renders the bundle from the new staged diff, and returns it. This is frozen as IF-0-P1-1 so every downstream piece (invoker hand-off, fix round, review prompt) builds against a fixed shape.

The other research open questions are resolved in Assumptions/Non-Goals below: a **separate** governed fix-round counter; the **claude leg deferred** (codex+gemini ship, still satisfying reviewer≠author); **serial dispatch only**; real `--governed`/`PHASE_LOOP_RUN_MODE` surfacing; planning gate on **first-attempt plans only**.

---

## Assumptions (fail-loud if wrong)

1. The pre-merge / commit boundary is `_perform_phase_closeout` (`runner.py:1840`, which `git commit`s at `~7418`), reached from the `status == "awaiting_phase_closeout"` branch (`runner.py:1838`). The governed gate hooks immediately before it.
2. The fix round reuses the existing repair re-dispatch verbatim — `_build_repair_context` (`runner.py:5834`) is the trusted-context builder, and `repair_context` (consumed by `build_prompt` at `runner.py:2662`) is the injection vehicle for panel `block` findings. Nothing in the launch machinery changes.
3. The governed fix-round counter is **separate** from `_recent_repeated_repair_failures` (`runner.py:6041`), so a governed re-review fix does not prematurely trip the existing executor-vendor pivot.
4. `run_mode` is currently a programmatic `run_loop` param only; there is no operator entry. P1 adds `--governed` / `PHASE_LOOP_RUN_MODE`.
5. The advisor-panel CLI invocations (codex `exec … --output-last-message`, `agy --print-timeout`) and their auth-signature fail-closed grep are a reliable per-leg spawn basis; subscription-auth only, never API keys.

## Non-Goals

- **No change to the autonomous default.** Every hook is guarded by `run_mode == "governed"` at the caller, so an autonomous run renders no bundle, probes no legs, spawns no panel, and adds no `human_required`.
- **Serial dispatch only.** Concurrent-wave governed coverage (`_dispatch_concurrent_wave`, the worktree merge-back) is explicitly deferred — its diff/fix-round semantics under worktree isolation are a later effort.
- **The native-Agent / Agent-View claude leg is deferred.** P2 ships codex + gemini and returns `unavailable` for claude; the 2-leg pool still satisfies reviewer≠author (claude-authored → codex+gemini; codex/gemini-authored → ≥1 disjoint vendor).
- No API-key auth anywhere; no new providers/executors.
- No rework of the v1 governed machinery beyond wiring + the one real spawn.

---

## Cross-Cutting Principles

1. **Autonomy-first, enforced at the caller.** The `run_mode == "governed"` guard sits *outside* the gate calls, so autonomous runs never even compute a bundle or probe legs — zero cost, byte-identical to today.
2. **Thin additive hooks, no closure refactor.** `_prepare_phase_launch` is a ~1400-line `nonlocal`-bound closure; v2 adds guarded branches that call the already-tested `governed_*` functions. Do not restructure the closure.
3. **Ownership stays coherent for the start-gate.** The governed fix round re-dispatches `repair` (writing the worktree) *inside the same phase's dispatch iteration*, so the cross-phase dirty start-gate (`runner.py:915`) never sees a now-inactive phase holding a lien — the issue-#1 failure mode the `_INACTIVE_DIRTY_OWNER_STATUSES` filter prevents.
4. **Every terminal is non-human.** Governed non-convergence and panel-unavailable-while-failing emit a non-human `review_gate_block` (+ halt + run-end surfacing) via the existing blocked-event pattern (`runner.py:2376-2386`); the human reviews between bounded runs (`--max-phases`), never a synchronous wait.
5. **Fail-loud is the floor.** v1 made `run_mode=governed` warn that it was inert; v2 removes the warning only as each capability becomes genuinely live, and ships `governed` honestly (serial-only, claude-leg-deferred) — never a silent partial.

---

## Phase Dependency DAG

```
  P1  Live pre-merge gate (serial) + run_mode surfacing + artifact-bundle contract
   │
   ▼
  P2  Real panel spawn (codex + gemini, fail-closed; claude deferred)
   │
   ▼
  P3  Planning-stage gate + escalation ladder
   │
   ▼
  P4  End-to-end hardening, invariants & docs
```

(Strictly serial: every phase edits the `runner.py` governed-hook region and the dense dispatch path; the start-gate would refuse overlapping in-flight phases.)

---

## Top Interface-Freeze Gates

These gates are the narrowest contracts that unblock downstream phases. `/claude-plan-phase` concretizes each when it plans the owning phase.

1. **IF-0-P1-1** — Artifact-bundle contract: a renderer producing the review bundle (staged diff over the phase's owned dirty paths + the plan's `## Acceptance Criteria` + verification-command results + a one-paragraph closeout summary), the on-disk staging convention (a file in the panel's read-only review dir, outputs separated), and the `apply_fix` return contract (re-rendered bundle after a repair re-dispatch that folds `block` findings into `repair_context`).
2. **IF-0-P1-2** — Governed pre-merge hook + operator surfacing: the `--governed` / `PHASE_LOOP_RUN_MODE` entry threaded to `run_loop` and onward to `build_phase_loop_closeout`; the hook site at `runner.py:1838` (before `_perform_phase_closeout`) gating execute closeouts only (branch on `closeout_terminal_status`); the separate governed fix-round counter; and the non-human `review_gate_block` terminal emission.

---

## Phases

### Phase 1 — Live Pre-Merge Gate, run_mode Surfacing & Artifact Bundle (P1)

**Objective**
Make governed mode reachable on the serial path: surface `run_mode` from the operator, freeze + implement the artifact-bundle contract, and wire the bounded pre-merge loop before the closeout commit (panel injected/mocked).

**Exit criteria**
- [ ] `run_mode` is plumbed from `--governed` / `PHASE_LOOP_RUN_MODE=governed` through `run_loop` to `build_phase_loop_closeout`; a test asserts the flag/env yields `run_mode="governed"` end to end (today `run_loop` validates then drops it).
- [ ] The artifact-bundle renderer (IF-0-P1-1) produces a bundle containing the staged diff, the plan's acceptance criteria, the verification results, and a summary, staged to a file in a read-only review dir; a test renders from a fixture closeout and asserts all sections present.
- [ ] The governed pre-merge loop is wired at `runner.py:1838` before `_perform_phase_closeout`, gating execute/implementation closeouts only (branch on `closeout_terminal_status`), with an `apply_fix` closure reusing `_build_repair_context`/`build_prompt`/`launch_with_spec`; a governed run with a mock panel returning block-then-pass shows the fix re-dispatched then the phase mergeable.
- [ ] Non-convergence terminates as a non-human `review_gate_block` + halt, surfaced in the run-end summary; a test asserts `human_required=False` and that the phase is **not** committed.
- [ ] Autonomous is zero-cost at the run level: an outer `run_mode=="governed"` guard means an autonomous run renders no bundle and makes no panel-invoker calls; a run-level regression asserts zero invoker calls and unchanged commit behavior.

**Scope notes**
- Decompose into 3 lanes owning disjoint regions: (a) `run_mode` operator surfacing (`cli.py` + the `run_loop`→closeout threading); (b) the artifact-bundle renderer + the `apply_fix` closure (a new module + the runner closure); (c) the pre-merge hook insertion at `runner.py:1838` + the non-human terminal emission. The `runner.py` governed-hook region is the **single integrator lane** owned by this phase.

**Non-goals**
- No planning gate (P3), no real panel spawn (P2) — the panel is injected/mocked here.

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/cli.py
- phase-loop-runtime/src/phase_loop_runtime/runner.py
- phase-loop-runtime/src/phase_loop_runtime/governed_premerge.py
- phase-loop-runtime/src/phase_loop_runtime/governed_bundle.py

**Depends on**
- (none)

**Produces**
- IF-0-P1-1
- IF-0-P1-2

---

### Phase 2 — Real Panel Spawn (codex + gemini, fail-closed) (P2)

**Objective**
Implement `panel_invoker._default_spawn` for real subscription CLI legs so a governed run actually consults frontier reviewers — fail-closed, no live calls in tests.

**Exit criteria**
- [ ] `_default_spawn(leg, artifact)` spawns codex and gemini as per-leg subprocesses with the advisor-panel flags (codex `exec --skip-git-repo-check --sandbox read-only --model gpt-5.5 -c model_reasoning_effort=xhigh --output-last-message <out>`; gemini `agy --model "Gemini 3.1 Pro (High)" --print-timeout`), staging the IF-0-P1-1 bundle as a file in a read-only review dir, and returns `(status, text)`.
- [ ] Fail-closed per leg: an auth/error stderr signature → `degraded`; rc 124 → `timeout`; a too-small body → `empty` — so a verbose auth error is never read as a real review. Tests drive each status via a stubbed subprocess (no live frontier calls).
- [ ] The claude leg returns `unavailable` (deferred); `select_reviewer_pool`/`available_panel_legs` handle the 2-leg pool, preserving reviewer≠author (a test asserts a claude-authored bundle is reviewed by codex+gemini, and a codex-authored bundle by ≥1 disjoint vendor).
- [ ] Subscription-auth only; a test asserts the spawn carries no API-key env var.

**Scope notes**
- Single lane: `panel_invoker._default_spawn` + its mockable subprocess seam + the fail-closed status mapping + tests (stubbed subprocess). Owns `panel_invoker.py`; no `runner.py` change. Justified single lane — one coherent fail-closed spawn boundary.

**Non-goals**
- No claude-leg native-Agent path; no runner changes.

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py
- phase-loop-runtime/src/phase_loop_runtime/capability_registry.py

**Depends on**
- P1

**Produces**
- (none)

---

### Phase 3 — Planning-Stage Gate & Escalation Ladder (P3)

**Objective**
Add the governed planning gate (first-attempt plans only) and bind the `model_class` escalation ladder atop the existing executor pivot.

**Exit criteria**
- [ ] `governed_planning_gate` runs at the planned→execute transition (`runner.py:1956`) in governed mode on **first-attempt plans only** (not repair re-plans), reviewing the plan-doc bundle; a non-promoted result holds the execute dispatch with a non-human `review_gate_block`. A test covers promote / hold / degraded-advisory and that a repair re-plan is not re-reviewed.
- [ ] `next_escalation` is bound at the repair decision point (`runner.py:2334-2339`): a repeated implementer-tier failure escalates `model_class` implementer→planner; a planner-tier repeated failure routes into the panel (governed) or a non-human terminal (autonomous). A test drives the `model_class` ladder atop the existing executor pivot, with the governed fix-round counter kept separate from `_recent_repeated_repair_failures`.
- [ ] Autonomous unaffected (no planning panel, no `human_required`) — regression.

**Scope notes**
- Decompose into 2 lanes: (a) the planning-gate hook at `runner.py:1956` + the first-attempt guard; (b) the `next_escalation` binding at the repair-pivot decision + the separate fix-round counter. Both touch the `runner.py` integrator region; sequence after P1/P2.

**Non-goals**
- No concurrent-wave coverage.

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/runner.py
- phase-loop-runtime/src/phase_loop_runtime/governed_premerge.py

**Depends on**
- P2

**Produces**
- (none)

---

### Phase 4 — End-to-End Hardening, Invariants & Docs (P4)

**Objective**
Prove the full governed loop end-to-end (mocked panel), lock invariants, and document governed mode as live (serial only) — the terminal phase.

**Exit criteria**
- [ ] An end-to-end governed-run test (mocked panel) exercises plan-gate → execute → pre-merge review → fix round → mergeable, plus the non-convergence terminal, on the serial path.
- [ ] CI invariants: a governed serial run gates pre-merge; an autonomous run spawns zero panel legs **at the run level**; the governed fix-round counter is independent of the repair pivot; gemini/pi are never the max-effort planner of record.
- [ ] Docs updated: `protocol.md` (governed `run_mode` now live, serial-only; concurrent waves out of scope), the plan/execute skill overrides, README, and a CHANGELOG `model-routing-v2` entry.
- [ ] `phase-loop validate-roadmap specs/phase-plans-v3.md` passes and the full standalone suite is green.

**Scope notes**
- Single lane: the end-to-end governed test + the invariant suite + the docs/contract/skill sweep, landed atomically. Sequenced after P1–P3.

**Non-goals**
- No new enforcement; P4 verifies and documents what P1–P3 built.

**Key files**
- phase-loop-runtime/tests/
- phase-loop-runtime/src/phase_loop_runtime/_contract_docs/phase-loop/protocol.md
- README.md
- CHANGELOG.md

**Depends on**
- P3

**Produces**
- (none)

---

## Execution Notes

- **Planning**: `/claude-plan-phase <ALIAS>` for each phase. The spine is strictly serial — `P1 → P2 → P3 → P4` — because every phase edits the `runner.py` governed-hook region / dense dispatch path, and the cross-phase dirty start-gate (`runner.py:915`) would refuse overlapping in-flight phases.
- **Execution**: `/claude-execute-phase <alias>` after each plan is approved.
- **Critical path**: `P1 → P2 → P3 → P4` — no parallel branch at the phase level.
- **Single-writer / integrator-lane files**: `runner.py` and `governed_premerge.py` are the integrator region across P1/P3 (and read by P2/P4) — one lane owns them, merges sequenced P1→P3; `panel_invoker.py` is P2-only.

---

## Acceptance Criteria

- [ ] **Governed mode is live on the serial path:** `phase-loop run --governed` gates planning and pre-merge through a real codex+gemini panel, runs a bounded fix loop, and terminates non-convergence as a non-human `review_gate_block` surfaced in the run-end summary.
- [ ] **Autonomy is byte-identical:** an autonomous run renders no bundle, spawns no panel leg, and commits exactly as before — a run-level regression proves zero panel-invoker calls.
- [ ] **The fix round reuses repair:** a `block` finding folds into `repair_context` and re-dispatches the existing repair path; the re-rendered bundle is what the panel re-reviews.
- [ ] **Reviewer ≠ author holds with the 2-leg panel**, and the claude-leg-deferred / serial-only / first-attempt-plan scoping is documented, not silent.
- [ ] `phase-loop validate-roadmap specs/phase-plans-v3.md` passes and the full standalone suite is green.

---

## Verification

```bash
# Roadmap lints clean
PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v3.md

# run_mode surfacing + artifact bundle + live pre-merge loop (after P1)
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/ -k "run_mode or bundle or premerge or governed" -q

# Real panel spawn fail-closed (after P2)
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/ -k "panel_invoker or spawn or default_spawn" -q

# Planning gate + escalation ladder (after P3)
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/ -k "planning_gate or escalation" -q

# Full standalone suite (after P4)
cd phase-loop-runtime && PYTHONPATH=src python -m pytest -q
```
