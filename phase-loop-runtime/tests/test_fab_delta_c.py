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
        """Only the SAFE-CHARSET / empty-non-string checks, and round-7's
        normalizes-to-nothing check, remain fail-closed at translate time.
        `/abs/path` normalizes rather than being rejected outright (drops
        its leading empty segment, round-6/7); `a/../b` is ALSO rejected —
        but via the round-8 `..`-is-always-malformed path, not this test's
        safe-charset/empty-non-string set — see
        `test_leading_slash_globs_still_normalize_dotdot_globs_do_not` and
        `test_any_dotdot_component_glob_is_always_malformed`."""
        for bad in ("", "a[bc]", "a{b,c}", "a b"):
            with self.assertRaises(fd.BoundaryManifestInvalid):
                fd._translate_glob_to_regex(bad)

    def test_any_dotdot_component_glob_is_always_malformed(self):
        """Round-8 (Consiliency/agent-harness#191, codex round-8 CR):
        round-7 RESOLVED a glob's `..` component the same way it resolved a
        changed path's `..` (pop the previous kept component) — but a
        glob's previous component can be `**`, a variable-length unit with
        no fixed segment to pop, so that resolution was UNSOUND
        (`**/../auth/**` round-7-normalized to `auth/**`, an anchored
        pattern that under-matched a real mangled path — see
        `BoundaryGlobEvasionRegressionTest.
        test_glob_dotdot_after_globstar_is_rejected_not_under_matched` for
        the end-to-end reproduction). Round-8's fix: a glob containing ANY
        `..` component, anywhere, is now UNCONDITIONALLY malformed — never
        resolved, regardless of whether the `..` would (if it were a path)
        pop a literal segment or a `**`. `x/../y` and `a/../b` are NO
        LONGER accepted/normalized (round-7 briefly allowed these); they now
        join the always-rejected set alongside a bare `..` and a
        repo-escaping `x/../../y`."""
        for bad in ("..", "x/../../y", "a/../b", "x/../y", "**/../x", "x/../**", "../auth/**", "**/../auth/**"):
            with self.subTest(bad=bad):
                with self.assertRaises(fd.BoundaryManifestInvalid):
                    fd._translate_glob_to_regex(bad)

    def test_leading_slash_globs_still_normalize_dotdot_globs_do_not(self):
        """Round-7's leading-`/`-drops-empty-segment normalization is
        UNCHANGED by round-8 (no `**`-length ambiguity in dropping an empty
        component): `/abs/path` still normalizes to `abs/path`. Round-7 ALSO
        briefly normalized an interior `..` component (`a/../b` -> `b`) —
        round-8 REMOVES that: `..` is now always rejected (malformed), never
        resolved, for the reason `test_any_dotdot_component_glob_is_always_
        malformed` documents."""
        self._assert_pattern(fd._translate_glob_to_regex("/abs/path"), r"^abs/path$")
        self.assertTrue(self._match("/abs/path", "abs/path"))
        with self.assertRaises(fd.BoundaryManifestInvalid):
            fd._translate_glob_to_regex("a/../b")

    def _assert_pattern(self, compiled, expected: str) -> None:
        self.assertEqual(compiled.pattern, expected)

    def test_dot_and_empty_component_globs_now_normalize_not_inert(self):
        """Round-7 (Consiliency/agent-harness#191, codex round-7 CR) —
        CORRECTING round-6's risk read. Round-6 removed round-3's
        per-segment `.`/empty-component rejection on the premise that such a
        glob was "at worst inert" once the PATH side was normalized. That
        premise was FALSE: leaving the GLOB side raw meant a glob whose
        components were entirely `.`/empty (`./**`, `a//b`, `x/./y`, `x/`,
        `**/`) compiled to a regex that could NEVER match any normalized
        path — a silent, total fail-open (a section declared with ONLY such
        a glob escalated NOTHING). Round-7 now NORMALIZES the glob's own
        components before compiling, so these compile to a BROADER,
        fail-safe pattern instead of an inert one — must not raise, and
        must actually match a normalized path."""
        cases = (
            ("./**", "config"),  # -> "**", matches everything
            ("a//b", "a/b"),
            ("x/./y", "x/y"),
            ("x/", "x"),
            ("**/", ".git/config"),  # -> "**", matches everything
        )
        for glob, matches in cases:
            with self.subTest(glob=glob):
                fd._translate_glob_to_regex(glob)  # must not raise
                self.assertTrue(self._match(glob, matches), f"{glob!r} must match {matches!r} once normalized")

    def test_normalizes_to_empty_globs_are_now_malformed(self):
        """Round-7: a glob whose components are ENTIRELY `.`/empty (so it
        normalizes to nothing at all) declares a surface with NO effective
        boundary and is MALFORMED — the narrower, corrected replacement for
        round-6's blanket "never raise on `.`/empty" rule."""
        for glob in (".", "./", "//"):
            with self.subTest(glob=glob):
                with self.assertRaises(fd.BoundaryManifestInvalid):
                    fd._translate_glob_to_regex(glob)

    def test_dot_git_component_compiles_and_matches(self):
        """Round-5 CR (codex, self-correcting): round-4 briefly rejected a
        literal `.git` path component on the premise that git's pathname
        verifier (`verify_path`) forbids `.git` anywhere in a tree, so such a
        glob could never match a real diff path. That premise was FALSE for
        this module's own threat model — `verify_path` governs the
        index/worktree, not the raw commit-tree diffs
        `fab_canonical.enumerate_changed_paths` actually enumerates, and Lane
        B's threat model explicitly includes hand-crafted trees (`git
        mktree`/`git commit-tree` permit a `.git` entry; `fsck`'s
        `hasDotgit` is a warning, not a rejection — see
        `HostileGitTreeDotGitTest` below for the crafted-tree reproduction).
        `.git/**` is therefore a legitimate, valuable boundary glob and must
        compile and match like any other literal segment, not raise."""
        for glob in (".git/**", "src/.GIT/**", ".Git/**", "a/.git/b", ".git", "**/.git/**"):
            with self.subTest(glob=glob):
                fd._translate_glob_to_regex(glob)  # must not raise
        self.assertTrue(self._match(".git/**", ".git/config"))
        self.assertTrue(self._match(".git", ".git"))
        self.assertTrue(self._match("**/.git/**", "sub/.git/config"))

    def test_dot_git_matching_does_not_over_match_similar_names(self):
        """A legitimately dot-prefixed directory that merely starts with the
        same three letters (`.github`) is a DIFFERENT component entirely,
        not `.git` — a `.git`-specific glob must not spuriously match it, and
        vice versa."""
        self.assertTrue(self._match("**", ".git/config"))
        self.assertTrue(self._match("*", ".git"))
        self.assertTrue(self._match(".github/workflows/**", ".github/workflows/ci.yml"))
        self.assertFalse(self._match(".github/workflows/**", ".git/workflows/ci.yml"))
        self.assertFalse(self._match(".git/**", ".github/workflows/ci.yml"))

    def test_legitimate_default_glob_set_all_compile_and_match_correctly(self):
        """Regression: the full legitimate default glob set (design §5.4)
        must all still compile AND match a representative in-boundary path
        AND NOT match a clearly-disjoint path — the semantic-empty-glob
        rejection must not over-reject any real-world pattern."""
        cases = (
            ("**/contracts/**", "pkg/contracts/x.py", "totally/unrelated.py"),
            ("**/*.proto", "a/b/c.proto", "a/b/c.txt"),
            ("**/schema/**", "a/schema/x.sql", "a/other/x.sql"),
            ("**/main.py", "src/main.py", "src/other.py"),
            ("Dockerfile*", "Dockerfile", "sub/Dockerfile"),
            ("**/auth/**", "src/auth/login.py", "src/other/login.py"),
            ("**/credsep.py", "convergence/broker/credsep.py", "convergence/broker/other.py"),
            ("**/*secret*", "a/my_secret.txt", "a/my_plain.txt"),
            ("**/migrations/**", "app/migrations/0001.py", "app/other/0001.py"),
            ("**/*.sql", "db/schema.sql", "db/schema.py"),
            ("**/deploy/**", "infra/deploy/main.tf", "infra/other/main.tf"),
            ("**/*.tf", "infra/main.tf", "infra/main.py"),
            (".github/workflows/**", ".github/workflows/ci.yml", "other/workflows/ci.yml"),
        )
        for glob, in_boundary, disjoint in cases:
            with self.subTest(glob=glob):
                pattern = fd._translate_glob_to_regex(glob)
                self.assertTrue(pattern.match(in_boundary), f"{glob!r} must match {in_boundary!r}")
                self.assertFalse(pattern.match(disjoint), f"{glob!r} must NOT match {disjoint!r}")

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

    def test_inert_glob_bug_reproduction_now_escalates(self):
        """THE round-7 (Consiliency/agent-harness#191, codex round-7 CR) bug
        reproduction and fix, end to end. Round-6 normalized the PATH side
        of every boundary match but left the GLOB side raw, so a boundary
        declared with only a `.`/empty-component glob (`[auth] globs =
        ["./**"]`, which compiled to `^\\.(?:/.*)?$` — a pattern no
        NORMALIZED path can ever satisfy) was INERT: `evaluate_boundary_
        escalation` returned `required=False` for EVERY changed path,
        including `src/live.py`, silently leaving the `auth` surface with
        ZERO effective boundary (fail-open, carry-forward permitted).
        Round-7's symmetric glob-component normalization fixes this: `./**`
        now normalizes to `**`, which matches every path — broader than the
        author probably intended, but fail-SAFE (escalates MORE, never
        less), closing the fail-open."""
        self.write(fd.BOUNDARY_MANIFEST_PATH, '[auth]\nglobs = ["./**"]\n')
        base = self.commit("c1 inert-glob boundary (pre-fix regression)")
        load = fd.load_boundary_manifest_at_base(self.repo, base)
        self.assertEqual(load.disposition, fd.MANIFEST_DISPOSITION_PRESENT)
        esc = fd.evaluate_boundary_escalation(load, ("src/live.py",))
        self.assertTrue(esc.required, "'./**' must escalate (round-7 fix) — pre-fix this was required=False")
        self.assertEqual(esc.trigger, "auth")

    def test_inert_glob_bug_reproduction_specific_surface(self):
        """The same bug/fix as `test_inert_glob_bug_reproduction_now_escalates`,
        but with a more specific declared surface (`./auth/**`, normalizing
        to `auth/**`) rather than the maximally-broad `./**`: it must
        escalate a delta touching `auth/login.py` (the intended surface)
        while remaining disjoint from an unrelated `other/thing.py` delta —
        i.e. the fix is not merely "escalate everything", it restores the
        AUTHOR'S actual declared boundary. `auth/**` (normalized, anchored)
        does NOT match `src/auth/login.py` — a nested `auth` dir under
        `src/` is a DIFFERENT location than a top-level `auth/**`; that
        requires `**/auth/**` instead, exercised elsewhere."""
        self.write(fd.BOUNDARY_MANIFEST_PATH, '[auth]\nglobs = ["./auth/**"]\n')
        base = self.commit("c1 specific dot-prefixed boundary")
        load = fd.load_boundary_manifest_at_base(self.repo, base)
        self.assertEqual(load.disposition, fd.MANIFEST_DISPOSITION_PRESENT)
        esc_in = fd.evaluate_boundary_escalation(load, ("auth/login.py",))
        self.assertTrue(esc_in.required)
        self.assertEqual(esc_in.trigger, "auth")
        esc_nested = fd.evaluate_boundary_escalation(load, ("src/auth/login.py",))
        self.assertFalse(esc_nested.required, "'./auth/**' normalizes to 'auth/**', anchored at repo root")
        esc_out = fd.evaluate_boundary_escalation(load, ("other/thing.py",))
        self.assertFalse(esc_out.required)

    def test_dot_and_empty_component_glob_manifest_present_and_now_escalates(self):
        """Round-7 correction of the round-6 test this replaces: these glob
        shapes are still PRESENT/well-formed at manifest-parse time, but —
        unlike round-6's (wrong) claim that they were harmlessly INERT —
        they now normalize to a BROADER, matching pattern and DO escalate
        the path their normalized form actually covers."""
        cases = (
            ("./**", "config"),  # -> "**"
            ("a//b", "a/b"),
            ("x/./y", "x/y"),
            ("x/", "x"),
            ("**/", ".git/config"),  # -> "**"
        )
        for glob, matching_path in cases:
            with self.subTest(glob=glob):
                self.write(fd.BOUNDARY_MANIFEST_PATH, f'[auth]\nglobs = ["{glob}"]\n')
                base = self.commit(f"c1 dot/empty-component glob {glob!r}")
                load = fd.load_boundary_manifest_at_base(self.repo, base)
                self.assertEqual(load.disposition, fd.MANIFEST_DISPOSITION_PRESENT)
                self.assertIsNotNone(load.manifest)
                esc = fd.evaluate_boundary_escalation(load, (matching_path,))
                self.assertTrue(esc.required, f"{glob!r} (normalized) must escalate {matching_path!r}")

    def test_normalizes_to_empty_glob_manifest_now_malformed(self):
        """A glob that normalizes to NO effective boundary at all (`.`,
        `./`, `//`) OR that contains ANY `..` component (round-8: `..` is
        always rejected, never resolved — a bare `..`, a repo-escaping
        `x/../../y`, and now ALSO an interior `a/../b`, `**/../x`,
        `x/../**`, `../auth/**`, and the confirmed-bug shape
        `**/../auth/**`) makes the WHOLE manifest MALFORMED at parse time
        and therefore forces escalate-every-delta exactly like a
        missing/genuinely-malformed manifest — fail-closed, never a silent
        under-match."""
        for bad_glob in (
            ".",
            "./",
            "//",
            "..",
            "x/../../y",
            "a/../b",
            "x/../y",
            "**/../x",
            "x/../**",
            "../auth/**",
            "**/../auth/**",
        ):
            with self.subTest(bad_glob=bad_glob):
                self.write(fd.BOUNDARY_MANIFEST_PATH, f'[auth]\nglobs = ["{bad_glob}"]\n')
                base = self.commit(f"c1 normalizes-to-empty glob {bad_glob!r}")
                load = fd.load_boundary_manifest_at_base(self.repo, base)
                self.assertEqual(load.disposition, fd.MANIFEST_DISPOSITION_MALFORMED)
                self.assertIsNone(load.manifest)
                esc = fd.evaluate_boundary_escalation(load, ("src/live.py",))
                self.assertTrue(esc.required, f"{bad_glob!r} must escalate (fail-closed), not silently permit carry-forward")

    def test_leading_slash_glob_manifest_still_normalizes(self):
        """Round-7's leading-`/`-drops-empty-segment normalization is
        UNCHANGED at manifest-parse time by round-8: `/abs/path` still
        normalizes to `abs/path` and the manifest remains PRESENT/
        well-formed, escalating exactly the normalized location. (A glob
        with an interior `..`, e.g. `a/../b`, is covered by
        `test_normalizes_to_empty_glob_manifest_now_malformed` instead —
        round-8 makes it MALFORMED, not normalizing.)"""
        self.write(fd.BOUNDARY_MANIFEST_PATH, '[auth]\nglobs = ["/abs/path"]\n')
        base = self.commit("c1 normalizing glob '/abs/path'")
        load = fd.load_boundary_manifest_at_base(self.repo, base)
        self.assertEqual(load.disposition, fd.MANIFEST_DISPOSITION_PRESENT)
        esc = fd.evaluate_boundary_escalation(load, ("abs/path",))
        self.assertTrue(esc.required, "'/abs/path' (normalized to 'abs/path') must escalate 'abs/path'")
        self.assertEqual(esc.trigger, "auth")
        esc_unrelated = fd.evaluate_boundary_escalation(load, ("totally/unrelated.py",))
        self.assertFalse(esc_unrelated.required)

    def test_glob_dotdot_after_globstar_manifest_is_malformed_escalates_every_delta(self):
        """END-TO-END reproduction of the round-8 (codex round-8 CR) confirmed
        bug at the manifest level: `[auth] globs = ["**/../auth/**"]` MUST
        be malformed (never silently accepted with an under-matching
        pattern) — and, being malformed, MUST escalate EVERY delta,
        including the exact mangled path (`x/y/../auth/login.py`, normalizing
        to `x/auth/login.py`) that round-7's `..`-resolution would have
        under-matched (`**/../auth/**` round-7-normalized to `auth/**`,
        compiling to the anchored `^auth(?:/.*)?$`, which does NOT match
        `x/auth/login.py`), and a completely unrelated path — malformed
        disposition escalates unconditionally, it does not merely happen to
        also cover the bug's specific path."""
        self.write(fd.BOUNDARY_MANIFEST_PATH, '[auth]\nglobs = ["**/../auth/**"]\n')
        base = self.commit("c1 round-8 confirmed-bug glob")
        load = fd.load_boundary_manifest_at_base(self.repo, base)
        self.assertEqual(load.disposition, fd.MANIFEST_DISPOSITION_MALFORMED)
        self.assertIsNone(load.manifest)
        esc_bug_path = fd.evaluate_boundary_escalation(load, ("x/y/../auth/login.py",))
        self.assertTrue(esc_bug_path.required, "malformed manifest must escalate — the round-7 under-match is gone")
        self.assertEqual(esc_bug_path.trigger, fd.ESCALATION_TRIGGER_MALFORMED_MANIFEST)
        esc_unrelated = fd.evaluate_boundary_escalation(load, ("totally/unrelated.py",))
        self.assertTrue(esc_unrelated.required, "malformed disposition escalates every delta, unconditionally")

    def test_dot_git_component_glob_manifest_is_present_not_malformed(self):
        """Round-5 CR (codex, self-correcting): round-4 treated a `.git`
        path-component glob as MALFORMED on the (now-reverted) premise that
        it could never match a real diff path. That premise is false — see
        `HostileGitTreeDotGitTest` below for the crafted-tree positive
        coverage — so a manifest declaring a `.git`-component glob is a
        perfectly legitimate, well-formed manifest (PRESENT disposition),
        not a malformed one."""
        for glob in (".git/**", "src/.GIT/**", ".Git/**", "a/.git/b"):
            with self.subTest(glob=glob):
                self.write(fd.BOUNDARY_MANIFEST_PATH, f'[surface]\nglobs = ["{glob}"]\n')
                base = self.commit(f"c1 dot-git-component glob {glob!r}")
                load = fd.load_boundary_manifest_at_base(self.repo, base)
                self.assertEqual(load.disposition, fd.MANIFEST_DISPOSITION_PRESENT)
                self.assertIsNotNone(load.manifest)

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
# Hostile-tree `.git`-component positive coverage (round-5 CR, codex,
# self-correcting revert of round-4 — Consiliency/agent-harness#191). Round-4
# rejected `.git`-component boundary globs as semantic-empty on the premise
# that git's `verify_path` forbids a `.git` path component anywhere in a
# tree. `verify_path` governs the INDEX/WORKTREE (`git checkout`, `git add`,
# ordinary commits) — it is NOT consulted by the raw plumbing
# (`git mktree`/`git commit-tree`) that builds and diffs commit-tree objects
# directly, and Lane B's own threat model (`fab_canonical.py`) explicitly
# includes hand-crafted trees. This class builds exactly such a crafted head
# commit via plumbing, confirms `fab_canonical.enumerate_changed_paths`
# reports the resulting `.git/config` changed path (i.e. the path IS
# reachable through Lane C's actual input, a raw commit-tree diff), and
# confirms a `.git/**` boundary glob escalates that delta — the corrected
# understanding that motivated the revert.
# --------------------------------------------------------------------------- #


