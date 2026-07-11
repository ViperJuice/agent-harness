"""#146 — a wrapped release-dispatch executor must not treat its OWN active lock
as a competitor. The exclusion is caller-identity based and injection-free: it
fires on ancestry (holder is an ancestor of the caller) or an explicit matching
``caller_run_id``, and fails closed for a genuine second dispatch."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from phase_loop_runtime.dispatch_lock import (
    DispatchLock,
    DispatchLockContention,
    _pid_is_ancestor,
    holder_is_self,
)
from phase_loop_test_utils import make_repo

# A pid that is neither this process nor any ancestor of it, and not alive.
_FOREIGN_PID = 2_000_000_000


class HolderIsSelfTest(unittest.TestCase):
    def test_holder_is_self_by_ancestry(self):
        # This process's own parent is an ancestor → recognised as self.
        parent = os.getppid()
        if parent > 1:
            self.assertTrue(holder_is_self(parent, None))

    def test_same_pid_is_not_self_without_ancestry(self):
        # Bare pid-equality is NOT a re-entrancy signal (a same-shell sibling
        # dispatch must still block); only ancestry / run_id qualify.
        self.assertFalse(holder_is_self(os.getpid(), None))

    def test_holder_is_self_by_run_id_match(self):
        # A setsid'd child shares no ancestry pid the walk can see, but a matching
        # injected run_id still identifies the caller's own run.
        self.assertTrue(
            holder_is_self(_FOREIGN_PID, "run-abc", caller_run_id="run-abc")
        )

    def test_foreign_holder_is_a_competitor(self):
        self.assertFalse(holder_is_self(_FOREIGN_PID, None))
        # Different run_ids never match.
        self.assertFalse(
            holder_is_self(_FOREIGN_PID, "run-abc", caller_run_id="run-xyz")
        )

    def test_missing_holder_pid_fails_closed(self):
        self.assertFalse(holder_is_self(None, None))
        self.assertFalse(holder_is_self(0, None))

    def test_ancestor_detection_current_process(self):
        # This process's own parent is, by definition, an ancestor of this pid.
        parent = os.getppid()
        if parent > 1:
            self.assertTrue(_pid_is_ancestor(parent, os.getpid()))
        self.assertFalse(_pid_is_ancestor(_FOREIGN_PID, os.getpid()))
        self.assertFalse(_pid_is_ancestor(os.getpid(), os.getpid()))


class DispatchLockReentrancyTest(unittest.TestCase):
    def test_child_process_reenters_parents_lock(self):
        # The real #146 scenario: the outer run holds the lock, a nested (child)
        # release-dispatch executor tries to acquire it and must re-enter, not block.
        if not hasattr(os, "fork"):
            self.skipTest("fork required for the ancestry re-entrancy path")
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            with DispatchLock(repo, roadmap):
                read_fd, write_fd = os.pipe()
                pid = os.fork()
                if pid == 0:  # child — the nested executor
                    os.close(read_fd)
                    try:
                        lock = DispatchLock(repo, roadmap).acquire()
                        msg = b"reentrant" if lock.reentrant else b"acquired-fresh"
                    except DispatchLockContention:
                        msg = b"contention"
                    except Exception as exc:  # pragma: no cover - defensive
                        msg = b"error:" + repr(exc).encode()[:120]
                    os.write(write_fd, msg)
                    os.close(write_fd)
                    os._exit(0)
                os.close(write_fd)
                out = os.read(read_fd, 256)
                os.close(read_fd)
                os.waitpid(pid, 0)
                self.assertEqual(out, b"reentrant")

    def test_reentrant_by_injected_run_id(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            with DispatchLock(repo, roadmap, caller_run_id="run-42"):
                inner = DispatchLock(repo, roadmap, caller_run_id="run-42").acquire()
                self.assertTrue(inner.reentrant)
                inner.release()

    def test_genuine_competitor_still_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            held = DispatchLock(repo, roadmap).acquire()
            try:
                # Overwrite the holder record with a foreign identity while the
                # flock is still held: the contender cannot recognise it as self.
                held.path.write_text(
                    json.dumps(
                        {
                            "pid": _FOREIGN_PID,
                            "started_at": 0.0,
                            "repo": str(repo),
                            "roadmap": str(roadmap),
                            "run_id": "someone-else",
                        },
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                with self.assertRaises(DispatchLockContention) as raised:
                    DispatchLock(repo, roadmap, caller_run_id="mine").acquire()
                self.assertEqual(raised.exception.holder_pid, _FOREIGN_PID)
            finally:
                held.release()

    def test_run_id_persisted_in_holder_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            roadmap = repo / "specs" / "phase-plans-v1.md"
            with DispatchLock(repo, roadmap, caller_run_id="run-99") as held:
                payload = json.loads(held.path.read_text(encoding="utf-8"))
            self.assertEqual(payload["run_id"], "run-99")


if __name__ == "__main__":
    unittest.main()
