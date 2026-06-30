# Detailed plan — #28: execute skills default to worktree → branch → verify → commit → push → PR

## Task
The implementation execute skills (`execute-detailed`, `execute-phase`) carry a **blanket ban** — *"Do not commit, push, merge, or run destructive git commands unless the user explicitly requested that operation."* The safety intent (no unsafe direct publication) is right, but the default is wrong: a **verified** implementation gets left as dirty changes in the primary checkout with **no PR / no review surface**, even when the user's intent was clearly to land it through review ("why no PR?").

Change the default for **implementation runs** to: start from a clean base → dedicated **worktree + branch** → implement plan-owned changes → verify → commit scoped diff → push → **open a PR** — unless the user asked for local-only / planning-only / no-publication, or verification failed. Keep merge/force-push/reset/destructive ops gated by explicit instruction. Make **worktree isolation mandatory by default** so agents can never spray commits from a dirty `main` checkout.

## Research summary (verified against `main` @ v0.1.9)
- **Canonical skill source is `skills-src/<harness>/<harness>-<skill>/SKILL.md`** (per-harness, fully resolved — 0 `<harness>` tokens), brought in-repo by the v0.1.9 CANON cutover (#25/#27). `build_bundle.DEFAULT_SOURCES` points at `skills-src/<harness>/`.
- **Propagation chain:** edit `skills-src/` → `python phase-loop-runtime/scripts/regenerate_skills_bundle.py` (regenerates `phase-loop-skills/`) → `python phase-loop-runtime/scripts/sync_skills_bundle.py` (refreshes the packaged `phase-loop-runtime/src/phase_loop_runtime/skills_bundle/`).
- **Hard parity gate:** `tests/test_skills_canon_parity.py` + `.github/workflows/skills-parity.yml` assert committed `phase-loop-skills/` == `build_bundle(skills-src/)` (README excluded); the #12-style `test_skills_bundle_drift` asserts the packaged bundle is byte-identical to regen. **Both must stay green** — so every edit is source-first then regenerate, never hand-edit `phase-loop-skills/` or `skills_bundle/`.
- **The ban language** lives in `skills-src/{claude,codex,gemini,opencode}/<harness>-execute-detailed/SKILL.md` (e.g. claude line 30) and the `<harness>-execute-phase/SKILL.md` set. There are 4 harnesses × 2 skills = **8 canonical files** to edit (+ the regenerated `phase-loop-skills/` + packaged `skills_bundle/`, produced by the scripts, not hand-edited).
- **Worktree location rule** (global AGENTS.md): when `/mnt/workspace` exists, worktrees go under `/mnt/workspace/worktrees/<project>-<branch-slug>`, never repo siblings on the small root disk. #28 reaffirms this.
- **Interaction with model-routing-v2 governed mode:** orthogonal — governed `run_mode` is an opt-in pre-merge *review* gate inside the runner closeout; this change is about the **publication default** of the human-invoked execute *skills*. Keep them independent; the worktree-PR default applies to the skill-driven path, not the runner's own closeout-commit.

## Changes

### A. `execute-detailed` (4 harness canonical files) — replace the blanket ban with a publication policy
Replace the single ban bullet with the #28-specified policy (adapt wording per harness to match each file's existing voice):
```md
- For implementation runs, work in a **dedicated git worktree + branch by default** before
  editing — never edit/commit directly in the primary checkout or on `main`/protected branches.
  If `/mnt/workspace` exists, create the worktree under
  `/mnt/workspace/worktrees/<project>-<branch-slug>`; otherwise a repo sibling.
- After the plan's required verification **passes**, commit ONLY plan-owned changes (stage by
  explicit path; never `git add -A`), push the branch, and **open a PR** — draft if dependencies
  remain or verification is partial, ready when complete. Do this by default; do NOT stop at a
  verified-but-dirty local state.
- Skip publication (stop and report) only when: the user asked for local-only / planning-only /
  no-publication; verification FAILED; the worktree has a dirty conflict with unrelated user work;
  GitHub auth/remote/credentials are missing; or repo ownership is unclear. Report what was
  attempted and why no PR.
- Do NOT merge, force-push, reset, delete worktrees/branches with unmerged work, commit to
  protected branches directly, or run other destructive git ops without explicit instruction.
- Never stage secrets or raw credential output. Fail closed on ambiguous dirty state.
- Cross-repo plans: one worktree + branch + PR PER repo; cross-link the PRs with ordering /
  dependency notes in each body; if repo A must land before repo B, open B as draft (or mark
  blocked-on A).
```

### B. `execute-phase` (4 harness canonical files) — narrower, runner-aware
`execute-phase` already has lane/worktree concepts and is often runner-driven, so the rule differs:
```md
- If the phase-loop RUNNER owns closeout (autonomous/governed run), defer to the runner's
  closeout mode — do not independently publish.
- If a HUMAN invokes execute-phase and completes verified phase-owned changes OUTSIDE
  runner-managed closeout, publish through branch/PR by default (same safety envelope as
  execute-detailed: worktree isolation, scoped staging, no protected-branch commits).
- Never report `complete` while verified phase-owned changes sit as local dirty state with no
  PR/review surface, unless the user explicitly requested local-only work.
```

### C. Propagate + keep the gates green
1. Edit the 8 `skills-src/<harness>/...` files (A + B).
2. `python phase-loop-runtime/scripts/regenerate_skills_bundle.py` (→ `phase-loop-skills/`).
3. `python phase-loop-runtime/scripts/sync_skills_bundle.py` (→ packaged `skills_bundle/`).
4. Confirm `test_skills_canon_parity.py` + `test_skills_bundle_drift.py` pass (committed bundle == regen).

### D. Docs
- `CHANGELOG.md` entry (behavior change to the execute skills' publication default).
- If `docs/phase-loop/harness-skill-matrix.md` or `skills-canonical-source.md` describes execute-skill behavior, note the new default.

## Safety constraints preserved (explicit)
No direct commits to `main`/protected branches; worktree isolation **mandatory** by default; stage only plan-owned files + explicitly-allowed generated artifacts; never include secrets; fail closed on ambiguous dirty state; merges stay behind explicit instruction or an active CR+merge gate; report mitigations when auth/remote blocks PR creation.

## Verification
```bash
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/test_skills_canon_parity.py tests/test_skills_bundle_drift.py -q
# grep the new policy is present in all 8 canonical + regenerated + packaged copies; ban removed:
git grep -c 'Do not commit, push, merge' -- 'skills-src/**execute*' 'phase-loop-skills/**execute*' 'phase-loop-runtime/**skills_bundle/**execute*'   # expect 0
cd phase-loop-runtime && PYTHONPATH=src python -m pytest -q   # full suite green
```

## Acceptance criteria
- [ ] All 8 `skills-src/<harness>/<harness>-execute-{detailed,phase}/SKILL.md` carry the worktree→PR-default policy (per-harness voice), ban removed; `execute-phase` keeps the runner-closeout deferral.
- [ ] `phase-loop-skills/` + packaged `skills_bundle/` regenerated (not hand-edited); **parity + drift gates green**; full suite green.
- [ ] Worktree isolation is mandatory-by-default; protected-branch/destructive ops still require explicit instruction; cross-repo → one PR per repo with dependency links.
- [ ] CHANGELOG + skill-matrix docs note the new default.

## Open questions for the user
1. **Panel review?** This is a well-specified policy change (you authored the spec), lower-stakes than the roadmaps — I'd skip the advisor panel unless you want it.
2. **Default PR readiness:** draft-by-default vs ready-when-verification-complete (the plan uses: draft if deps/partial, ready if complete) — confirm.
3. **Should this also touch the non-bundled top-level `~/.claude/skills` / dotfiles execute skills**, or is the agent-harness `skills-src` canon the whole scope? (#28 names the agent-harness files; dotfiles consume the bundle, so source-here is sufficient.)