def _git_stdin(repo: Path, *args: str, input_text: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], input=input_text, capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(f"git {args} failed: {result.stderr}")
    return result.stdout


class HostileGitTreeDotGitTest(GitRepoTestCase):
    def _craft_dot_git_injection_commit(self, base: str) -> str:
        """Build a head commit, parented on `base`, whose tree is IDENTICAL
        to `base`'s tree except for one added top-level entry: a `.git`
        subtree containing a `config` blob — the same plumbing sequence the
        task's empirical repro used (`git hash-object -w --stdin` for the
        blob, `git mktree` for the inner and outer trees, `git commit-tree`
        for the commit), entirely bypassing `verify_path`/`git add`."""
        blob_sha = _git_stdin(
            self.repo, "hash-object", "-w", "--stdin", input_text="[core]\n\tbare = false\n"
        ).strip()
        inner_tree_sha = _git_stdin(
            self.repo, "mktree", input_text=f"100644 blob {blob_sha}\tconfig\n"
        ).strip()
        base_tree_sha = _rev_parse(self.repo, f"{base}^{{tree}}")
        base_entries = _run(self.repo, "ls-tree", base_tree_sha).stdout
        outer_tree_input = base_entries + f"040000 tree {inner_tree_sha}\t.git\n"
        outer_tree_sha = _git_stdin(self.repo, "mktree", input_text=outer_tree_input).strip()
        crafted_head = _git_stdin(
            self.repo,
            "commit-tree",
            outer_tree_sha,
            "-p",
            base,
            "-m",
            "crafted .git tree injection",
            input_text="",
        ).strip()
        return crafted_head

    def test_dot_git_path_reachable_via_crafted_tree_raw_diff(self):
        """The empirical premise underlying the revert: a `.git/...` path IS
        a reachable CHANGED PATH in `fab_canonical`'s raw commit-tree diff
        enumeration when the head commit's tree was built via plumbing that
        bypasses `verify_path` — mirrors the task's `git diff --no-renames -z
        --raw` repro directly (rc==0, `.git/config` present)."""
        self.write("src/live.py", "print('hi')\n")
        base = self.commit("c1 base commit")
        crafted_head = self._craft_dot_git_injection_commit(base)

        changed = fc.enumerate_changed_paths(self.repo, base, crafted_head)
        self.assertIn(".git/config", changed)

    def test_dot_git_boundary_glob_escalates_crafted_tree_injection(self):
        """End to end: a manifest declaring `.git/**` as a protected surface
        must ESCALATE a delta whose only change is the crafted `.git/config`
        injection — `.git/**` is a legitimate, valuable boundary glob against
        exactly this hostile-tree attack, not a semantic-empty one that
        should invalidate the manifest."""
        self.write(fd.BOUNDARY_MANIFEST_PATH, '[git_injection]\nglobs = [".git/**"]\n')
        self.write("src/live.py", "print('hi')\n")
        base = self.commit("c1 base with boundary manifest")
        crafted_head = self._craft_dot_git_injection_commit(base)

        changed = fc.enumerate_changed_paths(self.repo, base, crafted_head)
        self.assertIn(".git/config", changed)

        # The glob must compile without raising (no longer malformed).
        pattern = fd._translate_glob_to_regex(".git/**")
        self.assertTrue(pattern.match(".git/config"))

        load = fd.load_boundary_manifest_at_base(self.repo, base)
        self.assertEqual(load.disposition, fd.MANIFEST_DISPOSITION_PRESENT)
        self.assertIsNotNone(load.manifest)

        esc = fd.evaluate_boundary_escalation(load, changed)
        self.assertTrue(esc.required, ".git/** must escalate a delta injecting .git/config via a hostile tree")
        self.assertEqual(esc.trigger, "git_injection")


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
# Round-6 (Consiliency/agent-harness#191) — `_normalize_path_for_matching`
# unit coverage, plus the CONFIRMED start-anchored-boundary-glob evasion
# regression: `enumerate_changed_paths` does NO normalization and
# `git mktree`/`git commit-tree` accept `.`, `..`, and empty tree-entry
# names, so a hostile delta can surface a MANGLED changed path
# (`./X`, `X//Y`, `x/../X`) that a START-ANCHORED glob's literal-string match
# never saw pre-fix, even though `git checkout` collapses it to the real,
# protected path `X`. Empirically verified while writing this regression
# (git 2.34.1): the `./X` and `x/../X` mangled forms are reachable through a
# REAL crafted commit tree end to end (`git diff --raw` succeeds, rc==0, and
# reports the literal mangled path) — the `X//Y` (embedded EMPTY tree-entry
# name) form is accepted by `git mktree` at WRITE time but git's tree reader
# refuses to WALK it back (`fatal: empty filename in tree entry`, rc==128),
# so `fab_canonical.enumerate_changed_paths` already fails closed
# (`PatchDigestInvalid`) for that specific sub-form on this git version
# before Lane C ever sees it. `_normalize_path_for_matching` still defends
# against it directly (unit-level below) — dropping empty segments is cheap,
# git's own hard-fail is an implementation detail of one plumbing path, not a
# security boundary Lane C should rely on.
# --------------------------------------------------------------------------- #


