<!--
  Consumer validation — before acting on this handoff:
  1. Verify `from:` is `claude-plan-detailed`.
  2. Verify `timestamp:` is within the last 7 days.
  3. Verify every `artifact:` path resolves under your current
     `$(git rev-parse --show-toplevel)`. If any path points to a different
     repo, stop and surface it to the user.
-->
---
from: claude-plan-detailed
timestamp: 2026-06-26T16:54:00Z
artifact: /home/viperjuice/code/agent-harness/plans/detailed-add-ci-workflows-20260626-1654.md
---

# Handoff for the implementer

## Summary
Bounded "add CI" plan for the public `Consiliency/agent-harness` repo (no `.github/workflows/` today). Plan doc: `plans/detailed-add-ci-workflows-20260626-1654.md` (committed). Scope: new `.github/` workflows + `.gitignore` + an optional script — **no source code changes**.

## Key decisions made this run
1. Test CI installs the package + `pytest` explicitly — there is **no** `[project.optional-dependencies]` test extra. Run from `phase-loop-runtime/` (conftest lives there); command `pytest -m "not dotfiles_integration"`.
2. Reuse the existing `phase-loop-runtime/scripts/gate_a_cleanroom.sh` as the wheel install/identity gate — don't reinvent it.
3. Tag↔version check compares the **two static version sources** (`phase-loop-runtime/pyproject.toml:7` and `src/phase_loop_runtime/__init__.py` `__version__`, both `0.1.0`) and, on tag events, the tag. It will (correctly) flag the existing `v0.1.1`/`0.1.0` inconsistency — documented as an out-of-scope open item.
4. gitleaks config must be created fresh (none exists); allowlist only the **synthetic** redaction-test fixtures (run gitleaks locally first to find exactly which paths to allowlist).
5. Skills-lint is **optional / deferred** — override coverage is uneven (36 SKILL.md, not 9×5), so the invariant must be confirmed with the maintainer before encoding it.

## Open items for the implementer
- Confirm `gate_a_cleanroom.sh` runs in a stock GitHub Actions ubuntu runner (no fleet-only tools); document any prereqs.
- Run `gitleaks detect` locally to enumerate findings on the synthetic fixtures, then write the `.gitleaks.toml` allowlist to exactly those paths.
- Pin the gitleaks action to a specific SHA/tag (supply-chain hygiene).
- Decide the skills `_overrides` invariant with the maintainer before shipping the optional lint (or skip it).
- Separately (NOT this CI change): resolve the historical `v0.1.1` tag/version mismatch — bump both version sources to 0.1.1 + re-tag, or cut 0.1.2.

## Repo-specific gotchas surfaced
- 138 generated files are tracked under `phase-loop-runtime/build/lib/...`; `.gitignore` alone won't untrack them — pair it with `git rm -r --cached phase-loop-runtime/build`.
- conftest pins suite env (`PHASE_LOOP_PROFILE_PLUGINS`, `PHASE_LOOP_SKILL_SOURCE_PLUGINS`, `PHASE_LOOP_CLAUDE_ROUTE=print`, clears `CI`) — don't override these in CI or tests may behave differently.
- GitHub Actions security: keep tag/ref values out of inline `${{ }}` in `run:` blocks — pass via `env:`.

## Files the implementer will touch
- `.gitignore` (create) + `git rm --cached phase-loop-runtime/build`
- `.github/workflows/test.yml` (create)
- `.github/workflows/release-consistency.yml` (create)
- `.github/workflows/scrub.yml` (create) + `.gitleaks.toml` (create)
- `README.md` (modify — Development/CI section)
- (optional) `scripts/lint_skills.py` + `.github/workflows/skills-lint.yml`
