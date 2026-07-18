# agent-harness backlog cleanup — triage & batches (2026-07-18)

Prioritized cleanup of the open agent-harness issue backlog. **Not** a phase-freeze
roadmap: these are largely independent bug fixes with no interface-freeze DAG, so they
run as themed batches, not serial phases. Sequencing rule: **fix the tooling that
reviews everything else first** (panel/review infra), then execution-correctness
(closeout/scheduler), then governance/verification hardening, then defer enhancements.

Grounded against origin/main + the open list on 2026-07-18. Two issues (#218, #219)
were verified already-fixed by #220 (`eaa8875`) and closed during triage — bare `#N` in
that PR's title never auto-closed them.

## Batch 1 — Panel / review infra (DO FIRST — it reviews all other work)

The advisor board is the instrument every other fix's CR rides on; it degraded this
session's CRs. #222 (grok default effort) already landed. Remaining, by priority:

- **P1 — #196 + #223 (Fable stall). ✅ DONE — merged as #228 (`bd40713`), issues closed.**
  Root cause = the fresh-cwd workspace-trust modal; leg now clears it (path-scoped,
  pre-submit-only, fail-closed on unrecognized gates) + quiescence-gated submit + typed
  DEGRADED→WARN diagnostics. Recon-grounded, 3-round cross-vendor CR converged, live smoke.
  - ~~#196: Fable advisor leg stalls silently in PTY `ep_poll` after authentication.~~
  - ~~#223: Claude Fable TUI stalls at the workspace-trust prompt in a fresh `out_dir`.~~
  - **Why top:** a *silent* stall is the worst failure mode — you eat the full leg
    timeout instead of seeing an immediate error. This is why the Fable correctness
    seat has been running as native-Opus rather than actual Fable. Fixing it restores a
    genuine 4th vendor lens to every panel/CR.
- **P2 — #171: ✅ DONE — merged as #229.** `available_panel_legs()` now exposes grok when
  its CLI is installed (availability-aware; frozen `PANEL_LEGS` untouched), so a down vendor
  reaches a 4th independent leg without a hand-roll. 3-vendor CR converged, CI green.
- **P3 — #224: ✅ DONE — merged as #230.** grokexec/launcher grok leg now clamps effort at
  the CLI boundary (`_grok_cli_effort`: minimal→low, xhigh/max→high), mirroring codex. 3-vendor
  CR converged, CI green. Follow-up filed: planner-eligibility asymmetry + panel `_GROK_EFFORT`
  hardening.
- **Defer (enhancement, not a bug):** #191 first-class delta review with reviewed-byte
  equivalence.

## Batch 2 — Closeout / scheduler correctness (execution-correctness spine)

Next-most load-bearing after review infra: these govern whether a phase can be marked
`complete`/`passed` correctly and whether lanes dispatch cleanly.

- **#85 + #90 (shared root — closeout artifact/status).** Decomposed after a repro pass
  confirmed each sub-bug still reproduces on current main:
  - #85(B) phase-alias provenance drift: **✅ DONE — merged as #235.** verification.json now
    records the LIVE run alias threaded from `runner.py:3508` (env override > threaded >
    `state.json:current_phase` > "unknown"), so a mid-run roadmap amendment no longer
    mis-attributes the artifact's phase. Callsite pinned by a static AST test (runtime
    run_loop is bundle-gated / `dotfiles_integration`-marked / CI-excluded).
  - #85(C) repo-path portability: ⏳ NEXT — repo-relative normalization in
    events/state_ops/reconcile/classifier/runner so closeout survives a moved/renamed repo root.
  - #85(D)/#90 rehydration: ⏳ reconcile must ingest a tracked `closeout.md` to rehydrate a
    completed roadmap without re-verification.
  - #85(A) hash-scope: ⏳ DEFERRED product decision — prose-edits-invalidate-completion is
    WAI-with-warning + test-locked; surface to the user rather than silently change.
- **#84: ✅ DONE — merged as #232.** Root cause was an argparse subparser clobber (not the
  dispatcher): common opts before the subcommand were reset to defaults; added SUPPRESS.
  Residual non-common-arg clobber class → #233 (fail-closed, low severity).
