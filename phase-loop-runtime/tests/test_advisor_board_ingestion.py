"""Artifact-by-reference ingestion ("reference, don't inline").

Proves the caller→runtime boundary can be fed by PATH (`artifact_ref` /
`brief_ref`) with the runtime reading off disk, WITHOUT disturbing the
`artifact: str` back-compat byte-identity (the golden keystone lives in
``test_advisor_board_golden.py``; this file proves the new path is byte-transparent
against it):

* ``artifact_ref=P`` stages ``review-bundle.md`` byte-identical to
  ``artifact=<contents of P>`` — same staged bytes ⇒ same input-scaled timeout;
* multi-path ``artifact_ref`` concatenates deterministically (stable order +
  per-file header);
* a missing ref path raises ``ValueError`` naming it (fail-closed, not silent-empty);
* ``brief_ref`` stages as ``review-instructions.md``;
* the inline-size guard WARNS once (never refuses, never mutates) above the
  threshold and is silent at/below it and for a from-ref artifact;
* the scratch GC removes a stale ``pl-panel-*`` dir, preserves a fresh one, and
  swallows every error.
"""
from __future__ import annotations

import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from phase_loop_runtime import panel_invoker as pi


def _capture_staging():
    """Mock ``_exec_leg`` to capture what ``_default_spawn`` stages (bundle +
    instructions) and the input-scaled timeout — the real staging path, NOT a
    ``spawn=`` injection (which would bypass staging and prove nothing)."""
    captured: dict = {}

    def fake_exec(leg, review_dir, out_dir, timeout_s, artifact, mode="review", model=None, **_kw):
        captured["bundle"] = (review_dir / "review-bundle.md").read_text(encoding="utf-8")
        captured["instructions"] = (review_dir / "review-instructions.md").read_text(encoding="utf-8")
        captured["timeout_s"] = timeout_s
        return 0, "x" * 300 + "\nAGREE", ""

    return captured, fake_exec


class ArtifactRefByteIdentityTests(unittest.TestCase):
    """``artifact_ref=P`` is byte-transparent vs ``artifact=<contents of P>``."""

    def test_artifact_ref_stages_bundle_byte_equal_to_inline(self) -> None:
        content = "# Review bundle\n\nAcceptance: X.\nVerification: Y.\n"
        with TemporaryDirectory() as td:
            p = Path(td) / "bundle.md"
            p.write_text(content, encoding="utf-8")

            cap_ref, fake_ref = _capture_staging()
            with patch.object(pi, "_exec_leg", side_effect=fake_ref):
                pi.invoke_panel("", ("gemini",), artifact_ref=str(p))

            cap_inline, fake_inline = _capture_staging()
            with patch.object(pi, "_exec_leg", side_effect=fake_inline):
                pi.invoke_panel(content, ("gemini",))

        # Same staged bundle bytes AND same input-scaled timeout (timeout is a pure
        # function of staged size — identical bytes ⇒ identical timeout).
        self.assertEqual(cap_ref["bundle"], content)
        self.assertEqual(cap_ref["bundle"], cap_inline["bundle"])
        self.assertEqual(cap_ref["timeout_s"], cap_inline["timeout_s"])
        # Instructions default to the mode brief, unchanged.
        self.assertEqual(cap_ref["instructions"], cap_inline["instructions"])

    def test_single_path_returns_content_verbatim_no_header(self) -> None:
        with TemporaryDirectory() as td:
            p = Path(td) / "only.md"
            p.write_text("BODY-ONLY", encoding="utf-8")
            self.assertEqual(pi._resolve_artifact(None, str(p)), "BODY-ONLY")
            # A single-element sequence behaves like the bare string (no header).
            self.assertEqual(pi._resolve_artifact(None, [str(p)]), "BODY-ONLY")


