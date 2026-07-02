# agent-harness - Cross-Vendor Advisor Panel Ownership And Routing - Phase Plan v4

> How to use this document: run `phase-loop validate-roadmap specs/phase-plans-v4.md`, then plan each phase with the phase-loop command for the phase alias.

---

## Context

This roadmap covers the combined work from `agent-harness` #36, `dotfiles` #135, and the model-routing-v3 follow-up. The goal is to make `agent-harness` the source of truth for the advisor-panel runtime primitive and skill surface, while dotfiles keeps only redacted install/bootstrap glue.

Initial implementation research found that `phase_loop_runtime.panel_invoker` existed, but its real panel path still had the failure modes called out by the issues: static 600 second timeouts, Gemini relying on `--add-dir` instead of an explicit staged-file prompt, a hard-coded unavailable Claude leg, and no `agent-harness`-owned `advisor-panel` skill in the canonical skill source tree.

Current external model source note: Anthropic's model documentation lists Claude Sonnet 5 with Claude API ID `claude-sonnet-5`, and Claude Code model configuration documentation says Sonnet 5 requires Claude Code `v2.1.197` or later. This roadmap treats Sonnet-family Claude panel execution as Sonnet 5 unless a compatibility phase explicitly records a temporary downgrade.

The work is roadmap-sized because it spans runtime interfaces, CLI feeding semantics, model routing, skill packaging, native Claude behavior, and dotfiles redaction.

## Top Interface-Freeze Gates

1. **IF-0-PNLFOUND-1** - Panel request/result contract: review artifact shape, per-leg status vocabulary, timeout metadata, redacted evidence, and degraded result handling.
2. **IF-0-PNLFOUND-2** - Model-routing-v3 policy: Claude Sonnet 5 as the Sonnet model, Gemini 3.5 Flash eligibility, and max-effort planner-of-record restrictions.
3. **IF-0-PNLFEED-3** - CLI prompt feeding contract: every CLI leg receives an explicit compact prompt that points to staged review material; no leg relies on implicit directory reading or embeds the artifact body in the prompt.
4. **IF-0-PNLSKILL-4** - Canonical advisor-panel skill source layout under `agent-harness`.
5. **IF-0-PNLREDACT-5** - Dotfiles cutover contract showing dotfiles no longer owns or installs an independent advisor-panel implementation.
6. **IF-0-PNLVERIFY-6** - Release verification matrix covering runtime, skill bundle parity, dotfiles cutover, and live panel smoke tests.

## Phase Dependency DAG

```
PNLFOUND
  |
  v
PNLFEED
  |
  v
PNLCLAUDE
  |
  v
PNLSKILL
  |
  v
PNLREDACT
  |
  v
PNLVERIFY
```

Strict serial order is intentional. The phases touch shared panel/routing/skill ownership seams, and dotfiles redaction must wait until the `agent-harness` source is packaged and verified.

## Phases

### Phase 1 — Panel Contract And Routing Baseline (PNLFOUND)

**Objective**
Freeze the panel contract and model-routing-v3 policy before changing execution behavior.

**Exit criteria**
- [ ] `PanelRequest`/`PanelResult` or equivalent runtime structures document artifact references, per-leg status, timeout metadata, degraded evidence, and redaction rules.
- [ ] Panel statuses are explicitly limited to `OK`, `EMPTY`, `TIMEOUT`, `ERROR`, `DEGRADED`, and `UNAVAILABLE`, or any addition is documented in `IF-0-PNLFOUND-1`.
- [ ] Timeout policy is input-scaled and leg-specific, with a bounded high-effort path capable of approximately 1200-1800 second reviews.
- [ ] Model-routing-v3 names `claude-sonnet-5` as the Sonnet-family Claude model and verifies Claude Code `v2.1.197` or later before selecting it through Claude Code.
- [ ] Model-routing-v3 names Gemini 3.5 Flash where eligible and keeps Gemini out of max-effort planner-of-record roles unless a later validated policy changes that.
- [ ] Tests assert no panel leg is silently dropped and no unknown status is accepted.

**Scope notes**
Decompose into three lanes: runtime contract lane owns `panel_invoker` data/status shape, routing lane owns model policy changes in profiles/capability registry/launcher mapping, and test lane owns contract drift tests. These lanes are disjoint until the final interface freeze.

**Non-goals**
No real CLI prompt-feeding change in this phase; no dotfiles edits; no native Claude leg implementation beyond routing contract decisions.

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py
- phase-loop-runtime/src/phase_loop_runtime/profiles.py
- phase-loop-runtime/src/phase_loop_runtime/capability_registry.py
- phase-loop-runtime/src/phase_loop_runtime/launcher.py
- phase-loop-runtime/tests/test_panel_invoker_spawn.py
- phase-loop-runtime/tests/test_phase_loop_launcher.py
- docs/research/model-routing-v2-integration.md

