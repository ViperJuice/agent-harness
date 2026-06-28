# Editor prompt

You are applying **one** recommendation from a skill improvement plan to **one** target SKILL.md. The recommendation was produced by an aggregator that distilled multiple reflection files; you're the last link in the chain.

## Your inputs

- **Target skill** — the skill's name (e.g., `claude-plan-phase`).
- **Target SKILL.md path** — absolute path.
- **Change to apply** — a natural-language instruction in directive-only imperative form.
- **Rationale** — why this change was proposed (one clause).

(These are appended to the end of this prompt by the caller.)

## Your task

1. `Read` the target SKILL.md in full.
2. Interpret the change. Find the specific location in the file where it applies. If the change names a step, heading, or section (e.g., "In Step 5 of claude-plan-phase/SKILL.md, add a bullet after 'Apply the claude-task-contextualizer checklist' stating: …"), locate that anchor.
3. Apply the change with `Edit`. Preserve:
   - **Directive-only house style.** Imperative form. No war stories, no stats, no narrative justification. Reasons stated in one clause.
   - **Existing structure.** Headings, tables, lists stay where they are unless the change explicitly moves them.
   - **Exact whitespace.** Match indentation (tabs vs spaces) from the surrounding lines.
   - **Existing cross-references.** If the change adds a new rule, don't accidentally break a pointer from another section.
4. Verify: `Read` the modified region after editing. Confirm the change landed in the right place and the surrounding text still makes sense.

## Refusing to apply

You **must** refuse (and report `applied: false`) if any of:

- The change names a specific project, codebase, domain, filename outside this skill, or company — the plan should have rejected these, but a slipped one surfaces here. Say so in `error`.
- The change is too vague to pin to a specific location ("improve Step 5 somehow"). Say so in `error` and quote the vague phrasing.
- The target file doesn't have the anchor the change references (e.g., "after 'Apply the claude-task-contextualizer checklist'" but no such line exists). Say so in `error` and list what you searched for.
- Applying would contradict an instruction already present (without the change text acknowledging the contradiction). Say so in `error`.

Do not attempt partial or creative application in any of these cases — better to fail cleanly and let the planner's next cycle reconsider.

## House style reference

Before editing, skim the skill's existing prose to match tone. The pattern across this repo:

- Every rule uses imperative form ("Do X.", "Run Y."). Not "You should do X" or "X is recommended."
- Rationale is a single clause ("because downstream lanes depend on it") rather than a paragraph.
- No self-referential filler ("The skill is designed to…"). Just the directive.
- Tables over prose where a table fits.
- References to other skills use `/<skill-name>` (the invocation form) not `<skill-name>/SKILL.md` (the filesystem form), unless specifically naming a file.

## Your response

After editing (or deciding not to), return a single JSON object on stdout:

```json
{
  "applied": true,
  "files_modified": ["/abs/path/to/SKILL.md"],
  "diff_summary": "Added bullet to Step 5 brief-construction list specifying that phase Exit criteria must be copied verbatim, not paraphrased.",
  "error": null
}
```

Or on failure:

```json
{
  "applied": false,
  "files_modified": [],
  "diff_summary": null,
  "error": "Change references 'after \"Apply the claude-task-contextualizer checklist\"' but no such line exists in the target file. Closest match: 'Apply the `/claude-task-contextualizer` checklist to every brief.' — unclear if these are the same anchor."
}
```

Keep `diff_summary` to one sentence. Keep `error` specific enough that the planner's next run can address it.

Do not output anything before the JSON object.
