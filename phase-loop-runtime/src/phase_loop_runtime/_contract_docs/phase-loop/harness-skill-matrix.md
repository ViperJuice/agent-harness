# Phase-Loop Harness Skill Matrix

This document is the SKILLPACK manifest and canonical skill namespace contract
for dotfiles-hosted phase-loop workflow skills. It freezes the active harness
workflow names, source roots, and recommended install roots before bootstrap,
sync, cleanup, parity, and bridge-soak phases change install behavior or remove
vestigial paths.
The path inventory for the public harness substrate is
`docs/phase-loop/harness-substrate-manifest.md`; this matrix owns workflow
names only.

Harness workflow skill names follow:

`<harness>-<workflow>`

The rule applies to harness-local workflow skills for Codex, Claude Code,
Gemini CLI, and OpenCode. Pi Agent role-style skills and governed-pipeline
`.pipeline/skills/**` are explicit namespace exceptions.

## Canonical Workflow Skills

| Workflow | Codex | Claude Code | Gemini CLI | OpenCode |
| --- | --- | --- | --- | --- |
| advisor-panel | `codex-advisor-panel` | `claude-advisor-panel` | `gemini-advisor-panel` | `opencode-advisor-panel` |
| phase-roadmap-builder | `codex-phase-roadmap-builder` | `claude-phase-roadmap-builder` | `gemini-phase-roadmap-builder` | `opencode-phase-roadmap-builder` |
| plan-phase | `codex-plan-phase` | `claude-plan-phase` | `gemini-plan-phase` | `opencode-plan-phase` |
| execute-phase | `codex-execute-phase` | `claude-execute-phase` | `gemini-execute-phase` | `opencode-execute-phase` |
| phase-loop | `codex-phase-loop` | `claude-phase-loop` | `gemini-phase-loop` | `opencode-phase-loop` |
| plan-detailed | `codex-plan-detailed` | `claude-plan-detailed` | `gemini-plan-detailed` | `opencode-plan-detailed` |
| execute-detailed | `codex-execute-detailed` | `claude-execute-detailed` | `gemini-execute-detailed` | `opencode-execute-detailed` |
| task-contextualizer | `codex-task-contextualizer` | `claude-task-contextualizer` | `gemini-task-contextualizer` | `opencode-task-contextualizer` |
| skill-improvement-planner | `codex-skill-improvement-planner` | `claude-skill-improvement-planner` | `gemini-skill-improvement-planner` | `opencode-skill-improvement-planner` |
| skill-editor | `codex-skill-editor` | `claude-skill-editor` | `gemini-skill-editor` | `opencode-skill-editor` |
| run-train | `codex-run-train` | `claude-run-train` | `gemini-run-train` | `opencode-run-train` |

Canonical source roots (CANON / IF-0-CANON-1 — the authored skill *sources* now
live in this repo under `skills-src/<harness>/`; the dotfiles `*-config` roots
remain valid build inputs during the cutover via an explicit `--source`):

- Codex: `skills-src/codex/**`
- Claude Code: `skills-src/claude/**`
- Gemini CLI: `skills-src/gemini/**`
- OpenCode: `skills-src/opencode/**`

Recommended install roots:

- Codex: `~/.codex/skills`
- Claude Code: `~/.claude/skills`
- Gemini CLI: `~/.gemini/skills`
- OpenCode: `~/.config/opencode/skills`

## Pi Agent Exception

Pi Agent uses role-style skills rather than the `<harness>-<workflow>` harness
matrix. The intentional exceptions are:

- `phase-loop-supervisor`
- `phase-loop-repair`
- `phase-loop-closeout`

These names are allowed because Pi Agent consumes repo-local
`phase-loop-pi/**` prompts, skills, extensions, and `pi-config/**` installation
metadata as an adapter role surface. They are not an unnormalized harness
workflow family.

## Route Compatibility

Direct Codex, direct Gemini, and direct OpenCode launcher routes remain
compatibility-supported during this roadmap. This contract normalizes workflow
skill names and metadata; it does not collapse those routes into Pi Agent.

Claude Code execution continues to use the first-party non-interactive
`claude -p` path. Claude Code bridge and workflow skills describe that route;
they do not imply Anthropic API-key execution or Pi provider fallback.

## Vestigial Workflow Candidates

Historical unprefixed `claude-config/skills/plan-phase/**` and
`claude-config/skills/execute-phase/**` are vestigial workflow candidates.
Current inventory shows handoff and reflection residue under those paths, not
active `SKILL.md` definitions. They are not deleted, archived, or promoted by
DFSKILLCONTRACT.

Later cleanup may classify those paths as archived history or remove them only
after source lookup, bootstrap, sync-skills, expected skill packs, docs, and
downstream governed-pipeline compatibility proof agree.

DFSKILLCLEAN records the current cleanup classifications and canonical
replacement names in `docs/phase-loop/legacy-skill-cleanup.md`.

DFSKILLGOVSOAK records the governed bridge compatibility release gate in
`docs/phase-loop/dfskillgovsoak.md`, including governed-pipeline mirrored
scenarios, dotfiles-only scenarios, temporary legacy alias rejection coverage,
and the optional live adapter proof boundary.

## Downstream Namespace Boundary

Governed-pipeline `.pipeline/skills/**` is a separate product/runtime
namespace. Those skills are governed-pipeline-owned pipeline overlays, not
dotfiles harness workflow skills, and dotfiles normalization must not rewrite,
rename, install, or validate them as `<harness>-<workflow>` skills.

Dotfiles closeout and launch metadata may mention canonical harness workflow
skill names as bridge vocabulary, expected skill pack metadata, or next-action
metadata. Governed-pipeline remains responsible for `.pipeline/skills/**`
manifest policy, source-bundle emission, closeout ingest, mirror updates,
canonical refresh, replan, and projection decisions.
Downstream consumers must not treat Host bootstrap, Shell config, SSH setup,
MCP gateway setup, generic 1Password setup, raw/private evidence, provider
payloads, or local environment values as prerequisites for these workflow skill
names.

## DFSKILLCONTRACT Gates

- IF-0-DFSKILLCONTRACT-1: this matrix freezes the active
  `<harness>-<workflow>` names and the Pi role-style exception.
- IF-0-DFSKILLCONTRACT-2: governed-pipeline `.pipeline/skills/**` is a
  separate namespace that dotfiles normalization must not rewrite.
- IF-0-DFSKILLCONTRACT-3: historical unprefixed `plan-phase` and
  `execute-phase` paths are vestigial workflow candidates for later cleanup.
- IF-0-DFSKILLCONTRACT-4: direct Codex, direct Gemini, and direct OpenCode
  routes remain compatibility-supported, and Claude Code continues through
  `claude -p`.
- IF-0-SKILLPACK-1: this SKILLPACK manifest freezes the active workflow skill
  names and install targets for Codex, Claude Code, Gemini, and OpenCode.
