#!/usr/bin/env python3
"""
audit_lane_file_touches.py — audit a lane's commit against the plan's
disjoint-owned-files contract.

Usage:
    audit_lane_file_touches.py <lane-sha> <plan-doc-path> <this-lane-id>

Emits EXACTLY one token on stdout, exit 0 always (verdict is on stdout
for the orchestrator to act on):

  CLEAN
    - Every file touched by <lane-sha> (vs its first-parent ancestor)
      falls within <this-lane-id>'s `Owned files` globs from the plan
      doc.

  PEER_INTRUSION
    - One or more files touched match some OTHER lane's `Owned files`
      globs. The lane wandered into a peer's territory. Orchestrator
      surfaces to user for merge/no-merge decision — defensive cross-
      lane test-mock edits happen and are usually benign, but should be
      reviewed rather than surfacing as a later merge conflict.

  ORPHAN_FILES
    - One or more files touched fall OUTSIDE every lane's globs. The
      teammate worked outside any declared lane scope — probable bug.

Any combination of intrusion + orphan is reported with intrusion winning.
Details (which peer, which files) are written to stderr.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set


_SL_SECTION_RE = re.compile(r"^###\s+(SL-\d+)\b.*$", re.MULTILINE)
_OWNED_BULLET_RE = re.compile(
    r"^\s*-\s*\*\*Owned files\*\*\s*:\s*(.*?)\s*$",
    re.IGNORECASE,
)
_CODE_TOKEN_RE = re.compile(r"`([^`]+)`")


def _fail(msg: str) -> None:
    print(f"audit_lane_file_touches: {msg}", file=sys.stderr)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        _fail(f"cannot read {path}: {exc}")
        sys.exit(2)


def _extract_lanes_body(src: str) -> str:
    """Return the body between '## Lanes' and the next '## '."""
    lines = src.splitlines()
    out: List[str] = []
    in_section = False
    for line in lines:
        if line.startswith("## ") and line.strip() == "## Lanes":
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section:
            out.append(line)
    return "\n".join(out)


def _parse_owned_globs(lanes_body: str) -> Dict[str, List[str]]:
    """Map SL-N -> list of owned globs as declared in the plan."""
    matches = list(_SL_SECTION_RE.finditer(lanes_body))
    result: Dict[str, List[str]] = {}
    for idx, m in enumerate(matches):
        sl_id = m.group(1)
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(lanes_body)
        body = lanes_body[start:end]
        globs: List[str] = []
        for line in body.splitlines():
            mb = _OWNED_BULLET_RE.match(line)
            if not mb:
                continue
            raw = mb.group(1)
            tokens = _CODE_TOKEN_RE.findall(raw)
            globs.extend(t.strip() for t in tokens if t.strip())
        # Drop annotation-only items like "(NEW)" / "(DELETE)".
        globs = [
            g.split()[0].strip() for g in globs
            if g and not g.startswith("(")
        ]
        result[sl_id] = globs
    return result


def _glob_to_regex(pattern: str) -> re.Pattern:
    """Minimal glob → regex. ** → any depth; * → any non-/; ? → one non-/."""
    s = pattern.strip("`").strip()
    esc = re.escape(s)
    esc = esc.replace(r"\*\*", ".*").replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
    return re.compile("^" + esc + "$")


def _touched_files(sha: str) -> List[str]:
    """Files changed by <sha> relative to its first parent."""
    try:
        r = subprocess.run(
            ["git", "diff", "--name-only", f"{sha}^..{sha}"],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError:
        # Commit has no parent (e.g., root) — fall back to diff against empty tree.
        r = subprocess.run(
            ["git", "show", "--name-only", "--pretty=", sha],
            capture_output=True, text=True, check=True,
        )
    return [line for line in r.stdout.splitlines() if line.strip()]


def main(argv: List[str]) -> int:
    if len(argv) != 4:
        _fail("usage: audit_lane_file_touches.py <lane-sha> <plan-doc-path> <this-lane-id>")
        return 2

    lane_sha = argv[1]
    plan_path = Path(argv[2])
    this_lane = argv[3]

    if not plan_path.exists():
        _fail(f"plan doc not found: {plan_path}")
        return 2

    src = _read(plan_path)
    lanes_body = _extract_lanes_body(src)
    owned_map = _parse_owned_globs(lanes_body)

    if this_lane not in owned_map:
        _fail(f"lane {this_lane} not found in plan doc's `## Lanes` section")
        return 2

    # Compile regexes per lane.
    compiled: Dict[str, List[re.Pattern]] = {
        sl: [_glob_to_regex(g) for g in globs]
        for sl, globs in owned_map.items()
    }

    touched = _touched_files(lane_sha)

    def match_any(path: str, pats: List[re.Pattern]) -> bool:
        return any(p.match(path) for p in pats)

    intrusions: Dict[str, List[str]] = {}
    orphans: List[str] = []
    clean: List[str] = []
    for path in touched:
        if match_any(path, compiled[this_lane]):
            clean.append(path)
            continue
        peer_hit: Optional[str] = None
        for sl, pats in compiled.items():
            if sl == this_lane:
                continue
            if match_any(path, pats):
                peer_hit = sl
                break
        if peer_hit is not None:
            intrusions.setdefault(peer_hit, []).append(path)
        else:
            orphans.append(path)

    # Emit verdict. Intrusion takes precedence over orphan (intrusion is more actionable).
    if intrusions:
        for peer, paths in sorted(intrusions.items()):
            _fail(f"{this_lane} touched files owned by {peer}: {paths}")
        if orphans:
            _fail(f"{this_lane} also touched orphan files (no lane owns): {orphans}")
        print("PEER_INTRUSION")
        return 0

    if orphans:
        _fail(f"{this_lane} touched files outside any lane's Owned files: {orphans}")
        print("ORPHAN_FILES")
        return 0

    print("CLEAN")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