**Depends on**
- (none)

**Produces**
- IF-0-PNLFOUND-1
- IF-0-PNLFOUND-2

### Phase 2 — Staged Artifact Prompting And CLI Leg Execution (PNLFEED)

**Objective**
Fix the panel leg failure mode by ensuring Codex and Gemini receive explicit prompts that point to staged review artifact files, not oversized embedded artifact bodies or implicit directory hints.

**Exit criteria**
- [ ] Codex panel tests prove command input references staged review files and does not embed the artifact body.
- [ ] Gemini panel tests prove command input references staged review files and does not embed the artifact body.
- [ ] Gemini no longer depends on `--add-dir` as the only way to see review material.
- [ ] All CLI subprocess paths set prompt input intentionally and close stdin when no input is intended.
- [ ] Empty CLI output is classified as `EMPTY` or `DEGRADED`; it is never treated as a successful review.
- [ ] Timeout statuses include elapsed time and configured timeout without leaking secrets.

**Scope notes**
Decompose into four lanes: Codex leg feeding, Gemini leg feeding, status classification, and large-input threshold handling. `panel_invoker.py` is the integrator file, but launcher command builders can be reused where practical.

**Non-goals**
No Claude native leg in this phase; no skill packaging; no dotfiles redaction.

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py
- phase-loop-runtime/src/phase_loop_runtime/launcher.py
- phase-loop-runtime/tests/test_panel_invoker_spawn.py
- phase-loop-runtime/tests/test_phase_loop_launcher.py

**Depends on**
- PNLFOUND

**Produces**
- IF-0-PNLFEED-3

### Phase 3 — Repo-Grounded Claude Leg And Whole-Feature Review (PNLCLAUDE)

**Objective**
Add a real Claude review leg that verifies repository state and reviews the whole feature, without falling back to unsupported or unauthenticated `claude -p` behavior.

**Exit criteria**
- [ ] Claude leg routes Sonnet-family requests to Claude Sonnet 5.
- [ ] Claude Code version below `v2.1.197` records `UNAVAILABLE` or `DEGRADED`, not a silent fallback to older Sonnet behavior.
- [ ] Claude leg is no longer hard-coded unavailable when the supported native path is available.
- [ ] Claude writes its review to a deterministic scratch output file for durable ingestion.
- [ ] Tests prove author/reviewer boundary remains visible and the panel can degrade when Claude is unavailable.
- [ ] Review prompts request repo-grounded, whole-feature, integration-oriented findings.
- [ ] No implementation requires API-key auth or unsupported headless `claude -p` behavior.

**Scope notes**
Decompose into three lanes: native Claude availability/execution lane, Sonnet 5/version-gate lane, and whole-feature review prompt/evidence lane. These lanes share only the final panel result status mapping.

**Non-goals**
No dotfiles cutover in this phase; no new provider beyond Claude/Codex/Gemini; no API-key based Claude path.

**Key files**
- phase-loop-runtime/src/phase_loop_runtime/panel_invoker.py
- phase-loop-runtime/src/phase_loop_runtime/launcher.py
- phase-loop-runtime/tests/test_panel_invoker_spawn.py
- docs/phase-loop/skills-canonical-source.md

**Depends on**
- PNLFEED

**Produces**
- (none)

### Phase 4 — Source-First Advisor Panel Skill Bundle (PNLSKILL)

**Objective**
Move the advisor-panel skill surface into `agent-harness` canonical skill source and keep the skill thin over the runtime primitive.

**Exit criteria**
- [ ] `agent-harness` contains canonical advisor-panel skill source.
- [ ] Packaged advisor-panel skill tells agents to use the runtime primitive or phase-loop command and avoids duplicating dotfiles scripts.
- [ ] Regenerated skill bundle artifacts are deterministic.
- [ ] Skill bundle parity tests cover advisor-panel source and packaged output.
- [ ] Canonical skill source documentation names advisor-panel ownership explicitly.

**Scope notes**
Decompose into four lanes: canonical skill source lane, runtime-primitive wording lane, bundle regeneration lane, and parity/doc lane. The lane boundary keeps prose changes separate from generated bundle sync.

**Non-goals**
No runtime panel execution changes beyond skill references; no dotfiles deletion until the next phase.

