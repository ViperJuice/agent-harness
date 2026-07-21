"""ah#231 — grok max-effort-planner representational honesty + panel effort hardening.

DECIDED design (REPRESENTATIONAL honesty, rescoped from the original
"narrow-reject" framing after a cross-vendor CR): grok's real reasoning ceiling
is ``high`` (its ``--reasoning-effort`` CLI rejects ``max``, ah#222/#224), so it
must not be REPRESENTED as a max-effort PLANNER OF RECORD — the same stance
gemini/pi take. But the mechanism is decoupled from run-level effort translation:

  * ``max_effort_planner_eligible`` now reads a dedicated ``planner_max_class``
    capability field. Unset (``None``) DERIVES from ``supported_efforts``
    (``"max" in supported_efforts``), preserving every other provider's behavior.
    grok sets ``planner_max_class=False`` explicitly.
  * UNLIKE gemini/pi, grok deliberately KEEPS its broad ``supported_efforts`` (it
    still includes ``max``). So an explicit ``max`` request for grok stays a VALID
    request at the policy layer (resolves to ``max``, not the fallback ``high``)
    and is clamped to grok's real ``high`` ceiling only at the CLI-emit boundary
    (``launcher._grok_cli_effort``, ah#224 — untouched here, pinned in
    ``test_grokexec.py``). This is exactly the pre-ah#231 effort-translation
    behavior; only the eligibility signal changed.

This is not a runtime SELECTION gate: ``resolve_dispatch_decision`` never consults
eligibility, and grok is never AUTOSEL-selected as the planner of record anyway
(ah#231). It is a representational guard so grok's reasoning ceiling is honest for
planner-selection purposes. It does not reduce grok's effort anywhere it runs —
grok stays fully usable as a panel/CR reviewer leg and as a planner for non-max
efforts, at its real ``high`` ceiling.

Also covers part 2 of ah#231: hardening the panel effort lookup (formerly the
direct-index ``_GROK_EFFORT`` dict, now ``_grok_panel_effort``/
``_GROK_EFFORT_OVERRIDES``) so it can never ``KeyError`` on an effort outside
its historical 4-key vocabulary.
"""
import unittest
from pathlib import Path

import pytest

from phase_loop_runtime.advisor_board.harness_mapping import (
    MECH_FLAG,
    _GROK_EFFORT_OVERRIDES,
    _grok_panel_effort,
    render_seat_invocation,
)
from phase_loop_runtime import launcher
from phase_loop_runtime.capability_registry import provider_policy_capabilities
from phase_loop_runtime.profiles import (
    SHIPPED_MODEL_POLICY,
    max_effort_planner_eligible,
    resolve_execution_policy,
    resolve_model_selection_from_policy,
    resolve_profile_for_executor,
    shipped_model_policy_rule,
)


def _resolve(action, executor, *, model_policy=False):
    selection = resolve_profile_for_executor(action=action, executor=executor)
    rule = shipped_model_policy_rule(action) if model_policy else None
    rp = resolve_execution_policy(
        action=action, executor=executor, model_selection=selection, model_policy_rule=rule
    )
    return rp.model, rp.effort


