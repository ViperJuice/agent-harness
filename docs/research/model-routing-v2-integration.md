# model-routing-v2 — Integration Design: Threading the Governed Pre-Merge Loop into the Live Executor Fix-Apply Cycle

> Research note produced from a deep read of the live runner. It is the design basis for the
> model-routing-v2 roadmap. All `file:line` anchors are against
> `phase-loop-runtime/src/phase_loop_runtime/runner.py` (on the `feat/model-routing-v1` branch)
> unless another file is named. v1 shipped the governed machinery fully unit-tested but **not
> live-threaded**; v2 wires it in.

## 0. Headline finding: `run_mode` enters the runner and dies

`run_mode` is a `run_loop` parameter (`runner.py:1105`), validated at `runner.py:1111-1112`, and then **never used again**. `governed_premerge_for_run` (`runner.py:7742`) and `next_escalation` (imported `runner.py:170`) are both **defined/imported but never called**. The v1 work is a fully-built, unit-tested island with zero live edges into the dispatch loop. v2 is entirely about adding **three call sites and one real-spawn implementation** — no rewrite of existing machinery.

---

## 1. Dispatch-path map (call graph with anchors)

The dispatch loop is built from nested closures inside `run_loop`. The serial path:

```
run_loop  (runner.py:~1080, run_mode param @1105, validated @1111)
 └─ _dispatch_phase()                                    runner.py:3908
     ├─ _prepare_phase_launch()                          runner.py:1417   (returns (_DispatchOutcome|None, _DispatchPrep|None))
     │   ├─ stop/stuck/start-gate guards                 runner.py:1419-1486, 1505
     │   ├─ _cross_phase_dirty_start_gate(...)           runner.py:1505  (def @922)
     │   ├─ status=="awaiting_phase_closeout" branch ───► _perform_phase_closeout(...)  runner.py:1838→1840  (def @7197)
     │   │        └─ git add @7397 / git commit @7418  ◄── THE PRE-MERGE / COMMIT BOUNDARY
     │   ├─ launch_action resolution                     runner.py:1855-1957
     │   │    • "repair" set @1951 (after _build_repair_context @1907, def @5834)
     │   │    • "plan"/"execute" set @1954-1956  (planned→execute transition @1956)
     │   ├─ prompt_profile = repair|execute|plan         runner.py:2187-2192
     │   ├─ repair-loop pivot (executor swap)            runner.py:2334-2374
     │   ├─ build_prompt(..., repair_context=...)        runner.py:2662  (from .prompts @165)
     │   ├─ build_launch_request(...)                    runner.py:2695
     │   ├─ build_launch_spec(request)                   runner.py:2710  (from @102)
     │   └─ returns _DispatchPrep(spec=..., ...)         runner.py:2857-2872  (NamedTuple @28)
     ├─ launch_with_spec(prep.spec, ...)                 runner.py:3912  (EXECUTOR LAUNCH, from @104)
     └─ _finalize_phase_launch(prep, result)             runner.py:3923  (def @2874; closeout classification @4163)
```

Concurrent path (`_dispatch_concurrent_wave`, `runner.py:3925`): reuses the **same** `_prepare_phase_launch` (`runner.py:3997`) and `prep.spec`, but launches via `PhaseWorkerJob` (`runner.py:4010`) in isolated worktrees through `run_phase_worker_pool`, then merges back. There is also a standalone `launch_with_spec` at `runner.py:4454` and `runner.py:4990` (delegated-child / single-shot paths) — secondary, not the main loop.

**Verification → closeout → commit:** the executor's result is classified in `_finalize_phase_launch`; a verified phase emits `status="awaiting_phase_closeout"` (`runner.py:4095, 4163`), which on the *next* dispatch pass hits the `runner.py:1838` branch and calls `_perform_phase_closeout` (`runner.py:1840`). Inside it, the verified-owned branch (`runner.py:7385` `else`) does `git add` (`7397`) then `git commit` (`7418`), producing the commit SHA at `7427`. **This commit is the merge.** The governed pre-merge gate must run *before* `runner.py:1840`.

---

