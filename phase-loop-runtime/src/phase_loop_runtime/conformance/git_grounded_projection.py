"""Git-grounded, digest-bound observability projection (issue #152, brick 1).

The EMISSION side of a liberty-preserving observability substrate: a reconciler
that turns RAW GIT REALITY into a DIGEST-BOUND projection so that ANY producer
-- a human by hand, this harness, an arbitrary agent, the pipeline -- is
observable and governed against the same contract SoT WITHOUT being gated into
a specific tool.

Two invariants make this producer-agnostic and liberty-preserving:

1. **Producer-agnostic git reality.** The reconciler reads raw git through the
   existing pure/injectable readers -- ``git_discipline.gather_repo_ref_facts``
   (branches / dirty paths via ``for-each-ref`` / ``status``) and
   ``gather_pipeline_ref_states`` (leased / merged via ``branch --merged``) --
   and classifies it against the CONTRACT SoT ref-class registry
   (``pipeline_ref_classes``) with ``evaluate_git_discipline`` /
   ``self_heal_partition``. Nothing here cares which tool produced the change;
   it reconciles ANY change in the repo, not tool-specific artifacts.

2. **Consent, not coercion.** The reconciler honors the existing
   ``.consiliency/manifest.json`` consent gate. An opted-in repo emits a
   projection; an un-adopted repo is a clean no-op (``status: "skipped"``,
   ``reason: "no-consent"``). Same gate ``reconcile_git_discipline`` already
   rides, so the liberty model (opt-in, any tool) is enforced identically.

The output is a DIGEST-BOUND projection: a canonical-JSON body describing the
git-grounded reconciled state, plus a ``sha256`` over the EXACT bytes written to
disk. The digest is the trust anchor. It is derived byte-for-byte the way the
Consiliency Portal's projection-index verify path re-derives a ``raw-sha256``
body digest (``sha256`` over the raw ``body_path`` file bytes -- no domain
prefix, no re-canonicalization at read time), so the same body the portal reads
re-verifies at render time. A tampered body fails the bind and never renders.

PORTAL CONTRACT NOTE (honest finding, issue #152). The portal's verify
*mechanism* accepts this digest-bound git-grounded body TODAY via the
``raw-sha256`` body-digest domain -- no portal or contract change is required to
re-verify it. But the ``projections.index.v1`` *schema* has no honest home for a
git-grounded reconciled state: ``kind`` is closed to
``proj-code-sbom | proj-code-api | proj-S-certified`` (no git-grounded value)
and ``body_content_type`` is ``text/markdown | text/html`` (no
``application/json``). :func:`build_projection_index_entry` therefore emits a
portal-ingestable entry but records ``kind_is_misnomer``/``git_grounded_kind``
provenance so the misfit is surfaced, not hidden. Closing the gap honestly is an
ADDITIVE contract change (a git-grounded ``kind`` + optional
``application/json`` content type); this module does not force it.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..consiliency_layout import find_consiliency_manifest
from ..git_discipline import (
    classify_ref,
    evaluate_git_discipline,
    gather_pipeline_ref_states,
    gather_repo_ref_facts,
    load_protocol,
    load_ref_classes,
    self_heal_partition,
)
from ..git_discipline import RefState

#: Wire discriminator for the git-grounded projection body. Versioned; a bump is
#: a body-shape change, never a silent edit.
GIT_GROUNDED_PROJECTION_SCHEMA = "git-grounded-observability-projection.v1"

#: The body-digest domain the portal's projection-index verify path re-derives
#: for a non-cert body: ``sha256`` over the RAW body-file bytes (no domain
#: prefix, no re-canonicalization). We write the body bytes and hash the exact
#: bytes we wrote, so a portal re-derive over the same file binds.
RAW_SHA256_DOMAIN = "raw-sha256"

#: The closed ``projections.index.v1`` ``kind`` enum has no git-grounded value.
#: ``proj-code-sbom`` is the closest non-cert, ``raw-sha256`` slot the portal
#: runtime accepts, but the name is a MISNOMER for a git-reality projection --
#: recorded as provenance so the misfit is visible (see module docstring).
PORTAL_KIND_MISNOMER = "proj-code-sbom"

#: The git-grounded ``kind`` an ADDITIVE contract change would introduce; emitted
#: as provenance so a downstream aggregator can map it once the contract lands.
GIT_GROUNDED_KIND = "proj-git-grounded"


def _canonical_bytes(body: Mapping[str, Any]) -> bytes:
    """Canonical JSON bytes for ``body``: sorted keys, compact separators, one
    trailing newline. The digest is taken over THESE bytes and these exact bytes
    are what gets written to disk, so ``sha256(path.read_bytes())`` re-derives
    the pinned digest (no byte-drift false-green -- XG-4 lesson)."""
    return (
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    """``sha256`` over raw bytes, mirroring the portal's ``raw-sha256`` derive."""
    return hashlib.sha256(data).hexdigest()