# --- codex CR gap (cross-vendor review of ah#231): end-to-end operator-effort
# regression coverage ---------------------------------------------------------
#
# Neither existing test drives an OPERATOR effort override through the REAL
# policy chain onto a grok CLI command:
#   * `_resolve` above (and every test built on it) proves `max` only via the
#     shipped model_policy's `effort="max"` field, never `operator_effort=`
#     (the `runner.py` codepath that carries a `--effort` CLI flag).
#   * `test_grokexec.test_build_grok_command_clamps_explicit_effort` builds a
#     `ModelSelection` DIRECTLY (`dataclasses.replace`), bypassing
#     `resolve_execution_policy`/`normalize_provider_effort` entirely.
# So neither would fail if a future edit narrowed grok's `supported_efforts`
# (dropping `xhigh`/`minimal`/`max`) and reintroduced the
# `normalize_provider_effort` `ValueError` this module's docstring describes
# guarding against. This test wires the full chain
# (`resolve_profile_for_executor` -> `resolve_execution_policy` with
# `operator_effort=`, mirroring `runner.py`'s `--effort` handling, ->
# `resolve_model_selection_from_policy` -> `launcher.build_grok_command`) so a
# regression there fails HERE.
@pytest.mark.parametrize(
    ("operator_effort", "expected_cli_effort"),
    [
        ("max", "high"),
        ("xhigh", "high"),
        ("minimal", "low"),
    ],
)
def test_grok_execute_operator_effort_survives_real_policy_chain_to_cli(operator_effort, expected_cli_effort):
    action = "execute"
    executor = "grok"
    # `execute` deliberately keeps `model_class="implementer"` (not "planner"),
    # so the `max_effort_planner_eligible` force-clamp guard in
    # `resolve_execution_policy` never fires here — an operator effort must
    # survive on grok's broad `supported_efforts` alone, exactly the path the
    # codex CR flagged as untested.
    selection = resolve_profile_for_executor(action=action, executor=executor)
    execution_policy = resolve_execution_policy(
        action=action,
        executor=executor,
        model_selection=selection,
        operator_effort=operator_effort,
        model_policy_rule=shipped_model_policy_rule(action),
    )
    # Reaching this line without a ValueError IS part of the assertion: a
    # narrowed `supported_efforts` would raise inside `resolve_execution_policy`
    # (via `normalize_provider_effort`) before this point.
    resolved_selection = resolve_model_selection_from_policy(
        profile=selection.profile, resolved_policy=execution_policy
    )
    command = launcher.build_grok_command(
        Path("/repo"), resolved_selection, action=action, context_file="ctx"
    )
    assert command[command.index("--reasoning-effort") + 1] == expected_cli_effort


class GrokMaxEffortPlannerEligibilityTest(unittest.TestCase):
    # 1 — the headline ask: grok is not represented as a max-effort planner of record.
    def test_grok_not_max_planner_of_record(self):
        self.assertFalse(max_effort_planner_eligible("grok"))
        # gemini/pi's existing ineligibility is untouched by this change.
        self.assertFalse(max_effort_planner_eligible("gemini"))
        self.assertFalse(max_effort_planner_eligible("pi"))
        # codex/claude remain the max-effort-eligible planners of record.
        self.assertTrue(max_effort_planner_eligible("codex"))
        self.assertTrue(max_effort_planner_eligible("claude"))

    # The DECOUPLING: grok keeps its broad supported_efforts (still honors an
    # explicit `max` for effort translation) yet carries the eligibility signal
    # on the dedicated `planner_max_class` field.
    def test_grok_keeps_broad_supported_efforts_but_flag_false(self):
        capability = provider_policy_capabilities()["grok"]
        self.assertIn("max", capability.supported_efforts)  # broad — honors explicit max
        self.assertIs(capability.planner_max_class, False)  # eligibility carried by the flag

    # The derive-default keeps every non-grok provider coupled to supported_efforts
    # (they leave the flag unset), so only grok's eligibility is explicitly overridden.
    def test_planner_max_class_derive_default_is_explicit_only_for_grok(self):
        caps = provider_policy_capabilities()
        self.assertIsNone(caps["codex"].planner_max_class)   # derives True from _ALL_EFFORTS
        self.assertIsNone(caps["gemini"].planner_max_class)  # derives False (narrow)
        self.assertIsNone(caps["pi"].planner_max_class)      # derives False (narrow)
        self.assertIs(caps["grok"].planner_max_class, False)  # the one explicit override

    # 2 — grok still runs at its own real max everywhere it actually runs: as a
    # planner for a non-max effort.
    def test_grok_still_usable_as_non_max_planner(self):
        model, effort = _resolve("review", "grok", model_policy=True)
        self.assertEqual(effort, "high")  # SHIPPED_MODEL_POLICY review effort, no clamp needed
        self.assertTrue(model)  # resolves to a concrete grok model, no exception

    # 3 — an explicit `max` request for grok is HONORED at the policy layer (stays
    # `max`, never crashes, because grok keeps a broad supported_efforts) and is
    # translated to grok's real `high` ceiling at the CLI-emit boundary (ah#224).
    # This is the pre-ah#231 effort behavior, deliberately preserved by the decoupling.
    def test_explicit_max_request_for_grok_honored_then_cli_clamped(self):
        self.assertEqual(SHIPPED_MODEL_POLICY["plan"]["effort"], "max")
        for action in ("plan", "roadmap"):
            model, effort = _resolve(action, "grok", model_policy=True)
            self.assertEqual(effort, "max", f"{action}: max stays valid at policy layer, not clamped")
            self.assertTrue(model)
        # ...and the real delivered ceiling is grok's `high`, clamped at CLI emit (unchanged).
        self.assertEqual(launcher._grok_cli_effort("max"), "high")

    # gemini/pi (narrow supported_efforts) clamp `max` to `high` at the POLICY layer;
    # grok (broad supported_efforts + planner_max_class=False) keeps `max` at the policy
    # layer and clamps at the CLI. All three are ineligible — the shared invariant — but
    # via two DIFFERENT mechanisms, which this test pins so a future edit can't conflate them.
    def test_ineligibility_shared_but_effort_translation_differs(self):
        for executor in ("gemini", "pi"):
            self.assertFalse(max_effort_planner_eligible(executor))
            _, effort = _resolve("plan", executor, model_policy=True)
            self.assertEqual(effort, "high")  # narrow → policy-layer clamp
        self.assertFalse(max_effort_planner_eligible("grok"))
        _, grok_effort = _resolve("plan", "grok", model_policy=True)
        self.assertEqual(grok_effort, "max")  # broad → honored at policy layer, CLI clamps


