# Advisor Board — Capabilities Card

The **Advisor Board** is a customizable, model-first, multi-harness review board
(`phase_loop_runtime.advisor_board` over the `phase_loop_runtime.panel_invoker`
runtime primitive). It evolved the fixed 3-vendor `advisor-panel` into a named,
purpose-tagged, open-ended board of **seats**, where a seat is a *cognition*
(`{model, effort, harness?, lens?, auth?, backing?}`) — the harness is a
defaulted-but-overridable execution lane, not the primary key.

This card is the release reference for **how the board is invoked**, **which
models run on which harnesses**, **the built-in board presets**, and **how to add
a custom board**. It is derived from the live registries / install code so it
cannot drift; a `pytest` smoke (`tests/test_advisor_board_alias_install.py`,
`tests/test_advisor_board_integration.py`) keeps the skill names and matrix honest.

---

## Skill names — the `<harness>-advisor-board` rule

The board is installed **per harness** under a harness prefix. Invoke it as
`<harness>-advisor-board`:

| Harness    | Canonical skill (invoke this) | Historical alias (still resolves) |
| ---------- | ----------------------------- | --------------------------------- |
| `claude`   | `claude-advisor-board`        | `claude-advisor-panel`            |
| `codex`    | `codex-advisor-board`         | `codex-advisor-panel`             |
| `gemini`   | `gemini-advisor-board`        | `gemini-advisor-panel`            |
| `opencode` | `opencode-advisor-board`      | `opencode-advisor-panel`          |

The supported prefixes are exactly the installed skill roots
(`skill_paths.HARNESS_DEFAULT_SKILL_ROOTS`): **claude, codex, gemini, opencode**.

