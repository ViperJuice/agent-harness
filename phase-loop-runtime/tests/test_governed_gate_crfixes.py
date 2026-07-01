"""De-masking tests for the governed-gate relocation code-review fixes.

Each test would FAIL against the pre-fix code (the fail-open / mis-parse it guards):
- #1: every `_perform_phase_closeout` call threads `run_mode` (a missed call site
  silently skipped the gate on the inline post-launch closeout path).
- #2: an empty/unknown author set fails CLOSED (the prior code ran the FULL panel
  including the author's own leg — a silent self-review).
- #4: a substantive review with no conforming terminal verdict is a BLOCK, not a
  non-gating warn (a real objection must not be downgraded to advisory).
- #6: a verdict formatted as markdown ("- AGREE", "> AGREE") still parses (so a
  genuine approval is not over-blocked on cosmetics).
- #8: a no-disjoint-reviewer hold surfaces its ACCURATE reason, not "non_convergence".
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import Mock

from phase_loop_runtime import governed_review
from phase_loop_runtime.governed_premerge import (
    LoopResult, run_governed_premerge_loop,
)
from phase_loop_runtime.governed_review import GateResult, governed_planning_gate
from phase_loop_runtime.panel_invoker import (
    PanelLegResult, PanelResult, terminal_verdict,
)


# --- #1: run_mode threaded to EVERY _perform_phase_closeout call -------------

def test_all_perform_phase_closeout_calls_thread_run_mode():
    # Resolve runner.py from the IMPORTED module so this source-structure guard works
    # in both the src checkout and a from-wheel/site-packages install (Gate A clean room),
    # not only at the ../src/ path.
    from phase_loop_runtime import runner as _runner_mod
    src = Path(_runner_mod.__file__).read_text(encoding="utf-8")
    # Each call to _perform_phase_closeout( ... ) must pass run_mode=. (The def
    # itself defaults it, but a CALLER that omits it silently runs autonomous.)
    calls = list(re.finditer(r"_perform_phase_closeout\(", src))
    callsites = [m for m in calls
                 if src[m.start():m.start() + 30].startswith("_perform_phase_closeout(")
                 and "def _perform_phase_closeout" not in src[max(0, m.start() - 4):m.start() + 30]]
    # Walk each call's argument span to its matching close paren; assert run_mode= appears.
    missing = []
    for m in callsites:
        i = m.end()
        depth = 1
        while i < len(src) and depth:
            depth += {"(": 1, ")": -1}.get(src[i], 0)
            i += 1
        if "run_mode" not in src[m.end():i]:
            missing.append(src[max(0, m.start() - 40):i][:80])
    assert not missing, f"_perform_phase_closeout call(s) missing run_mode=: {missing}"


# --- #2: unknown author fails closed (no full-pool self-review) --------------

def test_empty_author_set_fails_closed_no_panel_spawn():
    invoke = Mock()  # must NOT be called — the gate blocks before spawning a panel
    result = governed_planning_gate(
        artifact="bundle",
        author_vendors=frozenset(),          # unknown author
        run_mode="governed",
        available_legs=("codex", "gemini", "claude"),
        invoke=invoke,
    )
    assert result.promoted is False
    assert result.reason == "unknown_author"
    invoke.assert_not_called()               # never ran the full (self-including) pool


# --- #4: substantive non-conforming review blocks (not warn) -----------------

def _panel(*legs: PanelLegResult) -> PanelResult:
    return PanelResult(legs=tuple(legs))


def test_substantive_nonconforming_leg_blocks_not_warns():
    # One clean approval + one substantive review with NO terminal verdict. The
    # non-conforming leg must produce a BLOCK so the gate does not promote on the
    # lone AGREE (the pre-fix code downgraded it to a non-gating warn).
    panel = _panel(
        PanelLegResult(leg="codex", status="OK", text="Looks correct.\nAGREE"),
        PanelLegResult(leg="gemini", status="DEGRADED",
                       text="This drops the auth check and is unsafe — but I forgot the verdict line."),
    )
    findings = governed_review._findings_from_panel(panel)
    severities = sorted(f.severity for f in findings)
    assert "block" in severities, severities


def test_empty_leg_is_warn_not_block():
    # A leg with NO usable text (timeout/auth/empty) is "no review happened" → warn,
    # not a block (don't over-block on an absent reviewer; #7 calibration).
    panel = _panel(PanelLegResult(leg="gemini", status="TIMEOUT", text=""))
    findings = governed_review._findings_from_panel(panel)
    assert [f.severity for f in findings] == ["warn"], findings


# --- #6: markdown-formatted verdict still parses -----------------------------

def test_markdown_formatted_verdict_parses():
    assert terminal_verdict("review...\n- AGREE") == "AGREE"
    assert terminal_verdict("review...\n> DISAGREE") == "DISAGREE"
    assert terminal_verdict("review...\n1. PARTIALLY AGREE") == "PARTIALLY AGREE"
    assert terminal_verdict("review...\n**AGREE**") == "AGREE"
    assert terminal_verdict("review...\nVERDICT: DISAGREE — breaks auth") == "DISAGREE"
    # A non-verdict last line is still non-conforming (None → caller fails closed).
    assert terminal_verdict("Overall this looks fine to me.") is None


# --- #8: no-reviewer hold carries its accurate reason ------------------------

def test_no_disjoint_reviewer_hold_reports_accurate_reason():
    # The gate returns a structural block (no disjoint reviewer). The loop terminal
    # must carry THAT reason, not the generic "non_convergence".
    structural_block = GateResult(
        ran=True, promoted=False, degraded=False, reason="author_vendor_only",
        findings=(),
    )
    result = run_governed_premerge_loop(
        artifact="b", author_executor="", author_vendors=frozenset({"codex", "gemini"}),
        run_mode="governed", apply_fix=None,
        invoke=lambda **_: structural_block,
    )
    assert isinstance(result, LoopResult)
    assert result.mergeable is False
    assert result.reason == "author_vendor_only"   # accurate, not "non_convergence"
