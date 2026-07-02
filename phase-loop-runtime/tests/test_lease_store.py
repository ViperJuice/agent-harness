"""Behavior tests for CS-0.10c's local-file `LeaseStore`
(`phase_loop_runtime.lease_store`) and its `consiliency-lease` CLI wiring.

The vector-driven projection conformance lives in
`test_lease_store_conformance.py`; this file exercises the ROADMAP
CS-0.10c acceptance criteria against the real store: two local actors on
overlapping path-sets reroute (never block), a stale lease auto-expires,
release frees it, the on-disk event log validates against the vendored
schema, and the CLI dispatches all four operations.
"""
from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from phase_loop_runtime.cli import main as cli_main
from phase_loop_runtime.lease_store import (
    LeaseStore,
    events_path,
    lease_event_validator,
    lease_store_protocol_schema,
    protocol_descriptor,
)
from jsonschema import Draft202012Validator


class LeaseStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.store = LeaseStore(self.repo)

    def tearDown(self):
        self.tmp.cleanup()

    def test_two_actors_overlapping_path_sets_second_reroutes_not_blocked(self):
        first = self.store.acquire(
            lease_id="lease-src-mod",
            holder="agent-a",
            ttl_seconds=300,
            mode="soft",
            scope={"granularity": "path-set", "selector": ["src/mod/**"]},
            phase="implement",
            now="2026-01-01T00:00:00Z",
        )
        self.assertTrue(first.granted)

        second = self.store.acquire(
            lease_id="lease-src-mod-b",
            holder="agent-b",
            ttl_seconds=300,
            mode="soft",
            scope={"granularity": "path-set", "selector": ["src/mod/sub/**"]},
            phase="implement",
            now="2026-01-01T00:00:05Z",
        )
        # Never blocks or raises -- a structured conflict result carrying the
        # blocking lease and a reroute instruction.
        self.assertFalse(second.granted)
        self.assertEqual(second.give_way, "reroute")
        self.assertIsNotNone(second.conflict)
        self.assertEqual(second.conflict["holder"], "agent-a")
        self.assertEqual(second.reason, "conflict")

    def test_disjoint_path_sets_both_granted(self):
        first = self.store.acquire(
            lease_id="lease-a",
            holder="agent-a",
            ttl_seconds=300,
            mode="soft",
            scope={"granularity": "path-set", "selector": ["src/mod-a/**"]},
            phase="implement",
            now="2026-01-01T00:00:00Z",
        )
        second = self.store.acquire(
            lease_id="lease-b",
            holder="agent-b",
            ttl_seconds=300,
            mode="soft",
            scope={"granularity": "path-set", "selector": ["src/mod-b/**"]},
            phase="implement",
            now="2026-01-01T00:00:00Z",
        )
        self.assertTrue(first.granted)
        self.assertTrue(second.granted)

    def test_stale_lease_auto_expires_query_returns_none(self):
        self.store.acquire(
            lease_id="lease-src-mod",
            holder="agent-a",
            ttl_seconds=300,
            mode="soft",
            scope={"granularity": "path-set", "selector": ["src/mod/**"]},
            phase="implement",
            now="2026-01-01T00:00:00Z",
        )
        # No renew -- past heartbeat_at + ttl_seconds a dead holder never
        # freezes the path.
        current = self.store.query(lease_id="lease-src-mod", now="2026-01-01T00:05:01Z")
        self.assertIsNone(current)

        # A second actor can now acquire the same ground; it is NOT told to
        # reroute.
        result = self.store.acquire(
            lease_id="lease-src-mod-2",
            holder="agent-b",
            ttl_seconds=300,
            mode="soft",
            scope={"granularity": "path-set", "selector": ["src/mod/**"]},
            phase="implement",
            now="2026-01-01T00:05:01Z",
        )
        self.assertTrue(result.granted)

    def test_renew_extends_past_original_ttl(self):
        self.store.acquire(
            lease_id="lease-src-mod",
            holder="agent-a",
            ttl_seconds=300,
            mode="soft",
            scope={"granularity": "path-set", "selector": ["src/mod/**"]},
            phase="implement",
            now="2026-01-01T00:00:00Z",
        )
        renewed = self.store.renew(lease_id="lease-src-mod", holder="agent-a", now="2026-01-01T00:04:10Z")
        self.assertTrue(renewed.renewed)
        self.assertEqual(renewed.lease["heartbeat_at"], "2026-01-01T00:04:10Z")

        # Still active past the ORIGINAL ttl window (00:05:00).
        current = self.store.query(lease_id="lease-src-mod", now="2026-01-01T00:06:40Z")
        self.assertIsNotNone(current)
        self.assertEqual(current["holder"], "agent-a")

    def test_release_frees_before_ttl(self):
        self.store.acquire(
            lease_id="lease-src-mod",
            holder="agent-a",
            ttl_seconds=300,
            mode="soft",
            scope={"granularity": "path-set", "selector": ["src/mod/**"]},
            phase="implement",
            now="2026-01-01T00:00:00Z",
        )
        released = self.store.release(lease_id="lease-src-mod", holder="agent-a", now="2026-01-01T00:02:00Z")
        self.assertTrue(released.released)
        self.assertIsNone(self.store.query(lease_id="lease-src-mod", now="2026-01-01T00:02:10Z"))

    def test_release_is_idempotent_and_holder_scoped(self):
        # Releasing something never held is a no-op success (idempotent).
        first = self.store.release(lease_id="never-acquired", holder="agent-a", now="2026-01-01T00:00:00Z")
        self.assertTrue(first.released)

        self.store.acquire(
            lease_id="lease-x",
            holder="agent-a",
            ttl_seconds=300,
            mode="soft",
            scope={"granularity": "path-set", "selector": ["src/x/**"]},
            phase="implement",
            now="2026-01-01T00:00:00Z",
        )
        # Someone else can't release agent-a's lease.
        rejected = self.store.release(lease_id="lease-x", holder="agent-b", now="2026-01-01T00:00:10Z")
        self.assertFalse(rejected.released)
        self.assertEqual(rejected.reason, "not-holder")

    def test_hard_degrades_to_soft_on_this_backend(self):
        result = self.store.acquire(
            lease_id="lease-symbol-parse",
            holder="agent-a",
            ttl_seconds=300,
            mode="hard",
            scope={"granularity": "symbol", "selector": ["src/parser.rs::parse"]},
            phase="implement",
            now="2026-01-01T00:00:00Z",
        )
        self.assertTrue(result.granted)
        self.assertTrue(result.degraded)
        self.assertEqual(result.lease["mode"], "soft")

    def test_current_lease_view_is_events_only(self):
        """The store's query() signature has no parameter a coordination
        message could occupy -- the guardrail is structural. This asserts
        the concrete instance carries no such surface."""
        self.store.acquire(
            lease_id="lease-src-mod",
            holder="agent-a",
            ttl_seconds=300,
            mode="soft",
            scope={"granularity": "path-set", "selector": ["src/mod/**"]},
            phase="implement",
            now="2026-01-01T00:00:00Z",
        )
        import inspect

        query_params = set(inspect.signature(self.store.query).parameters)
        self.assertEqual(query_params & {"message", "messages", "inbox", "channel"}, set())

    def test_event_log_validates_against_vendored_schema(self):
        self.store.acquire(
            lease_id="lease-src-mod",
            holder="agent-a",
            ttl_seconds=300,
            mode="soft",
            scope={"granularity": "path-set", "selector": ["src/mod/**"]},
            phase="implement",
            now="2026-01-01T00:00:00Z",
        )
        self.store.renew(lease_id="lease-src-mod", holder="agent-a", now="2026-01-01T00:04:10Z")
        self.store.release(lease_id="lease-src-mod", holder="agent-a", now="2026-01-01T00:04:20Z")

        log_path = events_path(self.repo)
        self.assertTrue(log_path.exists())
        validator = lease_event_validator()
        lines = [line for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(lines), 3)
        for line in lines:
            validator.validate(json.loads(line))

    def test_protocol_descriptor_validates_against_the_contract(self):
        Draft202012Validator(lease_store_protocol_schema()).validate(protocol_descriptor())


class LeaseCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.repo = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cli_main(["consiliency-lease", "--repo", str(self.repo), *argv])
        return rc, out.getvalue()

    def test_acquire_query_release_round_trip_via_cli(self):
        rc, out = self._run(
            "acquire",
            "--lease-id", "lease-a",
            "--holder", "agent-a",
            "--scope", "src/mod/**",
            "--lease-phase", "implement",
            "--now", "2026-01-01T00:00:00Z",
            "--json",
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertTrue(payload["granted"])

        rc, out = self._run("query", "--path", "src/mod/x.py", "--now", "2026-01-01T00:00:10Z", "--json")
        self.assertEqual(rc, 0)
        current = json.loads(out)
        self.assertEqual(current["holder"], "agent-a")

        rc, out = self._run(
            "acquire",
            "--lease-id", "lease-b",
            "--holder", "agent-b",
            "--scope", "src/mod/**",
            "--lease-phase", "implement",
            "--now", "2026-01-01T00:00:20Z",
        )
        self.assertEqual(rc, 1)
        self.assertIn("reroute", out)

        rc, _ = self._run("release", "--lease-id", "lease-a", "--holder", "agent-a", "--now", "2026-01-01T00:00:30Z")
        self.assertEqual(rc, 0)

        rc, out = self._run("query", "--path", "src/mod/x.py", "--now", "2026-01-01T00:00:40Z", "--json")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "null")


if __name__ == "__main__":
    unittest.main()
