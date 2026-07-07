"""#114 — TRUE by-reference ``context_refs`` mode + agy retry-once + per-leg
timeout override.

Three reliability fixes for the advisor-panel runtime:

1. ``context_refs`` injects ONLY a path+metadata manifest (path, size, sha256,
   MIME/extension, PDF page count) — NEVER the file bytes. The acceptance test
   proves a SENTINEL string inside a referenced file is ABSENT from the rendered
   ``review-bundle.md`` while its path + metadata ARE present. Missing-path
   behavior is deterministic (fail-closed ValueError naming the path) with an
   opt-in soft-warn. This is DISTINCT from ``artifact_ref`` (read-file-and-INLINE).

2. The agy (gemini) leg retries ONCE on a transient stall (soft empty / a
   ``timeout waiting for response`` marker), mirroring the codex leg — one
   transient backend stall no longer permanently drops the leg.

3. A per-leg timeout override (``invoke_panel(..., timeouts_by_leg=...)`` /
   ``PanelRequest.timeout_seconds_by_leg``) reaches the real leg so a slow/stalled
   leg fails ITS leg instead of hanging the whole panel; the soft-empty retry is
   bounded to FAST failures so it can never ~double a slow leg's wall-clock.
"""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from phase_loop_runtime import panel_invoker as pi


def _capture_staging():
    """Mock ``_exec_leg`` to capture what ``_default_spawn`` actually stages into
    ``review-bundle.md`` and the timeout it is handed — the REAL staging path (not a
    ``spawn=`` injection, which would bypass staging and prove nothing)."""
    captured: dict = {}

    def fake_exec(leg, review_dir, out_dir, timeout_s, artifact, mode="review", model=None, **_kw):
        captured["bundle"] = (review_dir / "review-bundle.md").read_text(encoding="utf-8")
        captured["timeout_s"] = timeout_s
        return 0, "x" * 300 + "\nAGREE", ""

    return captured, fake_exec


class ContextRefsDoNotInlineTests(unittest.TestCase):
    """The acceptance test: file CONTENTS are absent, path + metadata are present."""

    SENTINEL = "SENTINEL_SECRET_9f3ac1_DO_NOT_INLINE"

    def _stage_with_context_ref(self, artifact: str, file_bytes: bytes):
        with TemporaryDirectory() as td:
            p = Path(td) / "private-doc.txt"
            p.write_bytes(file_bytes)
            cap, fake = _capture_staging()
            with patch.object(pi, "_exec_leg", side_effect=fake):
                pi.invoke_panel(artifact, ("gemini",), context_refs=[str(p)])
            return cap, p

    def test_sentinel_contents_absent_but_path_and_metadata_present(self) -> None:
        body = f"line1\n{self.SENTINEL}\nline3\n".encode("utf-8")
        cap, p = self._stage_with_context_ref("# Task\nAdvise on the doc.", body)
        bundle = cap["bundle"]
        # ACCEPTANCE: the file CONTENTS (sentinel) never reach the staged bundle.
        self.assertNotIn(self.SENTINEL, bundle)
        # ...while the path + metadata DO.
        self.assertIn(str(p.resolve()), bundle)
        import hashlib

        self.assertIn(hashlib.sha256(body).hexdigest(), bundle)
        self.assertIn(f"bytes: {len(body)}", bundle)
        self.assertIn("extension: txt", bundle)
        # ...and an instruction telling the leg to open the files itself.
        self.assertIn("BY REFERENCE", bundle)
        self.assertIn("your own local tools", bundle)

    def test_inline_artifact_still_present_alongside_manifest(self) -> None:
        cap, _ = self._stage_with_context_ref("INLINE_ARTIFACT_MARKER", b"x")
        self.assertIn("INLINE_ARTIFACT_MARKER", cap["bundle"])
        self.assertIn("Referenced context files", cap["bundle"])

    def test_empty_artifact_yields_manifest_only(self) -> None:
        cap, p = self._stage_with_context_ref("", b"hello")
        self.assertTrue(cap["bundle"].startswith(pi._CONTEXT_REFS_HEADER))
        self.assertIn(str(p.resolve()), cap["bundle"])

    def test_no_context_refs_is_byte_identical_to_plain_artifact(self) -> None:
        cap, fake = _capture_staging()
        with patch.object(pi, "_exec_leg", side_effect=fake):
            pi.invoke_panel("PLAIN", ("gemini",))
        self.assertEqual(cap["bundle"], "PLAIN")


