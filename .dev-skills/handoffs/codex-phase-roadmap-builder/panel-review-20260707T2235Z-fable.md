## claude — OK

ial

# Advisor pass — specs/phase-plans-v6.md (issue #114 ingestion + panel reliability)

## Bottom line

Proceed — but treat the three blockers below as CTXFREEZE work items, not post-freeze
cleanup. The roadmap's phase decomposition, dependency DAG, and gate structure are sound,
and (importantly) the verification strategy is real rather than aspirational: the
sentinel-absence, golden byte-identity, retry-once, and elapsed-guard tests already exist
in `tests/test_panel_context_refs_114.py` and actually assert what the roadmap claims. This
is a healthy plan over a genuinely solid implementation. The changes I'm asking for are
about what the freeze *locks in*, and they're cheap now and expensive later.

## The frame that should drive CTXFREEZE

Read the branch before advising and the picture changes: `context_refs` /
`context_refs_soft_warn` already thread through `invoke_panel`, `invoke_board`,
`PanelRequest`, and `invoke_panel_request`; `timeouts_by_leg` is wired end-to-end; the
Gemini/agy retry-once with the fast-failure elapsed guard is implemented and mirrors codex;
and there is a full acceptance test. In other words, CTXIMPL/CTXRELY are mostly *already
done*. That means Phase 1's real job is a **critical audit**, not a description of what
exists — and because the same author who wrote the code will also "freeze" the contract,
the dominant failure mode is a freeze that ratifies the current shape instead of
interrogating it. The roadmap already has the right exit criterion ("candidate branch
behavior is compared against the frozen contract and any mismatches are listed"); my advice
is to make that the *primary* deliverable of CTXFREEZE and to seed it with the specific
mismatches below. If the freeze doesn't surface these, it rubber-stamped.

## Three blockers (the freeze locks these in — decide them consciously)

**1. Sharpen the non-inlining safety claim: it protects file *bytes*, not path/metadata.**
This is the headline. `_context_ref_entry` stages the absolute `path.resolve()` and a
full-content `sha256`, and the acceptance test deliberately asserts the path *is* present.
So the true guarantee is "referenced file *contents* are absent from the bundle" — not "no
sensitive information leaks." For the motivating EZBidPro/PWA/PBS/NavBlue workflow the
absolute path is frequently the sensitive part (client name, matter name, "confidential" in
a filename), and it flows into the bundle and the prompt; the sha256 is a confirm-by-guess
fingerprint of a known document. None of this is a bug — a leg needs the path to open the
file — but the North Star and the sentinel test create an impression of stronger protection
than exists. IF-0-CTXFREEZE-2's "sentinel non-inlining guarantee" language must state
explicitly: **contents-only; path/filename and content-hash metadata are staged by design,
so callers must keep secrets out of pathnames.** Worth surfacing as a genuine tradeoff, not
just a doc note: the hash is caller-side integrity/audit, not something the leg needs to
open the file, so a strict-privacy posture could omit or truncate it. Freeze the choice
(keep the hash for integrity vs. drop it for minimal exposure) on purpose.

**2. Reconcile the per-leg timeout name before freezing its "shape."** `invoke_panel` and
`invoke_board` take `timeouts_by_leg`; `PanelRequest` carries `timeout_seconds_by_leg`; the
two are bridged only inside `invoke_panel_request`. IF-0-CTXFREEZE-3 freezes "the per-leg
timeout override shape" — freezing two names for one concept is exactly the kind of wart a
freeze exists to prevent. Either rename one to match the other, or consciously freeze both
with a one-line rationale (the request field is self-documenting; the kwarg is terser). The
roadmap correctly gates CTXDOCS behind CTXIMPL "until API names finalize," which is the
right instinct — but the decision itself belongs in CTXFREEZE, and Phase 3's
`no_spec_delta` posture assumes the name is already final, so this cannot be deferred.

**3. context_refs × remote (omnigent) backing is a silent degradation that contradicts a
stated Non-Goal.** `invoke_board` injects the path/metadata manifest into the artifact sent
to *every* seat, including gateway-routed omnigent seats that provably cannot read local
files. The Non-Goal says unsupported backings "should fail or degrade *explicitly*," but
today a remote seat silently receives a manifest of local paths it can't open and will
likely return a confused/degraded review with no signal tying it to the cause. This is the
neither-state: not scoped out, not explicitly degraded. Force the decision in CTXFREEZE —
either scope `context_refs` to local/homebrew backings and skip-with-warning on omnigent
seats (matching the existing fail-closed idiom), or downgrade the Non-Goal wording to a
"documented limitation." The Assumptions section already flags that local FS access "is not
guaranteed for every future backing," so the contract is half-aware of this; finish the
thought.

## Second tier — name them in the freeze/docs, don't dwell

- **A freeze surface already violates the principle #114 is freezing.** CONTRACTS.md's
  ABDREF section describes `artifact_ref` as "by-reference ingestion," which is precisely the
  mislabeling the #114 Cross-Cutting Principle ("names must match behavior; do not call
  read-file-and-stage 'by reference'") sets out to kill. Since CONTRACTS.md is a CTXFREEZE
  target surface, correct it there (and in CTXDOCS), not just in the new code comments.

- **Upgrade the manifest "frozen format" test from substring checks to a byte snapshot.**
  IF-0-CTXFREEZE-2 freezes "metadata fields, ordering," but the tests only `assertIn`
  individual fields — that would not catch a field reordering or a header rewording, so the
  format isn't actually pinned. Add one snapshot/golden assertion of the exact rendered
  manifest for a fixed input. This is higher-value than any additional negative-proof
  assertion.

- **Document the heterogeneous entry shape.** OK entries carry bytes/sha256/mime/extension
  (+ optional pdf_page_count); soft-warn MISSING/UNREADABLE entries carry only path+status.
  A downstream parser must not assume every entry has a hash — say so in the frozen manifest
  contract.

- **Path-normalization asymmetry.** The MISSING soft-warn branch emits the raw caller path
  while OK and UNREADABLE branches emit `path.resolve()`. Since IF-0-CTXFREEZE-2 freezes
  "path normalization," resolve consistently (or document why the missing branch can't).

## Two corrections to keep the advisory honest

- The Gemini `timeout_s + 60` vs codex `timeout_s` asymmetry is **load-bearing, not an
  accidental wart**: the +60 gives agy headroom over its own `--print-timeout` so the CLI
  self-reports "timeout waiting for response" (a soft, `_GEMINI_TRANSIENT_RE`-matching signal
  that earns the one retry) instead of a hard 124 that must not retry. The freeze should
  *document why it's asymmetric* so a future tidy-up doesn't symmetrize it and silently break
  transient recovery — not treat it as a defect to fix.

- The North Star claims contents never enter "bundle, prompt, logs, or result artifacts,"
  and the negative proof only checks the bundle. This is **safe by construction, not a hole**:
  file bytes never leave the local `data` in `_context_ref_entry`, and `_render_leg_prompt`
  references by path/sha and never inlines. Characterize the verification honestly as
  "bundle-asserted; prompt/artifacts safe by construction but unasserted." A prompt-level
  assertion is low value; spend the effort on the manifest snapshot instead.

## The dimensions the brief asked about, briefly

- **Phase decomposition / DAG:** sound. CTXFREEZE → {CTXIMPL, CTXRELY, CTXDOCS} → CTXVERIFY
  with the CTXIMPL→CTXDOCS/VERIFY edges is a clean fan-out/fan-in, and gating docs behind
  impl (for the timeout-name reason above) is exactly right. The only conceptual mismatch is
  that CTXIMPL/CTXRELY read green-field while the code already exists — the "implement or
  repair" wording covers it, but the exit criteria should be framed as audit-and-confirm.

- **Interface-freeze gates:** the five IF gates cover the right surfaces. Blockers 1–3 land
  inside IF-0-CTXFREEZE-2 and -3; ensure the freeze *decides* them rather than describing the
  current state.

- **Back-compat:** the load-bearing invariant (no context_refs ⇒ artifact byte-for-byte, and
  the default board's golden byte-identity) is real and already proven. Phase 5 must actually
  *run* `test_advisor_board_golden.py`, not merely assert it exists.

- **Docs/skills scope:** correct scope, but note the bundled-vs-source skill duplication
  (`skills_bundle/*` and `skills-src/*`) is a sync hazard — the closeout policy targets both,
  so regenerate the bundle from source rather than hand-editing, or they will drift.

- **Verification strategy:** the strongest part of the plan. The one addition that matters is
  the manifest snapshot test; everything else the roadmap enumerates already exists and is
  meaningful.

## Recommendation

Green-light CTXFREEZE, with its charter rewritten as a critical audit whose first output is
the contract-vs-branch mismatch list — seeded with blockers 1–3, which must be *decided*
(not merely noted) before the freeze closes. Everything downstream — the decomposition, the
gates, the back-compat keystone, and the verification suite — is well-constructed and can
proceed as written once those three are nailed down.
