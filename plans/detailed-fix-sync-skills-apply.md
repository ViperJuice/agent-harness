# Detailed plan — fix #14: `sync-skills --apply` silent no-op + stale post-cutover paths

## Task
`phase-loop sync-skills --apply` produces output **identical to `--check`** and exits 0 even when it repaired nothing — a silent no-op. Make `--apply` either actually repair the missing bridge skills (using a resolvable source, including the packaged `skills_bundle/` shipped by #12) **or** fail loud with actionable guidance and a non-zero exit. Also clean up two stale post-cutover paths. (Issue #14 part 2 — the stale `vendor/phase-loop-runtime` path in `SkillBundleResolutionError` — is **already fixed by #12**; this plan covers the rest + a `#14` comment noting that.)

## Research summary (verified)
- `maintenance.sync_bridge_skills` (`maintenance.py:89`) `--apply` loop (`109-124`): for each non-`ok` bridge record it reads `source_dir = record.get("source_dir")`; **`maintenance.py:115-116`** — `if not isinstance(source_dir, str) or not isinstance(repair_target, str): continue` — silently skips when `source_dir` is `None` (the repro's `missing_source` case). So when sources don't resolve, `changed` stays empty and the result is byte-identical to `--check`, exit 0. The reporting bug "lives here regardless" of source resolution (per the issue).
- Records come from `inspect_bridge_skill_inventory` (`skill_inventory.py`; record fields incl. `source_dir`/`repair_target`/`parity_status` at `skill_inventory.py:249-265`). `skill_inventory` already imports `packaged_resource_dir` and `_packaged_skills_bundle_dir()` → `packaged_resource_dir("skills_bundle")` (`skill_inventory.py:9,352`) from the #12 work, so the packaged bundle is reachable as a source — confirm whether the bridge/workflow inventory's `source_dir` resolution actually consults it (via `resolve_source_skill_dir`); wire it if not.
- Stale post-cutover paths: the `sync-skills --source` default is `vendor/phase-loop-skills` (`cli.py:313`); `injection.py:409` references `vendor/phase-loop-runtime/.../emit_phase_closeout.baml` (the BAML-path hint, separate from the already-fixed `SkillBundleResolutionError`).
- **Issue follow-up comment (confirmed root cause):** `register_skill_sources()` returns `DEFAULT_SOURCES` verbatim (relative); `skill_inventory._runner_repo_root()` reads `PHASE_LOOP_RUNNER_REPO_ROOT` (`skill_inventory.py:50`) as the anchor; without it the relative paths resolve against CWD → `missing_source`. The reporter's per-shell workaround sets BOTH `PHASE_LOOP_SKILL_SOURCE_PLUGINS` and `PHASE_LOOP_RUNNER_REPO_ROOT`. **#12 supersedes this for pinned installs** — the packaged `skills_bundle/` resolves absolutely with no env vars — so the durable source now exists. The comment's two remaining bugs: (a) the error message only mentions `PHASE_LOOP_SKILL_SOURCE_PLUGINS`, not the required `PHASE_LOOP_RUNNER_REPO_ROOT` anchor; (b) `sync-skills --apply` still no-ops (the core bug, this plan).

## Changes
1. **Honest / fail-loud `--apply`** (`maintenance.sync_bridge_skills` + the CLI handler `_sync_skills_command`, `cli.py:1026`):
   - In the apply loop, instead of silently `continue`-ing when `source_dir`/`repair_target` is missing, collect those records into an `unrepaired` list (with the reason, e.g. `no_source_resolved`).
   - Add `repaired`/`unrepaired` to the returned dict.
   - The CLI handler returns **non-zero** when `--apply` left any record unrepaired, printing an explicit message: how many repaired, how many could not be, and the actionable remediation — `run bootstrap.sh`, or `pip install -e ~/code/agent-harness/phase-loop-runtime`, or set `PHASE_LOOP_SKILL_SOURCE_PLUGINS`. Never produce check-identical output + exit 0 when repair was incomplete.
2. **Resolve the repair source from the packaged bundle** (`inspect_bridge_skill_inventory` source resolution):
   - Ensure `source_dir` resolves via `resolve_source_skill_dir` / the #12 packaged-bundle path so a pinned/consumer host (no dotfiles overlay) has a real source and `--apply` actually creates the missing bridge skills. Trace the current resolution; if it already routes through `resolve_source_skill_dir`, add a test confirming a packaged source repairs; if not, wire it (respecting the #12 `_canonical_skill_source_present` gate so dev checkouts keep their overlay-first behavior).
3. **Stale-path cleanup**:
   - `cli.py:313` `--source` default: drop the removed `vendor/phase-loop-skills` relative path — default to `None` and let the inventory resolution (sibling `phase-loop-skills/` / packaged bundle) supply the source, or to the correct resolved path.
   - `injection.py:409`: update the `vendor/phase-loop-runtime/.../emit_phase_closeout.baml` reference to the current location (or a path-agnostic phrasing).
4. **Error-message anchor (comment bug a)**: the `SkillBundleResolutionError` is now the fail-loud last resort (since #12's packaged bundle is the primary resolution). Make its remediation complete: it should note the packaged bundle is normally resolved (so a plain pinned install needs nothing), and for a source-mode run that uses the relative `DEFAULT_SOURCES`, mention BOTH `PHASE_LOOP_SKILL_SOURCE_PLUGINS` **and** the `PHASE_LOOP_RUNNER_REPO_ROOT` anchor — setting only the former still fails. (Confirm whether the #12 rewrite already names the anchor; add it if not.)

## Documentation impact
- `CHANGELOG.md`: fix entry (#14 — `sync-skills --apply` fail-loud + stale-path cleanup).
- A comment on issue #14 noting part 2 is already resolved by #12.

## Dependencies & order
2 (source resolves) makes repair possible; 1 (fail-loud) is correct regardless and is the must-fix. Do 2 → 1 → 3. (1 is independently valuable even if 2 is a no-op on a given host.)

## Verification
```bash
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/ -k "sync_skills or bridge or maintenance" -q
# New behavior:
#  - --apply with a resolvable (packaged) source repairs the missing bridge skill (target created; changed/repaired non-empty); exit 0.
#  - --apply with NO resolvable source returns non-zero + the explicit fail-loud message; NOT identical to --check.
grep -rn 'vendor/phase-loop-skills\|vendor/phase-loop-runtime' phase-loop-runtime/src/phase_loop_runtime/  # only legitimate remaining refs, if any
cd phase-loop-runtime && PYTHONPATH=src python -m pytest -q   # full suite green
```

## Acceptance criteria
- [ ] `sync-skills --apply` repairs missing bridge skills when a source (packaged bundle or overlay) resolves — target dirs created, `repaired` non-empty.
- [ ] When it cannot repair, `--apply` exits **non-zero** with actionable guidance and an explicit `unrepaired` summary — never silent-no-op-identical-to-`--check`-exit-0.
- [ ] `--source` default and `injection.py:409` no longer reference the removed `vendor/phase-loop-*` paths.
- [ ] New tests cover both the repair and the fail-loud paths; full standalone suite green.
