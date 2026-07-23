"""FAB Lane C (Consiliency/agent-harness#191) — delta-chain binding,
carry-forward, boundary-manifest escalation, and `review_scope` enforcement.
Deliberately UNMARKED (no ``dotfiles_integration``), so CI's
``-m "not dotfiles_integration"`` runs this module (the goal-id-inc2 lesson).
Uses REAL temporary git repositories for every path where the base-pinned
manifest read matters — no mocked git."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from phase_loop_runtime import fab_canonical as fc
from phase_loop_runtime import fab_delta as fd
from phase_loop_runtime import fab_provenance as fp
from phase_loop_runtime.convergence.broker import credsep

_GIT = shutil.which("git")


def _run(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        raise AssertionError(f"git {args} failed: {result.stderr}")
    return result


def _rev_parse(repo: Path, ref: str = "HEAD") -> str:
    return _run(repo, "rev-parse", ref).stdout.strip()


def _content_ref(byte: str) -> str:
    return f"sha256:{byte * 64}"


class GitRepoTestCase(unittest.TestCase):
    """A plain local working tree (no remotes needed — Lane C never calls
    `fab_canonical.equivalent()`, which is the only Lane B entry point that
    fetches from a remote)."""

    REPO_SLUG = "github.com/testorg/testrepo"

    def setUp(self) -> None:
        if _GIT is None:  # pragma: no cover - CI always has git
            self.skipTest("git not available")
        self._tmp = tempfile.mkdtemp(prefix="fab-delta-c-")
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))
        self.repo = Path(self._tmp) / "work"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        _run(self.repo, "config", "user.email", "t@example.com")
        _run(self.repo, "config", "user.name", "Test")

    def write(self, relpath: str, content: bytes | str) -> Path:
        path = self.repo / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            content = content.encode("utf-8")
        path.write_bytes(content)
        return path

    def rm(self, relpath: str) -> None:
        (self.repo / relpath).unlink()

    def commit(self, message: str) -> str:
        _run(self.repo, "add", "-A")
        _run(self.repo, "commit", "-q", "--allow-empty", "-m", message)
        return _rev_parse(self.repo)

    def digest(self, base_sha: str, head_sha: str) -> str:
        return fc.patch_digest(self.repo, base_sha, head_sha, repo_slug=self.REPO_SLUG)


_STRONG_MANIFEST = """
[auth_security]
globs = ["**/auth/**", "**/*secret*"]

