"""``phase_loop_runtime.conformance`` -- the named, stable conformance surface.

ONE LIBRARY, TWO ROLES. This module is the first-class public re-export of the
deterministic, consent-gated ``.consiliency/`` conformance evaluator that already
runs inside agent-harness. It exists so an EXTERNAL consumer -- a CR-fence in gp
CI, a git-host pre-merge check, anywhere -- can::

    from phase_loop_runtime.conformance import scan_consiliency_gates

    verdict = scan_consiliency_gates("/path/to/repo")
    if verdict["status"] == "blocked":
        ...

and run the *identical* function the actor runs against its own working tree.
Nothing here is re-implemented: every name is re-exported unchanged from the
module it already lives in, so the actor-side self-check (the "mock") and the
authoritative CR-fence (the "real validator") are provably the same code.

DUAL MOUNT -- ACTOR RESULT IS NEVER AUTHORITATIVE.
    This library is intended to be mounted BOTH as the actor-side self-check
    (a pre-PR sanity pass the author runs locally) AND as the authoritative
    CR-fence (the real validator, run by the reviewing side / gp). The
    actor-side result is advisory only -- a courtesy so the author is not
    surprised. It is NEVER trusted as proof: **the fence always re-runs the
    check itself.** Because it is the same function, the honest actor sees the
    same verdict the fence will; a dishonest or stale actor result simply does
    not matter, since the fence recomputes from the repo.

    Consequently this library is meant to be VERSIONED WITH THE CONTRACT it
    reads (the vendored ``consiliency_contract`` registries are the SoT for
    every gate). Pin actor and fence to the same contract version and they
    evaluate byte-for-byte the same policy. (This is a note, not machinery:
    nothing here enforces a pin -- it is a deployment discipline for callers.)

SCOPE -- SHAPE / GOVERNANCE TIER ONLY.
    What this surface covers is the L0 SHAPE + GOVERNANCE tier:

    * :func:`scan_consiliency_gates` -- the deterministic, consent-gated
      evaluator that runs the six L0 gates (presence, layout_validity,
      version_skew, git_discipline, local_integrity, spec_conformance) against
      the vendored contract. Input: a repo path (+ optional ``env`` for the
      gate-mode opt-in). Output: the per-gate findings plus an overall
      ``status`` verdict. Read-only; a repo with no ``.consiliency/manifest.json``
      is a pure no-op (``status: "skipped"``, ``consent: false``).
    * :func:`evaluate_git_discipline` / :func:`self_heal_partition` /
      :func:`evaluate_governance_scope` -- the PURE cores. They take injected
      facts (git ref facts, adoption profile, subject) and no repo/runner
      coupling, so a consumer that already has the facts can drive them
      directly.

    EXPLICITLY OUT OF SCOPE (delegated downstream / to gp): the cert-schema
    tier, and authority / provenance verification. This surface asserts only
    the *shape* and *governance* of a repo's ``.consiliency/`` layout -- it does
    NOT mint, sign, or verify a conformance certificate, and it does not verify
    canon/provenance of the underlying artifacts. Those higher rungs
    (``realized-edge-observed``+ in the maturity ladder) pass through as
    accepted claims here and are verified by a canon-backed verifier
    downstream, not by this library.

STABILITY.
    The names re-exported here are the stable public surface. Prefer importing
    from ``phase_loop_runtime.conformance`` (this submodule) rather than reaching
    into ``consiliency_gates`` / ``git_discipline`` / ``consiliency_ingest``
    directly -- those are the private implementation homes and may move; this
    module is the contract.
"""
from __future__ import annotations

# Re-exports (identical functions -- NOT re-implementations). The evaluator and
# its pure cores live in three implementation modules; this module is the single
# named door onto them.
from .consiliency_gates import (
    CONSILIENCY_GATES_ENV,
    CONSILIENCY_GATES_MODES,
    DEFAULT_CONSILIENCY_GATES_MODE,
    resolve_consiliency_gates_mode,
    scan_consiliency_gates,
)
from .consiliency_ingest import evaluate_governance_scope
from .git_discipline import evaluate_git_discipline, self_heal_partition

__all__ = [
    # The evaluator (the "one library" both roles mount).
    "scan_consiliency_gates",
    # Gate-mode helpers (so a consumer can inspect / set the block-opt-in).
    "resolve_consiliency_gates_mode",
    "CONSILIENCY_GATES_ENV",
    "CONSILIENCY_GATES_MODES",
    "DEFAULT_CONSILIENCY_GATES_MODE",
    # The pure cores (injected facts, no runner coupling).
    "evaluate_git_discipline",
    "self_heal_partition",
    "evaluate_governance_scope",
]
