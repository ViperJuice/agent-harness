<!-- POST070FIX Phase UNATTEND — W4 unattended consensus (IF-0-UNATTEND-1) +
     release-dispatch approval fixes (#146 caller-identity exclusion, #145 typed
     operator approval). Assembled into CHANGELOG.md by the RELEASE phase. -->

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
