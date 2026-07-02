# Canonical phase-loop skill source (IF-0-CANON-1)

This note declares the **single canonical source** for the phase-loop workflow
skill pack and the **one-command regenerate** that produces the committed bundle
from it. It satisfies interface-freeze gate **IF-0-CANON-1** of the v50 skills
cutover (decision **A — agent-harness owns the skill sources**).

## Canonical source location

The authored skill *sources* live in this repository at:

```
skills-src/
  claude/    claude-<skill>/   (SKILL.md + scripts/ references/ assets/)
  codex/     codex-<skill>/
  gemini/    gemini-<skill>/
  opencode/  opencode-<skill>/
```

One tree per active harness (`claude`, `codex`, `gemini`, `opencode`), each
holding the per-harness `<harness>-<skill>/` directories that `build_bundle`
consumes. These roots are the values of
`phase_loop_runtime.build_bundle.DEFAULT_SOURCES`. The tree carries only the
build-relevant inputs — `SKILL.md` plus the harness-agnostic aux subdirs
(`scripts/`, `references/`, `assets/`); runtime state directories (`handoffs/`,
`reflections/`, `agents/`) are intentionally NOT vendored, because `build_bundle`
never reads them.

The scoped skill set is exactly the phase-loop workflow pack
(`REQUIRED_SKILLS`): `advisor-panel`, `plan-phase`, `execute-phase`,
`phase-roadmap-builder`, `phase-loop`, `plan-detailed`, `execute-detailed`,
`skill-editor`, `skill-improvement-planner`, `task-contextualizer`.
The `advisor-panel` skill is source-first in this repository and stays thin over
`phase_loop_runtime.panel_invoker`; dotfiles may install or expose it during
cutover, but dotfiles does not own a divergent advisor-panel implementation.

## The bundle pipeline

```
skills-src/<harness>/            authored canonical source (IF-0-CANON-1)
    │  build_bundle  (neutral base from codex + per-harness _overrides)
    ▼
phase-loop-skills/               committed bundle, parity-gated (IF-0-CANON-2)
    │  scripts/sync_skills_bundle.py
    ▼
src/phase_loop_runtime/skills_bundle/   ships in the wheel
```

## Provenance of the committed bundle

`phase-loop-skills/` is **generated, not hand-edited**. It is produced by
`build_bundle(skills-src/)` via the regenerate command below. The hand-authored
`phase-loop-skills/README.md` is the one exception — it is a bundle index, not
`build_bundle` output, and is excluded from the parity comparison.

## Regenerate (one command)

After editing anything under `skills-src/`:

```sh
python phase-loop-runtime/scripts/regenerate_skills_bundle.py   # skills-src/ -> phase-loop-skills/
python phase-loop-runtime/scripts/sync_skills_bundle.py         # phase-loop-skills/ -> packaged skills_bundle/
```

Both steps are self-contained — no dotfiles checkout required.

## Parity gate (IF-0-CANON-2)

`phase-loop-runtime/tests/test_skills_canon_parity.py` (and the
`.github/workflows/skills-parity.yml` job) fail the build when the committed
`phase-loop-skills/` is not byte-identical to a fresh
`build_bundle(skills-src/)`. The downstream `phase-loop-skills/` ->
`skills_bundle/` half stays guarded by `test_skills_bundle_drift.py`.

## Cutover status (transitional)

During the v50 cutover the dotfiles `*-config` skill roots
(`claude-config/claude-skills/`, `codex-config/skills/`, `gemini-config/skills/`,
`opencode-config/skills/`) remain valid **build inputs**: the fleet `bootstrap.sh`
still rebuilds the bundle live from them via an explicit
`phase-loop build-bundle --source <dotfiles-root> …`. That path is left working
on purpose. Repointing the fleet consumer to the package bundle is Phase 2
(SINGLESRC); deleting the dotfiles roots is Phase 3 (REMOVE).