class ResolveArtifactContractTests(unittest.TestCase):
    def test_none_ref_returns_artifact_verbatim(self) -> None:
        # The invariant the golden relies on structurally: no ref ⇒ identity.
        self.assertEqual(pi._resolve_artifact("hello", None), "hello")
        self.assertEqual(pi._resolve_artifact("", None), "")
        self.assertEqual(pi._resolve_artifact(None, None), "")

    def test_multi_path_concatenates_deterministically_with_headers(self) -> None:
        with TemporaryDirectory() as td:
            a = Path(td) / "a.md"
            b = Path(td) / "b.md"
            a.write_text("ALPHA", encoding="utf-8")
            b.write_text("BETA", encoding="utf-8")
            out = pi._resolve_artifact(None, [str(a), str(b)])
        self.assertEqual(out, "## a.md\nALPHA\n\n## b.md\nBETA")
        # Order is the caller's order, deterministically preserved.
        with TemporaryDirectory() as td:
            a = Path(td) / "a.md"
            b = Path(td) / "b.md"
            a.write_text("ALPHA", encoding="utf-8")
            b.write_text("BETA", encoding="utf-8")
            reversed_out = pi._resolve_artifact(None, [str(b), str(a)])
        self.assertEqual(reversed_out, "## b.md\nBETA\n\n## a.md\nALPHA")

    def test_missing_path_raises_valueerror_naming_it(self) -> None:
        missing = "/nonexistent/does-not-exist-bundle.md"
        with self.assertRaises(ValueError) as ctx:
            pi._resolve_artifact(None, missing)
        self.assertIn(missing, str(ctx.exception))

    def test_missing_path_in_multi_raises_naming_it(self) -> None:
        with TemporaryDirectory() as td:
            a = Path(td) / "a.md"
            a.write_text("ALPHA", encoding="utf-8")
            missing = str(Path(td) / "gone.md")
            with self.assertRaises(ValueError) as ctx:
                pi._resolve_artifact(None, [str(a), missing])
        self.assertIn(missing, str(ctx.exception))

    def test_artifact_ref_wins_over_inline_artifact(self) -> None:
        with TemporaryDirectory() as td:
            p = Path(td) / "ref.md"
            p.write_text("FROM-FILE", encoding="utf-8")
            self.assertEqual(pi._resolve_artifact("INLINE-IGNORED", str(p)), "FROM-FILE")

    def test_invoke_panel_missing_ref_fails_closed(self) -> None:
        # Fail-closed at the top of invoke_panel, never a silent (empty) review.
        with self.assertRaises(ValueError):
            pi.invoke_panel("", ("gemini",), artifact_ref="/no/such/path.md",
                            spawn=lambda leg, art: ("OK", "AGREE"))


class BriefRefStagingTests(unittest.TestCase):
    def test_brief_ref_stages_as_review_instructions(self) -> None:
        brief = "CUSTOM BRIEF — advise on the tradeoffs.\n"
        with TemporaryDirectory() as td:
            bp = Path(td) / "brief.md"
            bp.write_text(brief, encoding="utf-8")
            cap, fake = _capture_staging()
            with patch.object(pi, "_exec_leg", side_effect=fake):
                pi.invoke_panel("BODY", ("gemini",), brief_ref=str(bp))
        self.assertEqual(cap["instructions"], brief)

    def test_brief_ref_none_stages_mode_instructions(self) -> None:
        self.assertEqual(pi._resolve_brief("review", None), pi._mode_instructions("review"))
        self.assertEqual(pi._resolve_brief("advisory", None), pi._mode_instructions("advisory"))

    def test_missing_brief_ref_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            pi._resolve_brief("review", "/no/such/brief.md")
        self.assertIn("/no/such/brief.md", str(ctx.exception))


