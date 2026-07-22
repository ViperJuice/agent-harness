"""Visual-avatar-evidence closeout validator (FAV, issue #91, Phase 4B).

Closes the visible-media hole: a phase whose closeout produces avatar/browser
media (an actual rendered avatar, camera/video capture surface, etc.) can
today self-report ``verification_status=passed`` with nothing proving the
rendered surface is not a black or blank frame. This validator raises a
finding for exactly those phases.

Detection contract (see ``models.avatar_visual_evidence_required`` for the
implementation both this validator and the ``reconcile`` CLI reuse): a phase
requires blocking visual evidence only when BOTH hold --

1. STRUCTURAL -- the phase owns/touches a visible-media-rendering surface
   (a browser HTML fixture, or a file whose name indicates media rendering:
   ``getUserMedia``, ``MediaStreamTrack``, ``getDisplayMedia``, a
   canvas/video/camera/session/track renderer, an avatar renderer).
2. EXPLICIT CLAIM -- the phase's plan text makes an explicit user-visible
   rendering claim as a deliverable (e.g. "visible avatar", "renders in the
   browser/meeting UI", "browser call-in", "synthetic media"/"MediaStream
   target", "getUserMedia target").

A bare keyword hit on only one axis (e.g. a phase that "tests video parsing"
with no owned media file, or "runs in a browser" with no owned media file and
no explicit render claim) produces NO finding -- legacy phases and non-media
phases stay silent.

When the contract is satisfied and the phase reports ``verification_status=
passed``, it must attach a runner-owned visual artifact: a screenshot/video
path (``visual_evidence_path``) PLUS automated pixel observations
(``visual_evidence_observed``, ``models.VisualEvidenceObservation``) strong
enough to reject a black or uniform/blank frame (``non_black_pixels > 0`` AND
``pixel_min != pixel_max``).

Autonomy-first: ``block``-severity, so under the default
``PHASE_LOOP_REVIEW=warn`` it is recorded and the loop continues; it blocks
only on opt-in. The agent self-satisfies by attaching valid visual evidence or
recording a typed opt-out reason (``visual_evidence_opt_out``) -- no human
required.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from .closeout_validators import CloseoutContext, ReviewFinding, register_closeout_validator
from .models import (
    VISUAL_EVIDENCE_OPT_OUT_REASONS,
    VisualEvidenceObservation,
    avatar_visual_evidence_required,
)


def _plan_text(plan_path: str) -> str:
    try:
        return Path(plan_path).read_text(encoding="utf-8")
    except OSError:
        return ""


def _artifact_path(terminal: Mapping) -> str | None:
    """Detect the visual-evidence artifact path in the same places the
    generic verification-evidence validator reads from: the top-level
    convenience key, or the nested ``artifact_paths`` map."""
    path = terminal.get("visual_evidence_path")
    if path:
        return str(path)
    artifact_paths = terminal.get("artifact_paths")
    if isinstance(artifact_paths, Mapping):
        nested = artifact_paths.get("visual_evidence") or artifact_paths.get("visual")
        if nested:
            return str(nested)
    return None


def _has_valid_visual_evidence(terminal: Mapping) -> bool:
    if not _artifact_path(terminal):
        return False
    observed = terminal.get("visual_evidence_observed")
    if not isinstance(observed, Mapping):
        artifact_paths = terminal.get("artifact_paths")
        if isinstance(artifact_paths, Mapping):
            observed = artifact_paths.get("visual_evidence_observed")
    observation = VisualEvidenceObservation.from_mapping(observed) if isinstance(observed, Mapping) else None
    if observation is None:
        return False
    return observation.is_valid()


@register_closeout_validator
def visual_avatar_evidence_validator(ctx: CloseoutContext) -> list[ReviewFinding]:
    reported = str(ctx.terminal.get("verification_status") or ctx.automation.get("verification_status") or "")
    if reported != "passed":
        return []
    if not avatar_visual_evidence_required(ctx.changed_paths, _plan_text(ctx.plan_path)):
        return []  # no owned avatar/browser-media surface + explicit visible-render claim
    if _has_valid_visual_evidence(ctx.terminal):
        return []  # a non-blank runner-owned visual artifact is attached
    opt_out = str(ctx.terminal.get("visual_evidence_opt_out") or "").strip()
    if opt_out in VISUAL_EVIDENCE_OPT_OUT_REASONS:
        return []  # declined with a typed reason
    return [
        ReviewFinding(
            code="visual_evidence_missing_or_blank",
            reason=(
                "phase owns an avatar/browser-media surface and claims a visible rendering "
                "deliverable, reported verification_status=passed, but attached no valid "
                "visual_evidence_path + visual_evidence_observed (non_black_pixels>0 and "
                "pixel_min!=pixel_max); attach a runner-owned screenshot/video with pixel "
                "observations or record a visual_evidence_opt_out reason "
                f"({', '.join(VISUAL_EVIDENCE_OPT_OUT_REASONS)})"
            ),
            severity="block",
            blocker_class="review_gate_block",
        )
    ]
