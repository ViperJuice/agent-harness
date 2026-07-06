"""Panel/board leg execution is PARALLEL BY DEFAULT; sequential is opt-in (ABDPAR).

These prove the two behaviors structurally, not by timing luck:

* default (``max_concurrency=None``) → legs run CONCURRENTLY. A ``threading.Barrier``
  of N parties can only release if all N legs are in-flight at once, so a sequential
  runner would dead-time-out and the legs would come back DEGRADED — asserting all
  legs OK proves real overlap, with no sleeps.
* ``max_concurrency=1`` → SEQUENTIAL. A lock-guarded active-counter never exceeds 1,
  even though each leg holds a small window open — the pool structurally serializes.
"""
import threading
import time
import unittest

from phase_loop_runtime.panel_invoker import invoke_panel


class PanelConcurrencyTests(unittest.TestCase):
    def test_default_runs_legs_in_parallel(self) -> None:
        legs = ["codex", "gemini", "claude"]
        barrier = threading.Barrier(len(legs), timeout=10)

        def spawn(leg: str, artifact: str) -> tuple[str, str]:
            # Only releases if ALL legs reach it simultaneously → proves parallelism.
            # A sequential runner would block here and raise BrokenBarrierError.
            barrier.wait()
            return "OK", f"{leg}: reviewed"

        result = invoke_panel("artifact", legs, spawn=spawn)  # default: parallel
        self.assertEqual([leg.status for leg in result.legs], ["OK", "OK", "OK"])
        # order preserved: result[i] corresponds to legs[i]
        self.assertEqual([leg.leg for leg in result.legs], legs)

    def test_max_concurrency_1_forces_sequential(self) -> None:
        legs = ["codex", "gemini", "claude"]
        lock = threading.Lock()
        active = 0
        max_active = 0

        def spawn(leg: str, artifact: str) -> tuple[str, str]:
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)  # hold the window open so any overlap WOULD be observed
            with lock:
                active -= 1
            return "OK", f"{leg}: reviewed"

        result = invoke_panel("artifact", legs, spawn=spawn, max_concurrency=1)
        self.assertEqual(max_active, 1, "max_concurrency=1 must never overlap legs")
        self.assertEqual([leg.status for leg in result.legs], ["OK", "OK", "OK"])
        self.assertEqual([leg.leg for leg in result.legs], legs)

    def test_max_concurrency_caps_parallelism(self) -> None:
        # cap=2 over 3 legs → at most 2 in flight at once.
        legs = ["codex", "gemini", "claude"]
        lock = threading.Lock()
        active = 0
        max_active = 0

        def spawn(leg: str, artifact: str) -> tuple[str, str]:
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return "OK", f"{leg}: reviewed"

        invoke_panel("artifact", legs, spawn=spawn, max_concurrency=2)
        self.assertLessEqual(max_active, 2, "max_concurrency=2 must cap in-flight legs at 2")


if __name__ == "__main__":
    unittest.main()