def build_git_grounded_body(
    repo: Path,
    *,
    registry: Mapping[str, Any] | None = None,
    protocol: Mapping[str, Any] | None = None,
    ref_states: Sequence[RefState] | None = None,
    repo_label: str = "repo",
    predicate: str = "git-grounded-observability",
) -> dict[str, Any]:
    """Reconcile RAW GIT REALITY into a producer-agnostic projection BODY.

    Pure-ish: reads git through the injectable ``git_discipline`` readers and
    classifies against the contract SoT registry. The returned dict is the body
    that gets digest-bound; it carries NO absolute paths, credentials, or raw
    diffs -- only ref names, classifications, and dirty/owned path lists.
    """
    reg = load_ref_classes() if registry is None else registry
    if reg is None:
        # Contract-absent (< 0.4): no ref-class SoT to classify against.
        raise GitGroundedContractAbsent(
            "pipeline_ref_classes registry unavailable -- git-discipline contract "
            "predates 0.4; cannot reconcile git reality against a contract SoT."
        )
    proto = load_protocol() if protocol is None else protocol

    facts = gather_repo_ref_facts(repo)
    states = (
        list(ref_states)
        if ref_states is not None
        else gather_pipeline_ref_states(repo, reg)
    )
    partition = self_heal_partition(states, reg)
    findings = evaluate_git_discipline(
        current_branch=facts["current_branch"],
        dirty_paths=facts["dirty_paths"],
        local_branches=facts["local_branches"],
        registry=reg,
        protocol=proto,
    )

    current_cls = classify_ref(facts["current_branch"], reg) if facts["current_branch"] else None
    ref_classes = sorted(
        (
            {
                "name": branch,
                "owner": (cls := classify_ref(branch, reg)).owner,
                "class_id": cls.class_id,
                "lease_required": cls.lease_required,
                "deletable_by_self_heal": cls.deletable_by_self_heal,
            }
            for branch in facts["local_branches"]
        ),
        key=lambda entry: entry["name"],
    )

    # Discipline verdict: clean when no findings AND no unauthorized pipeline
    # writes surfaced. Producer-agnostic -- a human, an agent, or the pipeline
    # all reconcile identically because the verdict is a function of git reality
    # against the SoT, not of who wrote the change.
    discipline_verdict = "clean" if not findings else "findings"

    return {
        "schema": GIT_GROUNDED_PROJECTION_SCHEMA,
        "repo": repo_label,
        "predicate": predicate,
        "current_branch": facts["current_branch"],
        "current_branch_owner": current_cls.owner if current_cls else None,
        "ref_classes": ref_classes,
        "dirty_paths": sorted(facts["dirty_paths"]),
        "self_heal_partition": {
            "deletable_by_self_heal": sorted(partition["deletable_by_self_heal"]),
            "protected": sorted(partition["protected"]),
            "human_refs": sorted(partition["human_refs"]),
            "never_deleted_human_refs": sorted(partition["never_deleted_human_refs"]),
        },
        "discipline_verdict": discipline_verdict,
        "discipline_findings": sorted(
            (dict(finding) for finding in findings),
            key=lambda finding: (finding.get("code", ""), finding.get("branch", ""), finding.get("path", "")),
        ),
        "producer_agnostic": True,
    }


class GitGroundedContractAbsent(RuntimeError):
    """Raised when the git-discipline ref-class contract SoT is unavailable."""


class GitGroundedProjection:
    """A digest-bound git-grounded projection: the body, the exact bytes that
    body serializes to, and the ``raw-sha256`` digest over those bytes.

    The digest is the trust anchor. :meth:`write` persists the exact bytes and
    :meth:`verify` re-derives the digest from what is on disk, so the runtime's
    green means the portal's ``raw-sha256`` re-derive over the same file binds.
    """

    def __init__(self, body: Mapping[str, Any]) -> None:
        self.body: dict[str, Any] = dict(body)
        self.body_bytes: bytes = _canonical_bytes(self.body)
        self.body_digest: str = _sha256_hex(self.body_bytes)
        self.body_digest_domain: str = RAW_SHA256_DOMAIN

    def write(self, path: Path) -> Path:
        """Write the EXACT digested bytes to ``path`` (never a re-serialization,
        so on-disk bytes == digested bytes)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.body_bytes)
        return path

    def verify(self, path: Path) -> bool:
        """Re-derive the ``raw-sha256`` digest from the bytes ON DISK (not from
        a re-serialized dict) and bind against the pinned digest. This is the
        exact check the portal's verify path performs."""
        if not path.is_file():
            return False
        return _sha256_hex(path.read_bytes()) == self.body_digest


