"""Cross-repo consumption-channel descriptor and injection primitive.

IF-0-P2-2 contract: every cross-repo dependency edge declares HOW the
downstream workspace references the upstream — via a manifest-file version pin,
a ``git submodule``, or a workspace/path override.  **NOT a git rebase**: two
repos have unrelated git histories.

The load-bearing primitive is :func:`set_upstream_ref`, which the coordinator
calls (with the upstream draft branch ref in P3 and the upstream merged SHA in
P4) **before** invoking the unchanged per-repo ``run_loop`` in the downstream
workspace.  This is how an unchanged ``run_loop`` can consume the upstream at
all.

Channel kinds (closed set — no plugin system):

``pin``
    A manifest-file version pin: the executor rewrites a file the downstream
    build ACTUALLY reads (e.g. ``requirements.txt``, ``package.json``, a
    lockfile, a plain version file).

    Two forms:
      ``pin file=<path>``               — plain version/ref file: writes ``ref``
                                          as the sole file content.
      ``pin file=<path> key=<a.b.c>``  — JSON manifest: loads existing JSON,
                                          sets the nested dotted key to ``ref``,
                                          writes back with 2-space indent.

    Required param: ``file`` (repo-relative path of the file to rewrite).
    Optional param: ``key`` (dotted JSON key; absent → plain file).

``submodule``
    A ``git submodule``.  Params: ``path`` (submodule path relative to the
    downstream workspace root).

``workspace``
    A workspace/path override (e.g. a ``[tool.uv.sources]`` workspace entry or
    a Cargo workspace path dep).  Params: ``path`` (the override path).
    **Rejected at train validation (T-E)**: workspace injection is not
    implemented for real consumption and is rejected at preflight.

``none``
    No upstream dependency; declared explicitly for root nodes.

Zero external deps (stdlib only).
"""

from __future__ import annotations

import json
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

        (none)                                         → ChannelDescriptor(kind="none")
        submodule path=vendor/consiliency-portal
        pin file=manifest.json                         → plain version/ref file
        pin file=manifest.json key=deps.schema         → JSON manifest, dotted key
        workspace path=../mylib

    Raises :exc:`ValueError` on unrecognised channel kind or missing required params.
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
        if "file" not in params:
            raise ValueError(
                f"pin channel requires a 'file' param (the manifest file that gets "
                f"rewritten with the upstream ref); got: {raw!r}"
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


class UnsupportedChannelKind(ValueError):
    """Raised by the live executor when a channel kind is valid in the schema but
    not yet implemented for real consumption in this MVP.

    Using a hollow sentinel file (written but never read by the downstream
    build) would silently build the downstream against the absent upstream,
    corrupting the whole train.  We fail loud instead.
    """


def _default_executor(
    workspace: Path,
    kind: str,
    params: Dict[str, str],
    ref: str,
) -> None:
    """Default live executor — runs real git/fs operations.

    Channel support:
      ``submodule`` — git fetch + checkout; the downstream build ACTUALLY
          consumes the injected ref (submodule HEAD is updated).
      ``pin``       — rewrites the manifest file the downstream build reads:
          - ``file`` only → writes ``ref`` as the plain file content (+ newline).
          - ``file`` + ``key`` → loads existing JSON, sets the nested dotted key
            to ``ref``, writes back with 2-space indent + trailing newline.
          The downstream build reads the file directly, so injection is real.
      ``workspace`` — NOT IMPLEMENTED for real consumption; raises
          :exc:`UnsupportedChannelKind`.  Workspace edges are rejected at train
          validation (T-E) before any executor is reached.

    Stubbing the executor (``_executor=stub``) is the correct approach for
    tests that exercise workspace channel kinds (which remain unimplemented).
    """
    if kind == "submodule":
        submodule_path = params["path"]
        # Dereference the submodule to the given ref so the downstream build
        # actually runs against the injected upstream content.
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
        file_path = workspace / params["file"]
        key = params.get("key")
        if key:
            # JSON manifest: load existing file (or start with empty dict), set
            # the nested dotted key to ref, write back with consistent indent.
            data: Dict = json.loads(file_path.read_text(encoding="utf-8")) if file_path.exists() else {}
            keys = key.split(".")
            node: Dict = data
            for k in keys[:-1]:
                if k not in node or not isinstance(node[k], dict):
                    node[k] = {}
                node = node[k]
            node[keys[-1]] = ref
            file_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        else:
            # Plain version/ref file: write ref as the sole content.
            file_path.write_text(ref + "\n", encoding="utf-8")
    elif kind == "workspace":
        raise UnsupportedChannelKind(
            f"'workspace' channel injection is not implemented for real consumption "
            f"(path={params.get('path')!r}). Workspace edges are rejected at train "
            f"validation (T-E) before reaching the executor; a workspace channel in a "
            f"live train means the train roadmap was not validated — check preflight."
        )
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
