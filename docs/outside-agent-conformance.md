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