[deployment]
globs = ["**/deploy/**", ".github/workflows/**"]
""".strip()


_WEAKENED_MANIFEST = """
[deployment]
globs = ["**/deploy/**", ".github/workflows/**"]
""".strip()  # auth_security section removed


def _seat(*, finding_ids: tuple[str, ...], verdict: str | None = "AGREE") -> fp.ProvenanceSeat:
    return fp.ProvenanceSeat(
        seat_key="codex:gpt-5.6-sol:high",
        vendor_leg="codex",
        required=True,
        status="ok",
        epoch=1,
        artifact_digest="1" * 64,
        evidence_digest="2" * 64,
        verdict=verdict,
        finding_ids=finding_ids,
    )


def _finding(id_: str, *, status: str = "clean", path_scope: tuple[str, ...] = ()) -> fp.Finding:
    return fp.Finding(id=id_, severity="advisory", status=status, path_scope=path_scope, body_ref=_content_ref("0"))


def _delta_review_scope(*, mode: str = fp.REVIEW_SCOPE_DELTA_ONLY, covers: str | None = None) -> fp.ReviewScope:
    return fp.ReviewScope(mode=mode, reviewed_material_digest=None, covers_patch_digest=covers)


# --------------------------------------------------------------------------- #
# Glob semantics (IF-0-FAB-C-1) — unit-level regression for the frozen
# translation, beyond the manual smoke-check done during development.
# --------------------------------------------------------------------------- #


class GlobSemanticsTest(unittest.TestCase):
    def _match(self, pattern: str, path: str) -> bool:
        return fd._translate_glob_to_regex(pattern).match(path) is not None

    def test_globstar_matches_zero_or_more_segments_including_root(self):
        self.assertTrue(self._match("**/contracts/**", "a/b/contracts/c/d.py"))
        self.assertTrue(self._match("**/contracts/**", "contracts/x.py"))
        self.assertTrue(self._match("**/contracts/**", "contracts"))
        self.assertFalse(self._match("**/contracts/**", "xcontracts/y.py"))
        self.assertFalse(self._match("**/contracts/**", "contractsx/y.py"))

    def test_no_implicit_prefix(self):
        """design §5.4: 'no implicit prefix' — a bare `Dockerfile*` (the
        design's own example) matches only a repo-ROOT Dockerfile, never a
        nested one."""
        self.assertTrue(self._match("Dockerfile*", "Dockerfile"))
        self.assertTrue(self._match("Dockerfile*", "Dockerfile.arm64"))
        self.assertFalse(self._match("Dockerfile*", "sub/Dockerfile"))

    def test_case_sensitive(self):
        self.assertTrue(self._match("**/Dockerfile", "sub/Dockerfile"))
        self.assertFalse(self._match("**/Dockerfile", "sub/dockerfile"))

    def test_star_does_not_cross_segment_boundary(self):
        # A bare "*" (no adjacent "**") is bounded to ONE path segment.
        self.assertTrue(self._match("src/*.proto", "src/c.proto"))
        self.assertFalse(self._match("src/*.proto", "src/sub/c.proto"))
        # "**/*.proto" DOES match a deeply nested file: the leading "**/"
        # spans "a/b/c/" (arbitrary depth) and "*.proto" only needs to match
        # the FINAL segment "d.proto" — this is the globstar unit doing the
        # depth-spanning, not the trailing "*" itself.
        self.assertTrue(self._match("**/*.proto", "a/b/c.proto"))
        self.assertTrue(self._match("**/*.proto", "a/b/c/d.proto"))

    def test_malformed_glob_rejects(self):
        for bad in ("", "/abs/path", "a/../b", "a[bc]", "a{b,c}", "a b"):
            with self.assertRaises(fd.BoundaryManifestInvalid):
                fd._translate_glob_to_regex(bad)

    def test_globstar_matches_across_embedded_newline(self):
        """agent-harness#191 CR finding 1: git permits NEWLINE characters in
        filenames and the `-z` + `os.fsdecode` path preserves them verbatim.
        A boundary glob must still match a path whose only difference from a
        covered path is an embedded `\\n` in a segment `**` needs to span —
        otherwise a protected-surface delta escapes escalation entirely."""
        self.assertTrue(self._match("**/auth/**", "src\n/auth/login.py"))
        self.assertTrue(self._match("auth/**", "auth/login\n.py"))
        self.assertTrue(self._match("**/*secret*", "a/b\nc/my\nsecret.txt"))


# --------------------------------------------------------------------------- #
# Boundary-manifest load — base-pinned read (T15), present/missing/malformed
# --------------------------------------------------------------------------- #


class BoundaryManifestLoadTest(GitRepoTestCase):
    def test_present_well_formed_manifest_loads(self):
        self.write(fd.BOUNDARY_MANIFEST_PATH, _STRONG_MANIFEST)
        base = self.commit("c1 with manifest")
        load = fd.load_boundary_manifest_at_base(self.repo, base)
        self.assertEqual(load.disposition, fd.MANIFEST_DISPOSITION_PRESENT)
        self.assertIsNotNone(load.manifest)
        self.assertEqual(set(load.manifest.sections), {"auth_security", "deployment"})
        self.assertEqual(load.ref.source_rev, base)
        self.assertEqual(load.ref.path, fd.BOUNDARY_MANIFEST_PATH)

    def test_missing_manifest_disposition(self):
        base = self.commit("c1 (no manifest at all)")
        load = fd.load_boundary_manifest_at_base(self.repo, base)
        self.assertEqual(load.disposition, fd.MANIFEST_DISPOSITION_MISSING)
        self.assertIsNone(load.manifest)
        self.assertEqual(load.ref.digest, fd.NO_MANIFEST_DIGEST)

    def test_malformed_toml_disposition(self):
        self.write(fd.BOUNDARY_MANIFEST_PATH, "this is not [valid toml")
        base = self.commit("c1 malformed manifest")
        load = fd.load_boundary_manifest_at_base(self.repo, base)
        self.assertEqual(load.disposition, fd.MANIFEST_DISPOSITION_MALFORMED)
        self.assertIsNone(load.manifest)
        self.assertNotEqual(load.ref.digest, fd.NO_MANIFEST_DIGEST)  # real content hash, not the sentinel

    def test_malformed_shape_disposition_missing_globs_key(self):
        self.write(fd.BOUNDARY_MANIFEST_PATH, '[auth_security]\nnotglobs = ["x"]\n')
        base = self.commit("c1 malformed shape")
        load = fd.load_boundary_manifest_at_base(self.repo, base)
        self.assertEqual(load.disposition, fd.MANIFEST_DISPOSITION_MALFORMED)

    def test_malformed_glob_within_manifest_disposition(self):
        self.write(fd.BOUNDARY_MANIFEST_PATH, '[auth_security]\nglobs = ["a[bc]"]\n')
        base = self.commit("c1 malformed glob")
        load = fd.load_boundary_manifest_at_base(self.repo, base)
        self.assertEqual(load.disposition, fd.MANIFEST_DISPOSITION_MALFORMED)

    def test_declared_section_with_empty_globs_is_malformed_not_present(self):
        """Round-2 CR (gemini) hypothesized a residual fail-open: a manifest
        declaring a section with an EMPTY `globs` list (`[auth]\\nglobs =
        []`) parses to a NON-empty `sections={"auth": ()}`, so the finding-2
        `if not sections:` zero-sections check (see
        `test_comment_only_manifest_is_malformed_not_present` above) would
        never fire, and `evaluate_boundary_escalation` would iterate zero
        compiled patterns and return `required=False` — silently permitting
        carry-forward with a declared-but-neutered protected surface.

        THIS DOES NOT REPRODUCE: `_parse_boundary_manifest_bytes`'s
        per-section shape check (`not isinstance(globs, list) or not globs or
        not all(isinstance(g, str) for g in globs)`) already rejects an empty
        `globs` list at parse time, on EVERY section, unconditionally — and
        has done so since the Lane C module's original commit (`2280a01`),
        which predates both the finding-2 fix and this round-2 finding. The
        loop over `data.items()` raises `BoundaryManifestInvalid` the moment
        it reaches the empty-globs section, so `sections`/`compiled` are
        never populated and the manifest never reaches PRESENT disposition.
        This test pins that existing fail-closed behavior as a regression
        guard so nobody accidentally loosens the `not globs` clause later."""
        self.write(fd.BOUNDARY_MANIFEST_PATH, "[auth]\nglobs = []\n")
        base = self.commit("c1 declared section, empty globs")
        load = fd.load_boundary_manifest_at_base(self.repo, base)
        self.assertEqual(load.disposition, fd.MANIFEST_DISPOSITION_MALFORMED)
        self.assertIsNone(load.manifest)

    def test_all_sections_with_empty_globs_is_malformed_not_present(self):
        """Same non-reproduction as above, but with multiple declared
        sections that are ALL neutered to empty globs lists — still caught
        by the same per-section check on whichever section the (insertion-
        ordered) loop reaches first."""
        self.write(fd.BOUNDARY_MANIFEST_PATH, "[auth]\nglobs = []\n\n[deployment]\nglobs = []\n")
        base = self.commit("c1 all sections empty globs")
        load = fd.load_boundary_manifest_at_base(self.repo, base)
        self.assertEqual(load.disposition, fd.MANIFEST_DISPOSITION_MALFORMED)
        self.assertIsNone(load.manifest)

    def test_one_downgraded_section_among_real_ones_is_malformed_not_present(self):
        """Partial-downgrade variant: most sections are genuinely populated,
        but one (`auth`) has been emptied to `globs = []` to neutralize just
        that protected surface while leaving the manifest otherwise
        well-formed. The per-section check rejects the WHOLE manifest
        (fail-closed at the manifest granularity — there is no partial-valid
        manifest), so this downgrade is closed the same way as the
        all-empty case, not silently accepted for the populated sections."""
        self.write(
            fd.BOUNDARY_MANIFEST_PATH,
            '[deployment]\nglobs = ["**/deploy/**"]\n\n[auth]\nglobs = []\n',
        )
        base = self.commit("c1 one downgraded section among real ones")
        load = fd.load_boundary_manifest_at_base(self.repo, base)
        self.assertEqual(load.disposition, fd.MANIFEST_DISPOSITION_MALFORMED)
        self.assertIsNone(load.manifest)

    def test_empty_globs_section_forces_escalate_every_delta(self):
        """End-to-end companion to the three tests above: gemini's exact
        example (`[auth]\\nglobs = []` as the manifest's only section) must
        force whole-patch escalation on an otherwise-unrelated delta, exactly
        like a missing/genuinely-malformed manifest — never
        `required=False`."""
        self.write(fd.BOUNDARY_MANIFEST_PATH, "[auth]\nglobs = []\n")
        base = self.commit("c1 declared section, empty globs")
        load = fd.load_boundary_manifest_at_base(self.repo, base)
        esc = fd.evaluate_boundary_escalation(load, ("totally/unrelated.py",))
        self.assertTrue(esc.required)
        self.assertEqual(esc.trigger, fd.ESCALATION_TRIGGER_MALFORMED_MANIFEST)

    def test_legitimate_manifest_with_real_globs_everywhere_still_works(self):
        """No-regression check: a well-formed manifest where every declared
        section has real, non-empty globs is unaffected by the above —
        PRESENT disposition, and a disjoint (non-boundary) delta still does
        NOT force escalation."""
        self.write(fd.BOUNDARY_MANIFEST_PATH, _STRONG_MANIFEST)
        base = self.commit("c1 legitimate manifest")
        load = fd.load_boundary_manifest_at_base(self.repo, base)
        self.assertEqual(load.disposition, fd.MANIFEST_DISPOSITION_PRESENT)
        self.assertIsNotNone(load.manifest)
        esc = fd.evaluate_boundary_escalation(load, ("src/unrelated/thing.py",))
        self.assertFalse(esc.required)
        self.assertIsNone(esc.trigger)

    def test_empty_file_manifest_is_malformed_not_present(self):
        """agent-harness#191 CR finding 2: a zero-byte
        `.advisor-board/boundaries.toml` parses to `{}` (valid TOML, no
        sections) and must NOT be classified PRESENT+valid — that would let
        `evaluate_boundary_escalation` see zero compiled sections and return
        `required=False`, silently permitting carry-forward with "no
        boundaries" in force. It must resolve to MALFORMED (same fail-closed
        disposition as missing/malformed) instead."""
        self.write(fd.BOUNDARY_MANIFEST_PATH, "")
        base = self.commit("c1 empty manifest file")
        load = fd.load_boundary_manifest_at_base(self.repo, base)
        self.assertEqual(load.disposition, fd.MANIFEST_DISPOSITION_MALFORMED)
        self.assertIsNone(load.manifest)

    def test_comment_only_manifest_is_malformed_not_present(self):
        """Same as above but for a manifest containing only TOML comments —
        also parses to `{}` with zero sections."""
        self.write(fd.BOUNDARY_MANIFEST_PATH, "# nothing to see here\n# just comments\n")
        base = self.commit("c1 comment-only manifest")
        load = fd.load_boundary_manifest_at_base(self.repo, base)
        self.assertEqual(load.disposition, fd.MANIFEST_DISPOSITION_MALFORMED)
        self.assertIsNone(load.manifest)

    def test_empty_manifest_forces_escalate_every_delta(self):
        """End-to-end: an empty manifest must force whole-patch escalation on
        every delta, exactly like a missing/malformed one."""
        self.write(fd.BOUNDARY_MANIFEST_PATH, "# comment-only, zero sections\n")
        base = self.commit("c1 comment-only manifest")
        load = fd.load_boundary_manifest_at_base(self.repo, base)
        esc = fd.evaluate_boundary_escalation(load, ("totally/unrelated.py",))
        self.assertTrue(esc.required)
        self.assertEqual(esc.trigger, fd.ESCALATION_TRIGGER_MALFORMED_MANIFEST)

    def test_manifest_read_at_base_not_at_a_later_tip(self):
        """T15 core mechanism: reading at `base_sha` must NOT see a manifest
        edit that only exists at a LATER commit."""
        self.write(fd.BOUNDARY_MANIFEST_PATH, _STRONG_MANIFEST)
        base = self.commit("c1 strong manifest")
        self.write(fd.BOUNDARY_MANIFEST_PATH, _WEAKENED_MANIFEST)
        later = self.commit("c2 weakened manifest (later tip)")
        self.assertNotEqual(base, later)

        load_at_base = fd.load_boundary_manifest_at_base(self.repo, base)
        load_at_later = fd.load_boundary_manifest_at_base(self.repo, later)
        self.assertIn("auth_security", load_at_base.manifest.sections)
        self.assertNotIn("auth_security", load_at_later.manifest.sections)


# --------------------------------------------------------------------------- #
# Escalation decision (design §5.4)
# --------------------------------------------------------------------------- #


class EscalationDecisionTest(GitRepoTestCase):
    def test_no_match_no_escalation(self):
        self.write(fd.BOUNDARY_MANIFEST_PATH, _STRONG_MANIFEST)
        base = self.commit("c1")
        load = fd.load_boundary_manifest_at_base(self.repo, base)
        esc = fd.evaluate_boundary_escalation(load, ("src/unrelated/thing.py",))
        self.assertFalse(esc.required)
        self.assertIsNone(esc.trigger)

    def test_glob_match_forces_escalation_with_section_trigger(self):
        self.write(fd.BOUNDARY_MANIFEST_PATH, _STRONG_MANIFEST)
        base = self.commit("c1")
        load = fd.load_boundary_manifest_at_base(self.repo, base)
        esc = fd.evaluate_boundary_escalation(load, ("src/auth/login.py",))
        self.assertTrue(esc.required)
        self.assertEqual(esc.trigger, "auth_security")

    def test_manifest_path_touch_forces_escalation_regardless_of_content(self):
        self.write(fd.BOUNDARY_MANIFEST_PATH, _STRONG_MANIFEST)
        base = self.commit("c1")
        load = fd.load_boundary_manifest_at_base(self.repo, base)
        esc = fd.evaluate_boundary_escalation(load, (fd.BOUNDARY_MANIFEST_PATH, "unrelated.py"))
        self.assertTrue(esc.required)
        self.assertEqual(esc.trigger, fd.ESCALATION_TRIGGER_MANIFEST_MODIFIED)

    def test_manual_escalation_is_typed_not_prose(self):
        self.write(fd.BOUNDARY_MANIFEST_PATH, _STRONG_MANIFEST)
        base = self.commit("c1")
        load = fd.load_boundary_manifest_at_base(self.repo, base)
        esc = fd.evaluate_boundary_escalation(
            load, ("unrelated.py",), manual_trigger="reviewer:codex:gpt-5.6-sol:high"
        )
        self.assertTrue(esc.required)
        self.assertEqual(esc.trigger, "reviewer:codex:gpt-5.6-sol:high")

    def test_manual_escalation_rejects_untyped_trigger(self):
        self.write(fd.BOUNDARY_MANIFEST_PATH, _STRONG_MANIFEST)
        base = self.commit("c1")
        load = fd.load_boundary_manifest_at_base(self.repo, base)
        with self.assertRaises(fd.EscalationInvalid):
            fd.evaluate_boundary_escalation(load, ("unrelated.py",), manual_trigger="because I said so")

    def test_no_manifest_escalates_every_delta(self):
        base = self.commit("c1 (no manifest)")
        load = fd.load_boundary_manifest_at_base(self.repo, base)
        esc = fd.evaluate_boundary_escalation(load, ("totally/unrelated.py",))
        self.assertTrue(esc.required)
        self.assertEqual(esc.trigger, fd.ESCALATION_TRIGGER_NO_MANIFEST)

    def test_malformed_manifest_escalates_every_delta(self):
        self.write(fd.BOUNDARY_MANIFEST_PATH, "not [valid toml")
        base = self.commit("c1 malformed")
        load = fd.load_boundary_manifest_at_base(self.repo, base)
        esc = fd.evaluate_boundary_escalation(load, ("totally/unrelated.py",))
        self.assertTrue(esc.required)
        self.assertEqual(esc.trigger, fd.ESCALATION_TRIGGER_MALFORMED_MANIFEST)


# --------------------------------------------------------------------------- #
# Carry-forward (design §5.3) — reuses the broker's OWN disjointness test
# --------------------------------------------------------------------------- #


class CarryForwardTest(unittest.TestCase):
    def test_disjoint_clean_finding_carries_forward(self):
        findings = [_finding("f1", status="clean", path_scope=("pkg/a.py",))]
        result = fd.carry_forward(findings, ("pkg/b.py",))
        self.assertEqual(result.carried_forward_finding_ids, ("f1",))
        self.assertEqual(result.reopened_finding_ids, ())
        self.assertEqual(result.reasons["f1"], fd.CARRY_FORWARD_REASON_DISJOINT)

    def test_intersecting_path_scope_reopens(self):
        findings = [_finding("f1", status="clean", path_scope=("pkg/a.py",))]
        result = fd.carry_forward(findings, ("pkg/a.py",))
        self.assertEqual(result.carried_forward_finding_ids, ())
        self.assertEqual(result.reopened_finding_ids, ("f1",))
        self.assertEqual(result.reasons["f1"], fd.CARRY_FORWARD_REASON_INTERSECTS)

    def test_directory_scope_intersects_nested_changed_path(self):
        findings = [_finding("f1", status="clean", path_scope=("pkg/",))]
        result = fd.carry_forward(findings, ("pkg/nested/file.py",))
        self.assertEqual(result.reopened_finding_ids, ("f1",))

    def test_empty_path_scope_never_carries(self):
        findings = [_finding("f1", status="clean", path_scope=())]
        result = fd.carry_forward(findings, ("unrelated/other.py",))
        self.assertEqual(result.carried_forward_finding_ids, ())
        self.assertEqual(result.reopened_finding_ids, ("f1",))
        self.assertEqual(result.reasons["f1"], fd.CARRY_FORWARD_REASON_EMPTY_SCOPE)

    def test_empty_string_path_scope_entry_never_carries(self):
        """agent-harness#191 CR finding 3: `path_scope=("",)` is a NON-EMPTY
        sequence containing an empty string. The old guard (`if not
        f.path_scope`) only checked sequence length, so this passed it — and
        the broker's `_covered_by_owned` SKIPS a blank `owned` entry
        (credsep.py:204-208's `if owned and (...)`), so the finding was
        classified DISJOINT and carried forward without ever actually
        scoping anything. Must reopen with `empty_path_scope` instead."""
        findings = [_finding("f1", status="clean", path_scope=("",))]
        result = fd.carry_forward(findings, ("some/changed/path.py",))
        self.assertEqual(result.carried_forward_finding_ids, ())
        self.assertEqual(result.reopened_finding_ids, ("f1",))
        self.assertEqual(result.reasons["f1"], fd.CARRY_FORWARD_REASON_EMPTY_SCOPE)

    def test_whitespace_only_path_scope_entry_never_carries(self):
        findings = [_finding("f1", status="clean", path_scope=("  ",))]
        result = fd.carry_forward(findings, ("some/changed/path.py",))
        self.assertEqual(result.carried_forward_finding_ids, ())
        self.assertEqual(result.reopened_finding_ids, ("f1",))
        self.assertEqual(result.reasons["f1"], fd.CARRY_FORWARD_REASON_EMPTY_SCOPE)

    def test_slash_only_path_scope_entry_never_carries(self):
        """A residual variant of finding 3: `_covered_by_owned` applies its
        OWN `.rstrip("/")` to each owned entry before checking truthiness
        (credsep.py:206), so an ALL-SLASH entry like `"/"` or `"//"` also
        becomes empty and gets silently skipped by the matcher -- the same
        bypass class as `("",)`, just reached via a different string. The
        guard must mirror `_covered_by_owned`'s own emptiness test
        (`.rstrip("/")`), not merely `.strip()`."""
        for scope in (("/",), ("//",), (" / ",)):
            with self.subTest(scope=scope):
                findings = [_finding("f1", status="clean", path_scope=scope)]
                result = fd.carry_forward(findings, ("some/changed/path.py",))
                self.assertEqual(result.carried_forward_finding_ids, ())
                self.assertEqual(result.reopened_finding_ids, ("f1",))
                self.assertEqual(result.reasons["f1"], fd.CARRY_FORWARD_REASON_EMPTY_SCOPE)

    def test_legitimate_directory_scope_with_trailing_slash_still_carries(self):
        """Sanity check that the blank-entry guard does not over-trigger: a
        NORMAL directory-scope entry like `"pkg/"` (trailing slash is a
        legitimate directory marker, not a blank entry) must still carry
        forward when disjoint from the delta."""
        findings = [_finding("f1", status="clean", path_scope=("pkg/",))]
        result = fd.carry_forward(findings, ("other/unrelated.py",))
        self.assertEqual(result.carried_forward_finding_ids, ("f1",))
        self.assertEqual(result.reasons["f1"], fd.CARRY_FORWARD_REASON_DISJOINT)

    def test_mixed_blank_and_real_path_scope_entry_never_carries(self):
        """A path_scope containing ONE blank entry alongside a real one must
        still fail closed -- the blank entry alone is disqualifying."""
        findings = [_finding("f1", status="clean", path_scope=("pkg/a.py", ""))]
        result = fd.carry_forward(findings, ("totally/unrelated.py",))
        self.assertEqual(result.carried_forward_finding_ids, ())
        self.assertEqual(result.reopened_finding_ids, ("f1",))
        self.assertEqual(result.reasons["f1"], fd.CARRY_FORWARD_REASON_EMPTY_SCOPE)

    def test_non_clean_finding_excluded_from_both_buckets(self):
        findings = [_finding("f1", status="open", path_scope=("pkg/a.py",))]
        result = fd.carry_forward(findings, ("unrelated.py",))
        self.assertEqual(result.carried_forward_finding_ids, ())
        self.assertEqual(result.reopened_finding_ids, ())
        self.assertEqual(result.reasons["f1"], fd.CARRY_FORWARD_REASON_NOT_CLEAN)

    def test_suppressed_by_escalation_reopens_everything_clean(self):
        findings = [
            _finding("f1", status="clean", path_scope=("pkg/a.py",)),
            _finding("f2", status="clean", path_scope=("other/b.py",)),
        ]
        result = fd.carry_forward(findings, ("totally/unrelated.py",), suppress=True)
        self.assertEqual(result.carried_forward_finding_ids, ())
        self.assertEqual(sorted(result.reopened_finding_ids), ["f1", "f2"])
        self.assertTrue(all(r == fd.CARRY_FORWARD_REASON_SUPPRESSED for r in result.reasons.values()))

    def test_reuses_the_actual_broker_matcher_not_a_reimplementation(self):
        """Assert the broker's `_covered_by_owned` (credsep.py:190) is the
        ACTUAL function invoked by `carry_forward`, not a parallel
        re-implementation — spy on the real bound method."""
        self.assertIs(fd.GitHubBrokerAdapter, credsep.GitHubBrokerAdapter)
        findings = [_finding("f1", status="clean", path_scope=("pkg/a.py",))]
        with mock.patch.object(
            fd.GitHubBrokerAdapter,
            "_covered_by_owned",
            wraps=credsep.GitHubBrokerAdapter._covered_by_owned,
        ) as spy:
            fd.carry_forward(findings, ("pkg/b.py",))
            spy.assert_called_once_with("pkg/b.py", ("pkg/a.py",))

    def test_is_carry_forward_eligible_only_at_reviewed_clean(self):
        base_kwargs = dict(
            policy=None,
            review_scope=_delta_review_scope(),
            material_digests=(),
            parent_digest="a" * 64,
            parent_chain_digest="b" * 64,
            delta_head_sha="c" * 40,
            delta_changed_paths=(),
            delta_commits=(),
            resolved_finding_ids=(),
            carried_forward_finding_ids=(),
            reopened_finding_ids=(),
            resulting_head_digest="d" * 64,
            escalation=fp.Escalation(required=False, trigger=None),
        )
        clean = fp.DeltaReviewRecord.build(status=fp.DELTA_STATUS_REVIEWED_CLEAN, **base_kwargs)
        pending = fp.DeltaReviewRecord.build(status=fp.DELTA_STATUS_PENDING, **base_kwargs)
        self.assertTrue(fd.is_carry_forward_eligible(clean))
        self.assertFalse(fd.is_carry_forward_eligible(pending))


# --------------------------------------------------------------------------- #
# T4 — resolved-finding-claim corroboration
# --------------------------------------------------------------------------- #


class ResolvedClaimCorroborationTest(unittest.TestCase):
    def test_uncorroborated_claim_rejected(self):
        with self.assertRaises(fd.ResolvedClaimUnverified):
            fd.require_seat_corroboration(("f1",), delta_round_seats=())

    def test_seat_with_null_verdict_does_not_corroborate(self):
        seats = (_seat(finding_ids=("f1",), verdict=None),)
        with self.assertRaises(fd.ResolvedClaimUnverified):
            fd.require_seat_corroboration(("f1",), delta_round_seats=seats)

    def test_seat_referencing_a_different_finding_does_not_corroborate(self):
        seats = (_seat(finding_ids=("f2",), verdict="AGREE"),)
        with self.assertRaises(fd.ResolvedClaimUnverified):
            fd.require_seat_corroboration(("f1",), delta_round_seats=seats)

    def test_corroborated_claim_passes(self):
        seats = (_seat(finding_ids=("f1", "f2"), verdict="AGREE"),)
        fd.require_seat_corroboration(("f1",), delta_round_seats=seats)  # no raise

    def test_partial_corroboration_still_rejects(self):
        seats = (_seat(finding_ids=("f1",), verdict="AGREE"),)
        with self.assertRaises(fd.ResolvedClaimUnverified):
            fd.require_seat_corroboration(("f1", "f2"), delta_round_seats=seats)


# --------------------------------------------------------------------------- #
# T5 — review_scope enforcement for boundary-escalated rounds
# --------------------------------------------------------------------------- #


class ReviewScopeEnforcementTest(unittest.TestCase):
    def test_non_escalated_round_is_unconstrained(self):
        esc = fp.Escalation(required=False, trigger=None)
        scope = _delta_review_scope(mode=fp.REVIEW_SCOPE_DELTA_ONLY, covers=None)
        fd.enforce_review_scope_for_escalation(escalation=esc, review_scope=scope, covering_patch_digest=None)

    def test_escalated_delta_only_scope_rejected(self):
        esc = fp.Escalation(required=True, trigger="auth_security")
        scope = _delta_review_scope(mode=fp.REVIEW_SCOPE_DELTA_ONLY, covers="d" * 64)
        with self.assertRaises(fd.ReviewScopeRejected):
            fd.enforce_review_scope_for_escalation(escalation=esc, review_scope=scope, covering_patch_digest="d" * 64)

    def test_escalated_whole_patch_scope_wrong_digest_rejected(self):
        esc = fp.Escalation(required=True, trigger="auth_security")
        scope = _delta_review_scope(mode=fp.REVIEW_SCOPE_WHOLE_PATCH, covers="d" * 64)
        with self.assertRaises(fd.ReviewScopeRejected):
            fd.enforce_review_scope_for_escalation(escalation=esc, review_scope=scope, covering_patch_digest="e" * 64)

    def test_escalated_whole_patch_scope_correct_digest_passes(self):
        esc = fp.Escalation(required=True, trigger="auth_security")
        scope = _delta_review_scope(mode=fp.REVIEW_SCOPE_WHOLE_PATCH, covers="d" * 64)
        fd.enforce_review_scope_for_escalation(escalation=esc, review_scope=scope, covering_patch_digest="d" * 64)


# --------------------------------------------------------------------------- #
# Delta binding validation (deliverable 1, design §5.2)
# --------------------------------------------------------------------------- #


class DeltaBindingValidationTest(GitRepoTestCase):
    def _round0(self) -> str:
        # A present, well-formed manifest whose globs do not match anything
        # this fixture's deltas touch -- so escalation stays False and the
        # tests below exercise ONLY binding validation, not the escalation
        # decision (that's `EscalationDecisionTest`/`AcceptanceCriteriaTest`).
        self.write(fd.BOUNDARY_MANIFEST_PATH, _STRONG_MANIFEST)
        self.write("a.py", "hello\n")
        return self.commit("c0 base")

    def _build_clean_delta(self, base: str, parent_head: str) -> tuple[fp.DeltaReviewRecord, str]:
        self.write("b.py", "unrelated new file\n")
        delta_head = self.commit("c1 delta")
        record = fd.build_delta_round(
            repo=self.repo,
            base_sha=base,
            repo_slug=self.REPO_SLUG,
            parent_head_sha=parent_head,
            parent_patch_digest=self.digest(base, parent_head),
            parent_chain_digest="parent-chain-digest",
            delta_head_sha=delta_head,
            findings=(),
            resolved_finding_ids=(),
            delta_round_seats=(),
            review_scope=_delta_review_scope(mode=fp.REVIEW_SCOPE_DELTA_ONLY, covers=None),
            status=fp.DELTA_STATUS_REVIEWED_CLEAN,
        )
        return record, delta_head

    def test_valid_binding_passes(self):
        base = self._round0()
        record, delta_head = self._build_clean_delta(base, base)
        fd.validate_delta_binds_to_parent(
            record,
            repo=self.repo,
            base_sha=base,
            repo_slug=self.REPO_SLUG,
            parent_head_sha=base,
            parent_patch_digest=self.digest(base, base),
            parent_chain_digest="parent-chain-digest",
        )

    def test_tampered_chain_digest_rejected(self):
        import dataclasses

        base = self._round0()
        record, delta_head = self._build_clean_delta(base, base)
        tampered = dataclasses.replace(record, chain_digest="0" * 64)
        with self.assertRaises(fd.DeltaBindingInvalid):
            fd.validate_delta_binds_to_parent(
                tampered,
                repo=self.repo,
                base_sha=base,
                repo_slug=self.REPO_SLUG,
                parent_head_sha=base,
                parent_patch_digest=self.digest(base, base),
                parent_chain_digest="parent-chain-digest",
            )

    def test_wrong_parent_chain_digest_rejected(self):
        base = self._round0()
        record, delta_head = self._build_clean_delta(base, base)
        with self.assertRaises(fd.DeltaBindingInvalid):
            fd.validate_delta_binds_to_parent(
                record,
                repo=self.repo,
                base_sha=base,
                repo_slug=self.REPO_SLUG,
                parent_head_sha=base,
                parent_patch_digest=self.digest(base, base),
                parent_chain_digest="SOMETHING-ELSE",
            )

    def test_wrong_parent_patch_digest_rejected(self):
        base = self._round0()
        record, delta_head = self._build_clean_delta(base, base)
        with self.assertRaises(fd.DeltaBindingInvalid):
            fd.validate_delta_binds_to_parent(
                record,
                repo=self.repo,
                base_sha=base,
                repo_slug=self.REPO_SLUG,
                parent_head_sha=base,
                parent_patch_digest="f" * 64,
                parent_chain_digest="parent-chain-digest",
            )

    def test_tampered_changed_paths_rejected(self):
        import dataclasses

        base = self._round0()
        record, delta_head = self._build_clean_delta(base, base)
        tampered = dataclasses.replace(record, delta_changed_paths=("not/the/real/path.py",))
        with self.assertRaises(fd.DeltaBindingInvalid):
            fd.validate_delta_binds_to_parent(
                tampered,
                repo=self.repo,
                base_sha=base,
                repo_slug=self.REPO_SLUG,
                parent_head_sha=base,
                parent_patch_digest=self.digest(base, base),
                parent_chain_digest="parent-chain-digest",
            )

    def test_tampered_resulting_head_digest_rejected(self):
        import dataclasses

        base = self._round0()
        record, delta_head = self._build_clean_delta(base, base)
        tampered = dataclasses.replace(record, resulting_head_digest="0" * 64)
        with self.assertRaises(fd.DeltaBindingInvalid):
            fd.validate_delta_binds_to_parent(
                tampered,
                repo=self.repo,
                base_sha=base,
                repo_slug=self.REPO_SLUG,
                parent_head_sha=base,
                parent_patch_digest=self.digest(base, base),
                parent_chain_digest="parent-chain-digest",
            )

    def test_reviewed_clean_without_parent_digest_rejected_at_binding_time(self):
        """`DeltaReviewRecord.build` itself does not enforce the dual-link
        rule (that is `validate_delta_binds_to_parent`'s job, mirroring Lane
        A's `verify_chain`) -- construction with `parent_digest=None` and
        `status=reviewed-clean` SUCCEEDS, but binding validation REJECTS it."""
        base = self._round0()
        self.write("b.py", "unrelated new file\n")
        delta_head = self.commit("c1 delta")
        record = fp.DeltaReviewRecord.build(
            policy=None,
            review_scope=_delta_review_scope(),
            material_digests=(),
            parent_digest=None,
            parent_chain_digest="parent-chain-digest",
            delta_head_sha=delta_head,
            delta_changed_paths=fc.enumerate_changed_paths(self.repo, base, delta_head),
            delta_commits=(),
            resolved_finding_ids=(),
            carried_forward_finding_ids=(),
            reopened_finding_ids=(),
            resulting_head_digest=self.digest(base, delta_head),
            status=fp.DELTA_STATUS_REVIEWED_CLEAN,
            escalation=fp.Escalation(required=False, trigger=None),
        )
        with self.assertRaises(fd.DeltaBindingInvalid):
            fd.validate_delta_binds_to_parent(
                record,
                repo=self.repo,
                base_sha=base,
                repo_slug=self.REPO_SLUG,
                parent_head_sha=base,
                parent_patch_digest=self.digest(base, base),
                parent_chain_digest="parent-chain-digest",
            )


# --------------------------------------------------------------------------- #
# End-to-end acceptance criteria (design §9) with real git repos
# --------------------------------------------------------------------------- #


class AcceptanceCriteriaTest(GitRepoTestCase):
    def test_acceptance_1_disjoint_clean_delta_carries_forward_no_whole_patch(self):
        """A large reviewed patch (simulated: several files + findings) plus
        a small `reviewed-clean` delta whose changed paths are disjoint from
        every clean finding's path_scope and touch no boundary -> carry-
        forward SUCCEEDS, no whole-patch re-review is forced."""
        self.write(fd.BOUNDARY_MANIFEST_PATH, _STRONG_MANIFEST)
        self.write("pkg/a.py", "large reviewed content a\n")
        self.write("pkg/b.py", "large reviewed content b\n")
        base = self.commit("c0 base: large reviewed patch already landed here conceptually")

        findings = (
            _finding("f1", status="clean", path_scope=("pkg/a.py",)),
            _finding("f2", status="clean", path_scope=("pkg/b.py",)),
        )

        self.write("pkg/c.py", "small unrelated delta file\n")
        delta_head = self.commit("c1 small disjoint delta")

        record = fd.build_delta_round(
            repo=self.repo,
            base_sha=base,
            repo_slug=self.REPO_SLUG,
            parent_head_sha=base,
            parent_patch_digest=self.digest(base, base),
            parent_chain_digest="C0",
            delta_head_sha=delta_head,
            findings=findings,
            resolved_finding_ids=(),
            delta_round_seats=(),
            review_scope=_delta_review_scope(mode=fp.REVIEW_SCOPE_DELTA_ONLY, covers=None),
            status=fp.DELTA_STATUS_REVIEWED_CLEAN,
        )
        self.assertFalse(record.escalation.required)
        self.assertEqual(sorted(record.carried_forward_finding_ids), ["f1", "f2"])
        self.assertEqual(record.reopened_finding_ids, ())
        self.assertEqual(record.status, fp.DELTA_STATUS_REVIEWED_CLEAN)
        self.assertTrue(fd.is_carry_forward_eligible(record))

        # The delta binds to its parent and the round-trip validates clean.
        fd.validate_delta_binds_to_parent(
            record,
            repo=self.repo,
            base_sha=base,
            repo_slug=self.REPO_SLUG,
            parent_head_sha=base,
            parent_patch_digest=self.digest(base, base),
            parent_chain_digest="C0",
        )

    def test_acceptance_4_boundary_glob_forces_whole_patch_escalation(self):
        """A delta whose changed paths hit a boundary glob (`**/auth/**`)
        forces whole-patch escalation: carry-forward is suppressed and every
        clean finding is reopened."""
        self.write(fd.BOUNDARY_MANIFEST_PATH, _STRONG_MANIFEST)
        self.write("pkg/a.py", "reviewed content\n")
        base = self.commit("c0 base")

        findings = (_finding("f1", status="clean", path_scope=("pkg/a.py",)),)

        self.write("src/auth/login.py", "touches a protected surface\n")
        delta_head = self.commit("c1 delta touching auth")

        record = fd.build_delta_round(
            repo=self.repo,
            base_sha=base,
            repo_slug=self.REPO_SLUG,
            parent_head_sha=base,
            parent_patch_digest=self.digest(base, base),
            parent_chain_digest="C0",
            delta_head_sha=delta_head,
            findings=findings,
            resolved_finding_ids=(),
            delta_round_seats=(),
            review_scope=_delta_review_scope(
                mode=fp.REVIEW_SCOPE_WHOLE_PATCH,
                covers=self.digest(base, delta_head),
            ),
            status=fp.DELTA_STATUS_ESCALATED_WHOLE_PATCH,
        )
        self.assertTrue(record.escalation.required)
        self.assertEqual(record.escalation.trigger, "auth_security")
        self.assertEqual(record.carried_forward_finding_ids, ())
        self.assertEqual(record.reopened_finding_ids, ("f1",))

    def test_acceptance_4_escalated_round_cannot_be_recorded_reviewed_clean(self):
        self.write(fd.BOUNDARY_MANIFEST_PATH, _STRONG_MANIFEST)
        base = self.commit("c0 base")
        self.write("src/auth/login.py", "touches a protected surface\n")
        delta_head = self.commit("c1 delta touching auth")
        with self.assertRaises(fd.EscalationInvalid):
            fd.build_delta_round(
                repo=self.repo,
                base_sha=base,
                repo_slug=self.REPO_SLUG,
                parent_head_sha=base,
                parent_patch_digest=self.digest(base, base),
                parent_chain_digest="C0",
                delta_head_sha=delta_head,
                findings=(),
                resolved_finding_ids=(),
                delta_round_seats=(),
                review_scope=_delta_review_scope(mode=fp.REVIEW_SCOPE_WHOLE_PATCH, covers=self.digest(base, delta_head)),
                status=fp.DELTA_STATUS_REVIEWED_CLEAN,  # contradiction: escalated but "clean"
            )

    def test_t15_weakened_manifest_at_a_later_tip_does_not_escape_escalation(self):
        """T15: a delta round two steps downstream weakens
        `boundaries.toml` (removing the auth_security section) at an
        intermediate tip; a LATER delta relative to that tip which touches
        `auth/` must still escalate, because escalation is decided from the
        manifest pinned at the chain's constant `base_sha`, never at any
        later tip/delta head."""
        self.write(fd.BOUNDARY_MANIFEST_PATH, _STRONG_MANIFEST)
        self.write("pkg/a.py", "reviewed content\n")
        base = self.commit("c0 base: strong manifest")

        # Round 1: a delta that WEAKENS the manifest (removes auth_security).
        # It legitimately forces its OWN escalation (manifest-path-touch) --
        # that is not what's under test here.
        self.write(fd.BOUNDARY_MANIFEST_PATH, _WEAKENED_MANIFEST)
        d1_head = self.commit("c1 weaken the manifest")
        round1 = fd.build_delta_round(
            repo=self.repo,
            base_sha=base,
            repo_slug=self.REPO_SLUG,
            parent_head_sha=base,
            parent_patch_digest=self.digest(base, base),
            parent_chain_digest="C0",
            delta_head_sha=d1_head,
            findings=(),
            resolved_finding_ids=(),
            delta_round_seats=(),
            review_scope=_delta_review_scope(mode=fp.REVIEW_SCOPE_WHOLE_PATCH, covers=self.digest(base, d1_head)),
            status=fp.DELTA_STATUS_ESCALATED_WHOLE_PATCH,
        )
        self.assertTrue(round1.escalation.required)
        self.assertEqual(round1.escalation.trigger, fd.ESCALATION_TRIGGER_MANIFEST_MODIFIED)

        # Round 2: relative to d1_head (whose CURRENT tree carries the
        # WEAKENED manifest), touch an auth/ file WITHOUT touching the
        # manifest path this round.
        self.write("auth/new.py", "a brand new auth-surface file\n")
        d2_head = self.commit("c2 touches auth only, manifest untouched this round")

        round2 = fd.build_delta_round(
            repo=self.repo,
            base_sha=base,  # constant across the whole chain, design §5.1
            repo_slug=self.REPO_SLUG,
            parent_head_sha=d1_head,
            parent_patch_digest=round1.resulting_head_digest,
            parent_chain_digest=round1.chain_digest,
            delta_head_sha=d2_head,
            findings=(),
            resolved_finding_ids=(),
            delta_round_seats=(),
            review_scope=_delta_review_scope(mode=fp.REVIEW_SCOPE_WHOLE_PATCH, covers=self.digest(base, d2_head)),
            status=fp.DELTA_STATUS_ESCALATED_WHOLE_PATCH,
        )
        # Despite the CURRENT (d1_head) tree carrying a weakened manifest
        # with no auth_security section, round2 STILL escalates because the
        # manifest is read at the chain's constant base_sha (the strong one).
        self.assertTrue(round2.escalation.required)
        self.assertEqual(round2.escalation.trigger, "auth_security")
        self.assertNotEqual(round2.escalation.trigger, fd.ESCALATION_TRIGGER_MANIFEST_MODIFIED)


# --------------------------------------------------------------------------- #
# T4/§5.3 (finding 4) — a reviewed-clean delta with reopened findings must
# have seat corroboration covering EVERY reopened finding, not just resolved
# claims.
# --------------------------------------------------------------------------- #


class ReopenedFindingCorroborationTest(GitRepoTestCase):
    def _base_and_intersecting_delta(self) -> tuple[str, str, fp.Finding]:
        """A base with a manifest that does NOT match anything this delta
        touches (so escalation stays False -- the counterexample must be a
        NON-escalated delta) plus a clean finding `f1` whose `path_scope`
        INTERSECTS the delta's changed paths, so `carry_forward` reopens it."""
        self.write(fd.BOUNDARY_MANIFEST_PATH, _STRONG_MANIFEST)
        self.write("pkg/a.py", "reviewed content\n")
        base = self.commit("c0 base")
        f1 = _finding("f1", status="clean", path_scope=("pkg/a.py",))
        self.write("pkg/a.py", "changed content, intersects f1's path_scope\n")
        delta_head = self.commit("c1 touches pkg/a.py")
        return base, delta_head, f1

    def test_reviewed_clean_delta_with_uncorroborated_reopened_finding_rejected(self):
        """agent-harness#191 CR finding 4: a NON-escalated delta whose
        changed paths intersect a clean finding's `path_scope` reopens that
        finding (`carry_forward`'s own INTERSECTS rule). Recording that round
        `status="reviewed-clean"` with ZERO delta-round seats used to succeed
        and `is_carry_forward_eligible` returned True -- the reopened finding
        was never actually re-reviewed by anyone. It must now be rejected."""
        base, delta_head, f1 = self._base_and_intersecting_delta()
        with self.assertRaises(fd.ResolvedClaimUnverified):
            fd.build_delta_round(
                repo=self.repo,
                base_sha=base,
                repo_slug=self.REPO_SLUG,
                parent_head_sha=base,
                parent_patch_digest=self.digest(base, base),
                parent_chain_digest="C0",
                delta_head_sha=delta_head,
                findings=(f1,),
                resolved_finding_ids=(),
                delta_round_seats=(),  # no seats at all
                review_scope=_delta_review_scope(mode=fp.REVIEW_SCOPE_DELTA_ONLY, covers=None),
                status=fp.DELTA_STATUS_REVIEWED_CLEAN,
            )

    def test_reviewed_clean_delta_with_corroborated_reopened_finding_succeeds(self):
        """The same shape, but the delta round's seats DID return a verdict
        referencing the reopened finding id -- corroborated, so it succeeds
        and the finding lands in `reopened_finding_ids`."""
        base, delta_head, f1 = self._base_and_intersecting_delta()
        seats = (_seat(finding_ids=("f1",), verdict="AGREE"),)
        record = fd.build_delta_round(
            repo=self.repo,
            base_sha=base,
            repo_slug=self.REPO_SLUG,
            parent_head_sha=base,
            parent_patch_digest=self.digest(base, base),
            parent_chain_digest="C0",
            delta_head_sha=delta_head,
            findings=(f1,),
            resolved_finding_ids=(),
            delta_round_seats=seats,
            review_scope=_delta_review_scope(mode=fp.REVIEW_SCOPE_DELTA_ONLY, covers=None),
            status=fp.DELTA_STATUS_REVIEWED_CLEAN,
        )
        self.assertEqual(record.reopened_finding_ids, ("f1",))
        self.assertTrue(fd.is_carry_forward_eligible(record))

    def test_escalated_whole_patch_round_does_not_require_reopened_corroboration(self):
        """A boundary-escalated round suppresses carry-forward and reopens
        EVERY clean finding (`suppress=True`) -- that round is, by design,
        still going BACK into review, so it is NOT required to already carry
        seat corroboration for those reopened findings at construction time
        (module resolved-ambiguity #3). Only `status="reviewed-clean"` is
        constrained by this fix."""
        self.write(fd.BOUNDARY_MANIFEST_PATH, _STRONG_MANIFEST)
        self.write("pkg/a.py", "reviewed content\n")
        base = self.commit("c0 base")
        f1 = _finding("f1", status="clean", path_scope=("pkg/a.py",))
        self.write("src/auth/login.py", "touches a protected surface\n")
        delta_head = self.commit("c1 delta touching auth")
        record = fd.build_delta_round(
            repo=self.repo,
            base_sha=base,
            repo_slug=self.REPO_SLUG,
            parent_head_sha=base,
            parent_patch_digest=self.digest(base, base),
            parent_chain_digest="C0",
            delta_head_sha=delta_head,
            findings=(f1,),
            resolved_finding_ids=(),
            delta_round_seats=(),  # no seats -- fine, round is not reviewed-clean
            review_scope=_delta_review_scope(mode=fp.REVIEW_SCOPE_WHOLE_PATCH, covers=self.digest(base, delta_head)),
            status=fp.DELTA_STATUS_ESCALATED_WHOLE_PATCH,
        )
        self.assertEqual(record.reopened_finding_ids, ("f1",))


if __name__ == "__main__":
    unittest.main()