**The alias exception.** The prior name `advisor-panel` remains a working alias of
`advisor-board`. On every install, `install_skills` installs the canonical
`advisor-board` a second time under the prefixed alias name
`<harness>-advisor-panel`, copied FROM the canonical source — so
`/claude-advisor-panel` (the maintainer's habitual invocation) resolves to
**today's** advisor-board, and a stale pre-rename `<harness>-advisor-panel` dir is
overwritten (content refreshed, orphan files pruned), never left drifting.
`skill_install.canonical_skill_name("<harness>-advisor-panel")` maps back to
`advisor-board` for callers that resolve by string.

> `advisor-board` is the *unprefixed* canonical skill inside the neutral bundle
> (`phase-loop-skills/advisor-board/`); the `<harness>-` prefix is added at install.

---

## Model × harness matrix

A seat is valid only when its `(model, harness)` pairing is registered — a
cross-vendor pairing (e.g. `claude:gpt-5.5`) is **rejected at config time** with an
actionable message, before any subprocess is spawned. Source of truth:
`DefaultHarnessRegistry` / `DefaultModelRegistry` / `DefaultCompatibilityMatrix`.

### Harnesses (execution lanes)

| Harness    | CLI            | Auth lanes              | Backing    |
| ---------- | -------------- | ----------------------- | ---------- |
| `claude`   | `claude`       | subscription, api_key   | homebrew   |
| `codex`    | `codex`        | subscription, api_key   | homebrew   |
| `gemini`   | `agy`          | subscription, api_key   | homebrew   |
| `opencode` | `opencode`     | subscription, api_key   | omnigent   |
| `pi`       | `pi`           | subscription, api_key   | omnigent   |
| `cursor`   | `cursor-agent` | subscription, api_key   | omnigent   |

- **homebrew** = the built-3 native launch (claude native/TUI, codex, gemini) + the
  native host leg. Byte-for-byte the legacy panel for the `default` board.
- **omnigent** = harness breadth (opencode/pi, and cursor/amp when the live
  `GET /v1/harnesses` catalog reports them) routed through omniagent-plus →
  Omnigent v0.4.0, **opt-in and fail-closed** (an unavailable lane skips-with-warning,
  never a silent homebrew fallback).

### Models

| Model            | Vendor family | Default lane | Runnable by       | Effort ceiling |
| ---------------- | ------------- | ------------ | ----------------- | -------------- |
| `gpt-5.5`        | codex         | `codex`      | codex, opencode   | max            |
| `claude-sonnet-5`| claude        | `claude`     | claude            | max            |
| `claude-opus-4-8`| claude        | `claude`     | claude            | max            |
| `claude-haiku-4-5`| claude       | `claude`     | claude            | max            |
| `claude-fable-5` | claude        | `claude`     | claude            | max            |
| `Gemini 3.1 Pro` | gemini        | `gemini`     | gemini            | max            |

**Effort is model-first `{model, effort}`**, split out of the model name and mapped
per harness by `render_seat_invocation`: `claude` → `--effort <level>`, `codex` →
`-c model_reasoning_effort=<xhigh|high|…>`, `gemini` → effort baked into the model
string (`"Gemini 3.1 Pro (High)"`). Canonical effort levels: `low, medium, high, max`.

**Auth is subscription-default, never-silent-key.** A subscription seat actively
scrubs *every* vendor API-key var from the subprocess env / gateway payload; an
api-key seat is reachable only behind `Board.allow_api_key_fallback` and injects
ONLY its own vendor's key. A board can't even be constructed holding an api-key
seat without opting in.

---

## Board presets

Four built-in presets (`advisor_board.presets`), each a named, purpose-tagged,
open-ended seat list. Load via `load_boards()` (self-validates every preset against
the matrix at load time).

| Preset        | Purpose          | Seats (model · effort · harness · lens) |
| ------------- | ---------------- | ---------------------------------------- |
| `default`     | premerge-review  | gpt-5.5 · max · codex · — ; Gemini 3.1 Pro · high · gemini · — ; claude-sonnet-5 · max · claude · — |
| `code-review` | code-review      | gpt-5.5 · max · codex · — ; claude-sonnet-5 · max · claude · adversarial |
| `brainstorm`  | brainstorm       | claude-sonnet-5 · high · claude · adversarial ; gpt-5.5 · high · codex · supportive ; Gemini 3.1 Pro · high · gemini · lateral |
| `doc-edit`    | doc-edit         | claude-sonnet-5 · medium · claude · copyedit ; gpt-5.5 · medium · codex · structure |

`default` **is** the shared fixture board (`fixtures.DEFAULT_BOARD`), so the
back-compat keystone holds by construction: a bare `advisor-board` invocation
resolves to today's exact three seats and reproduces the legacy 3-leg panel
byte-for-byte (proven in `tests/test_advisor_board_golden.py`).

### Invoking a board

```
advisor-board <artifact>                       # bare → the `default` board
advisor-board --board code-review <artifact>   # a named preset
advisor-board --seats gpt-5.5:max:codex <art>  # ad-hoc seats (model:effort[:harness])
```

Runtime entry point: `panel_invoker.invoke_board(board, artifact, ...)`. Legacy
callers keep using `panel_invoker.invoke_panel(...)` unchanged.

---

## How to add a custom board

Boards layer over the presets from
`$XDG_CONFIG_HOME/agent-harness/advisor-boards.toml`
(`advisor_board.board_config_path()`; shape frozen by
`fixtures/advisor-boards.example.toml`). A user board with the same name as a
preset overrides it.

```toml
# ~/.config/agent-harness/advisor-boards.toml
default_board = "my-review"          # optional: what bare `advisor-board` resolves to

[[boards]]
name = "my-review"
purpose = "code-review"

  [[boards.seats]]
  model = "gpt-5.5"                  # must be a registered model…
  effort = "high"                    # …at/under its effort ceiling…
  harness = "codex"                  # …on a compatible lane (else config-time reject)

  [[boards.seats]]
  model = "claude-sonnet-5"
  effort = "max"
  harness = "claude"
  lens = "adversarial"               # optional thinking lens; distinguishes same-model seats
```

Rules enforced at `load_boards()` time (never a silent drop):

- an unknown config key → clear error;
- an unregistered model or a cross-vendor `(model, harness)` pairing → rejected
  with the valid lanes named;
- an effort above the model's ceiling → rejected;
- `allow_api_key_fallback` defaults `false`; an api-key seat without opting in is
  rejected (never-silent-key);
- `backing` defaults `homebrew`; set `omnigent` for a breadth-harness seat (routes
  through the gateway when available, skips-with-warning when not).

Two seats that differ only by `lens` (or model/effort) are fully expressible: results
are keyed by **seat position** with `seat.seat_key` as the human-readable label, so
a two-same-vendor board is not collapsed.

---

## Migration note — for existing `advisor-panel` callers

**Nothing you have breaks.** The rename is additive and back-compat by construction:

1. **The name.** `advisor-panel` → `advisor-board`. The old name remains a working
   alias: `/<harness>-advisor-panel` still resolves (to the current advisor-board),
   agent instructions that say "advisor-panel" keep working, and
   `canonical_skill_name()` maps the alias back. There is **no** action required to
   keep an existing invocation working; prefer `<harness>-advisor-board` for new
   instructions.

2. **The runtime API.** `panel_invoker.invoke_panel(artifact, legs, ...)` is
   **unchanged** — same signature, same behavior. The live governed gates
   (`governed_review`, `governed_premerge`) still call it. The new
   `invoke_board(board, artifact, ...)` seam is *additive*; migrate to it only when
   you want boards/presets/breadth. When you do, the `default` board reproduces the
   legacy 3-leg run byte-for-byte on launch order, per-leg argv/env/timeout, status,
   text, and failure semantics.

   - **One intentional result-shape enrichment:** `invoke_board` populates
     `PanelLegResult.seat_key` with a richer per-seat label (e.g.
     `codex:gpt-5.5:max`) instead of the bare leg (`codex`). `.leg` is preserved, so
     any caller keying on `.leg` / `.status` / `.usable` is unaffected; this only
     *adds* the ability to tell two same-vendor seats apart. This is the sole
     contract-sanctioned delta (ABDRESOLVE finding 4), asserted explicitly in the
     golden proof.

3. **The presets.** If you invoked the panel for a premerge review, that is now the
   `default` board (bare `advisor-board`). For a lens-differentiated review reach for
   `--board code-review`; for divergent thinking, `--board brainstorm`. Define your
   own in `advisor-boards.toml` (above).

4. **Auth / observability posture is unchanged for the default path.** Subscription
   stays the default lane; the default board scrubs vendor keys exactly as before;
   `sink=None` (the default) emits no observability envelope, so the live default
   panel stays byte-neutral.
