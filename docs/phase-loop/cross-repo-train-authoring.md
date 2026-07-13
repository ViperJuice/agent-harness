# Cross-repo release train: authoring guide

This guide covers how to author a train roadmap for `phase-loop run-train`.
For the technical protocol spec (ledger shape, merge-SHA gate, invariants) see
`_contract_docs/phase-loop/protocol.md` ("Cross-Repo Release Train").

## What a train roadmap does

A train roadmap declares a set of per-repo phase-loop plans and their
dependency edges. The coordinator (`train_runner.run_train`) reads this file,
derives a topological execution order, and drives:

1. **Preflight** — every repo's plan is validated before any PR opens.
2. **Drafts-open (P3)** — per-repo `run_loop` calls open draft PRs in topo order.
3. **Governed merge (P4, `--governed` only)** — a train-level review panel
   reviews the entire multi-repo diff, then merges sequentially. Each downstream
   re-verifies against the upstream **MERGED SHA** (not the draft SHA) before
   its own merge.

## File format

```markdown
# Release Train: <name>

## Nodes

### Node: <repo> / <plan-path>

**Depends on:** (none)
**Channel:** (none)

### Node: <downstream-repo> / <downstream-plan>

**Depends on:** <repo> / <plan-path>
**Channel:** submodule path=<path-to-submodule>
```

One `### Node:` block per repo/plan pair. Node identifiers
(`<repo> / <plan-path>`) must be unique within the train.

## Channel types

A channel tells the coordinator how to update the downstream workspace's
upstream reference before re-verify at merge time.

| Channel | Syntax | Use when |
|---------|--------|----------|
| `submodule` | `submodule path=<path>` | Downstream uses a git submodule at `<path>` pointing to the upstream repo. |
| `pin file` | `pin file=<file> key=<yaml-key>` | Downstream reads the upstream SHA from a YAML pinfile at `<file>` under key `<key>`. |
| `order-only` | `order-only` | Downstream must merge **after** the upstream (freeze/merge order) but does **not** consume its artifact. No SHA is injected or re-resolved; the edge enforces ordering only. |

Declare `**Channel:** (none)` for root nodes (no upstream dependency).
Declare `**Depends on:** (none)` for root nodes.

For a **dependency** edge you must declare a channel: `pin`/`submodule` if the
downstream consumes the upstream, or `order-only` for a pure merge-order (freeze)
dependency. A bare `**Channel:** (none)` on a dependency edge is rejected (it is
ambiguous — likely a forgotten channel).

## Expand/contract recommendation

The coordinator updates the downstream's upstream pin to the **MERGED SHA**
of the upstream before re-verifying. However, the downstream PR itself was
opened with the **draft-time pin** — the SHA of the upstream PR's head at the
time the draft was created. This means the merged downstream PR carries the
draft-time pin, not the merge-commit SHA.

**To keep sequential merges safe**, use expand/contract upstream contracts:

1. **Expand** — the upstream change is additive and backward-compatible. The
   downstream continues to work with either the old or new interface. Merge
   the upstream first (the coordinator does this automatically in topo order).
2. **Contract** — the downstream adopts the new interface. Because the upstream
   is already merged (and the downstream re-verifies against its merged SHA),
   this merge is safe.

Avoid breaking upstream changes that require the downstream and upstream to
merge atomically — the train cannot guarantee zero-gap ordering.

## Example: submodule train

```markdown
# Release Train: add-feature-x

## Nodes

### Node: platform-lib / specs/feature-x.md

**Depends on:** (none)
**Channel:** (none)

### Node: app-service / specs/feature-x-consumer.md

**Depends on:** platform-lib / specs/feature-x.md
**Channel:** submodule path=vendor/platform-lib
```

The coordinator opens the `platform-lib` PR first, then the `app-service` PR.
At merge time, `app-service` re-verifies after its `vendor/platform-lib`
submodule pointer is updated to the merged `platform-lib` SHA.

## Example: pinfile train

```markdown
# Release Train: bump-runtime

## Nodes

### Node: runtime-core / specs/runtime-v2.md

**Depends on:** (none)
**Channel:** (none)

### Node: worker-fleet / specs/runtime-bump.md

**Depends on:** runtime-core / specs/runtime-v2.md
**Channel:** pin file=.config/pins.yaml key=runtime_sha
```

## Running the train

```sh
# P3 only: open draft PRs (no merge)
phase-loop run-train --train train.md

# P3+P4: open drafts, review panel, sequential merge
phase-loop run-train --train train.md --governed

# Resume after a blocked node (re-run the same command):
phase-loop run-train --train train.md --governed
```

The coordinator is crash-resumable: nodes already merged are skipped; blocked
nodes are retried. Use the same `--train` file on every run.

## Topo order

Dependencies are declared; order is computed. The coordinator calls
`roadmap.topo_order()` — you do not need to list nodes in dependency order.
Cycles are rejected at parse time.

## Safety invariants (structural)

- **Zero PRs on preflight failure** — all repos must pass preflight before any PR opens.
- **No merge before train approval** — `--governed` gates the full diff through a review panel; rejection is non-human terminal.
- **False-green killer** — `set_upstream_ref` is called with the MERGED SHA and re-verify runs *after* before each downstream merge.
- **Train state off `.phase-loop/`** — the ledger is never written inside any repo's `.phase-loop/` directory.
- **Autonomous boundary** — without `--governed`, the coordinator stops at `drafts_open`; cross-repo merges are never auto-merged.

## Converged coordinator behavior

The phase-loop command recovers the coordinator event log before every
dispatch, resume, publish, review, merge, release, or package action and
compares it with exact Git, GitHub, provider, and registry authority. A
missing probe, unsupported/mixed version, stale attempt or fence, invalid
verification artifact digest, stale approval, or ambiguous provider result is
a fail-closed typed blocker.

Each admitted mutation is bound to an immutable attempt, epoch, fence token,
approval digest, expected-version predicate, authority scope, and idempotency
key. Provider credentials stay within the broker boundary. Parallel execution
requires different repositories, complete disjoint owned paths, frozen shared
interfaces, and a recorded isolation decision; merges and release publication
remain serial. Following a merge, refresh each affected downstream to the
exact merge SHA, rerun its bound verification, then request a fresh broker
admission for republish/review.

For the full protocol spec see
`phase-loop-runtime/src/phase_loop_runtime/_contract_docs/phase-loop/protocol.md`.