class PathNormalizationTest(unittest.TestCase):
    """Direct unit coverage for `_normalize_path_for_matching` — the
    checkout-equivalent collapse `evaluate_boundary_escalation`/
    `carry_forward` apply to every path before matching."""

    def test_dot_and_empty_segments_dropped(self):
        self.assertEqual(fd._normalize_path_for_matching("./.github/workflows/ci.yml"), ".github/workflows/ci.yml")
        self.assertEqual(fd._normalize_path_for_matching(".github//workflows/ci.yml"), ".github/workflows/ci.yml")
        self.assertEqual(fd._normalize_path_for_matching("./Dockerfile"), "Dockerfile")
        self.assertEqual(fd._normalize_path_for_matching("a/./b"), "a/b")
        self.assertEqual(fd._normalize_path_for_matching("a//b"), "a/b")
        self.assertEqual(fd._normalize_path_for_matching("a/b/"), "a/b")
        self.assertEqual(fd._normalize_path_for_matching("/a/b"), "a/b")

    def test_dot_dot_pops_previous_segment(self):
        self.assertEqual(fd._normalize_path_for_matching("x/../.github/workflows/ci.yml"), ".github/workflows/ci.yml")
        self.assertEqual(fd._normalize_path_for_matching("x/../Dockerfile"), "Dockerfile")
        self.assertEqual(fd._normalize_path_for_matching("a/b/../c"), "a/c")

    def test_already_normal_path_is_unchanged(self):
        for p in (".github/workflows/ci.yml", "Dockerfile", "pkg/a.py", ".git/config"):
            with self.subTest(p=p):
                self.assertEqual(fd._normalize_path_for_matching(p), p)

    def test_dot_git_component_preserved_verbatim(self):
        """A literal `.git` segment is a real, meaningful path component
        (see `_translate_glob_to_regex`'s docstring) — normalization must
        NOT drop or transform it, only the true no-op `.`/empty/`..` forms."""
        self.assertEqual(fd._normalize_path_for_matching(".git/config"), ".git/config")
        self.assertEqual(fd._normalize_path_for_matching("./.git/config"), ".git/config")

    def test_root_escaping_dot_dot_is_abnormal(self):
        """A `..` with no previous segment to pop (would escape the repo
        root) must return None (fail-closed sentinel), never silently drop
        the `..` or leave it in the result."""
        for p in ("..", "../x", "x/../../outside", "x/../..", "a/b/../../../c"):
            with self.subTest(p=p):
                self.assertIsNone(fd._normalize_path_for_matching(p))

    def test_fully_empty_result_is_abnormal(self):
        for p in ("", ".", "./", "././.", "/"):
            with self.subTest(p=p):
                self.assertIsNone(fd._normalize_path_for_matching(p))


