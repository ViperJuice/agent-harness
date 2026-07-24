"""Governed pre-merge review bundle (model-routing-v2 P1, IF-0-P1-1).

The artifact the panel reviews at the pre-merge gate is a *review bundle*, not a
bare diff: the EXACT staged diff the closeout is about to commit, plus the plan's
``## Acceptance Criteria``, the verification-command results, and a one-paragraph
closeout summary. The panel needs the change *and* the spec to judge it against.

The gate fires INSIDE ``_perform_phase_closeout`` — after ``git add`` stages the
owned paths and before the commit is finalized — and passes the staged-index diff
(``git diff --cached``) in here. So "what the panel reviews" == "what gets
committed" by construction: no separately-discovered path set to diverge from
closeout, no untracked-file synthesis, and the renderer never writes a file into
the repo (advisor-panel reconciliation — the prior independent path discovery and
in-repo staging were the divergence + worktree-dirtying defects).
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence


def staged_index_diff(repo: Path, paths: Sequence[str]) -> str:
    """The EXACT diff about to be committed: ``git diff --cached -- <paths>``.

    After ``git add``, newly-added files appear natively in the cached diff (no
    ``--no-index`` synthesis, no untracked probe). This is the staged index, so it
    cannot diverge from what the commit records.
    """
    if not paths:
        return "(no staged paths)"
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "diff", "--cached", "--", *paths],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        return "(staged diff unavailable)"
    return out.stdout.rstrip() or "(empty staged diff)"


def committed_range_diff(repo: Path, base_sha: str, head_sha: str) -> str:
    """The EXACT committed-range diff ``git diff <base_sha> <head_sha>`` — the FAB
    piece-3b consumer's delta-review unit (the committed ``old_admitted..new_head``
    range a delta review actually looks at), the committed-range analog of
    ``staged_index_diff``.

    Mirrors ``staged_index_diff``'s sentinel-on-failure posture. The delta review
    is over a COMMITTED range (the closeout already happened on the PR branch), not
    a staged index, so there is nothing to ``git add``; the two SHAs pin exactly
    what the seats are shown. The producer's delta honesty gate rejects an
    incomplete render (binary-elided / attribute-suppressed / invalid-UTF-8
    sentinel) exactly as the candidate round does — so a transient render failure
    can never be laundered into provenance for bytes the seats never saw.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "diff", base_sha, head_sha],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        return "(committed range diff unavailable)"
    return out.stdout.rstrip() or "(empty committed range diff)"


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
    phase_alias: str,
    terminal: Mapping[str, Any],
    plan_path: str | Path | None,
    diff_text: str,
) -> str:
    """Render the review bundle text from a PRECOMPUTED staged-index diff.

    The caller (the in-closeout gate) computes ``diff_text`` via
    ``staged_index_diff`` over the exact paths it is committing, so the bundle
    cannot diverge from the commit. The renderer never touches git or the
    filesystem; ``panel_invoker`` stages the bundle as material in a temp dir
    OUTSIDE the repo, so the gate never dirties the worktree under review.
    """
    return "\n".join(
        [
            f"# Governed pre-merge review — phase {phase_alias}",
            "",
            "## Change (staged diff — exactly what will be committed)",
            "```diff",
            diff_text,  # producer (staged_index_diff) owns the empty/unavailable sentinel
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