def build_projection_index_entry(
    projection: GitGroundedProjection,
    *,
    repo_label: str,
    body_path: str,
    manifest_path: str,
    predicate: str = "git-grounded-observability",
    gate_state: str = "pass",
) -> dict[str, Any]:
    """A ``projections.index.v1`` entry that the portal's verify path ingests.

    The entry pins ``body_digest`` = the projection's ``raw-sha256`` digest over
    the body FILE bytes, so the portal re-verifies the git-grounded body ITSELF
    at render time (one body, one digest, verified end-to-end -- the portal
    certifies git reality, not a derived doc).

    ``kind`` is forced to the closed-enum ``proj-code-sbom`` MISNOMER (the only
    non-cert, ``raw-sha256`` slot the portal runtime accepts). The misfit is made
    explicit via ``git_grounded_kind`` / ``kind_is_misnomer`` provenance rather
    than hidden -- honest finding, not a forced fit (see module docstring).
    """
    return {
        "repo": repo_label,
        "kind": PORTAL_KIND_MISNOMER,
        "predicate": predicate,
        "body_path": body_path,
        "body_content_type": "text/markdown",
        "manifest_path": manifest_path,
        "body_digest": projection.body_digest,
        "body_digest_domain": projection.body_digest_domain,
        "maturity_label": "hash-checked",
        "gate_state": gate_state,
        # Provenance surfacing the honest contract-fit finding (NOT a schema
        # field the portal reads; an additive marker so the misnomer is visible
        # and a future git-grounded kind can map cleanly).
        "git_grounded_kind": GIT_GROUNDED_KIND,
        "kind_is_misnomer": True,
    }


def reconcile_git_grounded_projection(
    repo: Path,
    *,
    body_path: Path | None = None,
    registry: Mapping[str, Any] | None = None,
    protocol: Mapping[str, Any] | None = None,
    ref_states: Sequence[RefState] | None = None,
    repo_label: str = "repo",
    predicate: str = "git-grounded-observability",
    write: bool = True,
) -> dict[str, Any]:
    """Reconciler entrypoint: raw git reality -> digest-bound projection.

    Consent-gated (the liberty model): an opted-in repo (a
    ``.consiliency/manifest.json`` present) emits; an un-adopted repo is a clean
    no-op. Producer-agnostic -- reconciles ANY change in the repo, never
    tool-specific artifacts, so a human, this harness, an arbitrary agent, or
    the pipeline are all observed against the same contract SoT.

    Returns ``{"status": "skipped"|"emitted", ...}``. On ``emitted`` the result
    carries the ``body``, ``body_digest``, ``body_digest_domain``, the written
    ``body_path`` (when ``write``), and a portal-ingestable ``index_entry``.
    Never raises for consent/contract-absent; those are typed skips.
    """
    # Consent gate: opt-in, any tool. Mirrors reconcile_git_discipline.
    try:
        if find_consiliency_manifest(Path(repo)) is None:
            return {"status": "skipped", "reason": "no-consent"}
    except Exception:
        return {"status": "skipped", "reason": "no-consent"}

    try:
        body = build_git_grounded_body(
            repo,
            registry=registry,
            protocol=protocol,
            ref_states=ref_states,
            repo_label=repo_label,
            predicate=predicate,
        )
    except GitGroundedContractAbsent:
        return {"status": "skipped", "reason": "contract-absent"}

    projection = GitGroundedProjection(body)
    target = body_path if body_path is not None else _default_body_path(repo)
    manifest_rel = "spec-render/git-grounded/manifest.json"
    body_rel = "spec-render/git-grounded/observability.md"

    index_entry = build_projection_index_entry(
        projection,
        repo_label=repo_label,
        body_path=body_rel,
        manifest_path=manifest_rel,
        predicate=predicate,
    )

    result: dict[str, Any] = {
        "status": "emitted",
        "schema": GIT_GROUNDED_PROJECTION_SCHEMA,
        "body": projection.body,
        "body_digest": projection.body_digest,
        "body_digest_domain": projection.body_digest_domain,
        "index_entry": index_entry,
    }
    if write:
        written = projection.write(target)
        result["body_path"] = str(written)
        result["verified"] = projection.verify(written)
    return result


def _default_body_path(repo: Path) -> Path:
    """Default on-repo location for the emitted body (under the canonical
    ``.phase-loop/`` observability surface, never a sibling repo)."""
    return Path(repo) / ".phase-loop" / "observability" / "git-grounded-projection.json"


__all__ = [
    "GIT_GROUNDED_PROJECTION_SCHEMA",
    "RAW_SHA256_DOMAIN",
    "PORTAL_KIND_MISNOMER",
    "GIT_GROUNDED_KIND",
    "GitGroundedContractAbsent",
    "GitGroundedProjection",
    "build_git_grounded_body",
    "build_projection_index_entry",
    "reconcile_git_grounded_projection",
]
