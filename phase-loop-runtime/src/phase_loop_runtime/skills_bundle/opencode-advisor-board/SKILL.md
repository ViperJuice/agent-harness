---
name: opencode-advisor-board
description: Run a customizable cross-vendor advisor board (formerly advisor-panel; that name remains a working alias) through the agent-harness runtime primitive when a high-stakes change needs independent review evidence.
---

# Advisor Board

Use this skill when a plan, implementation diff, release closeout, or other high-stakes artifact needs an independent cross-vendor review board. This skill was formerly named `advisor-panel`; that name still resolves as an alias, so existing instructions that say "advisor-panel" keep working.

## Source Of Truth

The advisor-board (formerly advisor-panel) implementation is owned by `agent-harness`:

- Runtime primitive: `phase_loop_runtime.panel_invoker`
- Board model: `phase_loop_runtime.advisor_board` (seats, boards, resolver, validation)
- Entry points: `available_panel_legs`, `invoke_panel`, and `invoke_panel_request` (from a `PanelRequest`)
- Governed workflow integration: phase-loop governed review/pre-merge paths

Do not call dotfiles advisor-panel scripts, copy provider-specific shell scripts, or introduce a separate implementation in the skill body. The skill is a thin operator guide over the runtime primitive.

## Three Ways To Feed Material

There are THREE DISTINCT ways to give the panel material. The #114 fix names them accurately — the old "reference, don't inline" heading mislabeled `artifact_ref`, which always READ AND INLINED the file bytes into the staged bundle.

- **Inline** (`artifact="..."`) — small material passed as a string, written verbatim into `review-bundle.md`. A large inline artifact logs a steering warning.
- **Read-file-and-INLINE** (`artifact_ref="path/to/bundle.md"`, or a list) — the runtime READS the file(s) off disk and INLINES the bytes into `review-bundle.md` (a single path verbatim; multiple under per-file headers). This keeps YOUR context lean, but the file CONTENTS still land in the staged bundle every leg reads. Use it when you WANT the legs to read the material verbatim. `artifact_ref` wins over `artifact` if both are given.
- **TRUE by-reference** (`context_refs=["path", ...]`, #114) — the runtime injects ONLY a path + metadata manifest (path, size, sha256, MIME/extension, and PDF page count when cheap) plus an instruction telling each leg to OPEN the files with its own local tools. The file CONTENTS are NEVER read into the bundle or prompt. This is the mode for LARGE or PRIVATE material (e.g. third-party PDFs you must not paste into a model prompt): the harness enforces the boundary. A missing/unreadable path fails CLOSED naming the path, unless you pass `context_refs_soft_warn=True` (logs a warning and emits an `UNREADABLE` manifest entry).
- `brief_ref="path/to/brief.md"` — compose a large review brief in a file; staged as `review-instructions.md`. Omit it to use the built-in review/advisory brief.

## Bounding A Slow Leg

Legs fan out concurrently, so panel wall-clock ≈ max(leg), not sum. Each leg's default timeout is INPUT-SCALED (~600s floor + ~12s/KB, capped at 1800s): a ~150-line artifact is roughly ~11 min/leg, and a genuinely max-effort frontier review of a large bundle can approach the cap. Pass `timeouts_by_leg={"gemini": 300}` (or `PanelRequest.timeout_seconds_by_leg`) to BOUND a slow/stalled leg so it fails ITS leg instead of hanging the whole panel. A transient CLI stall (an empty turn or a "timeout waiting for response" marker) is retried once, but only when it fails FAST, so a retry can never double a slow leg's wall-clock.

## Use

1. Prefer the repo's governed phase-loop path when reviewing phase execution or pre-merge work.
2. For a standalone smoke or diagnostic, stage the review material in a file and pass its path via `artifact_ref` to `phase_loop_runtime.panel_invoker.invoke_panel`.
3. Require every leg to end with `AGREE`, `PARTIALLY AGREE`, or `DISAGREE`.
4. Treat `EMPTY`, `TIMEOUT`, `ERROR`, `DEGRADED`, and `UNAVAILABLE` as structured evidence, not successful reviews.
5. Keep provider API keys out of the environment; the runtime strips known API-key variables and uses local subscription CLIs.

## Standalone Smoke Shape

```python
from phase_loop_runtime.panel_invoker import available_panel_legs, invoke_panel

panel = invoke_panel("", available_panel_legs(), artifact_ref="path/to/bundle.md")
for leg in panel.legs:
    print(leg.leg, leg.status)
```