class BoundaryGlobEvasionRegressionTest(unittest.TestCase):
    """Unit-level regression for the CONFIRMED evasion (task brief, verified
    against `b7491f8`): a START-ANCHORED glob failed to match ANY of the
    mangled forms of a genuinely in-boundary path. Calls
    `evaluate_boundary_escalation` directly with hand-built
    `delta_changed_paths` — no real git required to prove the MATCHING
    decision itself is fixed (the crafted-tree class below additionally
    proves the mangled forms are reachable through real git plumbing)."""

    def _load(self, manifest_text: str) -> fd.BoundaryManifestLoad:
        manifest = fd._parse_boundary_manifest_bytes(manifest_text.encode("utf-8"), path=fd.BOUNDARY_MANIFEST_PATH, source_rev="f" * 40)
        return fd.BoundaryManifestLoad(disposition=fd.MANIFEST_DISPOSITION_PRESENT, manifest=manifest, ref=manifest.to_ref())

    def test_confirmed_evasions_now_escalate_workflows_glob(self):
        load = self._load('[deployment]\nglobs = [".github/workflows/**"]\n')
        for mangled in (
            "./.github/workflows/ci.yml",
            ".github//workflows/ci.yml",
            "x/../.github/workflows/ci.yml",
        ):
            with self.subTest(mangled=mangled):
                esc = fd.evaluate_boundary_escalation(load, (mangled,))
                self.assertTrue(esc.required, f"{mangled!r} must escalate — pre-fix this EVADED")
                self.assertEqual(esc.trigger, "deployment")

    def test_mangled_glob_side_mirrors_mangled_path_side_symmetry(self):
        """Round-7 (Consiliency/agent-harness#191, codex round-7 CR) glob-side
        mirror of the round-6 path-side fix above: BOTH a mangled PATH
        (`./auth/x`) and a mangled GLOB (`./auth/**`) must resolve to the
        SAME normalized location and agree — matching is symmetric on
        both sides of the comparison, not just the path side."""
        load = self._load('[auth]\nglobs = ["./auth/**"]\n')
        # a mangled glob (normalizes to "auth/**") against a CLEAN path.
        esc_clean_path = fd.evaluate_boundary_escalation(load, ("auth/x",))
        self.assertTrue(esc_clean_path.required)
        self.assertEqual(esc_clean_path.trigger, "auth")
        # a CLEAN glob against a mangled path (round-6's own case, restated
        # here for the direct side-by-side comparison).
        load_clean_glob = self._load('[auth]\nglobs = ["auth/**"]\n')
        esc_mangled_path = fd.evaluate_boundary_escalation(load_clean_glob, ("./auth/x",))
        self.assertTrue(esc_mangled_path.required)
        self.assertEqual(esc_mangled_path.trigger, "auth")
        # BOTH mangled simultaneously — the fully symmetric case.
        esc_both_mangled = fd.evaluate_boundary_escalation(load, ("./auth/x",))
        self.assertTrue(esc_both_mangled.required, "a mangled glob AND a mangled path must still agree and escalate")
        self.assertEqual(esc_both_mangled.trigger, "auth")

    def test_glob_dotdot_after_globstar_is_rejected_not_under_matched(self):
        """Round-8 (Consiliency/agent-harness#191, codex round-8 CR) — direct
        reproduction of the CONFIRMED bug at the matching layer (as opposed
        to `BoundaryManifestLoadTest.
        test_glob_dotdot_after_globstar_manifest_is_malformed_escalates_every_delta`'s
        end-to-end/manifest-level reproduction). PRE-FIX (round-7):
        `_translate_glob_to_regex("**/../auth/**")` popped the `**` on `..`
        and compiled to the ANCHORED `^auth(?:/.*)?$`, which did NOT match
        `x/auth/login.py` — the `_normalize_path_for_matching` form of the
        mangled changed path `x/y/../auth/login.py` — an UNDER-match: a
        surface declared `[auth] globs = ["**/../auth/**"]` silently failed
        to protect a real in-boundary delta. POST-FIX (round-8): the glob is
        rejected outright at translate time (`BoundaryManifestInvalid`), so
        it can never reach a matching decision at all — the manifest that
        declares it is malformed and escalates unconditionally instead
        (verified end-to-end in `BoundaryManifestLoadTest`)."""
        with self.assertRaises(fd.BoundaryManifestInvalid):
            fd._translate_glob_to_regex("**/../auth/**")

    def test_mangled_path_dotdot_still_resolves_against_normal_glob_symmetry_preserved(self):
        """Symmetry check: round-8 changes ONLY the glob-side `..` policy
        (reject, not resolve). The PATH-side `..`-resolution
        (`_normalize_path_for_matching`, used for every changed path) is
        UNTOUCHED — a mangled PATH (`x/../auth/login.py`, normalizing to
        `auth/login.py`, git's real on-disk collapse) must still escalate
        against a perfectly NORMAL, unmangled glob (`**/auth/**`), exactly
        as round-6 established. This is the "legit case" the round-8 task
        brief requires stay green: paths keep resolving `..`; only globs
        stop."""
        load = self._load('[auth]\nglobs = ["**/auth/**"]\n')
        esc = fd.evaluate_boundary_escalation(load, ("x/../auth/login.py",))
        self.assertTrue(esc.required, "a mangled PATH must still resolve '..' and escalate against a normal glob")
        self.assertEqual(esc.trigger, "auth")

    def test_confirmed_evasions_now_escalate_dockerfile_glob(self):
        load = self._load('[deployment]\nglobs = ["Dockerfile*"]\n')
        for mangled in ("./Dockerfile", "x/../Dockerfile"):
            with self.subTest(mangled=mangled):
                esc = fd.evaluate_boundary_escalation(load, (mangled,))
                self.assertTrue(esc.required, f"{mangled!r} must escalate — pre-fix this EVADED")
                self.assertEqual(esc.trigger, "deployment")

    def test_unrelated_mangled_path_still_does_not_escalate(self):
        """Sanity check that normalization does not over-trigger: a mangled
        path that normalizes to something genuinely OUTSIDE any declared
        boundary must still not escalate."""
        load = self._load('[deployment]\nglobs = [".github/workflows/**"]\n')
        esc = fd.evaluate_boundary_escalation(load, ("./src/unrelated/thing.py",))
        self.assertFalse(esc.required)

    def test_abnormal_root_escaping_path_forces_escalation(self):
        load = self._load('[deployment]\nglobs = [".github/workflows/**"]\n')
        esc = fd.evaluate_boundary_escalation(load, ("x/../../outside",))
        self.assertTrue(esc.required)
        self.assertEqual(esc.trigger, fd.ESCALATION_TRIGGER_ABNORMAL_PATH)

    def test_mangled_manifest_path_itself_still_forces_manifest_modified_trigger(self):
        """The manifest-touch check (highest priority after manual/abnormal)
        must also be normalization-aware: a mangled path to the manifest
        itself must not evade it either."""
        load = self._load('[deployment]\nglobs = ["**/deploy/**"]\n')
        mangled_manifest_path = "./" + fd.BOUNDARY_MANIFEST_PATH
        esc = fd.evaluate_boundary_escalation(load, (mangled_manifest_path, "unrelated.py"))
        self.assertTrue(esc.required)
        self.assertEqual(esc.trigger, fd.ESCALATION_TRIGGER_MANIFEST_MODIFIED)

    def test_legitimate_default_globs_still_match_normalized_in_boundary_paths(self):
        """No-regression check: normalization must not break matching for
        already-clean, never-mangled paths (the overwhelming common case)."""
        load = self._load('[auth_security]\nglobs = ["**/auth/**"]\n')
        esc_in = fd.evaluate_boundary_escalation(load, ("src/auth/login.py",))
        self.assertTrue(esc_in.required)
        esc_out = fd.evaluate_boundary_escalation(load, ("src/other.py",))
        self.assertFalse(esc_out.required)


