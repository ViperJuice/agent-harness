# Outside-Agent Conformance

Consiliency/spec owns outside-agent contract truth. Agent-harness consumes a
pinned metadata view of that contract: schema version, package version or git
SHA, vector manifest name, vector manifest hash, source owner, and redaction
posture.

The advisory path is not acceptance authority. It can catch cheap mistakes and
explain readiness before review, but governed-pipeline remains the real
acceptance fence and reruns validation against the same pinned contract.

Agent-harness must not copy canonical outside-agent schemas, raw vector bodies,
provider payloads, secrets, or local environment values. During the pre-release
train it may validate a Consiliency/spec checkout by immutable git SHA and
vector manifest hash. Once Consiliency/spec is published, production consumers
should pin the published `consiliency-spec` package version and the same
manifest hash.

## Shared Core

`phase_loop_runtime.conformance.outside_agent_core.validate_outside_agent_submission()`
is the shared deterministic core for OACORE consumers. It accepts local
metadata-only submission dictionaries for `work_request`,
`implementation_submission`, and `ambiguity_report`; it does not call provider
clients, read credentials, consult network services, or load environment secret
values.

The return value is an `OutsideAgentConformanceVerdict` with the pinned verdict
schema version, typed submission kind, `pass` or `blocked` status, typed
`OutsideAgentBlocker` entries, the pinned contract metadata, an input digest,
repo-relative provenance refs, metadata-only evidence refs, and
`redaction_posture="metadata_only"`.

The core fails closed on unsupported schema versions, unsupported submission
kinds, unknown fields, incomplete metadata, absolute paths, path traversal,
missing digests, digest mismatches, raw payload fields, provider response
bodies, raw logs, copied vector bodies, local environment values, and
secret-shaped fields or values. Verdict output is limited to metadata, digests,
repo-relative refs, typed failure information, contract pin metadata, and
metadata-only vector result evidence.

`outside_agent_vectors.run_outside_agent_vectors()` runs metadata-only vector
manifests through the same core and compares expected outcomes without copying
canonical Consiliency/spec vector bodies into this repository. This runner is CI
and release evidence for the pinned contract and vector manifest; it is not run
for every live governed-pipeline submission.

OACORE only produces shared conformance facts. OAMOCK can later wrap those facts
with advisory labeling. OAREAL attaches the governed-pipeline runtime surface
without changing the shared core or reusing advisory authority.

## Advisory Preflight

Producers and outside agents can run a local advisory preflight before attaching
work to a GitHub issue or PR:

```bash
phase-loop outside-agent-preflight path/to/outside-agent-submission.json --output outside-agent-advisory.json
```

The command reads one local metadata-only outside-agent submission JSON file,
runs the same shared core, and emits advisory evidence with
`authority="advisory"` and `redaction_posture="metadata_only"`. The output
contains the typed core status and blockers, the pinned contract metadata, an
input digest, repo-relative provenance refs, and metadata-only evidence refs. It
does not include `accepted_for_merge`, `merge_verdict`, or any acceptance field.

Exit code `0` means the advisory preflight found no blocker. Exit code `2`
means the submission is malformed, `3` means it contains redaction-forbidden
content, `4` means provenance or digest metadata failed, and `1` is reserved for
unexpected internal failures.

Attach the generated advisory JSON as supporting evidence only. It can help
reviewers and producers find cheap contract mistakes early, but governed-pipeline
remains the authoritative acceptance and merge boundary.

## Governed-Pipeline Validator

Governed-pipeline invokes the real validator against one local metadata-only
outside-agent submission JSON file and the submitted repo-relative refs:

```bash
phase-loop outside-agent-validate path/to/outside-agent-submission.json \
  --output outside-agent-verdict.json \
  --submitted-ref src/agent.py \
  --submitted-ref docs/evidence.md
```

The command writes the same deterministic JSON to `--output` and stdout. The
SDK entry points are
`phase_loop_runtime.conformance.build_outside_agent_validation_verdict()` and
`phase_loop_runtime.conformance.serialize_outside_agent_validation_verdict()`.
Runtime validation calls `validate_outside_agent_submission()` once for the live
submission, records submitted refs after repo-relative normalization, and sets
`vectors_executed=false`; canonical vectors stay in CI and release verification.

The real-validator JSON contains `gate_id="real_conformance_gate.v0.1"`,
`command="outside-agent-validate"`, `validator_version`,
`authority="governed_pipeline_validator"`, `verdict_schema_version`,
`contract_pin`, top-level `vector_manifest_hash`, `input_digest`,
`submitted_refs`, typed `status`, typed `blockers`, repo-relative
`evidence_refs`, `redaction_posture="metadata_only"`, `vectors_executed=false`,
and the numeric `exit_code`. It must not contain advisory-only labels,
`accepted_for_merge`, `merge_verdict`, Portal projection fields, provider
payloads, raw logs, copied schema JSON, copied vector bodies, local environment
values, secrets, or absolute local paths.

Exit code `0` means the submission conforms. Exit code `2` means malformed
input, `3` means a redaction violation, `4` means provenance or digest metadata
failed, `5` means the contract or vector pin failed, `6` means another typed
conformance blocker, and `1` is reserved for unexpected internal errors.

Downstream governed-pipeline refresh work should pin the published
`consiliency-spec` package version or immutable contract git SHA together with
`vector_manifest_hash`, then call the real validator command from its acceptance
flow. Agent-harness supplies deterministic conformance evidence, while
governed-pipeline remains the acceptance and merge authority.
