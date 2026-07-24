"""FAB Lane B (Consiliency/agent-harness#191) — canonical bytes, equivalence,
and hostile-git hardening. Deliberately UNMARKED (no ``dotfiles_integration``),
so CI's ``-m "not dotfiles_integration"`` runs this module (the goal-id-inc2
lesson). Uses REAL temporary git repositories (``git init``/commits) for every
digest/equivalence path — no mocked git for the core canonicalization logic."""

from __future__ import annotations

import hashlib
import io
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
        content_sha256, obj_type = hashes[entry.new_oid]
        self.assertEqual(obj_type, "blob")
        self.assertEqual(len(content_sha256), 64)  # our sha256
        self.assertNotEqual(content_sha256, reported_oid)

        expected = hashlib.sha256(b"alpha content that changed\n").hexdigest()
        self.assertEqual(content_sha256, expected)


def _record_without_mode(record: bytes) -> bytes:
    """Test-only helper (finding 3 negative control): rebuild a `status \\0
    new_mode \\0 content_sha256 \\0 path \\0` record with the `new_mode` field
    DROPPED, simulating what the record would look like if the production
    code regressed to not hash mode at all. Used ONLY to prove a positive
    assertion ("digests differ") is actually driven by the mode field, not by
    some other accidental difference."""
    status, _mode, content, rest = record.split(b"\x00", 3)
    return status + b"\x00" + content + b"\x00" + rest


