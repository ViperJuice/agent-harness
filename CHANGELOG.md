# Changelog

All notable changes to `agent-harness` (the `phase-loop-runtime` package + the
`phase-loop-skills` bundle) are documented here. This project adheres to semantic
versioning; the release tag, the package `version`, and this file are kept in lockstep.

## Unreleased
- **Advisor Board contract freeze (Phase 1 ABDFREEZE).** New additive,
  behavior-neutral `phase_loop_runtime.advisor_board` package freezing the
  model-first Advisor Board interfaces the parallel fan-out (ABDREG / ABDRESOLVE
  / ABDHOME) builds against — no change to the running `panel_invoker` path (the
  `default` board reproduces today's 3-leg behavior byte-for-byte). Freezes:
  `Seat{model, effort, harness?, lens?, auth?, backing?, host_leg?}` + `Board`
  schema and the `~/.config/agent-harness/advisor-boards.toml` config format
  (`schema.py`, `fixtures/advisor-boards.example.toml`); the per-harness
  model/effort mapping incl. the agy leg's effort-in-model-name special case
  (`harness_mapping.py`); the seat→vendor-family projection that keeps the
  governed reviewer≠author disjointness intact under model-first (byte-consistent
  with `governed_review`); host-leg identity; registry + `(model × harness)`
  compatibility/auth-availability matrix interfaces with importable stubs
  (`registries.py`); the provider-backing selector + active-env-scrubbing
  no-silent-key auth contract (`backing.py`); the internal advisor-board event
  envelope + best-effort forwarding (`events.py`); and shared canonical fixtures
  (`fixtures.py`). Contracts documented in `advisor_board/CONTRACTS.md`.
  Produces IF-0-ABDFREEZE-1..5. See `specs/phase-plans-v5.md`.
- **CI: publish `phase-loop-runtime` to PyPI on tag.** New
  `.github/workflows/publish-pypi.yml`, tag- and `workflow_dispatch`-triggered,
  builds sdist+wheel and publishes via PyPI Trusted Publishing (OIDC) — no
  token stored in the repo. Verifies the pushed tag matches
  `phase-loop-runtime`'s `pyproject.toml` version before building. Also flags
  in the README and `pyproject.toml` that the PyPI dist name is
  `phase-loop-runtime`, not `agent-harness` (an unrelated third-party PyPI
  project). One-time PyPI setup required before the first tag publish: claim
  project `phase-loop-runtime` and add a Trusted Publisher (owner
  `ViperJuice`, repo `agent-harness`, workflow `publish-pypi.yml`, environment
  `pypi`).

- **Self-ingest: agent-harness adopts its own `.consiliency/`.** First governed repo — archetype `tooling-meta` + `public` modifier, adopted scope `[layout, gates]`, vendoring `@consiliency/contract` 0.3.0. Additive (`.phase-loop/` untouched); all L0 gates pass (presence/local-integrity/layout/version-skew). Docs are L0 presence-stubs to be filled at L1.

- Bumped the vendored `consiliency-contract` pin from `>=0.2.1,<0.3.0` to
  `>=0.3.0,<0.4.0`. 0.3.0 rebalances the required-documents registry: the
  baseline shrinks from ~13 governed docs down to 6 universal ones (`readme`,
  `doc-contract-index`, `contract-version-status`, `glossary`,
  `interface-declaration`, `codeowners`); `service-catalog-ownership`, `sbom`,
  `adr-index`, `dev-setup`, and `changelog` move onto the code archetypes
  (`product`/`service`/`library`/`infra`/`tooling-meta`) that actually need
  them; and `contributing`/`license` move onto the (opt-in) `public` modifier.
  No `phase_loop_runtime` code changes — the scaffolder/gates/ingest modules
  already compose the required-document set purely from the vendored
  registry at runtime — but the consiliency test suite's fixtures/assertions
  are updated to the new baseline: scaffolding a bare archetype (no `public`
  modifier) no longer has a LICENSE gap to exercise, so
  `test_consiliency_gates.py`'s `_scaffolded_repo` and
  `test_consiliency_scaffold.py`'s fabrication test now request the `public`
  modifier explicitly to keep covering a real `l0_stub_allowed: false` gap,
  and `test_consiliency_ingest.py`'s second-verify-pass test now asserts a
  genuinely clean (`passed`) gate scan instead of the LICENSE-driven `warn`
  it exercised under the old, larger baseline.

## v0.1.13

- **CS-0.10a — `phase-loop worktree-index` freshness pointer.** New read-only,
  purely git-derived command that answers "where is the freshest working copy
  of a path, and who's touching it": enumerates active worktrees (`git
  worktree list --porcelain`), diffs each worktree's branch against
  `origin/<default-branch>` (falling back to `origin/main`), and reports the
  holders (worktree path, branch, last commit) for a queried path — or every
  touched path when none is given — plus whether `origin/main` is behind on
  it. No new persistent state; a repo with no divergent worktrees answers
  `origin/main`. `phase_loop_runtime.worktree_index` is the module; never
  writes repo state.
- **CS-0.7 — realized-edge / fleet-map v0 extractor.** Between the core
  Consiliency-standardization repos there are ~zero package-level deps, so a
  package-lockfile scan renders the real cross-repo interface graph
  invisible; the actual edges are git+ref pins, copied-literal (vendored)
  contract/schema drift, and hard-coded host-path refs in source. New
  `phase-loop fleet-map --repo <path> [--repo <path> ...] [--json]` (module
  `phase_loop_runtime.fleet_map`) statically extracts those three edge kinds
  across a set of repo paths and emits an interface-graph artifact — each
  edge `{from_repo, to_repo, kind, evidence, maturity_label}` — alongside a
  package-lockfile-only baseline for comparison (typically empty even over
  repos with ordinary, unrelated third-party manifest deps). v0: no network
  calls or git remote resolution, static file inspection only.
- **CS-0.10c — local-file `LeaseStore` + soft leases.** New
  `phase_loop_runtime.lease_store`: a local-file backend for the CS-0.10b
  `LeaseStore` contract (`consiliency_contract`'s `lease.schema.json` /
  `lease-event.schema.json` / `lease-store-protocol.schema.json`) so parallel
  local agents can claim path-sets without stepping on each other. SOFT MODE
  ONLY — this backend never declares an atomic backend, so a requested hard
  lease always degrades to soft (no cross-machine atomic acquire locally;
  that needs the off-device backend, CS-0.10d). TTL + heartbeat +
  auto-expiry: an unrenewed lease past `heartbeat_at + ttl_seconds`
  (exclusive boundary) is free, so a dead holder can never freeze a path.
  Give-way policy is REROUTE, not block: an `acquire()` colliding with an
  active lease (same `lease_id`, or an overlapping path-set scope held by
  someone else) returns the blocking lease instead of raising or waiting.
  The current-lease view (`query()`) is a pure projection of the append-only
  `.consiliency/leases/events.jsonl` event log ONLY — the module has no
  parameter a coordination-channel message could occupy, so the sole-truth
  guardrail (the inbox is never authoritative for lease state) is structural,
  not a runtime check. New `phase-loop consiliency-lease
  acquire|renew|release|query --repo <path> ...` CLI. Vector-tested against
  every `lease-*`/`coordination-*` conformance vector the vendored
  `consiliency_contract` (>=0.2.0) ships.
- **CS-0.8 — agent-runtime provider seam.** New `phase_loop_runtime.agent_runtime_provider`:
  an `AgentRuntimeProvider` Protocol (matching omniagent-plus core-contracts) +
  `HomebrewAgentRuntimeProvider` degraded profile — a one-shot CLI spawn presented as a
  single-turn, buffered-replay session, `cancel_turn` = process kill, unsupported
  capabilities declared via `health()`. The panel spawn path routes through the seam
  (behavior unchanged; the existing panel tests are the regression guard). Lets a future
  Omnigent-backed provider drop in without a caller change.
- **CS-0.11 — brownfield ingestion (shape-to-conform, then verify).** New
  `phase-loop consiliency-ingest --repo <path> [--adopt]`: first pass (no manifest) shapes a
  compliant `.consiliency/` via the CS-0.5 scaffolder + a CS-0.12 adoption profile + a
  conservative governed-set proposal (consent-gated on `--adopt`; unflagged repos untouched;
  scratch/other-harness namespaces never claimed); every subsequent pass verifies via the
  CS-0.6 L0 gates + `evaluate_governance_scope` (governed / foreign / present-nonconforming
  labels), never rewriting. Detection = manifest presence. The repo-library on-ramp.

- **CS-0.5 — `.consiliency/` scaffolder (first-writer).** New
  `phase-loop consiliency-scaffold --repo <path> --archetype <name>` (repeatable
  `--archetype`/`--modifier`, or `--baseline-only`) writes a schema-valid
  `.consiliency/` layout: `manifest.json` (declaring archetype(s)/modifier(s) +
  the composed governed-doc allowlist), `status.json` (contract-version-status)
  and `interfaces.json` (interface declaration) as real minimal artifacts, and
  L0 presence-stub docs — each an honest "unauthored, tracked" marker with an
  explicit authored zone, never a fabricated projection — for every doc the
  vendored `consiliency_contract` required-documents registry demands.
  Additive/first-writer: never touches `.phase-loop/`/`.pipeline/`, never
  overwrites a file that already exists (re-running is a safe no-op), and
  docs marked `l0_stub_allowed: false` (`readme`, `license`, `document-index`)
  are never fabricated — referenced if present, otherwise just declared for
  the presence gate to flag. `phase_loop_runtime.consiliency_scaffold` /
  `phase_loop_runtime.consiliency_layout` are the modules.
- **CS-0.6 — `.consiliency/` L0 gates.** Four gates — `presence`,
  `local-integrity` (git-scoped hash snapshot; a forward-compatible no-op
  today since Phase 0 floors every doc at `presence-only`), `layout-validity`
  (manifest/status/interfaces validate against the vendored schemas), and
  `version-skew` (repo `contract_version` vs the installed
  `consiliency-contract` package) — wired at top-of-loop (a non-blocking
  advisory notice, mirroring the existing governed-mode notice) and closeout
  (threaded into `build_phase_loop_closeout` alongside `docs_freshness`, new
  `consiliency_gates`/`consiliency_gates_detail` closeout fields). SOFT/warn
  by default; `PHASE_LOOP_CONSILIENCY_GATES=hard` opts into blocking (new
  `consiliency_gate_blocked` frozen blocker literal), never sets
  `human_required`, and the version-skew gate never blocks even under `hard`
  (Phase 0 severity is normatively `warn`). CONSENT-GATED: a repo without a
  `.consiliency/manifest` is a pure no-op. `phase_loop_runtime.consiliency_gates`
  is the module.
- Added `consiliency-contract` (the published shared Consiliency contract
  package) and `jsonschema` as runtime dependencies; all `.consiliency/`
  schemas/registries are read from the vendored package, never copied.

## v0.1.12

- **CS-0.4 release floor.** Bumped `phase-loop-runtime` to `0.1.12` for the
  Consiliency standardization release floor. No bridge-contract or behavior
  changes; `phaseLoopBridgeContract.v1` remains unchanged.
- Updated the public `install-agent-harness.sh` default ref to `v0.1.12` so
  off-tailnet installs resolve the same release by default.

## v0.1.11

- **Harness-neutral repo-validation contract.** `phase-loop repo-validate
  <target>` resolves and runs a repo's *explicit* local-first validation contract
  (`fast`/`gate`/`full`/`fix`/`affected`/`doctor`) so coding agents run the same
  checks locally, in worktrees, and in CI before opening PRs — GitHub stays the
  authoritative merge gate. Discovery is `just agent::<t>` (via `mod agent` +
  `agent.just`) then `package.json` `agent:<t>`; unmigrated repos **fail closed**
  (exit 20) rather than guessing `npm test`/`pytest`/`make test`. Frozen exit
  codes: 0 ok · 2 usage · 10 not-a-worktree · 20 no-contract · 21 runner-missing ·
  30 command-failed. The resolver (`phase_loop_runtime.repo_validation`) is
  stdlib-only with a pure `resolve()` split from `run_plan()`; Dagger is an
  optional, repo-owned posture (open-source Engine, no Dagger Cloud). Contract
  spec: `docs/repo-validation-contract.md`. Scope is the contract + neutral
  resolver + tests; per-repo checks and Dagger modules belong to consuming repos.
- **#66 — advisor-panel per-leg model override.** Each leg's model was hardcoded (`CLAUDE_IMPLEMENTER_MODEL`, `gpt-5.5`, `Gemini 3.1 Pro (High)`), so running e.g. the Claude leg on
  `claude-fable-5` required monkeypatching a module constant. `invoke_panel(..., models={"claude":
  "claude-fable-5"})` now overrides any subset per leg; unset legs use `DEFAULT_LEG_MODELS`.

- **#63 — advisor-panel advisory mode.** `panel_invoker` was hardcoded to a pre-merge
  code-review framing, so the three-model panel couldn't be used for general adversarial/advisory
  analysis (architecture, product, red-teaming a plan) — 2/3 legs replied "nothing to review".
  `invoke_panel(artifact, legs, mode="advisory")` now reuses all the leg-spawn machinery but swaps
  the framing and drops the AGREE/DISAGREE requirement (substantial prose is a real leg);
  `mode="review"` stays the default (back-compat, byte-identical behavior).

- **#64 — advisor-panel leg auth preflight + soft-empty-turn retry.** A logged-out CLI made the
  codex leg fail obliquely (an `rc=0` empty-turn, then rate-limit errors), so the panel silently
  degraded and the failure was misdiagnosed. `_exec_leg` now runs a cheap auth preflight
  (`codex login status`) before the expensive leg — a de-authed leg fails fast and classifies
  `DEGRADED` (never a silent empty leg) — and retries a transient soft empty-turn (`rc=0` + empty
  output) once, while never retrying a hard failure (`rc!=0`).

- **#48 — Advisor-panel Claude TUI leg no longer hangs on child exit.** The TUI read loop now
  treats PTY EOF (`os.read` → empty) as terminal: when the child CLI and its descendants close
  the pty, the leg returns a structured result (verdict if one landed, else an `ERROR`-classified
  `claude_tui_pty_eof_no_output`) instead of busy-spinning to the input-scaled (up to 30-min)
  deadline. Previously a lingering wrapper parent kept `proc.poll()` from firing while the EOF fd
  stayed "readable", hanging the panel indefinitely. On EOF the canonical review **file** is the
  only OK path; a transcript-scraped verdict is salvage evidence only (non-zero rc, fail-closed) —
  never promoted to OK, matching the sibling exit paths.
- **#52 — Actionable lane-IR closeout refusals.** When closeout/status fails closed on an
  unresolved Lane IR diagnostic, the `blocker_summary` now names the concrete diagnostic
  (`kind@lane` + message) and the phase-plan file location, instead of the opaque "Lane IR
  diagnostics failed closed for the current phase plan". The operator can repair the exact
  lane/contract (e.g. `missing_producer_dependency@SL-4: SL-4 consumes … without depending on
  it`) without guessing. The fail-closed contract is unchanged — diagnostics still block; they
  are just legible now.
- **#49 — Codex effort `max` → CLI `xhigh`.** The internal `max` effort tier (codex's top
  tier, used by the max-effort planner of record) is now translated to `xhigh` at the codex CLI
  boundary (`build_codex_command`). Previously `model_reasoning_effort="max"` was emitted verbatim
  and rejected by the codex CLI ("Invalid value: 'max'"), which misclassified the phase as
  `account_or_billing_setup`. Codex remains max-*eligible* in the policy/tier layer; only the CLI
  value is clamped to codex's real ceiling.
- **#47 — order-only cross-repo train dependencies.** `**Channel:** order-only` declares a
  merge-order (freeze) dependency with no channel injection — see the authoring guide.
- **#45 — `phase-loop train-status` command.** Non-mutating inspection of the cross-repo
  train ledger (`train-status --train <file> [--ledger-dir DIR] [--json]`) — the command the
  v0.1.11 run-train docs/skills already referenced but which did not exist. Resolves the same
  default ledger path as `run-train` and prints per-node status/branch/PR/merge-order/merged-SHA
  in topo order (pending nodes surfaced). Also corrects the stale `run-train --help` (the P4
  governed review + sequential merge path is implemented behind `--governed`).
- **#36 / dotfiles #135 — Advisor panel ownership and staged review packets.**
  `agent-harness` now owns the advisor-panel runtime primitive and harness-prefixed
  skill source. Codex and Gemini receive compact prompts that point to staged
  `review-instructions.md` / `review-bundle.md` files instead of embedded artifact
  bodies; Claude uses a Claude Code TUI session with `claude-sonnet-5`, max effort,
  subscription-safe env stripping, no `claude -p`, and a canonical scratch
  `panel-claude.txt` output file. Dotfiles can now reduce its
  unprefixed `advisor-panel` skill to compatibility guidance over this packaged source.
- **#33 / #39 — Runner hardening.** Background subprocess monitoring now preserves
  salvage evidence and bounds stale `quiet_unknown` children after a grace period when
  CPU sampling is unavailable, while still avoiding cleanup for CPU-active quiet work.
  Cross-repo live reverify honors hard-mode verification enforcement.
- **#29 — Cross-repo release-train coordinator.** `phase-loop run-train --train
  <roadmap>` orchestrates multi-repo changes in a single atomic train: draft PRs
  open across all nodes in topo order (P3), a train-level governed review gates
  the full diff, then nodes merge sequentially with each downstream re-verified
  against the upstream **MERGED SHA** before its own merge (P4). Safety invariants
  are enforced structurally and asserted in the CI suite:
  - **Zero PRs on preflight failure** (train-schema validation T-A/B/C/D runs before
    any `publish_from_worktree` call).
  - **No merge before train approval** — review panel rejection is a non-human
    terminal (`terminal_blocker.human_required=False`, zero `merge_pr` calls).
  - **False-green killer** — `set_upstream_ref` is called with the upstream MERGED
    SHA (not the draft SHA) ordered before `reverify_fn`; asserted by call-log
    capture in `tests/test_train_invariants.py::TestInvariant2FalseGreenKiller`.
  - **Forward-only guard** — downstream re-verify failure halts the train at that
    node; upstream merges stay merged.
  - **Train state off `.phase-loop/`** — `append_record` raises `ValueError` on
    violation; the ledger is caller-supplied and coordinator-side only.
  - **Autonomous boundary** — `run_mode="autonomous"` stops at `drafts_open`;
    `--governed` is required to proceed to review + merge.
  - **Crash-resumable** — the JSONL ledger records `merged`/`pr_open`/`blocked`
    per node; a second run skips already-merged nodes and retries blocked ones.
  - Documented limitation: the merged downstream PR carries the draft-time upstream
    pin (not the merge-commit SHA); safe under expand/contract upstream contracts.
  - New `run-train` workflow skill (`claude-run-train`, `codex-run-train`,
    `gemini-run-train`, `opencode-run-train`) added to `REQUIRED_SKILLS` and the
    harness-skill-matrix; regenerated bundle + synced `skills_bundle/`; parity/drift
    gates green.
  - Protocol doc (`_contract_docs/phase-loop/protocol.md`) extended with: train
    ledger shape, merge-SHA-pinned cross-repo gate, six numbered invariants, the
    expand/contract limitation, and the train roadmap authoring format.
  - `README.md` extended with a "Cross-repo release train" section.

## v0.1.10

- **#28 — execute skills default to a published review surface.** `execute-detailed`
  and `execute-phase` no longer leave a verified implementation as dirty changes in the
  primary checkout with no PR. A human-invoked run now publishes a branch + PR by default
  after verification, under a hard safety invariant: never commit to `main`/a protected
  branch or from a dirty primary checkout; if not already on a clean dedicated branch,
  work in a worktree off a fresh base ref. `execute-detailed` gains a publication flow
  (runner/manifest carve-out, pre-edit preflight, scoped staged-diff audit, push-rejection
  stop, skipped-verification→draft); `execute-phase` gains a 3-state publication mode that
  **stops rather than merging lanes onto `main`/a protected branch** and defers to the
  runner (incl. governed mode) when the runner owns closeout. Merge/force-push/reset stay
  gated by explicit instruction. Source-edited in the `skills-src/` canon (harness-neutral;
  collapses to the build base) + regenerated bundle; parity/drift gates green.
  Decision-panel-reconciled. Cross-repo publication is a separate follow-up (#29).
- **Skills-cutover cleanup (#26 → #30).** Repointed the dead `vendor/phase-loop-runtime/baml_src/emit_phase_closeout.baml` reference to the package-relative `phase_loop_runtime/baml_src/...` (resolvable wherever the runtime installs); preserved the `Claude Opus 4.8` co-author trailer through the harness brand-substitution (added to `PRESERVE_LITERALS`); added a backstop lint flagging any unguarded `claude-*` literal in `skills-src/`; bumped the installer pin. Item 2 (install-time body skill-name re-expansion) remains a deferred follow-up.

## v0.1.9

- **Canonical in-repo skill sources + hard parity gate (#25 → #27).** `skills-src/<harness>/` is now the authored canonical source; `build_bundle.DEFAULT_SOURCES` points at it, so the bundle builds entirely from the agent-harness tree with **no dotfiles checkout**. Added `scripts/regenerate_skills_bundle.py` (the one-command regenerate) and a hard parity gate (`tests/test_skills_canon_parity.py` + `.github/workflows/skills-parity.yml`) asserting the committed `phase-loop-skills/` equals `build_bundle(skills-src/)`. The fleet `bootstrap.sh --source <dotfiles-root>` path is unchanged.

## v0.1.8

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