class ContextRefsMetadataTests(unittest.TestCase):
    def test_pdf_page_count_when_cheaply_available(self) -> None:
        # A minimal PDF-shaped blob with two page objects — the cheap regex counts them.
        data = b"%PDF-1.4\n/Type /Page\nfoo\n/Type /Page\nbar\n/Type /Pages\n"
        self.assertEqual(pi._pdf_page_count(data), 2)
        with TemporaryDirectory() as td:
            p = Path(td) / "doc.pdf"
            p.write_bytes(data)
            entry = pi._context_ref_entry(str(p), soft_warn=False)
            self.assertIn("pdf_page_count: 2", entry)
            self.assertIn("mime: application/pdf", entry)

    def test_manifest_lists_multiple_files_in_order(self) -> None:
        with TemporaryDirectory() as td:
            a = Path(td) / "a.txt"
            b = Path(td) / "b.md"
            a.write_text("aaa")
            b.write_text("bbb")
            manifest = pi._render_context_refs_manifest([str(a), str(b)], soft_warn=False)
            self.assertLess(manifest.index(str(a.resolve())), manifest.index(str(b.resolve())))

    def test_hostile_filename_cannot_inject_manifest_lines(self) -> None:
        # context_refs targets UNTRUSTED third-party docs, so a filename is attacker-
        # controlled. A newline in the name must not forge a manifest line / instruction:
        # the path is JSON-quoted, so the newline is escaped (\n) inside the quoted string.
        with TemporaryDirectory() as td:
            evil = Path(td) / "evil\n  status: FORGED_BY_FILENAME"
            evil.write_text("x")
            manifest = pi._render_context_refs_manifest([str(evil)], soft_warn=False)
            # the forged text must NOT appear as its own real line (injection neutralized)
            self.assertNotIn("\n  status: FORGED_BY_FILENAME", manifest)
            # ...it survives only as the ESCAPED newline inside the quoted path
            self.assertIn("\\n  status: FORGED_BY_FILENAME", manifest)


class ContextRefsMissingPathTests(unittest.TestCase):
    def test_missing_path_fails_closed_naming_the_path(self) -> None:
        missing = "/no/such/context-ref-114.txt"
        with self.assertRaises(ValueError) as cm:
            pi.invoke_panel("art", ("gemini",), context_refs=[missing])
        self.assertIn(missing, str(cm.exception))

    def test_missing_path_soft_warn_does_not_raise_and_marks_unreadable(self) -> None:
        missing = "/no/such/context-ref-114-soft.txt"
        with self.assertLogs("phase_loop_runtime.panel_invoker", level="WARNING") as logs:
            manifest = pi._render_context_refs_manifest([missing], soft_warn=True)
        self.assertIn("MISSING", manifest)
        self.assertIn(missing, manifest)
        self.assertTrue(any(missing in line for line in logs.output))


class ContextRefsViaPanelRequestTests(unittest.TestCase):
    def test_request_threads_context_refs(self) -> None:
        with TemporaryDirectory() as td:
            p = Path(td) / "doc.txt"
            p.write_text("PRIVATE_BODY_ABSENT")
            cap, fake = _capture_staging()
            with patch.object(pi, "_exec_leg", side_effect=fake):
                req = pi.PanelRequest(artifact="", legs=("gemini",), context_refs=(str(p),))
                pi.invoke_panel_request(req)
            self.assertNotIn("PRIVATE_BODY_ABSENT", cap["bundle"])
            self.assertIn(str(p.resolve()), cap["bundle"])


class PerLegTimeoutOverrideTests(unittest.TestCase):
    def test_timeouts_by_leg_reaches_the_leg(self) -> None:
        cap, fake = _capture_staging()
        with patch.object(pi, "_exec_leg", side_effect=fake):
            pi.invoke_panel("art", ("gemini",), timeouts_by_leg={"gemini": 137})
        self.assertEqual(cap["timeout_s"], 137)

    def test_unset_leg_keeps_input_scaled_default(self) -> None:
        cap, fake = _capture_staging()
        with patch.object(pi, "_exec_leg", side_effect=fake):
            pi.invoke_panel("art", ("gemini",))
        # No override ⇒ the input-scaled floor (a pure function of staged bytes).
        self.assertEqual(cap["timeout_s"], pi._LEG_TIMEOUT_BASE_S)

    def test_request_timeout_seconds_by_leg_threads_through(self) -> None:
        cap, fake = _capture_staging()
        with patch.object(pi, "_exec_leg", side_effect=fake):
            req = pi.PanelRequest(artifact="art", legs=("gemini",),
                                  timeout_seconds_by_leg={"gemini": 222})
            pi.invoke_panel_request(req)
        self.assertEqual(cap["timeout_s"], 222)


