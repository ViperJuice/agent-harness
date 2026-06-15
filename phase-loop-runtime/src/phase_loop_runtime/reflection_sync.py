"""Multi-machine reflection collection (Slice 1).

Reflections are written locally by skills at close-out (see
``claude-config/shared/runtime-state.md``) and never leave the machine — they
are gitignored and live in ``~/.<harness>/skills/<skill>/reflections/``. This
module provides the *collection half*: redact each reflection (so it is safe to
pool across machines, including off-tailnet collaborators) and copy it under a
machine-namespaced path into a shared store directory.

Two pure, testable building blocks:

* :func:`machine_id` — a stable, non-secret per-machine identifier.
* :func:`redact_reflection_text` — deterministically strips repo/branch/commit/
  path identity from a reflection so the pooled copy is repo-agnostic.

and the orchestrator :func:`export_reflections`, invoked by
``scripts/reflections-sync``. The git transport and run-id dedup ledger are
intentionally deferred to Slice 2 (a ``phase-loop reflections sync`` subcommand).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import socket
import sys
import uuid
from pathlib import Path

from .skill_paths import resolve_skill_bundle_root

DEFAULT_HARNESSES: tuple[str, ...] = ("claude", "codex", "gemini", "opencode")

# Repo-local (source-controlled) reflection roots per harness, relative to repo.
_REPO_LOCAL_ROOTS: dict[str, tuple[str, ...]] = {
    "claude": ("claude-config/claude-skills", "claude-config/skills"),
    "codex": ("codex-config/skills",),
    "gemini": ("gemini-config/skills",),
    "opencode": ("opencode-config/skills",),
}


# ---------------------------------------------------------------------------
# Machine identity
# ---------------------------------------------------------------------------

def _slug(value: str) -> str:
    out = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    out = re.sub(r"-{2,}", "-", out).strip("-")
    return out or "unknown"


def _machine_id_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "dotfiles" / "machine-id"


def machine_id() -> str:
    """Return a stable, non-secret identifier for this machine.

    Persisted at ``$XDG_CONFIG_HOME/dotfiles/machine-id`` (default
    ``~/.config/dotfiles/machine-id``) so it survives across runs and is
    independent of tailnet membership — off-tailnet collaborators have no
    tailscale hostname. ``DOTFILES_MACHINE_ID`` overrides. Falls back to a
    hostname-derived hash if the id file cannot be written.
    """
    override = os.environ.get("DOTFILES_MACHINE_ID")
    if override and override.strip():
        return _slug(override)

    path = _machine_id_file()
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return _slug(existing)
    except OSError:
        pass

    new_id = uuid.uuid4().hex[:12]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_id + "\n", encoding="utf-8")
        return new_id
    except OSError:
        host = socket.gethostname() or "unknown-host"
        return "host-" + hashlib.sha256(host.encode("utf-8")).hexdigest()[:10]


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

# Structured Run-context / frontmatter fields whose values reveal identity.
# Maps a lowercased field name to the placeholder prefix used for its hash.
_SENSITIVE_FIELDS: dict[str, str] = {
    "repo": "repo",
    "repo root": "repo-root",
    "repo_root": "repo-root",
    "repo path": "repo-root",
    "branch": "branch",
    "branch slug": "branch",
    "branch_slug": "branch",
    "commit": "commit",
    "artifact": "artifact",
    "artifact path": "artifact",
}

# Matches "- Field: value", "Field: value", "field = value" (markdown or yaml).
_FIELD_RE = re.compile(
    r"^(?P<prefix>\s*(?:[-*]\s+)?(?P<name>[A-Za-z][A-Za-z _]*?)\s*[:=]\s*)(?P<value>.*?)\s*$"
)

# Already-redacted token (e.g. "<repo:ab12cd34ef>") or an explicit empty marker.
_REDACTED_RE = re.compile(r"^(<[^>]*>|none|n/?a|-)?$", re.IGNORECASE)

# Absolute project/worktree roots: collapse the private prefix *and* the project
# segment to "<repo>", keeping any instructive repo-relative tail.
_PROJECT_PATH_RE = re.compile(
    r"/(?:mnt/[^/\s]+/(?:code|worktrees)"
    r"|home/[^/\s]+/(?:projects|code|src|repos|dev|work)"
    r"|Users/[^/\s]+/(?:projects|code|src|repos|dev|work))"
    r"/[^/\s:'\"]+"
)

# Remaining home prefixes: drop the username, keep the generic tail (e.g.
# "/home/jenner/.claude/skills" -> "~/.claude/skills").
_HOME_PATH_RE = re.compile(r"/home/[^/\s]+|/Users/[^/\s]+|/root(?=/|\b)")


def _hash_token(value: str, prefix: str) -> str:
    digest = hashlib.sha256(value.strip().encode("utf-8")).hexdigest()[:10]
    return f"<{prefix}:{digest}>"


def _redact_paths(text: str) -> str:
    text = _PROJECT_PATH_RE.sub("<repo>", text)
    text = _HOME_PATH_RE.sub("~", text)
    return text


def redact_reflection_text(text: str, machine: str | None = None) -> str:
    """Return a repo-agnostic copy of a reflection, safe to pool across machines.

    Deterministic and idempotent. Two passes:

    1. **Structured fields** (Repo/Branch/Commit/Artifact/repo_root/...): replace
       the value with a stable hash token, so the same repo/branch still
       *correlates* across reflections without revealing its name. Values that
       are already redacted, empty, or "none" are left untouched (idempotency).
    2. **Free-text paths**: collapse absolute project/worktree paths to
       ``<repo>/...`` and strip usernames from home paths.

    Known limitation: free-text references to a repo/branch *by bare name*
    (not as a path) can't be detected and are not redacted — the v36 schema's
    "repo-agnostic" intent still applies to authored content. The structured
    fields above are the guaranteed-clean part.
    """
    lines = text.splitlines(keepends=False)
    out: list[str] = []
    already_marked = bool(lines) and lines[0].startswith("<!-- reflection-sync: redacted;")
    if machine and not already_marked:
        out.append(f"<!-- reflection-sync: redacted; machine={_slug(machine)} -->")

    for line in lines:
        m = _FIELD_RE.match(line)
        if m:
            name = m.group("name").strip().lower()
            prefix = _SENSITIVE_FIELDS.get(name)
            if prefix is not None:
                value = m.group("value")
                if _REDACTED_RE.match(value.strip()):
                    out.append(line)
                else:
                    out.append(m.group("prefix") + _hash_token(value, prefix))
                continue
        out.append(_redact_paths(line))

    result = "\n".join(out)
    if text.endswith("\n"):
        result += "\n"
    return result


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def _candidate_roots(repo: Path, harness: str) -> list[Path]:
    roots = [resolve_skill_bundle_root(harness)]
    for rel in _REPO_LOCAL_ROOTS.get(harness, ()):
        roots.append(repo / rel)
    return roots


def iter_reflection_files(repo: Path, harnesses: tuple[str, ...] = DEFAULT_HARNESSES):
    """Yield ``(skill_name, rel_path, source_path)`` for every live reflection.

    ``rel_path`` is relative to the skill's ``reflections/`` dir (e.g.
    ``<repo_hash>/<branch_slug>/<run_id>.md``, or a flat ``<file>.md`` for legacy
    layouts). Archived reflections are skipped. Duplicate (skill, rel) pairs
    seen across roots are de-duplicated.
    """
    repo = Path(repo).expanduser()
    seen: set[tuple[str, str]] = set()
    for harness in harnesses:
        for root in _candidate_roots(repo, harness):
            if not root.exists():
                continue
            for refl_dir in sorted(root.glob("*/reflections")):
                skill = refl_dir.parent.name
                for path in sorted(refl_dir.rglob("*.md")):
                    if "archive" in path.parts or not path.is_file():
                        continue
                    rel = path.relative_to(refl_dir)
                    key = (skill, rel.as_posix())
                    if key in seen:
                        continue
                    seen.add(key)
                    yield skill, rel, path


def export_reflections(
    repo: Path,
    dest_root: Path,
    machine: str | None = None,
    harnesses: tuple[str, ...] = DEFAULT_HARNESSES,
) -> list[Path]:
    """Redact and copy every live reflection into a machine-namespaced store.

    Destination layout (the ``<machine>`` segment sits directly above the file,
    so machines never collide even on an identical repo_hash/branch):

        ``<dest_root>/<skill>/<rel.parent>/<machine>/<rel.name>``

    Returns the list of written destination paths.
    """
    machine = machine or machine_id()
    dest_root = Path(dest_root).expanduser()
    written: list[Path] = []
    for skill, rel, src in iter_reflection_files(repo, harnesses):
        try:
            text = src.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Skip reflections that were imported from the pool (already redacted on
        # another machine) so an aggregator never re-pools them under its own id.
        if text.lstrip().startswith("<!-- reflection-sync: redacted;"):
            continue
        redacted = redact_reflection_text(text, machine=machine)
        dest = dest_root / skill / rel.parent / machine / rel.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(redacted, encoding="utf-8")
        written.append(dest)
    return written


# ---------------------------------------------------------------------------
# Minimal CLI seam (the full `phase-loop reflections sync` subcommand is Slice 2)
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="phase_loop_runtime.reflection_sync")
    sub = parser.add_subparsers(dest="command", required=True)

    exp = sub.add_parser("export", help="redact + copy local reflections to a store dir")
    exp.add_argument("--repo", required=True, help="repo root to scan")
    exp.add_argument("--dest", required=True, help="destination store root")
    exp.add_argument("--machine", default=None, help="machine id override")

    mid = sub.add_parser("machine-id", help="print this machine's stable id")
    mid.set_defaults(_is_machine_id=True)

    args = parser.parse_args(argv)

    if args.command == "machine-id":
        print(machine_id())
        return 0

    if args.command == "export":
        written = export_reflections(
            Path(args.repo), Path(args.dest), machine=args.machine
        )
        print(f"exported {len(written)} reflection(s) to {args.dest}")
        return 0

    parser.error("unknown command")
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
