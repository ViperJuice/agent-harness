---
name: claude-skill-improvement-planner
description: "Claude Code skill feedback aggregator. Reads workflow skill reflections, groups recurring recommendations, and writes an improvement plan for claude-skill-editor."
---

# claude-skill-improvement-planner

## Runtime State

For reflections, handoffs, and latest handoff pointers, follow `claude-config/shared/runtime-state.md`. This repo/branch/run-isolated contract supersedes any older flat closeout examples retained for historical context in this skill.

Reads reflection files produced by the planning-chain skills' close-out steps — plus reflections emitted by the meta-skills (`claude-skill-improvement-planner`, `claude-skill-editor`) themselves — aggregates recurring themes across runs, and writes an improvement plan. Does not edit skills. A separate `claude-skill-editor` skill ingests the plan and performs the edits. Including the meta-skills' own reflections closes the self-improvement loop so this planner and the editor can be iterated on with the same pipeline they drive.

## Pipeline

Claude meta-skill source changes move through three tiers:

1. Canonical source: `claude-config/claude-skills/claude-<skill>/SKILL.md`.
2. Harness-neutral bundle: `vendor/phase-loop-skills/<bare-skill>/SKILL.md`, currently bundle-derived-from-codex.
3. Installed runtime roots: `~/.claude/skills/`, `~/.codex/skills/`, `~/.gemini/skills/`, and `~/.opencode/skills/`.

SP-DOC edits only the canonical source tier. Leave the neutral bundle and installed runtime roots stale until the end-of-v36 cutover. The cutover regenerates the bundle from the codex-derived source path and runs `./bootstrap.sh`, which installs the bundle with `python3 -m phase_loop_runtime.cli install --source vendor/phase-loop-skills --symlink --apply`.

## When to use

- The user wants to audit accumulated reflections and decide what to change.
- Several phases have executed; reflections have built up at `resolve_skill_bundle_root("claude")/<skill>/reflections/`.
- The user asks about updating skills based on past runs.

## When NOT to use

- User wants to edit a skill directly — they want `/claude-skill-editor` (once it exists) or manual edits.
- No reflections exist yet — the skill will exit with a user-facing message.

## Inputs

| Arg | Required | Meaning |
|---|---|---|
| `--target <skill-name>` | no | Plan only for one skill; skip the rest. Default: all five skills (the three pipeline skills plus the two meta-skills). |
| `--min-reflections <N>` | no | Default 2. Skip skills with fewer new (un-archived) reflections to avoid acting on noise. |
| `--output <path>` | no | Override the generated plan path. |

## Workflow

### Step 1 — Enumerate reflections

Glob these paths, excluding any `archive/` subdirectory. For each skill, scan both the Claude runtime reflection root and the codex-derived source-controlled reflection root:

- `resolve_skill_bundle_root("claude")/claude-phase-roadmap-builder/reflections/**`
- `resolve_skill_bundle_root("codex")/claude-phase-roadmap-builder/reflections/**`
- `resolve_skill_bundle_root("claude")/claude-plan-phase/reflections/**`
- `resolve_skill_bundle_root("codex")/claude-plan-phase/reflections/**`
- `resolve_skill_bundle_root("claude")/claude-execute-phase/reflections/**`
- `resolve_skill_bundle_root("codex")/claude-execute-phase/reflections/**`
- `resolve_skill_bundle_root("claude")/claude-skill-improvement-planner/reflections/**`
- `resolve_skill_bundle_root("codex")/claude-skill-improvement-planner/reflections/**`
- `resolve_skill_bundle_root("claude")/claude-skill-editor/reflections/**`
- `resolve_skill_bundle_root("codex")/claude-skill-editor/reflections/**`

The last two close the self-improvement loop: this planner and the editor write reflections on their own runs, and those reflections must be aggregated here or the meta-skills can never be improved by their own pipeline. A missing `reflections/` directory for either meta-skill is not an error — they materialize lazily on first close-out.

