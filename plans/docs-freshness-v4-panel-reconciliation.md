# Advisor-panel reconciliation — docs-freshness roadmap (phase-plans-v4)

Three independent frontier legs (native Claude Opus @ max+ultrathink WITH repo access; Codex/GPT-5.5 @ xhigh; Gemini 3.1 Pro @ high). **All three: PARTIALLY AGREE** — direction correct (a diff-driven, pipeline-independent, fail-loud `docs-audit` is the right primary control; P1 load-bearing), but not ready as drafted. (Codex + Gemini failed transiently on the first attempt — gemini timeout, codex MCP-auth stall — and succeeded on retry; the Claude leg's claims were independently re-verified against the repo regardless.)

## Repo-verified factual errors in the draft (all confirmed by me against `main`)
1. **`validate_plan_doc.py` is NOT a runtime module** — it's a stdlib-only standalone script vendored into the skill bundles from `phase-loop-skills/{plan,execute}-phase/scripts/`, built by `build_bundle.py`. It cannot import a shared taxonomy → needs a **vendored copy + drift-guard** (the #12 pattern). The draft's P3 key-file path did not exist.
2. **Two surface taxonomies already exist** — `models.PUBLIC_SURFACE_GLOBS` (general) + `release_guard.RELEASE_AFFECTING_PATTERNS` (release-class). The draft proposed a greenfield third (`docs_surfaces.py`) — the exact drift it warned against. → unify/re-export the two.
3. **`release_guard.py` already provides the release-dispatch machinery** (`is_release_dispatch_plan`, `release_dispatch_blocker`, `_release_base_ref`, `_is_release_affecting_path`). P4 must extend it, not greenfield.
4. **The autonomous loop pushes directly to `main`** (`runner.py:7715`, `closeout_mode=="push"`); there is **no `gh pr create`** in the runtime. A PR-only required check is blind to the exact path that caused #18.

## Convergent design findings (≥2 legs)
- **Rubber-stampable satisfaction** (all 3): "any doc-touch OR a token" is gameable (README whitespace / boilerplate token). → release-class needs **relevance binding** (changed surface → its required doc surfaces) and **cannot** be satisfied by `docs_follow_up_filed`/`no_doc_delta`; **every** general surface must carry a *recorded* decision (closes Gemini's silent-absence leak). The decision record must be repo-recoverable without `.phase-loop/` state.
- **P1 alone ≠ freshness** (Codex, Gemini, Claude): diff-presence without P2's stale scan is "some doc activity," not "fresh." → **MVP = P1 + P2.**
- **Layer B is advisory by construction** (Codex): the validator loader swallows failures and `block` is forced to `warn` under default. → all non-bypassable claims on Layer A; in-loop release findings stay `warn`-effective, never `human_required`; the actual block is the CI gate.
- **Enforce on every shipping path** (Codex): PR check + `push:main` + a dependency of release/publish jobs (don't publish on placeholders) + `push:tags`. "Required check" is partly external GitHub branch-protection config — a stated invariant.
- **P4 reducer = post-release evidence *repair*, not freshness** (Codex): a reducer can't make the *tagged* commit fresh → the publish job runs `docs-audit` *before* publishing.

## Resolved decision (operator)
On the direct-to-main push (no PR): **detect + alert** — the audit runs on PRs (blocks merge) AND `push:main` (post-hoc red-mark), preserving the autonomy model (no forced PRs / branch protection). Coverage on main is detect, not prevent.

## Disposition
All of the above are folded into `specs/phase-plans-v4.md` (panel-reconciled r2): unified taxonomy + `release_guard` reuse + vendored-script drift-guard; per-surface relevance-bound decision contract; MVP = P1+P2; Layer A is the only non-bypassable control; both CI triggers + publish-job dependency; P4 reframed as evidence repair. `validate-roadmap` clean. The Gemini "minimum doc-change size / semantic LLM freshness check" idea is noted as a future option, not required.