class CraftedTreeGlobEvasionTest(GitRepoTestCase):
    """End-to-end: crafted commit trees (`git mktree`/`git commit-tree`,
    bypassing `verify_path`/`git add` entirely — the same hostile-tree
    technique `HostileGitTreeDotGitTest` uses) that make a mangled path
    reach `fab_canonical.enumerate_changed_paths` as a LITERAL changed-path
    string, proving the confirmed evasion's mangled forms are not merely a
    hypothetical string-matching exercise."""

    def _craft_mangled_path_commit(self, base: str, path_segments: Sequence[str], content: str) -> str:
        """Build a head commit, parented on `base`, whose tree adds ONE new
        top-level entry chain reproducing the literal path
        `'/'.join(path_segments)` — segments may be `.` or `..` (git's raw
        tree plumbing accepts both as literal entry names; only a literal
        `/` is ever rejected) — with `content` as the leaf blob's bytes.
        `path_segments` must have at least 2 elements (>=1 directory
        component plus a leaf name)."""
        assert len(path_segments) >= 2, "need at least one directory segment plus a leaf name"
        leaf_name = path_segments[-1]
        dir_segments = path_segments[:-1]
        blob_sha = _git_stdin(self.repo, "hash-object", "-w", "--stdin", input_text=content).strip()
        tree_sha = _git_stdin(self.repo, "mktree", input_text=f"100644 blob {blob_sha}\t{leaf_name}\n").strip()
        for seg in reversed(dir_segments[1:]):
            tree_sha = _git_stdin(self.repo, "mktree", input_text=f"040000 tree {tree_sha}\t{seg}\n").strip()
        base_tree_sha = _rev_parse(self.repo, f"{base}^{{tree}}")
        base_entries = _run(self.repo, "ls-tree", base_tree_sha).stdout
        outer_tree_input = base_entries + f"040000 tree {tree_sha}\t{dir_segments[0]}\n"
        outer_tree_sha = _git_stdin(self.repo, "mktree", input_text=outer_tree_input).strip()
        return _git_stdin(
            self.repo, "commit-tree", outer_tree_sha, "-p", base, "-m", "crafted mangled-path injection", input_text=""
        ).strip()

    def test_dot_prefixed_workflow_path_reachable_and_escalates(self):
        """`./.github/workflows/ci.yml` — empirically confirmed reachable
        via a crafted `.`-named top-level tree entry: `git diff --raw`
        reports the literal mangled path (rc==0). A `.github/workflows/**`
        boundary glob must escalate it (pre-fix, it silently did not)."""
        self.write(fd.BOUNDARY_MANIFEST_PATH, '[deployment]\nglobs = [".github/workflows/**"]\n')
        self.write("src/live.py", "print('hi')\n")
        base = self.commit("c1 base with boundary manifest")
        crafted_head = self._craft_mangled_path_commit(
            base, (".", ".github", "workflows", "ci.yml"), "name: ci\n"
        )

        changed = fc.enumerate_changed_paths(self.repo, base, crafted_head)
        self.assertIn("./.github/workflows/ci.yml", changed)

        load = fd.load_boundary_manifest_at_base(self.repo, base)
        self.assertEqual(load.disposition, fd.MANIFEST_DISPOSITION_PRESENT)
        esc = fd.evaluate_boundary_escalation(load, changed)
        self.assertTrue(esc.required, "the crafted ./.github/workflows/ci.yml delta must escalate")
        self.assertEqual(esc.trigger, "deployment")

    def test_dot_dot_prefixed_dockerfile_path_reachable_and_escalates(self):
        """`x/../Dockerfile` — empirically confirmed reachable via a crafted
        `x` -> `..` tree-entry chain: `git diff --raw` reports the literal
        mangled path (rc==0). A `Dockerfile*` boundary glob must escalate
        it."""
        self.write(fd.BOUNDARY_MANIFEST_PATH, '[deployment]\nglobs = ["Dockerfile*"]\n')
        self.write("src/live.py", "print('hi')\n")
        base = self.commit("c1 base with boundary manifest")
        crafted_head = self._craft_mangled_path_commit(base, ("x", "..", "Dockerfile"), "FROM scratch\n")

        changed = fc.enumerate_changed_paths(self.repo, base, crafted_head)
        self.assertIn("x/../Dockerfile", changed)

        load = fd.load_boundary_manifest_at_base(self.repo, base)
        esc = fd.evaluate_boundary_escalation(load, changed)
        self.assertTrue(esc.required, "the crafted x/../Dockerfile delta must escalate")
        self.assertEqual(esc.trigger, "deployment")

    def test_root_escaping_path_reachable_and_forces_abnormal_escalation(self):
        """`x/../../outside` — empirically confirmed reachable via a crafted
        `x` -> `..` -> `..` tree-entry chain (rc==0). This normalizes to
        `None` (would escape the repo root) and must force whole-patch
        escalation via `ESCALATION_TRIGGER_ABNORMAL_PATH`, independent of
        any glob content."""
        self.write(fd.BOUNDARY_MANIFEST_PATH, '[deployment]\nglobs = ["**/deploy/**"]\n')
        self.write("src/live.py", "print('hi')\n")
        base = self.commit("c1 base with boundary manifest")
        crafted_head = self._craft_mangled_path_commit(base, ("x", "..", "..", "outside"), "sneaky\n")

        changed = fc.enumerate_changed_paths(self.repo, base, crafted_head)
        self.assertIn("x/../../outside", changed)

        load = fd.load_boundary_manifest_at_base(self.repo, base)
        esc = fd.evaluate_boundary_escalation(load, changed)
        self.assertTrue(esc.required)
        self.assertEqual(esc.trigger, fd.ESCALATION_TRIGGER_ABNORMAL_PATH)


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

    def test_mangled_delta_path_intersecting_clean_finding_still_reopens(self):
        """Round-6 (Consiliency/agent-harness#191): a mangled delta path
        that NORMALIZES to a location inside a clean finding's `path_scope`
        must still reopen that finding — pre-fix, the un-normalized literal
        mismatch (`./pkg/a.py` vs `pkg/a.py`) would make the finding look
        DISJOINT and wrongly carry forward without re-review, even though
        the delta genuinely touched the file the finding was about."""
        findings = [_finding("f1", status="clean", path_scope=("pkg/a.py",))]
        for mangled in ("./pkg/a.py", "pkg//a.py", "x/../pkg/a.py"):
            with self.subTest(mangled=mangled):
                result = fd.carry_forward(findings, (mangled,))
                self.assertEqual(result.carried_forward_finding_ids, ())
                self.assertEqual(result.reopened_finding_ids, ("f1",))
                self.assertEqual(result.reasons["f1"], fd.CARRY_FORWARD_REASON_INTERSECTS)

    def test_mangled_path_scope_entry_intersecting_normal_delta_path_still_reopens(self):
        """The dual direction: a finding's OWN `path_scope` entry can be
        mangled too (it is client-supplied, per this module's trust
        boundary) — a mangled `path_scope=("./pkg/a.py",)` must still be
        recognized as covering a normal, un-mangled `pkg/a.py` changed
        path, not silently narrowed into looking disjoint."""
        findings = [_finding("f1", status="clean", path_scope=("./pkg/a.py",))]
        result = fd.carry_forward(findings, ("pkg/a.py",))
        self.assertEqual(result.carried_forward_finding_ids, ())
        self.assertEqual(result.reopened_finding_ids, ("f1",))
        self.assertEqual(result.reasons["f1"], fd.CARRY_FORWARD_REASON_INTERSECTS)

    def test_mangled_delta_path_disjoint_from_scope_still_carries(self):
        """No over-triggering: a mangled delta path that normalizes to
        something genuinely OUTSIDE the finding's path_scope must still
        carry forward."""
        findings = [_finding("f1", status="clean", path_scope=("pkg/a.py",))]
        result = fd.carry_forward(findings, ("./other/unrelated.py",))
        self.assertEqual(result.carried_forward_finding_ids, ("f1",))
        self.assertEqual(result.reasons["f1"], fd.CARRY_FORWARD_REASON_DISJOINT)

    def test_root_escaping_delta_path_forces_reopen_of_every_clean_finding(self):
        """An ABNORMAL (root-escaping) delta path cannot be asserted
        disjoint from anything — it must force every remaining clean
        finding to reopen, fail-closed, regardless of `path_scope`."""
        findings = [
            _finding("f1", status="clean", path_scope=("pkg/a.py",)),
            _finding("f2", status="clean", path_scope=("totally/unrelated/dir/",)),
        ]
        result = fd.carry_forward(findings, ("x/../../outside",))
        self.assertEqual(result.carried_forward_finding_ids, ())
        self.assertEqual(sorted(result.reopened_finding_ids), ["f1", "f2"])
        self.assertTrue(all(r == fd.CARRY_FORWARD_REASON_INTERSECTS for r in result.reasons.values()))

    def test_root_escaping_path_scope_entry_reopens_via_empty_scope_reason(self):
        """A `path_scope` entry that itself fails to normalize (root-escape)
        disqualifies the WHOLE path_scope, same as a blank entry — the
        finding reopens as `empty_path_scope`, not silently narrowed by
        dropping just the bad entry."""
        findings = [_finding("f1", status="clean", path_scope=("x/../../outside",))]
        result = fd.carry_forward(findings, ("totally/unrelated.py",))
        self.assertEqual(result.carried_forward_finding_ids, ())
        self.assertEqual(result.reopened_finding_ids, ("f1",))
        self.assertEqual(result.reasons["f1"], fd.CARRY_FORWARD_REASON_EMPTY_SCOPE)

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
