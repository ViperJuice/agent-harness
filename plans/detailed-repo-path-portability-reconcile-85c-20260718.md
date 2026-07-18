# Detailed plan: repo-path portability in reconcile/status (ah#85 sub-fix C)

## Task
Fix ah#85 sub-fix C (repo-path portability / symptom #5). Phase-loop persists **absolute**
repo/roadmap paths in `state.json` and `events.jsonl`. When `.phase-loop/` is copied into a git
worktree or the repo root moves/renames (e.g. `/home/user/code/avatar-client` →
`/mnt/workspace/worktrees/…`), `reconcile()`/status replay silently treats ALL phases as
**unplanned** instead of either normalizing paths or failing closed with a clear
state-portability warning. Deliver **repo-relative normalization** (the issue's "portable"
option, preferred over fail-closed since we can rebase) with a single informational
state-relocation ledger warning, across `reconcile.py` / `classifier.py` (read side), with a
shared helper in `runtime_paths.py`.

## Research summary
Repro is **source-verified on current main** (not stale like #186). Two decisive gates discard
persisted status on a moved root:
- `reconcile.py:65` — `same_roadmap = Path(snapshot.roadmap).expanduser().resolve() == roadmap.resolve()`.
  A moved root ⇒ stored abs ≠ current ⇒ the whole snapshot-status-application block (66–109) is
  skipped, so no phase gets its persisted status.
- `reconcile.py:122` — `if Path(event["roadmap"]).resolve() != roadmap.resolve(): continue`. Every
  event is dropped on a moved root, so no event-derived status either. Result: all-unplanned.

Sibling roadmap path-gates: `reconcile.py:740, 786, 844, 884` and `classifier.py:32`; plan-path
gates at `754, 800`. **Every** one of these path gates is paired with a **content-SHA provenance
guard** — snapshot at `:74` (`status_provenance_matches`), main event loop at `:161`
(`_event_status_provenance_matches`, the guard on the `phases[phase]=status` write at `:190`),
breakglass loops at `:735`/`:781` (SHA checked *before* the path gate), terminal-recovery loops at
`:850`/`:894`, classifier at `:38`. So the SHAs (`roadmap_sha256`/`phase_sha256`, content-bound)
are the real integrity backstop; the path gates are only a "same roadmap file" convenience filter
that breaks under relocation. Loosening them to **repo-relative** comparison is safe: an event
from a genuinely different repo that happens to share the relative path
(`specs/phase-plans-v1.md`) is still rejected by the SHA guard unless its content matches — in
which case it *is* the same roadmap content and applying it is correct. Both `state.json` and each
event already persist BOTH `repo` and `roadmap` (`state.py:128-129`, `events.py:41-42`), so a
read-time repo-relative comparison needs **no schema change** and is backward-compatible with
existing absolute-path state (fast-path abs-equality still matches). `verification.json` is a
**frozen closed-field contract** (`_contract_docs/runtime/verification-evidence-contract.md:10`) and
is deliberately NOT touched — it is not read by reconcile. Reuse precedent:
`adoption_bundle.py:273` / `dispatch_lock.py:237` (`resolve().relative_to(repo)`); the new helper
belongs in `runtime_paths.py`.

## Changes

### `phase-loop-runtime/src/phase_loop_runtime/runtime_paths.py` (modify)
- `roadmap_paths_match(stored_repo, stored_roadmap, repo, roadmap) -> tuple[bool, bool]` — **add** —
  single portable roadmap-identity helper returning `(matches, relocated)`. Logic:
  1. Fast path: `Path(stored_roadmap).expanduser().resolve() == roadmap.resolve()` → `(True, False)`
     (unchanged legacy behavior; existing same-root state matches here).
  2. Portable path: compute repo-relative subpaths on both sides —
     `Path(stored_roadmap).expanduser().resolve().relative_to(Path(stored_repo).expanduser().resolve())`
     vs `roadmap.resolve().relative_to(repo.resolve())`. Equal ⇒ `(True, True)` (matched only via
     relocation). `relative_to` is lexical (no filesystem dependency on the stale stored root).
  3. On `ValueError`/`OSError`/`TypeError` (roadmap outside its repo, unparseable stored path) →
     `(False, False)` — conscious fallback to non-match, i.e. **no regression** vs today's
     abs-equality (still treated as a different roadmap; no silent status resurrection).
  Reason: one tested helper, reused at every gate; keeps relativization edge-cases in one place.

### `phase-loop-runtime/src/phase_loop_runtime/reconcile.py` (modify)
- `reconcile()` snapshot gate (~`:65`) — **modify** — replace the abs-equality with
  `same_roadmap, relocated = roadmap_paths_match(snapshot.repo, snapshot.roadmap, repo, roadmap)`.
  When `relocated`, append **one** `_ledger_warning("state", snapshot.current_phase or "", "", "repo_relocated")`
  (single emission point — this block runs once). Reason: restores persisted phase statuses after
  relocation (the PRIMARY all-unplanned fix); SHA-guarded at `:74` so safe.
- `reconcile()` main event-filter (~`:122`) — **modify** — replace abs-inequality `continue` with
  `matches, _ = roadmap_paths_match(event.get("repo"), event.get("roadmap"), repo, roadmap); if not matches: continue`.
  Reason: stop dropping every event on a moved root; SHA-guarded at `:161`. Do NOT emit a
  per-event relocation warning (dedup — the snapshot warning covers the signal; if there is no
  snapshot, see Dependencies note).
- `_lane_ir_override` roadmap gate (~`:740`) and `_closeout_allow_unowned_attested` gate (~`:786`) —
  **modify** — same `roadmap_paths_match` swap. Reason: restore operator breakglass attestations
  after relocation; SHA-guarded first at `:735`/`:781`. Leave the `plan_path` equality checks
  (`:754`, `:800`) abs for now UNLESS the implementer confirms they also gate status on the moved
  path — if so, apply the same repo-relative treatment using `repo`/`stored_repo`; otherwise note
  as out-of-scope (plan artifacts are re-derived from `repo` at read time via `find_plan_artifact`).
- terminal-recovery loops (~`:844`, ~`:884`) — **modify** — same `roadmap_paths_match` swap.
  Reason: full-fidelity replay of clean-verified/manual-repair recovery after relocation;
  SHA-guarded at `:850`/`:894`.
- closeout artifact resolve (~`:1298`, `Path(str(artifact)).expanduser().resolve().relative_to(repo)`) —
  **modify only if it raises uncaught on a relocated abs artifact path** — wrap the `.relative_to(repo)`
  in a guard that falls back to the stored value (or skips) instead of raising. Reason: defensive;
  implementer must confirm whether this path is reachable with a stale abs artifact path and
  currently unguarded. Lower priority than the roadmap gates.

### `phase-loop-runtime/src/phase_loop_runtime/classifier.py` (modify)
- `classify_phase` roadmap gate (~`:32`) — **modify** — replace
  `Path(state.roadmap).expanduser().resolve() == roadmap.resolve()` with
  `roadmap_paths_match(state.repo, state.roadmap, repo, roadmap)[0]`. Reason: single-phase
  classification must be portable too; SHA-guarded at `:38`.

### `phase-loop-runtime/tests/test_reconcile_portability_85c.py` (create — UNMARKED module)
- `test_roadmap_paths_match_*` — **add** — unit-cover the helper: (a) identical abs →
  `(True, False)`; (b) relocated same-relative → `(True, True)`; (c) different relative roadmap →
  `(False, False)`; (d) roadmap outside repo / unparseable stored path → `(False, False)` fallback.
- `test_reconcile_preserves_status_after_repo_relocation` — **add** — write `state.json` + events
  under root A (via `make_repo` + real reconcile), copy `.phase-loop/` to root B (a second
  tempdir), reconcile from B → assert the completed phase is NOT `unplanned` (statuses preserved)
  AND exactly one `repo_relocated` ledger warning is present. MUST be hermetic (reconcile is
  read-side; confirm no `SkillBundleResolutionError`). UNMARKED so CI's `-m "not dotfiles_integration"`
  runs it. Reason: pins the end-to-end symptom-#5 fix and would fail on today's code.

## Documentation impact
- `CHANGELOG.md` — **add** — one entry under the unreleased/next section noting reconcile/status
  now portable across a relocated repo root (with a `repo_relocated` state warning). Required by the
  committed-diff docs-freshness CI gate for a behavior change.
- `_contract_docs/runtime/verification-evidence-contract.md` — **none** — frozen closed-field
  contract, deliberately untouched (verification.json is not read by reconcile).
- No state/reconcile contract doc exists under `_contract_docs/runtime/`; if the implementer finds a
  ledger-reason enumeration anywhere, add `repo_relocated` there (additive). Otherwise none.

## Dependencies & order
1. Add `roadmap_paths_match` to `runtime_paths.py` first (all call sites import it).
2. Swap the reconcile snapshot gate (`:65`) — this alone is the PRIMARY fix (SHA-guarded, safe) and
   resolves the main all-unplanned symptom via the snapshot's phase-status map.
3. Swap the event-site gates (`:122, 740, 786, 844, 884`) + classifier (`:32`) — SECONDARY,
   full-fidelity replay + breakglass restoration; all independently SHA-backstopped so safe.
4. Warning dedup: emit the single `repo_relocated` warning at the snapshot gate. If there is no
   snapshot (state.json absent but events present), the implementer may detect relocation at the
   first admitted relocated event and emit once via a `relocation_warned` latch — keep it to ONE
   warning per reconcile.
5. Tests last.

## Verification
```bash
cd phase-loop-runtime
# new tests (unmarked → CI-visible)
PYTHONPATH=src:tests python -m pytest tests/test_reconcile_portability_85c.py -q
# no regression in reconcile / state / classifier suites
PYTHONPATH=src:tests python -m pytest tests/test_reconcile.py tests/test_state.py tests/test_classifier.py -q
# CI-visible subset mirror
PYTHONPATH=src:tests python -m pytest -m "not dotfiles_integration" -q
```
Behaviors to observe: the relocation test fails on current `main` (all-unplanned) and passes after
the fix; same-root reconciles are byte-unchanged (fast-path abs-equality); a genuinely different
roadmap sharing the relative path stays rejected (SHA guard) — add an assertion if cheap.
Edge cases: roadmap outside repo (fallback to non-match); symlinked repo roots (resolve() on the
live side, lexical relative_to on the stale side).

## Acceptance criteria
- [ ] `runtime_paths.roadmap_paths_match(stored_repo, stored_roadmap, repo, roadmap)` returns
      `(True, True)` when the roadmap's repo-relative subpath matches but the absolute roots differ,
      `(True, False)` on identical absolute paths, and `(False, False)` for a different relative path
      or an unrelatable stored path.
- [ ] `reconcile()` run against a `.phase-loop/` written under repo root A but reconciled from root B
      preserves the persisted phase statuses (no all-unplanned) and emits exactly one
      `repo_relocated` state ledger warning — pinned by an UNMARKED test that fails on current `main`.
- [ ] Every reconcile/classifier roadmap path-equality gate that writes/keeps phase status remains
      gated by its existing content-SHA provenance check (no gate loosened without an adjacent SHA
      backstop).
- [ ] `verification.json` schema and `verification-evidence-contract.md` are unchanged; existing
      `test_reconcile.py` / `test_state.py` / `test_classifier.py` stay green.

## Execution Policy
- execute: effort=medium, reason=correctness-sensitive around the SHA-backstop invariant and path
  relativization edge-cases, but a bounded read-side swap once the single helper exists.