If `--target <skill>` is set, limit to that one skill.

### Step 2 — Parse each reflection

For each file:

- **Skill name**: parent directory's parent name.
- **Version**: extract from filename (`<skill>-reflection-v(\d+)\.md`); fall back to mtime for non-standard filenames.
- **Body**: read; extract the `## What worked` and `## Improvements to SKILL.md` sections. If those headings are absent (older or hand-named reflection), keep the raw body and tag as `unstructured`.

### Step 3 — Gate on minimum

- Total reflections = 0 → print "No reflections to aggregate. Reflections not yet written at `resolve_skill_bundle_root("claude")/<skill>/reflections/`, or all are archived." Exit 0.
- Per skill: new reflections < `--min-reflections` → skip that skill; note in the plan summary.

### Step 4 — Aggregate via frontier-tier Agent

Resolve the `frontier` tier from `claude-execute-phase`'s Model tiers table. Spawn one Agent:

```
Agent(
  subagent_type: "general-purpose",
  model: "<frontier-model-id>",
  name: "skill-improvement-aggregator",
  prompt: <contents of assets/aggregator_prompt.md>
        + "\n\n# Reflections to aggregate\n\n" + <concatenated reflection bodies, grouped by skill, with version tags>
)
```

The aggregator prompt (in `assets/aggregator_prompt.md`) instructs the Agent to:

- Identify recurring themes (≥ `--min-reflections` distinct reflections per skill, or ≥ 2 across skills for cross-cutting).
- Separate high-confidence actionable from speculative one-offs.
- Flag contradictions.
- Propose concrete SKILL.md edits in directive-only style.
- Enforce repo-agnostic output — reject or rewrite any recommendation that names a specific project, codebase, domain, or filename.
- Cite supporting reflection versions per theme.

### Step 5 — Write the plan file

Resolve the next plan path:

```bash
N=$(ls resolve_skill_bundle_root("claude")/claude-skill-improvement-planner/plans/ 2>/dev/null | grep -c '^plan-v')
PLAN_PATH=resolve_skill_bundle_root("claude")/claude-skill-improvement-planner/plans/plan-v$((N+1))-$(date -u +%Y%m%dT%H%M%SZ).md
```

Write the plan using the template in `## Plan file format` below. The frontmatter's `reflections_consumed` field must list absolute paths to every reflection that was read — this is how the downstream claude-skill-editor knows what to archive.

### Step 6 — Close-out (standard artifact-producing pattern)

No cleanup commit needed (plans/ is gitignored; no other files changed). Verify `git status` clean with the allowlist `plans/` and exit.

Resolve close-out paths:

```bash
REFLECTION_PATH=$(python3 resolve_skill_bundle_root("claude")/_shared/next_reflection_path.py claude-skill-improvement-planner)
REPO_LOCAL_HANDOFF=<repo>/.dev-skills/handoffs/claude-skill-improvement-planner/latest.md
SKILL_MD=resolve_skill_bundle_root("claude")/claude-skill-improvement-planner/SKILL.md
```

Spawn ONE close-out agent on the `frontier` tier. It writes both files directly via the Write tool:

```
Agent(
  subagent_type: "general-purpose",
  model: "<frontier-model-id>",
  name: "claude-skill-improvement-planner-closeout",
  prompt: """
    Review the skill at <SKILL_MD> and the current execution transcript.
    Produce TWO files via the Write tool.

    FILE 1 — REPO-AGNOSTIC reflection → write to <REFLECTION_PATH>

      # claude-skill-improvement-planner reflection — <ISO>

      ## What worked
      - <bullet about the SKILL's instructions>

      ## Improvements to SKILL.md
      - <specific, actionable change>

      Do NOT reference this project or the specific reflections aggregated
      this run.

    FILE 2 — REPO-SPECIFIC handoff → write to <REPO_LOCAL_HANDOFF>

      ---
      from: claude-skill-improvement-planner
      timestamp: <ISO>
      artifact: <absolute path to plan file>
      ---

      # Handoff for claude-skill-editor

      ## Summary
      <1–2 sentences: plan path, how many reflections aggregated,
       how many recommendations produced>

      ## Key decisions made this run
      - <what themes were promoted vs deferred>

      ## Open items for claude-skill-editor
      - <read the plan at <path>; apply recommendations in order;
         archive consumed reflections per the plan's directive>

      ## Files to watch for claude-skill-editor
      - <target SKILL.md files named in the plan>
  """
)
```

