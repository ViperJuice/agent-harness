"""model-routing-v2 P2 — real panel spawn (codex + gemini, fail-closed).

No live frontier CLI is ever called: the single subprocess boundary
`panel_invoker._exec_leg` is stubbed. We assert the status mapping, the
claude-leg deferral, bundle staging, subscription-only env, and reviewer≠author.
"""
import os
import unittest
from unittest.mock import patch

from phase_loop_runtime import panel_invoker as pi
from phase_loop_runtime.governed_review import select_reviewer_pool


class ClaudeLegDeferredTest(unittest.TestCase):
    def test_claude_leg_unavailable(self):
        self.assertEqual(pi._default_spawn("claude", "bundle"), ("unavailable", ""))


class StatusMappingTest(unittest.TestCase):
    def _spawn_with(self, rc, review_text, log_text):
        with patch.object(pi, "_exec_leg", return_value=(rc, review_text, log_text)):
            return pi._default_spawn("codex", "bundle")

    def test_ok(self):
        status, text = self._spawn_with(0, "A real review. " * 30 + "\nAGREE", "")
        self.assertEqual(status, "ok")
        self.assertIn("AGREE", text)

    def test_empty(self):
        self.assertEqual(self._spawn_with(0, "tiny", "")[0], "empty")  # <=200 bytes, no verdict

    def test_terse_verdict_is_ok_not_empty(self):
        # A real but terse block (~35 bytes) carries the structured verdict and must
        # classify `ok`, not `empty` — else a genuine DISAGREE silently downgrades to
        # a non-gating warn (code-review finding #2, verified).
        status, text = self._spawn_with(0, "DISAGREE — the endpoint skips auth", "")
        self.assertEqual(status, "ok")
        self.assertIn("DISAGREE", text)

    def test_degraded_on_auth_signature(self):
        self.assertEqual(self._spawn_with(0, "x" * 300, "error: not logged in; please run codex login")[0], "degraded")

    def test_timeout_rc124(self):
        self.assertEqual(self._spawn_with(124, "", "")[0], "timeout")

    def test_exec_exception_degrades(self):
        with patch.object(pi, "_exec_leg", side_effect=RuntimeError("boom")):
            self.assertEqual(pi._default_spawn("gemini", "bundle")[0], "degraded")


class BundleStagingTest(unittest.TestCase):
    def test_bundle_and_instructions_staged_readonly_dir(self):
        captured = {}

        def fake_exec(leg, review_dir, out_dir):
            captured["bundle"] = (review_dir / "review-bundle.md").read_text(encoding="utf-8")
            captured["instructions_exists"] = (review_dir / "review-instructions.md").exists()
            captured["out_separate"] = out_dir != review_dir
            return 0, "x" * 300 + "\nAGREE", ""

        with patch.object(pi, "_exec_leg", side_effect=fake_exec):
            status, _ = pi._default_spawn("gemini", "BUNDLE-CONTENT")
        self.assertEqual(status, "ok")
        self.assertEqual(captured["bundle"], "BUNDLE-CONTENT")
        self.assertTrue(captured["instructions_exists"])
        self.assertTrue(captured["out_separate"])


class SubscriptionAuthTest(unittest.TestCase):
    def test_api_keys_stripped(self):
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "sk-x", "ANTHROPIC_API_KEY": "sk-y", "GEMINI_API_KEY": "g",
            "PATH": os.environ.get("PATH", ""),
        }):
            env = pi._subscription_env()
        for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
            self.assertNotIn(var, env)
        self.assertIn("PATH", env)  # non-key env preserved


class InvokePanelFailClosedTest(unittest.TestCase):
    def test_degraded_and_empty_legs_are_not_usable(self):
        def spawn(leg, artifact):
            return {"codex": ("ok", "Solid review. " * 20 + "\nAGREE"),
                    "gemini": ("degraded", ""),
                    "claude": ("unavailable", "")}[leg]
        panel = pi.invoke_panel("b", ("codex", "gemini", "claude"), spawn=spawn)
        usable = {leg.leg for leg in panel.usable_legs}
        self.assertEqual(usable, {"codex"})  # only the ok leg with text is usable


class ReviewerNeqAuthorTest(unittest.TestCase):
    def test_claude_author_reviewed_by_codex_gemini(self):
        pool, degraded = select_reviewer_pool("claude", ("codex", "gemini", "claude"))
        self.assertEqual(set(pool), {"codex", "gemini"})
        self.assertIsNone(degraded)

    def test_codex_author_has_disjoint_reviewer(self):
        pool, _ = select_reviewer_pool("codex", ("codex", "gemini", "claude"))
        self.assertNotIn("codex", pool)
        self.assertIn("gemini", pool)  # a usable disjoint vendor even with claude deferred


if __name__ == "__main__":
    unittest.main()
