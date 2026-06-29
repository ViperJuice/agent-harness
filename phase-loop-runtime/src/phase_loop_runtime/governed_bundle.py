"""Governed pre-merge review bundle (model-routing-v2 P1, IF-0-P1-1).

The artifact the panel reviews at the pre-merge gate is a *review bundle*, not a
bare diff: the staged diff over the phase's owned dirty paths, plus the plan's
``## Acceptance Criteria``, the verification-command results, and a one-paragraph
closeout summary. The panel needs the change *and* the spec to judge it against.

``render_governed_bundle`` produces the bundle text and stages it as a file in a
read-only review dir (panelists read it; outputs land elsewhere). ``apply_fix``
(the runner closure) returns the re-rendered bundle after a repair re-dispatch.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

BUNDLE_FILENAME = "review-bundle.md"


def _owned_dirty_paths(snapshot_or_terminal: Mapping[str, Any]) -> tuple[str, ...]:
    t = snapshot_or_terminal
    paths = (
        t.get("phase_owned_dirty_paths")
        or t.get("dirty_paths")
        or t.get("previous_phase_owned_paths")
        or ()
    )
    return tuple(str(p) for p in paths if str(p))


def _staged_diff(repo: Path, paths: Sequence[str]) -> str:
    if not paths:
        return "(no owned dirty paths recorded)"
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "diff", "HEAD", "--", *paths],
            capture_output=True, text=True, timeout=30,
        )
        body = out.stdout.strip()
        return body or "(empty diff over owned paths)"
    except Exception:
        return "(diff unavailable)"


def _acceptance_criteria(plan_path: str | Path | None) -> str:
    if not plan_path:
        return "(no plan path)"
    try:
        text = Path(plan_path).read_text(encoding="utf-8")
    except OSError:
        return "(plan unreadable)"
    lines = text.splitlines()
    out: list[str] = []
    capture = False
    for line in lines:
        if line.lstrip().lower().startswith("## acceptance"):
            capture = True
            continue
        if capture and line.startswith("## "):
            break
        if capture:
            out.append(line)
    body = "\n".join(out).strip()
    return body or "(no acceptance-criteria section found)"


def _verification_results(terminal: Mapping[str, Any]) -> str:
    results = terminal.get("verification") if isinstance(terminal.get("verification"), Mapping) else None
    if isinstance(results, Mapping):
        rows = results.get("results") or results.get("commands") or ()
    else:
        rows = terminal.get("verification_results") or ()
    if not rows:
        status = terminal.get("verification_status") or "unknown"
        return f"verification_status: {status} (no per-command results recorded)"
    lines = []
    for r in rows:
        if isinstance(r, Mapping):
            lines.append(f"- {r.get('code') or r.get('command') or r}: {r.get('status') or r.get('ok')}")
        else:
            lines.append(f"- {r}")
    return "\n".join(lines)


def _summary(terminal: Mapping[str, Any]) -> str:
    return str(
        terminal.get("next_action")
        or terminal.get("terminal_status")
        or "(no summary)"
    )


def render_governed_bundle(
    *,
    repo: Path,
    phase_alias: str,
    terminal: Mapping[str, Any],
    plan_path: str | Path | None,
    review_dir: Path | None = None,
) -> tuple[str, Path | None]:
    """Render the review bundle and (optionally) stage it to a file.

    Returns ``(bundle_text, staged_path_or_None)``. ``review_dir`` is the
    panel's read-only input dir; the bundle is written as ``review-bundle.md``.
    """
    paths = _owned_dirty_paths(terminal)
    bundle = "\n".join(
        [
            f"# Governed pre-merge review — phase {phase_alias}",
            "",
            "## Change (staged diff over phase-owned paths)",
            "```diff",
            _staged_diff(repo, paths),
            "```",
            "",
            "## Acceptance Criteria (the spec to judge against)",
            _acceptance_criteria(plan_path),
            "",
            "## Verification results",
            _verification_results(terminal),
            "",
            "## Summary",
            _summary(terminal),
            "",
        ]
    )
    staged: Path | None = None
    if review_dir is not None:
        try:
            review_dir.mkdir(parents=True, exist_ok=True)
            staged = review_dir / BUNDLE_FILENAME
            staged.write_text(bundle, encoding="utf-8")
        except OSError:
            staged = None
    return bundle, staged
