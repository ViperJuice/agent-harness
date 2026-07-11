# LEGACY lane (d) — omniagent-plus `phase-plans-v1` supersession check (FAIL-LOUD)

**Phase:** CLEANSHIP LEGACY (P7), lane (d) — cross-repo soft-gate.
**Date:** 2026-07-11.
**Outcome:** `v1` is **LIVE / foundational, NOT superseded** → **no archive**. This is
the explicitly-sanctioned fail-loud branch (roadmap `phase-plans-v9.md` judgment
call 11d: "archive/orphan is conditioned on an in-repo supersession check; fail-loud
if `v1` is live"). Neither this outcome nor the deferral of any archive blocks LEGACY
or the agent-harness RELEASE (soft-gate).

## What the spec assumed

`specs/phase-plans-v9.md` assumption #9: "omniagent-plus `phase-plans-v1` is
SUPERSEDED (the v2 GP-adapter plan branch supplanted it; `0/72` acceptance). A bare
new-agent run there would dispatch abandoned work, so LEGACY lane (c/d) archives it."

## What the in-repo check found (primary source)

Verified against `omniagent-plus` `origin/main` and `origin/plan/gp-adapter-roadmap`:

1. **`v1` acceptance boxes are `0/72` checked — but that signal is misleading.** The
   roadmap's acceptance checkboxes were never ticked, yet the **plan manifest**
   (`plans/manifest.json` on `origin/main`) records multiple `v1` phases as
   `status: "completed"`: `v1-BOOTCORE`, `v1-CLI`, `v1-CONTRACT`, `v1-STATELEDGER`
   (others `committed`/`imported`). Executors track lifecycle in the manifest, not by
   ticking roadmap acceptance boxes, so `0/72` does NOT mean "abandoned."

2. **The v2 roadmap explicitly disclaims supersession.** `specs/phase-plans-v2.md` on
   `origin/plan/gp-adapter-roadmap` opens with: *"this is a **separate initiative**
   from `specs/phase-plans-v1.md`. v1 is the full-depth build of the
   `agent-runtime-provider-omnigent` provider layer inside `omniagent-plus`. v2 is the
   narrow cross-repo integration ... **v2 depends on v1's `IF-0-ADAPTERS-10` adapter
   surface already existing in source; it does not re-open v1's phases.**"* The v2
   author is authoritative about v2's own scope — v2 **builds on** v1, it does not
   supplant it.

3. **v1's adapter output is PUBLISHED and depended upon.** The v2 roadmap's
   2026-07-11 amendment records that PUBHARDEN is complete:
   `@consiliency/runtime-provider@0.2.0` (v1's ADAPTERS surface) is published to npm
   with OIDC provenance, and GPBRANCH pins it. Archiving v1 would orphan the
   foundation the shipped seam and v2 both depend on.

## Decision

- **Do NOT archive `specs/phase-plans-v1.md`** to `specs/archive/` and **do NOT orphan**
  its manifest entries. No omniagent-plus PR is opened.
- The spec's premise (v1 abandoned) is contradicted by primary source; the conditioned
  fail-loud branch fires. The exit criterion ("the in-repo check RAN; either outcome
  satisfies it") is met by this note.
- If a bare new-agent run in omniagent-plus is a concern, the fix belongs in that repo
  (its own manifest/roadmap hygiene), not an archive of a live foundational roadmap.
