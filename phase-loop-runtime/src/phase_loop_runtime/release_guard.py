from __future__ import annotations

import fnmatch
import subprocess
from collections.abc import Iterable as AbcIterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from .discovery import plan_metadata

if TYPE_CHECKING:  # annotations only — no hard runtime dependency on the policy module
    from .ratification_policy import BoardFacts, RatificationPolicy


RELEASE_DISPATCH_MUTATION = "release_dispatch"
# The ratification gate W4 resolves the release merge/tag grant against.
RELEASE_DISPATCH_GATE = "release-dispatch"
# Run modes for the W4 unattended-consensus substitution. Attended is the
# default: W4 EXTENDS the existing human grant, it never removes it.
ATTENDED_RUN_MODE = "attended"
UNATTENDED_RUN_MODE = "unattended"
RELEASE_AFFECTING_PATTERNS = (
    ".github/workflows/**",
    "CHANGELOG*",
    "RELEASE*",
    "VERSION",
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
    "uv.lock",
    "poetry.lock",
    "requirements*.txt",
    "docs/release/**",
    "docs/releases/**",
    "docs/release*.md",
    "scripts/*release*",
    "scripts/download-release.py",
)


@dataclass(frozen=True)
class ReleaseDispatchBlocker:
    blocker_class: str
    blocker_summary: str
    required_human_inputs: tuple[str, ...]
    metadata: dict[str, Any]

    def to_blocker(self) -> dict[str, Any]:
        return {
            "human_required": True,
            "blocker_class": self.blocker_class,
            "blocker_summary": self.blocker_summary,
            "required_human_inputs": self.required_human_inputs,
        }


def is_release_dispatch_plan(plan: Path | None) -> bool:
    if plan is None:
        return False
    metadata = plan_metadata(plan)
    return metadata.get("phase_loop_mutation") == RELEASE_DISPATCH_MUTATION


def release_dispatch_blocker(repo: Path, plan: Path | None) -> ReleaseDispatchBlocker | None:
    if plan is None or not is_release_dispatch_plan(plan):
        return None

    metadata = plan_metadata(plan)
    dirty_paths = _dirty_release_affecting_paths(repo)
    if dirty_paths:
        return ReleaseDispatchBlocker(
            blocker_class="dirty_worktree_conflict",
            blocker_summary=(
                "Release dispatch requires clean release-affecting files before the "
                "external workflow is started."
            ),
            required_human_inputs=(
                "Commit, merge, or isolate the release-affecting changes.",
                "Sync the release branch with the configured base ref, then rerun the dispatch phase.",
            ),
            metadata={
                "guard": "release_dispatch",
                "reason": "dirty_release_affecting_paths",
                "plan": str(plan),
                "dirty_paths": dirty_paths,
                "phase_loop_mutation": metadata.get("phase_loop_mutation"),
            },
        )

    base_ref, base_ref_explicit = _release_base_ref(metadata)
    sync = _branch_sync(repo, base_ref)
    if not sync["base_available"]:
        if not base_ref_explicit and not sync["base_remote_available"]:
            return None
        return ReleaseDispatchBlocker(
            blocker_class="branch_sync_conflict",
            blocker_summary=f"Release dispatch base ref `{base_ref}` is unavailable locally.",
            required_human_inputs=(
                f"Fetch or configure `{base_ref}` before rerunning release dispatch.",
                "Rerun the dispatch phase from a branch that can be compared to the release base.",
            ),
            metadata={
                "guard": "release_dispatch",
                "reason": "base_ref_unavailable",
                "plan": str(plan),
                **sync,
            },
        )
    if sync["head"] != sync["base_commit"]:
        return ReleaseDispatchBlocker(
            blocker_class="branch_sync_conflict",
            blocker_summary=(
                f"Release dispatch requires `HEAD` to match `{base_ref}` before the "
                "external workflow is started."
            ),
            required_human_inputs=(
                f"Merge or sync the release branch so `HEAD` matches `{base_ref}`.",
                "Rerun the dispatch phase from the clean synced branch.",
            ),
            metadata={
                "guard": "release_dispatch",
                "reason": "head_not_at_base_ref",
                "plan": str(plan),
                **sync,
            },
        )
    return None