**Key files**
- skills-src/*/advisor-panel/SKILL.md
- phase-loop-skills/*/advisor-panel/SKILL.md
- phase-loop-runtime/src/phase_loop_runtime/skills_bundle/**
- phase-loop-runtime/scripts/regenerate_skills_bundle.py
- phase-loop-runtime/scripts/sync_skills_bundle.py
- phase-loop-runtime/tests/test_skills_canon_parity.py
- docs/phase-loop/skills-canonical-source.md

**Depends on**
- PNLCLAUDE

**Produces**
- IF-0-PNLSKILL-4

### Phase 5 — Dotfiles Redaction And Fleet Cutover (PNLREDACT)

**Objective**
Remove dotfiles as an independent advisor-panel implementation while preserving install/update behavior.

**Exit criteria**
- [ ] Dotfiles no longer owns a standalone advisor-panel implementation.
- [ ] Dotfiles bootstrap installs or exposes advisor-panel from the `agent-harness` source of truth.
- [ ] Existing skill roots avoid double-installing conflicting advisor-panel copies.
- [ ] Dotfiles #135 has a reproducer or smoke test proving Gemini and Codex receive compact prompts that point to staged review files.
- [ ] No secrets or local auth payloads are added to either repository.

**Scope notes**
Decompose into four lanes: dotfiles redaction, bootstrap/install wiring, compatibility checks across existing skill roots, and issue-smoke evidence. This is intentionally cross-repo and must not start until `PNLSKILL` produces a packaged source of truth.

**Non-goals**
No new agent-harness runtime behavior; no deletion of unrelated dotfiles shared skills; no credential migration.

**Key files**
- /mnt/HC_Volume_105438154/code/dotfiles/shared/skills/advisor-panel/SKILL.md
- /mnt/HC_Volume_105438154/code/dotfiles/shared/skills/advisor-panel/scripts/run_cli_panels.sh
- /mnt/HC_Volume_105438154/code/dotfiles/install.sh
- /mnt/HC_Volume_105438154/code/dotfiles/bootstrap.sh

**Depends on**
- PNLSKILL

**Produces**
- IF-0-PNLREDACT-5

### Phase 6 — Live Default Verification And Release Closure (PNLVERIFY)

**Objective**
Prove the owned advisor-panel flow works end to end and close the roadmap with release-quality evidence.

**Exit criteria**
- [ ] Focused panel, launcher, routing, and skill parity tests pass.
- [ ] Full runtime suite passes.
- [ ] A real smoke panel proves staged review files are visible to Codex and Gemini through outputs or structured status evidence.
- [ ] Dotfiles cutover evidence proves no divergent advisor-panel implementation remains installed from dotfiles.
- [ ] Issues #36 and #135 can be updated with concrete verification evidence.

**Scope notes**
Decompose into four lanes: runtime suite verification, live panel smoke, dotfiles installed-skill smoke, and issue/documentation closeout. This phase owns evidence aggregation and does not introduce new runtime behavior.

**Non-goals**
No new feature work; no new model routing decision beyond validating the gates produced earlier.

**Key files**
- phase-loop-runtime/tests/test_panel_invoker_spawn.py
- phase-loop-runtime/tests/test_phase_loop_launcher.py
- phase-loop-runtime/tests/test_skills_canon_parity.py
- docs/phase-loop/skills-canonical-source.md
- README.md

**Depends on**
- PNLREDACT

**Produces**
- IF-0-PNLVERIFY-6

## Execution Notes

- Plan each phase with the phase-loop command using the alias: `PNLFOUND`, `PNLFEED`, `PNLCLAUDE`, `PNLSKILL`, `PNLREDACT`, then `PNLVERIFY`.
- Keep the phase order serial. `PNLFEED` depends on the interface shape from `PNLFOUND`; `PNLCLAUDE` depends on stable leg feeding/status behavior; `PNLSKILL` depends on the runtime primitive; `PNLREDACT` depends on packaged skill source; `PNLVERIFY` depends on both repositories being updated.
- Treat `panel_invoker.py`, model-routing policy files, and skill bundle generation as single-writer regions within their owning phases.
- Do not close dotfiles #135 until the agent-harness-owned panel path has live or smoke evidence that Codex and Gemini can inspect the staged review artifact files.

## Acceptance Criteria

- [ ] `agent-harness` owns the runtime panel primitive and advisor-panel skill source.
- [ ] Codex and Gemini panel legs receive compact prompts that point to staged review artifacts, not implicit directory reads or embedded artifact bodies.
- [ ] Claude Sonnet-family panel execution uses Claude Sonnet 5 with a visible Claude Code version gate.
- [ ] Dotfiles no longer installs a divergent advisor-panel implementation.
- [ ] Empty, unavailable, timeout, degraded, and successful leg results remain distinct in structured evidence.
- [ ] Roadmap validation and the final runtime verification matrix pass.

## Verification

```bash
# Roadmap validation
PYTHONPATH=phase-loop-runtime/src python -m phase_loop_runtime.cli validate-roadmap specs/phase-plans-v4.md

# Runtime panel/routing focus after implementation
cd phase-loop-runtime
PYTHONPATH=src python -m pytest tests/test_panel_invoker_spawn.py tests/test_phase_loop_launcher.py tests/test_skills_canon_parity.py -q

# Full runtime suite after implementation
PYTHONPATH=src python -m pytest -q
```