class PatchDigestModeTypeTest(GitRepoTestCase):
    """T8: mode/type swaps with IDENTICAL bytes must still change the digest.

    Finding 3 (codex): the prior version of these tests compared a
    mode/type-change digest against an EMPTY-diff digest (`self.digest(base,
    base)`), which passes even if `new_mode` were omitted from the record
    entirely — any non-empty diff would differ from an empty one regardless
    of mode. Rewritten below to build TWO SIBLING heads off the identical
    base, with identical status/path/content, differing ONLY in mode/type —
    and to include a negative control that reconstructs the byte streams with
    `new_mode` stripped, proving they'd collapse to IDENTICAL digests if mode
    weren't hashed (i.e. the test genuinely discriminates on mode today)."""

    def test_pure_mode_change_identical_bytes_changes_digest(self):
        base = self.commit("c1 (base, no file yet)")

        _run(self.repo, "checkout", "-qb", "head-644")
        path = self.write("sub/b.py", "same bytes\n")
        path.chmod(0o644)
        head_644 = self.commit("c2 add sub/b.py at 644")

        _run(self.repo, "checkout", "-q", base)
        _run(self.repo, "checkout", "-qb", "head-755")
        path = self.write("sub/b.py", "same bytes\n")
        path.chmod(0o755)
        head_755 = self.commit("c2 add sub/b.py at 755, IDENTICAL bytes")

        entries_644 = fc._iter_raw_diff_entries(fc._git_diff_raw_bytes(self.repo, base, head_644))
        entries_755 = fc._iter_raw_diff_entries(fc._git_diff_raw_bytes(self.repo, base, head_755))
        self.assertEqual(len(entries_644), 1)
        self.assertEqual(len(entries_755), 1)
        # Isolate mode as the ONLY difference: same status, same path.
        self.assertEqual(entries_644[0].status, entries_755[0].status)
        self.assertEqual(entries_644[0].path, entries_755[0].path)
        self.assertEqual(entries_644[0].new_mode, "100644")
        self.assertEqual(entries_755[0].new_mode, "100755")

        records_644 = fc._build_records(self.repo, entries_644, allow_gitlinks=False)
        records_755 = fc._build_records(self.repo, entries_755, allow_gitlinks=False)
        _, rec_644 = records_644[0]
        _, rec_755 = records_755[0]
        status_644, mode_644, content_644, _ = rec_644.split(b"\x00", 3)
        status_755, mode_755, content_755, _ = rec_755.split(b"\x00", 3)
        self.assertEqual(status_644, status_755)
        self.assertEqual(content_644, content_755)  # SAME content bytes -- isolates mode
        self.assertNotEqual(mode_644, mode_755)

        # Positive assertion: today's code discriminates on mode.
        d_644 = self.digest(base, head_644)
        d_755 = self.digest(base, head_755)
        self.assertNotEqual(d_644, d_755)

        # Negative control: strip `new_mode` from both records -> the two
        # streams become byte-IDENTICAL (since status/content/path already
        # match), so a code path that dropped mode from the record would make
        # this test pass EVEN THOUGH mode is no longer hashed. This proves
        # today's `assertNotEqual` above is genuinely driven by mode.
        header = (
            fc.CANONICAL_BYTES_HEADER_PREFIX + self.REPO_SLUG.encode("utf-8") + b"\x00" + base.encode("ascii") + b"\x00"
        )
        stream_644_no_mode = header + _record_without_mode(rec_644)
        stream_755_no_mode = header + _record_without_mode(rec_755)
        self.assertEqual(stream_644_no_mode, stream_755_no_mode)
        self.assertEqual(
            hashlib.sha256(stream_644_no_mode).hexdigest(),
            hashlib.sha256(stream_755_no_mode).hexdigest(),
        )

    def test_file_to_symlink_typechange_changes_digest(self):
        base = self.commit("c1 (base, no file yet)")
        target = "a.py"  # the symlink's target string == the regular file's exact byte content

        _run(self.repo, "checkout", "-qb", "head-regular")
        self.write("sub/c.py", target)  # exact bytes b"a.py", no trailing newline
        head_regular = self.commit("c2 add sub/c.py as a regular file")

        _run(self.repo, "checkout", "-q", base)
        _run(self.repo, "checkout", "-qb", "head-symlink")
        (self.repo / "sub").mkdir(parents=True, exist_ok=True)
        (self.repo / "sub/c.py").symlink_to(target)
        _run(self.repo, "add", "-A")
        _run(self.repo, "commit", "-q", "-m", "c2 add sub/c.py as a symlink, IDENTICAL content bytes")
        head_symlink = _rev_parse(self.repo)

        entries_regular = fc._iter_raw_diff_entries(fc._git_diff_raw_bytes(self.repo, base, head_regular))
        entries_symlink = fc._iter_raw_diff_entries(fc._git_diff_raw_bytes(self.repo, base, head_symlink))
        self.assertEqual(len(entries_regular), 1)
        self.assertEqual(len(entries_symlink), 1)
        self.assertEqual(entries_regular[0].status, entries_symlink[0].status)
        self.assertEqual(entries_regular[0].path, entries_symlink[0].path)
        self.assertEqual(entries_regular[0].new_mode, "100644")
        self.assertEqual(entries_symlink[0].new_mode, "120000")

        records_regular = fc._build_records(self.repo, entries_regular, allow_gitlinks=False)
        records_symlink = fc._build_records(self.repo, entries_symlink, allow_gitlinks=False)
        _, rec_regular = records_regular[0]
        _, rec_symlink = records_symlink[0]
        status_r, mode_r, content_r, _ = rec_regular.split(b"\x00", 3)
        status_s, mode_s, content_s, _ = rec_symlink.split(b"\x00", 3)
        self.assertEqual(status_r, status_s)
        self.assertEqual(content_r, content_s)  # a symlink's blob content IS its target bytes
        self.assertNotEqual(mode_r, mode_s)

        d_regular = self.digest(base, head_regular)
        d_symlink = self.digest(base, head_symlink)
        self.assertNotEqual(d_regular, d_symlink)

        # Negative control, same technique as the mode-change test above.
        header = (
            fc.CANONICAL_BYTES_HEADER_PREFIX + self.REPO_SLUG.encode("utf-8") + b"\x00" + base.encode("ascii") + b"\x00"
        )
        stream_r_no_mode = header + _record_without_mode(rec_regular)
        stream_s_no_mode = header + _record_without_mode(rec_symlink)
        self.assertEqual(stream_r_no_mode, stream_s_no_mode)
        self.assertEqual(
            hashlib.sha256(stream_r_no_mode).hexdigest(),
            hashlib.sha256(stream_s_no_mode).hexdigest(),
        )


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

    def test_cat_file_batch_nonzero_rc_invalidates_even_with_a_complete_response(self):
        """Finding 1 (codex + gemini, corroborated): a `cat-file --batch`
        process that EXITS NONZERO must invalidate even when the stream it
        emitted before exiting looks complete and well-formed — rc is a
        SEPARATE, independent check from stream well-formedness (this is the
        same rc==0-only contract `_git_diff_raw_bytes` already honors,
        design §3.2 finding 6, extended to cat-file). Prior to the fix,
        `_cat_file_content_hashes` consumed the stream, called `proc.wait()`,
        and unconditionally returned the computed hashes with no rc check at
        all."""
        oid = b"a" * 40
        content = b"hello\n"
        stream = f"{oid.decode()} blob {len(content)}\n".encode("ascii") + content + b"\n"
        with mock.patch.object(subprocess, "Popen") as mocked_popen:
            proc = mock.MagicMock()
            proc.stdin = mock.MagicMock()
            proc.stdout = io.BytesIO(stream)
            proc.wait.return_value = 9
            proc.returncode = 9
            mocked_popen.return_value = proc
            with self.assertRaises(fc.PatchDigestInvalid):
                fc._cat_file_content_hashes(self.repo, [oid])

    def test_cat_file_batch_kill_after_timeout_invalidates(self):
        """Finding 1: the kill-after-timeout path (`proc.wait()` raises
        `TimeoutExpired` -> `proc.kill()` -> `proc.wait()` again) must ALSO
        invalidate. A killed process (rc typically negative, e.g. -9 for
        SIGKILL) is never a valid result, even with a complete-looking
        stream — the fix's rc check runs AFTER this exact kill sequence, so
        it must observe the post-kill returncode, not skip it."""
        oid = b"a" * 40
        content = b"hello\n"
        stream = f"{oid.decode()} blob {len(content)}\n".encode("ascii") + content + b"\n"
        with mock.patch.object(subprocess, "Popen") as mocked_popen:
            proc = mock.MagicMock()
            proc.stdin = mock.MagicMock()
            proc.stdout = io.BytesIO(stream)
            proc.returncode = None

            def _wait(timeout=None):
                if proc.wait.call_count == 1:
                    raise subprocess.TimeoutExpired(cmd="git cat-file --batch", timeout=timeout)
                proc.returncode = -9
                return -9

            proc.wait.side_effect = _wait
            mocked_popen.return_value = proc
            with self.assertRaises(fc.PatchDigestInvalid):
                fc._cat_file_content_hashes(self.repo, [oid])
            self.assertEqual(proc.kill.call_count, 1)
            self.assertEqual(proc.wait.call_count, 2)