def _release_base_ref(metadata: dict[str, str]) -> tuple[str, bool]:
    for key in ("release_base_ref", "phase_loop_release_base_ref"):
        value = metadata.get(key)
        if value:
            return value, True
    return "origin/main", False


def _dirty_release_affecting_paths(repo: Path) -> list[str]:
    try:
        status = subprocess.check_output(
            ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
            text=True,
        )
    except Exception:
        return []
    paths: list[str] = []
    for line in status.splitlines():
        if not line:
            continue
        path = _status_path(line)
        if path and _is_release_affecting_path(path):
            paths.append(path)
    return sorted(dict.fromkeys(paths))


def _status_path(line: str) -> str:
    path = line[3:] if len(line) > 3 else ""
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    return path.strip().strip('"')


def _is_release_affecting_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return any(fnmatch.fnmatchcase(normalized, pattern) for pattern in RELEASE_AFFECTING_PATTERNS)


def _branch_sync(repo: Path, base_ref: str) -> dict[str, Any]:
    head = _git(repo, "rev-parse", "HEAD")
    base_commit = _git(repo, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
    remote = base_ref.split("/", 1)[0] if "/" in base_ref else ""
    return {
        "base_ref": base_ref,
        "base_available": bool(base_commit),
        "base_remote_available": bool(remote and _git(repo, "remote", "get-url", remote)),
        "head": _short_sha(head),
        "base_commit": _short_sha(base_commit),
    }


def _git(repo: Path, *args: str) -> str:
    try:
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def _short_sha(value: str) -> str:
    return value[:12] if value else ""


# ---------------------------------------------------------------------------
# W4 — unattended consensus substitutes for the human merge/tag grant
# (IF-0-UNATTEND-1). Consumes the frozen ratification_policy (IF-0-POLICY-1).
# ---------------------------------------------------------------------------

# Grant outcomes the durable record can carry.
GRANT_CONSENSUS = "consensus_granted"      # board ratified → consensus stands in for the human
GRANT_PROCEED_DEGRADED = "proceed_degraded"  # shortfall, but on_shortfall dial proceeds w/ audit
GRANT_ESCALATED = "escalated"              # shortfall + escalate → NON-human review_gate_block


@dataclass(frozen=True)
class UnattendedReleaseGrant:
    """IF-0-UNATTEND-1 — the durable record a consensus-substitutes-for-human grant writes.

    In an ``unattended`` run there is no human to satisfy the
    :meth:`ReleaseDispatchBlocker.to_blocker` ``human_required`` grant. W4 lets an
    N-vendor consensus quorum (the release-dispatch ``RatificationPolicy``)
    substitute for it, and records the substitution durably so the paper trail
    survives the run:

    * ``granted`` — did the board (or the ``proceed_degraded`` dial) stand in for
      the human grant? ``True`` ⇒ the release proceeds; ``False`` ⇒ held.
    * ``outcome`` — ``consensus_granted`` (clean ratify), ``proceed_degraded``
      (degraded board knowingly accepted — the 1-subscription dial), or
      ``escalated`` (a NON-human hold).
    * ``ratification`` — the embedded ``RatificationDecision.to_audit()`` verbatim,
      so this freeze can never drift from POLICY's frozen decision shape.
    * ``reviewed_sha`` — the exact commit the board reviewed (#88 SHA-binding): the
      grant vouches for THIS head, not a later one.

    Autonomy-first (Assumption #5): ``escalated`` is a ``review_gate_block``, never
    a new ``human_required`` gate — W4 only substitutes for the EXISTING one."""

    gate: str
    run_mode: str
    substitutes_for: str          # the blocker_class the human grant would have carried
    granted: bool
    outcome: str
    ratification: dict[str, Any]  # RatificationDecision.to_audit(), embedded verbatim
    reviewed_sha: str | None
    detail: str

    def to_audit(self) -> dict[str, Any]:
        """The durable audit record a consumer persists (the paper trail)."""
        return {
            "kind": "unattended_release_grant",
            "gate": self.gate,
            "run_mode": self.run_mode,
            "substitutes_for": self.substitutes_for,
            "granted": self.granted,
            "outcome": self.outcome,
            "reviewed_sha": self.reviewed_sha,
            "detail": self.detail,
            "ratification": self.ratification,
        }

    def to_blocker(self) -> dict[str, Any] | None:
        """The blocker the caller emits INSTEAD of the human grant.

        ``None`` when the grant proceeds (consensus / proceed_degraded). On an
        ``escalate`` shortfall, a NON-human ``review_gate_block`` (``human_required``
        is ``False`` — an agent-recoverable hold, never a human stall)."""
        if self.granted:
            return None
        return {
            "human_required": False,
            "blocker_class": "review_gate_block",
            "blocker_summary": (
                f"Unattended release-dispatch escalated: {self.detail}. "
                "Add the missing reviewer independence/consensus, then rerun."
            ),
            "required_human_inputs": (),
        }


def evaluate_unattended_release(
    blocker: ReleaseDispatchBlocker,
    *,
    policy: "RatificationPolicy",
    facts: "BoardFacts",
    run_mode: str = ATTENDED_RUN_MODE,
) -> UnattendedReleaseGrant | None:
    """W4: in ``unattended`` mode, substitute a consensus quorum for the human grant.

    ``attended`` (default) ⇒ ``None``: the caller keeps the existing
    :meth:`ReleaseDispatchBlocker.to_blocker` ``human_required`` path unchanged —
    W4 EXTENDS the autonomy-first posture, it never removes the human option.

    ``unattended`` ⇒ evaluate the release-dispatch ``RatificationPolicy`` against
    the ACHIEVED board ``facts`` (the pure ``evaluate_ratification`` from
    IF-0-POLICY-1) and return a durable :class:`UnattendedReleaseGrant`. The
    ``policy.on_shortfall`` dial is what handles a 1-subscription operator: a
    degraded board still ``proceed_degraded`` with an audit record, while a strict
    gate ``escalate``\\ s to a non-human hold. No IO, no board composition here —
    the caller supplies ``facts`` (e.g. via ``board_facts_from``)."""
    if run_mode != UNATTENDED_RUN_MODE:
        return None
    from .ratification_policy import evaluate_ratification, shortfall_detail

    decision = evaluate_ratification(policy, facts, gate=RELEASE_DISPATCH_GATE)
    if decision.satisfied:
        outcome, granted = GRANT_CONSENSUS, True
    elif decision.blocks:  # ESCALATE → non-human hold
        outcome, granted = GRANT_ESCALATED, False
    else:  # PROCEED_DEGRADED → proceed with a paper trail
        outcome, granted = GRANT_PROCEED_DEGRADED, True
    return UnattendedReleaseGrant(
        gate=RELEASE_DISPATCH_GATE,
        run_mode=run_mode,
        substitutes_for=blocker.blocker_class,
        granted=granted,
        outcome=outcome,
        ratification=decision.to_audit(),
        reviewed_sha=facts.reviewed_sha,
        detail=shortfall_detail(decision),
    )


# ---------------------------------------------------------------------------
# #145 — typed, metadata-only operator approval for release-dispatch executors.
# ---------------------------------------------------------------------------

# Substrings that mark a key as carrying a secret/credential. #145 requires the
# approval record be METADATA-ONLY: any such key is rejected at parse (fail-closed).
_SECRET_KEY_MARKERS = (
    "secret",
    "token",
    "password",
    "passwd",
    "credential",
    "api_key",
    "apikey",
    "private_key",
    "privatekey",
    "access_key",
    "auth",
)


# The metadata-only field values an approval may carry. bool is an int subclass —
# listed for clarity. Anything else (dict/list/set/object) is refused at parse.
_SCALAR_TYPES = (str, int, float, bool, type(None))


class OperatorApprovalError(ValueError):
    """A raw operator-approval payload was malformed or carried a secret."""


@dataclass(frozen=True)
class OperatorApproval:
    """A typed, metadata-only operator approval for a release-dispatch launch (#145).

    Carries WHICH targets an operator explicitly approved plus the provenance a
    downstream SL-0 gate verifies (timestamp, source, watch-window owner, and the
    roadmap/phase/run identity), and NEVER a secret value. :meth:`covers` is the
    fail-closed predicate an executor uses to confirm every mutated target was
    approved."""

    approved_targets: tuple[str, ...]
    approved_at: str
    source: str
    watch_owner: str
    roadmap: str
    phase: str
    run_id: str

    def covers(self, targets: Iterable[str]) -> bool:
        """True iff EVERY requested target was explicitly approved (fail-closed).

        An empty request is not vacuously approved — a release-dispatch that mutates
        nothing named should not read as "approved"; and any target absent from
        ``approved_targets`` fails the whole check (missing/mismatched → closed)."""
        requested = tuple(targets)
        if not requested:
            return False
        approved = set(self.approved_targets)
        return all(target in approved for target in requested)

    def to_metadata(self) -> dict[str, Any]:
        """The metadata-only projection injected into launch/state/event context.

        Secret-free by construction (the type has no secret field); this is the
        exact shape a ledger/executor context carries so SL-0 can verify the
        approval without unstructured chat history."""
        return {
            "kind": "operator_approval",
            "approved_targets": list(self.approved_targets),
            "approved_at": self.approved_at,
            "source": self.source,
            "watch_owner": self.watch_owner,
            "roadmap": self.roadmap,
            "phase": self.phase,
            "run_id": self.run_id,
        }


def operator_approval_from(payload: Mapping[str, Any]) -> OperatorApproval:
    """Parse a raw approval payload into a typed :class:`OperatorApproval`.

    Fail-closed: rejects any secret-bearing key (:data:`_SECRET_KEY_MARKERS`),
    rejects any non-scalar field value (so a nested container can't smuggle a
    secret past the top-level key scan and get stringified into the metadata), and
    requires at least one approved target label. Missing scalar fields default to
    empty strings — the SL-0 gate decides how strict the provenance must be; this
    parser's job is the type + the metadata-only invariant."""
    if not isinstance(payload, Mapping):
        raise OperatorApprovalError("operator approval payload must be a mapping")
    for key, value in payload.items():
        lowered = str(key).lower()
        if any(marker in lowered for marker in _SECRET_KEY_MARKERS):
            raise OperatorApprovalError(
                f"operator approval must be metadata-only; secret-bearing key rejected: {key!r}"
            )
        # Every field except the target list must be a plain scalar. A nested
        # mapping/list is refused outright — otherwise `{"source": {"api_token":
        # "..."}}` would slip past the (top-level) key scan and be str()'d into
        # to_metadata(), leaking the secret value. Metadata-only ⇒ scalars only.
        if key != "approved_targets" and not isinstance(value, _SCALAR_TYPES):
            raise OperatorApprovalError(
                f"operator approval must be metadata-only scalars; non-scalar value for {key!r}"
            )
    raw_targets = payload.get("approved_targets") or ()
    if isinstance(raw_targets, (str, bytes)):
        raise OperatorApprovalError("approved_targets must be a list of target labels, not a string")
    if not isinstance(raw_targets, AbcIterable):
        raise OperatorApprovalError("approved_targets must be an iterable of target labels")
    # Each element must be a plain string label. A non-str element (dict/list/int)
    # would otherwise be str()'d into an "approved target" and surface in
    # to_metadata() — the same secret-leak vector as a non-scalar field value.
    targets_list: list[str] = []
    for target in raw_targets:
        if not isinstance(target, str):
            raise OperatorApprovalError(
                f"each approved target must be a string label, got {type(target).__name__}"
            )
        if target:
            targets_list.append(target)
    targets = tuple(targets_list)
    if not targets:
        raise OperatorApprovalError("operator approval must name at least one approved target")
    return OperatorApproval(
        approved_targets=targets,
        approved_at=str(payload.get("approved_at", "")),
        source=str(payload.get("source", "")),
        watch_owner=str(payload.get("watch_owner", "")),
        roadmap=str(payload.get("roadmap", "")),
        phase=str(payload.get("phase", "")),
        run_id=str(payload.get("run_id", "")),
    )