## 2. The fix-re-dispatch mechanism (how a governed "fix round" re-invokes the executor)

The existing repair re-dispatch — the exact analog the governed loop must reuse — is constructed in `_prepare_phase_launch`:

1. `launch_action = "repair"` (`runner.py:1951`), gated by `repair_precondition_for_snapshot` (`runner.py:1889`, def `5878`).
2. `repair_context, repair_missing = _build_repair_context(repo, alias, plan, snapshot)` (`runner.py:1907`, def `5834`) — assembles the trusted dict: `terminal_summary`, `dirty_paths`, `phase_owned_dirty_paths`, `closeout_summary`, `artifact_paths`, recovery commands (`runner.py:5858-5874`). Returns `None` if trusted context is incomplete (fail-closed).
3. `prompt_profile = "repair"` (`runner.py:2190`).
4. `build_prompt(launch_action, ..., repair_context=repair_context, ...)` (`runner.py:2662-2672`) — **`repair_context` is the injection vehicle for findings**.
5. `build_launch_request(...)` (`runner.py:2695`) → `build_launch_spec(request)` (`runner.py:2710`) → launched at `runner.py:3912`.
6. Executor-class/model selection: `resolve_dispatch_decision(...)` (`runner.py:2324`) plus the **repair-loop pivot** (`runner.py:2334-2374`) which, on `_recent_repeated_repair_failures(...) >= 2` (`runner.py:2339`, def `6041`), swaps to a fallback executor or emits a non-human `repeated_verification_failure` block (`runner.py:2376-2386`).

**The v2 fix round (the crux):** `run_governed_premerge_loop`'s `apply_fix: Callable[[int, str, findings], str]` (`governed_premerge.py:118, 177`) is a **pure seam**, but the machinery above lives in the `nonlocal`-bound closure `_prepare_phase_launch`. Therefore the governed loop **must be invoked from inside the dispatch loop**, and `apply_fix` must be a **closure that captures** `repo/roadmap/alias/plan/snapshot/selection/dispatch_decision`. On each call it:
- builds a `repair_context` via `_build_repair_context` (`5834`) **augmented with the panel `findings`** (the `block` reasons become repair instructions),
- forces `launch_action="repair"` / `prompt_profile="repair"`,
- calls `build_prompt`(`2662`)→`build_launch_request`(`2695`)→`build_launch_spec`(`2710`)→`launch_with_spec`(`3912`),
- re-reads the resulting closeout/diff and **returns the new artifact string** for re-review.

This reuses the *entire* existing repair path verbatim; the only new thing is folding panel `block` findings into `repair_context`. The bound `max_rounds=3` (`governed_premerge.py:34`) is independent of (and additive to) the existing repair-pivot cap.

**`next_escalation` binding (the model-class ladder on top of the existing pivot):** today `repair_loop_pivot` (`runner.py:2334-2374`) escalates the **executor (vendor)**. `next_escalation` (`governed_premerge.py:59`) adds the orthogonal **model_class** ladder `implementer→planner→[governed: invoke_panel | autonomous: non-human terminal]`. v2 should call it at the same decision point (`runner.py:2334-2339`), feeding `failed_tests/patch_retries` from `_recent_repeated_repair_failures` / the verification history, so a planner-tier repeated failure in governed mode routes into the panel rather than only swapping vendors.

---

## 3. The two integration points

### (a) Planning-stage gate — `governed_planning_gate`

**Hook site:** the planned→execute transition at `runner.py:1956` (`elif status == "planned" and plan is not None:`), *before* `launch_action` becomes `"execute"`. Wrap with `if run_mode == "governed":` and call `governed_planning_gate(artifact=<plan doc text>, author_executor=<planner executor>, run_mode="governed", available_legs=...)` (`governed_review.py:110`). A non-promoted result holds the execute dispatch and emits a non-human `review_gate_block` (mirror the blocked-event pattern at `runner.py:2376-2386`); `degraded` passes advisory (autonomy-first, `governed_review.py:131-146`).

### (b) Pre-merge gate — the bounded loop before commit

**Hook site:** the `runner.py:1838` branch (`status == "awaiting_phase_closeout"`), immediately **before** `_perform_phase_closeout` at `runner.py:1840`. Guard:

