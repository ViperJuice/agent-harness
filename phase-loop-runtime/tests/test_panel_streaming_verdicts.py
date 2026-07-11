"""REVIEWGOV IF-0-REVIEWGOV-2 — opt-in streaming verdict delivery on the SHARED
``_run_legs_ordered`` helper (which drives both ``invoke_panel`` and
``invoke_board``).

The load-bearing design point: streaming is strictly additive. When neither
``on_leg_complete`` nor ``review_dir`` is set, the consolidated return is the exact
historical submission-ordered result — so ``invoke_panel``'s byte-identical golden
is untouched. When opted in, each leg is delivered THE MOMENT IT LANDS (callback +
an incremental per-leg verdict file), while the consolidated return is still
re-sorted to submission order.

ONE shared out-of-order-completion fixture (``_out_of_order_fixture``) proves both
halves, so neither can pass trivially. The fixture's ``slow`` leg blocks on an
explicit ``release_slow`` event and its ``fast`` leg signals ``fast_done`` — so
each test can drive a DETERMINISTIC completion order ``fast`` → ``slow`` with no
sleeps and no scheduling race:

* The DEFAULT-path (golden) assertion waits for ``fast_done`` (fast has genuinely
  completed) BEFORE releasing slow, then asserts the consolidated result is still
  submission order ``[slow, fast]`` — proving order survives real out-of-order
  completion (it cannot pass trivially).
* The STREAMING assertion releases slow only from INSIDE the fast leg's callback,
  so slow provably cannot land until fast's verdict has already been delivered —
  proving fast fires before slow finishes, with no head-of-line blocking.
"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from phase_loop_runtime import panel_invoker as pi
from phase_loop_runtime.advisor_board.fixtures import DEFAULT_BOARD


class _Leg:
    """A fake leg with a controllable completion barrier. The ``slow`` leg blocks in
    ``run_one`` until ``release`` is set; the ``fast`` leg returns at once and sets
    ``done`` — so a test can force the completion order ``fast`` → ``slow``
    deterministically (no sleeps, no scheduling race)."""

    def __init__(self, name: str, *, release: threading.Event | None = None,
                 done: threading.Event | None = None):
        self.name = name
        self._release = release
        self._done = done

    def __call__(self) -> pi.PanelLegResult:
        if self._release is not None and not self._release.wait(timeout=5.0):
            raise TimeoutError("slow leg was never released — fixture deadlock")
        result = pi.PanelLegResult(
            leg=self.name, status="OK", text=f"{self.name}\nAGREE", seat_key=self.name
        )
        if self._done is not None:
            self._done.set()
        return result


def _out_of_order_fixture():
    """Return ``(items, run_one, release_slow, fast_done)`` in SUBMISSION order
    ``[slow, fast]``. ``slow`` blocks until ``release_slow`` is set; ``fast`` sets
    ``fast_done`` as it completes. A two-worker fan-out runs them concurrently."""
    release_slow = threading.Event()
    fast_done = threading.Event()
    items = [_Leg("slow", release=release_slow), _Leg("fast", done=fast_done)]

    def run_one(item: "_Leg") -> pi.PanelLegResult:
        return item()

    return items, run_one, release_slow, fast_done


@unittest.skipUnless(
    pi._PANEL_MAX_WORKERS >= 2,
    "out-of-order fixture needs >=2 workers (slow + fast must run concurrently)",
)
class SharedOutOfOrderFixtureTests(unittest.TestCase):
    def test_default_path_returns_submission_order_under_out_of_order_completion(self) -> None:
        # GOLDEN half: default path (no streaming params) — the byte-identical
        # historical behavior. We wait for ``fast_done`` (fast has genuinely
        # completed first) BEFORE releasing slow, then assert the consolidated
        # result is still submission order ``[slow, fast]``. So the ordered contract
        # survives real out-of-order completion and cannot pass trivially.
        items, run_one, release_slow, fast_done = _out_of_order_fixture()
        box: dict[str, list[pi.PanelLegResult]] = {}
        runner = threading.Thread(
            target=lambda: box.__setitem__(
                "results", pi._run_legs_ordered(items, run_one, max_concurrency=2)
            )
        )
        runner.start()
        self.assertTrue(fast_done.wait(timeout=5.0), "fast leg never completed")
        release_slow.set()
        runner.join(timeout=5.0)
        self.assertFalse(runner.is_alive(), "run did not finish after releasing slow")
        self.assertEqual([r.leg for r in box["results"]], ["slow", "fast"])

    def test_streaming_delivers_fast_leg_before_slow_finishes(self) -> None:
        # STREAMING half: SAME fixture. slow is released ONLY from inside the fast
        # leg's callback, so slow provably cannot land until fast's verdict has
        # already been delivered (callback fired + file on disk). This IS "fast
        # fires before slow finishes", with no head-of-line blocking.
        items, run_one, release_slow, _fast_done = _out_of_order_fixture()
        landed: list[str] = []
        snap: dict[str, bool] = {}
        lock = threading.Lock()
        with tempfile.TemporaryDirectory() as d:
            review_dir = Path(d)

            def on_leg_complete(result: pi.PanelLegResult) -> None:
                with lock:
                    landed.append(result.leg)
                    if result.leg == "fast":
                        # fast's file is already on disk (the helper writes BEFORE the
                        # callback) and slow's is NOT — slow is still blocked.
                        snap["fast_file_present"] = (review_dir / "leg-0001-fast.verdict.json").exists()
                        snap["slow_file_absent"] = not (review_dir / "leg-0000-slow.verdict.json").exists()
                        release_slow.set()  # only now may slow complete

            results = pi._run_legs_ordered(
                items, run_one, on_leg_complete=on_leg_complete, review_dir=review_dir,
                max_concurrency=2,
            )

            # Callbacks fired in COMPLETION order (fast strictly before slow).
            self.assertEqual(landed, ["fast", "slow"])
            # At fast's landing: its file present, slow's absent (no head-of-line block).
            self.assertTrue(snap.get("fast_file_present"))
            self.assertTrue(snap.get("slow_file_absent"))
            # Consolidated return re-sorted to SUBMISSION order.
            self.assertEqual([r.leg for r in results], ["slow", "fast"])
            # Incremental per-leg verdict files written for BOTH, index-prefixed.
            names = sorted(p.name for p in review_dir.glob("*.verdict.json"))
            self.assertEqual(names, ["leg-0000-slow.verdict.json", "leg-0001-fast.verdict.json"])
            payload = json.loads((review_dir / "leg-0000-slow.verdict.json").read_text())
            self.assertEqual(payload["leg"], "slow")
            self.assertEqual(payload["status"], "OK")
            self.assertTrue(payload["usable"])


class StreamingFailOpenTests(unittest.TestCase):
    """The streaming side-channel is best-effort: a raising callback or an unwritable
    ``review_dir`` must NEVER break the pool or fail a leg — the consolidated ordered
    return is authoritative."""

    def test_raising_callback_never_breaks_the_pool(self) -> None:
        items, run_one, release_slow, _ = _out_of_order_fixture()
        release_slow.set()  # both legs complete freely; order is not under test here

        def boom(_result: pi.PanelLegResult) -> None:
            raise RuntimeError("callback boom")

        results = pi._run_legs_ordered(items, run_one, on_leg_complete=boom, max_concurrency=2)
        self.assertEqual([r.leg for r in results], ["slow", "fast"])  # full ordered results

    def test_unwritable_review_dir_is_fail_open(self) -> None:
        items, run_one, release_slow, _ = _out_of_order_fixture()
        release_slow.set()
        with tempfile.NamedTemporaryFile() as f:
            bad = Path(f.name) / "sub"  # parent is a FILE → mkdir raises → swallowed
            results = pi._run_legs_ordered(items, run_one, review_dir=bad, max_concurrency=2)
        self.assertEqual([r.leg for r in results], ["slow", "fast"])


class InvokeStreamingOptInTests(unittest.TestCase):
    """The public entry points thread the opt-in through to the shared helper; the
    default (no params) is unchanged (proven byte-identical by the advisor-board
    golden)."""

    def test_invoke_board_streams_when_opted_in(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            seen: list[str] = []
            lock = threading.Lock()

            def on_leg_complete(r: pi.PanelLegResult) -> None:
                with lock:
                    seen.append(r.leg)

            res = pi.invoke_board(
                DEFAULT_BOARD, "artifact",
                spawn=lambda leg, art: ("OK", f"{leg}\nAGREE"),
                on_leg_complete=on_leg_complete,
                stream_dir=d,
            )
            self.assertEqual(sorted(seen), sorted(r.leg for r in res.legs))
            self.assertEqual(len(list(Path(d).glob("*.verdict.json"))), len(res.legs))
            # Consolidated return still in canonical seat order.
            self.assertEqual([r.leg for r in res.legs], list(pi.PANEL_LEGS))

    def test_invoke_panel_streams_when_opted_in(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            seen: list[str] = []
            lock = threading.Lock()

            def on_leg_complete(r: pi.PanelLegResult) -> None:
                with lock:
                    seen.append(r.leg)

            res = pi.invoke_panel(
                "artifact", pi.PANEL_LEGS,
                spawn=lambda leg, art: ("OK", f"{leg}\nAGREE"),
                on_leg_complete=on_leg_complete,
                stream_dir=d,
            )
            self.assertEqual(sorted(seen), sorted(pi.PANEL_LEGS))
            self.assertEqual(len(list(Path(d).glob("*.verdict.json"))), len(pi.PANEL_LEGS))
            self.assertEqual([r.leg for r in res.legs], list(pi.PANEL_LEGS))


if __name__ == "__main__":
    unittest.main()
