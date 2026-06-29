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
    """The exact path set the closeout will commit — `_perform_phase_closeout`
    stages ``dict.fromkeys((*phase_owned_dirty_paths, *previous_phase_owned_paths))``.
    The panel must review THAT set, not the whole worktree: an earlier
    ``or dirty_paths`` fallback widened the bundle to every dirty path and leaked
    a sibling phase's changes into this phase's review (code-review finding,
    verified). No whole-worktree fallback here, by design."""
    t = snapshot_or_terminal
    union = (
        *(t.get("phase_owned_dirty_paths") or ()),
        *(t.get("previous_phase_owned_paths") or ()),
    )
    return tuple(dict.fromkeys(str(p) for p in union if str(p)))


def _is_untracked(repo: Path, path: str) -> bool:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "--error-unmatch", "--", path],
            capture_output=True, text=True, timeout=30,
        )
        return out.returncode != 0
    except Exception:
        return False


def _staged_diff(repo: Path, paths: Sequence[str]) -> str:
    """Diff the owned paths against HEAD. Plain ``git diff HEAD`` omits untracked
    NEW files, so a phase that only adds files would present an empty diff to the
    panel (code-review finding, verified). For each untracked owned path we append
    a synthetic added-file diff via ``git diff --no-index`` (side-effect-free — it
    never touches the index, so the subsequent closeout commit is unaffected)."""
    if not paths:
        return "(no owned dirty paths recorded)"
    sections: list[str] = []
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "diff", "HEAD", "--", *paths],
            capture_output=True, text=True, timeout=30,
        )
        if out.stdout.strip():
            sections.append(out.stdout.rstrip())
    except Exception:
        return "(diff unavailable)"
    for path in paths:
        if not _is_untracked(repo, path):
            continue
        try:
            # --no-index against /dev/null renders the whole untracked file as added;
            # rc 1 (differences found) is expected, not an error.
            u = subprocess.run(
                ["git", "-C", str(repo), "diff", "--no-index", "--", "/dev/null", path],
                capture_output=True, text=True, timeout=30,
            )
            if u.stdout.strip():
                sections.append(u.stdout.rstrip())
        except Exception:
            sections.append(f"(untracked owned file, diff unavailable: {path})")
    body = "\n".join(sections).strip()
    return body or "(empty diff over owned paths)"


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
    # The terminal summary records per-command verification under
    # `verification_commands` (list[dict]); `build_terminal_summary` never emits
    # `verification`/`verification_results`, so the old keys always missed and the
    # panel saw "no results recorded" even when commands ran (code-review finding,
    # verified). Accept the real key first; keep the legacy keys as a tolerant
    # fallback for hand-built terminals in tests.
    rows = terminal.get("verification_commands") or ()
    if not rows:
        results = terminal.get("verification") if isinstance(terminal.get("verification"), Mapping) else None
        if isinstance(results, Mapping):
            rows = results.get("results") or results.get("commands") or ()
        else:
            rows = terminal.get("verification_results") or ()
    if not rows:
        unit = terminal.get("latest_verification_unit")
        status = (
            (unit.get("status") if isinstance(unit, Mapping) else None)
            or terminal.get("verification_status")
            or "unknown"
        )
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
