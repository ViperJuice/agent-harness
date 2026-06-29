# Changelog

All notable changes to `agent-harness` (the `phase-loop-runtime` package + the
`phase-loop-skills` bundle) are documented here. This project adheres to semantic
versioning; the release tag, the package `version`, and this file are kept in lockstep.

## Unreleased

- **Fix (#18 follow-up) — F5: evidence-backed docs-freshness decision.** A
  `docs_freshness: passed` claim is now *provable* from the scan evidence rather than a
  self-attested literal: `scan_docs_freshness` no longer emits `passed` when the scan ran
  but enumerated **no** public-doc surfaces (a bare/empty detail reports `skipped` — "could
  not verify" — instead of reading as a pass), and every result carries an `evidence_backed`
  flag (a new `docs_freshness_evidence_backed()` helper: `passed` AND surfaces enumerated AND
  no blocking hit). The pre-existing `doc_delta` gate's self-attested `no_doc_delta` is now
  **corroborated** against that scan on release phases — the freshness result is threaded into
  the closeout-validator context (validators stay pure; no repo IO) and an un-corroborated
  `no_doc_delta` is downgraded to a recorded **warn** (`doc_delta_uncorroborated`), never a
  block. Ordinary phases, and any phase with no scan threaded in, are unaffected.
- **Fix (#18 follow-up) — F4: required post-dispatch evidence-reducer lane.** A
  release-dispatch phase writes evidence docs referencing a commit SHA / workflow result that
  is unknowable before the tag is cut, so a pre-dispatch reducer necessarily leaves a
  placeholder (F1's scan is the backstop that blocks the closeout if it survives). F4 makes the
  back-fill an explicit, required planning step: `validate_plan_doc.py` now **errors** on an
  explicit `phase_loop_mutation: release_dispatch` plan that omits a post-dispatch
  evidence-reducer lane (and **warns** on a non-dispatch release shape that omits it), mirroring
  the F2 explicit-release posture. Added to the plan-phase SKILL guidance and propagated to the
  four `skills_bundle/*-plan-phase` copies via `sync_skills_bundle.py`. This is a
  plan-validation rule only — no new runtime back-fill engine (the placeholder scan remains the
  enforcement).

## v0.1.7

- **Fix (#18 follow-up) — pipeline-independent `docs-audit` backstop.** v0.1.6's
  `docs_freshness` closeout gate is *path-keyed* (it fires in the closeout pipeline and
  flags stale *tokens present* in a doc) but structurally cannot catch the **silent-absence**
  case: a release surface changed (e.g. `pyproject.toml`/`VERSION` bumped) while the
  CHANGELOG simply was not updated — no token to find — or any path that bypasses closeout
  (direct-`Agent()`, absent runtime helper). This adds a **diff-driven, pipeline-independent**
  `phase-loop docs-audit --base <ref>` CLI (a new `docs_audit` module over a standalone
  `docs_surfaces` taxonomy) wired into CI (`docs-audit.yml`) on `pull_request` (blocks the
  merge), `push:main` (red-marks post-hoc — the autonomous loop pushes directly to main, so
  the **whole pushed batch** is diffed via `github.event.before..HEAD`, not just the tip), and
  `push:tags`. It enforces a per-surface, **relevance-bound** decision contract — a release
  surface must change its *required* doc (a token or an unrelated README edit does not satisfy
  it); every general public surface needs at least a recorded decision — and **fails closed**
  on any un-evaluable input (unresolved base / git-diff error) rather than passing silently.
  The shipped v0.1.6 closeout gate and `release_guard` are untouched (this is purely additive;
  a single unified taxonomy is a later, separately-tested change). Decision-panel-reconciled.

## v0.1.6

Docs-freshness closeout gate (#18) + the model-routing & governed-review work and the
#12/#14 packaging fixes that shipped under this tag (previously left under "Unreleased").

- **Fix (#18):** A phase-loop release recovery could close **green** (clean tree, pushed
  `main`, release workflow passed) while its public docs stayed stale or absent — the
  existing doc-delta gate is diff-keyed, so files that *should* have changed but didn't
  were invisible, and under the default `PHASE_LOOP_REVIEW=warn` no finding ever blocks.
  Three load-bearing fixes:
  - **F1 — docs-freshness closeout gate** (`docs_freshness.py`): a *path-keyed*
    pre-scan (runner-side; validators stay pure) enumerates public-doc surfaces from the
    filesystem and `.claude/docs-catalog.json` (**not** from `changed_paths`) and scans
    their contents for stale placeholders (`recovery commit pending`, `TBD`, …). For
    **release/package phases only** it blocks `complete` as a hard gate — modeled on the
    verification-evidence gate, governed by its own `PHASE_LOOP_DOCS_FRESHNESS`
    (`hard` default | `warn` | `off`), independent of `PHASE_LOOP_REVIEW`. The hard
    block is **opt-in via explicit release frontmatter** (`phase_loop_mutation:
    release_dispatch` or a release `phase_type`): only an explicitly-declared release
    phase can be `blocked`. A heuristic-only release shape (the artifact-glob match on
    e.g. `CHANGELOG.md`/`**/pyproject.toml` with no release frontmatter) still scans and
    records evidence, but block-severity hits are **downgraded to warn** and can never
    halt the run — so an ordinary changelog/dep bump on a feature phase is never
    fleet-halted. Ordinary phases with no artifact match are unaffected (status
    `skipped`). The closeout now always carries `docs_freshness: passed|skipped|blocked`
    + a `docs_freshness_detail` evidence record (including an `explicit_release` flag), so
    a clean worktree alone cannot imply docs are current. Fuzzy signals (stale
    package-count claims, "skeleton") are warn-tier; an inline `<!-- freshness-ok -->`
    marker suppresses a false positive.
  - **F2 — release docs-lane ownership** (`validate_plan_doc.py`): release/package phases
    must have a docs lane that **owns** `README`/`CHANGELOG`/release-notes (or records an
    explicit no-doc-change decision), and the docs reducer must **depend on every producer
    lane**. ERROR only for **explicitly-declared** release phases (frontmatter); a
    heuristic-only release shape and ordinary phases are WARN (autonomy-first preserved).
  - **F3 — widened `PUBLIC_SURFACE_GLOBS`** to cover package-level `**/README.md`,
    `CHANGELOG*`, and release-notes surfaces.
  - Deferred as follow-ups: F4 (post-dispatch evidence reducer that back-fills the
    commit SHA/workflow result not knowable before tag creation) and F5 (evidence-backed
    freshness decision literal).
- **model-routing-v2 — governed mode goes live (serial path).** The v1 governed-review
  machinery (a tested island where `run_mode` reached `run_loop` but was never used) is now
  wired into the live runner: `--governed` / `PHASE_LOOP_RUN_MODE=governed` surfaces the mode;
  a plan-stage gate reviews first-attempt plans; a pre-merge gate runs before the closeout
  commit on implementation closeouts and runs a bounded review→fix→re-review loop; the panel
  spawns the **codex + gemini** subscription CLI legs fail-closed (claude leg `unavailable`
  pending a native-Agent path); every governed terminal is a non-human `review_gate_block`
  surfaced in the run-end summary. The **autonomous default is byte-identical** — an outer
  `run_mode=="governed"` guard means it renders no bundle and spawns zero panel legs (asserted
  at the run level). The panel reviews a *review bundle* (staged diff + acceptance criteria +
  verification results + summary) staged to a file. Remaining threads (documented, not
  overclaimed): the `model_class` escalation decision is recorded on dispatch metadata but not
  yet re-routed into live model selection; concurrent-wave dispatch is not governed; the real
  CLI spawn boundary (`_exec_leg`) can't run frontier models in CI so it's stubbed in tests.
- **Governed gate hardened (advisor-panel reconciliation).** A 3-model panel (Claude Opus +
  Codex GPT-5.5 + Gemini 3.1 Pro, each verifying against the code) found the gate failing in
  both directions and prescribed a structural fix, now landed:
  - **Relocated** — the pre-merge gate now runs INSIDE `_perform_phase_closeout`, after
    `git add` and before the commit, and reviews the EXACT staged index
    (`git diff --cached`). "What the panel reviews" == "what gets committed" *by construction*,
    which dissolves the prior bundle-vs-commit divergence, the untracked-new-files omission,
    the fail-open `_is_untracked` probe, and the N+1 git subprocesses in one move (the parallel
    `governed_bundle` path discovery is deleted; the renderer no longer writes into the repo).
  - **Reviewer≠author derived correctly** — from the UNION of the dispatch events'
    `selected_executor` (the `action='run'` event shape, not a filtered `execute`/`repair`),
    excluding EVERY vendor that authored the phase (rotation/repair can have several); an
    unknown author set fails closed.
  - **Fail-closed verdicts** — a strict terminal-line contract (last line begins with
    `AGREE`/`PARTIALLY AGREE`/`DISAGREE`, tolerant of markdown bullet/blockquote/numbered
    formatting); a *substantive* review with no conforming verdict is treated as a BLOCK (not
    a non-gating warn), and no usable disjoint reviewer HOLDS the merge (`review_gate_block`)
    instead of silently passing — the prior advisory-pass-on-degraded fail-open is gone. On a
    block the staged index is reset, so a stray `git commit` can't land the rejected change.
    The autonomous default stays byte-identical (the gate is a no-op off the governed path).
  - **Governed mode is EXPERIMENTAL — known limitations (documented, fail-safe).** It may
    over-block, but never silently passes unreviewed or self-reviewed code. With only the
    **codex + gemini** legs live (the claude leg is deferred), a **multi-vendor** phase —
    authored by codex *and* repaired by gemini — has no disjoint reviewer, so it is HELD with
    an explicit reason (it cannot be independently reviewed until the claude leg lands), rather
    than promoted. The `model_class` escalation decision is still recorded-not-yet-routed, and
    the executor-driven `apply_fix` re-dispatch remains a thread; a governed block halts the
    bounded run for the operator.
- **Fix (#14):** `phase-loop sync-skills --apply` silently no-oped — when a bridge skill's
  source did not resolve it skipped the record, producing output identical to `--check`
  with exit 0. It now reports the unrepaired skills and **exits non-zero** with actionable
  remediation, so it can never falsely imply success. The repair source resolves from the
  in-wheel `skills_bundle/` (via #12), so on a pinned install `--apply` actually repairs.
  Also: dropped stale post-cutover `vendor/phase-loop-{skills,runtime}` paths in the
  `build-bundle --source` default and the BAML-closeout prompt label, and the
  `SkillBundleResolutionError` now names the `PHASE_LOOP_RUNNER_REPO_ROOT` anchor (setting
  `PHASE_LOOP_SKILL_SOURCE_PLUGINS` alone is insufficient when its roots are relative).
- **Fix (#12):** `phase-loop run`/`dry-run` failed with `SkillBundleResolutionError`
  in a pinned/pip install (no dotfiles checkout) — the wheel shipped no skills and the
  built-in source roots were dotfiles-repo-relative. The assembled neutral workflow
  skill bundle now ships **inside** the package (`phase_loop_runtime/skills_bundle/`,
  generated by `scripts/sync_skills_bundle.py`) and `resolve_source_skill_dir` falls
  back to it by absolute path (`importlib.resources`), tried last so a dotfiles overlay
  still wins for dev checkouts. The Gate A clean-room probe now asserts a pinned install
  resolves the core skills under site-packages. The resolution error message no longer
  falsely claims the entry-point is unregistered and notes that custom
  `PHASE_LOOP_SKILL_SOURCE_PLUGINS` providers must return absolute roots.

### model routing & governed review (model-routing-v1/v2)

Tiered model selection + an opt-in governed review mode. **Two orthogonal axes,
kept separate** — and the autonomous default is unchanged:

- **`model_policy`** (*what model*): a vendor-agnostic `model_class` role layer
  (`planner`/`implementer`/`worker`) resolved to a concrete model per executor
  (claude → opus/sonnet/haiku; codex → gpt-5.5/5.4/5.4-mini; gemini → `pro` for
  planning and its built-in `auto` routing alias for implementer/worker —
  gemini exposes no vetted distinct cheap model). This repo ships a default
  policy — planning at `max` effort, implementation at the implementer class. A
  checkout with **no** `model_policy` resolves model + effort byte-for-byte as
  before (the empty-policy back-compat path).
- **Behavior change for upgraders (deliberate):** with this repo's shipped
  policy, default autonomous `execute`/`repair` now route to the *implementer*
  model_class (e.g. claude → `claude-sonnet-4-6` at `medium`) rather than the
  prior executor-default heavy model at `high`. This is the intended
  implementation-by-implementer-class design; pin `--model`/`--effort` or a plan
  `## Execution Policy` to override per run/phase. The empty-policy path is
  unchanged.
- **`run_mode`** (*how governed*): `autonomous` (default) vs `governed`
  (opt-in). Autonomous invokes **no** panel and adds no `human_required`;
  governed adds a 3-harness advisor-panel gate at planning + pre-merge with a
  bounded review loop and a non-human escalation terminal.

Details:
- **Effort clamp**: requesting `max` for a sub-max provider (gemini ceilings at
  `high`) *raises* unless the policy opts into the provider `effort_map`
  fallback — the shipped policy does, so `(plan, gemini)@max → high`.
- **Selection guard**: gemini/pi are never the max-effort *planner of record*
  (they can't run at `max`); enforced at dispatch selection, not only the clamp.
- **Governed gate** reuses the rigor-v1 `ReviewFinding` severity vocabulary but
  runs on a separate plan-stage seam (not the closeout registry) and
  short-circuits before any panel spawns in autonomous mode.
- **Route logging**: each dispatch records `model_class`/`concrete_model`/
  `effort`/`route_reason` (metadata-only) to the ledger; governed panel verdicts
  surface in the run-end summary.
- *Note*: the governed pre-merge loop's logic, ladder, and rendering are wired
  and unit-tested; full live threading into the executor fix-apply cycle is
  follow-up work. The autonomous default path is a proven no-op.

## v0.1.5

Closeout convergence fixes — both resolve infinite re-dispatch loops at the source.

- **#5:** build-regenerated **gitignored** artifacts are no longer classified as un-owned
  spillover. In `_classify_dirty_paths`, a path matching a gitignore pattern
  (`git check-ignore --no-index`, which matches even *tracked* paths) is excluded from the
  `unowned` set, so it can't trigger `dirty_worktree_conflict` -> an endless repair loop. It is
  NOT dropped from the dirty set: a gitignored path the plan OWNS still classifies as
  phase-owned and commits normally (no data loss); a genuinely-unowned non-ignored path still blocks.
- **#6:** a phase whose verified work is **already on the base branch** (nothing staged to
  commit) finalizes as a no-op (`closeout_action=noop_already_committed`, `closeout_commit=HEAD`)
  and advances, instead of `git commit` exiting non-zero, being mistaken for a commit failure and
  re-dispatching forever. Gated strictly on `terminal_status == "complete"` (== verification
  passed) so a blocked/failed/non-verified phase is never finalized; checked before the
  default-branch commit guard (a no-op commits nothing).

(The deterministic-blocker loop-breaker and a `reconcile --to-status complete` escape hatch
from the fix plans remain optional follow-ups — the above resolves both loops directly.)

## v0.1.4 — planning & execution rigor (rigor-v1)

Adds autonomy-first review gates and planner guidance. **Default behavior is
unchanged**: every new gate runs at `warn` severity (records a finding to the
closeout and the loop continues); gates block only when an operator opts in via
`PHASE_LOOP_REVIEW=block`, and **no new gate ever sets `human_required`**. Human
review cadence is meant to come from bounded runs (`--max-phases`), not in-loop
stalls.

- **Pluggable closeout-validator hook** (`closeout_validators.py`): the single
  seam review gates register through, with a `warn`/`block` severity model and
  the `PHASE_LOOP_REVIEW` control (default `warn`). `closeout.py` stays
  single-writer.
- **Doc-delta gate**: a public-surface change (CLI/schema/contract docs/README/
  CHANGELOG) with no recorded `doc_delta_decision` raises a finding.
- **Verification-evidence gate**: closes the generic-phase hole — a phase
  reporting `passed` with no evidence artifact (and no typed opt-out reason)
  raises a finding. The legacy `RG`/`--verification-log` hard gate is unchanged.
- **Visual-evidence gate**: a UI/visual change (`*.tsx/jsx/vue/svelte/css`,
  `components/**`) with no `visual_evidence_path` raises a finding.
- **New blocker class** `review_gate_block` (non-human, agent-recoverable).
- **Planner guidance**: model/effort tiering (right-size each lane, escalate
  with a reason via the `## Execution Policy` section); `validate_plan_doc.py`
  gains WARN checks for a terminal docs lane, non-testable acceptance criteria,
  and missing browser/screenshot steps on UI plans.
- **Mode-aware handoff**: the `/clear` recommendation is interactive-TUI-only;
  autonomous runs rely on the written handoff + fresh runner process, or a
  dispatched subagent.
- **Run-end findings summary**: at the end of a (bounded) run the runner emits an
  aggregated, de-duplicated summary of the review findings to stderr, so a human
  reviewing between `--max-phases` batches sees them without the loop ever
  stalling.
- **Hygiene**: untracked committed `__pycache__/*.pyc` bytecode caches.

## v0.1.3

- **Fix:** break the cross-phase dirty start-gate dead-end (#1) — the start-gate's
  recommended `reconcile` recovery no longer points at a command that only accepts
  `blocked` phases, so a repo with accumulated `.phase-loop/` state can always recover.
- **Hygiene:** removed a committed `build/` directory + `egg-info` (a stale build
  artifact carrying `__version__ = "0.1.0"` that setuptools intermittently reused,
  making installs report the wrong version) and added a `.gitignore` for build artifacts.

## v0.1.2

Packaging and documentation polish — no runtime behavior change.

- `phase-loop-runtime` package `version` now tracks the release tag (was reporting
  `0.1.0` on the `v0.1.1` tag).
- Public package metadata: harness-neutral `description` (no longer "vendored for
  dotfiles"), `[project.urls]`, license classifier, Python-version classifiers, author.
- Rewrote `phase-loop-runtime/README.md` for the public install flow (the prior copy
  referenced private `vendor/...` paths and "not published").
- Added this CHANGELOG, a `phase-loop-skills/` bundle README, and a
  `docs/TEAM-ONBOARDING.md` quickstart.
- Installer default ref bumped to `v0.1.2`.

## v0.1.1

- Synced the runtime to the post-TESTDECOUPLE state: bundled `_contract_docs/` and
  `_test_fixtures/` package data so the runtime-core test suite resolves them via
  `importlib.resources` and passes standalone in the extracted layout.
- Re-ran the public-release scrub.

Supersedes v0.1.0 (which predated TESTDECOUPLE and was missing the bundled
contract-docs package data).

## v0.1.0

- Initial public extraction of the harness-neutral phase-loop runtime + the
  cross-harness workflow-skills bundle from a private fleet repo, under Apache-2.0.
- **Superseded by v0.1.1** — do not pin v0.1.0.
