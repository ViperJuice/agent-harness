# Plan — order-only cross-repo train dependencies (#47)

## Problem
`run-train` requires a physical consumption channel (`pin`/`submodule`) on every dependency edge; a
dep with `Channel: (none)` is rejected (T-C). But the Consiliency unification needs **order-only /
freeze-order** edges: B must merge *after* A, yet B does not *consume* A's artifact (no submodule / no
version pinfile). Adding an artificial pinfile just to satisfy the schema wires something the
architecture doesn't need.

## Design — an explicit `order-only` channel kind
A dependency edge may declare `**Channel:** order-only`, meaning "B depends on A for MERGE ORDER only;
no artifact is injected or re-resolved." It still participates fully in the topo sort + sequential
merge; it simply carries no channel injection or channel re-verify.

## Changes (all my #29 code; train_roadmap/train_runner untouched by other branches)
1. **`cross_repo_channel.py`**
   - `ChannelKind` += `"order-only"`; `parse_channel_line("order-only") -> ChannelDescriptor(kind="order-only", params={})` (no params).
   - `set_upstream_ref` for `order-only` → raise (defensively: an order-only edge must never be injected; the coordinator skips it). Consistent with `workspace` raising `UnsupportedChannelKind`.
2. **`train_roadmap.py`**
   - T-C already passes (kind is `"order-only"`, not `"none"`).
   - T-E: add `"order-only"` to the supported-kinds set so it is NOT rejected as unsupported.
3. **`train_runner.py`**
   - P3 injection loop (~789) and P4 merged-SHA injection loop (~1071): `if edge.channel.kind == "order-only": continue` BEFORE the SHA-resolve + `set_upstream_ref` — skip injection, but the edge still enforces topo/merge order and the downstream is still re-verified (against its channel upstreams, if any).
   - A downstream with a MIX of channel + order-only upstreams: inject only the channel upstreams; order-only contribute ordering only.

## Non-goals
No auto-detection of order-only; no change to `none` semantics (a bare `(none)` on a dep is still
rejected — order-only must be explicit intent). Change-detection on resume left as-is (conservative).

## Tests
- validate-roadmap: an `order-only` dep edge passes T-C + T-E (no error); a bare `(none)` dep still fails T-C.
- run-train: an order-only downstream enforces merge order but `set_upstream_ref` is NOT called for it;
  a mixed downstream injects only its channel upstream.
- `set_upstream_ref(order-only)` raises.