class PatchDigestTypeSwapTest(GitRepoTestCase):
    """Finding 2 (codex): a `100644`/`100755`/`120000` (non-gitlink) mode
    entry whose OID actually addresses a NON-blob object (tree/commit/tag)
    is a crafted type-swap and must be rejected fail-closed, never hashed as
    if its payload bytes were real file content."""

    def test_nonblob_object_at_a_blob_mode_is_rejected(self):
        base = self.commit("c1 (empty base)")

        # Build a real TREE object to use as the type-swap target -- an
        # ordinary subdirectory of a normal commit, so this needs NO hostile
        # git binary: pure repo-content crafting, squarely inside the stated
        # threat model (attacker controls repo contents).
        self.write("inner/f.py", "inner content\n")
        self.commit("c-inner (creates a real tree object)")
        inner_tree = _run(self.repo, "rev-parse", "HEAD:inner").stdout.strip()
        self.assertEqual(_run(self.repo, "cat-file", "-t", inner_tree).stdout.strip(), "tree")

        # Reset to base and hand-craft a 100644 (blob-mode) entry pointing at
        # the TREE oid above, via `update-index --cacheinfo` -- this bypasses
        # `git mktree`'s type validation (confirmed empirically: `git mktree`
        # itself refuses a mode/type mismatch with "is a tree but specified
        # type was (blob)"), exactly like the existing gitlink-crafting
        # fixture (`_add_gitlink` in `PatchDigestGitlinkTest`) does for mode
        # 160000. Confirmed empirically to round-trip through `git diff --raw`
        # reporting mode 100644 for an OID `cat-file` reports as `type=tree`.
        _run(self.repo, "checkout", "-q", base)
        _run(self.repo, "update-index", "--add", "--cacheinfo", f"100644,{inner_tree},evil.py")
        head = self.commit_no_add("c2 type-swap: 100644 mode pointing at a tree oid")

        raw = fc._git_diff_raw_bytes(self.repo, base, head)
        entries = fc._iter_raw_diff_entries(raw)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].new_mode, "100644")
        self.assertEqual(entries[0].new_oid, inner_tree.encode("ascii"))

        with self.assertRaises(fc.PatchDigestInvalid):
            self.digest(base, head)

    def test_mocked_nonblob_type_is_rejected_at_the_build_records_level(self):
        """Same defect, exercised via a mocked cat-file response reporting
        `type=tree` for an entry at a non-gitlink mode — isolates the check
        inside `_build_records` from git-plumbing craftability concerns."""
        entry = fc._RawDiffEntry(status="A", new_mode="100644", new_oid=b"a" * 40, path=b"evil.py")
        with mock.patch.object(fc, "_cat_file_content_hashes") as mocked:
            mocked.return_value = {b"a" * 40: ("d" * 64, "tree")}
            with self.assertRaises(fc.PatchDigestInvalid):
                fc._build_records(self.repo, [entry], allow_gitlinks=False)

    def test_blob_type_is_accepted(self):
        """Negative control: a genuine `blob`-typed object at a blob-family
        mode is accepted (proves the check discriminates on type, not on
        merely being present)."""
        entry = fc._RawDiffEntry(status="A", new_mode="100644", new_oid=b"a" * 40, path=b"ok.py")
        with mock.patch.object(fc, "_cat_file_content_hashes") as mocked:
            mocked.return_value = {b"a" * 40: ("d" * 64, "blob")}
            records = fc._build_records(self.repo, [entry], allow_gitlinks=False)
        self.assertEqual(len(records), 1)


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
                epoch=2,
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
