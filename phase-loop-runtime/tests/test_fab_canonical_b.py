"""FAB Lane B (Consiliency/agent-harness#191) — canonical bytes, equivalence,
and hostile-git hardening. Deliberately UNMARKED (no ``dotfiles_integration``),
so CI's ``-m "not dotfiles_integration"`` runs this module (the goal-id-inc2
lesson). Uses REAL temporary git repositories (``git init``/commits) for every
digest/equivalence path — no mocked git for the core canonicalization logic."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from phase_loop_runtime import fab_canonical as fc
from phase_loop_runtime import fab_provenance as fp

_GIT = shutil.which("git")


def _run(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        raise AssertionError(f"git {args} failed: {result.stderr}")
    return result


def _rev_parse(repo: Path, ref: str = "HEAD") -> str:
    return _run(repo, "rev-parse", ref).stdout.strip()


class GitRepoTestCase(unittest.TestCase):
    """Base fixture: a working tree with TWO remotes —

      * ``origin``  -> a github.com-shaped URL string (never actually fetched;
        used ONLY so `resolve_broker_repo_identity` resolves a real slug,
        exactly like production), and
      * ``fetchsrc`` -> a real local bare repo `equivalent()`'s `origin=`
        parameter is pointed at for the actual `git fetch`.

    This decouples "what slug does resolve_broker_repo_identity report" from
    "where does the fetch actually reach", without needing to fight git's own
    `url.<x>.insteadOf` rewriting (which, unlike a naive reading of `git remote
    get-url --help`, DOES rewrite `get-url`'s own output — confirmed
    empirically while building this fixture)."""

    REPO_SLUG = "github.com/testorg/testrepo"

    def setUp(self) -> None:
        if _GIT is None:  # pragma: no cover - CI always has git
            self.skipTest("git not available")
        self._tmp = tempfile.mkdtemp(prefix="fab-canonical-b-")
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))
        self.origin_dir = Path(self._tmp) / "origin.git"
        _run(Path(self._tmp), "init", "-q", "--bare", str(self.origin_dir), check=False)
        subprocess.run(["git", "init", "-q", "--bare", str(self.origin_dir)], check=True)
        self.repo = Path(self._tmp) / "work"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        _run(self.repo, "config", "user.email", "t@example.com")
        _run(self.repo, "config", "user.name", "Test")
        _run(self.repo, "remote", "add", "origin", "git@github.com:testorg/testrepo.git")
        _run(self.repo, "remote", "add", "fetchsrc", str(self.origin_dir))

    def write(self, relpath: str, content: bytes | str) -> Path:
        path = self.repo / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            content = content.encode("utf-8")
        path.write_bytes(content)
        return path

    def commit(self, message: str) -> str:
        _run(self.repo, "add", "-A")
        _run(self.repo, "commit", "-q", "--allow-empty", "-m", message)
        return _rev_parse(self.repo)

    def commit_no_add(self, message: str) -> str:
        """Like `commit`, but skips `git add -A` — needed after
        `git update-index --add --cacheinfo` (a gitlink entry with no real
        on-disk directory): `add -A` treats the gitlink as a working-tree
        deletion relative to the index and un-stages it (confirmed
        empirically), which is exactly wrong for staging a crafted gitlink."""
        _run(self.repo, "commit", "-q", "--allow-empty", "-m", message)
        return _rev_parse(self.repo)

    def push_main(self, ref: str = "HEAD") -> None:
        _run(self.repo, "push", "-q", "-f", "fetchsrc", f"{ref}:refs/heads/main")

    def digest(self, base_sha: str, head_sha: str, **kwargs) -> str:
        return fc.patch_digest(self.repo, base_sha, head_sha, repo_slug=self.REPO_SLUG, **kwargs)


# --------------------------------------------------------------------------- #
# patch_digest — core canonicalization
# --------------------------------------------------------------------------- #


class PatchDigestBasicTest(GitRepoTestCase):
    def test_deterministic_across_recomputation(self):
        self.write("a.py", "hello\n")
        base = self.commit("c1")
        self.write("a.py", "hello world\n")
        head = self.commit("c2")
        d1 = self.digest(base, head)
        d2 = self.digest(base, head)
        self.assertEqual(d1, d2)
        self.assertRegex(d1, r"^[0-9a-f]{64}$")

    def test_no_changes_is_a_legitimate_empty_digest_not_an_error(self):
        base = self.commit("c1 (empty)")
        d = self.digest(base, base)
        self.assertRegex(d, r"^[0-9a-f]{64}$")

    def test_acceptance_2_unrelated_byte_changes_digest(self):
        """Acceptance criterion 2 (design §9): adding an UNRELATED byte to any
        changed file changes patch_digest -> equivalence FAILS closed."""
        self.write("a.py", "hello\n")
        base = self.commit("c1")
        self.write("a.py", "hello world\n")
        head1 = self.commit("c2")
        d1 = self.digest(base, head1)

        self.write("a.py", "hello world!\n")  # one extra unrelated byte
        head2 = self.commit("c3")
        d2 = self.digest(base, head2)
        self.assertNotEqual(d1, d2)

    def test_header_binds_repo_slug_and_base_sha(self):
        """T1/T10: a digest computed against a different base or repo slug can
        never compare equal, because both are baked into the header."""
        self.write("a.py", "hello\n")
        base = self.commit("c1")
        self.write("a.py", "hello world\n")
        head = self.commit("c2")
        d_same = self.digest(base, head)
        d_other_repo = fc.patch_digest(self.repo, base, head, repo_slug="github.com/other/repo")
        self.assertNotEqual(d_same, d_other_repo)

        # Isolate the base_sha binding from record-content differences: reset
        # to `base`'s tree and make an `--allow-empty` commit there. This
        # `base_twin` has an IDENTICAL TREE to `base` (so `diff(base_twin,
        # head)` produces the byte-IDENTICAL record stream as `diff(base,
        # head)`) but a DIFFERENT commit SHA. If the header did NOT bind
        # base_sha, these two digests would collide.
        _run(self.repo, "reset", "-q", "--hard", base)
        base_twin = self.commit("c1-twin (empty, identical tree to base)")
        self.assertNotEqual(base, base_twin)
        d_twin_base = fc.patch_digest(self.repo, base_twin, head, repo_slug=self.REPO_SLUG)
        self.assertNotEqual(d_same, d_twin_base)

    def test_no_normalization_of_whitespace_or_eol(self):
        """design §3.5: no whitespace/EOL normalization — CRLF vs LF content
        diffed against the SAME base must produce DIFFERENT digests (both
        sides are real, non-empty diffs, so this isn't a trivial empty-diff
        comparison)."""
        self.write("a.py", "line0\n")
        base = self.commit("c1")

        self.write("a.py", "line0\nline1\r\nline2\r\n")
        head_crlf = self.commit("c2 crlf")
        d_crlf = self.digest(base, head_crlf)

        # Rewind the working tree to the base state (a sibling commit off the
        # SAME base, not a descendant of head_crlf) so both digests are
        # diffed against the identical base_sha.
        _run(self.repo, "reset", "-q", "--hard", base)
        self.write("a.py", "line0\nline1\nline2\n")
        head_lf = self.commit("c3 lf")
        d_lf = self.digest(base, head_lf)

        self.assertNotEqual(d_crlf, d_lf)


class PatchDigestOidVsBytesTest(GitRepoTestCase):
    """T3: content_sha256 is OUR OWN sha256 of the actual bytes, never git's
    reported sha1 OID — proven structurally (different algorithm => different
    length/value) and by direct recomputation."""

    def test_content_hash_is_sha256_of_real_bytes_not_the_reported_oid(self):
        self.write("a.py", "alpha\n")
        base = self.commit("c1")
        self.write("a.py", "alpha content that changed\n")
        head = self.commit("c2")

        raw = fc._git_diff_raw_bytes(self.repo, base, head)
        entries = fc._iter_raw_diff_entries(raw)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        reported_oid = entry.new_oid.decode("ascii")
        self.assertEqual(len(reported_oid), 40)  # git's sha1

        hashes = fc._cat_file_content_hashes(self.repo, [entry.new_oid])
        content_sha256 = hashes[entry.new_oid]
        self.assertEqual(len(content_sha256), 64)  # our sha256
        self.assertNotEqual(content_sha256, reported_oid)

        expected = hashlib.sha256(b"alpha content that changed\n").hexdigest()
        self.assertEqual(content_sha256, expected)


class PatchDigestModeTypeTest(GitRepoTestCase):
    """T8: mode/type swaps with IDENTICAL bytes must still change the digest."""

    def test_pure_mode_change_identical_bytes_changes_digest(self):
        path = self.write("sub/b.py", "same bytes\n")
        path.chmod(0o644)
        base = self.commit("c1 644")
        path.chmod(0o755)
        head = self.commit("c2 755, same bytes")

        d_before = self.digest(base, base)
        d_after = self.digest(base, head)
        self.assertNotEqual(d_before, d_after)

        raw = fc._git_diff_raw_bytes(self.repo, base, head)
        entries = fc._iter_raw_diff_entries(raw)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].new_mode, "100755")
        self.assertEqual(entries[0].status, "M")

    def test_file_to_symlink_typechange_changes_digest(self):
        self.write("sub/c.py", "target contents\n")
        self.write("a.py", "hello\n")
        base = self.commit("c1")
        (self.repo / "sub/c.py").unlink()
        (self.repo / "sub/c.py").symlink_to("a.py")
        head = self.commit("c2 typechange to symlink")

        raw = fc._git_diff_raw_bytes(self.repo, base, head)
        entries = fc._iter_raw_diff_entries(raw)
        typechange = [e for e in entries if e.status == "T"]
        self.assertEqual(len(typechange), 1)
        self.assertEqual(typechange[0].new_mode, "120000")

        d = self.digest(base, head)
        d_noop = self.digest(base, base)
        self.assertNotEqual(d, d_noop)


class PatchDigestDeleteTest(GitRepoTestCase):
    """T11: deleting a path removes its record, changing the sorted stream; a
    delete record carries the "-" sentinel, never a real 64-hex digest."""

    def test_delete_record_uses_sentinel_and_zero_mode(self):
        self.write("a.py", "hello\n")
        base = self.commit("c1")
        (self.repo / "a.py").unlink()
        head = self.commit("c2 delete a.py")

        raw = fc._git_diff_raw_bytes(self.repo, base, head)
        entries = fc._iter_raw_diff_entries(raw)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].status, "D")

        records = fc._build_records(self.repo, entries, allow_gitlinks=False)
        self.assertEqual(len(records), 1)
        _path, record = records[0]
        status, new_mode, content_hex, path_and_nul = record.split(b"\x00", 3)
        self.assertEqual(status, b"D")
        self.assertEqual(new_mode, fc.DELETED_NEW_MODE.encode("ascii"))
        self.assertEqual(content_hex, fc.DELETED_CONTENT_SENTINEL.encode("ascii"))

    def test_deleting_a_path_changes_the_digest(self):
        self.write("a.py", "hello\n")
        self.write("b.py", "keep me\n")
        base = self.commit("c1")
        (self.repo / "a.py").unlink()
        head = self.commit("c2 delete a.py only")
        d_delete_only = self.digest(base, head)

        # A digest that never saw the delete (e.g. base==head) must differ.
        d_noop = self.digest(base, base)
        self.assertNotEqual(d_delete_only, d_noop)

    def test_sentinel_can_never_collide_with_a_real_digest(self):
        self.assertEqual(len(fc.DELETED_CONTENT_SENTINEL), 1)
        self.assertNotRegex(fc.DELETED_CONTENT_SENTINEL, r"^[0-9a-f]{64}$")


class PatchDigestGitlinkTest(GitRepoTestCase):
    """T17 / design §3.3 finding 6: gitlinks default-reject."""

    def _add_gitlink(self, path: str = "sub") -> None:
        fake_sha = "a" * 40
        _run(self.repo, "update-index", "--add", "--cacheinfo", f"160000,{fake_sha},{path}")

    def test_gitlink_add_rejected_by_default(self):
        base = self.commit("c1 (empty)")
        self._add_gitlink()
        head = self.commit_no_add("c2 add gitlink")
        with self.assertRaises(fc.GitlinkRejected):
            self.digest(base, head)

    def test_gitlink_reject_is_a_patch_digest_invalid_subclass(self):
        self.assertTrue(issubclass(fc.GitlinkRejected, fc.PatchDigestInvalid))
        self.assertTrue(issubclass(fc.PatchDigestInvalid, fp.ProvenanceInvalid))

    def test_gitlink_delete_is_not_rejected(self):
        """Deleting a gitlink carries new_mode=000000 (not 160000) — no content
        trust question arises, so this is a normal delete record, not a
        reject."""
        self._add_gitlink()
        base = self.commit_no_add("c1 add gitlink")
        _run(self.repo, "rm", "-q", "--cached", "sub")
        head = self.commit_no_add("c2 remove gitlink")
        d = self.digest(base, head)  # must not raise
        self.assertRegex(d, r"^[0-9a-f]{64}$")

    def test_allow_gitlinks_true_is_a_documented_stub_not_silently_lenient(self):
        base = self.commit("c1 (empty)")
        self._add_gitlink()
        head = self.commit_no_add("c2 add gitlink")
        with self.assertRaises(fc.PatchDigestInvalid):
            self.digest(base, head, allow_gitlinks=True)


class PatchDigestHostileGitTest(GitRepoTestCase):
    """T17 / design §3.2 finding 6: rc!=0 and cat-file missing/malformed both
    invalidate — never an empty/partial result."""

    def test_unresolvable_base_sha_invalidates(self):
        base_looks_valid_but_unknown = "a" * 40
        self.write("a.py", "hello\n")
        head = self.commit("c1")
        with self.assertRaises(fc.PatchDigestInvalid):
            self.digest(base_looks_valid_but_unknown, head)

    def test_non_full_sha_revision_syntax_rejected_before_any_git_call(self):
        self.write("a.py", "hello\n")
        head = self.commit("c1")
        with self.assertRaises(fc.PatchDigestInvalid):
            self.digest("HEAD~1", head)
        with self.assertRaises(fc.PatchDigestInvalid):
            self.digest(head, "HEAD")

    def test_cat_file_missing_object_invalidates(self):
        with mock.patch.object(fc, "_cat_file_content_hashes") as mocked:
            mocked.side_effect = fc.PatchDigestInvalid("cat-file reports object missing (fail-closed): simulated")
            self.write("a.py", "hello\n")
            base = self.commit("c1")
            self.write("a.py", "hello world\n")
            head = self.commit("c2")
            with self.assertRaises(fc.PatchDigestInvalid):
                self.digest(base, head)

    def test_cat_file_batch_missing_response_line_raises(self):
        oid = b"a" * 40
        with mock.patch.object(subprocess, "Popen") as mocked_popen:
            proc = mock.MagicMock()
            proc.stdin = mock.MagicMock()
            proc.stdout = mock.MagicMock()
            proc.stdout.readline.return_value = f"{oid.decode()} missing\n".encode("ascii")
            proc.wait.return_value = 0
            mocked_popen.return_value = proc
            with self.assertRaises(fc.PatchDigestInvalid):
                fc._cat_file_content_hashes(self.repo, [oid])

    def test_diff_nonzero_rc_never_treated_as_empty(self):
        """design §3.2 finding 6: rc!=0 must NEVER be silently treated as an
        empty/no-changes result — it must always raise."""
        with mock.patch.object(subprocess, "run") as mocked_run:
            mocked_run.return_value = subprocess.CompletedProcess(
                args=["git"], returncode=1, stdout=b"", stderr=b"simulated failure"
            )
            with self.assertRaises(fc.PatchDigestInvalid):
                fc._git_diff_raw_bytes(self.repo, "a" * 40, "b" * 40)


class PatchDigestPathEncodingTest(GitRepoTestCase):
    """Non-ASCII / CR-containing paths round-trip: bytes discipline, no
    universal-newline collapse (mirrors #250's a\\r.py / a\\r\\n.py / a\\n.py
    distinctness requirement)."""

    def test_non_ascii_path_round_trips(self):
        base = self.commit("c1 (empty)")
        name = "café.py"  # café.py
        self.write(name, "content\n")
        head = self.commit("c2 non-ascii path")
        raw = fc._git_diff_raw_bytes(self.repo, base, head)
        entries = fc._iter_raw_diff_entries(raw)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].path, name.encode("utf-8"))
        d = self.digest(base, head)
        self.assertRegex(d, r"^[0-9a-f]{64}$")

    def test_cr_containing_paths_are_distinct(self):
        base = self.commit("c1 (empty)")
        import os

        p1 = self.repo / "a\rx.py"
        p2 = self.repo / "a\r\nx.py"
        p3 = self.repo / "a\nx.py"
        for p in (p1, p2, p3):
            p.write_bytes(b"content\n")
        _run(self.repo, "add", "-A")
        _run(self.repo, "commit", "-q", "-m", "c2 cr paths")
        head = _rev_parse(self.repo)
        raw = fc._git_diff_raw_bytes(self.repo, base, head)
        entries = fc._iter_raw_diff_entries(raw)
        paths = {e.path for e in entries}
        # All three distinct byte sequences must survive as three DISTINCT
        # records — no b"\r"/b"\r\n" -> b"\n" collapse.
        self.assertEqual(
            paths,
            {os.fsencode("a\rx.py"), os.fsencode("a\r\nx.py"), os.fsencode("a\nx.py")},
        )
        self.assertEqual(len(entries), 3)


# --------------------------------------------------------------------------- #
# equivalent() — base binding + invalidation
# --------------------------------------------------------------------------- #


class EquivalenceTest(GitRepoTestCase):
    def _binding(self, base_sha: str, expected_digest: str, base_ref: str = "main") -> fc.EquivalenceBinding:
        return fc.EquivalenceBinding(
            repo_slug=self.REPO_SLUG,
            base_ref_name=base_ref,
            base_sha=base_sha,
            expected_head_digest=expected_digest,
        )

    def _setup_pr(self):
        self.write("a.py", "hello\n")
        base = self.commit("c1")
        self.push_main()
        _run(self.repo, "checkout", "-qb", "pr1")
        self.write("a.py", "hello world\n")
        head = self.commit("c2 on pr1")
        return base, head

    def test_same_state_is_equivalent(self):
        base, head = self._setup_pr()
        expected = self.digest(base, head)
        binding = self._binding(base, expected)
        result = fc.equivalent(binding, self.repo, live_base_ref_name="main", live_head_sha=head, origin="fetchsrc")
        self.assertEqual(result.result, fp.EQUIVALENCE_EQUIVALENT)
        self.assertIsNone(result.reason)
        self.assertEqual(result.observed_head_digest, expected)
        self.assertEqual(result.live_base_sha, base)

    def test_acceptance_3_rebase_onto_different_base_invalidates(self):
        base, head = self._setup_pr()
        expected = self.digest(base, head)
        binding = self._binding(base, expected)

        _run(self.repo, "checkout", "-q", "main")
        self.write("unrelated.py", "advance\n")
        self.commit("advance main")
        self.push_main()
        _run(self.repo, "checkout", "-q", "pr1")
        _run(self.repo, "rebase", "-q", "main")
        head_rebased = _rev_parse(self.repo)

        result = fc.equivalent(
            binding, self.repo, live_base_ref_name="main", live_head_sha=head_rebased, origin="fetchsrc"
        )
        self.assertEqual(result.result, fp.EQUIVALENCE_INVALIDATED)
        self.assertTrue(result.reason.startswith(fc.REASON_BASE_SHA_MISMATCH))

    def test_acceptance_3_conflict_resolution_merge_on_head_invalidates(self):
        base, head = self._setup_pr()
        expected = self.digest(base, head)
        binding = self._binding(base, expected)

        _run(self.repo, "checkout", "-q", "main")
        self.write("a.py", "hello from main\n")
        self.commit("main advances, touching a.py (conflict source)")
        self.push_main()
        _run(self.repo, "checkout", "-q", "pr1")
        # Merge main into pr1 (conflict resolution committed ON the head).
        merge = subprocess.run(
            ["git", "-C", str(self.repo), "merge", "-q", "--no-edit", "main"], capture_output=True, text=True
        )
        if merge.returncode != 0:
            # Real conflict: resolve by taking ours and completing the merge commit.
            _run(self.repo, "checkout", "-q", "--ours", "a.py")
            _run(self.repo, "add", "-A")
            _run(self.repo, "commit", "-q", "-m", "resolve conflict on head")
        head_merged = _rev_parse(self.repo)

        result = fc.equivalent(
            binding, self.repo, live_base_ref_name="main", live_head_sha=head_merged, origin="fetchsrc"
        )
        self.assertEqual(result.result, fp.EQUIVALENCE_INVALIDATED)
        self.assertTrue(result.reason.startswith(fc.REASON_BASE_SHA_MISMATCH))

    def test_retarget_invalidates_even_without_a_real_branch(self):
        base, head = self._setup_pr()
        expected = self.digest(base, head)
        binding = self._binding(base, expected)
        result = fc.equivalent(
            binding, self.repo, live_base_ref_name="release/2.0", live_head_sha=head, origin="fetchsrc"
        )
        self.assertEqual(result.result, fp.EQUIVALENCE_INVALIDATED)
        self.assertTrue(result.reason.startswith(fc.REASON_BASE_REF_RETARGETED))

    def test_i10_reordered_noop_commit_same_net_content_passes(self):
        """I10: reordered/no-op-extra-commit history whose NET content is
        identical is NOT invalidated (design §4.5 — safe under delta identity,
        §4.2)."""
        base, head = self._setup_pr()
        expected = self.digest(base, head)
        binding = self._binding(base, expected)

        # Add a no-op commit: add a byte then revert it (net content identical).
        self.write("a.py", "hello world\nTEMP\n")
        self.commit("temp change")
        self.write("a.py", "hello world\n")
        head_noop = self.commit("revert temp change (net no-op)")

        result = fc.equivalent(
            binding, self.repo, live_base_ref_name="main", live_head_sha=head_noop, origin="fetchsrc"
        )
        self.assertEqual(result.result, fp.EQUIVALENCE_EQUIVALENT)

    def test_i11_base_tip_advance_pr_unchanged_passes(self):
        """I11: a concurrent base-tip advance B->B' with the PR unchanged is
        NOT invalidated (design §4.2/§4.5)."""
        base, head = self._setup_pr()
        expected = self.digest(base, head)
        binding = self._binding(base, expected)

        _run(self.repo, "checkout", "-q", "main")
        self.write("unrelated.py", "advance\n")
        self.commit("advance main, unrelated to the PR")
        self.push_main()
        _run(self.repo, "checkout", "-q", "pr1")

        result = fc.equivalent(binding, self.repo, live_base_ref_name="main", live_head_sha=head, origin="fetchsrc")
        self.assertEqual(result.result, fp.EQUIVALENCE_EQUIVALENT)
        self.assertEqual(result.live_base_sha, base)

    def test_acceptance_2_content_drift_invalidates_at_equivalence_level(self):
        base, head = self._setup_pr()
        expected = self.digest(base, head)
        binding = self._binding(base, expected)

        self.write("a.py", "hello world!\n")  # one extra unrelated byte
        head2 = self.commit("c3 unrelated byte")

        result = fc.equivalent(binding, self.repo, live_base_ref_name="main", live_head_sha=head2, origin="fetchsrc")
        self.assertEqual(result.result, fp.EQUIVALENCE_INVALIDATED)
        self.assertEqual(result.reason, fc.REASON_CONTENT_DRIFT)
        self.assertNotEqual(result.observed_head_digest, result.expected_head_digest)

    def test_repo_mismatch_invalidates(self):
        base, head = self._setup_pr()
        expected = self.digest(base, head)
        binding = fc.EquivalenceBinding(
            repo_slug="github.com/other/repo", base_ref_name="main", base_sha=base, expected_head_digest=expected
        )
        result = fc.equivalent(binding, self.repo, live_base_ref_name="main", live_head_sha=head, origin="fetchsrc")
        self.assertEqual(result.result, fp.EQUIVALENCE_INVALIDATED)
        self.assertTrue(result.reason.startswith(fc.REASON_REPO_MISMATCH))

    def test_fetch_failure_invalidates(self):
        base, head = self._setup_pr()
        expected = self.digest(base, head)
        binding = self._binding(base, expected)
        result = fc.equivalent(
            binding, self.repo, live_base_ref_name="main", live_head_sha=head, origin="nonexistent-remote"
        )
        self.assertEqual(result.result, fp.EQUIVALENCE_INVALIDATED)
        self.assertTrue(result.reason.startswith(fc.REASON_GIT_ERROR))

    def test_gitlink_in_live_head_is_reported_as_gitlink_rejected(self):
        base, head = self._setup_pr()
        expected = self.digest(base, head)
        binding = self._binding(base, expected)

        fake_sha = "a" * 40
        _run(self.repo, "update-index", "--add", "--cacheinfo", f"160000,{fake_sha},sub")
        head_gitlink = self.commit_no_add("add gitlink on pr1")

        result = fc.equivalent(
            binding, self.repo, live_base_ref_name="main", live_head_sha=head_gitlink, origin="fetchsrc"
        )
        self.assertEqual(result.result, fp.EQUIVALENCE_INVALIDATED)
        self.assertTrue(result.reason.startswith(fc.REASON_GITLINK_REJECTED))


class EquivalenceBindingFromArtifactTest(unittest.TestCase):
    def _artifact(self, *, with_delta_chain: bool, patch_digest_value):
        base = fp.BaseBinding(ref_identity="github.com/testorg/testrepo#main", base_sha="a" * 40)
        boundary = fp.BoundaryManifestRef(path=".advisor-board/boundaries.toml", source_rev="a" * 40, digest="b" * 64)
        scope = fp.ReviewScope(mode="whole-patch", reviewed_material_digest="c" * 64, covers_patch_digest=patch_digest_value)
        candidate = fp.CandidateRecord(head_sha="e" * 40, review_scope=scope, patch_digest=patch_digest_value)
        delta_chain = ()
        if with_delta_chain:
            escalation = fp.Escalation(required=False, trigger=None)
            delta_scope = fp.ReviewScope(mode="delta-only", reviewed_material_digest="d" * 64, covers_patch_digest=None)
            record = fp.DeltaReviewRecord.build(
                policy=boundary.to_dict(),
                review_scope=delta_scope,
                material_digests=(),
                parent_digest=patch_digest_value,
                parent_chain_digest="f" * 64,
                delta_head_sha="1" * 40,
                delta_changed_paths=(),
                delta_commits=(),
                resolved_finding_ids=(),
                carried_forward_finding_ids=(),
                reopened_finding_ids=(),
                resulting_head_digest="9" * 64,
                status=fp.DELTA_STATUS_REVIEWED_CLEAN,
                escalation=escalation,
            )
            delta_chain = (record,)
        return fp.ReviewProvenanceArtifact.build(
            repo="github.com/testorg/testrepo",
            base=base,
            boundary_manifest=boundary,
            candidate=candidate,
            delta_chain=delta_chain,
        )

    def test_degenerate_no_delta_chain_resolves(self):
        artifact = self._artifact(with_delta_chain=False, patch_digest_value="7" * 64)
        binding = fc.EquivalenceBinding.from_provenance_artifact(artifact)
        self.assertEqual(binding.repo_slug, "github.com/testorg/testrepo")
        self.assertEqual(binding.base_ref_name, "main")
        self.assertEqual(binding.base_sha, "a" * 40)
        self.assertEqual(binding.expected_head_digest, "7" * 64)

    def test_delta_chain_present_fails_closed_not_a_guess(self):
        artifact = self._artifact(with_delta_chain=True, patch_digest_value="7" * 64)
        with self.assertRaises(fc.PatchDigestInvalid):
            fc.EquivalenceBinding.from_provenance_artifact(artifact)

    def test_no_patch_digest_yet_fails_closed(self):
        artifact = self._artifact(with_delta_chain=False, patch_digest_value=None)
        with self.assertRaises(fc.PatchDigestInvalid):
            fc.EquivalenceBinding.from_provenance_artifact(artifact)


class FrozenInterfaceTest(unittest.TestCase):
    """IF-0-FAB-B-1: the header prefix, sentinel, and reason-code prefixes are
    part of the frozen contract — this test pins their literal values so a
    silent rename doesn't slip through unnoticed."""

    def test_header_prefix(self):
        self.assertEqual(fc.CANONICAL_BYTES_HEADER_PREFIX, b"fab.canonical-bytes.v2\x00")

    def test_reason_code_constants_are_distinct_strings(self):
        codes = {
            fc.REASON_REPO_IDENTITY_UNRESOLVABLE,
            fc.REASON_REPO_MISMATCH,
            fc.REASON_BASE_REF_RETARGETED,
            fc.REASON_GIT_ERROR,
            fc.REASON_GITLINK_REJECTED,
            fc.REASON_BASE_SHA_MISMATCH,
            fc.REASON_CONTENT_DRIFT,
        }
        self.assertEqual(len(codes), 7)

    def test_gitlink_rejected_and_patch_digest_invalid_chain_to_provenance_invalid(self):
        self.assertTrue(issubclass(fc.PatchDigestInvalid, fp.ProvenanceInvalid))
        self.assertTrue(issubclass(fc.GitlinkRejected, fc.PatchDigestInvalid))


if __name__ == "__main__":
    unittest.main()
