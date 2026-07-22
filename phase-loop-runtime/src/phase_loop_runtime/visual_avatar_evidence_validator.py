"""Visual-avatar-evidence closeout validator (FAV, issue #91, Phase 4B).

Closes the visible-media hole: a phase whose closeout produces avatar/browser
media (an actual rendered avatar, camera/video capture surface, etc.) can
today self-report ``verification_status=passed`` with nothing proving the
rendered surface is not a black or blank frame. This validator raises a
finding for exactly those phases.

Trigger contract (agent-harness#272, DECIDABLE BY CONSTRUCTION): the BLOCK
decision reads ONLY the executor's DECLARED ``visual_render_declared`` bool
(plus evidence validity once declared) -- nothing else. The two structural/
NL heuristics this validator used to gate the block on directly --
``models.avatar_media_surface_touched`` (an owned browser HTML fixture, or a
file whose name indicates media rendering: ``getUserMedia``,
``MediaStreamTrack``, ``getDisplayMedia``, a canvas/video/camera/session/
track renderer, an avatar renderer) and ``models.avatar_visible_render_
claimed`` (an explicit user-visible rendering claim in the plan text, e.g.
"visible avatar", "renders in the browser/meeting UI", "browser call-in",
"synthetic media"/"MediaStream target", "getUserMedia target") -- now feed
ONLY a non-blocking advisory (``VISUAL_RENDER_UNDECLARED_SURFACE_CODE``,
warn/info, posture-independent) via the shared
``models.avatar_visual_evidence_advisory_applies`` predicate, when either
fires but the phase never declared. Neither heuristic, alone or combined, can
ever produce a block-class finding -- that closes the two known NL-parser
edges (negation clause-scoping fail-open, Non-goals-nesting FP; see #272's
design comment) below the block line, where they can no longer gate a merge.

When the contract is satisfied and the phase reports ``verification_status=
passed``, it must attach a runner-owned visual artifact: an IMAGE path
(``visual_evidence_path`` -- a screenshot/frame PNG, JPEG, GIF, BMP, or WEBP;
see ``models.derive_visual_observation``) PLUS automated pixel observations
(``visual_evidence_observed``, ``models.VisualEvidenceObservation``) strong
enough to reject a black or uniform/blank frame (``non_black_pixels`` a
meaningful fraction of the frame AND ``pixel_min != pixel_max``). Fix 4
(agent-harness#91 round-6 CR): the decoder only handles IMAGE headers/pixel
data -- a video CONTAINER (mp4/webm/...) is never decoded here. Decoding a
representative frame out of a video artifact is a tracked follow-up, not
claimed by this gate today; callers must attach an already-extracted image
frame.

Autonomy-first: ``block``-severity, so under the default
``PHASE_LOOP_REVIEW=warn`` it is recorded and the loop continues; it blocks
only on opt-in. The agent self-satisfies by attaching valid visual evidence or
recording a typed opt-out reason (``visual_evidence_opt_out``) -- no human
required.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from .closeout_validators import (
    CloseoutContext,
    ReviewFinding,
    register_closeout_validator,
    visual_evidence_decoder_absent_is_silent,
)
from .models import (
    VISUAL_EVIDENCE_OPT_OUT_REASONS,
    avatar_visual_evidence_advisory_applies,
    derive_visual_observation_or_error,
    resolve_visual_evidence_artifact,
)

#: FAV (agent-harness#272): the non-blocking advisory code emitted when the
#: heuristic (structural surface touched OR an explicit visible-render claim)
#: fires but the executor never declared ``visual_render_declared``. NEVER
#: block-severity, under any posture -- see ``visual_avatar_evidence_validator``.
VISUAL_RENDER_UNDECLARED_SURFACE_CODE = "visual_render_undeclared_surface"


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


def _visual_evidence_status(terminal: Mapping, repo_root: str | None) -> "tuple[bool, str | None]":
    """FAV round-3 (codex CR): ``(is_valid, derivation_error_code)``. The
    referenced path must EXIST and be CONTAINED inside the repo (requires a
    real ``repo_root`` -- see below), and the pass/fail decision is made
    from the DERIVED pixel stats (``models.derive_visual_observation``),
    computed from the DECODED artifact -- never from the agent-supplied
    ``visual_evidence_observed``. A self-reported observation can no longer
    override a failing derived result; belt-and-suspenders, its own
    in-range/min<=max validation in ``VisualEvidenceObservation.
    __post_init__`` still applies wherever it's parsed at all.

    ``derivation_error_code`` is populated (``"visual_evidence_undecodable"``
    or ``"visual_evidence_cannot_verify"``) only when derivation itself could
    not run at all (undecodable file, or no image decoder available in this
    environment) -- distinct from the generic "no artifact attached" case,
    so the caller can surface a more specific, non-silently-passing finding.

    ``repo_root=None`` (legacy/test callers that cannot supply a root) can
    prove neither containment NOR let us decode the artifact, so this fails
    closed rather than falling back to trusting the self-reported
    observation -- that fallback WAS the round-3 hole (a valid-header blank
    image + fabricated numbers passing because nothing ever looked at the
    actual pixels). Every LIVE path (runner + reconcile) threads a real repo
    root, so this only affects callers that genuinely cannot resolve one."""
    path = _artifact_path(terminal)
    if not path or repo_root is None:
        return False, None
    resolved = resolve_visual_evidence_artifact(repo_root, path)
    if resolved is None:
        return False, None  # nonexistent, or an out-of-repo/absolute-escape path
    derived, derivation_error = derive_visual_observation_or_error(resolved)
    if derivation_error is not None:
        return False, derivation_error
    return derived.is_valid(), None


@register_closeout_validator
def visual_avatar_evidence_validator(ctx: CloseoutContext) -> list[ReviewFinding]:
    reported = str(ctx.terminal.get("verification_status") or ctx.automation.get("verification_status") or "")
    if reported != "passed":
        return []
    declared = bool(ctx.terminal.get("visual_render_declared"))
    if not declared:
        # FAV #272: the heuristic (structural surface touched OR an explicit
        # visible-render claim) NEVER blocks -- it only raises a hard
        # non-blocking advisory when it fires without a declaration. This is
        # posture-independent: run_closeout_validators forces every finding
        # to "warn" under PHASE_LOOP_REVIEW=warn, and under "block" a
        # finding's severity passes through UNCHANGED -- so a severity that
        # is always "warn" here can never become "block" under either
        # posture (see closeout_validators.run_closeout_validators).
        if avatar_visual_evidence_advisory_applies(
            ctx.changed_paths, _plan_text(ctx.plan_path), declared
        ):
            return [
                ReviewFinding(
                    code=VISUAL_RENDER_UNDECLARED_SURFACE_CODE,
                    reason=(
                        "phase touched an avatar media surface / prose claims a visible render "
                        "but did not declare visual_render_declared=true; the visual-evidence "
                        "gate did not enforce. Declare it to enforce."
                    ),
                    severity="warn",
                )
            ]
        return []  # no owned avatar/browser-media surface, no explicit visible-render claim
    is_valid, derivation_error = _visual_evidence_status(ctx.terminal, ctx.repo_root)
    if is_valid:
        return []  # a validated, non-blank, DECODED runner-owned visual artifact is attached
    opt_out = str(ctx.terminal.get("visual_evidence_opt_out") or "").strip()
    if opt_out in VISUAL_EVIDENCE_OPT_OUT_REASONS:
        return []  # declined with a typed reason
    if derivation_error is not None:
        # Fix 4b (agent-harness#91 round-6 CR): a decoder-UNAVAILABLE
        # environment (Pillow, the optional `visual` extra, not installed) is
        # an ADOPTION-DEFAULT concern distinct from every other finding this
        # validator raises -- a standard install without the `visual` extra
        # would otherwise get a `visual_evidence_cannot_verify` finding on
        # EVERY passing visual/avatar phase closeout under the default
        # warn posture, purely because the optional decoder isn't installed
        # (not because anything is actually wrong). Stay SILENT for that one
        # specific case under warn/off; only the opt-in `block` posture turns
        # a missing decoder into a hard block. An UNDECODABLE artifact
        # (Pillow present, decode failed) is a genuine, actionable finding
        # and is unaffected -- it is recorded under warn like any other
        # finding, exactly as before. Shared with the reconcile guard
        # (round-7 CR) via `visual_evidence_decoder_absent_is_silent` so the
        # two enforcement points can never diverge.
        if visual_evidence_decoder_absent_is_silent(derivation_error):
            return []
        return [
            ReviewFinding(
                code=derivation_error,
                reason=(
                    "phase declared visual_render_declared=true and reported "
                    "verification_status=passed with a visual_evidence_path attached, but the "
                    "referenced artifact could not be "
                    "authoritatively verified ("
                    + (
                        "no image decoder (Pillow) is available in this environment"
                        if derivation_error == "visual_evidence_cannot_verify"
                        else "the artifact could not be decoded as an image"
                    )
                    + "); self-reported pixel observations are never accepted as a substitute -- "
                    "attach a genuinely decodable screenshot/frame image or record a "
                    f"visual_evidence_opt_out reason ({', '.join(VISUAL_EVIDENCE_OPT_OUT_REASONS)})"
                ),
                severity="block",
                blocker_class="review_gate_block",
            )
        ]
    return [
        ReviewFinding(
            code="visual_evidence_missing_or_blank",
            reason=(
                "phase declared visual_render_declared=true and reported "
                "verification_status=passed, but attached no valid "
                "visual_evidence_path + a DECODED image whose derived pixel stats show "
                "real, sufficiently-covered content (non_black_pixels a meaningful fraction "
                "of the frame and pixel_min!=pixel_max); attach a runner-owned "
                "screenshot/frame image or record a visual_evidence_opt_out reason "
                f"({', '.join(VISUAL_EVIDENCE_OPT_OUT_REASONS)})"
            ),
            severity="block",
            blocker_class="review_gate_block",
        )
    ]
