"""#63 — advisor-panel advisory mode.

`panel_invoker` was hardcoded to a pre-merge code-review framing, so it could not
be used as a general adversarial/advisory panel (architecture, product, red-teaming
a plan) — 2/3 legs replied "nothing to review". A `mode="advisory"` reuses all the
leg-spawn machinery but swaps the framing and drops the AGREE/DISAGREE requirement.
`mode="review"` stays the default (back-compat).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import phase_loop_runtime.panel_invoker as pi

_PROSE = "Here is my substantial advice: weigh the tradeoffs and the main risk is X."


def test_completion_ok_review_requires_verdict():
    assert pi._completion_ok("long review body with no verdict at the end here", "review") is False
    assert pi._completion_ok("body\nDISAGREE", "review") is True


def test_completion_ok_advisory_accepts_prose_without_verdict():
    assert pi._completion_ok(_PROSE, "advisory") is True
    assert pi._completion_ok("ok", "advisory") is False  # below substance threshold


def test_mode_instructions_differ():
    adv = pi._mode_instructions("advisory")
    assert "NOT a code review" in adv
    assert "NO AGREE/DISAGREE verdict is required" in adv  # advisory drops the verdict
    assert "pre-merge" in pi._mode_instructions("review")
    assert adv != pi._mode_instructions("review")


def test_classify_leg_advisory_prose_is_ok_review_would_degrade():
    assert pi._classify_leg(0, _PROSE, "", "advisory") == "OK"
    assert pi._classify_leg(0, _PROSE, "", "review") == "DEGRADED"  # no verdict → fail-closed


def test_default_spawn_advisory_stages_advisory_instructions_and_accepts_prose(tmp_path):
    captured = {}

    def fake_exec(leg, review_dir, out_dir, timeout_s, artifact, mode="review", model=None, **kwargs):
        captured["mode"] = mode
        captured["instructions"] = (review_dir / "review-instructions.md").read_text(encoding="utf-8")
        return 0, _PROSE, ""

    with patch.object(pi, "_exec_leg", side_effect=fake_exec):
        status, text = pi._default_spawn("gemini", "QUESTION", mode="advisory")

    assert status == "OK"  # prose accepted (no verdict required)
    assert captured["mode"] == "advisory"
    assert "NOT a code review" in captured["instructions"]  # advisory framing staged


def test_invoke_panel_advisory_passes_mode_and_accepts_prose():
    def fake_spawn(leg, artifact):
        return "OK", _PROSE

    res = pi.invoke_panel("QUESTION", ("codex", "gemini"), spawn=fake_spawn, mode="advisory")
    assert all(leg.status == "OK" and leg.usable for leg in res.legs)


def test_invoke_panel_review_is_default_and_unchanged():
    seen = {}

    def fake_spawn(leg, artifact):
        seen["called"] = True
        return "OK", "body\nAGREE"

    res = pi.invoke_panel("bundle", ("codex",), spawn=fake_spawn)  # no mode → review
    assert seen["called"] and res.legs[0].status == "OK"


def test_invoke_panel_rejects_unknown_mode():
    with pytest.raises(ValueError):
        pi.invoke_panel("x", ("codex",), spawn=lambda *a: ("OK", "y"), mode="nonsense")
