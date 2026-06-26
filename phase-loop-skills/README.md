# phase-loop-skills

The harness-neutral workflow-skills bundle for the phase-loop runtime. Each top-level
directory is one workflow skill, authored once and installed with a per-harness prefix
(`claude-`, `codex-`, `gemini-`, `opencode-`) into that harness's skill root.

## The skills

| Skill | What it does |
|---|---|
| `phase-roadmap-builder` | Turn a plan/conversation into a phased `specs/phase-plans-v<N>.md` roadmap |
| `plan-phase` | Architect one phase into parallel swim-lanes with frozen interfaces |
| `execute-phase` | Run a phase's lanes to completion (parallel, auto-merge on green) |
| `plan-detailed` | Plan a single bounded change, no roadmap overhead |
| `phase-loop` | Drive the roadmap → plan → execute loop end-to-end |
| `skill-editor` | Author/edit a skill |
| `skill-improvement-planner` | Plan improvements to a skill from reflections |

## Layout

```
phase-loop-skills/
  <skill>/
    SKILL.md                    # the base (harness-neutral) skill
    _overrides/<harness>/       # optional per-harness overlay (claude|codex|gemini|opencode)
```

The base `SKILL.md` is shared; `_overrides/<harness>/` files replace or augment it for a
specific harness at install time.

## Install

Use the runtime's installer (it resolves the per-harness prefix + skill root):

```sh
phase-loop install --harness claude --source <path-to>/phase-loop-skills --symlink --dry-run
phase-loop install --harness claude --source <path-to>/phase-loop-skills --symlink --apply
```

Or just run the repo's `install-agent-harness.sh --harness <h>`, which installs the
runtime and these skills together. Default skill roots: `~/.claude/skills`,
`~/.codex/skills`, `~/.gemini/skills`, `~/.config/opencode/skills`.
