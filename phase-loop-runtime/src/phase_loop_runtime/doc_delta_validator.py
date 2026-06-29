"""Doc-delta closeout validator (rigor-v1 P2; release-aware in docs-freshness v4 P3).

Layer B — the *advisory, in-pipeline* doc-freshness check (the non-bypassable
control is the Layer A `docs-audit` CLI). Two findings, both **`block` severity
but `warn`-effective under the default `PHASE_LOOP_REVIEW=warn`** and never
`human_required` (the severity model in `closeout_validators` enforces this):

* ``release_doc_missing`` — a changed **release-class** surface
  (version/manifest/install-posture/workflow) whose **required** doc surface did
  NOT also change. Relevance binding (anti-rubber-stamp): a recorded decision
  token does NOT rescue a release-class change — only the actual relevant doc
  change does.
* ``doc_delta_undecided`` — a changed **general** public surface with no recorded
  ``doc_delta_decision``. A token (``no_doc_delta`` + justification) satisfies it.

Structural only (no file reads): Layer B has no repo handle, so the *content*
stale-text scan (placeholders) is Layer A's job in `docs_audit`. This catches
*absent / under-scoped* docs — the #18 class — at closeout time.
"""
from __future__ import annotations

from . import docs_surfaces
from .closeout_validators import CloseoutContext, ReviewFinding, register_closeout_validator
from .models import DOC_DELTA_DECISIONS


def _required_doc_changed(release_path: str, changed: tuple[str, ...]) -> bool:
    """True when a changed path satisfies the release surface's required-doc binding.

    A release surface with no specific requirement is not relevance-gated (True).
    """
    required = docs_surfaces.required_docs_for(release_path)
    if not required:
        return True
    return any(docs_surfaces._any(c, required) for c in changed)


@register_closeout_validator
def doc_delta_validator(ctx: CloseoutContext) -> list[ReviewFinding]:
    changed = tuple(ctx.changed_paths)
    findings: list[ReviewFinding] = []

    # Release-class relevance binding — a token does not satisfy; the relevant doc must change.
    release_unsatisfied = sorted(
        p
        for p in changed
        if docs_surfaces.is_release_surface(p) and not _required_doc_changed(p, changed)
    )
    if release_unsatisfied:
        findings.append(
            ReviewFinding(
                code="release_doc_missing",
                reason=(
                    "changed release surface(s) "
                    + ", ".join(release_unsatisfied)
                    + " without updating the required doc (CHANGELOG / package README / "
                    "release docs); a recorded decision token does not satisfy a "
                    "release-class change — update the relevant doc surface"
                ),
                severity="block",
                blocker_class="review_gate_block",
            )
        )

    # General public surface — a recorded doc_delta decision (token OK) satisfies it.
    general_changed = any(docs_surfaces.classify_surface(p) == "general" for p in changed)
    if general_changed:
        decision = str(ctx.terminal.get("doc_delta_decision") or "").strip()
        if decision not in DOC_DELTA_DECISIONS:
            findings.append(
                ReviewFinding(
                    code="doc_delta_undecided",
                    reason=(
                        "changed a public surface (CLI/schema/contract docs/README/CHANGELOG) "
                        "but recorded no doc_delta decision; update the doc surface or record "
                        "doc_delta_decision=no_doc_delta with a justification"
                    ),
                    severity="block",
                    blocker_class="review_gate_block",
                )
            )
    return findings
