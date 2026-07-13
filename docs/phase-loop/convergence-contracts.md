# Convergence contract freeze

FREEZE published typed contracts. BROKER adds the sole credential-capable mutation epoch;
coordinator wiring remains INTEG-owned.

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

## Broker epoch

`phase_loop_runtime.convergence.broker` is the only mutation boundary. Admission evaluates
policy and fencing under an inter-process lock, persists intent before provider dispatch,
then persists only observed terminal evidence. A supported provider pair may reach an
adapter; the current GitHub matrix remains `human-executed`, so production requests fail
closed before mutation. Ambiguous outcomes permanently block the epoch across restart.

Broker environment roles retain mutation credential keys only for the broker. Coordinator
and workers receive a stripped environment. The GitHub adapter rechecks repository branch
and HEAD, pushes `refs/heads/<branch>` without force, and opens the requested draft posture
without a recommit. `publishing.py` may stage and commit locally, but delegates push/PR work
through `BrokerClient`. INTEG owns all coordinator and CLI connection work.