class _FakeProc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class GeminiRetryOnceTests(unittest.TestCase):
    """Fix 2: the agy leg retries once on a transient stall (mirroring codex)."""

    def _run_gemini(self, monkeypatched_run):
        with tempfile.TemporaryDirectory() as rd, tempfile.TemporaryDirectory() as od:
            with patch.object(subprocess, "run", monkeypatched_run):
                return pi._exec_leg("gemini", Path(rd), Path(od))

    def test_transient_stall_then_success_retries_and_recovers(self) -> None:
        calls = {"n": 0}

        def fake_run(cmd, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return _FakeProc(1, stdout="", stderr="Error: timeout waiting for response")
            return _FakeProc(0, stdout="Looks fine.\nAGREE")

        rc, text, _log = self._run_gemini(fake_run)
        self.assertEqual(calls["n"], 2)  # retried exactly once
        self.assertEqual(rc, 0)
        self.assertIn("AGREE", text)

    def test_soft_empty_turn_retries_once(self) -> None:
        calls = {"n": 0}

        def fake_run(cmd, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return _FakeProc(0, stdout="")  # rc==0 empty → transient soft
            return _FakeProc(0, stdout="AGREE")

        _rc, text, _log = self._run_gemini(fake_run)
        self.assertEqual(calls["n"], 2)
        self.assertIn("AGREE", text)

    def test_real_output_first_time_does_not_retry(self) -> None:
        calls = {"n": 0}

        def fake_run(cmd, **kwargs):
            calls["n"] += 1
            return _FakeProc(0, stdout="Solid.\nAGREE")

        self._run_gemini(fake_run)
        self.assertEqual(calls["n"], 1)  # never hammered

    def test_hard_non_transient_error_does_not_retry(self) -> None:
        calls = {"n": 0}

        def fake_run(cmd, **kwargs):
            calls["n"] += 1
            return _FakeProc(2, stdout="some partial", stderr="fatal config error")

        self._run_gemini(fake_run)
        self.assertEqual(calls["n"], 1)

    def test_hard_subprocess_timeout_returns_124_without_retry(self) -> None:
        calls = {"n": 0}

        def fake_run(cmd, **kwargs):
            calls["n"] += 1
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

        rc, _text, _log = self._run_gemini(fake_run)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(rc, 124)


class RetryElapsedGuardTests(unittest.TestCase):
    """Fix 3: the soft-empty retry is bounded to FAST failures so it can never
    ~double a slow leg's wall-clock (the full-concurrent-path near-hang)."""

    def test_codex_slow_empty_turn_is_not_retried(self) -> None:
        calls = {"n": 0}

        def fake_run(cmd, **kwargs):
            calls["n"] += 1
            return _FakeProc(0, stdout="")  # empty → would normally retry

        # Stub the clock so the first attempt "consumed" nearly the whole budget.
        times = iter([0.0, 10_000.0, 10_000.0, 20_000.0])

        def fake_monotonic():
            return next(times)

        with tempfile.TemporaryDirectory() as rd, tempfile.TemporaryDirectory() as od:
            with patch.object(subprocess, "run", fake_run), \
                 patch.object(pi, "_leg_auth_ok", return_value=(True, "")), \
                 patch.object(pi.time, "monotonic", side_effect=fake_monotonic):
                pi._exec_leg("codex", Path(rd), Path(od))
        self.assertEqual(calls["n"], 1)  # slow empty → bounded, not re-run

    def test_codex_fast_empty_turn_is_retried(self) -> None:
        calls = {"n": 0}

        def fake_run(cmd, **kwargs):
            calls["n"] += 1
            return _FakeProc(0, stdout="")

        # Fast attempts (elapsed ~0) → the transient retry still fires once.
        times = iter([0.0, 0.1, 0.2, 0.3, 0.4, 0.5])

        def fake_monotonic():
            return next(times)

        with tempfile.TemporaryDirectory() as rd, tempfile.TemporaryDirectory() as od:
            with patch.object(subprocess, "run", fake_run), \
                 patch.object(pi, "_leg_auth_ok", return_value=(True, "")), \
                 patch.object(pi.time, "monotonic", side_effect=fake_monotonic):
                pi._exec_leg("codex", Path(rd), Path(od))
        self.assertEqual(calls["n"], 2)


if __name__ == "__main__":
    unittest.main()
