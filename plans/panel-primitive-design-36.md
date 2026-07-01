# Design brief — own the cross-vendor panel in agent-harness (#36)

## Problem
The harness's governed/review features (governed pre-merge gate, `plan-phase --consensus`, code-review
gating) rely on a **3-agent cross-vendor adversarial panel** (native Claude + Codex + Gemini). Today that
mechanism lives ONLY in the maintainer's private dotfiles (`~/.claude/skills/advisor-panel` +
`run_cli_panels.sh`). agent-harness is the PUBLIC package that ships `phase-loop-skills` to
claude/codex/gemini/opencode — so a downstream `install-agent-harness.sh` gets the workflow skills but
NOT the panel they depend on. A public harness's core dependency shouldn't live in one maintainer's
private config. It also proved unreliable in practice (the codex/gemini legs silently degraded to 1 leg
for much of the #29 work — see the lessons below).

## Goal / non-goals
- **Goal:** a harness-owned, tested, portable cross-vendor panel-review primitive + a thin skill fronting
  it, shipped from agent-harness via the existing `phase-loop-skills` pipeline; redact the dotfiles copy.
- **Non-goals:** changing the review *policy* (panel for plans/designs/CRs stays); replacing the native
  Claude leg's repo-grounded review; a general background-task manager (that's #33/upstream).

## Proposed architecture
1. **Runtime primitive** (`phase_loop_runtime/panel_review.py`): `run_panel(artifact_paths, brief, *,
   effort, legs) -> PanelResult`. Reuses `launcher.py` `build_codex_command` / `build_gemini_command`
   (tested, correct subscription auth). Dispatches: codex (`codex exec`, read-only), gemini (`agy
   --print`), and — when running inside a Claude harness — a native Claude leg with repo access. Returns
   per-leg `{status: ok|empty|timeout|degraded, verdict, findings, error_signature}` + a reconciled view.
2. **Review profile baked in** (the #135 lessons — all proven):
   - **Inline the artifact into the prompt** for gemini (`agy --print` does NOT read `--add-dir` files →
     hangs to timeout). Codex reads via its own tools but is slow.
   - **Input-scaled timeout / effort:** codex at `xhigh` on a review takes ~900s on ~1.3k lines; use a
     generous timeout (~1200-1800s) and/or drop to `high` for large inputs; chunk when needed.
   - **Minimal profile:** read-only sandbox, **no MCP**, no plugins (MCP/plugin noise is non-fatal but
     slows/clutters); web-search on for research.
   - **Never silently degrade:** report each leg's status; surface which legs actually contributed. A
     "3-agent panel" that's secretly 1 leg is a safety illusion.
   - **Salvage:** capture each leg's session-id / transcript path so a hung/empty leg can be salvaged
     (absorbs the panel-relevant part of #33).
3. **Thin skill** (`skills-src/<harness>/<harness>-advisor-panel/`): the human entry point; defers all
   dispatch/reconcile to the runtime primitive. Regenerated through `regenerate_skills_bundle.py` +
   `sync_skills_bundle.py` like every other workflow skill.
4. **Self-contained:** the primitive must NOT depend on the dotfiles `*-cli-runner` bridge skills
   (that would invert the portability bug). Encode the codex/agy invocations in the runtime.
5. **Dotfiles redaction:** delete the dotfiles `advisor-panel` copy; the bootstrap installs it from the
   pinned agent-harness clone (same as the other phase-loop skills). `/advisor-panel` stays available
   everywhere; only the source of truth moves.
6. **Merge with model-routing-v3** (reliable cross-vendor dispatch in the executor framework — same home).

## Open questions for the panel
1. Is `panel_review.py` the right seam, or should it live inside the existing executor/launcher module
   (reuse the dispatch loop) vs a new module? Composition vs a parallel path.
2. The native Claude leg needs repo access + to run the suite — it can only be spawned by a Claude host.
   How should the primitive spawn it portably (native `Agent` when host==claude; Agent View `claude --bg`
   otherwise; skip + report degraded when neither)? Does that belong in the runtime or the skill?
3. Reconciliation: does the primitive *reconcile* (pick a verdict) or just return per-leg results for the
   caller to reconcile with primary evidence? (The session lesson: reconcile with code-verification, not
   vote-count — which argues for returning raw + a repo-grounded verify step, not auto-voting.)
4. Testing a subprocess-dispatch primitive without spending real frontier tokens in CI: stub the
   codex/agy boundary + assert the command construction, profile flags, inline-feeding, timeout scaling,
   and degraded-leg reporting — is that sufficient, or is a gated live smoke needed?
5. Scope discipline: MVP = the dispatch primitive + profile + degraded reporting + the thin skill +
   redaction. Defer: auto-reconciliation, a 4th panelist, live-smoke CI. Right cut?

---

## PANEL RECONCILIATION + CORRECTED SCOPE (post-review)

The 3-agent design panel (native Claude repo-verified; gemini inline PARTIALLY AGREE; codex empty) —
**verdict: PARTIALLY AGREE, redirect not reject.** The repo-grounded leg caught that this brief was
GREENFIELD-WRONG: the primitive already exists.

**Verified findings (confirmed by direct code read):**
- `panel_invoker.py` already IS `run_panel`: `invoke_panel(...) -> PanelResult`, per-leg statuses,
  `terminal_verdict` fail-closed classifier, `_subscription_env` (strips API keys), codex read-only +
  `--output-last-message` profile, the injectable `_exec_leg`/`spawn` test seam. **LIVE** in the
  governed gate (`governed_review.py:26/188/221`, `governed_premerge.py`, `runner.py:8010/8070`).
- The claude leg is the deferred piece (`_exec_leg`/`_default_spawn` return `"unavailable"`).
- Reusing `build_codex_command`/`build_gemini_command` would be a REGRESSION — they emit
  `--sandbox danger-full-access` (launcher.py:309/311) + an executor-shaped closeout prompt. The right
  reuse is `profiles.py` review-aware model/effort (`"review": (gpt-5.5, "high")`, :29).
- **Gemini `--add-dir` WORKS** (live smoke confirmed): the "inline not --add-dir" claim in this brief
  was an over-generalization of a #29 *timeout* on a large diff. The real bug was the fixed
  `_LEG_TIMEOUT_S = 600` (< codex-xhigh ~900s) → the panel silently degraded. KEEP `--add-dir`.

**Corrected scope (extend, don't build):**
1. [DONE, this PR] Input-scaled leg timeout (`_leg_timeout_for`) + argv-assertion tests — the real
   silent-degradation bug. `--add-dir`/`--output-last-message` profile unchanged.
2. Parameterize `_LEG_PROMPT` / single-`artifact` → `run_panel(artifact_paths, brief)` for arbitrary
   review use; keep `_exec_leg`/`spawn` seam + `terminal_verdict`; governed consumers must keep passing.
3. Fill the claude leg: native `Agent` (repo-access verify arm) via the thin skill; `claude --bg` in
   `_exec_leg` WITH the dotfiles `run_claude_leg.sh` MCP-trust scratch-dir fix (fresh mktemp outside any
   project trust scope + `--strict-mcp-config --mcp-config '{"mcpServers":{}}'`; never cwd=review_dir).
4. Reuse `profiles.py` review model/effort (drop the hardcoded gpt-5.5/xhigh).
5. Thin `advisor-panel` skill via the phase-loop-skills pipeline; a gated gemini live smoke.
6. Dotfiles redaction LAST: port claude leg → ship skill → re-pin bootstrap → verify install → redact.
