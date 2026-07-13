# Goal-fidelity vs. diagnostics: anatomy of a multi-day phase-loop thrash

**Status:** analysis / decision record
**Date:** 2026-07-13
**Motivating case:** a downstream repo's browser-driven two-avatar staging proof ("DUALPROOF") that a `codex exec` executor thrashed on for ~3 days — fix-one-bug / fail-the-next-stage, ~6 cycles, no convergence.
**Question investigated:** *Did a "goal" mechanism — the Codex-native goal feature, or a harness-synthesized objective that drifted from the authored roadmap — cause the thrash?*
**Verdict:** **No.** All three framings of the "goals interfere" hypothesis are disconfirmed as the cause. The thrash was **infrastructure failure + scrubbed diagnostics**. Chasing it surfaced two genuine, *distinct*, *latent* framework gaps — filed as **#209** (diagnostics) and **#211** (acceptance-coverage fidelity) — neither of which fired as the cause here.

This document is the consolidated evidence behind both issues so they share one source of truth.

---

## 1. The three hypotheses, and why each is disconfirmed

The operator's intuition — "the harness seems to define its own goals and that may interfere if not perfectly aligned with the roadmap and phase plan" — was reframed three ways. Each was tested against primary sources.

### H1 — The Codex-native "goal" feature is injected into our executors

**Disconfirmed.** The Codex-native goal feature is TUI/Desktop-only (`config.toml [features] goals=true`, `goals_1.sqlite`, injected as `<codex_internal_context source="goal">`). It never reaches a headless `codex exec` executor. Session-store evidence: **1726 headless `codex_exec` executor sessions with 0 goal injections** vs. 35 Desktop-originated sessions. The phase-loop adapter drives executors via non-interactive `codex exec {plan}` with no goal argument. Not a factor.

### H2 — The harness synthesizes an objective that silently drifts from the roadmap

**Disconfirmed *as the cause of this thrash*, but points at a real latent gap (see §3).** Empirically, for DUALPROOF the phase-plan copied the roadmap exit-criteria **near-verbatim** into its Acceptance Criteria (both participants publish audio *and* video, exact track attribution, live-provider not fake-media, notes provenance held for review, metadata-only evidence). No loosening occurred. The executor honestly recorded a `blocked` acceptance-gap rather than claiming a false pass. The target was correct; the agent was chasing the right thing.

### H3 — This is the same class as the known literal-drift audit

**Disconfirmed — wrong axis.** `phase-loop-drift-audit.py` audits closeout **enum literals** (`blocker_class`, `verification_status`) against frozen allowlists — a **syntactic** check. Acceptance-coverage drift is a **semantic** axis that audit does not touch. (The #211 fix is proposed as a *sibling* static audit, not an extension of the literal audit's mechanism.)

---

## 2. What actually caused the thrash

Two compounding, mundane causes — no goal mechanism involved:

1. **Infrastructure fault (the blocker):** the persistent-session avatar runner task wedged after a single swallowed STT-session failure. Post-wedge it still accepted dispatches and burned CPU, but every subsequent session hung at "running" forever and its logging went dead. Runs *before* the wedge joined; every run *after* was silently stuck. A single unhandled failure inside a persistent-session task took down the whole task with no surfaced signal.

2. **Scrubbed diagnostics (the blindfold):** the failure that wedged the task was swallowed by a bare `catch {}` and re-emitted as a generic `browser_launch_failed`. Reports were metadata-only, each run a ~4-minute all-or-nothing infra chain. The agent could see *that* a run failed but never *which layer* failed — signaling/chat/data-channel succeeded (and masqueraded as media-OK) while the media layer (decoded video + non-silent audio, which acceptance actually requires) never published. Every adjacent plumbing fix advanced exactly one stage, so the next blind run failed one stage later — the fix-N/fail-N+1 signature.

**Had raw failure diagnostics been preserved, this was a one-run localization instead of three blind days.** That is precisely the #209 gap.

---

## 3. The fidelity trace — where a goal *could* drift, and why nothing catches it

There is exactly **one** synthesis hop in the loop, and it is by design:

| Hop | Behavior | Fidelity binding |
|---|---|---|
| roadmap → plan | **Paraphrase (by design).** `skills-src/*/plan-phase/SKILL.md`: acceptance criteria "copied **or refined** from the roadmap exit criteria." Refinement is legitimate — prose criteria must become checkable. | **None checks the refinement for coverage.** |
| plan → executor | **Verbatim.** `injection.py` hands off `codex-execute-phase {plan}` by file path. No runtime re-synthesis. | File identity. |
| executor → closeout | Re-synthesizes nothing, **but binds nothing back to the roadmap.** `CloseoutContext` = `{plan_path, terminal, changed_paths}`; no validator reads acceptance/exit-criteria; `EmitPhaseCloseout` receives only `{phase_alias, plan_produces (IF-gate literals), plan_owned_files, sha}`. | IF-gate literal presence + verification exit codes + evidence authenticity. **Not** acceptance-to-criteria semantics. |

**The load-bearing finding:** at closeout, when a synthesized objective and the roadmap criteria disagree, **neither "wins" — because there is no comparison.** The runtime closeout gate never loads the roadmap exit-criteria at all. So the **plan's planner-authored acceptance is the de-facto authority**, and the roadmap is bound only by **file hash** (`roadmap_sha256` = staleness detection). A faithful copy and a loosened paraphrase are **indistinguishable to every runtime gate** — they hash-anchor identically. That absence of a comparison *is* the gap.

---

## 4. Two failure directions — different axes

The clean mental model that separates the two issues:

- **Direction A — FALSE-GREEN** (a looser paraphrase reads as pass): the planner drops "non-silent audio" → "audio track present"; the executor produces **authentic** evidence for the *weaker* target; honest pass; roadmap criterion silently unmet. Not caught by authenticity checks (evidence is real) or by diagnostics (nothing failed). **Structurally possible; NOT observed in DUALPROOF.** → **#211**.

- **Direction B — NEVER-SATISFIED / almost-passing** (the target is correct-and-strict; the agent just can't localize *which layer* failed): what DUALPROOF actually was. Upper-layer success masquerades as progress; scrubbed diagnostics prevent localization. → **#209**.

DUALPROOF = **Direction B**. The target-fidelity gap (Direction A / #211) is real but **latent** — it did not fire here.

---

## 5. Recommended fixes (both filed)

- **#209 — Preserve raw failure diagnostics on verification failure.** Localize the failing stage in a multi-stage proof rather than gating only the terminal artifact. Add a typed `failure_kind` (timeout vs. fault) and surface the raw, bounded error at the swallow points. This is the fix that would have prevented *this* thrash.

- **#211 — Static acceptance-coverage audit.** Compare a plan's acceptance/verification against the roadmap exit-criteria of the phase it is anchored to (`roadmap` + `roadmap_sha256` already identify the source section) and flag dropped / weakened / renamed criteria. Static, no runtime truth, no executor cooperation. A sibling to the existing literal-drift audit, on the semantic axis it doesn't cover. Guards Direction A.

**Net ledger:** codex-native-goal = clean **no**; synthesized-goal-drift = real but **latent** (#211); the DUALPROOF thrash itself = **blind diagnosis** (#209). No third issue needed — #209 + #211 cover both directions.
