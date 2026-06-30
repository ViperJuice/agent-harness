"""Cross-repo release-train coordinator (P3 + P4).

P3: Serial draft-PR execution: topo-sort the train, preflight ALL repos, then
per node (in topo order): inject upstream draft ref via set_upstream_ref →
invoke the unchanged per-repo run_loop → publish a draft PR → append to ledger.

P4 (``_merge_phase_enabled=True``): After all draft PRs are open, hold for a
train-level governed review (one round, reusing ``run_governed_premerge_loop``).
On panel approval, merge sequentially in topo order.  Before merging each
downstream node, re-resolve its channel to the upstream MERGED SHA and re-verify
(the false-green killer — NEVER merge a downstream that was only green against
the draft ref).  Forward-only: a downstream failure does NOT revert merged
upstream nodes; use expand/contract upstream contracts to keep sequential merges
safe.

Safety invariants (enforced structurally, asserted in tests):
  1. **Zero-PRs-on-preflight-failure**: preflight runs on ALL repos before the
     per-node loop is entered.  If any check fails, ``run_train`` returns
     immediately with ``status="preflight_failed"`` and zero publish calls.
     Train-schema validation (T-A/B/C/D via ``validate_train_loud``) runs as
     part of this gate — a malformed train (e.g. a ``none``-channel dependency
     edge) opens zero PRs.
  2. **Draft-only** (P3): every ``publish_from_worktree`` call uses
     ``draft=True``.  P3 never merges.
  3. **Train state off .phase-loop/**: ledger_path is caller-supplied and must
     pass ``_assert_not_phase_loop``; the coordinator never touches any repo's
     ``.phase-loop/`` directory.
  4. **Resumable with upstream-change detection**: a partial run leaves prior
     nodes' draft PRs open and the failed node ``blocked`` in the ledger.
     Re-running re-reads both the ledger and live PR state; confirmed-open
     nodes are skipped unless an upstream changed (rebuilt this run, or its live
     head SHA diverged from the ledger — out-of-band push).  When an upstream
     changed and the downstream's PR is already open, the downstream is
     **blocked with a clear reason** (``upstream_changed_downstream_pr_open``)
     so the user can close the stale PR and re-run.
     NOTE: automatic downstream rebuild when an upstream changes requires an
     update-existing-PR primitive and is deferred to a future release.
  5. **Exception safety**: if inject or run_loop raises, the node is marked
     ``blocked`` in the ledger (never left stuck at ``running``).
  6. **Autonomy boundary** (P4): cross-repo merges are NEVER auto-merged.  In
     autonomous mode with ``_merge_phase_enabled=True`` the coordinator stops at
     ``status="drafts_open"`` so the operator can review and re-run with
     ``--governed``.  Only ``run_mode="governed"`` proceeds to review + merge.
  7. **False-green killer** (P4): before merging each downstream node,
     ``set_upstream_ref`` is called with the upstream MERGED SHA (not the draft
     SHA) and ``_reverify_fn`` re-verifies the downstream.  A downstream that
     was green only against the draft ref is blocked (``merge_halted``).
  8. **Idempotent resume** (P4): ledger ``merged`` records carry
     ``upstream_merge_sha`` (the real merge-commit SHA) plus ``branch``,
     ``pr_url``, and ``head_sha`` so the resumed run can skip already-merged
     nodes without double-merging and can still inject the correct draft SHA
     for any remaining downstream nodes.

All git/gh/run_loop/publish/review boundaries are injectable seams so the
module is fully testable without live network access.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set

from .cross_repo_channel import ChannelDescriptor, set_upstream_ref
from .train_ledger import LedgerRecord, append_record, read_ledger
from .train_roadmap import TrainEdge, TrainNode, TrainRoadmap

# ---------------------------------------------------------------------------
# Types

ResolveWorkspace = Callable[[TrainNode], Path]
ResolveOwnedPaths = Callable[[TrainNode], Sequence[str]]

# ---------------------------------------------------------------------------
# Preflight check functions
# Each is a module-level function so tests can patch it individually.


def _check_gh_auth() -> Optional[str]:
    """Return an error string if gh auth is not valid, else None.

    Stubbable seam: ``patch("phase_loop_runtime.train_runner._check_gh_auth")``.
    """
    completed = subprocess.run(
        ["gh", "auth", "status"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if completed.returncode != 0:
        return f"gh auth status failed: {completed.stderr.strip() or 'not authenticated'}"
    return None


def _check_repo_clean(workspace: Path, node_id: str) -> Optional[str]:
    """Return an error string if the workspace has uncommitted changes, else None."""
    completed = subprocess.run(
        ["git", "-C", str(workspace), "status", "--short"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if completed.returncode != 0:
        return (
            f"[{node_id}] git status failed "
            f"(workspace may not be a git repo): {completed.stderr.strip()}"
        )
    if completed.stdout.strip():
        return f"[{node_id}] workspace '{workspace}' has uncommitted changes — preflight failed"
    return None


def _check_remote_reachable(workspace: Path, node_id: str, remote: str = "origin") -> Optional[str]:
    """Return an error string if the remote is not reachable, else None."""
    completed = subprocess.run(
        ["git", "-C", str(workspace), "ls-remote", "--exit-code", remote, "HEAD"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        return (
            f"[{node_id}] remote '{remote}' is not reachable: "
            f"{completed.stderr.strip() or 'ls-remote failed'}"
        )
    return None


def _check_base_branch_exists(
    workspace: Path, node_id: str, base: str = "main"
) -> Optional[str]:
    """Return an error string if origin/<base> does not exist, else None."""
    completed = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "--verify", f"origin/{base}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        return f"[{node_id}] base branch 'origin/{base}' does not exist"
    return None


def _default_preflight(
    nodes: List[TrainNode],
    resolve_workspace: ResolveWorkspace,
) -> List[str]:
    """Run all preflight checks across all nodes; return list of errors (empty = pass).

    Checks (in order):
    1. ``gh auth status`` — once, globally.
    2. Per-repo: workspace clean (no uncommitted changes).
    3. Per-repo: remote ``origin`` is reachable.
    4. Per-repo: base branch ``origin/main`` exists.

    A non-empty return means the entry gate is closed; zero PRs must be opened.
    """
    errors: List[str] = []

    # gh auth — once globally, before touching any repo
    auth_err = _check_gh_auth()
    if auth_err:
        errors.append(auth_err)

    for node in nodes:
        workspace = resolve_workspace(node)
        for check_fn in (
            _check_repo_clean,
            _check_remote_reachable,
            _check_base_branch_exists,
        ):
            err = check_fn(workspace, node.node_id)
            if err:
                errors.append(err)

    return errors


# ---------------------------------------------------------------------------
# Live PR state seams


def _live_pr_is_open(workspace: Path, branch: str) -> bool:
    """Return True if ``branch`` has an open PR on the remote.

    Stubbable seam for tests.  Uses ``_gh_pr_metadata`` from ``git_topology``
    (already reused by the P1 publish primitive for the same reason).
    """
    from .git_topology import _gh_pr_metadata

    meta = _gh_pr_metadata(workspace, branch)
    return bool(meta.get("pr_url"))


def _live_pr_head_sha(workspace: Path, branch: str) -> Optional[str]:
    """Return the live PR head commit SHA for ``branch``, or None if unavailable.

    Queries ``gh pr list`` (the same endpoint as ``_gh_pr_metadata``, which uses
    the proven ``--head <branch>`` flag) and extracts ``headRefOid`` — the commit
    SHA at the PR's head, which may differ from the ledger-recorded value if the
    branch was force-pushed since the last run.

    Uses ``gh pr list --head <branch>`` (not ``gh pr view``, which takes a PR
    number, not a branch ref).

    Stubbable seam: inject ``_live_pr_head_sha_fn`` into :func:`run_train`.
    """
    try:
        completed = subprocess.run(
            [
                "gh", "pr", "list",
                "--head", branch,
                "--state", "open",
                "--limit", "1",
                "--json", "headRefOid",
                "--jq", ".[0].headRefOid",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(workspace),
        )
        sha = completed.stdout.strip() if completed.returncode == 0 else ""
        return sha or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# P4 merge seams — stubbed in tests; live implementations below.

#: Synthetic ledger node_id used to record the train-level review approval.
#: Not a real roadmap node — only stored in the coordinator ledger.
_TRAIN_REVIEW_NODE_ID: str = "_train_review_"


def _live_merge_pr(workspace: Path, branch: str) -> str:
    """Merge the PR for ``branch`` via the GitHub CLI; return the merge commit SHA.

    Idempotent: checks ``_live_pr_merged_sha`` before issuing the merge command.
    Already-merged PRs return the existing SHA without error.

    Stubbable seam: inject ``_merge_pr_fn`` into :func:`run_train`.
    """
    # Idempotent guard: if already merged, return the existing SHA.
    existing = _live_pr_merged_sha(workspace, branch)
    if existing:
        return existing

    subprocess.run(
        ["gh", "pr", "merge", branch, "--merge", "--delete-branch", "--yes"],
        cwd=str(workspace),
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    result = subprocess.run(
        [
            "gh", "pr", "view", branch,
            "--json", "mergeCommit",
            "--jq", ".mergeCommit.oid",
        ],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=30,
    )
    sha = result.stdout.strip()
    if not sha or sha == "null":
        raise RuntimeError(
            f"could not determine merge commit SHA for branch '{branch}' in "
            f"'{workspace}'; gh pr view returned no mergeCommit.oid after merge"
        )
    return sha


def _live_reverify(workspace: Path, roadmap_path: Path, run_mode: str) -> bool:
    """Re-verify a downstream node against the injected upstream merged pin.

    Called after ``set_upstream_ref`` writes the merged SHA into the downstream
    workspace.  Runs the downstream's verification commands (from its plan file)
    directly against that workspace and returns True only when they all pass.

    This is the live default for ``_reverify_fn``.  Tests stub this seam.

    NOTE: Does NOT re-publish or open a new PR — only re-verifies.  The
    existing draft PR from P3 remains open.  The merged-pin file that
    ``set_upstream_ref`` wrote is read by whatever commands the plan declares
    in its ``## Verification`` section.

    Fail-closed: if the plan cannot be located or verification cannot be run
    (for any reason), returns False — never silently green.
    """
    import os

    from .discovery import find_plan_artifact, resolve_suite_command_doc, verification_commands_from_plan
    from .reconcile import reconcile
    from .verification_evidence import (
        ARTIFACT_NAME,
        detect_changed_dependency_manifests,
        resolve_install_command,
        run_verification,
        validate_verification_artifact,
    )

    try:
        # 1. Find the current phase from the workspace state.  The node was
        #    left at awaiting_phase_closeout by the P3 run_loop call; reconcile
        #    reads the persisted state and event log to reconstruct that status.
        snapshot = reconcile(workspace, roadmap_path)
        phase = snapshot.current_phase
        if phase is None:
            # Fallback: scan for any phase still at awaiting_phase_closeout.
            for ph, status in snapshot.phases.items():
                if status == "awaiting_phase_closeout":
                    phase = ph
                    break
        if phase is None:
            # Cannot determine which phase to verify — fail closed.
            return False

        # 2. Locate the plan file (same resolver the closeout path uses).
        plan = find_plan_artifact(workspace, phase, roadmap=roadmap_path)
        if plan is None:
            # No plan = cannot verify → fail closed.
            return False

        # 3. Extract verification commands from the plan.
        commands, operational_exemptions = verification_commands_from_plan(plan)
        suite_command, suite_findings = resolve_suite_command_doc(workspace, roadmap_path, plan)
        if suite_findings:
            # Malformed suite command — fail closed.
            return False

        # 4. If the plan declares no verification at all, treat as trivial pass
        #    (the plan author deliberately chose not to add verification).
        if not commands and suite_command is None:
            return True

        # 5. Run verification against the workspace.  set_upstream_ref has
        #    already written the merged-pin file, so commands that read the
        #    pin file will see the merged SHA.
        manifests = detect_changed_dependency_manifests(workspace, "HEAD")
        install_argv = resolve_install_command(workspace, manifests) if manifests else None
        env_refresh = (
            {
                "triggered": True,
                "manifests": manifests,
                "install_argv": install_argv or [],
                "exit_code": 127,
            }
            if manifests and install_argv is None
            else (
                {"triggered": True, "manifests": manifests, "install_argv": install_argv}
                if manifests
                else None
            )
        )
        timeout_s = float(os.environ.get("PHASE_LOOP_VERIFY_TIMEOUT_SECONDS", "1200"))
        # run_verification requires run_dir inside the workspace (the same
        # constraint run_artifacts enforces).  Use a timestamped subdirectory
        # under .phase-loop/runs/ so the artifact is discoverable.
        from .models import utc_now
        run_id = f"{utc_now().replace(':', '').replace('-', '').replace('Z', 'Z')}-reverify"
        run_dir = workspace / ".phase-loop" / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        run_verification(
            workspace,
            run_dir,
            commands,
            suite_command,
            env_refresh,
            timeout_s,
            operational_exemptions=operational_exemptions,
        )
        artifact_path = run_dir / ARTIFACT_NAME
        validation = validate_verification_artifact(artifact_path)
        return validation.ok
    except Exception:
        return False


def _live_pr_merged_sha(workspace: Path, branch: str) -> Optional[str]:
    """Return the merge-commit SHA if the PR for ``branch`` is already merged, else None.

    Used for idempotent resume: the merge loop skips nodes whose PR has already
    landed on main (covers crash-between-merge-and-ledger-write).

    Stubbable seam: inject ``_pr_merged_sha_fn`` into :func:`run_train`.
    """
    try:
        result = subprocess.run(
            [
                "gh", "pr", "view", branch,
                "--json", "state,mergeCommit",
                "--jq", 'if .state == "MERGED" then .mergeCommit.oid else null end',
            ],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=15,
        )
        sha = result.stdout.strip()
        return sha if (result.returncode == 0 and sha and sha not in {"null", ""}) else None
    except Exception:
        return None


def _default_train_review(artifact: str, run_mode: str) -> "LoopResult":
    """Train-level governed review: one-round bounded panel review.

    Returns a :class:`LoopResult` with ``mergeable=True`` on approval or a
    non-human terminal blocker (``human_required=False``) on rejection.

    In ``autonomous`` mode ``run_governed_premerge_loop`` short-circuits to
    ``mergeable=True`` without spawning a panel — callers should gate on
    ``run_mode == "governed"`` before reaching here (P4 enforces this).

    Stubbable seam: inject ``_train_review_fn`` into :func:`run_train`.
    """
    from .governed_premerge import LoopResult, run_governed_premerge_loop

    return run_governed_premerge_loop(
        artifact=artifact,
        author_executor="train-coordinator",
        run_mode=run_mode,
        max_rounds=1,
        apply_fix=None,
    )


def _build_train_review_bundle(
    roadmap: "TrainRoadmap",
    completed_nodes: Dict[str, Dict],
    topo_order: "List[TrainNode]",
) -> str:
    """Build the artifact text for the train-level review panel.

    Summarises all draft PRs in merge order so the panel can review the
    cross-repo change as one logical unit.
    """
    lines: List[str] = [
        "# Train-level bundle review\n\n",
        f"**Train:** `{roadmap.title}`\n\n",
        "## Draft PRs (merge order)\n\n",
        "Review the following PRs as **one logical cross-repo change**.\n",
        "Approve (AGREE) only if the change is correct as a unit.\n\n",
    ]
    for i, node in enumerate(topo_order, 1):
        nid = node.node_id
        info = completed_nodes.get(nid, {})
        pr_url = info.get("pr_url", "(unknown)")
        head_sha = info.get("head_sha") or "?"
        short_sha = head_sha[:8] if len(head_sha) >= 8 else head_sha
        lines.append(f"{i}. **`{nid}`** — [PR]({pr_url}) (draft `{short_sha}`)\n")
    lines.append(
        "\n---\n"
        "Reject (DISAGREE) with specific blocking concerns if not ready.\n"
    )
    return "".join(lines)


# ---------------------------------------------------------------------------
# PR body builder


def _build_pr_body(
    node: TrainNode,
    topo_order: List[TrainNode],
    upstream_results: Dict[str, Dict],
    upstream_edges: List[TrainEdge],
) -> str:
    """Build the PR body with cross-repo dependency links and merge order.

    At creation time, upstream PRs are already open (topo order guarantees
    it).  Downstream PRs are not yet open, so only backward-links are included.
    """
    lines: List[str] = [
        f"## Cross-repo release train\n\n",
        f"**Node:** `{node.node_id}`\n\n",
    ]

    if upstream_edges:
        lines.append("### Upstream dependencies (must merge first)\n\n")
        for edge in upstream_edges:
            result = upstream_results.get(edge.upstream.node_id, {})
            pr_url = result.get("pr_url", "(not yet open)")
            lines.append(f"- [{edge.upstream.node_id}]({pr_url})\n")
        lines.append("\n")

    lines.append("### Train merge order\n\n")
    for i, n in enumerate(topo_order, 1):
        marker = " **(this PR)**" if n.node_id == node.node_id else ""
        lines.append(f"{i}. `{n.node_id}`{marker}\n")

    return "".join(lines)


# ---------------------------------------------------------------------------
# Main coordinator


def run_train(
    roadmap: TrainRoadmap,
    ledger_path: Path,
    *,
    run_mode: str = "autonomous",
    resolve_workspace: ResolveWorkspace,
    resolve_owned_paths: Optional[ResolveOwnedPaths] = None,
    # Injectable seams — default to the live implementations; tests override.
    _run_loop: Optional[Callable] = None,
    _publish: Optional[Callable] = None,
    _set_upstream_ref_fn: Optional[Callable] = None,
    _pr_is_open: Optional[Callable] = None,
    _live_pr_head_sha_fn: Optional[Callable] = None,
    _preflight_fn: Optional[Callable] = None,
    # P4 gate: False (default) preserves P3 behavior for all existing callers.
    # The CLI sets this True; run_mode then determines autonomous vs governed.
    _merge_phase_enabled: bool = False,
    # P4 seams — unused when _merge_phase_enabled is False.
    _merge_pr_fn: Optional[Callable] = None,       # (workspace, branch) → merged_sha
    _reverify_fn: Optional[Callable] = None,         # (workspace, roadmap_path, run_mode) → bool
    _train_review_fn: Optional[Callable] = None,     # (artifact, run_mode) → LoopResult
    _pr_merged_sha_fn: Optional[Callable] = None,    # (workspace, branch) → Optional[str]
) -> Dict:
    """Coordinate a cross-repo release train: preflight, topo-sort, draft-PR open [+ merge].

    Parameters
    ----------
    roadmap:
        Parsed ``TrainRoadmap`` (P2 schema).
    ledger_path:
        Path to the coordinator-side ledger file.  Must not be inside any
        repo's ``.phase-loop/`` (enforced by ``append_record``).
    run_mode:
        ``"autonomous"`` or ``"governed"``.  Passed unchanged to each
        per-repo ``run_loop`` call and to the P4 review/merge gate.
    resolve_workspace:
        Maps a ``TrainNode`` to its workspace ``Path`` on disk.
    resolve_owned_paths:
        Maps a ``TrainNode`` to the list of paths the publish primitive
        should stage.  When ``None`` (the default for real end-to-end runs),
        the coordinator uses the paths produced by ``run_loop`` itself:
        ``StateSnapshot.phase_owned_dirty_paths`` (or ``dirty_paths`` as
        fallback).  Callers may pass an explicit resolver to override this
        (e.g. tests, or callers that know the paths ahead of time).
    _run_loop, _publish, _set_upstream_ref_fn, _pr_is_open,
    _live_pr_head_sha_fn, _preflight_fn:
        P3 injectable seams for testing.  Each defaults to the corresponding
        live implementation.
    _merge_phase_enabled:
        When ``False`` (default): P3 behavior only — returns
        ``{"status": "completed"}`` once all draft PRs are open.  This
        preserves backward compatibility with all P3 callers and tests.
        When ``True``: P4 merge logic activates.  Autonomous mode stops at
        ``{"status": "drafts_open"}``; governed mode proceeds to review+merge.
    _merge_pr_fn, _reverify_fn, _train_review_fn, _pr_merged_sha_fn:
        P4 injectable seams.  Each defaults to the corresponding live
        implementation.  Unused when ``_merge_phase_enabled=False``.

    Returns
    -------
    dict
        P3 statuses:
          ``{"status": "completed", "nodes": {…}}`` — all draft PRs open
          (``_merge_phase_enabled=False``);
          ``{"status": "blocked", "node_id": …, "detail": …}`` — P3 node
          failed; prior PRs remain open (resumable);
          ``{"status": "preflight_failed", "errors": […]}`` — zero PRs opened.
        P4 statuses (``_merge_phase_enabled=True``):
          ``{"status": "drafts_open", "nodes": {…}}`` — all draft PRs open;
          autonomous mode terminal (no merge);
          ``{"status": "review_halted", …}`` — panel rejected; ZERO merges
          (``terminal_blocker`` carries ``human_required=False``);
          ``{"status": "merged", "nodes": {nid: {branch, merged_sha}}}`` —
          all nodes merged in topo order;
          ``{"status": "merge_halted", "node_id": …, "reason": …}`` —
          downstream re-verify failed; upstream stays merged (forward-only).
    """
    # Resolve seams
    from .publishing import publish_from_worktree as _default_publish
    from .runner import run_loop as _default_run_loop

    run_loop_fn = _run_loop if _run_loop is not None else _default_run_loop
    publish_fn = _publish if _publish is not None else _default_publish
    set_upstream_ref_fn = (
        _set_upstream_ref_fn if _set_upstream_ref_fn is not None else set_upstream_ref
    )
    pr_is_open_fn = _pr_is_open if _pr_is_open is not None else _live_pr_is_open
    live_pr_head_sha_fn = (
        _live_pr_head_sha_fn if _live_pr_head_sha_fn is not None else _live_pr_head_sha
    )
    preflight_fn = _preflight_fn if _preflight_fn is not None else _default_preflight

    # Track whether caller supplied an explicit owned-paths resolver so we know
    # whether to fall back to the run_loop-produced snapshot paths (Finding #1).
    _explicit_owned_paths = resolve_owned_paths is not None

    # --- Step 0: Train-schema validation (T-A/B/C/D) — BEFORE any PR ------
    # A malformed train (e.g. a none-channel dependency edge) must open ZERO
    # PRs.  validate_train_loud raises ValueError on any violation.
    from .train_roadmap import validate_train_loud

    try:
        validate_train_loud(roadmap)
    except ValueError as exc:
        return {
            "status": "preflight_failed",
            "errors": [f"train validation failed: {exc}"],
        }

    # --- Step 1: Topo-sort (raises ValueError on cycle) --------------------
    topo_order = roadmap.topo_order()

    # --- Step 2: Train-level preflight — ALL repos, BEFORE any PR ----------
    # This is the structural guarantee that preflight failure → zero PRs:
    # we return immediately here, before the per-node loop is entered.
    preflight_errors = preflight_fn(topo_order, resolve_workspace)
    if preflight_errors:
        return {
            "status": "preflight_failed",
            "errors": preflight_errors,
        }

    # --- Step 3: Re-read ledger + live PR state (resume support) -----------
    ledger_state = read_ledger(ledger_path)
    # completed_nodes: node_id → {branch, head_sha, pr_url}
    # These are the upstream refs the coordinator can inject into downstream
    # nodes via set_upstream_ref (IF-0-P2-2).
    completed_nodes: Dict[str, Dict] = {}
    # out_of_band_upstreams: nodes whose live PR head SHA differs from the
    # ledger-recorded head_sha — an out-of-band push since the last run.
    out_of_band_upstreams: Set[str] = set()

    for node in topo_order:
        nid = node.node_id
        rec = ledger_state.get(nid)
        if rec and rec.status in ("pr_open", "merged") and rec.branch and rec.pr_url:
            workspace = resolve_workspace(node)
            if rec.status == "pr_open":
                if not pr_is_open_fn(workspace, rec.branch):
                    # In P4 mode: before dropping, check whether this PR was
                    # already merged on GitHub (crash window between the merge
                    # call and the ledger write).  If merged, recover the SHA
                    # and add to completed_nodes so downstream injection works
                    # correctly; write a merged record so the P4 merge loop
                    # skips it (idempotent, forward-only).
                    if _merge_phase_enabled:
                        _step3_merged_sha_fn = (
                            _pr_merged_sha_fn if _pr_merged_sha_fn is not None
                            else _live_pr_merged_sha
                        )
                        _recovered_sha = _step3_merged_sha_fn(workspace, rec.branch)
                        if _recovered_sha:
                            _recovered_rec = LedgerRecord(
                                node_id=nid,
                                status="merged",
                                branch=rec.branch,
                                pr_url=rec.pr_url,
                                head_sha=rec.head_sha,
                                upstream_merge_sha=_recovered_sha,
                            )
                            append_record(ledger_path, _recovered_rec)
                            # Update in-memory view so Step 4's merged-node skip
                            # (nid_rec.status == "merged" → continue) fires correctly.
                            ledger_state[nid] = _recovered_rec
                            completed_nodes[nid] = {
                                "branch": rec.branch,
                                "head_sha": rec.head_sha,
                                "pr_url": rec.pr_url,
                            }
                    continue  # not open: recovered-as-merged or dropped
                # Prefer the live PR head SHA (the branch may have been updated
                # since the last run); fall back to the ledger-recorded head_sha.
                live_sha = live_pr_head_sha_fn(workspace, rec.branch)
                head_sha = live_sha or rec.head_sha
                # Detect out-of-band push: live SHA exists and differs from ledger.
                if live_sha and rec.head_sha and live_sha != rec.head_sha:
                    out_of_band_upstreams.add(nid)
            else:
                # status == "merged" — P4 resume: PR is already merged, no live check.
                # head_sha carries the draft SHA (written to the merged record by P4
                # so this node can still serve as the injection ref for any P3 nodes
                # that were not yet processed before the crash — see merged-record write).
                head_sha = rec.head_sha
            completed_nodes[nid] = {
                "branch": rec.branch,
                "head_sha": head_sha,
                "pr_url": rec.pr_url,
            }

    # --- Step 4: Execute in topo order ------------------------------------
    # rebuilt_this_run tracks nodes where run_loop was actually invoked during
    # this execution.  Used to detect when a downstream's confirmed-open PR
    # is stale because its upstream was rebuilt (Finding #4).
    rebuilt_this_run: Set[str] = set()

    for i, node in enumerate(topo_order):
        nid = node.node_id

        # Resume: skip nodes already confirmed pr_open (live PR check passed) OR
        # already merged (P4 resume — merged nodes cannot be rebuilt or stale).
        # For pr_open nodes: check stale-upstream (an upstream rebuilt this run
        # or received an out-of-band push) and block with a clear reason so the
        # user can close the stale PR and re-run.
        #
        # NOTE: automatic downstream rebuild when an upstream changes requires an
        # update-existing-PR primitive and is deferred to a future release.
        if nid in completed_nodes:
            # Merged nodes: skip unconditionally — no stale-upstream check applies.
            nid_rec = ledger_state.get(nid)
            if nid_rec and nid_rec.status == "merged":
                continue

            upstream_edges = roadmap.edges_for_downstream(node)
            changed_upstreams = [
                edge for edge in upstream_edges
                if edge.upstream.node_id in rebuilt_this_run
                or edge.upstream.node_id in out_of_band_upstreams
            ]
            if not changed_upstreams:
                continue
            # An upstream changed and this node's draft PR is still open.
            # Block so the user can close the stale PR and re-run.
            change_reasons: List[str] = []
            for edge in changed_upstreams:
                uid = edge.upstream.node_id
                if uid in rebuilt_this_run:
                    change_reasons.append(f"upstream {uid!r} was rebuilt this run")
                else:
                    new_sha = completed_nodes.get(uid, {}).get("head_sha", "<unknown>")
                    change_reasons.append(
                        f"upstream {uid!r} advanced to {new_sha!r} (out-of-band push)"
                    )
            detail_msg = (
                "; ".join(change_reasons)
                + f"; close/supersede the stale downstream PR and re-run"
            )
            append_record(
                ledger_path,
                LedgerRecord(
                    node_id=nid,
                    status="blocked",
                    branch=completed_nodes[nid].get("branch"),
                ),
            )
            return {
                "status": "blocked",
                "node_id": nid,
                "detail": {
                    "reason": "upstream_changed_downstream_pr_open",
                    "message": detail_msg,
                },
            }

        workspace = resolve_workspace(node)
        upstream_edges = roadmap.edges_for_downstream(node)

        # Mark as running (durable breadcrumb for diagnostics)
        append_record(ledger_path, LedgerRecord(node_id=nid, status="running"))

        try:
            # (i) Inject upstream draft refs (IF-0-P2-2) BEFORE run_loop.
            #     Collect injected paths to union into owned_paths after run_loop.
            #
            # The guard below is a defensive invariant.  It should be unreachable
            # in a well-formed train: validate_train_loud (T-B) ensures every
            # upstream is a declared node, and topo-sort guarantees we processed
            # it before this node.  If the upstream failed/was blocked, run_train
            # returns immediately and never reaches this downstream.  Kept here to
            # make the "no silent skip" contract explicit and catch future refactors.
            injected_channel_paths: List[str] = []
            for edge in upstream_edges:
                upstream_result = completed_nodes.get(edge.upstream.node_id)
                if upstream_result is None:
                    # Defensive: topo-order + T-B validation make this
                    # unreachable; kept as an explicit fail-loud guard.
                    raise RuntimeError(
                        f"upstream ref for '{edge.upstream.node_id}' is not resolved "
                        f"(not in completed_nodes) — cannot inject into "
                        f"'{nid}'; the upstream must be built and published first"
                    )
                ref = upstream_result.get("head_sha")
                if not ref:
                    # Block: do NOT fall back to injecting a moving branch name.
                    # A missing SHA means neither the live query nor the ledger
                    # have a pinnable ref — injecting a branch name would build
                    # the downstream against a moving target.
                    raise RuntimeError(
                        f"no resolvable SHA for upstream '{edge.upstream.node_id}' "
                        f"(live head SHA query returned None and ledger head_sha is "
                        f"None); cannot inject a moving branch name for channel "
                        f"{edge.channel.kind!r} — resolve the upstream SHA and re-run"
                    )
                injected = set_upstream_ref_fn(workspace, edge.channel, ref)
                if injected:
                    injected_channel_paths.extend(injected)

            # (ii) Invoke the unchanged per-repo run_loop.
            #      The real run_loop returns (StateSnapshot, list[LaunchResult]).
            result_tuple = run_loop_fn(
                workspace, workspace / node.roadmap, run_mode=run_mode
            )

            # SHOULD-FIX 3: Guard against partial multi-phase nodes.
            #
            # run_loop defaults to max_phases=1, so a node with a >1-phase
            # roadmap stops after the first phase.  If any phase is still
            # "planned" the node is incomplete — publishing it ships a partial
            # draft PR.  Block loudly instead.
            #
            # Use getattr so we're forward-compatible with test fixtures that
            # return lightweight SimpleNamespace objects lacking a phases field.
            _node_snapshot = result_tuple[0] if isinstance(result_tuple, tuple) else None
            _node_phases = getattr(_node_snapshot, "phases", None)
            if _node_phases is not None:
                # A node may publish a draft PR only when EVERY phase reached a
                # clean green terminal — "complete" or "awaiting_phase_closeout"
                # (the post-run_loop success state under manual closeout).  ANY
                # other state (planned/blocked/failed_verification/executing/
                # human_required/unknown) means the node is incomplete or broken.
                # Blocking only "planned" (the prior narrow guard) let a
                # *failed*-phase node publish a draft that could later trivial-pass
                # P4 re-verify on a no-verification plan — a combined false-green.
                # Block loudly on any non-green phase instead.
                _GREEN_PHASE_STATES = {"complete", "awaiting_phase_closeout"}
                _not_green = sorted(
                    ph for ph, st in _node_phases.items() if st not in _GREEN_PHASE_STATES
                )
                if _not_green:
                    raise RuntimeError(
                        f"node '{nid}' has phases not in a green state after run_loop "
                        f"({', '.join(_not_green)}); refusing to publish a partial or "
                        f"failed draft PR — every phase must reach complete/"
                        f"awaiting_phase_closeout before publishing"
                    )

            # (iii) Determine owned paths (Finding #1).
            #       If the caller supplied an explicit resolver, honour it.
            #       Otherwise use the snapshot's produced/owned paths so the
            #       published PR contains the actual implementation, not just
            #       the roadmap file.
            if _explicit_owned_paths:
                owned_paths = list(resolve_owned_paths(node))  # type: ignore[arg-type]
            else:
                snapshot = result_tuple[0] if isinstance(result_tuple, tuple) else None
                if snapshot is not None:
                    produced = (
                        getattr(snapshot, "phase_owned_dirty_paths", None)
                        or getattr(snapshot, "dirty_paths", None)
                        or ()
                    )
                    owned_paths = list(produced)
                else:
                    owned_paths = []

            # Union the coordinator-injected channel paths into owned_paths so
            # the pin/submodule change always ships in the PR even if run_loop's
            # snapshot doesn't include the injected file (Finding #6 / union fix).
            # de-duplicate while preserving order (snapshot paths first).
            if injected_channel_paths:
                seen = set(owned_paths)
                for p in injected_channel_paths:
                    if p not in seen:
                        owned_paths.append(p)
                        seen.add(p)

            # (iv) Publish as draft PR via the P1 runtime primitive.
            #      draft=True is structural — P3 never merges.
            pr_body = _build_pr_body(node, topo_order, completed_nodes, upstream_edges)
            publish_result = publish_fn(
                workspace,
                owned_paths,
                draft=True,  # P3 invariant: draft-only, never merge
                pr_body=pr_body,
            )

        except Exception as exc:
            # Inject or run_loop or publish raised — mark blocked so the node
            # is never left stuck at "running" (Finding #3 / exception safety).
            append_record(
                ledger_path,
                LedgerRecord(
                    node_id=nid,
                    status="blocked",
                    branch=None,
                ),
            )
            return {
                "status": "blocked",
                "node_id": nid,
                "detail": {"reason": str(exc)},
            }

        if publish_result.get("status") != "published":
            # Node blocked by the publish primitive (e.g. push rejected, dirty
            # worktree, publication_blocked).  Record in ledger and halt.
            # Prior nodes' draft PRs remain open; the train is resumable.
            append_record(
                ledger_path,
                LedgerRecord(
                    node_id=nid,
                    status="blocked",
                    branch=publish_result.get("branch"),
                ),
            )
            return {
                "status": "blocked",
                "node_id": nid,
                "detail": publish_result,
            }

        # Record success — store draft head in ``head_sha``; leave
        # ``upstream_merge_sha`` for P4's merged-SHA (Finding #5).
        branch = publish_result["branch"]
        head_sha = publish_result["head_sha"]
        pr_url = publish_result["pr_url"]

        completed_nodes[nid] = {
            "branch": branch,
            "head_sha": head_sha,
            "pr_url": pr_url,
        }
        rebuilt_this_run.add(nid)

        append_record(
            ledger_path,
            LedgerRecord(
                node_id=nid,
                status="pr_open",
                branch=branch,
                pr_url=pr_url,
                head_sha=head_sha,       # draft branch HEAD SHA
                upstream_merge_sha=None,  # reserved for P4 (merge SHA only)
                merge_order=i,
            ),
        )

    # --- P3 complete: all draft PRs open ------------------------------------

    if not _merge_phase_enabled:
        # P3 behavior (default): done once draft PRs are open.  Cross-repo
        # merges are a P4 concern.  All existing P3 callers and tests hit this
        # path because they never set _merge_phase_enabled=True.
        return {"status": "completed", "nodes": completed_nodes}

    # --- P4: train-level governed review + sequential merge with re-verify ---
    #
    # Autonomy boundary: cross-repo merges are NEVER auto-merged.  In
    # autonomous mode (the default) the coordinator stops here at a
    # ``drafts_open`` terminal.  Only ``run_mode="governed"`` (opt-in via
    # ``--governed`` CLI flag) proceeds to the review + merge loop.
    if run_mode != "governed":
        return {"status": "drafts_open", "nodes": completed_nodes}

    # Resolve P4 seams (defaults to live; tests inject stubs).
    merge_pr_fn = _merge_pr_fn if _merge_pr_fn is not None else _live_merge_pr
    reverify_fn = _reverify_fn if _reverify_fn is not None else _live_reverify
    train_review_fn = (
        _train_review_fn if _train_review_fn is not None else _default_train_review
    )
    pr_merged_sha_fn = (
        _pr_merged_sha_fn if _pr_merged_sha_fn is not None else _live_pr_merged_sha
    )

    # Re-read ledger to recover P4 state from a previous partial run
    # (idempotent resume: skip already-merged nodes, recover their SHA).
    p4_ledger_state = read_ledger(ledger_path)

    # Build merged_shas: node_id → actual merge-commit SHA.  The field
    # ``upstream_merge_sha`` on a ``merged`` record holds the real SHA
    # (NOT the draft head_sha — ledger schema keeps these distinct).
    merged_shas: Dict[str, str] = {}
    for _nid_r, _rec_r in p4_ledger_state.items():
        if _rec_r.status == "merged" and _rec_r.upstream_merge_sha:
            merged_shas[_nid_r] = _rec_r.upstream_merge_sha

    # Cross-check with live GitHub state: covers crash-between-merge-and-
    # ledger-write (the PR landed on main but the ledger write didn't happen).
    for _node_r in topo_order:
        _nid_r = _node_r.node_id
        if _nid_r in merged_shas:
            continue  # already recovered from ledger
        _pr_branch_r = completed_nodes.get(_nid_r, {}).get("branch")
        if _pr_branch_r:
            _ws_r = resolve_workspace(_node_r)
            _live_sha_r = pr_merged_sha_fn(_ws_r, _pr_branch_r)
            if _live_sha_r:
                merged_shas[_nid_r] = _live_sha_r

    # --- Train-level review (one-round bounded panel) ----------------------
    # Idempotent resume: skip review if already approved in a previous run.
    train_review_rec = p4_ledger_state.get(_TRAIN_REVIEW_NODE_ID)
    already_approved = (
        train_review_rec is not None and train_review_rec.status == "approved"
    )

    if not already_approved:
        bundle_text = _build_train_review_bundle(roadmap, completed_nodes, topo_order)
        review_result = train_review_fn(bundle_text, run_mode)

        if not review_result.mergeable:
            # Non-approval → NON-HUMAN terminal, ZERO merges.
            # The partial-merge-disaster guard: no node is merged if the panel
            # rejects the train.  terminal_blocker carries human_required=False.
            return {
                "status": "review_halted",
                "nodes": completed_nodes,
                "terminal_blocker": review_result.terminal_blocker,
                "reason": review_result.reason or "train_review_rejected",
            }

        # Record approval (with synthetic node_id — never a real roadmap node).
        append_record(
            ledger_path,
            LedgerRecord(
                node_id=_TRAIN_REVIEW_NODE_ID,
                status="approved",
            ),
        )

    # --- Sequential merge in topo order with downstream re-verify -----------
    #
    # False-green killer: before merging each downstream node, re-resolve its
    # channel to the upstream MERGED SHA and re-verify.  NEVER merge a
    # downstream that was only green against the draft (unmerged) upstream ref.
    #
    # Forward-only: a downstream re-verify failure does NOT revert merged
    # upstream nodes.  Recommendation: use expand/contract (backward-compatible)
    # upstream contracts so sequential merges remain safe even when a downstream
    # fails post-merge of its upstream.
    for _i_m, _node_m in enumerate(topo_order):
        _nid_m = _node_m.node_id

        # Idempotent: skip already-merged nodes (never double-merge).
        if _nid_m in merged_shas:
            continue

        _ws_m = resolve_workspace(_node_m)
        _upstream_edges_m = roadmap.edges_for_downstream(_node_m)

        if _upstream_edges_m:
            # Re-resolve every upstream channel to its MERGED SHA (not the
            # draft SHA from P3).  This is the call the test asserts on:
            # set_upstream_ref must be called with the merged SHA and must
            # appear in the call log BEFORE the re-verify call.
            #
            # Wrap inject + reverify: either can raise (e.g. fs error from
            # set_upstream_ref_fn, or unexpected exception from reverify_fn).
            # An unguarded exception here would escape run_train as a traceback
            # instead of returning merge_halted, leaving already-merged upstreams
            # with no record.  Mirror the same try/except pattern used for the
            # merge call below (forward-only: already-merged upstreams stay merged).
            try:
                for _edge_m in _upstream_edges_m:
                    _upstream_merged_sha = merged_shas.get(_edge_m.upstream.node_id)
                    if _upstream_merged_sha is None:
                        # Defensive: topo-order ensures upstream is processed first.
                        raise RuntimeError(
                            f"upstream '{_edge_m.upstream.node_id}' is not yet merged "
                            f"— cannot re-verify downstream '{_nid_m}'; "
                            f"check topo order and that the upstream merge succeeded"
                        )
                    set_upstream_ref_fn(_ws_m, _edge_m.channel, _upstream_merged_sha)

                # Re-verify the downstream against the merged upstream contracts.
                # Failure means the downstream was only green against the draft ref.
                _reverify_ok = reverify_fn(_ws_m, _ws_m / _node_m.roadmap, run_mode)
            except Exception as _inject_exc_m:
                # Inject or reverify raised — record blocked + return merge_halted
                # so the status-dict contract is preserved and no traceback escapes.
                append_record(
                    ledger_path,
                    LedgerRecord(
                        node_id=_nid_m,
                        status="blocked",
                        branch=completed_nodes.get(_nid_m, {}).get("branch"),
                    ),
                )
                return {
                    "status": "merge_halted",
                    "node_id": _nid_m,
                    "reason": "reverify_failed",
                    "detail": str(_inject_exc_m),
                }

            if not _reverify_ok:
                append_record(
                    ledger_path,
                    LedgerRecord(
                        node_id=_nid_m,
                        status="blocked",
                        branch=completed_nodes.get(_nid_m, {}).get("branch"),
                    ),
                )
                # Forward-only: DO NOT revert the already-merged upstream nodes.
                # Use expand/contract upstream contracts to prevent this situation.
                return {
                    "status": "merge_halted",
                    "node_id": _nid_m,
                    "reason": "downstream_reverify_failed",
                    "detail": (
                        f"node '{_nid_m}' failed re-verification against upstream "
                        f"merged SHA(s). Upstream nodes remain merged (forward-only). "
                        f"Recommendation: use expand/contract upstream contracts so "
                        f"sequential merges are safe even when a downstream fails."
                    ),
                }

        # Merge the PR.  Wrap the call: a real gh pr merge failure (branch
        # protection, conflict, required checks) raises CalledProcessError
        # from the live default (subprocess check=True).  Record blocked +
        # return merge_halted so no uncaught exception escapes run_train and
        # already-merged upstream nodes remain recorded (forward-only).
        _pr_branch_m = completed_nodes[_nid_m]["branch"]
        try:
            _merged_sha_m = merge_pr_fn(_ws_m, _pr_branch_m)
        except Exception as _merge_exc_m:
            append_record(
                ledger_path,
                LedgerRecord(
                    node_id=_nid_m,
                    status="blocked",
                    branch=_pr_branch_m,
                ),
            )
            return {
                "status": "merge_halted",
                "node_id": _nid_m,
                "reason": "merge_failed",
                "detail": str(_merge_exc_m),
            }
        merged_shas[_nid_m] = _merged_sha_m

        # Record the merge.  Carry branch/pr_url/head_sha forward from
        # completed_nodes so the merged record is self-sufficient for P4 resume
        # (last-wins overwrites the pr_open record; the resumed run reads branch
        # and head_sha from this merged record to inject downstream nodes).
        _node_info_m = completed_nodes[_nid_m]
        append_record(
            ledger_path,
            LedgerRecord(
                node_id=_nid_m,
                status="merged",
                branch=_node_info_m.get("branch"),
                pr_url=_node_info_m.get("pr_url"),
                head_sha=_node_info_m.get("head_sha"),   # draft SHA for downstream injection
                upstream_merge_sha=_merged_sha_m,         # actual merge-commit SHA (P4)
                merge_order=_i_m,
            ),
        )

    return {
        "status": "merged",
        "nodes": {
            _nid_out: {
                "branch": completed_nodes[_nid_out]["branch"],
                "merged_sha": _sha_out,
            }
            for _nid_out, _sha_out in merged_shas.items()
            if _nid_out in completed_nodes  # exclude _train_review_ synthetic node
        },
    }
