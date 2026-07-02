# Open Issues Planning Assessment - 2026-06-30 20:35 UTC

## Scope

This assessment classifies the currently open `agent-harness` issue work that was researched on branch
`codex/open-issues-planning-20260630`:

- `agent-harness` #39: `_live_reverify` trivial-passes when no `## Verification`; honor `PHASE_LOOP_VERIFY_ENFORCE`
- `agent-harness` #36: own cross-vendor advisor panel in `agent-harness`; runtime primitive plus thin skill; redact from dotfiles
- `agent-harness` #33: background subprocess management should detect and salvage hung processes
- `agent-harness` #26: skills-cutover follow-ups
- `dotfiles` #135: advisor-panel Codex/Gemini review legs fail because panel input is not actually fed to the CLIs

## Classification

| Work item | Artifact | Reason |
| --- | --- | --- |
| #39 live reverify hard-mode fail-closed | Detailed plan: `plans/detailed-fix-issue-39-live-reverify-hard-mode-20260630-2035.md` | Single bounded runtime bug with a small code surface, clear acceptance, and direct tests. |
| #33 background subprocess liveness/salvage | Detailed plan: `plans/detailed-fix-issue-33-background-subprocess-liveness-20260630-2035.md` | Mostly one runtime subsystem with existing stdin and stale-cleanup behavior; the remaining gap is targeted hardening and tests. |
| #36 + dotfiles #135 + model-routing-v3/Gemini 3.5 Flash | Roadmap: `specs/phase-plans-v4.md` | Multi-phase, cross-repo work touching runtime panel contracts, model routing, skill packaging, native Claude leg behavior, and dotfiles redaction. It needs interface freeze points and phased closure. |
| #26 skills-cutover follow-ups | No local `agent-harness` plan artifact yet | Latest issue context leaves lower-priority follow-ups in governed-pipeline and dotfiles. They should be separate repo-owned plans if prioritized, not folded into the advisor-panel or subprocess changes. |

## Research Summary

### #39

`phase-loop-runtime/src/phase_loop_runtime/train_runner.py` already performs live reverify in `_live_reverify`, but returns `True` when both plan verification commands and `automation.suite_command` are absent. That is the false-green path reported in #39.

`phase-loop-runtime/src/phase_loop_runtime/runner.py` already has the intended hard-mode behavior in `_execute_verification_preflight_blocker`: when `PHASE_LOOP_VERIFY_ENFORCE=hard`, missing verification evidence yields a non-human blocker with class `verification_evidence_missing`.

The detailed plan keeps warn/default behavior unchanged while making hard mode fail closed in the train coordinator.

### #33

The launcher already closes stdin for background paths:

- `launch(..., log_path=None)` uses `stdin=subprocess.DEVNULL` when no payload is supplied.
- The `Popen` log path uses `stdin=subprocess.DEVNULL` when no payload is supplied.
- Existing tests cover both paths.

The remaining issue is not a blank slate. The useful work is to add CPU-aware liveness classification, preserve salvage evidence before cleanup, and ensure live CPU-active processes are not misclassified as hung solely because output is quiet.

### #36 and dotfiles #135

The runtime already has a panel primitive in `phase_loop_runtime/panel_invoker.py`, but the current implementation still has the weaknesses called out by #36 and #135:

- static 600 second leg timeout
- Gemini is invoked with `--add-dir` instead of being forced to receive the review artifact inline
- the prompt asks the CLI to read files from the review dir, which is the failure shape seen in dotfiles #135
- the Claude leg is currently unavailable, while #36 requires a repo-grounded verifying Claude leg
- the repo does not yet own an `advisor-panel` skill in the canonical `skills-src` bundle
- dotfiles still owns and installs a separate `shared/skills/advisor-panel`

This is a roadmap-sized unit because it needs runtime contract design, model routing updates, skill source migration, and cross-repo cutover.

### #26

The latest issue comments identify remaining work outside `agent-harness`: a governed-pipeline package pin and a dotfiles `test_pi_agent_watch` stdout-stream race. No current `agent-harness` code path should be changed for #26 without a new, specific local acceptance target.

## Recommended Execution Order

1. Implement #39 first. It is narrow, safety-relevant, and unblocks trustworthy train results.
2. Execute `specs/phase-plans-v4.md` for #36/#135/model-routing-v3. It is the strategic work and should proceed through governed phases.
3. Implement #33 after or alongside the panel roadmap only if ownership stays clearly separate from panel leg execution changes.
4. Track #26 follow-ups in their owning repos if they become priority work.

