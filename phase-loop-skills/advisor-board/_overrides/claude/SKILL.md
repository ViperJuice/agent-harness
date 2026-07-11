---
name: advisor-board
description: Run a customizable cross-vendor advisor board (formerly advisor-panel; that name remains a working alias) through the agent-harness runtime primitive when a high-stakes change needs independent review evidence.
---

# Advisor Board

Use this skill when a plan, implementation diff, release closeout, or other high-stakes artifact needs an independent cross-vendor review board. This skill was formerly named `advisor-panel`; that name still resolves as an alias, so existing instructions that say "advisor-panel" keep working.

## Source Of Truth

The advisor-board (formerly advisor-panel) implementation is owned by `agent-harness`:

- Runtime primitive: `phase_loop_runtime.panel_invoker`
- Board model: `phase_loop_runtime.advisor_board` (seats, boards, resolver, validation)
- Entry points (runnable default): `advisor_board.composition.compose_review_board` + `panel_invoker.invoke_board`, exposed as the `phase-loop advisor-board <artifact>` CLI. The legacy `available_panel_legs`/`invoke_panel`/`invoke_panel_request` stay in place for the governed review/pre-merge gates (unchanged, byte-identical golden).
- Governed workflow integration: phase-loop governed review/pre-merge paths

Do not call dotfiles advisor-panel scripts, copy provider-specific shell scripts, or introduce a separate implementation in the skill body. The skill is a thin operator guide over the runtime primitive.

## Boards & Availability-Aware Composition

Named boards live in `phase_loop_runtime.advisor_board.presets`; the default review board is `code-review`, a 4-vendor cross-vendor panel — one seat per frontier vendor (grok, claude, codex, gemini), each at its MAXIMUM thinking with a DISTINCT review lens (adversarial / correctness / red-team / alternative-approach).

Composition is AVAILABILITY-AWARE (`composition.compose_review_board`): it targets 4 independent reviewers (hard floor 3) and NEVER collapses to 1–2 when vendors are down. Each vendor that is both present on PATH AND authenticated gets one lens-distinct seat first; the remaining seats are BACKFILLED onto the available (up + authed) vendors with DIFFERENT lenses. So 2 vendors up still yields a full 4-seat board, and 1 vendor up yields 4 distinct-lens seats on that vendor. The bare `advisor-board` (`default`/premerge) board is unchanged — the byte-frozen 3-leg panel.

## Three Ways To Feed Material

There are THREE DISTINCT ways to give the panel material. The #114 fix names them accurately: `artifact_ref` and `brief_ref` are Read-file-and-stage conveniences, while `context_refs` is the true by-reference mode.

- **Inline** (`artifact="..."`) — small material passed as a string, written verbatim into `review-bundle.md`. A large inline artifact logs a steering warning.
- **Read-file-and-stage** (`artifact_ref="path/to/bundle.md"`, or a list) — the runtime READS the local file(s) off disk and stages their bytes into `review-bundle.md` (a single path verbatim; multiple paths under per-file headers). This keeps YOUR context lean, but the file CONTENTS still land in the staged bundle every leg reads. Use it when you WANT the legs to read the material verbatim. `artifact_ref` wins over `artifact` if both are given.
- `brief_ref="path/to/brief.md"` — a Read-file-and-stage path for a large review brief; staged as `review-instructions.md`. Omit it to use the built-in review/advisory brief.
- **TRUE by-reference** (`context_refs=["path/to/large.pdf", ...]`, #114) — the runtime stages ONLY a path + metadata manifest (path, size, sha256, MIME/extension, and PDF page count when cheap) plus an instruction telling each leg to OPEN the files with its own local tools. Raw file contents are not read into the bundle or prompt by this runtime path. Use it for LARGE or PRIVATE local material when the selected provider/backing can access the same local file path. A missing/unreadable path fails CLOSED naming the path, unless you pass `context_refs_soft_warn=True` (logs a warning and emits an `UNREADABLE` manifest entry). Pathnames and hashes can disclose sensitive metadata, and a leg may disclose file contents after it intentionally inspects a referenced file unless an output policy forbids disclosure.

## Bounding A Slow Leg

Legs fan out concurrently, so panel wall-clock ≈ max(leg), not sum. Each leg's default timeout is INPUT-SCALED (~600s floor + ~12s/KB, capped at 1800s): a ~150-line artifact is roughly ~11 min/leg, and a genuinely max-effort frontier review of a large bundle can approach the cap. Pass `timeouts_by_leg={"gemini": 300}` (or `PanelRequest.timeout_seconds_by_leg`) to BOUND a slow/stalled leg so it fails ITS leg instead of hanging the whole panel. A transient CLI stall (an empty turn or a "timeout waiting for response" marker) is retried once, but only when it fails FAST, so a retry can never double a slow leg's wall-clock.

## Use

**On a Harness Code host, run the claude/Fable leg as a NATIVE Agent, not the runtime TUI.** When you are *inside* Harness Code, the runtime runs the non-<harness> board legs (`grok`, `codex`, `gemini` — whichever are available AND authenticated) and returns the `claude` leg as `UNAVAILABLE` ("deferred to native Agent") by design — it must not spawn a second Harness TUI. Supply the <harness> leg yourself with the Task tool (a Fable/Harness Agent given the same `review-instructions.md` + `review-bundle.md`), require it to end with `AGREE`/`PARTIALLY AGREE`/`DISAGREE`, and reconcile it with ALL the runtime legs (do NOT drop `grok` — the board is 4-vendor, not the old 3-leg panel). A `UNAVAILABLE` <harness> leg is a *gap to fill*, not an acceptable board short a seat.

1. Prefer the repo's governed phase-loop path when reviewing phase execution or pre-merge work.
2. For a standalone smoke or diagnostic, run `phase-loop advisor-board <artifact>` (or, in-process, compose with `compose_review_board` and pass the material's path via `artifact_ref` to `phase_loop_runtime.panel_invoker.invoke_board`).
3. Require every leg to end with `AGREE`, `PARTIALLY AGREE`, or `DISAGREE`.
4. Treat `EMPTY`, `TIMEOUT`, `ERROR`, `DEGRADED`, and `UNAVAILABLE` as structured evidence, not successful reviews.
5. Keep provider API keys out of the environment; the runtime strips known API-key variables and uses local subscription CLIs.

## Standalone Smoke Shape

```python
from phase_loop_runtime.advisor_board.composition import compose_review_board
from phase_loop_runtime.panel_invoker import invoke_board

# Availability-aware by default: compose_review_board seats only vendors that are
# BOTH on PATH and authenticated (unauthed vendors are dropped and backfilled).
board = compose_review_board()
result = invoke_board(board, "", artifact_ref="path/to/bundle.md")
for leg in result.legs:
    print(leg.seat_key, leg.status)
```

Under Harness Code, expect the `claude` leg to report `UNAVAILABLE` (deferred to the native Agent) — the runtime does not spawn a Harness TUI here. Supply the <harness> leg natively (Task tool) and reconcile it with ALL the runtime legs (`grok` + `codex` + `gemini`, whichever are up and authed) — the board is 4-vendor, so do not drop `grok`.