class GrokPanelUsabilityTest(unittest.TestCase):
    # grok remains a fully usable panel/CR reviewer leg at its real `high`
    # ceiling — this path never consults `supported_efforts`/eligibility at all.
    def test_grok_panel_seat_renders_at_high(self):
        seat = render_seat_invocation("grok", "grok-4.5", "high")
        self.assertEqual(seat.harness, "grok")
        self.assertEqual(seat.mechanism, MECH_FLAG)
        self.assertEqual(seat.effort_args, ("--reasoning-effort", "high"))

    def test_grok_panel_seat_clamps_max_to_high(self):
        seat = render_seat_invocation("grok", "grok-4.5", "max")
        self.assertEqual(seat.effort_args, ("--reasoning-effort", "high"))

    def test_grok_panel_seat_low_medium_pass_through(self):
        for effort in ("low", "medium"):
            seat = render_seat_invocation("grok", "grok-4.5", effort)
            self.assertEqual(seat.effort_args, ("--reasoning-effort", effort))


class GrokEffortLookupHardeningTest(unittest.TestCase):
    # part 2 — `_grok_panel_effort` must never KeyError, unlike the old direct
    # `_GROK_EFFORT[effort]` indexing it replaces.
    def test_known_efforts_map_correctly(self):
        self.assertEqual(_grok_panel_effort("low"), "low")
        self.assertEqual(_grok_panel_effort("medium"), "medium")
        self.assertEqual(_grok_panel_effort("high"), "high")
        self.assertEqual(_grok_panel_effort("max"), "high")

    def test_growing_vocabulary_still_clamps_to_a_valid_grok_token(self):
        # Simulates the panel effort vocabulary growing past today's 4-key
        # EFFORT_LEVELS to include "minimal"/"xhigh" (which
        # normalize_provider_effort's NORMALIZED_EFFORT_LEVELS already knows
        # about but the panel doesn't yet). The old `_GROK_EFFORT[effort]` direct
        # index would KeyError here; parity with `launcher._grok_cli_effort`
        # requires these to clamp to a CLI-valid token, not merely avoid crashing
        # (an unclamped "xhigh"/"minimal" would still error the grok CLI, trading
        # a KeyError for a CLI rejection).
        self.assertEqual(_grok_panel_effort("xhigh"), "high")
        self.assertEqual(_grok_panel_effort("minimal"), "low")

    def test_truly_unknown_effort_passes_through_instead_of_keyerror(self):
        # A genuinely unrecognized token (outside both today's and any
        # anticipated future vocabulary) must never KeyError — it passes through
        # unchanged rather than being silently dropped.
        self.assertEqual(_grok_panel_effort("ultra"), "ultra")

    def test_overrides_map_matches_launcher_grok_cli_effort_verbatim(self):
        # Parity requirement from ah#231: the panel's clamp map must match
        # `launcher._GROK_CLI_EFFORT_OVERRIDES` exactly, not just its `.get`
        # structure — otherwise the panel and the launcher clamp the SAME
        # canonical effort to DIFFERENT grok CLI tokens.
        from phase_loop_runtime.launcher import _GROK_CLI_EFFORT_OVERRIDES

        self.assertEqual(_GROK_EFFORT_OVERRIDES, _GROK_CLI_EFFORT_OVERRIDES)
        self.assertEqual(_GROK_EFFORT_OVERRIDES, {"minimal": "low", "xhigh": "high", "max": "high"})


if __name__ == "__main__":
    unittest.main()