Exit message to user:

> Plan written to `<PLAN_PATH>`.
> Reflection saved to `<REFLECTION_PATH>`.
> Handoff written to `<REPO_LOCAL_HANDOFF>`.
>
> Recommended next step: run `/clear`, then invoke `/claude-skill-editor <PLAN_PATH>`. The editor will apply the recommendations and archive the reflections this plan consumed. If `/claude-skill-editor` isn't installed yet, the plan is still readable and actionable by hand.

## Plan file format

```markdown
---
from: claude-skill-improvement-planner
timestamp: <ISO>
min_reflections: <N>
reflections_consumed:
  - /absolute/path/to/reflection1.md
  - /absolute/path/to/reflection2.md
  - …
---

# Skill improvement plan — <ISO>

## Summary
<1–2 paragraphs: reflections read, skills covered, headline themes, contradictions surfaced.>

## Recommendations by skill

### <skill-name>
- **Change**: <specific SKILL.md edit, directive-only imperative form>
  - **Rationale**: <recurring theme this addresses>
  - **Supporting reflections**: v3, v5, v7
- …

(Repeat per skill. If a skill had no actionable themes, write: "No recurring themes above the `--min-reflections` threshold.")

## Cross-cutting recommendations
<themes that affect multiple skills at once>

## Speculative / low-confidence notes
<one-off feedback worth recording but not acting on yet>

## Contradictions surfaced
<reflections that disagreed; surface for user judgment>

## Archival directive for claude-skill-editor

After successfully applying each recommendation above, move every file listed under `reflections_consumed` (frontmatter) to `<reflections-dir>/archive/<original-filename>`. Create the `archive/` subdirectory if absent. This prevents re-aggregating the same feedback next cycle. If a specific recommendation fails to apply, leave its supporting reflections in place so the next planning pass can reconsider them.
```

## Archive convention

New convention introduced by this skill (the downstream editor performs the move):

- Path: `resolve_skill_bundle_root("claude")/<skill>/reflections/<repo_hash>/<branch_slug>/archive/<original-filename>`
- Directory created lazily on first archive.
- This planner excludes `archive/` when globbing.
- Already gitignored — `claude-config/skills/*/reflections/` in dotfiles covers `archive/` as a subpath.

## Best practices followed

- Directive-only: imperative form, no narratives, no stats.
- Progressive disclosure: the long aggregator prompt lives in `assets/aggregator_prompt.md`, not inline.
- Close-out pattern matches the pipeline skills so this skill's own corpus feeds future self-improvement passes.
- Repo-agnostic enforcement is load-bearing — aggregated reflections drive changes to SKILL.md files that ship to every repo, so any repo-specific leakage would propagate.

## Reference files

- `assets/aggregator_prompt.md` — the full prompt given to the aggregation Agent.


Use `phase_loop_runtime.skill_paths` resolver helpers for harness skill roots, handoff roots, helper roots, and reflection roots.

## Closeout

Closeout payload shape is defined by `EmitPhaseCloseout` in `phase_loop_runtime/baml_src/emit_phase_closeout.baml` (if that path is absent in the checkout, use the operator/prompt-supplied field contract or the installed `phase_loop_runtime` package — the missing vendored BAML source is not a blocker); keep skill text focused on value selection and handoff routing, not duplicated field ceremony.
