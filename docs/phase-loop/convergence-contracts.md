# Convergence contract freeze

FREEZE publishes typed contracts only. It does not add coordinator wiring, broker calls,
admission storage, reconciliation I/O, or mutation credentials.

## Authority and invalidation

The versioned roadmap is authority for intent; the event log is authority for active
operation state; Git commits and PR heads are authority for implementation; merged SHAs
are authority for merged state; registries and manifests are authority for released state.
Transcripts and canonical `.phase-loop` metadata are recovery evidence only.

Verification and approval invalidate on `effective_code_changed`, `roadmap_changed`,
`base_sha_changed`, `dependency_sha_changed`, or `verification_plan_digest_changed`.

## Provider terminal outcome

After `provider_call_in_flight`, an operation exits only through observed terminal effect,
proven terminal no-effect with a non-late-commit guarantee, or durable ambiguous-outcome
blocking. Timeouts and human overrides do not turn ambiguity into progress. The current
GitHub mutation paths are classified `human-executed`; no pair is automatable until its
completion evidence satisfies the frozen contract.

## Failure taxonomy and fixtures

The metadata-only fixture set preserves fail-closed cases for crash, partition,
stale-worker, delayed-commit, mixed-version, exact-head, degraded-seat, ambiguous-outcome,
forged completion evidence, malformed envelopes, capability overclaim, stale or delayed
seat writes, and action-outside-bounds. Absent review sources and unavailable baseline
measurements are recorded as unavailable rather than inferred.

## Isolation and admission

Future work may run concurrently only with known evidence, disjoint owned paths, and
frozen shared interfaces. Same-repository mutation, topological merges, release
publication, overlap, and unknown evidence serialize. RUNTIME and BROKER share the single
seven-field `AdmissionRequest` fence rather than duplicate admission shapes.
