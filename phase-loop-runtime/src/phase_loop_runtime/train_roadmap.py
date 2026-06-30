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
    raw = raw.strip()
    if " / " in raw:
        repo, roadmap = raw.split(" / ", 1)
    elif "/" in raw:
        repo, roadmap = raw.split("/", 1)
    else:
        raise ValueError(f"invalid node identifier (expected '<repo> / <roadmap>'): {raw!r}")
    return TrainNode(repo=repo.strip(), roadmap=roadmap.strip())


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
        upstream = node_by_id.get(dep_id) or node_by_str.get(dep_id)
        if upstream is None:
            # Try normalising " / " vs "/"
            alt = dep_id.replace(" / ", "/")
            upstream = node_by_id.get(alt)
        if upstream is None:
            raise ValueError(
                f"node '{downstream.node_id}' depends on unknown node '{dep_id}'"
            )
        channel = parse_channel_line(channel_raw)
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

    # (T-C) Every edge must carry a non-none channel
    for edge in roadmap.edges:
        if edge.channel.kind == "none":
            errors.append(
                f"(T-C) edge '{edge.downstream.node_id}' → '{edge.upstream.node_id}' "
                f"has no consumption-channel descriptor; a channel (pin/submodule/workspace) "
                f"is required for every cross-repo dependency"
            )

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
