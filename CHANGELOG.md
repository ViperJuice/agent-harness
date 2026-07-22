# Changelog

All notable changes to `agent-harness` (the `phase-loop-runtime` package + the
`phase-loop-skills` bundle) are documented here. This project adheres to semantic
versioning; the release tag, the package `version`, and this file are kept in lockstep.

## [Unreleased]

### Visual-avatar-evidence closeout gate (FAV, Consiliency/agent-harness#91)

New opt-in-to-block closeout validator, `visual_avatar_evidence_validator`, mirroring the
`verification_evidence_validator` pattern: registered via `@register_closeout_validator`,
`block`-severity, force-downgraded to `warn` (recorded, non-blocking) under the default
`PHASE_LOOP_REVIEW=warn` posture, blocking only when opted in.

- **Detection contract** (`models.avatar_visual_evidence_required` /
  `avatar_visual_evidence_required_for_plan`): a phase requires blocking visual evidence only
  when BOTH a STRUCTURAL signal (the phase owns/touches a browser HTML fixture or a file
  named for media rendering — `getUserMedia`, `MediaStreamTrack`, `getDisplayMedia`, a
  canvas/video/camera/session/track renderer, an avatar renderer) AND an EXPLICIT CLAIM
  signal (the plan text makes an explicit user-visible rendering claim as a deliverable, e.g.
  "visible avatar", "renders in the browser/meeting UI", "browser call-in", "synthetic
  media"/"MediaStream target", "getUserMedia target") hold. A bare keyword hit on only one
  axis — e.g. a plan that just "tests video parsing" or "runs in a browser" — stays silent,
  same false-positive scoping discipline as #243. Legacy phases with no media surface get no
  finding.
- **Visual-evidence schema** (`models.VisualEvidenceObservation`, schema
  `visual_evidence_observed.v1`): pixel-level observations (`non_black_pixels`, `pixel_min`,
  `pixel_max`, tolerant of the camelCase `nonBlackPixels`/`pixelMin`/`pixelMax` a browser tool
  emits) attached alongside a `visual_evidence_path` screenshot/video artifact. `is_valid()`
  rejects an all-black frame (`non_black_pixels == 0`) and a uniform/blank frame
  (`pixel_min == pixel_max`, e.g. a solid `#f3f3f3` gray).
- **Typed opt-out**: a `visual_evidence_opt_out` reason (`no_visible_media_surface`,
  `visual_deferred_to_later_phase`, `operator_attested_manual`) suppresses the finding,
  mirroring `verification_evidence_opt_out`.
- **Reconcile/manual-repair guard**: `phase-loop reconcile` now re-applies the same
  detection + evidence contract before promoting a matching phase to `complete`
  (`--visual-evidence-path` / `--visual-evidence-observed` / `--visual-evidence-opt-out`),
  reusing the SAME contract functions and pixel-validity check as the closeout validator so
  the two call sites cannot diverge. Warn-default still applies: under
  `PHASE_LOOP_REVIEW=warn` (default) a missing/blank shortfall is recorded on the
  `manual_repair` audit trail but the promotion proceeds; only `PHASE_LOOP_REVIEW=block`
  refuses it. Never sets `human_required`.

### Verification-evidence hardening: whole-artifact integrity + closeout-diagnostic redaction (#243)

Follow-up to #242 (#209), which preserved per-failing-stage raw diagnostics via a
per-stage byte-offset scheme but explicitly scoped out two hardenings. Both now land:

- **Whole-artifact integrity.** `log_sha256` sealed `verification.log` but not
  `verification.json`, so a multi-field / structural edit — e.g. deleting a failed
  `commands[]` entry to forge a pass — was undetected. `run_verification` now embeds a
  canonical digest of the artifact (all fields except the derived `log_sha256`) as a
  `verification-artifact-sha256:<hex>` trailer line in `verification.log` before computing
  `log_sha256`, so the log's SHA also seals the artifact digest. On the would-be-PASS path
  `validate_verification_artifact` recomputes and compares the digest, returning
  `artifact_seal_mismatch` (fail closed) on any mismatch. An oversized artifact is rejected
  `oversized_artifact` before parsing. Additive + backward-compatible: an artifact whose log
  carries no seal trailer (v1/older, or an externally-built log) still validates.
- **Closeout-diagnostic redaction.** A failure diagnostic's bounded `raw_tail` excerpt of
  `verification.log` bytes is redacted to metadata-only before it enters the persisted
  closeout record when it (or an `argv` token) carries a secret/PII-shaped value — detected
  with the same `_FORBIDDEN_METADATA_PATTERNS` the closeout malformed-metadata gate enforces
  (this also removes a latent false `malformed_closeout` block). The on-disk
  `verification.log` is left full; `PHASE_LOOP_VERIFY_REDACT_DIAGNOSTICS=all` forces full
  `raw_tail` suppression. (Consiliency/agent-harness#243)

## [0.7.10] - 2026-07-21

### `verification.json` records the phase alias on the train re-verify path (#236)

Follow-up to #235 (ah#85b), which threaded the live run alias into `run_verification`
on the execute path. The train re-verify path (`train_runner._live_reverify`) also calls
`run_verification` but passed no `phase_alias`, so `verification.json` fell back to
`current_phase` → `'unknown'` even though a phase alias was already resolved in scope. It
now threads the resolved (reconcile-recomputed) phase, so a re-verify records the actual
phase rather than `'unknown'`. (Consiliency/agent-harness#236)

### Panel/advisor-board: a codex review is no longer mislabeled DEGRADED for discussing auth (#252)

`_classify_leg` scanned a leg's full transcript for auth-error signatures; because the
codex leg's `log_text` includes its stdout+stderr (codex echoes its own review onto
stderr), a clean, conforming codex review whose prose merely *discusses* "unauthorized" /
"rate limit exceeded" (routine in security/auth reviews) was forced to `DEGRADED` and the
operator told to discard a valid review. A conforming **review-mode** `rc==0` verdict is
now classified `OK` before the auth scan (its terminal-verdict predicate is one a
de-authed CLI can't fake). Advisory mode keeps auth-scan-first (its weaker length
predicate could otherwise fail open on a real banner), preserving the #63 behavior.
Fail-closed for genuine auth failures is unchanged. (Consiliency/agent-harness#252)

### Dual-declared run-family options survive before the subcommand (#233)

Follow-up to the argparse copy-back clobber fixed in #84/#232. Those PRs added
`default=argparse.SUPPRESS` to the common subparser args (`_add_common_subparser_args`),
but the same clobber still hit options that are dual-declared (top-level **and** on the
run/resume/dry-run subparser) outside that helper. So `phase-loop --force-replan run`
silently dropped to `False`, `phase-loop --allow-cross-phase-dirty REASON run` to `None`,
and likewise `--rotate-executors`, `--rotation-mode`, `--rotation-on-policy-pin` (the last
two also clobbered the top-level `"phase"`/`"skip"` defaults to `None`, masked at the call
site by `or "phase"`/`or "skip"`), `--full-phase`, and `--no-deprecation-hints`. Each
subparser copy now carries `default=argparse.SUPPRESS` (identical to #232's pattern), so a
value parsed before the subcommand survives in both option positions. The top-level parser
still declares each option with its concrete default, so the attribute is never absent from
the final namespace. CLI-only fix. (Consiliency/agent-harness#233)

### Planner skills emit and reference goal IDs — goal-ID Increment 2 (#211)

The planner **skills** now produce roadmaps/plans that use the decidable goal-coverage
mechanism shipped in Increment 1 (#247), so it applies to real roadmaps, not just
fixtures. All four harnesses' `phase-roadmap-builder` emit each exit-criterion led by a
stable goal ID — `- [ ] EC-<ALIAS>-<N> — <assertion>` — with the convention documented
(all-or-none per phase, unique + alias-scoped, gaps allowed, never reuse/renumber a
deleted ID). All four harnesses' `plan-phase` and `plan-detailed` author acceptance
items that **reference** the roadmap goal by its `EC-<ALIAS>-<N>` ID and name the
proving command, instead of restating (and drifting from) the goal text — so validation
always checks the plan against the original goal and a plan can never silently weaken
it. `validate_plan_doc.py` gains a `(P)` warn: when the anchored roadmap phase declares
goal IDs, every declared ID should be referenced by an acceptance item and every
reference should resolve (no dangling); legacy phases with no goal IDs get no finding.

Opt-in and non-breaking: existing `specs/phase-plans-v*.md` roadmaps are untouched and
stay `not_applicable` until re-authored; a phase that declares no goal IDs uses the
prior testable-assertion authoring. No runtime code change (Increment 1 ships the
mechanism); the skills bundle is regenerated and byte-parity is green. (Consiliency/agent-harness#211)

### Broker reconciles publish scope with the branch's actual content (#202)

`publish_committed_branch` publishes the whole committed branch by `(repo, branch,
head_sha)`, and the admission's approval digest covers the coordinator-supplied
`owned_paths` — but the broker never checked that those `owned_paths` actually
matched what the branch changed. The broker now **re-derives the branch's diff vs its
declared base itself** (`origin/<base>...head_sha`, three-dot — the same derivation
the #201 coordinator uses) and refuses to publish when the admitted `owned_paths` do
**not** cover what the branch actually changed, catching drift or coordinator bugs
where the declared scope diverges from the real branch content. Every outcome fails
closed *before* any push, and each is a **proven no-effect** (`no_effect_terminal_proven`
— the push is never reached) distinguished only by its detail string: a changed path
outside the admitted scope → `owned-scope-exceeded:<paths>`; a non-empty `owned_paths`
with an empty branch diff → `owned-scope-empty-diff` (also catches a gamed `base==head`
ref); a git-diff failure → `owned-scope-diff-failed`. A #202 reject has zero *mutation*
ambiguity (nothing was pushed), so it is not `outcome_ambiguous_blocked` — which is
permanent and poisons the repo's broker epoch, a blast radius a purely-local read-only
git failure must not trigger. `no_effect_terminal_proven` is also the only valid
`provider_call_in_flight →` reject transition (`rejected_before_start` is reachable only
pre-intent, and the service records intent before the adapter runs). Directory
`owned_paths` entries cover files beneath them, so an over-specified scope never
false-rejects.

Scope note: `base` is a coordinator-supplied ref *name* (not the digest-bound
`base_sha`), so this reconciles against the **declared** base — it closes accidental
drift and coordinator bugs, not a coordinator that deliberately games the base ref
(binding `base` into the approval digest would be the stronger, separate step). Only
the `run_train` prebuilt coordinator path reaches this check today, and it runs in the
node workspace where `origin/<base>` is already present, so there is no added fetch or
regression. `BrokerRequest` gains a `base` field (default `main`);
`publish_from_worktree` gains a matching `base` parameter; the `run_train` prebuilt
path threads the same `_DEFAULT_BASE` its owned-paths were derived from. Follow-up
hardening from the #201 panel (codex + grok). (Consiliency/agent-harness#202)

### codex review-leg cannot write the reviewed live tree (#177)

The product-loop `review` action's codex leg was built with `--sandbox
danger-full-access` (or `--dangerously-bypass-approvals-and-sandbox`) against the
live worktree — the same as write actions — so a codex review leg could mutate the
reviewed tree. The `review` action now points codex at a **staged copy** instead of
the live repo (matching the agy review leg), the airtight barrier for
`IF-0-SANDBOX-1` (`build_codex_command` gains a `read_only` flag;
`build_codex_launch_spec` threads `read_only=(action == "review")`; all winning over
`bypass_approvals`):

- **`--cd <staged copy>`** — the review leg's working directory is a throwaway,
  gitignore-aware copy of the tree (via the same `_stage_review_tree` agy uses),
  materialized at launch and cleaned afterward. codex cannot reach the live tree at
  all, regardless of which config layer (user / system / enterprise) declares an
  out-of-sandbox MCP server. This is the primary guarantee.
- `--sandbox read-only` and `--ignore-user-config` are kept as defense in depth
  (block shell writes; drop user-config MCP; auth still resolves via `CODEX_HOME`;
  `--model`/`model_reasoning_effort` are passed explicitly so the run is hermetic),
  and `--skip-git-repo-check` because the staged copy carries no `.git`.

A cross-vendor review blocked an earlier flag-only fix (`--sandbox read-only` +
`--ignore-user-config`) as an overclaim: MCP tools run **outside** codex's shell
sandbox (openai/codex#4152) and can be declared in config layers `--ignore-user-config`
does not drop, so only the staged copy makes the guarantee airtight. This brings the
codex review leg to the `IF-0-SANDBOX-1` bar the other vendors already meet (agy =
staged copy, grok = read-only `--tools` allow-list, claude = plan/read-only), with a
filesystem-level write-proof test.

The shared review-stage cleanup was also hardened (benefiting the agy leg too): the
launch-time cleanup now removes the **exact** copy paths the materializer created,
rather than inferring ownership from an `--add-dir`/`--cd` argv basename — so an
*execute* run against a live repo that happens to be named `pl-review-stage-*` can
never be deleted. Stage materialization now self-cleans if the copy or a later
launch-time resolution step (e.g. output-schema materialization) fails, closing a leak
window since staging runs before the launch cleanup `finally`.
(Consiliency/agent-harness#177)

### Goal-ID single source of truth — Increment 1 (#211)

Redefines #211 from a fuzzy text-diff audit (proven undecidable) into a decidable
**goal-coverage** check by removing the duplication between a roadmap's goals and a
plan's restatement of them. Roadmap phase exit-criteria may now carry stable
`EC-<ALIAS>-<N>` **goal IDs** (mirroring the `IF-0-<ALIAS>-<N>` gate scheme);
`roadmap_lint` reconciles them (alias-scoped, unique, **all-or-none per phase**, gaps
allowed so a deleted criterion never forces a renumber that would silently re-bind a
downstream reference). `Phase.exit_criteria` stays `list[str]` (API-compatible); IDs
are exposed via an additive accessor.

A plan's `## Acceptance Criteria` items **reference** the goal IDs (item-leading:
`- [ ] EC-P1-1 — proven by <test>`) instead of restating the goal. The new
`goal_coverage.check_goal_coverage` verifies — by pure set membership, no
word-matching — that every declared goal ID is referenced by ≥1 acceptance item;
an unreferenced goal or a dangling/typo'd reference is a `contract_bug`, a prose
mention (not item-leading) does not count, and a phase with no EC-IDs is
`not_applicable` (legacy, opt-in per phase — nothing existing breaks). It runs at
three points, all pure Python with **no `EmitPhaseCloseout` schema change**: a
`goal-coverage-audit` CLI (exit 0/1/2), the phase-loop **preflight**, and a
**closeout** re-check (mirroring the IF-gate `Produces` precedent, closing the
window where a plan is edited mid-execution and loses a reference). Preflight and
closeout are warn-default / opt-in block via `PHASE_LOOP_ACCEPTANCE_ENFORCE=block`,
never `human_required`.

**Honest scope:** this guarantees *completeness* (no goal silently forgotten); it
does **not** verify *adequacy* (that the referenced test actually discharges the
goal) — that stays with code review + evidence authenticity (#91). It makes weak
evidence human-reviewable at the reference point rather than hidden in a paraphrase.
Increment 2 (planner skills emitting/referencing IDs by default + roadmap migration)
is deferred behind a go/no-go. Design + hardenings converged through a cross-vendor
plan review (codex + gemini + Fable).

### Preserve raw failure diagnostics on verification failure (#209)

When runner-executed verification failed, the runner-owned verdict scrubbed the
failing stage down to a bare `exit_code` — the raw reason survived in
`verification.log` but never reached the terminal/closeout record, a named
contributor to multi-day thrash (#213). The verification verdict
(`VerificationArtifactValidation.to_json()`, consumed into the persisted closeout
record) now carries a `diagnostics` list: one entry per failing stage, in declared
order (`commands` → `env_refresh` → `suite`), each with a typed `failure_kind`
(`timeout` / `error` / `nonzero_exit`), the stage's `argv`/`exit_code`, and a
bounded `raw_tail` sliced from that stage's **exact** log region. A failed verdict
can no longer be diagnostic-empty: a silent (output-less) failure still surfaces
typed context flagged `diagnostic_status: missing_output`. Multi-step chains keep
per-step order and reduce fail-closed (an earlier failing step blocks even if a
later step passes).

To localize each stage exactly, `verification.json` is bumped to **schema v2**
(additive): every stage may carry `log_end_offset` + a runner-observed
`failure_kind`, and `env_refresh`/`suite` now record their own `log_offset` (the
suite's start offset was previously discarded, making post-hoc boundaries
uncomputable). `failure_kind` is captured at execution time, never re-derived from
`exit_code`, so a child that itself returns 124/127 is not mislabeled a
timeout/missing-executable. **v1 artifacts still load** (new fields default null;
`load_verification_artifact` accepts `schema_version` in `{1, 2}`). Diagnostics are
built only on the sha-authenticated `nonzero_exit` branch — an unauthenticated log
(sha mismatch / missing) blocks on the integrity failure without surfacing a
possibly-forged tail. The bounded `raw_tail` is an excerpt of bytes already
persisted in `verification.log` at the same trust level; a follow-up covers opt-in
closeout-diagnostic redaction. Design converged through a cross-vendor plan review
(codex + gemini + Fable) that rejected a post-hoc verdict-only approach.

### Versioned/absolute suite-interpreter guard, redone robustly (#221)

`#219a`'s interpreter shim prepended a PATH dir whose bare `python`/`python3` resolve to a
`requires-python`-satisfying interpreter, but a suite (or a plan-level `commands` verification
bullet) that explicitly named a **versioned** interpreter — `python3.10` — bypassed the bare-name
shim and could run GREEN under an unsupported interpreter. The regex string-scan detector #220
shipped for this was removed as unsound (fail-open on shell metacharacters + wired only to
`suite_command`; false-block on `pythonX.Y` string literals / env paths). It is now redone at
**executable-resolution** level: whenever a `requires-python` constraint exists, the shim also
shadows every **non-satisfying** `python3.X` name (below OR above a bounded specifier, via the
PEP 440 predicate) with a **fail-closed wrapper**, on every path where the shim is built (pin,
host-default-satisfies, and auto-resolve). Because it intercepts the executable name (not a command
string), `python3.10&&pytest` is caught while `python3.12 -c 'print("python3.10")'` and
`PYTHONPATH=/opt/python3.10 pytest` are no longer false-blocked, and both the `suite_command` and
`commands` paths are covered. The shadow set spans a wide fixed name range (`python3.0`–`python3.39`
plus `python2*`), decoupled from the bounded host-probe list, so an old `python3.7` or a future
`python3.15` cannot reopen the hole. Patch-level constraints are handled precisely: a candidate
present on the host is compared at its **full** version, so `python3.11` is shadowed under
`<3.11.5` when the host's is 3.11.9 (fail-closed, not fail-open) and is NOT shadowed under
`>=3.11.5` when the host's is 3.11.9 (no false-block). When the host default already satisfies, the
shim carries only the shadows and leaves bare `python`/`python3` untouched, so an active venv is
preserved. The interpreter version is probed with `cwd=repo`, so a version-manager shim
(pyenv/asdf) is measured under the same `.python-version` context the suite runs in. A **login
shell** (`bash -lc`) that re-sources a PATH-reordering profile is handled by re-prepending the shim
inside the `-c` payload (which runs after profile loading), so the shim wins even against a profile
that puts a below-floor `python3.X` first. Escape hatches (the operator's explicit declared
environment, each strictly harder for an operator than the guard it evades): an **absolute-path**
interpreter (`/usr/bin/python3.10`) which bypasses PATH entirely; and — deferred to #241 for
hardening with proper planning — exotic `bash --login -O opt -c` option forms and an interpreter
absent at resolve time but introduced by the login profile under a patch-level constraint.

### Reconcile can recover a completed phase from a tracked closeout artifact (#90)

`phase-loop reconcile --verification-log <closeout.md>` rejected a tracked, committed closeout
markdown as `malformed_artifact` because it validates the path as a runner-owned
`verification.json`. Operators whose roadmap already reached CLOSEOUT with durable committed
closeout artifacts — but no ephemeral `.phase-loop/runs/verification.json` (e.g. after an
interrupted session) — could not rehydrate completed phase state and were stuck re-planning from
the start. Reconcile now accepts a new **`--closeout-artifact <path>`** flag for artifact-backed
recovery: it adopts a **git-tracked, committed** closeout markdown as recovery evidence
(provenance `tracked_closeout_artifact`), requiring `--closeout-commit`, `--repair-summary`, and
`--verification-status`. This is an **audit anchor + provenance label** for an explicit,
operator-reasoned manual recovery — not a runner verification pass and not an authorization gate.
The evidence is bound before acceptance: the `--closeout-commit` must resolve to a real commit
reachable from `HEAD` (the index `:0`, tree-ish, and orphan commits are rejected), the path must
be a **non-empty regular blob** at that commit (a directory, symlink, or gitlink is rejected), and
the basename or content must reference the phase (an unrelated tracked file is rejected). It never
satisfies a phase that hard-requires runner verification (RG / `IF-0-RG-1`), is mutually exclusive
with `--verification-log`, and the existing runner-verification path is unchanged (still rejects a
markdown as `malformed_artifact`). The provenance is surfaced at the status boundary
(`Closeout verification: passed (recovery evidence: tracked_closeout_artifact)`), so consumers can
tell a recovery apart from a runner pass. This is an audit anchor for an operator-reasoned
recovery, not an authorization gate; ancestry is checked with grafts/replace-refs neutralized, so
an active `.git/info/grafts` or a **shallow clone** is rejected (`closeout_commit_ancestry_untrusted`)
— unshallow (`git fetch --unshallow`) before recovering in a shallow checkout.

### Reconcile/status survive a relocated repo root (#85)

`.phase-loop/` state and events persist **absolute** repo/roadmap paths. When the directory
was copied into a git worktree or the repo root moved/renamed (e.g.
`/home/user/code/avatar-client` → `/mnt/workspace/worktrees/…`), `reconcile`/`status` compared
the stored absolute roadmap path against the live one, found them unequal, and silently
discarded every persisted phase status — replaying as all-unplanned. The roadmap
path-equality gates in `reconcile` and `classifier` now match **repo-relative** (via the new
`runtime_paths.roadmap_paths_match`, which keeps identical-absolute as a fast path and falls
back to non-match when a path cannot be relativized), so persisted status and operator
breakglass attestations survive relocation. Content-SHA provenance
(`roadmap_sha256`/`phase_sha256`) remains the integrity backstop at every gate, so a genuinely
different roadmap sharing the relative path is still rejected. A single informational
`repo_relocated` ledger warning is emitted once per reconcile (whether the relocation is
detected from the persisted snapshot or, for an events-only replay, from the first relocated
event). Operator **breakglass SL-2 attestations** (`lane_ir_override`, `closeout_allow_unowned`)
deliberately do **not** relocate: an operator authorization is bound to the repo root it was
granted in, so those two gates stay fail-closed to the original absolute path and require
re-attestation in the new location. `verification.json` is untouched.

### `verification.json` records the live run phase alias (#85)

`verification.json`'s `phase_alias` was re-derived from `.phase-loop/state.json:current_phase`,
so it could disagree with the run's `terminal-summary.json` phase (and mis-attribute the phase)
after a mid-run roadmap amendment changed `current_phase`. On the execute path the runner now
threads the **live run alias** into `run_verification` (new optional `phase_alias` param), so the
artifact is attributed to the phase that actually produced it. The `PHASE_LOOP_PHASE_ALIAS` env
override still wins, and callers with no live run alias (hotfix / train re-verify) keep the
`current_phase` fallback — behavior unchanged for them.

### Common options before the subcommand no longer silently reset (#84)

`phase-loop --phase ROOM run` (and any common option placed BEFORE the subcommand —
`--max-phases`, `--model`, `--effort`, `--json`, `--dry-run`, …) was silently reset to its
default by the subcommand parser's copy-back, because the per-subcommand copies of those
args lacked `default=argparse.SUPPRESS`. `--phase ROOM` became `phase=None`, so the runner
fell through to repairing a blocked phase instead of dispatching the requested one. Every
common subparser arg now carries `SUPPRESS` (matching `--closeout-mode`/`--pipeline-mode`/
`--lane-scheduler`), so a value placed before the subcommand survives — an explicit
`--phase` reaches the dispatcher and the requested phase runs. The dispatcher was correct;
this is a CLI-only fix.

### Panel/CR grok leg no longer errors on default runs — effort clamped to grok's CLI ceiling (#222)

The panel / advisor-board grok leg hard-coded `--reasoning-effort max` on the default
path, but grok's CLI accepts only `high | medium | low` and rejects `max`, so the grok
leg **errored on every default panel/CR run** (it never contributed a review). The
panel-path effort is now clamped to grok's ceiling (`max → high`), so the grok leg runs
at its real maximum instead of failing. Sibling of the grokexec-path clamp in #224.
(Consiliency/agent-harness#222)

### grokexec leg clamps `--reasoning-effort` to grok's CLI subset (#224)

The grokexec/launcher grok leg passed the requested effort **raw** to
`grok --reasoning-effort`, but grok's CLI accepts only `high | medium | low`, so an
**explicit** `max` / `xhigh` / `minimal` grokexec run (all in `NORMALIZED_EFFORT_LEVELS`)
crashed grok (`unknown effort level 'max'; use one of: high, medium, low`). A new
CLI-boundary clamp `launcher._grok_cli_effort` translates `minimal→low` and
`xhigh`/`max`→`high` (grok's ceiling), mirroring codex's `max→xhigh` and the panel-path fix
in #222/#225 — so an explicit high-effort grokexec run is honored at grok's ceiling instead
of crashing. The default path (`medium`) was already valid. The grok provider policy's
misleading "accepts a superset / no clamp needed" note and its dead identity `effort_map`
are corrected. Sibling of #222.

### Advisor-board `available_panel_legs()` exposes grok (#171)

The documented panel preflight `available_panel_legs()` now considers all four vendors
(codex, gemini, claude, **grok**) and returns those whose CLI is installed — so a caller
whose `gemini`/`agy` leg is down transparently reaches a 4th independent vendor (grok)
through `invoke_panel(..., available_panel_legs(), ...)` instead of hand-rolling the grok
CLI (which naively inlines the bundle into argv and chokes). Availability-aware: grok
appears only when the grok CLI is present, so a host without it still returns the exact
frozen 3-tuple. The byte-frozen `PANEL_LEGS` keystone and the advisor-board goldens are
unchanged — grok is added only to the separate availability list the preflight iterates.

### Panel Fable/Claude TUI leg clears the workspace-trust gate (#196, #223)

The advisor-board Fable/Claude reviewer leg drives Claude Code headless under a
self-allocated PTY. Claude Code shows an interactive **workspace-trust modal** for
the fresh scratch `cwd` before it accepts a prompt; the leg used to bracket-paste
the review prompt after a fixed 8s delay, landing it in the `Enter y/n:` field —
so no reviewer session ever started and the #188 liveness monitor reclaimed it
after 180s and mislabeled it `claude_tui_stalled`. Net effect: the Fable
correctness seat was effectively dead on panels/CRs. Root cause and the exact
modal/editor rendering were confirmed by a real PTY capture on Claude Code 2.1.208.

- **Startup state machine (fix).** `_run_claude_tui_session` now detects the
  workspace-trust modal against the accumulated de-ANSI'd screen (the modal spans
  multiple lines), answers `y` exactly once — **strictly pre-submit, path-scoped to
  the harness-created scratch cwd, and disarmed the instant the review prompt is
  pasted** — then submits only when the editor is quiescent after real post-gate
  output (never on a blind timer into a possibly-modal screen). The PTY is opened at
  a wide window so a long `/tmp` cwd path renders un-wrapped. Because auto-answering
  is disabled once the prompt is submitted, review output or a reviewed diff that
  contains the trust strings can never inject a keystroke or mis-classify a healthy
  review.
- **Typed, fail-closed diagnostics (fix).** An uncleared trust gate now yields a
  typed `claude_tui_workspace_trust_blocked`, and a never-ready editor yields
  `claude_tui_editor_not_ready` — both evaluated **before** the 180s generic stall
  and surfaced as `DEGRADED` (not the misleading `claude_tui_stalled`). These
  operational failures carry **empty** review text so the governed-review classifier
  records a non-gating `panel_leg_degraded` warning (availability-aware degrade) rather
  than a promotion-blocking nonconforming review; the bounded, credential-redacted,
  control-stripped PTY tail (the buffer *end*) is preserved as a `WARNING` log for
  diagnosis.

### Governed closeout & gate-integrity hardening (#218, #219)

Three bounded fixes so a phase cannot be marked `complete`/`verification: passed`
when its evidence does not support it. The wire contract (blocker-class enums,
closeout JSON schema, IF-gate grammar) is unchanged — only derivation logic.

- **Dir-aware ownership classification (fix).** When a phase's owned deliverable
  is a brand-new directory and the executor self-reports the *collapsed* bare
  directory (`pkg/newmod/`) instead of its member files, a file-level owned glob
  (`pkg/newmod/*.py`) never matched the bare-directory string, so the path routed
  to the unowned remainder and tripped a spurious `closeout_scope_violation`. A
  new `git_ops.expand_dir_dirty_paths` normalizes a directory entry to its member
  files (via `git status --porcelain --untracked-files=all -- <dir>`) before
  ownership matching, applied in the closeout fallback classifier; the ownership
  matcher also gained a directory-prefix guard as defense-in-depth. A new
  all-owned directory now closes `complete` with the directory committed.
  (agent-harness#218)
- **Non-zero suite/command exit now fails closed (fix).** A VerificationResult
  with any non-zero command/suite/env-refresh exit forces `verification_status` to
  failed/blocked at closeout, overriding an executor's self-asserted `passed`, and
  blocks the runner-owned verification reduction — always, irrespective of
  `PHASE_LOOP_VERIFY_ENFORCE` (which continues to soften only evidence-integrity
  findings like log-sha drift). The exit codes are read **directly from the
  artifact's `exit_summary`**, not just the first-failing validation code, so a red
  suite accompanied by a tampered/missing log (which reports `log_sha256_mismatch`/
  `missing_log`) can no longer shadow the red suite into a warning. A red suite is
  never a warning. Additionally, any governed phase whose plan declares an
  `automation.suite_command` now requires a VerificationResult artifact, so a
  self-asserted `passed` with no evidence can no longer close ungated.
  (agent-harness#219) (An unparseable/`malformed_artifact` supplied with a
  self-asserted `passed` still only warns under `warn` — a separate evidence-
  integrity gap, tracked for a follow-up.)
- **requires-python-aware suite interpreter (fix).** The verification suite now
  runs under an interpreter satisfying the target repo's `requires-python`: an
  optional `automation.python` plan pin wins **but is validated against
  `requires-python`** (a pin below the floor fails closed), otherwise the lowest
  satisfying host `pythonX.Y` is resolved and shimmed onto the suite subprocess
  `PATH`. The shim covers bare `python` as well as `python3` — if either present on
  `PATH` is below the floor, both names are shimmed onto a satisfying interpreter.
  An `env_refresh` pip install runs under the **same** resolved interpreter as the
  suite (not the host `sys.executable`), so deps are visible to the suite. When no
  satisfying interpreter exists the suite fails closed with a named blocker
  (recorded as a non-zero suite exit). The interpreter resolution (pin +
  requires-python auto-resolve) is honored on **all three** verification paths —
  execute, train-reverification, and hotfix. This removes the py3.10-vs-
  `requires-python>=3.11` false failure that previously needed a manual shim.
  *Known limitation:* the shim only redirects a bare `python`/`python3`; a suite or
  verification command that **explicitly** names a versioned/absolute interpreter
  (e.g. `python3.10`, `/usr/bin/python3.10`) below the floor is not caught and can
  still run under an unsupported Python — tracked separately in agent-harness#221.
  (agent-harness#219)
- **Safe gitignore handling at closeout (fix).** The gitignored exclusion no
  longer drops OWNED paths from `phase_owned_dirty_paths`: it applies to the
  *unowned* classification only, so a tracked-then-ignored owned file (real work
  that now also matches a `.gitignore` pattern) commits instead of being silently
  dropped (the #215 data-loss trap). Separately, the closeout fallback classifier
  now filters disposable byproducts the executor over-reports — paths that are
  BOTH untracked AND gitignored (`build/`, `*.egg-info/`, `.phase-loop/`,
  `.dev-skills/`) — so a disposable-only over-report with a genuinely-clean tree and
  passing verification finalizes as a no-op instead of a false
  `dirty_worktree_conflict` (the EXTRACT failure). The clean-tree check fails
  closed on an unreadable git probe (a probe error never reads as "clean"). A
  tracked file (even if ignored) is never treated as disposable. Collapsed owned
  directories are expanded to member files on the trusted path as well as the
  fallback, and the closeout `git add` force-adds only the **proven-tracked**
  members of the vetted path set — so a tracked-then-ignored file stages without a
  spurious non-zero exit, while an untracked+ignored path an executor wrongly
  reports as owned still fails closed rather than being force-committed.
  (agent-harness#186)

#### Round-4 cross-vendor CR: two fail-open / data-loss closures (#220)

- **Whole-verification interpreter fencing (fix).** When no host interpreter can
  satisfy the target's `requires-python` (or an `automation.python` pin is below
  the floor), `run_verification` now fences the **entire** verification, not just
  the suite: env-refresh, the `commands`, and the suite are all skipped and a
  non-zero (127) result is synthesized so the evidence gate hard-blocks.
  Previously the blocker fenced only the suite, so a plan with `commands` but **no**
  `suite_command` ran env-refresh + commands on the host default and a green exit
  produced a `passed` artifact — silently bypassing the pin/`requires-python`.
  (agent-harness#220)
- **Closeout disposable filter fails closed on a git-probe failure (fix).** The
  untracked-and-gitignored disposable filter no longer drops a path when it cannot
  prove the path is untracked. `_tracked_paths` now returns `None` on a `git
  ls-files` probe failure (distinct from an empty "nothing tracked" result) and
  the disposable computation drops nothing on `None`, so a transient probe failure
  can no longer misclassify a genuinely **tracked** file as a disposable byproduct
  and drop it (the #215 data-loss class under a probe failure). A collapsed
  bare-directory entry (`build/`) — which reaches the filter only when directory
  expansion's own git probe failed — is also never classified disposable, since
  string membership against `git ls-files` (which lists member files, never the
  bare-dir string) cannot prove the directory holds no modified tracked file; it is
  kept and blocks rather than being dropped. The closeout `git add` path likewise
  fails closed on a `None` probe (all paths get a plain add → a tracked-then-ignored
  file that fails the plain add blocks rather than being force-committed or dropped).
  (agent-harness#220)

## [0.7.9] - 2026-07-14

### Planning — validator enforces the producer-dependency contract (fail-fast)

- **`validate_plan_doc.py` now errors when a lane consumes an interface provided by
  another in-plan lane it does not depend on directly** (a new `(O)` check). Previously
  the plan validator passed such a plan (check F only traced that the interface was
  *provided* somewhere) and the phase-loop lane IR then failed closed at execute time
  with `missing_producer_dependency` — so a reviewed, signed plan could pass its
  canonical validator and design panel, then become non-executable at the
  approval/baseline gate. `(O)` **delegates to the runtime `phase_loop_runtime.plan_ir`**
  (the single source of truth) rather than reimplementing the parse/identity, so
  planning-time and execution-time enforce the *same* contract by construction — no
  interface-normalization or lane-parser divergence (verified: 0 divergences across all
  22 committed plans). When the runtime is not importable at plan time, `(O)` skips (the
  execute-time lane IR still enforces the contract). (agent-harness#182)

## [0.7.8] - 2026-07-13

### Convergence — the run-train live path now works from the shipped CLI

Completes the broker live-path the SPECPKGMIN pilot surfaced as unusable from the
shipped CLI. Together these make `phase-loop run-train` actually open draft PRs. The
routing broker keeps a **per-repo, per-train** admission/evidence store so one node's
ambiguous outcome fail-closes only that repo, never siblings or unrelated trains
(broker root keyed by the roadmap's resolved-path hash).

- **`gh pr create` is now a complete non-interactive argv (fix).** The broker's
  `GitHubBrokerAdapter` issued `gh pr create --draft` with no `--title`/`--body`,
  which aborts when gh is not attached to a tty ("must provide --title and --body")
  — the branch pushed but no PR opened (`outcome_ambiguous_blocked`). It now derives
  a title from the branch HEAD commit subject, passes the request's `pr_body` as
  `--body`, pins `--head <branch>`, and appends `--draft` for draft requests.
  (agent-harness#207)
- **`build_routing_broker_client` — one broker client serves a MULTI-repo train
  (new).** `build_github_broker_client` binds one `repo_path` at construction, so a
  single client could only serve one repo; a cross-repo `run_train` mis-bound
  `git -C <wrong-repo>` on node 2+. The routing client binds a fresh
  `GitHubBrokerAdapter` per `BrokerRequest.repo` (the node's resolved workspace) AND
  keeps a **per-repo** admission + evidence store under `broker_root/<repo-slug>`.
  Per-repo stores are load-bearing for safety, not just routing: `epoch_blocked` is a
  global scan over a store and an ambiguous terminal is durable + permanent and can be
  tripped by a benign transient (e.g. a one-off `ls-remote`/`gh` network hiccup →
  `remote-read-failed`/`pr-unconfirmed`), so a shared store would let one repo's
  transient permanently fail-close every other repo. Per-repo stores scope the
  fail-closed epoch to exactly the repo whose mutation became ambiguous.
  (agent-harness#206)
- **`run-train` CLI now wires a broker-authoritative coordinator (fix).**
  `_run_train_command` builds a `CoordinatorRuntime` carrying the routing broker and
  passes it to `run_train`. Previously the CLI passed no runtime, so every publish
  fail-closed `broker_required` and the train opened ZERO PRs. The broker root is
  namespaced **per train by the roadmap's resolved-path hash**
  (`<ledger-dir>/broker/<path-hash>`) — so two distinct roadmap files, even
  same-stemmed and even under one explicit `--ledger-dir`, get distinct broker roots
  and an ambiguous outcome in one train never fail-closes a different train. (Keying on
  the stable path rather than the content digest keeps a resumed train on its own epoch
  across roadmap edits.) (agent-harness#205)

### SPECPKGMIN

- **Harness dogfood of the `consiliency-spec-ingest` wheel + GP interchange seam.**
  An env-gated (`SPEC_ROOT`/`GP_ROOT`/`HARNESS_ROOT`) cross-repo integration test that
  builds + installs the wheel, runs `evaluate(...)`, and checks the GP interchange
  seam; bounds every child process with a timeout. (agent-harness#204)

## [0.7.7] - 2026-07-13

### run-train — prebuilt node publish mode (broker-mediated)

- **Land already-committed cross-repo branch work without re-executing the phase.**
  Train roadmap nodes accept a new `**Mode:**` attribute: `execute` (default,
  unchanged — runs the per-repo `run_loop` then publishes) or `prebuilt`, which
  publishes an already-committed, independently-verified branch WITHOUT any
  executor dispatch. A prebuilt node preflights as CLEAN **and** strictly ahead of
  `origin/main` (clean-but-not-ahead is a preflight error → zero PRs), skips
  `run_loop` and upstream injection entirely, and derives the PR's owned paths from
  the committed diff (`git diff --name-only origin/main...HEAD`).
- **The prebuilt publish is routed through the credential broker** — the same
  broker-mediated, exact-head-verified `publish_committed_branch`/`github` path
  execute nodes use (`prebuilt=True`, `broker_client`/`admission` from the
  broker-authoritative `CoordinatorRuntime`). It pushes the existing branch (by
  name, no `--force`) and opens a draft PR with **no new commit**. Without a
  broker the publish fails closed (`broker_required`); a prebuilt node never does
  a direct push.
- **Per-node workspace override for arbitrary paths/volumes.** Nodes may declare a
  `**Workspace:** <abs-path>` attribute, and `run-train` gains a repeatable
  `--workspace <repo>=<path>` flag. Resolution precedence: `--workspace` flag >
  `**Workspace:**` attribute > `<workspace-root>/<repo>` (the unchanged default).
- **Guardrails.** An unknown `**Mode:**` value is rejected at parse time with a
  coded, node-named `(T-G)` error (zero PRs). P4 governed merge for prebuilt nodes
  is out of scope this release: a prebuilt node under `--governed` is rejected up
  front (zero PRs) rather than emitting a misleading re-verify failure — open the
  drafts without `--governed` and merge the prebuilt PRs manually. Execute-mode
  behavior is byte-unchanged; all train invariants (INV-1..7 + merge-SHA
  false-green killer) remain green.

## [0.7.6] - 2026-07-13

### Convergence

- Enable live `publish_committed_branch`/`github` verb (broker-mediated,
  exact-head-verified, fail-closed); other verbs remain gated. Flip the single
  `publish_committed_branch`/`github` provider contract to `SUPPORTED`
  (`convergence/provider_contracts.py`) and add an opt-in
  `convergence.broker.build_github_broker_client(repo_path, *, broker_root, run=…)`
  helper that wires `LinearizableAdmissionStore` + `BrokerEvidenceStore` +
  `GitHubBrokerAdapter` + `BrokerService` for a broker-authoritative
  `CoordinatorRuntime`. Every other verb×provider (merge, release, package,
  publish, and all non-github providers) stays `HUMAN_EXECUTED` and is refused
  before start; legacy trains (no coordinator runtime) publish unchanged.

## [0.7.5] - 2026-07-13

### Convergence: crash-safe cross-repo coordinator + credential broker (verb-gated skeleton)

- **Panel-ratified convergence coordinator** (`phase_loop_runtime/convergence/**`): frozen contracts
  (event schema, result envelope, provider completion-contract matrix + terminal-outcome state machine,
  broker verb/admission, reconciliation authority-split, resource-isolation predicate, shared
  admission/fencing binding), an append-only event log + exact-state reconciliation, bounded
  codex/claude/outside-agent adapters, advisor-seat lifecycle, and transcript-free status.
- **Single credential-capable broker epoch** (`convergence/broker/**`): linearizable admission,
  terminal effect/no-effect evidence with **permanent fail-closed on `outcome_ambiguous_blocked`**
  (no timeout/override escape), canonical `(repo,branch,head_sha)` idempotency, and
  `publish_committed_branch` with **real exact-published-head verification** (`git ls-remote` +
  `gh pr list --json headRefOid`; any read-failure/mismatch fails closed, never fabricates success).
- **Adversarial fault suite** (crash/partition/stale-worker/delayed-commit/mixed-version/exact-head/
  outside-agent-adversarial) proving crash-safety + fail-closed behaviour before any enablement.
- **Live automation stays DISABLED**: every provider verb is classified `HUMAN_EXECUTED`, so the broker
  refuses all mutations. This ships the live-capable, verb-gated enforcement skeleton; flipping verbs
  to `SUPPORTED` (live GitHub mutation) is a separate, explicitly-authorized step.
- Fixes: untrack + gitignore `.dev-skills/handoffs` (tracking broke governed closeouts with
  `dirty_worktree_conflict`).

## [0.7.4] - 2026-07-13

### run-train — actionable roadmap-format diagnostics (agent-harness#60)

- **Named, coded parse diagnostics for train roadmaps.** `parse_train_roadmap`
  now surfaces the offending node/heading on malformed input instead of failing
  opaquely: a malformed `**Channel:**` descriptor is prefixed with its `node_id`;
  a duplicate `### Node:` block is caught by a new coded `(T-F)` check *before*
  the topo pass (previously mis-reported as a spurious `(T-D)` cycle with an empty
  node list); and an empty `<repo>`/`<plan-path>` component is rejected at parse,
  naming the heading. Both parse-time raises are caught at the CLI boundary with a
  clean message and zero PRs opened; the duplicate fix is bound to INV-3. The
  child-launch/dispatch half was already correct (dispatch locks are keyed per
  `(repo, roadmap)`) and is unchanged; all train invariants (INV-1..6, merge-SHA
  false-green killer) remain intact.

### Advisor board — heartbeat-aware liveness for the claude/Fable PTY leg (agent-harness#188)

- **Reviewer-progress heartbeat, not cosmetic animation.** The self-PTY Claude TUI
  leg (`_run_claude_tui_session`) now resets its stall clock only on GENUINE reviewer
  progress — novel de-animated terminal text, review-file growth, or transcript
  growth. The TUI's animated "thinking" status line (rotating verb + per-second
  elapsed counter) and a Node CLI's incidental libuv/GC CPU no longer count as
  liveness. This closes a regression where a genuinely-wedged Fable leg (blocked in
  `ep_poll`, ~2s CPU, no output) animated forever and hung ~17 min with no reclaim.
- **Typed stalled leg.** A reclaimed wedge is surfaced as `DEGRADED`
  (`claude_tui_stalled`), so the board names the liveness reclaim while the completed
  seats are preserved. No fixed model-response timeout is injected when the caller did
  not request one — the hard deadline stays the generous `_MAX` backstop and the real
  kill is heartbeat extinction.
- **Regression coverage.** New PTY-subprocess tests prove a silent-but-animating,
  CPU-trickling leg is reclaimed within the stall window; a slow-but-progressing leg
  is not killed; and no fixed short timeout is injected on the default path.

## [0.7.3] - 2026-07-12

### Roadmap supersession authority

- **Repository-common roadmap authority.** Phase-loop can pin one active roadmap
  across linked worktrees, retire superseded roadmap bytes by digest, and fail
  closed when required authority controls are missing, malformed, blocked, or
  inconsistent. A worktree-local latch preserves that refusal if linked-worktree
  Git metadata becomes unavailable.
- **Mutation fencing.** Roadmap selection, state writes, event appends,
  ratification, tier-3 audit writes, maintenance commands, and branch-governance
  overrides all enforce the same authority decision before mutation.
- **Regression coverage.** New tests cover linked-worktree selection, append and
  state refusal, retired-blob tampering, authority latching, maintenance command
  refusal, and branch-governance behavior.

### SOURCEBROKER approval identity (agent-harness#190)

- Preserve nested approval-request identity through broker client and resolver
  paths, with CLI and resolver regression coverage.

## [0.7.2] - 2026-07-12

### Advisor board — headless claude/Fable leg + native-fill affordance (agent-harness#183)

Interim fix (ahead of the larger panel/board refactor), reconciled with the
just-shipped `native_adapter_required` work (agent-harness#175 / agent-harness#125)
per an explicit owner decision: run the self-PTY TUI by default, keep
`NativeAgentLegRequest` as the native-fill affordance.

- **Headless non-Claude callers now run the claude/Fable leg.** `_exec_claude_tui_leg`
  gates on `_under_claude_code(env)` only, so a headless NON-Claude caller (e.g. Codex
  Desktop) with valid Claude Max OAuth runs the leg through the self-allocated PTY in
  `_run_claude_tui_session` instead of dropping the seat as `UNAVAILABLE`. The
  under-Claude-Code deferral (agent-harness#92), PTY-EOF fail-closed (agent-harness#48),
  and subscription-only auth-scrub are unchanged. `native_adapter_required` is now an
  affordance/fallback (surfaced via `native_agent_leg_request()`), not the default that
  silently dropped the seat.
- **`phase-loop advisor-board --json` reports a requested-vs-delivered shortfall.** New
  top-level `requested_seats` / `delivered_seats` and a `shortfall` block
  (`natively_fillable_seats`, `unfilled_seats[]`), plus a per-leg `needs_native_agent`
  request (`seat_key` / `model` / `effort` / `lens` / `artifact_ref` + review contract)
  on a deferred claude/Fable seat, so a native harness fills the seat rather than
  silently running a degraded board. Exit code stays floor-based.
- **`claude-advisor-board` / `claude-advisor-panel` skills** now steer filling a
  `needs_native_agent` seat with a native Fable Task Agent (or accepting the shortfall
  with provenance); the `codex-advisor-board` skill reframes `native_adapter_required`
  as the fallback affordance.

## [0.7.1] - 2026-07-12

POST070FIX — a parallel 8-phase backlog closeout on top of 0.7.0: phase-loop authoring-skill refinements, push-after-merge visibility, the REVIEWGOV W3/W4 review-ratification architecture (parameterized ratification policy + unattended consensus-substitutes-for-human), per-vendor review-leg sandboxing, manifest robustness, and a runner/reconcile correctness batch. This tag also contains the authenticated local task-message source broker (agent-harness#167, agent-harness#168) that landed after 0.7.0 — see the SOURCEBROKER section below.

### SKILLREF

Folded recurring, code-verified skill-reflection learnings into the phase-loop
authoring skills. All four harness sources (`claude`/`codex`/`gemini`/`opencode`)
were edited together and the neutral bundle regenerated + synced; skill-canon
parity, bundle drift, claude literal-lint, and the LaunchSpec golden all stay
green.

- **roadmap-builder "Validator Format Contract".** `phase-roadmap-builder`
  SKILL now documents the load-bearing formatting rules that
  `phase_loop_runtime.roadmap_lint` enforces by regex: the `[A-Za-z0-9]+` alias
  shape with no decoration after `(ALIAS)`, each `**Field**` label on its own
  line, bulleted lists / `- [ ]` checkboxes, the lane-count/partition hint
  (`decompose into N lanes` / `Single lane`), and the malformed-heading cascade
  (a bad heading drops the whole phase — fix the heading first, then re-run).

- **`phase_loop_runtime.skill_paths` resolver is now primary.** In
  `phase-roadmap-builder`, `plan-phase`, `plan-detailed`, and `execute-phase`,
  closeout/handoff resolution leads with the installed
  `phase_loop_runtime.skill_paths` resolver (`resolve_handoff_root`,
  `resolve_reflection_root`) and demotes the repo-local `handoff_path.py` mirror
  to a fallback used only when the runtime is not importable.

- **Skip-Explore-when-context-in-session + proportionality.** `plan-phase` and
  `plan-detailed` now tell the planner not to spawn Explore/reconnaissance
  subagents to re-gather context already in the session, and to keep
  reconnaissance proportional to the change size.

- **Multi-roadmap alias/create-mode note.** `phase-roadmap-builder` clarifies
  that each `specs/phase-plans-v*.md` is its own alias namespace and that a new
  initiative is a new roadmap (create mode), not an append onto the newest
  version.

- **Draft-PR-early protocol (re-homed here from PUSHFLOW).** `execute-phase`
  documents pushing the branch and opening a DRAFT PR on the first commit of a
  phase (visibility contract, not a merge request), respecting runner-owned
  publication in governed/autonomous mode. Homing it in SKILLREF keeps the
  execute-phase skill a single-writer surface for this run.

- **Reflection cache cleared.** These edits digest the recurring
  `~/.codex/skills/*/reflections/` learnings; that cache is cleared at phase
  closeout (after this branch merges, and only once no concurrent codex-harness
  run is mid-write) for a fresh post-0.7.0 start. This committed note is the
  durable record of that out-of-repo deletion.

### PUSHFLOW

- **Closeout pushes by DEFAULT (CLI arg layer, IF-0-PUSHFLOW-1).** The
  `phase-loop run` / `resume` / `dry-run` default closeout mode flips from
  `manual` to `push` at the CLI arg layer (`cli.py` `_resolve_run_closeout_mode`),
  so phase-owned work lands on origin instead of accumulating 70–100 commits ahead
  locally. An explicit `--closeout-mode` always wins; the new `--no-push` flag
  restores the prior `manual` default. The push runs through the existing runner
  closeout path unchanged and degrades gracefully with no push remote (recorded as
  `push_refused`, never an error). No `runner.py` edit — the runner closeout push
  path is left to its single-writer owner.

- **`commits_ahead_of_origin` ahead-of-origin signal.** The worktree index now
  reports, per worktree, how many commits its branch is ahead of the base ref
  (`git rev-list --count <base>..<branch>`), mirroring the existing `main_behind`
  divergence signal. `phase-loop worktree-index` renders `[N ahead]` (and a WARN
  hint past `AHEAD_WARN_THRESHOLD`); the opt-in `--fail-on-ahead` flag soft-blocks
  (exit non-zero) when a worktree exceeds the threshold. `phase-loop doctor` gains
  a metadata-only `worktree_divergence` aggregate (max ahead + verdict). WARN by
  default; never human_required.

- **`phase-loop doctor` pinned-clone staleness check.** A new BOM entry compares
  the pinned agent clone (`~/.local/share/agent-harness`, via `AGENT_HARNESS_HOME`)
  against the checked-in `RELEASE_PIN`; a `stale` verdict flags a clone left behind
  the pin (the live gap where clones sat at 0.6.0 under `RELEASE_PIN=v0.7.0`). The
  check is local (works offline) and never gates — WARN only. Fix: re-run
  `install-agent-harness.sh` to re-pin the clone; the documented release step
  requires bumping `RELEASE_PIN` in lockstep with the release so clones re-pin
  (see `docs/releases/outside-agent-release-handoff.md`).

### POLICY

- **Parameterized, strict-typed ratification policy — the frozen IF-0-POLICY-1 shape
  UNATTEND + GPGATE consume (REVIEWGOV W3).** New module
  `phase_loop_runtime.ratification_policy` freezes a `RatificationPolicy` dataclass
  (`required_vendors: int`, `required_lens_coverage: int`, `required_consensus`
  ∈ `{unanimous, majority}`, `on_shortfall` ∈ `{escalate, proceed_degraded}`) with a
  per-gate `DEFAULT_RATIFICATION_POLICIES` for `plan-ratify` / `design-ratify` /
  `pre-merge-CR` / `release-dispatch`, and a PURE `evaluate_ratification(policy, facts)`
  that returns a `RatificationDecision` (status + shortfalls + durable `to_audit()`
  record). The vendor quorum binds to vendors that produced a USABLE review
  (`min(distinct_seated, usable_legs)`), so a seated-but-silent board (legs
  empty/timed-out under contention) fails CLOSED — it never ratifies an N-vendor
  gate on a single usable review. Board facts are projected from the availability-aware board via
  `board_facts_from` (imports `advisor_board.composition.board_independence` for the
  distinct-vendor count; the distinct-lens count is computed in POLICY's own file, never
  by touching SANDBOX's `composition.py`). The freeze **is** that import surface — the
  canonical path is `from phase_loop_runtime.ratification_policy import RatificationPolicy,
  DEFAULT_RATIFICATION_POLICIES, BoardFacts, board_facts_from, evaluate_ratification`.

- **Autonomy-first, extended not replaced.** `on_shortfall=escalate` produces a NON-human,
  agent-recoverable `review_gate_block` (never `human_required`); `proceed_degraded`
  proceeds and writes an audit record — the dial that lets a 1-subscription operator
  ratify on a degraded board with a paper trail (the W4 `on_shortfall` consumer). The
  posture bridge `gate_posture.resolve_ratification_policy(gate, manifest=…)` lets a
  per-repo `.consiliency/manifest.json` (`ratification_policy_overrides`) partially patch
  a gate's policy; a malformed/out-of-enum override fails safe to the frozen default.
  `closeout_validators.ratification_findings(decision)` is the closeout wiring
  (escalate → one `block` finding; proceed_degraded → one `warn` finding; ratified → none).

- **`review_gate_block` now persists the ACTUAL panel finding body
  (`ViperJuice/agent-harness#80`).** A governed pre-merge block previously persisted only
  the generic `panel_block` reason ("panel leg gemini raised a blocking concern"), and the
  panel scratch dir was torn down after the leg completed — so the concrete review a
  non-human repair needs was unrecoverable. `ReviewFinding` gains an optional `body` field;
  `governed_review._findings_from_panel` now stamps the leg's actual review text onto the
  block (and non-conforming) findings, and `ReviewFinding.to_json` persists it to the
  durable state/handoff/ledger artifacts. Byte-neutral for every existing caller (the field
  defaults to `None`). Closes #80.

- **SHA-bound agent-review-gate (`ViperJuice/agent-harness#88`).** Findings and board facts
  carry the reviewed head SHA (`ReviewFinding.reviewed_sha`, `BoardFacts.reviewed_sha`);
  `governed_planning_gate(reviewed_sha=…)` threads it through, and
  `closeout_validators.verdict_binds_to(finding, head_sha)` binds a verdict to the EXACT
  reviewed commit (fail-closed: an unbound finding or an unknown head never binds). This is
  the process-separation binding scoped by the roadmap — the verdict is tied to the commit
  it reviewed, not re-trusted for a later head. Closes #88.

### SANDBOX

- **agy review legs run on a STAGED COPY, never the live tree (D3, IF-0-SANDBOX-1).**
  The product-loop `review` action pointed the gemini/`agy` leg at `--add-dir
  <repo>` — the live worktree — and `agy` honors no read-only lever (`--sandbox`
  still permits writes, no per-tool restriction), so a review leg could mutate the
  reviewed tree. `build_gemini_command` now emits the repo path behind a review-stage
  placeholder for the `review` action; `launch_with_spec` materializes a
  gitignore-aware working-tree copy at launch (tracked + untracked-non-ignored
  files, minus ignored build artifacts and `.git`, so uncommitted changes are still
  reviewed) and points `--add-dir` at the copy, cleaning it in the `finally`. A
  write by the leg can only ever hit the throwaway copy. Dry-run resolves to the
  live path with no copy materialized. No change to the non-`review` (execute /
  repair / roadmap / plan) paths.

- **IF-0-SANDBOX-1 frozen — the per-vendor read-only mechanism, per vendor.** The
  lever differs because the CLIs differ: **codex** honors `--sandbox read-only`
  (as-is); **claude** runs plan/Read-only (as-is); **grok** — whose headless `-p`
  auto-approves writes — is constrained by the `GROK_REVIEW_READONLY_TOOLS`
  read/search `--tools` allow-list (landed #149); **gemini/agy** — no honored lever
  — is constrained by the staged copy above. A regression test
  (`tests/test_review_leg_sandbox.py`) proves a review leg cannot write the reviewed
  tree on both surfaces: the launcher product-loop `review` leg (staged copy) and
  the panel/advisor-board cross-vendor CR (legs confined to a bundle-only review
  dir that never contains the repo).

- **Known deferred gap (out of scope, intentionally left as-is) — filed as
  ViperJuice/agent-harness#177:** the codex product-loop `review` leg is launched
  with `--sandbox danger-full-access` (write-capable). Codex *honors* `--sandbox
  read-only`, so this is trivially closable later by threading the `review` action
  into `build_codex_command`; it is left untouched here per phase scope (the phase
  targets the two vendors — agy + grok — where `--sandbox` is insufficient) and to
  avoid churning the codex launchspec golden.

- **Advisor-board `claude` leg exposes a machine-branchable deferral + structured
  native-agent request (#125).** The runtime never spawns a Claude TUI it cannot
  drive, so on a host with no controlling terminal the `claude` leg degrades to
  `UNAVAILABLE` (empty text — never an AGREE, recorded as a non-gating
  `panel_leg_degraded` warn). #92 blended two host cases into one reason string;
  `panel_invoker._claude_leg_deferred_reason(env)` now returns a distinct code —
  `under_claude_code` (inside a Claude Code session → the driving session runs its
  own `Task` Agent) vs `native_adapter_required` (a headless / no-tty host such as
  the Codex Desktop tool shell → the host fulfills the leg through its native
  sub-agent adapter). New additive `panel_invoker.native_agent_leg_request(...)`
  returns a `NativeAgentLegRequest` descriptor (leg, model, mode, reason, review
  brief `instructions`, and the terminal-verdict contract; `.to_dict()` for a tool
  boundary) so a Codex-hosted driver can spawn the third leg natively instead of a
  human noticing `UNAVAILABLE` and improvising. The descriptor is a pure function of
  its inputs and is NEVER threaded through the governed `(status, text)` spawn
  boundary, so `invoke_panel`'s byte-pinned governed path and the advisor-board
  golden stay byte-identical. The codex advisor-board skill documents the Codex
  Desktop native-adapter flow. Closes #125.

### MANIFEST

- **A single stale/renamed/missing manifest entry no longer invalidates the whole
  manifest (agent-harness#164).** Manifest-backed roadmap/plan discovery now
  validates the plan manifest **per-entry**: one bad entry (e.g. a plan file that
  was renamed or removed on disk) is skipped — treated orphaned — while the valid
  entries still resolve. Previously `discovery._phase_manifest_entries` gated on
  the all-or-nothing `validate_manifest(...).valid`, so a single bad entry hid the
  entire manifest and silently degraded discovery back to regex/glob (the manifest
  became invisible with no operator signal on the discovery path). A structural
  failure (unparseable JSON, wrong `schema_version`, or a non-array `plans`) still
  hides the whole manifest, since nothing in it is trustworthy. The skipped
  entry's operator signal (`manifest_plan_file_missing`) continues to fire
  independently from `reconcile._reconcile_plan_manifest`. The consumer
  materializes only the valid rows via `plan_manifest.valid_phase_entries`
  (index-aligned to `validate_manifest`), so even a *parse-hostile* sibling row
  (a non-object entry / `roadmap_ref` / lifecycle event that the all-or-nothing
  `read_manifest` load raises on) no longer re-hides the valid entries — closing
  the residual whole-manifest-degrade class flagged by the cross-vendor review.

- **IF-0-MANIFEST-1 — per-entry manifest validation result shape (frozen).**
  `plan_manifest.validate_manifest` now returns a `ValidationResult` with
  `structural_valid: bool` + `structural_errors` (the whole-manifest verdict) and
  `entries: tuple[EntryValidationResult, ...]` (a per-entry verdict aligned to the
  `plans` array by `index`), plus a `valid_indices()` helper. The legacy
  `valid`/`errors` attributes are preserved as backward-compatible aggregate
  properties (structural + all per-entry errors), so existing callers and the
  malformed-entry validation tests are unchanged. RUNCORE2 rebases on this shape.

### UNATTEND

- **W4 — unattended consensus substitutes for the human merge/tag grant, with a
  durable audit record (IF-0-UNATTEND-1).** New `phase_loop_runtime.release_guard`
  surface `evaluate_unattended_release(blocker, *, policy, facts, run_mode)` consumes
  the frozen `RatificationPolicy` / `BoardFacts` from IF-0-POLICY-1: in an `unattended`
  run an N-vendor consensus quorum stands in for the EXISTING
  `ReleaseDispatchBlocker.to_blocker()` `human_required` grant. A clean board ratifies
  and proceeds; the `policy.on_shortfall` dial handles a 1-subscription operator —
  `proceed_degraded` proceeds with a paper trail, `escalate` emits a NON-human
  `review_gate_block` (never a new `human_required` gate; W4 extends the autonomy-first
  posture, it never replaces the human option). `attended` mode (the default) returns
  `None`, leaving the existing human grant path byte-identical. The frozen record is the
  `UnattendedReleaseGrant` dataclass — `granted` / `outcome`
  (`consensus_granted | proceed_degraded | escalated`) / `reviewed_sha` (#88 SHA-binding)
  and the embedded `RatificationDecision.to_audit()` verbatim — with `to_audit()` (the
  durable trail) and `to_blocker()` (the non-human hold, or `None` when granted).

- **Release-dispatch concurrency no longer self-blocks a wrapped executor
  (`ViperJuice/agent-harness#146`).** `DispatchLock` previously refused a nested
  release-dispatch run with `concurrent_dispatch` because the outer run necessarily
  already held the per-roadmap lock. The lock now recognises its OWN run on contention
  via a caller-identity exclusion in `dispatch_lock.py`: injection-free by default (the
  lock holder being an **ancestor** of the caller marks legitimate re-entrancy), with an
  optional injected `caller_run_id` for the `setsid` case. It fails closed for a genuine
  second dispatch (a same-shell sibling is never an ancestor and still blocks) and a
  re-entrant acquire takes no second flock, so releasing it never drops the outer lock.
  The exclusion self-determines at the existing dispatch call site (no runner change) —
  a nested executor's outer run is on its parent chain (survives `subprocess`/`setsid`),
  which fully resolves the reported symptom; the *stronger explicit run-id/lease* path
  (runner-side injection) is a later refinement deferred to RUNCORE2. Closes #146.

- **Typed, metadata-only operator approval for release-dispatch launches
  (`ViperJuice/agent-harness#145`).** New `release_guard.OperatorApproval` +
  `operator_approval_from(payload)` parser: a typed record of the approved target labels
  plus provenance (timestamp, source, watch-window owner, roadmap/phase/run identity),
  with a fail-closed `covers(targets)` predicate (every mutated target must be explicitly
  approved; an empty request is not vacuously approved) and `to_metadata()` for the
  ledger/executor projection. The parser rejects any secret-bearing key or non-scalar
  value and any non-string target element (fail-closed — the record is metadata-only).
  Refs #145 — RUNCORE2 does the runner-side injection and closes it (remaining: the typed
  record visible in launch/state/event metadata + executor context, and the
  fail-closed-with-`admin_approval` emission on a missing/mismatched target).

### RUNCORE2

- **Roadmap amendments no longer make a completed phase look "genuinely unplanned"
  (agent-harness#85).** When a roadmap is amended in-flight and the edit churns a
  COMPLETED phase's own section, that phase's `phase_sha256` drifts and its stored
  completion is (correctly, by the completion-invalidation invariant) no longer
  trusted, so it reclassifies to `unplanned`. Reconcile now stamps the resulting
  provenance-mismatch warning with a repairable `gold_record_amendment` marker —
  carrying the drifted vs current `phase_sha256` and a repair hint — so `status`
  can distinguish "an amendment changed this completed phase's hashes" (repair by
  restoring the section wording or re-attesting) from a phase that was genuinely
  never planned (which gets no marker). The invalidation itself is unchanged; only
  its observability is fixed. Follow-ups (not in this change): #85's runner
  active-run closeout phase-alias preservation on in-flight amendment, and
  worktree/repo path-portability replay.

- **Standalone closeout prompt no longer drops the active plan's owned files
  (agent-harness#58).** The non-governed closeout prompt built by
  `injection.build_prompt_bundle` hardcoded an empty `plan_owned_files`, so the
  executor saw a blank "Active plan owned files" section, reported empty
  `phase_owned_dirty_paths`, and the runner refused closeout with
  `missing_phase_owned_dirty_paths` even for a plan with explicit lane ownership.
  The prompt now sources the plan's declared owned patterns via
  `parse_plan_ownership`, mirroring the governed `build_lane_prompt_bundle` path.

- **Unobserved (`--no-observe`) executor children no longer hang silently
  (agent-harness#61, agent-harness#86).** An unobserved planner/execute child fell
  to `launcher.launch`'s bare `subprocess.run` branch (`log_path=None`), which has
  no heartbeat, no quiet-child / CPU-idle stall detection, and no timeout — so an
  idle child wedged the parent inside `subprocess.run` with a stale monitor and no
  fresh artifact (the avatar-client ARTIFACTS/SCENARIO wedge). `launch` now takes an
  opt-in `ephemeral_monitor` flag (set by `launch_with_spec` whenever the child is
  unobserved) that routes the child through the SAME streaming + quiet-child
  detector used for observed runs, against a throwaway log dir that is discarded
  afterward (nothing persisted, honoring `--no-observe`). `result.stalled` /
  `result.timed_out` now fire, so the runner's existing `_launch_contract_blocker`
  emits a structured `stalled_child_observation` blocker instead of hanging. The
  wall-clock timeout stays opt-in (the "no short timeout on CLI legs" rule); the
  quiet/CPU-idle detector is what catches the wedge. Cross-repo train node children
  inherit this coverage automatically (they run through `run_loop` →
  `launch_with_spec`). Not closed here: agent-harness#90 (rehydrating a completed
  roadmap from committed closeout artifacts without a runner-owned
  `verification.json` — a reconcile rehydration-contract change that must not weaken
  the `verification.json` tamper-evidence gate) and the roadmap-format-handling half
  of agent-harness#60 — both left open.

- **Compact operator stop summary in closeout/handoff (agent-harness#119).** New
  harness-agnostic `operator_stop_summary.v1` surface (`handoff.operator_stop_summary`)
  derived from closeout/handoff state: 1-10 short plain-English bullets shaped as
  What happened / Verified / Current state / Next, suitable for direct display in a
  Codex/Claude/Gemini final response instead of relying on each model to remember to
  summarize. Token-efficient by construction — no raw logs, secret values, or
  dirty-path dumps (dirty paths are summarized as a count). Rendered as a
  top-of-file "Operator Stop Summary" section in `.phase-loop/tui-handoff.md`. The
  bridge-skill wiring that injects it into each harness's final response is homed in
  the skill sources (SKILLREF), not the runtime.

- **Runner-side operator-approval injection for release-dispatch (agent-harness#145).**
  Completes #145 (UNATTEND landed the typed `OperatorApproval` record + `dispatch_lock`
  fix; this wires it into the runner executor context). A release-dispatch plan that
  opts in with `phase_loop_requires_operator_approval: true` now has the runner
  resolve a metadata-only `.phase-loop/operator-approval.json`, freshness-scope it to
  this exact roadmap + phase (mirroring `_closeout_allow_unowned_attested`), and
  inject `OperatorApproval.to_metadata()` into the launch-metadata file the child
  reads plus the launch event/state — so SL-0 verifies the approval from runner
  context instead of `record_status=absent_from_runner_context` (even under
  `--bypass-approvals`). When a fresh valid record cannot be injected — absent,
  malformed, secret-bearing (rejected by `operator_approval_from`), or STALE (scoped
  to a different roadmap/phase) — the runner fail-closes to a sticky, human-required
  `admin_approval` blocker BEFORE launch, flowing through the existing
  `release_dispatch_blocker` emit path. Target-coverage stays with the child's SL-0
  `OperatorApproval.covers()` — the runner does not re-implement it. The gate is
  plan-declared opt-in, so existing release-dispatch plans launch unchanged.
  Approval injection requires an observed run: an approval-gated release-dispatch
  under `--no-observe` fails closed (`admin_approval`, `record_status=
  requires_observe`) rather than launching without the approval reaching the child.
  The `admin_approval` gate is sticky (like `missing_secret`); its
  `required_human_inputs` name the standard sticky-blocker recovery
  (`phase-loop reconcile --phase <P> --to-status planned --reason … --force`), not a
  bare rerun. Freshness is scoped to roadmap PATH + phase ALIAS (normalized, tolerant
  of absolute/relative/symlink forms); it is NOT content-bound — the frozen
  `OperatorApproval` carries no sha256. Deferred follow-ups (documented, not in this
  change): content-bound freshness (add sha256 to the record + compare provenance) and
  record authenticity (the file is hand-writable, weaker than a runner-emitted ledger
  attestation; a planted file with repo write access is trusted, the same threat
  surface as the rest of phase-loop's file-based ledger).

- **RUNCORE2 cross-vendor CR hardening (codex / grok / agy).** The 3-vendor review
  converged on concrete defects, all fixed here: the lane-(a) amendment marker moved
  off the misleading `blocker_class` key to a warning-only `diagnostic_class` and is
  framed as "provenance drift (amendment-shaped)" rather than a confirmed amendment
  (a hash mismatch could also be a hand-edited ledger); the lane-(c) ephemeral-monitor
  temp dir is now torn down via `try/finally` on the exception path; and the lane-(e)
  tests were strengthened to assert the injected approval reaches the child launch
  metadata + persisted event (not merely "not blocked"), plus wrong-roadmap-stale and
  secret `record_status` coverage and a `build_prompt_bundle`-level #58 wiring test.

### SOURCEBROKER — authenticated task-message source broker (agent-harness#167, agent-harness#168, agent-harness#176, agent-harness#178)

- **Add the authenticated local task-message source broker.** A loopback-only
  root-managed system service running as the unprivileged source owner wraps
  the real Codex owner socket, authenticates capability
  tokens by pinned SHA-256 before socket access, and streams exact metadata-only
  heartbeats plus one release-SHA-bound resolver result. New `--broker-url`
  probe/resolve mode consumes this channel without a total wall-clock timeout
  while heartbeats remain fresh. Tailscale Serve HTTPS is the only documented
  tailnet exposure; SSH workarounds, Funnel, second app-servers, and arbitrary
  proxy methods remain forbidden.

- **Fix task-message persistence compatibility (`ViperJuice/agent-harness#165`).**
  Resolve governed approval sources by Codex app-server's persisted
  `userMessage.clientId`, not its separately assigned item `id`, and require a
  distinct `<source-client-id>-approval` user message for the exact JSON body.
  Both messages must have one text item, unique client and stored identities,
  source-before-approval ordering, fresh turn timestamps, and matching body
  claims. A new local `--control-socket` transport performs the supported
  WebSocket-over-Unix handshake with compression disabled, so the resolver can
  run on the source host and return its proof over an independently
  authenticated channel without exposing a network listener.
  App-server-concatenated single-item envelopes remain rejected.

## [0.7.0] — 2026-07-11

CLEANSHIP — a single-writer backlog closeout of confirmed phase-loop **runner**,
**executor-governance**, **status**, **skill**, and **advisor-board** correctness
bugs plus **roadmap-discovery** hygiene, landed on top of the post-`0.6.2`
EXECDISPATCH (default-executor AUTOSEL) and adoptability platform work below.
Behavioral changes in the executor-governance AUTO gate
(`ViperJuice/agent-harness#153`) and advisor-board availability
(`ViperJuice/agent-harness#151`) motivate the minor bump.

### Added

- **The 4-vendor advisor board is now the runnable agent-facing default.** A new
  `phase-loop advisor-board <artifact>` CLI subcommand runs a real
  availability-aware review by composing seats through
  `advisor_board.composition.compose_review_board` (auth-aware: a vendor is seated
  only when it is BOTH on PATH and authenticated, REVIEWGOV IF-0-REVIEWGOV-1) and
  dispatching them via `panel_invoker.invoke_board`. The 8 harness advisor skills'
  RUNNABLE code blocks now call this board path instead of
  `invoke_panel(available_panel_legs())`. The load-bearing legacy `invoke_panel`
  (kept by the governed review/premerge gates) is **untouched** — its body and its
  byte-identical golden are unchanged.

- **Opt-in streaming verdict delivery on the shared `_run_legs_ordered`
  (IF-0-REVIEWGOV-2).** `invoke_panel` / `invoke_board` gain optional
  `on_leg_complete` (a per-leg callback) and `stream_dir` (incremental per-leg
  verdict files) parameters. When set, each leg's verdict is delivered the moment
  it lands — no head-of-line blocking on the slowest leg — while the consolidated
  return is still re-sorted to submission order. Both default to the exact
  historical behavior, so the load-bearing `invoke_panel` path and its
  byte-identical advisor-board golden are untouched. The streaming side-channel is
  fail-open (a raising callback or an unwritable `stream_dir` never breaks the pool
  or fails a leg).

- **`default_board_auth_ok`** — the reusable, fail-closed board auth probe, exported
  from `advisor_board`. It is the default gate `load_boards` applies (so the live
  `code-review` board is auth-aware by default); a caller can also inject its own
  `auth_ok` to override or, in a test, isolate the availability dimension.

### Fixed

- **Governed dry-run no longer performs closeout side effects
  (`ViperJuice/agent-harness#78`).** A `phase-loop run … --dry-run
  --closeout-mode commit` against a phase already in `awaiting_phase_closeout`
  entered `_perform_phase_closeout` — launching the governed premerge panel and
  staging the worktree — instead of remaining side-effect-free. The two
  `_perform_phase_closeout` call sites inside the dispatch closure (the
  awaiting-closeout dispatch and the repair-recovery re-closeout) now preview the
  pending closeout and break under `--dry-run`, emitting a `dry_run` terminal
  with no panel launch, no `git add`, and no commit. `dry_run` is not threaded
  into `_perform_phase_closeout` itself — the guard lives at the call site so the
  closeout body stays side-effect-free by construction.

- **`--closeout-allow-unowned` breaks through a sticky closeout scope violation on
  rerun (`ViperJuice/agent-harness#71`).** After a partial closeout committed the
  phase-owned subset and blocked human-required with
  `blocker_class=closeout_scope_violation` over a live unowned remainder, an
  operator rerun with a non-empty `--closeout-allow-unowned <reason>` recorded the
  attestation event but never recovered — the dispatch closure short-circuited at
  the human-required guard before closeout could consume the reason. The guard now
  routes a break-glassable `closeout_scope_violation` into `_perform_phase_closeout`
  with the reason (this is the "SL-1 rerun" the BREAKGLASS protocol promises); all
  other human-required blockers still short-circuit. The closeout fallback
  re-derives the unowned remainder from live git when the reconciled blocked
  snapshot carries no dirty summary, **scoped to the remainder the prior closeout
  recorded** (the paths the operator's reason attests to) intersected with what is
  still dirty — so an unrelated live edit can never be force-committed under a reason
  that named only the phase's remainder. Secrets remain non-break-glassable and keep
  the phase blocked. The closeout now **isolates the index** before staging, review,
  and commit — it resets the index to `HEAD`, stages only the accepted
  `closeout_dirty_paths`, and commits the reviewed staged index (pathspec-less);
  previously a pre-staged unrelated file — including a `.env`/secret the fallback
  deliberately excluded — was swept into the commit, silently defeating the
  secrets-never-break-glassable contract. A **secret-only** break-glass remainder now
  also keeps the sticky `closeout_scope_violation` (`human_required`) gate instead of
  downgrading to a non-human `dirty_worktree_conflict`.

- **A valid planned repair closeout clears the stale blocker instead of looping
  repair (`ViperJuice/agent-harness#59`).** When a bounded repair child reshaped
  the plan and emitted a valid closeout (`terminal_status=planned`,
  `verification_status=not_run`, `dirty_paths=[]`, no blocker, `human_required=null`)
  leaving the tree clean, the parent runner kept the stale non-human blocked state
  and relaunched the same repair path. `repair_precondition_for_snapshot` now clears
  the planned-repair-closeout case (beyond `dirty_worktree_conflict`) so the phase
  re-executes from the repaired plan — conditioned on the repair child's own
  planned/not_run/clean evidence (every field required present) and a clean tree,
  not on `blocker_class` alone. The evidence predicate is **fail-closed**: a
  truncated/partial child payload cannot clear a blocker.

- **Claude `subagent`/`agent_team` authoring actions auto-degrade to solo instead
  of an opaque TEAMGOV block (`ViperJuice/agent-harness#153`).** A claude run in
  `subagent` or `agent_team` mode whose sub-step is an authoring action
  (`plan`/`roadmap`/`maintain-skills` — the modes' `disallowed_actions`) previously
  terminated with a bare policy sentence even though team semantics are meaningless
  for a single authoring action. `build_claude_launch_spec` now AUTO-DEGRADES that
  case to solo and dispatches (solo tool policy, recorded
  `claude_execution_mode=solo`); the authoring set is read from the mode's own
  `ClaudeTeamPolicy.disallowed_actions`, never re-hardcoded. Additionally,
  `default_executor_resolver._gate_candidate` now consults claude's
  `claude_execution_policies`: on the AUTO path an authoring action under
  `subagent`/`agent_team` skips claude rather than seeding a pick the launcher would
  then block. The seed-gate and the launch-time auto-degrade are LAYERED: the gate
  removes claude from the AUTO seed; the auto-degrade is the backstop that dispatches
  claude-solo in the residual session-degraded case. A residual (non-authoring) team
  block now carries actionable remediation in the runner terminal.

- **`phase-loop status` no longer dirties `plans/manifest.json`
  (`ViperJuice/agent-harness#62`).** A `phase-loop status` (or `handoff`) that
  reconciled the plan manifest could append a synthetic auto-import row or flip a
  missing-file entry to `orphaned`, silently mutating a tracked file on a pure read
  path. `reconcile()` now takes a keyword-only `read_only` flag (default `False`,
  so every write-intent caller is byte-for-byte unchanged) threaded into
  `_reconcile_plan_manifest`, where it skips the `append_entry` and
  `update_lifecycle` writers by construction while still surfacing the same ledger
  warnings. `status_snapshot()` defaults to `read_only=True`, and the `status` and
  `handoff` CLI commands pass it explicitly — so a read invocation leaves the
  worktree byte-clean. The duplicate-ACCEPT drift is confirmed already resolved by
  the `#46` `_manifest_file_phase_key` dedup (verified load-bearing end-to-end); no
  new dedup logic was required.

- **Advisor board no longer seats an unauthenticated vendor
  (`ViperJuice/agent-harness#151`, IF-0-REVIEWGOV-1).** `compose_review_board` now
  composes on `is_available ∧ auth_ok`: a PATH-present-but-unauthenticated vendor
  (e.g. a `grok` binary on PATH with no logged-in session) is treated as **down** —
  dropped and backfilled onto an authenticated vendor with a distinct lens, exactly
  like a PATH-absent vendor. The auth gate reuses each executor's own cached,
  timeout-bounded, fail-closed `auth_ok` (`executor_availability.auth_ok_for`), so
  the board's verdict is single-sourced with the dispatch path's and never
  re-implements probing. The live convening path is auth-aware **by default**.

- **Roadmap discovery no longer auto-selects a stale/completed roadmap on a bare
  run.** `discovery.manifest_backed_roadmap` now also skips manifest entries with
  `status == "completed"` (previously only `"orphaned"`), so an all-completed
  manifest falls through to the glob branch instead of silently resuming finished
  work. Default ON, with a one-release env escape hatch
  `PHASE_LOOP_DISCOVERY_ALLOW_COMPLETED=1` that restores the pre-change behavior.
  Genuine resumption is unaffected — the state-file ladder (`active_state_roadmap`)
  precedes the manifest branch.

- **The ambiguous-glob roadmap selection is now a recoverable blocker, not a
  crash.** With multiple `specs/phase-plans-v*.md` present (agent-harness itself
  ships `v1`–`v9`) and no state/manifest/handoff to disambiguate, `select_roadmap`
  raised a bare `RuntimeError` that surfaced as an uncaught traceback. It now
  raises a typed `AmbiguousRoadmapError`, which the CLI converts to a
  `blocker_class="ambiguous_roadmap_selection"` snapshot with an actionable
  "pass `--roadmap`" summary (exit 2). This ships as a UNIT with the
  `completed`-skip above.

- **`plans/manifest.json` repaired.** The tracked manifest was frozen at `v4` and
  never recorded `v5`–`v9`, so a bare run resolved a single completed roadmap. Its
  stale entries are marked to no longer resolve a completed roadmap on a bare run.

- **Regression guard: read/write status parity on an orphaned-entry + renamed-plan
  repo (`ViperJuice/agent-harness#162`).** The `#162` follow-up (grok CR of READONLY
  `#62`) hypothesized that a read-only `phase-loop status`/`handoff` over a manifest
  entry whose plan file was renamed would diverge from a write-intent `reconcile()`.
  Verified at primary source that this does NOT reproduce on this base (the
  `manifest_plan_file_missing` branch is unreachable — a missing-file entry fails
  `validate_manifest` and `_phase_manifest_entries` hides all entries symmetrically
  in read and write mode). A parity test pins read-mode `phases` and
  `ledger_warnings` equal to the write path across `committed`/`executing`/`completed`
  statuses, guarding against a future change to the validate gate or orphan logic.

### Changed

- **Explicit `--phase` consistency on the concurrent coordinator-waves selector.**
  `_select_parallel_dispatch_phase` did not accept a `phase` argument, so it could
  only pick by wave order. It now accepts and honors an explicit phase (bounded to
  the wave structure), mirroring the serial `_select_ready_phase`. This is a
  **defensive consistency** change, not a currently reachable bug fix: through
  `run_loop`, `coordinator_waves` is populated only when no explicit `--phase` is set,
  so an explicit phase is already served by the serial selector today; the guarantee
  matters only if that invariant changes.

- **`claude-plan-detailed` is usable outside Plan Mode and defaults to
  `.consiliency/plans/` (`ViperJuice/agent-harness#87`).** Non-Plan-Mode invocation
  is now a first-class path: the skill writes the plan artifact + handoff without
  calling `ExitPlanMode` or gating on a plan-approval flow. When Plan Mode is active
  it still calls `ExitPlanMode`. Detailed plans now default to
  `.consiliency/plans/detailed-<slug>-<YYYYMMDD-HHMM>.md` (dir auto-created) instead
  of `plans/` at repo root; `--output` still overrides, and the `plans/manifest.json`
  registry location is unchanged (the entry `file` field records the new path).
  Claude-only skill source edit; gemini/opencode/codex sources untouched; regenerated
  `phase-loop-skills/` + packaged `skills_bundle/` copy, `test_skills_canon_parity.py`
  green.

- **Advisor `-panel`/`-board` skill twins collapsed to one canonical skill per
  harness plus an alias.** The canonical source is `<harness>-advisor-board`;
  `<harness>-advisor-panel` is installed as an alias of it (`SKILL_ALIASES`), so a
  historical `/<harness>-advisor-panel` invocation resolves to today's board skill
  and the two can never drift.

### Security

- **grok `execute` runs with a `--disallowed-tools` deny-list that removes privileged
  non-coding built-ins (`ViperJuice/agent-harness#154`).** The grok `execute` leg now
  subtracts the scheduler (`scheduler_create`/`scheduler_delete`/`scheduler_list`/
  `monitor`) and image/video (`image_gen`/`image_edit`/`image_to_video`/
  `reference_to_video`) built-in families while keeping grok's coding tools
  (read/search + write/edit + terminal), so a headless execute leg cannot schedule
  work or generate media outside the phase-loop's governance. Scoped to `execute`;
  `review` keeps its stricter read-only `--tools` allow-list. Live-proven against grok
  0.2.93 (an argv-verified deny-list; a behavioral tripwire test documents the
  still-open subagent gap — see Known open below). The originally-specified `--tools`
  ALLOW-LIST is unusable for a write leg (grok force-adds `run_terminal_command`, whose
  default config aborts the session), so a deny-list is used instead.

### Known open / deferred

- **`ViperJuice/agent-harness#154` (residual) — grok `spawn_subagent` cannot be
  disabled from the CLI.** NEITHER `--disallowed-tools spawn_subagent` NOR the
  dedicated `--no-subagents` flag stops a headless grok leg from spawning (both
  verified BEHAVIORALLY — a forced spawn still succeeds with a live `subagent_id`).
  Both levers are passed anyway as forward-compat, and a behavioral tripwire test
  (`test_grok_spawn_subagent_denial_tripwire`) trips the moment a future grok blocks
  it, so the gap is documented, not silently over-claimed. #154 stays open on this
  residual.

- **`ViperJuice/agent-harness#164` — advisor-board manifest fragility (open).** The
  advisor-board manifest ingestion remains fragile to malformed/partial manifests;
  tracked separately from the READONLY `#62` read-only fix and the `#162` parity
  guard above. Not addressed in this release.

- **`ViperJuice/agent-harness#84` — explicit-`--phase` serial selection
  (investigation).** The reported serial-path symptom (`--phase ROOM` repairs a
  blocked `SEAL` instead of dispatching the explicit ready phase) does **not**
  reproduce on current `main`: the serial selector already honors an explicit
  `--phase` and AUTOSEL/#152 touches zero phase-selection code (confirmed). The
  adjacent concurrent coordinator-waves selector was hardened for consistency (see
  Changed above); a regression guard pins `(ROOM, execute)` on the serial path. See
  `plans/decision-issue-84-explicit-phase-20260711.md`. #84 kept open pending a
  reproducible case.

- **REVIEWGOV W3/W4 deferred to the next roadmap (non-goals).** The review-policy
  layer — `ViperJuice/agent-harness#88` (SHA-bound review gate),
  `ViperJuice/agent-harness#145`/`#146` (release-dispatch approval + concurrency),
  and `governed-pipeline#74` (governed merge-policy consumer) — is out of scope for
  CLEANSHIP and recorded here as an explicit non-goal.

### Platform work landed since 0.6.2 (EXECDISPATCH + adoptability)

- **Authenticated cross-host task-message proof resolver (`ViperJuice/agent-harness#155`).**
  Added neutral `task-message-probe` and `task-message-resolve` commands backed by
  the Codex app-server's authenticated WebSocket `thread/read` protocol. The
  resolver accepts only an exact, pre-identified two-message envelope (source
  message plus JSON approval body), binds the body's source identity and
  SHA-256 claim to the exact source-message UTF-8 bytes,
  computes the RFC 8785 canonical approval digest, enforces freshness, and fails
  closed with a frozen typed error set. Probe/failure output is metadata-only;
  raw bytes are returned as base64 only on successful exact resolution. No
  caller-authored digest-only record or copied session JSONL can satisfy the
  interface.

- **Behavior change: phase-loop now resolves the DEFAULT executor by AUTOSEL
  (EXECDISPATCH Phase 2) instead of always falling back to `codex`.** When no
  operator/CLI/plan/roadmap hint names an executor, the default is resolved in
  four ordered layers: (1) explicit override (unchanged) → (2) the harness
  phase-loop is *run from* (env-signature detection) → (3) a single-available
  registry scan → (4) the `codex` legacy default. The two new AUTO layers
  hard-gate every candidate on `is_available ∧ auth_ok ∧ launch_complete ∧
  headless_launchable ∧ live_available` and fall through on failure, so an AUTO
  layer never picks an executor dispatch would then reject (e.g. the tty-only
  `claude` leg is never auto-picked headlessly; a proof-gated-but-live executor
  like `grok` still is). Selection provenance (which layer chose, which
  candidates were rejected and why) is logged on every auto-pick. **Escape
  hatch:** set `EXECDISPATCH_DISABLE_AUTOSEL=1` to force the pre-AUTOSEL codex
  default (explicit-override + codex-legacy only). Wiring is a seed override
  threaded into `resolve_dispatch_decision` (the historical codex auto-seed
  site); explicit hints, the work-unit rotation path, and delegation/child
  defaults are unchanged. Availability/auth probes are now bounded by a strict
  per-probe subprocess timeout AND the single-available scan carries a wall-clock
  budget and short-circuits once a second executor passes, so a wedged CLI can no
  longer stall the dispatch hot path (it degrades to the codex default); probes
  fail CLOSED on a timeout or any error. Every phase-loop-owned child executor spawn now scrubs
  Claude Code's self-markers and stamps `PHASE_LOOP_CHILD=1` so a child never
  mis-reads the host harness as its own run-from context. New record field
  `ExecutorCapabilityRecord.headless_launchable`.

- **Security: hard read-only `--tools` allow-list on the advisor-panel / CR grok
  leg.** The GROKEXEC finding (agent-harness#147) established that headless
  `grok -p` AUTO-APPROVES file writes regardless of `--permission-mode` and
  `--sandbox` — the only lever that holds grok to read-only is an explicit
  `--tools` allow-list of its read/search built-ins. The launcher's grok
  *review* path already applied this; the advisor-panel / cross-vendor-CR grok
  leg in `panel_invoker._exec_leg` did NOT, so panel/CR grok reviewer legs (which
  are read-only by contract) ran with write access. The panel grok argv now
  appends `--tools GROK_REVIEW_READONLY_TOOLS` (`read_file,grep,list_dir,search_tool`),
  the same constant `launcher.build_grok_command` uses — a single shared
  source of truth for the read-only reviewer tool set. With the write built-ins
  (`write`, `search_replace`, `run_terminal_command`) and every other tool
  (scheduler / spawn_subagent / memory / image) excluded from the allow-list, the
  panel/CR grok leg can no longer mutate the workspace. A regression guard asserts
  the panel grok argv carries the exact allow-list and that no write/privileged
  tool appears in it. No behavior change for the grok EXECUTOR (execute-path)
  argv, which stays proof-gated and out of scope.

- **Registry-driven launch dispatch (EXECDISPATCH EXECREG, IF-0-EXECREG-1).**
  `ExecutorCapabilityRecord` (`models.py`) now carries optional
  `build_command` / `is_available` / `auth_ok` / `provider_backing` /
  `get_session_transcript` fields, and `build_launch_spec` delegates to the
  record's `build_command` instead of a hardcoded `if request.executor == …`
  if-branch chain. Runnable-command construction for every executor (codex,
  claude, gemini, opencode, pi, command, manual) is now a per-record build
  function; adding an executor is a capability-record addition, not a dispatch
  edit. Behavior is spec-identical — a normalized `LaunchSpec` golden (every
  field except the two pass-through preamble objects, `prompt_bundle` /
  `injection_metadata`, which a single shared helper computes identically for all
  executors) is asserted byte-identical for every executor across codex, gemini,
  opencode, pi, command, manual and the claude agent_view / channel / print route
  paths; the golden was regenerated on the pre-refactor base and proven to match
  the refactored output byte-for-byte. Availability (PATH probe) and auth (cached,
  bounded probe gate) live
  in the new `executor_availability` module and are dormant until AUTOSEL. No
  default-executor or runtime behavior change.

- **SPIKE-DISSECT research artifacts (EXECDISPATCH Phase 3, gate IF-0-DISSECT-1).**
  Added a research-only spike under `spikes/execdispatch-dissect/` — a versioned
  tool-usage-profile schema (`schema.v0.draft.json` → frozen `schema.v1.json`), a
  metadata-only extractor (`extract_profile.py`), a stdlib-only validator with an
  active semantic redaction gate (`validate_profile.py`), claude-code + codex
  datasets, and a per-harness feasibility verdict. No production `src/` change; the
  CHANGELOG entry accompanies the committed `schema*.json` public-surface files so
  the docs-freshness audit stays green. Answers north-star B1 (profiles extractable
  for ≥2 harnesses: claude-code + codex).

- **EXECDISPATCH roadmap + pi-native north star (docs).** Adds
  `specs/phase-plans-v8.md` (the panel-ratified EXECDISPATCH phase plan —
  EXECREG → GROKEXEC → AUTOSEL serial spine plus the SPIKE-DISSECT parallel
  root) and `specs/north-star-pi-native.md` (the vision + gated backlog the
  roadmap executes against). Docs-only; no runtime change.

- **`consiliency-harness` publish uses the `pypi` environment.** The
  trusted-publishing workflow now gates on the same `pypi` GitHub environment as the
  runtime's `publish-pypi.yml` (instead of a bespoke `pypi-consiliency-harness`
  environment), so the maintainer registers one set of PyPI trusted-publisher
  coordinates (owner=ViperJuice, repo=agent-harness,
  workflow=publish-consiliency-harness.yml, environment=pypi) for a clean tag-and-go
  first publish. Workflow-only; no runtime change.

## [0.6.2] — 2026-07-10

Front-door adoptability for the primitive (roadmap AHADOPT; freezes
IF-0-AHADOPT-1 + IF-0-AHADOPT-2).

- **`consiliency-harness` install-friendly front door (new PyPI meta-package).**
  A pure dependency shim whose sole install-requires is
  `phase-loop-runtime>=0.6.1` and which ships ZERO `[project.scripts]` — it adds no
  console script and cannot shadow the runtime's `phase-loop` / `codex-phase-loop`.
  The obvious name `agent-harness` is an unrelated third party; `pip install
  consiliency-harness` now pulls the real engine. Staged for release via a
  dedicated tokenless-OIDC trusted-publishing workflow
  (`publish-consiliency-harness.yml`, `consiliency-harness-v*` tag namespace) —
  the maintainer cuts the tag; no agent credential.
- **`phase-loop doctor` (new top-level command).** A strict superset of
  `repo-validate doctor` that reports which tools/CLIs are installed + authed and
  what each unlocks, across BOTH install surfaces (wheel-bundled `phase-loop run`
  vs interactive `~/.claude/skills`), plus a multi-registry **BOM** comparing
  named consumer pins to npm + PyPI registry latest (PyPI `consiliency-contract`;
  npm `@consiliency/contract`, `@consiliency/canon-core`; the install-script ref;
  the mac-skills ref) with `stale|current|unknown` verdicts. Emits the checked-in
  `phase-loop-doctor.v1` schema (+ golden fixture); metadata-only. Degrades to
  `unknown` (never fails) when a registry is unreachable. `--fail-on-stale` exits
  non-zero on a stale GATING (repo-owned) target and is WIRED as a CI drift gate.
  The doctor import graph pulls no dotfiles-domain module (DECOUPLE SL-1).
- **Install-pin auto-track.** `install-agent-harness.sh` no longer hardcodes a
  stale release ref; it resolves the ref from a new checked-in `RELEASE_PIN`
  (sibling when cloned, fetched from the default branch on curl-pipe), held ==
  the package version by the `release-consistency` gate. A test asserts the pin
  is never behind the PyPI-published version.

## [0.6.1] — 2026-07-10

- **Stall-aware leg-liveness monitor (kill on heartbeat extinction, not a blind
  wall-clock).** The codex/gemini/grok print-mode review legs no longer hard-kill at
  the input-scaled `timeout_s` (a slow-but-STREAMING frontier review was being killed
  mid-review at the 600s floor, silently degrading the panel to fewer legs). Each leg
  now runs through `panel_invoker._run_leg_with_liveness`, which terminates the whole
  process group only on HEARTBEAT EXTINCTION — no new stdout/stderr byte AND no
  process-group CPU advance for `_LEG_STALL_THRESHOLD_S` (180s). Both fds are watched
  (codex streams its transcript to STDERR; grok/agy to STDOUT), and advancing
  process-group CPU is a secondary, NON-killing reset (it can only extend a leg's
  life, never false-kill). The wall-clock deadline is retained purely as a raised,
  rarely-hit backstop, so the existing `except subprocess.TimeoutExpired → 124` path
  is preserved; the input-scaled `timeout_s` now only feeds the #114 retry-fraction
  heuristic, not the kill.
  - **An EXPLICIT per-leg timeout override is honored as the hard deadline** (frozen
    contract): `timeouts_by_leg` / `timeout_seconds_by_leg` still bound a leg exactly
    (`{"gemini": 300}` kills at 300s). Only the input-scaled DEFAULT is raised to the
    `_MAX_LEG_TIMEOUT_S` backstop — `_default_spawn` alone knows whether the override
    was explicit and threads the resolved `deadline_s`/`backstop_s` down. (An earlier
    revision nullified overrides via `max(timeout_s, 1800)`; caught in cross-vendor CR.)
  - **Reclaim regression fixed:** if a leg's LEADER exits while a descendant still holds
    the stdout/stderr pipe open, the group is now reclaimed after a short idle grace
    (`_LEG_POST_EXIT_GRACE_S`) via a `force_group` `killpg` — instead of burning the full
    backstop (the old code hit neither the clean-exit nor the stall branch). Also caught
    in CR.
- **Fix: the gemini (agy) review leg silently ran an EMPTY prompt.** The leg passed
  `agy … -p -` and fed the composed prompt on stdin (`input=prompt`), but `agy -p -`
  IGNORES stdin and runs an empty prompt — agy printed its "How can I help you
  today?" greeting (~26 bytes), which the panel classified as a non-review and
  degraded on every run. The gemini leg was effectively non-functional (the
  root cause of the observed "dying agy sessions"). The prompt is now passed inline
  as the `-p` argv value (`stdin` closed via `DEVNULL`), exactly like the grok leg;
  the prompt is the small staged-bundle pointer so argv length stays bounded.

## [0.6.0] — 2026-07-10

- **4-vendor panel reports cross-vendor independence.** `advisor_board.board_independence(board)` returns `independent`/`degraded`/`none` + `distinct_vendors` so a governed consumer (gp's `degraded_independence` gate) can distinguish a backfilled same-vendor board from a true cross-vendor one — the availability-aware fallback previously surfaced no such signal (unanimous 4-vendor CR finding).
- **4-vendor default review board + availability-aware lens-diversity fallback.**
  The `code-review` board is now the 4-vendor cross-vendor panel — one seat per
  frontier vendor (grok `grok-4.5`, claude `claude-fable-5`, codex `gpt-5.6-sol`,
  gemini `Gemini 3.1 Pro`), each at its MAX thinking (gemini's ceiling is `high`)
  with a DISTINCT review lens. Grok joins as a 4th homebrew review lane
  (`grok -p <prompt> --output-format plain --cwd -m grok-4.5 --reasoning-effort`;
  registered subscription-only; full 600/1800s slow-leg timeout bounds). The new
  `advisor_board.composition.compose_review_board` composes the board
  AVAILABILITY-AWARE: it targets 4 independent reviewers (hard floor 3) and never
  collapses to 1–2 when vendors are down — each available vendor gets one
  lens-distinct seat, then the remaining seats are backfilled onto available
  vendors with different lenses (2 up → still 4 seats; 1 up → 4 distinct-lens
  seats). It reuses the registry PATH probe
  (`DEFAULT_HARNESS_REGISTRY.is_available`) so detection is registration-driven.
  The bare `advisor-board`/`default` premerge board is unchanged (byte-frozen
  3-leg panel; the golden + backcompat proofs stay green). The model-id-source
  guard now also recognizes `grok-*` ids.
- **Fix fleet-sweep ORPHAN false-positive (path-contaminated stat grep).** `sweep_fleet_worktrees.sh`'s ENOENT-vs-EACCES discriminator grepped the raw `stat` stderr, which interpolates the path — so a gitdir path literally containing "not found"/"no such file" (or a CRLF `.git` file) misclassified an inaccessible-but-present worktree as ORPHAN, deleting recoverable work under opt-in `--prune`. Strip the path from the stderr before matching + tolerate CRLF. Found by a Grok 4.5 adversarial review that the codex+claude PCR missed.
- **Live prune-on-merge trigger + fleet-wide detection backstop.** Two hardening
  items that shorten the window merged worktrees linger and add a cross-repo
  safety net, both reusing the ironclad safety of `prune_merged_worktrees.sh`
  (skip every owning repo's primary + the current tree; base-confined,
  permission-only `sudo -n rm -rf`; MERGED+CLEAN criterion).
  - *Live trigger (P3):* the release-train coordinator (`train_runner.run_train`)
    now fires a best-effort `post_merge_hook` the moment each node's PR merges —
    the genuine merge-observe point — instead of waiting for the next
    `execute-phase` closeout sweep. The live default delegates to the guarded
    `prune_merged_worktrees.sh` with `cwd` = the node's workspace, so `git worktree
    list` there enumerates that repo's siblings and the just-merged branch's
    worktree is swept by the proven criterion. The hook never prunes the node's
    own per-repo checkout (that is not a disposable worktree), and — because merges
    are forward-only — any hook error is logged and swallowed so a prune failure
    can never fail the train. The `run-train` SKILLs are intentionally unchanged:
    they are thin bridges with no per-node merge event to hook, and the merge
    happens inside the coordinator.
  - *Fleet detection (P2):* new `phase-loop-runtime/scripts/sweep_fleet_worktrees.sh`
    periodically sweeps ALL worktrees under `${PHASE_LOOP_WORKTREES_BASE:-/mnt/workspace/worktrees}`
    across every owning repo (not just the one the current run is in). It prunes
    only MERGED+CLEAN worktrees or genuinely ORPHANED dirs (owning gitdir absent —
    not merely unreachable), resolves and excludes each candidate's owning-repo
    primary, defaults to `--dry-run` (opt-in `--prune`), and offers
    `--alert-threshold N` (exit 2 when N+ prunable dirs accumulate) for scheduled
    alerting. No cron is installed by this change; the script header, this entry,
    and the PR document a recommended daily dry-run cron/systemd-timer line for the
    fleet owner to schedule. Self-tests (`test_sweep_fleet_worktrees_script.py`,
    `TestPostMergeHook`) prove the fleet sweep never selects a primary checkout of
    any owning repo, never removes outside the approved base, alerts at threshold,
    and that the live hook fires per merged node and never fails the train.
- **Multi-repo issue/PR reference convention.** Adds a root `AGENTS.md`
  (imported by a root `CLAUDE.md`) documenting that issue/PR numbers must be
  qualified with their repo (`agent-harness#130` or
  `ViperJuice/agent-harness#130`), never a bare `#130`, since the fleet is
  multi-repo. The same convention is folded into the shipped phase-loop execute
  skills (`*-execute-phase` and `*-execute-detailed` across all four harnesses)
  at their report/handoff/PR-body points, and the neutral bundle
  (`phase-loop-skills/` + packaged `skills_bundle/`) was regenerated to match.
- **Closeout standing rule: prune lane worktree after merge (permission-aware).**
  The `claude`/`codex`/`gemini`/`opencode` `execute-phase` and `execute-detailed`
  skill closeouts now codify a standing worktree-lifecycle rule: prune a lane's git
  worktree and delete its now-dead branch as soon as its PR merges — never leave
  merged or abandoned worktrees behind. The rule is a MERGED+CLEAN *sweep* (MERGED =
  ancestor of `origin/main` OR `gh` reports the PR merged; CLEAN =
  `git status --porcelain` empty), so the current run's own unmerged worktree is
  KEPT and unmerged/dirty peer worktrees are left intact. Documents the
  permission-locked gotcha — worktrees whose `node_modules`/build output was
  installed under a different uid (CI-offload / rootless-docker) reject
  `git worktree remove --force` and `rm -rf`, requiring a `sudo rm -rf` (or same-uid)
  fallback. Adds an idempotent `execute-phase/scripts/prune_merged_worktrees.sh`
  helper that encodes the criterion and the fallback. The helper hardens the
  `sudo rm -rf` fallback with three independent guards so a linked-worktree
  invocation can never destroy the primary checkout: (a) the PRIMARY worktree
  (first `git worktree list --porcelain` record) and the current worktree are
  always excluded; (b) the `sudo` fallback is confined to paths strictly under the
  approved worktrees base (`$PHASE_LOOP_WORKTREES_BASE`, else the parent of the
  current worktree, with a trailing-slash prefix boundary); (c) it escalates to
  `sudo -n rm -rf` ONLY on a genuine permission-denied git error — a "main working
  tree"/locked/other failure skips with a warning. A script self-test proves the
  primary is never selected when the sweep runs from a linked worktree. Closes the
  gap that let sibling worktrees accumulate without bound under the shared
  workspace volume.
- **CI guard against hardcoded model IDs (`model-id-source` convention).** Adds
  `scripts/check_model_id_sources.py`, wired into CI, enforcing "reference the
  central constant, don't inline" for concrete model IDs in
  `src/phase_loop_runtime/**`. A quoted model-ID literal fails the build unless
  the file is a sanctioned *registry* file (the advisor-board matrix/presets/
  fixtures + `profiles.py`) or the line carries a `# model-id-source: <reason>`
  marker. Catches the scatter/duplication class that let a stale `"gpt-5.5"`
  fallback linger in `panel_invoker.py`; a self-test proves the guard flags a
  planted violation, not just that it passes.

- **CERT / SCHEMA tier in the shared conformance library (#153).** Adds
  `phase_loop_runtime.conformance.validate_certificate(cert) -> dict` — the rung
  above `hash-checked`. It structurally validates a *declared* parity
  certificate against the contract-distributed `certificate` schema
  (`consiliency_contract.load_schema('certificate')`, from contract 0.6.4+):
  required fields present, and the `result_state` `$ref` closure enforced
  (`overall_result_state` / `dimension_results[].result_state` must be real
  contract result-state enum members). Loaded via the SAME `consiliency_contract`
  loader the SHAPE gates use, so it is versioned with the contract and
  byte-identical between the actor-side self-check (a mock, never authoritative)
  and the fence (the real mount). It returns the SHAPE-gate verdict shape
  (`{"status", "findings", ...}`) and is additive — it does not change any
  existing shape-tier gate behavior. Contract-absent or a contract too old to
  distribute the cert schema degrades to a neutral `skipped` verdict
  (`consent: false`, mirroring the `gate_posture.available()` pattern), never a
  crash; a companion `certificate_schema_available()` predicate exposes the
  degrade signal. STRUCTURAL only — it does NOT verify the certificate digest
  byte-value, any signature, or canon/provenance; those authority / provenance /
  signing rungs stay downstream in gp.

## [0.5.0] — 2026-07-09

- **Outside-agent conformance runtime (OARELEASE).** Adds the metadata-only outside-agent
  release handoff with `phase-loop-runtime` package identity, validator version,
  `consiliency-spec` contract pin, vector manifest hash, focused release-check
  evidence, temp wheel/sdist inventory, governed-pipeline pinning instructions,
  advisory preflight usage, and maintainer-owned publish/tag/workflow-dispatch
  boundaries. The runtime can be published as the `0.5.0` validator release, but
  downstream production merge enforcement remains governed-pipeline-owned.

## [0.4.0] — 2026-07-09

- **PSCAT-PL — `protected_source_category` sourced from the contract SoT (#155).**
  phase-loop no longer hardcodes the accepted protected-source vocabulary. The
  coarse category enum is now read from the distributed contract registry
  `protected_source_categories` (`consiliency-contract` pin moves
  `>=0.6.3,<0.7` → `>=0.6.5,<0.7`) via the same `consiliency_contract.load_registry`
  loader the git-discipline / consiliency gates use — so the runtime stays in
  lockstep with the SoT. Registry-present is authoritative (the seven coarse
  buckets: the six pre-existing + the new `governance_contracts`); when an older
  contract lacks the registry the runtime degrades to the legacy six-tuple
  (`protected_source_category_registry_available()` False) rather than hard-crashing
  at import, mirroring the `gate_posture.available()` pattern. `PipelineProtectedSource`
  now accepts an optional free-form producer `subtype` (never enum-gated — the
  contract deliberately keeps the fine vocabulary un-coupled across repos; the
  registry's `fine_subtypes` is a soft reference set only). `malformed_source_bundle`
  / `malformed_closeout` no longer fire for a category that is valid per the
  contract coarse enum, including `governance_contracts`.
- **Advisor panel `context_refs` maturity (#114–#118).** The `context_refs`
  path now keeps referenced artifacts out-of-line, carries stable metadata and
  hashes, preserves bounded per-leg timeouts, retries the Gemini leg correctly,
  and has release-gate proof for focused regressions, standalone tests, skill
  parity, clean-room install, and worktree hygiene.
- **Claude Code native-agent review leg (#120, fixes #92).** The advisor-panel
  Claude leg now defers to a native Agent under Claude Code instead of spawning
  a headless TUI, preserving subscription-auth posture and avoiding the prior
  stuck/headless execution mode.
- **Shared conformance library (#121).** Adds the named public
  `phase_loop_runtime.conformance` API so runtime and companion checks share one
  conformance implementation instead of duplicating role-specific helpers.

## [0.3.0] — 2026-07-07

The **spec-integration** release: the harness now consumes the published
`consiliency-contract` and enforces its shared governance, retractably.

- **`git_discipline` guardrail ACTIVATED.** The contract pin moves
  `>=0.3.0,<0.5.0` → `>=0.6.3,<0.7` (contract published to PyPI + npm at 0.6.3).
  This supplies the `pipeline_ref_classes` registry + `git_discipline_protocol`
  schema, so the git-discipline guardrail — previously a latent no-op under the
  0.3.0-resolving ceiling — is now live (warn-default; `human_required` never set).
- **HGATE — a 6th `spec_conformance` governed gate.** Warns when a declared
  spec-projection (`proj-S`/`proj-code`) sits below the conformance bar
  (`hash-checked`+, set by an advisory panel). Structural (no canon dependency),
  warn-default, no-op when no spec-projections are declared, and contract-sanctioned
  L0 stubs are exempt. Forward-infrastructure: dormant on schema-valid manifests
  until the per-archetype conformance ratchet + the manifest maturity enum expand.
- **Retractable-teeth posture dial.** Gate enforcement is now a per-finding-class
  posture (`observe` < `warn` < `enforce`) resolved from the merge-gated
  `gate_posture_registry` in the contract, with **non-retractable floors**: a repo
  raises any class freely via `.consiliency/manifest.json` `gate_posture_overrides`
  and lowers only to each class's floor. `never_delete_human_refs` is enforce-floored
  (forward-declared — no emitter yet), `write_footprint_violation`/`hash_drift` are
  warn-floored; advisories retract to `observe`. Any downward move emits the
  non-retractable `posture_retracted` note (de-fanging is fleet-visible). The
  operator's `PHASE_LOOP_CONSILIENCY_GATES=hard` remains the master block switch;
  `human_required` is never set. Backward-compatible: on a contract `< 0.6.2` every
  gate falls back to its prior behavior. The three previously-hardcoded per-gate
  postures (`version_skew` cap, git-discipline notes, HGATE loud/info) are migrated
  into the registry.

## [0.2.1] — 2026-07-06

- **Advisor Board purpose-derived default mode + advisory prompt hygiene.** A
  board's `purpose` now selects its default panel **mode** automatically, so a
  domain board — especially the legal boards (`legal-review`,
  `legal-strategy-review`, `legal-brainstorm`) — runs `advisory` **analysis**
  instead of being hard code-review-gated (which rejected a legal artifact for
  lacking an AGREE/DISAGREE verdict — correct behavior for the wrong mode).
  `invoke_board(mode=…)` now defaults to `None` and derives
  `_mode_for_purpose(board.purpose)`: code-review-class purposes
  (`code-review` / `premerge-review`) → `review` (strict pre-merge gate,
  verdict required); the known domain purposes → `advisory`; an **unknown**
  purpose → `review` (back-compat safe default). A caller-passed `mode` still
  overrides, and `invoke_panel` keeps its legacy `mode="review"` default. The
  advisory leg prompt also drops the code-review-gate framing (no "authoritative",
  no "untrusted material under review", no accept/reject) while keeping the
  injection-safe instructions/material separation. `DEFAULT_BOARD.purpose` is
  `premerge-review` → derives `review`, so the review path — mode derivation AND
  prompt — is **byte-for-byte unchanged** and the golden byte-identity proof still
  holds.
- **Advisor Board ingestion modes (`artifact`, `artifact_ref` / `brief_ref`,
  `context_refs`).** `invoke_panel` / `invoke_board` / `invoke_panel_request` now
  distinguish three caller choices. `artifact` is small inline text. `artifact_ref`
  (a path or ordered list of paths) and `brief_ref` (a path) are read-file-and-stage
  conveniences: the runtime reads local files and stages their bytes into
  `review-bundle.md` / `review-instructions.md`, so a caller no longer has to inline
  a 20k+ token bundle into its own context to invoke a board. `context_refs` is the
  true by-reference path/metadata mode: the runtime stages pathnames, hashes, size,
  MIME/extension hints, and optional PDF page-count metadata instead of file bytes,
  then instructs each leg to intentionally inspect the referenced local file with its
  own tools. Pathnames and hashes can disclose sensitive metadata, and a leg may
  disclose file contents after inspection unless an output policy forbids disclosure;
  remote providers, sandboxed backings, or service-backed harnesses may not share the
  caller host's local file access. A missing `artifact_ref` / `brief_ref` path fails
  **closed** (`ValueError` naming the path, never a silent-empty review); hard
  `context_refs` also fail closed unless `context_refs_soft_warn=True` records a
  manifest warning. An oversized **inline** artifact (> 16 KB) logs a one-line
  steering warning pointing to `artifact_ref` — **warn, never refuse, never mutate**.
  `artifact: str` back-compat is untouched (no ref ⇒ identical staged bytes ⇒
  identical per-leg argv / env / timeout — the golden byte-identity proof still
  holds). Adds a best-effort, age-gated GC of crash-residual `pl-panel-*` scratch
  dirs that can never affect a run, and rewrites the `advisor-board` skill to lead
  with the three ingestion modes.

## [0.2.0] — 2026-07-06

_First public PyPI release of `phase-loop-runtime`. Headline: the complete
model-first **Advisor Board** — customizable, multi-harness review boards that run
**parallel by default** with frontier models (Fable, not the implementer, on the
review path) — plus everything merged on `main` since v0.1.13. The package installs
standalone from public PyPI (all dependencies, incl. `consiliency-contract`, are
public); the Consiliency governance layer is opt-in._

- **Advisor Board default board library — Fable review path, parallel-by-default
  panels, legal + general/solo boards (#103).** The review-class presets (`default`,
  `code-review`) now seat **Fable** (`claude-fable-5`), never the mid-tier
  implementer, and `panel_invoker.DEFAULT_LEG_MODELS["claude"]` is the single source
  of truth so the **live governed gates** (`governed_review` / `governed_premerge`)
  also review on Fable — `CLAUDE_IMPLEMENTER_MODEL` is left untouched (the implementer
  stays Sonnet; the review model is decoupled from it). `invoke_board` / `invoke_panel`
  now run their legs **CONCURRENTLY by default** through a bounded `ThreadPoolExecutor`
  (wall-clock ≈ slowest leg, not the sum); `max_concurrency` is the knob — `None`
  (default) = parallel, `1` = sequential opt-in, `N` = cap under a hard 8-worker
  ceiling — threaded through the governed gates so a governed caller can request
  sequential. Result order is preserved regardless of finish order, and the golden
  proof still holds (default board byte-identical to `invoke_panel`, now on the Fable
  panel). Adds three **legal** boards (`legal-review`, `legal-strategy-review`,
  `legal-brainstorm`) and two domain-agnostic **catch-alls** (`general` — a top-tier
  cross-vendor panel for any task; `solo` — one top-end member), for **9 built-in
  presets** total. `brainstorm` / `doc-edit` deliberately keep Sonnet, where a diverse
  / low-stakes voice is the right tool.
- **Advisor Board end-to-end verification + capabilities card (Phase 7 ABDVERIFY).**
  The back-compat GOLDEN proof (the `default` board reproduces today's `invoke_panel`
  byte-for-byte — the release keystone protecting every existing caller), an
  end-to-end integration matrix (registries × resolver × backing; the fail-closed
  matrix; presets smoke), the per-harness **capabilities card**
  (`docs/advisor-board-capabilities-card.md`, model×harness matrix + presets + how to
  add a board), and an install-time verification that the `/<harness>-advisor-panel`
  alias resolves to the renamed `advisor-board` skill (and prunes a stale drifted
  install). See `specs/phase-plans-v5.md` (Phase 7 ABDVERIFY).
- **Advisor Board observability forwarding (Phase 6 ABDOBS).** A natively-launched
  board can, opt-in, **emit** its runtime events as the frozen `AdvisorBoardEvent`
  envelope (`advisor_board/events.py`) to a `sink` — **async / best-effort**, so a
  forwarding failure can never delay or fail a leg (the emit is a separate pass over
  results, off the spawn path). The envelope maps to omniagent-plus's own
  append-only **state-ledger** via a `LedgerWriter` seam (a clean cross-language
  integration point, since the ledger is TS and the emit is Python); no ledger
  internals are reimplemented. Per-workload boundary documented + enforced: the board
  is native + optional forward, and the native host leg is **observed, never
  relaunched** through a gateway. `sink=None` (default) builds no envelope, so the
  default board stays byte-neutral. See `specs/phase-plans-v5.md` (Phase 6 ABDOBS).
- **Advisor Board Omnigent backing (Phase 5 ABDOMNI).** Adds the `omnigent`
  provider transport so opt-in breadth seats route through omniagent-plus →
  Omnigent **v0.4.0** over the frozen HTTP surface, opt-in and fail-closed — the
  sibling of ABDHOME's `homebrew` backing, never a hand-written per-harness adapter.
  Implemented **on the shared provider seam**: `agent_runtime_provider.py` gains
  `OmnigentAgentRuntimeProvider` — an `AgentRuntimeProvider` sibling of
  `HomebrewAgentRuntimeProvider` (filling the `["omnigent"]` runtime the module
  reserves), ported method-for-method from omniagent-plus `http-provider.ts` over the
  frozen v0.4.0 surface. `advisor_board/backing_omnigent.py` supplies the transport
  (`OmnigentHttpClient`, stdlib-only) and the `OmnigentBacking` adapter
  (`from_env`/`from_config` factories) whose `run_seat` drives the seam provider
  (`create_session`→`send_turn`→`get_session_info`→`read_history`→`close_session`),
  exactly as the homebrew path drives its provider. `panel_invoker.invoke_board` gains an
  additive `omnigent=None` param and a tri-state `gateway_available` (`None` probes
  the wired backing): an `omnigent` seat routes iff a backing is wired AND the live
  `GET /v1/harnesses` catalog reports its harness; with no backing it stays the
  ABDHOME no-provider skip ("not served by homebrew (ABDOMNI)"), so the default
  board + built-3 stay byte-neutral. Opt-in opencode/pi seats route through
  Omnigent as the primary lane; cursor/amp route ONLY when the live catalog exposes
  them (a dynamic per-seat gate, no code gate). Two DISTINCT fail-closed skips —
  gateway-unreachable (via `select_backing`) and harness-not-in-catalog — each with
  its own reason; the native-host-leg invariant is preserved (a host-leg `omnigent`
  seat is still a hard raise even with a backing wired). Auth reuses the frozen
  no-silent-key contract: the SAME `resolve_seat_env` scrub that governs the
  homebrew subprocess env governs the wire, so a subscription seat transmits ZERO
  vendor-key material and an api-key opt-in seat transmits EXACTLY the seat vendor's
  key; the gateway DERIVES and REPORTS the resolved auth lane
  (`session.metadata.auth_lane`), read back by the backing, so no-silent-key is
  TESTABLE on the recorded request — not asserted. HTTP rejections map through the
  omniagent-plus `failure-mapper` categories (429→rate_limit, 403 auth/billing/
  policy) to a DEGRADED seat that never blocks the board. "We did not fork the
  transport" is a checked invariant: the frozen v0.4.0 `http-surface.json` +
  `source-metadata.json` are vendored (`tests/fixtures/omnigent_contract/`, from
  omniagent-plus `744420d`) and a conformance test asserts every endpoint the
  client issues appears in that surface and the freeze target is `0.4.0`. See
  `specs/phase-plans-v5.md` (Phase 5 ABDOMNI).
- **Advisor Board registries + matrix + presets (Phase 2 ABDREG).** Populates the
  real data behind the frozen ABDFREEZE interfaces (no frozen type/stub changed —
  the stubs still raise; `panel_invoker` untouched). Adds: the six-harness
  `DefaultHarnessRegistry` (claude/codex/gemini built-3 `homebrew`;
  opencode/pi/cursor breadth `omnigent`, cursor's availability gated on the
  `cursor-agent` binary) and the `DefaultModelRegistry` (model → default
  subscription lane pinned to a built-3 leg, `runnable_by` derived from the frozen
  `schema.vendor_family` projection, effort ceiling) in `registries.py`; the
  populated `(model × harness)` compatibility + per-lane auth-availability matrix
  behind the frozen `is_valid(model, harness) -> (bool, AuthAvailability)` /
  `default_lane(model)` API, plus config-time `validate_seat` / `validate_board`
  that reject an invalid pairing (e.g. `claude:gpt-5.5`) or an over-ceiling effort
  with an actionable message (`matrix.py`); the four board presets `default`
  (== the shared `DEFAULT_BOARD` fixture, so today's 3 seats reconstruct by
  construction), `code-review`, `brainstorm`, `doc-edit` (`presets.py`); and the
  user-editable config loader for `~/.config/agent-harness/advisor-boards.toml`
  (`config.py`) — `allow_api_key_fallback` defaults false, unknown top-level/board/
  seat keys are a hard error (never a silent drop), and every board (presets
  included) is matrix-validated at load. New dep: `tomli` on Python 3.10 (stdlib
  `tomllib` on 3.11+). See `specs/phase-plans-v5.md` (Phase 2 ABDREG).
- **Advisor Board resolution + rename (Phase 3 ABDRESOLVE).** Board resolution,
  seat validation, and the `advisor-panel` → `advisor-board` skill rename, coded
  against the frozen ABDFREEZE interfaces + shared canonical fixtures.
  `advisor_board/resolver.py`: `BoardResolver` turns a board name into seats,
  honors a settable default board, and parses ad-hoc `--seats model:effort[:harness]`;
  `advisor-panel` resolves as a board-name alias of the default board.
  `advisor_board/validation.py`: `validate_seat`/`validate_board` fail fast against
  the frozen `CompatibilityMatrix.is_valid` with actionable "did-you-mean"
  diagnostics. `advisor_board/standin.py`: fixture-backed stand-in registries/matrix
  (until ABDREG's real data). The `advisor-panel` skill is renamed `advisor-board`
  across all harness prefixes (`skills-src/`, `phase-loop-skills/`, packaged
  `skills_bundle/`, `REQUIRED_SKILLS`, `CANONICAL_WORKFLOW_SKILLS`, the SKILLPACK
  matrix); `advisor-panel` stays a working alias (`SKILL_ALIASES` +
  `canonical_skill_name`), and the stray prefixed `<harness>-advisor-panel` install
  is no longer produced. Result identity is re-keyed leg→seat: `PanelLegResult`
  gains an additive `seat_key` (defaults to `leg`, so the default board is
  byte-equivalent) so a board with two same-vendor seats is expressible, and the
  skill-documented `PanelRequest` is reconciled as a real entry point via
  `invoke_panel_request` (`invoke_panel`'s signature is unchanged — the ABDFREEZE-4
  back-compat anchor). See `specs/phase-plans-v5.md`.
- **Advisor Board homebrew backing through the seam (Phase 4 ABDHOME).** Board
  seats now run through the provider seam with per-seat `homebrew` backing:
  `panel_invoker.invoke_board(board, artifact, …)` routes each seat via
  `select_backing`, renders `seat.effort` to each built-3 CLI through the frozen
  `render_seat_invocation` mapping (incl. the agy leg, where effort is baked into
  the model-name string — previously hard-coded), and launches with an ACTIVELY
  scrubbed subprocess env via `resolve_seat_env` (a subscription/default seat
  scrubs every vendor API-key var; an api-key seat injects ONLY its own vendor's
  key, and only behind the board opt-in — never silent). The built-3 (claude
  native-host / Agent-View TUI off-host, codex, gemini) are behavior-neutral
  behind the seam: the `default` board renders each leg to today's exact argv +
  env. `enforce_native_host_leg` encodes the native-host-leg invariant — the host
  leg is never routed through a gateway (a host-leg `omnigent` seat is a hard
  raise, distinct from the ordinary omnigent/breadth skip-with-warning). Breadth
  harnesses (opencode/pi/cursor) get NO hand-written homebrew adapters — they are
  Omnigent-or-skip (ABDOMNI); an unavailable lane degrades skip-with-warning
  without blocking the board. The governed reviewer≠author disjointness is rewired
  onto the ONE frozen `advisor_board.schema` vendor projection (not a copy):
  `governed_review.select_reviewer_pool` now projects each available leg through
  `vendor_of_harness` before exclusion, so a same-vendor breadth lane (e.g. an
  `opencode` reviewer over a `codex`-authored artifact) is correctly excluded for
  custom/model-first boards while the built-3 panel stays byte-neutral. Effort/env
  default to today's behavior on the legacy `invoke_panel` path (its frozen
  signature is unchanged). See `specs/phase-plans-v5.md` (ABDHOME).
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