class InlineSizeGuardTests(unittest.TestCase):
    LOGGER = "phase_loop_runtime.panel_invoker"

    def test_large_inline_warns_exactly_once(self) -> None:
        big = "x" * (pi._MAX_INLINE_ARTIFACT_BYTES + 1)
        with self.assertLogs(self.LOGGER, level="WARNING") as cm:
            pi._maybe_warn_inline_size(big, from_ref=False)
        self.assertEqual(len(cm.records), 1)
        self.assertIn("artifact_ref", cm.output[0])

    def test_at_or_below_threshold_does_not_warn(self) -> None:
        exactly = "x" * pi._MAX_INLINE_ARTIFACT_BYTES
        with self.assertNoLogs(self.LOGGER, level="WARNING"):
            pi._maybe_warn_inline_size(exactly, from_ref=False)
            pi._maybe_warn_inline_size("small", from_ref=False)

    def test_from_ref_never_warns_even_when_large(self) -> None:
        big = "x" * (pi._MAX_INLINE_ARTIFACT_BYTES * 4)
        with self.assertNoLogs(self.LOGGER, level="WARNING"):
            pi._maybe_warn_inline_size(big, from_ref=True)

    def test_guard_never_mutates_content(self) -> None:
        big = "y" * (pi._MAX_INLINE_ARTIFACT_BYTES + 100)
        # The guard returns None and cannot change the caller's value; assert the
        # resolved artifact is unchanged end-to-end through the resolve+warn pair.
        resolved = pi._resolve_artifact(big, None)
        self.assertIsNone(pi._maybe_warn_inline_size(resolved, from_ref=False))
        self.assertEqual(resolved, big)

    def test_invoke_panel_emits_the_steering_warning(self) -> None:
        big = "z" * (pi._MAX_INLINE_ARTIFACT_BYTES + 1)
        with self.assertLogs(self.LOGGER, level="WARNING") as cm:
            pi.invoke_panel(big, ("gemini",), spawn=lambda leg, art: ("OK", "AGREE"))
        self.assertTrue(any("artifact_ref" in line for line in cm.output))


class PanelRequestRefTests(unittest.TestCase):
    LOGGER = "phase_loop_runtime.panel_invoker"

    def test_request_artifact_ref_resolves_and_does_not_warn_when_large(self) -> None:
        # A LARGE bundle loaded from a file via PanelRequest.artifact_ref must NOT
        # trip the inline-size warning (from_ref=True) — the by-reference entry point
        # must not scold the caller for doing exactly the right thing.
        big = "b" * (pi._MAX_INLINE_ARTIFACT_BYTES + 500)
        seen: dict = {}

        def spawn(leg, artifact):
            seen["artifact"] = artifact
            return ("OK", "AGREE")

        with TemporaryDirectory() as td:
            p = Path(td) / "big-bundle.md"
            p.write_text(big, encoding="utf-8")
            req = pi.PanelRequest(artifact="", artifact_ref=str(p), legs=("gemini",))
            with self.assertNoLogs(self.LOGGER, level="WARNING"):
                pi.invoke_panel_request(req, spawn=spawn)
        # Resolution actually happened (spawn saw the file content, not the empty
        # inline artifact).
        self.assertEqual(seen["artifact"], big)


class ScratchGCTests(unittest.TestCase):
    def _seed(self, root: Path, name: str, age_s: float) -> Path:
        d = root / name
        d.mkdir()
        (d / "review").mkdir()
        past = time.time() - age_s
        os.utime(d, (past, past))
        return d

    def test_removes_stale_and_preserves_fresh(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            stale = self._seed(root, "pl-panel-stale", age_s=48 * 3600)
            fresh = self._seed(root, "pl-panel-fresh", age_s=60)
            unrelated = self._seed(root, "other-tmpdir", age_s=48 * 3600)
            pi._gc_stale_panel_scratch(root, max_age_s=24 * 3600)
            self.assertFalse(stale.exists(), "stale pl-panel-* not reclaimed")
            self.assertTrue(fresh.exists(), "fresh pl-panel-* wrongly removed")
            self.assertTrue(unrelated.exists(), "non-pl-panel dir wrongly touched")

    def test_gc_swallows_errors_and_never_raises(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            self._seed(root, "pl-panel-stale", age_s=48 * 3600)
            with patch.object(pi.shutil, "rmtree", side_effect=OSError("boom")):
                # Must not propagate — GC is advisory hygiene, never affects a run.
                pi._gc_stale_panel_scratch(root, max_age_s=24 * 3600)

    def test_gc_on_missing_root_is_noop(self) -> None:
        # A non-existent root must not raise (best-effort).
        pi._gc_stale_panel_scratch(Path("/nonexistent/scratch/root"), max_age_s=1)


if __name__ == "__main__":
    unittest.main()
