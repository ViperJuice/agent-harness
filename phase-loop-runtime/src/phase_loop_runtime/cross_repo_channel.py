"""Cross-repo consumption-channel descriptor and injection primitive.

IF-0-P2-2 contract: every cross-repo dependency edge declares HOW the
downstream workspace references the upstream — via a package/version pin, a
``git submodule``, or a workspace/path override.  **NOT a git rebase**: two
repos have unrelated git histories.

The load-bearing primitive is :func:`set_upstream_ref`, which the coordinator
calls (with the upstream draft branch ref in P3 and the upstream merged SHA in
P4) **before** invoking the unchanged per-repo ``run_loop`` in the downstream
workspace.  This is how an unchanged ``run_loop`` can consume the upstream at
all.

Channel kinds (closed set — no plugin system):

``pin``
    A package-manager version pin (e.g. ``requirements.txt``, ``package.json``
    dependency entry).  Params: ``name`` (package name), ``version`` (version
    string or ref; may be a SHA for VCS installs).

``submodule``
    A ``git submodule``.  Params: ``path`` (submodule path relative to the
    downstream workspace root).

``workspace``
    A workspace/path override (e.g. a ``[tool.uv.sources]`` workspace entry or
    a Cargo workspace path dep).  Params: ``path`` (the override path).

``none``
    No upstream dependency; declared explicitly for root nodes.

Zero external deps (stdlib only).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Literal, Optional

# ---------------------------------------------------------------------------
# Channel descriptor — IF-0-P2-2

ChannelKind = Literal["pin", "submodule", "workspace", "none"]

VALID_CHANNEL_KINDS: frozenset[str] = frozenset({"pin", "submodule", "workspace", "none"})

_NONE_VALUES = frozenset({"(none)", "none", ""})


@dataclass(frozen=True)
class ChannelDescriptor:
    """Per-edge consumption-channel descriptor (IF-0-P2-2).

    Attributes:
        kind: One of ``pin``, ``submodule``, ``workspace``, or ``none``.
        params: Kind-specific parameters (see module docstring).
    """

    kind: ChannelKind
    params: Dict[str, str] = field(default_factory=dict, compare=True, hash=False)

    def __hash__(self) -> int:
        return hash((self.kind, tuple(sorted(self.params.items()))))

    def __repr__(self) -> str:  # pragma: no cover
        if self.kind == "none":
            return "ChannelDescriptor(none)"
        params_str = " ".join(f"{k}={v}" for k, v in sorted(self.params.items()))
        return f"ChannelDescriptor({self.kind} {params_str})"


# ---------------------------------------------------------------------------
# Parser

def parse_channel_line(raw: str) -> ChannelDescriptor:
    """Parse a ``**Channel:**`` field value into a :class:`ChannelDescriptor`.

    Accepted forms::

        (none)                              → ChannelDescriptor(kind="none")
        submodule path=vendor/consiliency-portal
        pin name=mylib version=1.2.3
        workspace path=../mylib

    Raises :exc:`ValueError` on unrecognised channel kind.
    """
    stripped = raw.strip()
    if stripped.lower() in _NONE_VALUES:
        return ChannelDescriptor(kind="none", params={})

    parts = stripped.split()
    kind = parts[0].lower()
    if kind not in VALID_CHANNEL_KINDS or kind == "none":
        raise ValueError(
            f"unknown channel kind '{kind}'; expected one of: pin, submodule, workspace"
        )

    params: Dict[str, str] = {}
    for token in parts[1:]:
        if "=" in token:
            k, _, v = token.partition("=")
            params[k.strip()] = v.strip()
        else:
            raise ValueError(
                f"channel param '{token}' has no '='; expected 'key=value' form"
            )

    _validate_params(kind, params, raw)  # type: ignore[arg-type]
    return ChannelDescriptor(kind=kind, params=params)  # type: ignore[arg-type]


def _validate_params(kind: ChannelKind, params: Dict[str, str], raw: str) -> None:
    if kind == "pin":
        missing = [k for k in ("name", "version") if k not in params]
        if missing:
            raise ValueError(
                f"pin channel requires 'name' and 'version' params; "
                f"missing: {', '.join(missing)} in: {raw!r}"
            )
    elif kind in ("submodule", "workspace"):
        if "path" not in params:
            raise ValueError(
                f"{kind} channel requires a 'path' param; got: {raw!r}"
            )


# ---------------------------------------------------------------------------
# Injection primitive — IF-0-P2-2

# The git/fs boundary is injectable for tests (stub it; never call real git in
# unit tests).  The protocol is a callable:
#   executor(workspace: Path, kind: ChannelKind, params: dict, ref: str) -> None

ChannelExecutor = Callable[[Path, str, Dict[str, str], str], None]


def _default_executor(
    workspace: Path,
    kind: str,
    params: Dict[str, str],
    ref: str,
) -> None:
    """Default live executor — runs real git/fs operations."""
    if kind == "submodule":
        submodule_path = params["path"]
        # Dereference the submodule to the given ref.
        subprocess.run(
            ["git", "fetch", "origin"],
            cwd=workspace / submodule_path,
            check=True,
        )
        subprocess.run(
            ["git", "checkout", ref],
            cwd=workspace / submodule_path,
            check=True,
        )
    elif kind == "pin":
        # Re-resolve a VCS/package pin.  For VCS installs (pip editable /
        # git+https) the version field carries the ref.  The coordinator is
        # responsible for invoking the package manager after set_upstream_ref.
        # Here we write the new version into a sentinel file that the package
        # manager reads, OR rely on the coordinator's post-injection install
        # step.  For MVP: write the ref to .phase-loop-upstream-pin/<name>
        # for the executor skill to pick up.
        pin_dir = workspace / ".phase-loop-upstream-pin"
        pin_dir.mkdir(parents=True, exist_ok=True)
        (pin_dir / params["name"]).write_text(ref, encoding="utf-8")
    elif kind == "workspace":
        # Write the resolved ref to a workspace-override sentinel file.
        override_dir = workspace / ".phase-loop-workspace-ref"
        override_dir.mkdir(parents=True, exist_ok=True)
        (override_dir / "ref").write_text(ref, encoding="utf-8")
    else:
        raise ValueError(f"unknown channel kind for executor: {kind!r}")


def set_upstream_ref(
    workspace: Path,
    channel: ChannelDescriptor,
    ref: str,
    *,
    _executor: Optional[ChannelExecutor] = None,
) -> None:
    """Re-resolve the downstream workspace's dependency channel to ``ref``.

    This is the **load-bearing injection primitive** (IF-0-P2-2): the
    coordinator calls this *before* invoking the unchanged per-repo
    ``run_loop`` to point the downstream's consumption channel at a specific
    upstream ref — the draft branch ``head_sha`` in P3 and the upstream
    **merge SHA** in P4.

    Args:
        workspace: Absolute path to the downstream repo's worktree.
        channel: The :class:`ChannelDescriptor` for this edge.
        ref: The upstream ref or SHA to pin the channel to.
        _executor: Optional injectable executor (for testing).  Defaults to
            the live git/fs executor.

    Raises:
        ValueError: If the channel kind is ``none`` (root nodes have no
            channel to re-resolve) or unknown.
    """
    if channel.kind == "none":
        raise ValueError(
            "set_upstream_ref called on a 'none' channel — root nodes have no "
            "upstream dependency to resolve"
        )
    executor = _executor if _executor is not None else _default_executor
    executor(workspace, channel.kind, dict(channel.params), ref)
