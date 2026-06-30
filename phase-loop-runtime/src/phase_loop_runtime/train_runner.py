"""Cross-repo release-train coordinator (P3).

Serial draft-PR execution: topo-sort the train, preflight ALL repos, then per
node (in topo order): inject upstream draft ref via set_upstream_ref → invoke
the unchanged per-repo run_loop → publish a draft PR → append to ledger.

Safety invariants (enforced structurally, asserted in tests):
  1. **Zero-PRs-on-preflight-failure**: preflight runs on ALL repos before the
     per-node loop is entered.  If any check fails, ``run_train`` returns
     immediately with ``status="preflight_failed"`` and zero publish calls.
  2. **Draft-only**: every ``publish_from_worktree`` call uses ``draft=True``.
     P3 never merges.  The merge seam (P4) is absent here.
  3. **Train state off .phase-loop/**: ledger_path is caller-supplied and must
     pass ``_assert_not_phase_loop``; the coordinator never touches any repo's
     ``.phase-loop/`` directory.
  4. **Resumable**: a partial run leaves prior nodes' draft PRs open and the
     failed node ``blocked`` in the ledger.  Re-running re-reads both the
     ledger and live PR state; completed nodes are skipped.

All git/gh/run_loop/publish boundaries are injectable seams so the module is
fully testable without live network access.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

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
# Live PR state seam


def _live_pr_is_open(workspace: Path, branch: str) -> bool:
    """Return True if ``branch`` has an open PR on the remote.

    Stubbable seam for tests.  Uses ``_gh_pr_metadata`` from ``git_topology``
    (already reused by the P1 publish primitive for the same reason).
    """
    from .git_topology import _gh_pr_metadata

    meta = _gh_pr_metadata(workspace, branch)
    return bool(meta.get("pr_url"))


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
    _preflight_fn: Optional[Callable] = None,
) -> Dict:
    """Coordinate a cross-repo release train: preflight, topo-sort, draft-PR open.

    Parameters
    ----------
    roadmap:
        Parsed ``TrainRoadmap`` (P2 schema).
    ledger_path:
        Path to the coordinator-side ledger file.  Must not be inside any
        repo's ``.phase-loop/`` (enforced by ``append_record``).
    run_mode:
        ``"autonomous"`` or ``"governed"``.  Passed unchanged to each
        per-repo ``run_loop`` call.
    resolve_workspace:
        Maps a ``TrainNode`` to its workspace ``Path`` on disk.
    resolve_owned_paths:
        Maps a ``TrainNode`` to the list of paths the publish primitive
        should stage.  Defaults to ``[node.roadmap]`` (the roadmap file).
        Override for real workspaces where the AI agent changed more files.
    _run_loop, _publish, _set_upstream_ref_fn, _pr_is_open, _preflight_fn:
        Injectable seams for testing.  Each defaults to the corresponding
        live implementation.

    Returns
    -------
    dict
        ``{"status": "completed", "nodes": {node_id: {branch, head_sha, pr_url}}}``
        on success;
        ``{"status": "blocked", "node_id": ..., "detail": ...}`` if a node
        fails (prior nodes' draft PRs remain open; train is resumable);
        ``{"status": "preflight_failed", "errors": [...]}`` if any preflight
        check fails (zero PRs opened).
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
    preflight_fn = _preflight_fn if _preflight_fn is not None else _default_preflight

    if resolve_owned_paths is None:
        # Default: stage only the roadmap file; callers override for real use
        resolve_owned_paths = lambda n: [n.roadmap]

    # --- Step 1: Topo-sort (raises ValueError on cycle) -------------------
    topo_order = roadmap.topo_order()

    # --- Step 2: Train-level preflight — ALL repos, BEFORE any PR ---------
    # This is the structural guarantee that preflight failure → zero PRs:
    # we return immediately here, before the per-node loop is entered.
    preflight_errors = preflight_fn(topo_order, resolve_workspace)
    if preflight_errors:
        return {
            "status": "preflight_failed",
            "errors": preflight_errors,
        }

    # --- Step 3: Re-read ledger + live PR state (resume support) ----------
    ledger_state = read_ledger(ledger_path)
    # completed_nodes: node_id → {branch, head_sha, pr_url}
    # These are the upstream refs the coordinator can inject into downstream
    # nodes via set_upstream_ref (IF-0-P2-2).
    completed_nodes: Dict[str, Dict] = {}

    for node in topo_order:
        nid = node.node_id
        rec = ledger_state.get(nid)
        if rec and rec.status == "pr_open" and rec.branch and rec.pr_url:
            workspace = resolve_workspace(node)
            if pr_is_open_fn(workspace, rec.branch):
                # Re-read the live head_sha stored as upstream_merge_sha in the
                # ledger (P3 reuses this field for the draft branch head SHA).
                completed_nodes[nid] = {
                    "branch": rec.branch,
                    "head_sha": rec.upstream_merge_sha,
                    "pr_url": rec.pr_url,
                }

    # --- Step 4: Execute in topo order ------------------------------------
    for i, node in enumerate(topo_order):
        nid = node.node_id

        # Resume: skip nodes already completed (pr_open + live PR confirmed)
        if nid in completed_nodes:
            continue

        workspace = resolve_workspace(node)
        upstream_edges = roadmap.edges_for_downstream(node)

        # (i) Inject upstream draft refs via set_upstream_ref (IF-0-P2-2)
        #     This is how the unchanged run_loop can see the upstream change-
        #     in-flight.  Must happen BEFORE run_loop is called.
        for edge in upstream_edges:
            upstream_result = completed_nodes.get(edge.upstream.node_id)
            if upstream_result:
                # Use head_sha (precise content pin) if available, else branch
                ref = upstream_result.get("head_sha") or upstream_result.get("branch")
                if ref:
                    set_upstream_ref_fn(workspace, edge.channel, ref)

        # Mark as running in ledger (durable breadcrumb for diagnostics)
        append_record(ledger_path, LedgerRecord(node_id=nid, status="running"))

        # (ii) Invoke the unchanged per-repo run_loop
        run_loop_fn(workspace, workspace / node.roadmap, run_mode=run_mode)

        # (iii) Publish as draft PR via the P1 runtime primitive.
        #       draft=True is structural — P3 never merges.
        owned_paths = list(resolve_owned_paths(node))
        pr_body = _build_pr_body(node, topo_order, completed_nodes, upstream_edges)
        publish_result = publish_fn(
            workspace,
            owned_paths,
            draft=True,  # P3 invariant: draft-only, never merge
            pr_body=pr_body,
        )

        if publish_result.get("status") != "published":
            # Node blocked — record in ledger, halt loop.
            # Prior nodes' draft PRs remain open.  The train is resumable:
            # re-running will skip completed_nodes and retry from here.
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

        # Record success
        branch = publish_result["branch"]
        head_sha = publish_result["head_sha"]
        pr_url = publish_result["pr_url"]

        completed_nodes[nid] = {
            "branch": branch,
            "head_sha": head_sha,
            "pr_url": pr_url,
        }

        append_record(
            ledger_path,
            LedgerRecord(
                node_id=nid,
                status="pr_open",
                branch=branch,
                pr_url=pr_url,
                # Store the draft branch head_sha here so downstream nodes
                # (in this run or a resumed run) can inject it via
                # set_upstream_ref (IF-0-P2-2).
                upstream_merge_sha=head_sha,
                merge_order=i,
            ),
        )

    return {"status": "completed", "nodes": completed_nodes}