```python
if run_mode == "governed" and <this is an execute/implementation closeout, not a plan-doc closeout>:
    loop = governed_premerge_for_run(
        artifact=<phase diff over closeout dirty paths>,
        author_executor=<executor that produced the work>,
        run_mode="governed",
        apply_fix=<closure capturing dispatch context, §2>,
        available_legs=available_panel_legs(),  # panel_invoker:51
    )
    if not loop.mergeable:
        # emit loop.terminal_blocker (human_required=False) via the blocked-event pattern @2376-2386
        return (_DispatchOutcome("break", None), None)
# else fall through to _perform_phase_closeout @1840 → commit @7418
```

**Critical branch (per advisor):** `_perform_phase_closeout` commits **both** plan-doc closeouts (`terminal_status=="planned"` → `status="planned"`, `runner.py:7428`) *and* execute work. The pre-merge gate must branch on `launch_action`/`snapshot.closeout_terminal_status` so it reviews **implementation diffs only**, never plan-doc closeouts (those are the planning gate's job, §3a). Use `snapshot.closeout_terminal_status` (`runner.py:7208`) to distinguish.

The same hook must also be added to the concurrent path's merge-back (`_dispatch_concurrent_wave`, around the parent-closeout merge, `runner.py:3925+`) if governed mode is to cover concurrent waves — recommend deferring this to a later v2 phase (see §6).

---

## 4. `_default_spawn` wiring plan (real 3-leg, fail-closed)

`panel_invoker._default_spawn` (`panel_invoker.py`) is the real per-leg execution boundary: `spawn(leg, artifact) -> (status, text)`, with `PANEL_LEGS = ("codex","gemini","claude")`.

**PNLFOUND model-routing-v3 amendment (2026-06-30).** The owned panel contract now canonicalizes leg statuses as uppercase `OK`, `EMPTY`, `TIMEOUT`, `ERROR`, `DEGRADED`, and `UNAVAILABLE`, while normalizing legacy lowercase spawn output at the runtime boundary. The contract also records an input-scaled per-leg timeout policy capped at 1800 seconds. Claude's Sonnet-family implementer route is `claude-sonnet-5`; Gemini implementer/worker routes use `Gemini 3.5 Flash (High)`, while Gemini remains capped at high effort and is still not eligible as the max-effort planner of record.

**PNLFEED prompt-feeding amendment (2026-06-30).** Codex and Gemini panel legs now receive a compact prompt that points to staged `review-instructions.md` and `review-bundle.md` files in the temp review directory. The review artifact body is not embedded in argv or stdin; large artifacts are represented in the prompt by deterministic digest and size metadata so panelists can inspect the staged Markdown file themselves.

**PNLCLAUDE TUI amendment (2026-07-01).** The Claude panel leg now uses a local Claude Code TUI session with `claude-sonnet-5`, max effort, and Claude Max subscription auth, gated on Claude Code `2.1.197` or newer before launch. Claude writes its full review to canonical scratch output file `panel-claude.txt`; missing, nonconforming, timed-out, or below-minimum Claude Code states return `UNAVAILABLE`, `TIMEOUT`, or `DEGRADED` leg evidence rather than silently falling back to an older Sonnet path. The implementation keeps provider API keys stripped from the child environment and does not use unsupported headless `claude -p` behavior.

**Contract mismatch to resolve:** `~/.claude/skills/advisor-panel/scripts/run_cli_panels.sh` runs codex + gemini **together** in one invocation and **deliberately excludes the claude leg** ("the claude leg is the orchestrator's job"). So the script cannot be shelled wholesale into a per-leg seam. Wiring plan:

- **codex / gemini legs** — replicate the script's per-leg subprocess approach (do not call the whole script):
  - codex: `timeout -k 15s <T>s codex exec --cd <dir> --skip-git-repo-check --sandbox read-only --model gpt-5.5 -c model_reasoning_effort=xhigh --output-last-message <out> -` with a compact file-reference prompt on stdin. Clean review text comes from `--output-last-message`; raw stdout is a noisy transcript.
  - gemini: `cd <dir> && timeout -k 15s <T+60>s agy --model "Gemini 3.1 Pro (High)" --print-timeout <T>s -p -` with the same compact file-reference prompt on stdin. Stdout is the clean response; `--add-dir` is not the artifact-feeding mechanism.
  - **Auth/error fail-closed:** replicate the script's stderr-signature grep (`not logged in|please run .*login|unauthorized|invalid api key|usage limit ...`) → return `status="DEGRADED"` (or `"TIMEOUT"` on rc 124), and `status="EMPTY"` when the body has no review text. This is what stops a verbose auth error being mistaken for a real review — and matches `invoke_panel`'s fail-closed translation (`panel_invoker.py:91-97`) and `governed_review._findings_from_panel` (`governed_review.py:77-107`).
  - Subscription-auth only (ChatGPT login / Google token); **never API keys** — per the script header.
- **claude leg** — a **separate** path, not the CLI script: local Claude Code TUI with `claude-sonnet-5` and max effort, since `claude -p` is being deprecated for subscription use and Agent View is prone to background PTY reaping on this host. The TUI starts in the isolated output dir with only `Read` and `Write` tools, reads the staged review files, and writes the full review to `panel-claude.txt`. Unsupported Claude Code versions, missing CLI, timed-out launches, or nonconforming output classify as `UNAVAILABLE`, `TIMEOUT`, or `DEGRADED`, which `select_reviewer_pool` and the panel fail-closed path already handle.
- **Artifact handoff:** `_default_spawn` writes `artifact` + a `review-instructions.md` into a temp review dir (read-only to panelists), sends only a compact pointer prompt to each leg, and reads each leg's clean output back from deterministic files in the separate output dir (`panel-codex.txt`, `panel-gemini.txt`, `panel-claude.txt`).

Net: `_default_spawn(leg, artifact)` is a dispatcher — `codex`/`gemini` use per-leg subprocesses with the script's flags, timeouts, and auth-grep; `claude` uses the local Claude Code TUI path. `invoke_panel` already isolates each leg in try/except, so one broken leg degrades, never crashes the gate.

---

## 5. Risks & invariants

1. **Cross-phase dirty start-gate (`runner.py:922`, called `1505`).** It scans the last 50 events for a prior in-flight phase holding a dirty-path lien on the current tree (`runner.py:937-977`) and refuses dispatch (`runner.py:1506`). The governed fix round re-dispatches `repair`, which **writes to the worktree between rounds**. Invariant a v2 change must not break: the fix-round re-dispatch must leave the tree owned by the *current* phase (it already is — repair operates on the same phase's owned paths). Do **not** let an aborted fix round leave dirty paths attributed to a now-inactive phase, or the next phase's start-gate refuses with no recovery path (the issue-#1 failure mode the `_INACTIVE_DIRTY_OWNER_STATUSES` filter at `runner.py:953` prevents). Keep the governed loop *inside* the same phase's dispatch iteration so ownership stays coherent.

2. **Autonomous no-op must stay zero-cost.** Two layers already guarantee no panel spawn in autonomous (`governed_review.py:125` short-circuits before pool selection; `governed_premerge.py:130` returns `ran=False`). v2 must add a **third, outer guard at the caller**: `if run_mode == "governed":` around both hook sites (§3) so that in autonomous mode the runner does **not even compute the diff** or call `available_panel_legs()`. The default path stays byte-identical to today.

3. **Non-human terminal.** Every governed terminal (`LoopResult.terminal_blocker`, `EscalationDecision.blocker`) sets `human_required=False` (`governed_premerge.py:40-47`). v2 must emit these via the **existing blocked-event pattern** (mirror `runner.py:2376-2386` / the `repeated_verification_failure` blocks) and must never promote them to `human_required`. The blocker classes are `review_gate_block` and `repeated_verification_failure` — both already in the runner's vocabulary (`runner.py:2379, 3168, 6711`).

4. **Dense-loop fragility.** `_prepare_phase_launch` is a ~1400-line closure with ~6 `nonlocal`s (`runner.py:1418`) and many early-return control points (`_DispatchOutcome("break"/"continue"/"fall")`). Mutating its body is high-risk. **Mitigation:** add the governed hooks as *thin, additive* branches guarded by `run_mode=="governed"`, calling out to the already-tested `governed_*` functions — do not refactor the closure. The pre-merge hook lives at `runner.py:1838` (before `1840`), a clean insertion point; the planning hook at `runner.py:1956`. Keep the `apply_fix` closure small and side-effect-localized to repair re-dispatch.

5. **Artifact identity is undefined (also §7).** `run_governed_premerge_loop(artifact: str)` — the design assumes this is the phase diff over `closeout_dirty_paths` (`runner.py:7218-7220`) at the pre-merge gate, and the plan-doc text at the planning gate. `apply_fix` returns the *new* diff after repair. This must be pinned before implementation because it determines what the panel sees.

---

## 6. Proposed v2 phase breakdown

**Phase v2-P1 — Live the pre-merge gate (serial path), panel still mocked.**
Insert the `run_mode=="governed"` hook at `runner.py:1838` before `_perform_phase_closeout` (`1840`); build the `apply_fix` closure reusing `_build_repair_context`(`5834`)/`build_prompt`(`2662`)/`launch_with_spec`(`3912`); emit `LoopResult.terminal_blocker` via the blocked-event pattern (`2376-2386`). Branch on `closeout_terminal_status` to gate **execute** closeouts only. Wire with an injected mock `invoke`/`spawn`. Touches: `runner.py`, calls `governed_premerge.run_governed_premerge_loop`.

**Phase v2-P2 — Real panel spawn (fail-closed, 2 CLI legs + Claude TUI path).**
`panel_invoker._default_spawn` — per-leg Codex/Gemini subprocesses with the `run_cli_panels.sh` flags, timeouts, and auth-signature grep; Claude leg through local Claude Code TUI and canonical scratch output file. No runner change. Reconcile `PANEL_LEGS` (3) vs script (2).

**Phase v2-P3 — Planning-stage gate + escalation ladder.**
Hook `governed_planning_gate` at `runner.py:1956`; bind `next_escalation` (`governed_premerge.py:59`) into the repair-pivot decision (`runner.py:2334-2339`) so model_class escalation (implementer→planner→panel) sits atop the existing executor pivot.

**Phase v2-P4 (optional) — Concurrent-wave coverage.**
`_dispatch_concurrent_wave` (`3925`) merge-back path; apply the same pre-merge gate at the parent-closeout merge. Deferred because worktree isolation + `run_phase_worker_pool` make the diff/fix-round semantics more complex.

---

## 7. Open questions for the human

1. **Artifact identity (blocking):** What exactly is the `artifact` string at the pre-merge gate — the staged diff over `closeout_dirty_paths`, a rendered review bundle, or a path? And what does `apply_fix` return after a repair re-dispatch (new diff vs. updated bundle)? Determines what the panel reviews.
2. **Fix-round vs. repair-pivot cap interaction:** the governed loop has `max_rounds=3` (`governed_premerge.py:34`); the repair-loop pivot triggers at `>=2` repeated failures (`runner.py:2339`). Should a governed fix round count toward `_recent_repeated_repair_failures` (and thus trigger an executor pivot mid-loop), or use a separate counter? They currently share the repair path.
3. **Claude leg:** the current implementation uses the Claude Code TUI plus `panel-claude.txt` scratch output because real Agent View session data showed background PTY reaping; future work can revisit Agent View if that lifecycle stabilizes.
4. **Concurrent waves:** is governed mode in scope for concurrent dispatch (P4), or constrained to serial dispatch in v2?
5. **`run_mode` surfacing:** `run_mode` reaches `run_loop` (`1105`) — is it plumbed from the CLI/env (`resolve_run_mode` / `PHASE_LOOP_RUN_MODE`, `governed_review.py:47-52`) for real, or only via the programmatic API today? Confirm the operator entry point.
6. **Planning-gate scope:** should the planning gate review every planned→execute transition (`1956`), or only first-attempt plans (not repair re-plans), to avoid double-reviewing on repair cycles?
