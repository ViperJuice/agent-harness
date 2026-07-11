<!-- POST070FIX Phase POLICY — parameterized ratification policy (REVIEWGOV W3,
     IF-0-POLICY-1) + review-finding persistence (#80) + SHA-bound gate (#88).
     Assembled into CHANGELOG.md by the RELEASE phase; one entry per fix. -->

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
