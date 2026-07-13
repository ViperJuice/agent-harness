"""Cross-repo release-train roadmap schema, parser, and gate identity.

IF-0-P2-1 contract: train roadmap nodes = ``(repo, roadmap)``, edges =
``Depends on: <upstream node>`` plus a per-edge consumption-channel
descriptor, and a cross-repo gate identity pinned to an upstream merge SHA.

The gate identity uses the ``XGATE:`` namespace — **not** the in-repo
``IF-0-<alias>-<n>`` token (which is hardwired to a phase alias and has no
cross-repo consume side).  Any attempt to reuse an ``IF-0-...`` token as a
node identifier or dependency reference is rejected by ``validate_train``.

This is a **new parser**, separate from the ``### Phase N (ALIAS)`` regex in
``roadmap_lint``.  The topo-sort / cycle-check algorithm is extracted from
``roadmap_lint.check_dag_acyclic`` and reused unchanged.

Train roadmap Markdown format (minimal)::

    # Release Train: <title>

    ## Nodes

    ### Node: <repo> / <roadmap-path>

    **Depends on:** (none)
    **Channel:** (none)

    ### Node: <repo2> / <roadmap2-path>

    **Depends on:** <repo> / <roadmap-path>
    **Channel:** submodule path=vendor/repo

Zero external deps (stdlib only).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .cross_repo_channel import ChannelDescriptor, parse_channel_line


# ---------------------------------------------------------------------------
# Data model — IF-0-P2-1

@dataclass(frozen=True)
class TrainNode:
    """Identity of one node in the release train: a ``(repo, roadmap)`` pair."""

    repo: str
    roadmap: str

    @property
    def node_id(self) -> str:
        """Canonical string identifier: ``<repo>/<roadmap>``."""
        return f"{self.repo}/{self.roadmap}"

    def __str__(self) -> str:
        return f"{self.repo} / {self.roadmap}"


@dataclass
class TrainEdge:
    """A directed dependency edge (upstream → downstream) with a channel.

    This is the static declaration.  ``gate_id(sha)`` produces the runtime
    cross-repo gate identity once the upstream merges.
    """

    upstream: TrainNode
    downstream: TrainNode
    channel: ChannelDescriptor

    def gate_id(self, merge_sha: str) -> str:
        """Return the cross-repo gate identity token for a given upstream merge SHA.

        The ``XGATE:`` namespace is distinct from ``IF-0-<alias>-<n>`` — it
        carries a full node reference and is pinned to a content-addressed
        merge commit rather than a phase alias.
        """
        return f"XGATE:{self.upstream.node_id}@{merge_sha}"


@dataclass
class TrainRoadmap:
    """Parsed cross-repo release-train roadmap (IF-0-P2-1)."""

    title: str
    nodes: List[TrainNode] = field(default_factory=list)
    edges: List[TrainEdge] = field(default_factory=list)

    def topo_order(self) -> List[TrainNode]:
        """Return nodes in a valid serial execution order (Kahn's algorithm).

        Raises ``ValueError`` if the train is not serially orderable (cycle).
        """
        order = _topo_sort(self.nodes, self.edges)
        return order

    def node_by_id(self, node_id: str) -> Optional[TrainNode]:
        for n in self.nodes:
            if n.node_id == node_id:
                return n
        return None

    def edges_for_downstream(self, node: TrainNode) -> List[TrainEdge]:
        return [e for e in self.edges if e.downstream == node]


# ---------------------------------------------------------------------------
# Parser

# Regex for the train-roadmap headings (new format — NOT the phase-plan regex)
_TITLE_RE = re.compile(r"^#\s+Release Train:\s*(.+?)\s*$", re.MULTILINE)
_NODE_HEADING_RE = re.compile(r"^###\s+Node:\s*(.+?)\s*$", re.MULTILINE)
_FIELD_RE = re.compile(
    r"^\*\*(?P<label>Depends on|Channel):\*\*\s*(?P<value>.+)$",
    re.MULTILINE | re.IGNORECASE,
)

# Reject in-repo IF-gate tokens used as node / dep references
_IF_GATE_TOKEN_RE = re.compile(r"\bIF-0-[A-Za-z0-9]+-\d+\b")

_NONE_VALUES = frozenset({"(none)", "none", ""})


def _parse_node_id(raw: str) -> TrainNode:
    """Parse ``<repo> / <roadmap>`` or ``<repo>/<roadmap>`` into a TrainNode."""
    original = raw.strip()
    if " / " in original:
        repo, roadmap = original.split(" / ", 1)
    elif "/" in original:
        repo, roadmap = original.split("/", 1)
    else:
        raise ValueError(f"invalid node identifier (expected '<repo> / <roadmap>'): {original!r}")
    repo, roadmap = repo.strip(), roadmap.strip()
    # Both components are load-bearing: `repo` selects the workspace and
    # `roadmap` is joined onto it as the plan path. An empty half parses into a
    # degenerate node that only fails later at child-launch (workspace / '' =
    # the workspace dir itself). Reject it at parse time, naming the heading.
    if not repo or not roadmap:
        raise ValueError(
            f"invalid node heading '### Node: {original}': both a repo and a "
            f"plan-path are required in '<repo> / <plan-path>' form "
            f"(got repo={repo!r}, plan-path={roadmap!r})"
        )
    return TrainNode(repo=repo, roadmap=roadmap)


def _extract_node_bodies(text: str) -> List[Tuple[str, str]]:
    """Return list of (raw_node_id, body_text) for each ``### Node:`` block."""
    matches = list(_NODE_HEADING_RE.finditer(text))
    result = []
    for i, m in enumerate(matches):
        raw_id = m.group(1)
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        result.append((raw_id, text[body_start:body_end]))
    return result


def parse_train_roadmap(text: str) -> TrainRoadmap:
    """Parse train-roadmap markdown text into a :class:`TrainRoadmap`.

    Raises :exc:`ValueError` on malformed input (not a validation function —
    use :func:`validate_train` for full semantic checks).
    """
    title_m = _TITLE_RE.search(text)
    if not title_m:
        raise ValueError("missing '# Release Train: <title>' heading")
    title = title_m.group(1)

    node_bodies = _extract_node_bodies(text)
    if not node_bodies:
        raise ValueError("no '### Node: <repo> / <roadmap>' blocks found")

    nodes: List[TrainNode] = []
    raw_edges: List[Tuple[TrainNode, str, str]] = []  # (downstream, dep_id, channel_raw)

    for raw_id, body in node_bodies:
        node = _parse_node_id(raw_id)
        nodes.append(node)

        fields: Dict[str, str] = {}
        for m in _FIELD_RE.finditer(body):
            fields[m.group("label").lower()] = m.group("value").strip()

        dep_raw = fields.get("depends on", "(none)")
        channel_raw = fields.get("channel", "(none)")

        if dep_raw.strip().lower() not in _NONE_VALUES:
            raw_edges.append((node, dep_raw.strip(), channel_raw.strip()))

    # Resolve edges
    node_by_id: Dict[str, TrainNode] = {n.node_id: n for n in nodes}
    # Also index by "repo / roadmap" string representation for lookup
    node_by_str: Dict[str, TrainNode] = {str(n): n for n in nodes}

    edges: List[TrainEdge] = []
    for downstream, dep_id, channel_raw in raw_edges:
        # Distinct check: reject in-repo IF-gate tokens before the node lookup so
        # "invalid in-repo token" and "missing node" produce distinguishable errors.
        if _IF_GATE_TOKEN_RE.search(dep_id):
            raise ValueError(
                f"node '{downstream.node_id}' references in-repo IF-gate token "
                f"'{dep_id}' as a dependency; cross-repo edges use '<repo>/<roadmap>' "
                f"node identifiers (XGATE: namespace), not IF-0-... tokens"
            )
        upstream = node_by_id.get(dep_id) or node_by_str.get(dep_id)
        if upstream is None:
            # Try normalising " / " vs "/"
            alt = dep_id.replace(" / ", "/")
            upstream = node_by_id.get(alt)
        if upstream is None:
            raise ValueError(
                f"node '{downstream.node_id}' depends on unknown node '{dep_id}'"
            )
        # Name the offending node when the channel descriptor is malformed.
        # parse_channel_line raises a bare "pin channel requires a 'file' param"
        # style error; without this wrapper a train author with N nodes cannot
        # tell WHICH node's **Channel:** line is broken (the opaque
        # roadmap-format-handling symptom of agent-harness#60).
        try:
            channel = parse_channel_line(channel_raw)
        except ValueError as exc:
            raise ValueError(
                f"node '{downstream.node_id}' has a malformed **Channel:** "
                f"descriptor ({channel_raw!r}): {exc}"
            ) from exc
        edges.append(TrainEdge(upstream=upstream, downstream=downstream, channel=channel))

    return TrainRoadmap(title=title, nodes=nodes, edges=edges)


def load_train_roadmap(path: Path | str) -> TrainRoadmap:
    """Load and parse a train roadmap from disk."""
    return parse_train_roadmap(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Validation — extends roadmap_lint's topo algorithm with train-specific rules

def validate_train(roadmap: TrainRoadmap) -> List[str]:
    """Return human-readable validation issues for a parsed :class:`TrainRoadmap`.

    Checks:
    (T-A) No in-repo ``IF-0-<alias>-<n>`` tokens used as node IDs or deps.
    (T-B) Every edge's upstream node exists in the node list.
    (T-C) Every edge with a dependency carries a non-none channel descriptor.
    (T-D) The cross-repo DAG is acyclic (topo-sort succeeds).
    (T-E) Every dependency edge uses a supported channel kind: ``pin`` (with
          ``file=``) or ``submodule``.  ``workspace`` channels are not
          implemented for real consumption and are rejected here so that a
          train with a workspace edge fails the preflight gate — opening zero
          PRs — rather than running until injection fails mid-train.
    (T-F) Node identifiers are unique within the train.  A duplicated
          ``### Node:`` block would otherwise collapse in the topo-sort
          indegree map and mis-surface as a spurious (T-D) cycle.
    """
    errors: List[str] = []

    # (T-A) Reject in-repo IF-gate tokens
    for node in roadmap.nodes:
        if _IF_GATE_TOKEN_RE.search(node.node_id):
            errors.append(
                f"(T-A) node id '{node.node_id}' contains an in-repo IF-gate token "
                f"(IF-0-...) — use '<repo>/<roadmap>' node identifiers instead"
            )
    for edge in roadmap.edges:
        if _IF_GATE_TOKEN_RE.search(edge.upstream.node_id):
            errors.append(
                f"(T-A) edge depends on '{edge.upstream.node_id}' which contains an "
                f"in-repo IF-gate token — use node identifiers instead"
            )

    # (T-B) Every upstream in an edge is a declared node
    declared = {n.node_id for n in roadmap.nodes}
    for edge in roadmap.edges:
        if edge.upstream.node_id not in declared:
            errors.append(
                f"(T-B) edge '{edge.downstream.node_id}' depends on "
                f"'{edge.upstream.node_id}' which is not a declared node"
            )

    # (T-C) Every dependency edge must declare a channel — a bare `(none)` is
    # ambiguous (forgotten channel?). #47: an intentional ORDER-ONLY dependency
    # (B must merge after A but does not consume A's artifact) is declared
    # explicitly with `Channel: order-only`, which is NOT `none` and passes here.
    for edge in roadmap.edges:
        if edge.channel.kind == "none":
            errors.append(
                f"(T-C) edge '{edge.downstream.node_id}' → '{edge.upstream.node_id}' "
                f"has no consumption-channel descriptor; declare a channel "
                f"(pin/submodule) if the downstream consumes the upstream, or "
                f"'order-only' for a merge-order-only (freeze) dependency"
            )

    # (T-E) Supported channel kinds: pin (with file=), submodule, or order-only
    # (order-only carries no injection — it enforces merge order only, #47).
    _SUPPORTED_KINDS = frozenset({"pin", "submodule", "order-only"})
    for edge in roadmap.edges:
        kind = edge.channel.kind
        if kind == "none":
            continue  # T-C already reported this
        if kind not in _SUPPORTED_KINDS:
            errors.append(
                f"(T-E) edge '{edge.downstream.node_id}' → '{edge.upstream.node_id}' "
                f"uses unsupported channel kind '{kind}'; only 'pin' (with file=), "
                f"'submodule', and 'order-only' are supported. "
                f"'workspace' channels are rejected at preflight to prevent hollow injection."
            )

    # (T-F) Node identifiers must be unique within the train (authoring guide:
    # "Node identifiers must be unique within the train"). A duplicated
    # `### Node:` block otherwise collapses in the topo-sort indegree map and
    # surfaces as a misleading "(T-D) cycle detected ... unresolved nodes: "
    # with an EMPTY node list. Report it as its own coded, node-named error;
    # the `if not errors` guard on the T-D block below then suppresses the
    # spurious cycle. A duplicate is also a dispatch/ledger-integrity hazard:
    # two nodes with the same id collide on the coordinator ledger key.
    seen_ids: set = set()
    reported_dups: set = set()
    for node in roadmap.nodes:
        nid = node.node_id
        if nid in seen_ids and nid not in reported_dups:
            errors.append(
                f"(T-F) node id '{nid}' is declared more than once; each "
                f"'### Node: <repo> / <plan-path>' block must be unique within "
                f"the train (authoring guide: node identifiers must be unique)"
            )
            reported_dups.add(nid)
        seen_ids.add(nid)

    # (T-D) Acyclic + serially orderable
    if not errors:
        try:
            _topo_sort(roadmap.nodes, roadmap.edges)
        except ValueError as exc:
            errors.append(f"(T-D) {exc}")

    return errors


def validate_train_loud(roadmap: TrainRoadmap) -> None:
    """Raise :exc:`ValueError` if the train roadmap has any validation issues."""
    issues = validate_train(roadmap)
    if issues:
        msg = "\n".join(f"  • {e}" for e in issues)
        raise ValueError(f"train roadmap has {len(issues)} issue(s):\n{msg}")


# ---------------------------------------------------------------------------
# Topo-sort (reused from roadmap_lint.check_dag_acyclic — Kahn's algorithm)

def _topo_sort(nodes: List[TrainNode], edges: List[TrainEdge]) -> List[TrainNode]:
    """Return nodes in topological order.  Raises :exc:`ValueError` on a cycle."""
    adj: Dict[str, List[str]] = {n.node_id: [] for n in nodes}
    indeg: Dict[str, int] = {n.node_id: 0 for n in nodes}
    for edge in edges:
        up = edge.upstream.node_id
        dn = edge.downstream.node_id
        if up in adj and dn in indeg:
            adj[up].append(dn)
            indeg[dn] += 1

    queue = [nid for nid, d in indeg.items() if d == 0]
    order: List[str] = []
    while queue:
        cur = queue.pop(0)
        order.append(cur)
        for nb in adj[cur]:
            indeg[nb] -= 1
            if indeg[nb] == 0:
                queue.append(nb)

    if len(order) != len(nodes):
        unresolved = [nid for nid, d in indeg.items() if d > 0]
        raise ValueError(
            f"cycle detected in train dependency DAG; unresolved nodes: {', '.join(unresolved)}"
        )

    node_map = {n.node_id: n for n in nodes}
    return [node_map[nid] for nid in order]
