Review the following detailed implementation plan produced by `/claude-plan-detailed`. The plan describes one bounded, single-concern change (a bug fix, small feature, or targeted refactor) that one agent or developer will implement end-to-end. The plan follows a fixed template: Task, Research summary, Changes (grouped by file with entity/action/reason), Documentation impact, Dependencies & order, Verification, Acceptance criteria.

Assess, concretely:

1. **Change specificity** — does every bullet under `## Changes` name a concrete entity (class, method, function, migration, table, column, config key)? Flag anything that reads as a vague scope ("improve the auth module", "clean up the handler"), or an action word without an entity ("add validation"). The skill exists specifically to force concreteness; a vague bullet is a real defect.

2. **Modification-over-creation discipline** — any newly-created files or functions that could have been additions to existing files instead? Call these out by name. Acceptable reasons to create new: separation of concerns demands it, no existing home exists, existing code would become unwieldy. Unacceptable reason: "it's cleaner to start fresh."

3. **Scope creep** — compare the Task statement against the Changes list. Flag any change that is NOT on the critical path of the stated task. Common drifts: unrelated refactors of surrounding code, speculative error handling, type annotations on unchanged code, "while I'm here" additions.

4. **Documentation completeness** — is the Documentation impact section consistent with the Changes? For each kind of change, does the affected doc surface get an entry?
   - User-facing API / CLI change → `README.md` and usually `CHANGELOG.md`.
   - Public contract change → `AGENTS.md` / `CLAUDE.md` / `llm.txt` / `openapi.*` as applicable.
   - Architectural shift → `ARCHITECTURE.md` / `DESIGN.md` / relevant `docs/**`.
   Flag anything that plausibly needs a doc update but isn't listed. Flag anything listed that isn't justified.

5. **Verification concreteness** — every Verification step should be runnable as a shell command or observable behavior. Flag any "manually check that it works," "verify the output looks right," or similar un-runnable item.

6. **Acceptance-criteria testability** — every `- [ ]` item must be a testable assertion. "Users can log in" is not testable; "`POST /api/auth` returns 200 with a valid session cookie for a registered user" is. Flag any item written as prose or aspiration.

7. **Dependency ordering** — if any change depends on another (migration must run before new column is read, shared type must exist before consumers are updated), is that dependency explicitly called out in `## Dependencies & order`? Flag missing ordering constraints.

Be specific. Cite file paths, entity names, and the exact bullet at issue. Prefer "Changes → `src/auth/handler.ts` → `validateToken()` is described as 'improve' but doesn't say what changes — specify the added check or extracted helper" over general critique.

ARTIFACT:
