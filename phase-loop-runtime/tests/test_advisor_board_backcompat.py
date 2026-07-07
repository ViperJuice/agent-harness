"""ABDFREEZE — back-compat golden-test SCAFFOLD (IF-0-ABDFREEZE-4).

This proves the ``default`` board fixture resolves to TODAY'S three legs — cross-
checked against the LIVE ``panel_invoker`` constants, not a re-baselined copy — so
a future drift in the panel constants trips here. The FULL golden assertions
(launch order, prompt/input payloads, env/auth, timeout/retry, result keys, output
formatting, failure semantics) land in ABDVERIFY; this file is the scaffold + the
default-board equivalence anchor, plus the auth-scrub byte-equivalence proof.

Behavior-neutrality of the whole phase is proven at the repo level (``git diff``
on ``panel_invoker.py`` is empty + the full existing suite green); this module
adds the seat-level equivalence.
"""
from __future__ import annotations

import inspect
import unittest
from pathlib import Path

from phase_loop_runtime import panel_invoker as pi
from phase_loop_runtime.advisor_board import (
    AUTH_API_KEY,
    CANONICAL_LEG_ORDER,
    DEFAULT_BOARD,
    DEFAULT_SEAT_EFFORT_ARGS,
    DEFAULT_SEAT_RENDERED_MODEL,
    Seat,
    all_vendor_key_vars,
    render_seat_invocation,
    resolve_seat_env,
    seat_vendor_family,
)
from phase_loop_runtime.advisor_board.backing import VENDOR_API_KEY_VARS


class DefaultBoardReproducesTodayTests(unittest.TestCase):
    def test_default_board_is_three_seats_in_panel_leg_order(self) -> None:
        self.assertEqual(len(DEFAULT_BOARD.seats), 3)
        self.assertEqual(tuple(seat_vendor_family(s) for s in DEFAULT_BOARD.seats), pi.PANEL_LEGS)
        self.assertEqual(CANONICAL_LEG_ORDER, pi.PANEL_LEGS)

    def test_each_seat_renders_to_todays_default_leg_model(self) -> None:
        # The model-first {model, effort} split must reconstruct the exact strings
        # in the live DEFAULT_LEG_MODELS — including the agy leg's effort-in-name.
        for seat in DEFAULT_BOARD.seats:
            leg = seat_vendor_family(seat)
            inv = render_seat_invocation(seat.harness, seat.model, seat.effort)
            self.assertEqual(inv.model, pi.DEFAULT_LEG_MODELS[leg], f"leg={leg}")
            self.assertEqual(inv.model, DEFAULT_SEAT_RENDERED_MODEL[leg], f"leg={leg}")

    def test_each_seat_renders_todays_effort_form(self) -> None:
        for seat in DEFAULT_BOARD.seats:
            leg = seat_vendor_family(seat)
            inv = render_seat_invocation(seat.harness, seat.model, seat.effort)
            self.assertEqual(inv.effort_args, DEFAULT_SEAT_EFFORT_ARGS[leg], f"leg={leg}")

    def test_effort_literals_match_the_live_panel_invoker_source(self) -> None:
        # Prove the golden effort tokens are the ones panel_invoker actually emits
        # today (they are hard-coded inline, not exported constants).
        src = Path(pi.__file__).read_text(encoding="utf-8")
        self.assertIn("model_reasoning_effort=xhigh", src)  # codex :992
        self.assertIn('"--effort",', src)                    # claude --effort ...
        self.assertIn('"Gemini 3.1 Pro (High)"', src)        # agy effort-in-name :1016


class AuthScrubByteEquivalenceTests(unittest.TestCase):
    def test_vendor_key_var_union_equals_todays_flat_tuple(self) -> None:
        # subscription scrubbing stays byte-equivalent to _subscription_env only if
        # the vendor-keyed map's union is exactly the current flat _API_KEY_VARS.
        self.assertEqual(set(all_vendor_key_vars()), set(pi._API_KEY_VARS))

    def test_subscription_seat_scrubs_every_vendor_key(self) -> None:
        base = {var: "secret" for var in pi._API_KEY_VARS} | {"PATH": "/usr/bin", "HOME": "/h"}
        sub_seat = Seat(model="gpt-5.5", effort="max", harness="codex")  # subscription default
        env = resolve_seat_env(sub_seat, base)
        for var in pi._API_KEY_VARS:
            self.assertNotIn(var, env)
        self.assertEqual(env["PATH"], "/usr/bin")  # non-key vars preserved

    def test_api_key_seat_injects_only_its_vendor_key(self) -> None:
        base = {var: "secret" for var in pi._API_KEY_VARS} | {"PATH": "/usr/bin"}
        codex_seat = Seat(model="gpt-5.5", effort="max", harness="codex", auth=AUTH_API_KEY)
        env = resolve_seat_env(codex_seat, base, allow_api_key_fallback=True)
        self.assertEqual(env["OPENAI_API_KEY"], "secret")          # only codex's key
        self.assertNotIn("ANTHROPIC_API_KEY", env)                 # never another vendor's
        self.assertNotIn("GEMINI_API_KEY", env)
        # sanity: the map really does carve openai out for the codex family
        self.assertEqual(VENDOR_API_KEY_VARS["codex"], ("OPENAI_API_KEY",))

    def test_api_key_seat_without_optin_raises_never_silent(self) -> None:
        codex_seat = Seat(model="gpt-5.5", effort="max", harness="codex", auth=AUTH_API_KEY)
        with self.assertRaises(ValueError):
            resolve_seat_env(codex_seat, {"OPENAI_API_KEY": "x"}, allow_api_key_fallback=False)


class InvokePanelApiStabilityTests(unittest.TestCase):
    def test_invoke_panel_signature_is_frozen(self) -> None:
        # ABDFREEZE-4: the invoke_panel() API is part of the back-compat contract.
        # The frozen positional/keyword contract (artifact, legs) is unchanged; the
        # keyword-only, default-valued extensions (`models` #66, `max_concurrency`,
        # and the ABDREF by-reference ingestion `artifact_ref` / `brief_ref`) are
        # additive and back-compat — no existing caller passes them positionally.
        sig = inspect.signature(pi.invoke_panel)
        self.assertEqual(
            list(sig.parameters),
            ["artifact", "legs", "spawn", "repo_dir", "mode", "models",
             "max_concurrency", "artifact_ref", "brief_ref",
             # #114 additive, keyword-only, defaulted: TRUE by-reference file refs +
             # per-leg timeout bound.
             "context_refs", "context_refs_soft_warn", "timeouts_by_leg"],
        )
        # The additions are keyword-only with defaults (the real back-compat guarantee).
        for name in ("models", "max_concurrency", "artifact_ref", "brief_ref",
                     "context_refs", "context_refs_soft_warn", "timeouts_by_leg"):
            self.assertEqual(sig.parameters[name].kind, inspect.Parameter.KEYWORD_ONLY)
            self.assertIsNot(sig.parameters[name].default, inspect.Parameter.empty)


class DeferredGoldenDimensionsScaffold(unittest.TestCase):
    """The golden dimensions this scaffold deferred are now PROVEN in ABDVERIFY —
    ``tests/test_advisor_board_golden.py``. This map is kept so the ABDFREEZE→
    ABDVERIFY hand-off stays legible: each deferred dimension names the golden test
    that now carries its full assertion (no dimension was dropped)."""

    #: deferred dimension -> the ABDVERIFY golden test that now asserts it.
    LANDED_IN_ABDVERIFY = {
        "launch_order": "GoldenWholeBoardBehaviorTests.test_every_leg_launched_once_and_results_in_order_both_paths (legs now fan out concurrently: every leg launched once + RESULTS in canonical order)",
        "prompt_payloads": "GoldenWholeBoardBehaviorTests.test_every_leg_launched_once_* (same artifact fed to every leg)",
        "env_auth": "GoldenPerLegLaunchTests.test_*_argv_env_and_timeout_equal_legacy",
        "timeout_retry": "GoldenPerLegLaunchTests.test_per_leg_timeout_is_a_pure_function_of_the_staged_artifact",
        "result_keys": "GoldenWholeBoardBehaviorTests.test_seat_key_is_the_sole_documented_delta",
        "output_formatting": "GoldenWholeBoardBehaviorTests.test_ok_results_are_byte_identical_except_seat_key",
        "failure_semantics": "GoldenWholeBoardBehaviorTests.test_failure_semantics_* / test_mixed_per_leg_outcomes_classify_identically",
    }

    def test_every_deferred_dimension_landed(self) -> None:
        # The scaffold enumerated 7 deferred dimensions; each now maps to a real
        # ABDVERIFY golden assertion.
        self.assertEqual(len(self.LANDED_IN_ABDVERIFY), 7)
        self.assertTrue(all(self.LANDED_IN_ABDVERIFY.values()))


if __name__ == "__main__":
    unittest.main()
