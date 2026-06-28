# Detailed plan — fix #12: skill-bundle unresolvable in a pinned/pip install

## Task
Make `phase-loop run` / `dry-run` work from a clean `pip install` of `phase-loop-runtime` (no dotfiles checkout, no sibling `phase-loop-skills/`). Today they raise `SkillBundleResolutionError` because the runtime resolves **no** skill-source roots: the wheel ships no skills, and the built-in provider returns dotfiles-repo-relative paths. Fix #12 by shipping the assembled neutral skill bundle inside the wheel and resolving it by absolute path.

## Research summary (verified)
- Only `run`/`dry-run` build prompt bundles → only they hit `injection.build_prompt_bundle` → `_resolve_pack_skill_dirs` (`injection.py:~58`), which calls `resolve_source_skill_dir(repo, harness, skill_name)` per expected skill and raises `SkillBundleResolutionError` (`injection.py:66-73`) when none resolve. The error text wrongly says the entry-point "is not registered."
- Skill-source roots come from `skill_inventory.iter_skill_source_roots()` (`skill_inventory.py:69`), aggregating (1) the `phase_loop_runtime.skill_sources` entry-point group and (2) the `PHASE_LOOP_SKILL_SOURCE_PLUGINS` env opt-in. The entry-point **is** registered (`pyproject:48-49` → `skill_sources_plugin:register_skill_sources`).
- `skill_sources_plugin.register_skill_sources()` (`skill_sources_plugin.py:24`) returns roots derived from `build_bundle.DEFAULT_SOURCES` (`build_bundle.py:20`), which are **dotfiles-relative** (`claude-config/claude-skills`, `codex-config/skills`, …) — meaningless outside a dotfiles checkout (`_normalize_sources(None)` → `build_bundle.py:183`).
- The wheel `package-data` (`pyproject:66`) ships `baml_src`/`schemas`/`_contract_docs`/`_test_fixtures` but **no skills**. The skills source is the sibling `phase-loop-skills/` (with `<harness>` placeholders + `_overrides/<harness>/`), assembled into per-harness dirs (`claude-phase-roadmap-builder`, `claude-plan-phase`, `claude-execute-phase`, `claude-phase-loop`, + codex/gemini/opencode) by `build_bundle.py`. The injection layer expects those assembled dir names (`HARNESS_ACTION_SKILLS`, `injection.py`).
- Precedent for in-wheel data kept in lockstep: `baml_src`/`_contract_docs` ship as package-data and are synced by `scripts/sync_runtime_package_data.py`.

## Changes
1. **Ship the assembled bundle as package-data.**
   - Add a packaged directory `phase_loop_runtime/skills_bundle/` containing the **assembled** per-harness skill dirs (the output of `build_bundle.py` over `phase-loop-skills/` — resolved `<harness>` tokens, `_overrides` folded in), laid out so `resolve_source_skill_dir` finds `claude-phase-roadmap-builder` etc. (confirm the exact expected layout by tracing `resolve_source_skill_dir` + `iter_skill_source_roots`).
   - Add `"skills_bundle/**"` to `[tool.setuptools.package-data]` in `phase-loop-runtime/pyproject.toml`.
   - Extend `scripts/sync_runtime_package_data.py` to (re)generate `skills_bundle/` from `../../phase-loop-skills/` via the existing assembly path, so it stays in lockstep (and is regenerated, not hand-edited). Document the regen command.
2. **Resolve the packaged bundle absolutely.**
   - In `skill_sources_plugin.register_skill_sources()` (and/or the `_normalize_sources(None)` default), when **not** in a dotfiles checkout, return **absolute** roots resolved via `importlib.resources.files("phase_loop_runtime") / "skills_bundle"` (anchored to the install location) instead of the cwd-relative `DEFAULT_SOURCES`.
   - Resolution order: dotfiles overlay (when a dotfiles tree is reachable — reuse the existing probe) → packaged `skills_bundle` → (then the env opt-in / error). Pinned install resolves the packaged bundle; a dotfiles dev checkout is unchanged.
3. **Honest error + docs.**
   - Update `SkillBundleResolutionError` (`injection.py:66-73`): drop the false "entry-point is not registered" claim; state that no skill roots resolved, that a custom `PHASE_LOOP_SKILL_SOURCE_PLUGINS` provider must return **absolute** roots, and that a normal pinned install should resolve the packaged bundle.
   - Note the absolute-roots requirement where `PHASE_LOOP_SKILL_SOURCE_PLUGINS` is documented (`skill_sources_plugin.py` docstring / README install section).

## Documentation impact
- `README.md` install section: a pinned `pip install` now ships skills and runs `run`/`dry-run` out of the box.
- `CHANGELOG.md`: add a fix entry (#12 — pinned-install skill-bundle resolution).
- `scripts/sync_runtime_package_data.py` header / a short note on regenerating `skills_bundle/`.

## Dependencies & order
Change 1 (package the bundle) is the prerequisite — resolution (2) and the test have nothing to point at until the skills physically ship. Order: 1 → 2 → 3.

## Verification
```bash
# Unit: roots resolve absolutely from the package, no dotfiles tree
cd phase-loop-runtime && PYTHONPATH=src python -m pytest tests/ -k "skill_source or skill_bundle or injection or pinned" -q

# Clean-room from-wheel proof (the load-bearing test): build the wheel, install into a
# throwaway venv with NO dotfiles checkout and NO sibling phase-loop-skills/, and assert
# dry-run resolves the bundle instead of SkillBundleResolutionError.
python -m build --wheel phase-loop-runtime
python -m venv /tmp/pl-cleanroom && /tmp/pl-cleanroom/bin/pip install dist/phase_loop_runtime-*.whl
cd /tmp && /tmp/pl-cleanroom/bin/phase-loop dry-run --repo <fixture-repo> --roadmap specs/phase-plans-v1.md
# (or an equivalent in-process test that points importlib.resources at the installed package
#  and asserts _resolve_pack_skill_dirs returns the 4 claude skills)

# Full standalone suite stays green
cd phase-loop-runtime && PYTHONPATH=src python -m pytest -q
```

## Acceptance criteria
- [ ] `phase_loop_runtime/skills_bundle/` ships the assembled per-harness skills as package-data, regenerated (not hand-edited) by `scripts/sync_runtime_package_data.py` from `phase-loop-skills/`.
- [ ] With **no** dotfiles tree and **no** sibling `phase-loop-skills/`, `_resolve_pack_skill_dirs(repo, "claude", …)` resolves the 4 claude skills from the packaged bundle (a test proves it — ideally the #9 clean-room/standalone gate).
- [ ] A dotfiles dev checkout still resolves the overlay first (existing behavior unchanged — existing tests green).
- [ ] `SkillBundleResolutionError` no longer claims the entry-point is unregistered and states the absolute-roots requirement.
- [ ] README + CHANGELOG updated; full standalone suite green.
