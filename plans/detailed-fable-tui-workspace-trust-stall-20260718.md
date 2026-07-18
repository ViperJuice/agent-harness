# Detailed plan: fix Fable/Claude advisor-board TUI leg silent stall (workspace-trust gate)

## Task
Fix the Fable/Claude advisor-board TUI reviewer leg that stalls silently and is
misclassified (Consiliency/agent-harness#196 + #223, one work item). Reproduced root cause:
`_run_claude_tui_session` submits the review prompt after a fixed `_CLAUDE_TUI_SUBMIT_DELAY_S
= 8s` without detecting that Claude Code first shows a workspace-trust dialog for the fresh
scratch `cwd`. The bracket-paste + Enter lands the review prompt in the `y/n` field → no
reviewer session → the #188 liveness monitor reclaims after 180s and mislabels it
`claude_tui_stalled`. This is why the Fable correctness seat has been effectively dead
(running as native-Opus) on our panels.

## Status: v2 design of record (recon-grounded). v1 was paneled → DISSENT; superseded.

- **v1 (markers + maybe-sidestep)** — paneled 2026-07-18: **codex DISAGREE, gemini
  DISAGREE, Fable PARTIALLY AGREE**. See "Panel review" below.
- **Repro-host recon on `ai`** (the panel's unanimous gating ask) — done 2026-07-18;
  see "Recon findings". It resolved the empirical unknowns and **shrank the design**.
- **v2** — recon-grounded design; re-paneled 2026-07-18: **Fable AGREE, codex/gemini
  DISAGREE** on concrete refinements (not directional).
- **v3 = v2 Changes + the "Panel review v2 REFINEMENTS" (R1–R6) below, which override the
  matching v2 bullets.** This is the design of record to implement. Change set grows to
  `panel_invoker.py` + `agent_runtime_provider.py` (R3 detail contract) + the test file +
  CHANGELOG.

## Panel review v2 (2026-07-18) — REFINEMENTS folded into v3 (implement THIS set)
v2 re-panel: **Fable AGREE, codex DISAGREE, gemini DISAGREE.** The dissents are concrete
implementation defects (not directional); all fixes below are agreed and OVERRIDE the
matching v2 Changes bullets. Two would have been real bugs.

- **R1 — Multi-line modal match (gemini, blocking).** The captured modal spans SEPARATE
  lines (header / path / "Enter y/n:"), so a per-line conjunction over the complete-line
  stream NEVER matches → detector never fires → false `trust_blocked` every run. **Evaluate
  the conjunction against the ACCUMULATED de-ANSI'd buffer (or a sliding window of the last
  N lines), not a single line.** Also pin an explicit wide PTY winsize (e.g. 200×50) so the
  path line does not wrap. Tests must replay a captured modal through the SAME complete-line
  pipeline the leg uses.
- **R2 — Quiescence is not positive readiness (Fable + codex, blocking).** The
  TRUST_MODAL→WAITING_FOR_EDITOR transition and the no-modal readiness deadline are
  under-defined; complete silence can satisfy 2s quiet at the 8s (spawn-anchored) floor, and
  a post-answer editor burst delayed >2s recreates premature submission → paste into an
  unmounted editor → silent garbled/empty prompt (the non-fail-closed hazard). **Arm the
  quiescence timer only AFTER ≥1 novel de-ANSI'd content event FOLLOWING the trust answer
  (and, on the no-modal path, after the first novel output post-spawn).** Define the observable
  modal-cleared/editor transition; define a no-modal readiness deadline that precedes the
  180s stall. Test zero-output and delayed-post-answer-burst.
- **R3 — `detail` propagation contract (codex, confirmed).** `SpawnResult = tuple[str,str]`
  (`agent_runtime_provider.py:225`), `_exec_claude_tui_leg` returns `(status,text)`, and
  `_run_seat` builds `PanelLegResult` at `panel_invoker.py:2978` from `(status,text)` — the
  diagnostic is discarded before construction. Widening `_run_claude_tui_session` alone is
  INSUFFICIENT. **Freeze a typed detail-propagation path: `_run_claude_tui_session` →
  `_exec_claude_tui_leg` → the claude spawn route → `_run_seat`'s `PanelLegResult.detail`.**
  Prefer widening `SpawnResult`/the claude spawn return to carry an optional `detail` over an
  out-of-band channel. Add a test that `detail` reaches the FINAL `invoke_board` result, not
  just the session return. (Adds `agent_runtime_provider.py` to the change set.)
- **R4 — Tail sanitizer order (codex + Fable).** `_redacted_stderr_excerpt(...,max_chars=200)`
  keeps the buffer BEGINNING; slicing the tail BEFORE redaction can expose a secret whose key
  lies before the cut, and the `_ANSI_*` regexes cover CSI/OSC only. **Strip ALL control seqs
  → redact the COMPLETE text → THEN keep the final ≤200 chars.** Test boundary-spanning
  secrets + residual control chars.
- **R5 — Path-scoping (codex vs gemini/Fable SPLIT → reconciled).** codex: exact full
  `out_dir` match is the correct safety scope, basename-only is unsafe. gemini + Fable: full
  path is fragile (line-wrap at default width, `/private/tmp` on macOS, multi-line) → false
  misses. **Resolution:** the pre-submit-only guard is the actual security boundary (all
  three agree it is airtight — pre-submit, only Claude's own startup output is on the stream),
  so the path term's job is disambiguation, not security. Require the conjunction (header AND
  choice AND the run-unique out_dir token) matched over the accumulated buffer, where the
  token is the **harness-generated unique basename** (NOT attacker-derived — see R6 invariant),
  with the wide winsize (R1) keeping the full path un-wrapped as corroboration. This is robust
  (survives wrap) AND safe (unique harness token + pre-submit guard).
- **R6 — Hardening (Fable).** Flip state to SUBMITTED and DISARM the detector BEFORE writing
  the first paste byte (the paste itself contains the trigger strings; local echo could
  re-trigger mid-write). Make "write `y` once" a session-lifetime latch. Widen
  `claude_tui_editor_not_ready` to also cover "modal answered but quiescence never achieved"
  (today it only covers the no-modal path). Document the invariant: **out_dir names are
  harness-generated, never derived from PR/branch/title content.**

## Recon findings (host `ai`, Claude Code 2.1.208, real self-PTY capture 2026-07-18)
Captured raw `terminal_bytes` from real headless `claude` launches (production argv) in
fresh scratch dirs. Dumps + method: `/tmp/fable_trust_recon*` on `ai`;
`scratchpad/fable_trust_recon.py`.

1. **The trust gate is CWD-keyed.** The modal reads verbatim:
   `Permission Required: Accessing workspace:` / `<cwd path>` /
   `Is this a project you created or one you trust?` / `y. Yes, I trust this folder` /
   `n. No, exit` / `Enter y/n:`. With `cwd=stableA` and `--add-dir=freshB`, the modal named
   **`stableA` (the cwd)**, never the `--add-dir`. **`--add-dir` triggers NO trust modal.**
2. **Today's production `cwd=out_dir` (under `/tmp`) hits ONLY the trust modal.** A
   `/tmp` scratch cwd shows the trust modal then goes straight to the editor — **no MCP
   prompt**. (Production sets `cwd=out_dir` deliberately — a `_claude_tui_command` comment
   notes it dodges a `Write` path-scoping prompt.)
3. **Moving cwd into `$HOME` REINTRODUCES a second gate.** With a `$HOME`-subtree cwd,
   after trust Claude shows an **MCP-server enable prompt** ("2 new MCP servers found in
   this project … Enter selections …") **even under `--safe-mode --strict-mcp-config`**.
   → the "sidestep by moving cwd to a stable $HOME dir" idea is REJECTED: it trades one
   modal for two and risks the Write prompt. **Keep `cwd=out_dir`.**
4. **Trust persistence is unreliable for `/tmp`.** Accepting trust for a `$HOME` dir
   persisted to `~/.claude.json` `projects[<dir>].hasTrustDialogAccepted=true`; `/tmp`
   dirs were never recorded. So we cannot "pre-trust" a `/tmp` cwd via config; the modal
   must be handled **in-session** on the `cwd=out_dir` path. (Also: do NOT hand-write
   `~/.claude.json` from panel code — racy/version-fragile, per panel.)
5. **Readiness is observable but content-y; use quiescence.** After answering `y`, the
   editor renders a welcome burst (`Claude Code v2.1.208` … `manual mode on` … input
   prompt) then goes quiet. There is no stable, version-robust "ready marker" string
   (release-notes/promo text varies), confirming the panel: gate submit on **quiescence
   after the modal clears**, not on a content substring.
6. **Answering `y` when NO modal is present is harmful.** In an already-ready editor, a
   stray `y` becomes a chat message ("'y' came in as the first message"). → answering must
   be strictly **detection-gated** (only when the trust modal is positively identified).

## Panel review 2026-07-18 (v1) — DISSENT, required changes (all folded into v2)
codex DISAGREE, gemini DISAGREE, Fable PARTIALLY AGREE. Consensus (now satisfied by v2):
(1) **auto-answer PRE-SUBMIT ONLY** — the scanner must hard-disable at submit, or a
reviewed PR diff / review output / a destructive-tool confirm containing "enter y/n" could
get an unintended `y` (this leg reviews agent-harness PRs about *this code* — a real
self-referential + prompt-injection hazard); (2) **path-scope** the trust match to the
exact `out_dir`; (3) submission needs **positive readiness**, not absence-of-modal (the 8s
fallback races a late modal); (4) **drop brittle ready-markers** for a state machine +
quiescence; (5) trust/readiness **deadline must precede the 180s stall**, distinct typed
reasons; (6) evidence tail must be **control-sanitized + tail-end-bounded**; (7) verify on
the repro host (done); (8) add adversarial tests incl. the post-submit false-positive test.

## Research summary
`_run_claude_tui_session` (`panel_invoker.py`, ~:1360-1531) returns `(rc, review_text,
log_text)`; submit is purely time-gated at `_CLAUDE_TUI_SUBMIT_DELAY_S=8.0` (~:1472) — no
readiness/PTY-screen scan except the heartbeat novelty path (`_tui_take_complete_lines` /
`_tui_chunk_has_novel_content` / `_normalize_tui_line`), whose de-ANSI'd complete-line
stream is computed each chunk (~:1443) — the hook to extend. #188 liveness is
heartbeat-extinction (`_LEG_STALL_THRESHOLD_S=180`, ~:118); `last_heartbeat` (~:1390)
resets on novel PTY text / review-file growth / transcript growth; **deliberately no CPU
heartbeat** (~:1504-1508). Any dialog answer MUST reset `last_heartbeat` (~:1445) or it
self-trips the stall. `terminal_bytes` (~:1386/1434) accumulates all PTY bytes but is
discarded at return (~:1524-1531); `PanelLegResult.detail` (frozen dataclass ~:186-216) is
the home for a tail; reuse `runner._redacted_stderr_excerpt` (`runner.py:8773`). The fix is
confined to the self-PTY branch (entered only when NOT `_under_claude_code`) and cannot
touch the native-adapter/#183 path (`native_agent_leg_request`, live caller ~:2986).
`_exec_claude_tui_leg` (~:1801-1885) maps `claude_tui_stalled`→DEGRADED at ~:1883.

## Changes (v2 — design of record)
All in `panel_invoker.py` unless noted. Cited lines are from recon — re-anchor by symbol.
Keep `cwd=out_dir` (recon finding 2/3). No new `LEG_STATUSES` value (reuse `DEGRADED`).

### `phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py` (modify)
- **New constants** (near `:132`): `_CLAUDE_TUI_TRUST_HEADER = "permission required: accessing
  workspace"`; `_CLAUDE_TUI_TRUST_CHOICE = "trust this folder"`; `_CLAUDE_TUI_TRUST_PROMPT =
  "enter y/n"`; `_CLAUDE_TUI_TRUST_REJECT = "please answer y or n"`; `_CLAUDE_TUI_TRUST_ANSWER
  = b"y\r"`; `_CLAUDE_TUI_READY_QUIESCENCE_S = 2.0` (no novel de-ANSI'd content for this long
  ⇒ editor ready); `_CLAUDE_TUI_TRUST_DEADLINE_S = 45` (< 180 stall; trust-not-cleared
  bound); `_CLAUDE_TUI_TRUST_BLOCKED_MARKER = "claude_tui_workspace_trust_blocked"`;
  `_CLAUDE_TUI_NOT_READY_MARKER = "claude_tui_editor_not_ready"` (readiness never reached
  with NO modal observed — distinct from trust-blocked).
- **`_run_claude_tui_session` — startup state machine.** Replace the fixed-delay submit
  (`:1472`) with an explicit `STARTING → TRUST_MODAL → WAITING_FOR_EDITOR → SUBMITTED` over
  the existing de-ANSI'd complete-line stream (`:1443`). Track `state`, `trust_answered:
  bool`, `last_novel_monotonic`.
  - **Trust detection (path-scoped, once, pre-submit):** enter `TRUST_MODAL` only on the
    CONJUNCTION — `_CLAUDE_TUI_TRUST_HEADER` present AND the exact `out_dir` path string
    present AND (`_CLAUDE_TUI_TRUST_PROMPT` or `_CLAUDE_TUI_TRUST_CHOICE`) — in the recent
    line window. On first entry: `os.write(master_fd, _CLAUDE_TUI_TRUST_ANSWER)` exactly
    once, `trust_answered=True`, **reset `last_heartbeat=now`** (reuse `:1445`). Never
    answer without a positively-identified modal (recon finding 6).
  - **Readiness = quiescence:** track `last_novel_monotonic` on every novel-content chunk.
    Editor is ready when `now - last_novel_monotonic >= _CLAUDE_TUI_READY_QUIESCENCE_S`
    AND no unresolved trust modal, AND `now - start >= _CLAUDE_TUI_SUBMIT_DELAY_S` (8s
    retained only as a floor). On ready → submit the review prompt ONCE (existing
    bracket-paste + Enter), set `SUBMITTED`.
  - **Hard pre-submit-only guard:** once `state == SUBMITTED`, DISABLE all trust
    detection/answering permanently (closes the self-referential/injection hazard — panel
    #1). No `os.write` of `y` may occur after submit.
  - **Typed failures, deadline-ordered BEFORE the 180s stall:** if `TRUST_MODAL` entered
    and `_CLAUDE_TUI_TRUST_REJECT` seen after the answer, or still in `TRUST_MODAL` after
    `_CLAUDE_TUI_TRUST_DEADLINE_S` → return `claude_tui_workspace_trust_blocked`. If NO
    modal was ever observed but readiness never arrived within a bound → return
    `claude_tui_editor_not_ready` (distinct). Both mirror the stall return shape (`rc =
    proc.poll() or 1`) and must be checked before the generic `_LEG_STALL_THRESHOLD_S`
    branch (`:1513`).
  - **Bounded, sanitized evidence:** before every non-OK return, `tail = _redacted_stderr
    _excerpt(<control-stripped de-ANSI of terminal_bytes>, max_chars=200)` keeping the
    **END** of the buffer; thread out as a NEW 4th element `pty_tail`. Strip ANSI/OSC/control
    seqs (reuse the module's `_ANSI_*` regexes) BEFORE redaction (panel #6). Import
    `_redacted_stderr_excerpt` **lazily** (function-local) — verify no panel_invoker↔runner
    cycle.
- **`_run_claude_tui_session` callers** — grep first (expected ≤2); update for the 4th
  return element.
- **`_exec_claude_tui_leg` (`:1883`)** — consume `pty_tail`; add mapping branches so
  `claude_tui_workspace_trust_blocked` and `claude_tui_editor_not_ready` (non-OK) → DEGRADED
  with descriptive text; set `PanelLegResult.detail` to the sanitized `pty_tail`.

### `phase-loop-runtime/tests/test_panel_tui_liveness_188.py` (modify)
Reuse the real-subprocess `sh -c` harness (`:70-78`; monkeypatch `_LEG_STALL_THRESHOLD_S`,
`_CLAUDE_TUI_SUBMIT_DELAY_S`, `_CLAUDE_TUI_READY_QUIESCENCE_S`, `_latest_claude_transcript
_text`). New tests:
- `test_trust_modal_answered_once_then_submits_on_quiescence` — synthetic script prints the
  captured trust modal (incl. the scratch path), waits for `y`, then prints a welcome burst
  and quiesces → assert exactly one `y` written, submit only after quiescence, no stall.
- `test_post_submit_marker_text_triggers_no_write_no_block` (**critical, panel #1**) — after
  submission, the script emits review output containing "permission required" / "enter y/n"
  / "please answer y or n" → assert NO further `y` is written and the leg is NOT classified
  trust_blocked.
- `test_trust_modal_never_clears_maps_degraded_before_stall` — reject marker persists →
  `claude_tui_workspace_trust_blocked` → DEGRADED, elapsed < the 180s threshold.
- `test_no_modal_ready_on_quiescence` and `test_no_modal_never_ready_distinct_reason` —
  no-modal readiness path + `claude_tui_editor_not_ready`.
- `test_failed_leg_detail_is_sanitized_and_tail_bounded` — planted `token=SECRET` + ANSI
  control bytes scrubbed; `detail` ≤ bound and holds the buffer END.
- Keep the 4 existing #188 tests, `test_no_fixed_short_timeout_is_injected…`, and
  `test_panel_native_fill_183.py` / `test_panel_invoker_spawn.py` green.

## Documentation impact
- `CHANGELOG.md` — add — the Fable/Claude advisor-board leg now clears the Claude Code
  workspace-trust gate (answered once, pre-submit, path-scoped to its scratch dir), gates
  submission on editor quiescence instead of a fixed delay, and emits typed
  `claude_tui_workspace_trust_blocked` / `claude_tui_editor_not_ready` diagnostics with a
  sanitized PTY tail. No other docs.

## Dependencies & order
1. Constants. 2. State machine + quiescence readiness (reset `last_heartbeat` on the trust
answer — the critical hazard). 3. Pre-submit-only hard guard. 4. Typed
trust-blocked/not-ready reasons, ordered before the 180s stall. 5. Sanitized+bounded
`pty_tail` threading (new arity) → `detail`; update callers; lazy redactor import verified
acyclic. 6. Tests. No new frozen vocabulary (`LEG_STATUSES` unchanged).

## Execution Policy
- execute: effort=high, reason=PTY/pexpect concurrency + subtle #188 liveness coupling +
  security-sensitive (pre-submit-only trust answering) + real-subprocess test surface.

## Verification
```bash
cd phase-loop-runtime
PYTHONPATH=src:tests python -m pytest tests/test_panel_tui_liveness_188.py -q
PYTHONPATH=src:tests python -m pytest tests/test_panel_native_fill_183.py tests/test_panel_invoker_spawn.py -q
PYTHONPATH=src:tests python -m pytest tests/ -q -k "panel or tui or liveness or trust"
```
Then the **operational acceptance gate on host `ai`** (Claude Code 2.1.20x): a narrow
`phase-loop advisor-board` run where the Fable seat completes with real review text (not a
stall/DEGRADED). Record as the PR's operational evidence.

## Acceptance criteria
- [ ] A synthetic trust modal (captured strings, incl. the scratch path) is answered `y`
      exactly once and the review prompt is submitted only after editor quiescence —
      `test_trust_modal_answered_once_then_submits_on_quiescence` passes.
- [ ] Post-submission review output containing trust-marker text produces NO further writes
      and NO `trust_blocked` verdict — `test_post_submit_marker_text_triggers_no_write_no
      _block` passes.
- [ ] An unclearable trust gate → `claude_tui_workspace_trust_blocked` → DEGRADED with
      elapsed < `_LEG_STALL_THRESHOLD_S`; a no-modal never-ready → `claude_tui_editor_not
      _ready` (distinct).
- [ ] Non-OK leg `PanelLegResult.detail` is control-sanitized, secret-scrubbed, bounded,
      and retains the buffer END.
- [ ] The 4 existing #188 tests, the no-short-timeout guard, and the #183 native-fill /
      spawn tests stay green; live `ai` smoke shows the Fable seat completing.