- **#186: ⚠️ LIKELY STALE — fix withdrawn (#234 closed).** The reported empty-owned-file
  monolithic dispatch was already fixed by #58 (`_extract_plan_owned_files` now aggregates
  lane ownership via `parse_plan_ownership`; verified non-empty on the real fixture). A
  preflight-reject also breaks the DOCUMENTED `--lane-scheduler serialized` compat mode.
  Re-verify against current main → likely close as resolved-by-#58.
  **✅ DONE: re-verified (owned-files now populated on the monolithic path) → CLOSED as resolved-by-#58; PR #234 withdrawn.**

## Batch 3 — Verification / governance hardening

- **#221:** robust versioned/absolute suite-interpreter guard (split out of #220; the
  regex detector was unsound). Follow-up hardening of the #219 interpreter fix.
- **#209:** preserve raw failure diagnostics on verification failure (localize the
  failing stage in a multi-stage suite) — this was a named contributor to multi-day
  thrash (see #213).
- **#211:** static acceptance-coverage audit — catch silent loosening of roadmap
  exit-criteria at the planner hop. (Overlaps the deferred #219(b-ii)
  acceptance-criteria→command coverage.)
- **#177:** launcher product-loop review action — codex review leg not confirmed
  read-only for the live-tree `--add-dir` path (security/read-only integrity).
- **#202:** broker — re-diff `head_sha`-vs-`base` to bind `publish_committed_branch`
  admission scope to actual branch changes.

## Batch 3.5 — Org-rename canonical sweep (`ViperJuice` → `Consiliency`)

The repo moved from `ViperJuice/agent-harness` to `Consiliency/agent-harness`
(2026-07-18). No breakage — the local remote + `gh` already resolve to Consiliency,
and GitHub redirects preserve old clone/install/web URLs and issue/PR numbers. But
**58 hardcoded `ViperJuice/agent-harness` refs across 22 tracked files** are now
canonically stale. This is a bounded `/claude-plan-detailed` item, **not a blind
`sed`** — the refs split by intent:

- **Update (live/canonical):** `install-agent-harness.sh`, `phase-loop-runtime/pyproject.toml`
  + `consiliency-harness/pyproject.toml` project URLs, `README.md` (x3) install
  instructions, `AGENTS.md` fully-qualified-ref example, `skills-src/**` +
  `phase-loop-skills/**` + `skills_bundle/**` SKILL.md install commands (keep the 4
  regenerated skill copies in parity).
- **Update with care (paired fixture+assertion):** `test_panel_tui_liveness_188.py`,
  `test_task_message_broker.py`, `tests/fixtures/fleet_map/gp/**` (`agent-harness.pin.json`,
  `bootstrap.sh`) — move the string and its assertion together.
- **Leave (provenance/historical):** `CHANGELOG.md`, dated `plans/detailed-*.md`,
  `specs/phase-plans-v6/v7.md`, `.phase-loop/handoffs/*` — record what was true when written.

Verify the release-pin/install path still resolves after the sweep (the pin JSON drives
`consiliency-harness` bootstrap). Fully-qualified refs going forward:
`Consiliency/agent-harness#N` (short `agent-harness#N` unaffected).

## Batch 4 — Enhancements / non-bugs (defer or convert; do NOT batch with fixes)

- **#213 — close/convert.** Analysis/evidence issue ("goal-fidelity was NOT the
  multi-day thrash cause — infra + scrubbed diagnostics were"). Its actionable residue
  is already captured by #209 (diagnostics) and #211 (coverage). Close with a summary
  pointer, or convert to a docs note — not an execution item.
- **#91 — defer (feature).** Require blocking visual-avatar evidence for
  avatar/browser media closeout.
- **#191 — defer (feature).** First-class delta review with reviewed-byte equivalence.

## Suggested execution order

1. #196+#223 (Fable stall) → `/claude-plan-detailed`, then implement + cross-vendor CR.
2. #171 (wire grok into available legs).
3. Batch 2 closeout cluster (#85+#90, then #84, then #186).
4. Batch 3 hardening (#221 first — smallest, follow-up of a landed fix).
5. Triage Batch 4 (close #213; label #91/#191 as enhancements, defer).

## Byproduct: labels

All 16 open issues are currently label-less. Apply during triage: `panel-infra`
(#171/#196/#223/#224/#191), `closeout`/`scheduler` (#84/#85/#90/#186),
`verification` (#177/#202/#209/#211/#221), `enhancement` (#91/#191), `analysis` (#213).
