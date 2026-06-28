Review the following phase-plan document produced by `/claude-plan-phase`. The plan describes one phase of a multi-phase software roadmap, decomposed into parallel swim lanes with disjoint file ownership. Downstream, `/claude-execute-phase` will spawn one worktree-isolated teammate per lane and auto-merge lanes as their verify passes.

Assess, concretely:

1. **Interface-freeze completeness** — does every symbol, type, endpoint, or migration listed under any lane's `Interfaces consumed` trace to an upstream lane's `Interfaces provided` or to pre-existing code? Flag any consumed interface that is never produced.

2. **DAG parallelizability** — could any lane be moved to an earlier phase or to an earlier position in this phase's DAG, given a narrower interface freeze? Flag lanes whose `Depends on:` could be reduced. Flag sibling phases that could have been a single phase with more lanes.

3. **Exit-criteria testability** — every `- [ ]` item under `**Exit criteria**` must be checkable by a shell command, test run, or grep. Flag items that are prose ("users can log in") rather than runnable assertions ("`POST /api/auth` returns 200 with a valid session cookie").

4. **Scope-note lane partition clarity** — does each lane's `**Scope notes**` state the file partition (which files the lane owns exclusively) and identify any single-writer files (files multiple lanes might want to touch, with one owner named)? Flag lanes where the partition is unclear or where file globs overlap another lane.

5. **Stale-base hazards** — does the plan assume a rebase or merge ordering that a downstream lane could invalidate? In particular: any lane that rewrites shared infrastructure (barrel index files, generated types, migration numbers) must be called out as a single-writer. Flag lanes whose work would be destroyed by a silent `git reset --hard` or `git checkout HEAD~N -- …`.

Be specific. Cite lane IDs (SL-1, SL-2, …), line numbers, and the exact symbol/file at issue. Prefer "lane SL-3 consumes `FooContract` on line 42 but no upstream lane provides it" over general suggestions like "improve interface tracking."

ARTIFACT:
