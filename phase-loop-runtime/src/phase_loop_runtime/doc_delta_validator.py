"""Doc-delta closeout validator (rigor-v1 P2).

When a phase's diff touches a user-visible public surface but the closeout
records no doc-delta decision, raise a finding. Autonomy-first: the finding is
``block``-severity, so under the default ``PHASE_LOOP_REVIEW=warn`` it is merely
recorded (the loop continues) and only blocks when an operator opts in. The
agent self-satisfies it by updating docs or recording a ``no_doc_delta``
decision in the terminal summary — no human required.

The executor records its decision as ``doc_delta_decision: <literal>`` in the
terminal summary (one of ``models.DOC_DELTA_DECISIONS``).

issue #18 F5 — evidence-backed corroboration. A bare ``no_doc_delta`` is a
*self-attested literal*: the executor claims "the public surface is already
current, no docs change needed". On a **release/explicit-release** phase that
claim is exactly the one the docs-freshness incident proved unreliable. When a
runner-side freshness scan was threaded in (``ctx.docs_freshness``), we
corroborate the ``no_doc_delta`` against its enumerated-surface evidence: an
un-corroborated ``no_doc_delta`` on a release phase is downgraded to a recorded
**warn** finding (never a block — autonomy-first), surfacing the scan result
alongside. Ordinary phases, and any phase with no scan threaded in, are
unaffected (the literal still satisfies, as before).
"""
from __future__ import annotations

from .closeout_validators import CloseoutContext, ReviewFinding, register_closeout_validator
from .docs_freshness import docs_freshness_evidence_backed
from .models import DOC_DELTA_DECISIONS, public_surface_touched


@register_closeout_validator
def doc_delta_validator(ctx: CloseoutContext) -> list[ReviewFinding]:
    if not public_surface_touched(ctx.changed_paths):
        return []
    decision = str(ctx.terminal.get("doc_delta_decision") or "").strip()
    if decision in DOC_DELTA_DECISIONS:
        return _corroborate_no_doc_delta(ctx, decision)  # may be [] or a warn
    return [
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
    ]


def _corroborate_no_doc_delta(ctx: CloseoutContext, decision: str) -> list[ReviewFinding]:
    """F5: corroborate a self-attested ``no_doc_delta`` on a release phase.

    Default-safe by construction:

    * Only ``no_doc_delta`` is corroborated — ``docs_updated`` /
      ``docs_follow_up_filed`` already evidence a real change and pass through.
    * No scan threaded in (``ctx.docs_freshness is None``) → no corroboration is
      available → satisfied (return ``[]``). We never newly-fail because the
      runner didn't wire the scan.
    * Non-release phases are untouched: an ordinary phase legitimately records
      ``no_doc_delta`` for an internal change, and the freshness scan reports
      ``skipped`` there anyway.
    * The downgrade is **warn**, never block — the existing block path is the
      *undecided* case, which this branch never reaches.
    """
    if decision != "no_doc_delta":
        return []
    detail = ctx.docs_freshness
    if not detail:
        return []  # unwired scan — no corroboration available, satisfied
    detail = dict(detail)
    if not detail.get("is_release_phase"):
        return []  # ordinary phase — self-attested no_doc_delta is fine
    if docs_freshness_evidence_backed(detail):
        return []  # the path-keyed scan corroborates the claim — satisfied
    status = str(detail.get("status") or "skipped")
    surfaces = list(detail.get("surfaces_scanned") or ())
    return [
        ReviewFinding(
            code="doc_delta_uncorroborated",
            reason=(
                "release phase recorded `no_doc_delta` but the path-keyed "
                "docs-freshness scan does not corroborate it "
                f"(docs_freshness={status}, surfaces_scanned={len(surfaces)}); "
                "treat the no-doc-change claim as unverified until the freshness "
                "scan enumerates the public-doc surfaces and reports `passed`."
            ),
            severity="warn",
            blocker_class="review_gate_warn",
        )
    ]
