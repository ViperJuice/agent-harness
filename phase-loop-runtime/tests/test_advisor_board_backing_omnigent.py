"""ABDOMNI (Phase 5) — the ``omnigent`` provider backing.

Proves the exit criteria with a **faithful in-process fake Omnigent v0.4.0
server** (a real ``http.server`` on 127.0.0.1, so the Python client's wire path is
genuinely exercised — no mock of the transport):

* an opt-in opencode/pi seat routes through Omnigent as the primary lane, OK with
  text (``PrimaryRoutingTests``);
* no-silent-key is TESTABLE, not asserted: the fake DERIVES the auth lane from the
  vendor-key material it actually received and reports it, and the assertions run
  on the fake's recorded request — a subscription seat leaks ZERO vendor-key
  material; an api-key opt-in seat transmits EXACTLY the seat vendor's key
  (``AuthLaneOnTheWireTests``);
* two DISTINCT fail-closed skips — gateway unreachable vs harness-not-in-catalog —
  each with its own reason (``GatewayDownTests`` / ``DynamicCatalogGateTests``);
* the native host leg + built-3 are unaffected when an omnigent backing is wired
  (``BuiltThreeAndNativeUnaffectedTests``);
* "we did not fork the transport": every endpoint the Python client issues appears
  in the frozen v0.4.0 ``http-surface.json`` and the freeze target is 0.4.0
  (``ContractConformanceTests``).
"""
from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import phase_loop_runtime.panel_invoker as pi
from phase_loop_runtime.advisor_board import (
    HARNESS_ENDPOINT,
    OMNIGENT_FREEZE_TARGET,
    SESSION_ENDPOINTS,
    VENDOR_KEY_HEADER_PREFIX,
    BACKING_HOMEBREW,
    BACKING_OMNIGENT,
    Board,
    HostContext,
    OmnigentBacking,
    OmnigentGatewayUnavailable,
    OmnigentHttpClient,
    Seat,
    classify_http_failure,
)

_CONTRACT_DIR = Path(__file__).resolve().parent / "fixtures" / "omnigent_contract"


# --- faithful in-process fake Omnigent v0.4.0 server -------------------------


class _FakeOmnigentHandler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # silence test noise
        pass

    # -- helpers --
    @property
    def _srv(self) -> "FakeOmnigentServer":
        return self.server._fake  # type: ignore[attr-defined]

    def _received_vendor_key_vars(self) -> list[str]:
        # HTTP header NAMES are case-insensitive and BaseHTTPRequestHandler
        # title-cases them; vendor API-key env var names are uppercase by
        # construction, so .upper() faithfully recovers the transmitted var name.
        vars_: list[str] = []
        for name, _value in self.headers.items():
            if name.lower().startswith(VENDOR_KEY_HEADER_PREFIX):
                vars_.append(name[len(VENDOR_KEY_HEADER_PREFIX):].upper())
        return vars_

    def _body(self) -> dict:
        length = int(self.headers.get("content-length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _json(self, status: int, payload) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    # -- routing --
    def do_GET(self) -> None:  # noqa: N802
        srv = self._srv
        path = self.path.split("?")[0]
        srv.request_log.append({"method": "GET", "path": path, "vendor_key_vars": []})
        if path == "/v1/harnesses":
            self._json(200, srv.catalog)
            return
        if path.endswith("/items"):
            sid = path.split("/")[3]
            self._json(200, srv.sessions.get(sid, {}).get("items", []))
            return
        parts = path.split("/")
        if len(parts) == 4 and parts[1:3] == ["v1", "sessions"]:
            sid = parts[3]
            snap = srv.sessions.get(sid)
            self._json(200 if snap else 404, snap or {"error": "not found"})
            return
        self._json(404, {"error": "route not found"})

    def do_POST(self) -> None:  # noqa: N802
        srv = self._srv
        path = self.path.split("?")[0]
        body = self._body()
        vendor_key_vars = self._received_vendor_key_vars()
        srv.request_log.append({"method": "POST", "path": path, "body": body,
                                "vendor_key_vars": vendor_key_vars})
        if path == "/v1/sessions":
            # DERIVE the auth lane from what actually arrived on the wire — never a
            # client-asserted claim: any vendor-key header -> api_key, else subscription.
            lane = "api_key" if vendor_key_vars else "subscription"
            sid = f"session-{body.get('idempotencyKey', 'x')}"
            snapshot = {
                "id": sid,
                "title": body.get("title", ""),
                "status": "idle",
                "backend": "omnigent-http",
                "createdAt": "2026-07-05T00:00:00Z",
                "updatedAt": "2026-07-05T00:00:00Z",
                "items": [{"id": f"{sid}-created",
                           "event": {"type": "session.created", "sessionId": sid}}],
                "metadata": {"auth_lane": lane, "targetHarness": body.get("targetHarness"),
                             "received_vendor_key_vars": vendor_key_vars},
            }
            srv.sessions[sid] = snapshot
            # Persistent audit copy — survives run_seat's best-effort DELETE so the
            # gateway's reported lane stays assertable after the leg completes.
            srv.created_snapshots.append(snapshot)
            self._json(200, snapshot)
            return
        if path.endswith("/events"):
            sid = path.split("/")[3]
            if srv.reject_next_turn_with:
                rej = srv.reject_next_turn_with
                srv.reject_next_turn_with = None
                if rej == "rate_limit":
                    self._json(429, {"error": "usage cap reached"})
                elif rej == "billing":
                    self._json(403, {"error": "billing issue"})
                elif rej == "policy":
                    self._json(403, {"error": "policy blocked"})
                else:
                    self._json(403, {"error": "auth required"})
                return
            msg = str(body.get("data", {}).get("message", ""))
            turn = f"turn-{len(srv.sessions[sid]['items'])}"
            srv.sessions[sid]["items"].extend(_normal_terminal_items(sid, turn, msg))
            self._json(200, {"queued": True, "sessionId": sid, "turnId": turn})
            return
        self._json(404, {"error": "route not found"})

    def do_DELETE(self) -> None:  # noqa: N802
        srv = self._srv
        path = self.path.split("?")[0]
        srv.request_log.append({"method": "DELETE", "path": path, "vendor_key_vars": []})
        sid = path.split("/")[3]
        srv.sessions.pop(sid, None)
        self.send_response(204)
        self.end_headers()


def _normal_terminal_items(session_id: str, turn_id: str, message: str) -> list[dict]:
    """The v0.4.0 ``normal-terminal`` event flow as history items (text delta +
    completed outputText + terminal markers)."""
    echo = f"Echo: {message}"
    events = [
        {"type": "response.created", "turnId": turn_id},
        {"type": "response.output_text.delta", "delta": echo, "turnId": turn_id},
        {"type": "response.completed", "outputText": echo, "turnId": turn_id, "terminal": True},
        {"type": "turn.completed", "turnId": turn_id, "terminal": True},
    ]
    return [{"id": f"{turn_id}-{i}", "event": {"sessionId": session_id, **e}}
            for i, e in enumerate(events)]


class FakeOmnigentServer:
    """Faithful v0.4.0 fake: real HTTP, a request log, wire-derived auth lanes, a
    toggleable harness catalog, and injectable turn rejections."""

    def __init__(self, *, harnesses: set[str] | None = None, include_cursor: bool = False,
                 reject_next_turn_with: str | None = None) -> None:
        self.sessions: dict[str, dict] = {}
        self.created_snapshots: list[dict] = []
        self.request_log: list[dict] = []
        self.reject_next_turn_with = reject_next_turn_with
        names = {"opencode", "pi"} if harnesses is None else set(harnesses)
        if include_cursor:
            names.add("cursor")
        # v0.4.0 catalog shape: {category: [{name, ...}, ...]}.
        self.catalog = {
            "local": [{"name": n, "capability": "native-terminal"} for n in sorted(names)],
            "sdk": [{"name": "openai-agents", "capability": "sdk"}],
        }
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOmnigentHandler)
        self._httpd._fake = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def __enter__(self) -> "FakeOmnigentServer":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2)

    @property
    def base_url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def backing(self) -> OmnigentBacking:
        return OmnigentBacking.from_config(base_url=self.base_url)

    def session_requests(self) -> list[dict]:
        return [r for r in self.request_log if r["path"] == "/v1/sessions" and r["method"] == "POST"]


def _opencode_seat(auth: str = "subscription") -> Seat:
    return Seat(model="gpt-5.5", effort="high", harness="opencode",
                backing=BACKING_OMNIGENT, auth=auth)


def _board(*seats: Seat, allow_api_key_fallback: bool = False) -> Board:
    return Board(name="brd", purpose="x", seats=seats,
                 allow_api_key_fallback=allow_api_key_fallback)


# --- contract conformance (lock: did NOT fork the transport) -----------------


class ContractConformanceTests(unittest.TestCase):
    def _frozen_endpoint_set(self) -> set[tuple[str, str]]:
        surface = json.loads((_CONTRACT_DIR / "http-surface.json").read_text())
        pairs = set()
        for entry in surface["session_endpoints"] + surface.get("harness_endpoints", []):
            pairs.add((entry["method"], entry["path"]))
        return pairs

    def test_every_client_endpoint_is_in_the_frozen_v0_4_0_surface(self) -> None:
        frozen = self._frozen_endpoint_set()
        client_endpoints = {HARNESS_ENDPOINT, *SESSION_ENDPOINTS}
        missing = client_endpoints - frozen
        self.assertEqual(missing, set(),
                         f"client issues endpoints absent from frozen v0.4.0 surface: {missing}")

    def test_freeze_target_is_0_4_0(self) -> None:
        meta = json.loads((_CONTRACT_DIR / "source-metadata.json").read_text())
        self.assertEqual(OMNIGENT_FREEZE_TARGET, "0.4.0")
        self.assertEqual(meta["freeze_target"]["package_version"], OMNIGENT_FREEZE_TARGET)


# --- primary routing (opencode/pi) -------------------------------------------


class PrimaryRoutingTests(unittest.TestCase):
    def test_opencode_seat_routes_through_omnigent_ok_with_text(self) -> None:
        with FakeOmnigentServer() as srv:
            res = pi.invoke_board(_board(_opencode_seat()), "please review",
                                  omnigent=srv.backing(), base_env={})
        leg = res.legs[0]
        self.assertEqual(leg.leg, "opencode")
        self.assertEqual(leg.status, "OK")
        self.assertIn("Echo: please review", leg.text)
        self.assertIn(f"omnigent v{OMNIGENT_FREEZE_TARGET}", leg.detail or "")
        # A real session was created against the gateway (primary lane, not skipped).
        self.assertEqual(len(srv.session_requests()), 1)

    def test_pi_seat_routes_through_the_transport(self) -> None:
        # The pi lane has no registered model in the frozen ABDREG registry (a
        # named contract-extension, like cursor/amp), so a bare pi board seat cannot
        # pass config-time validation yet; the TRANSPORT is nonetheless harness-
        # agnostic — run_seat routes targetHarness="pi" and returns OK.
        with FakeOmnigentServer() as srv:
            seat = Seat(model="gpt-5.5", effort="high", harness="pi", backing=BACKING_OMNIGENT)
            outcome = srv.backing().run_seat(seat, "review pi", base_env={})
        self.assertEqual(outcome.status, "OK")
        self.assertIn("Echo: review pi", outcome.text)
        self.assertEqual(srv.session_requests()[0]["body"]["targetHarness"], "pi")


# --- auth lane derived on the wire (no-silent-key is testable) ----------------


class AuthLaneOnTheWireTests(unittest.TestCase):
    def test_subscription_seat_leaks_no_vendor_key_material(self) -> None:
        base_env = {"OPENAI_API_KEY": "sk-open", "ANTHROPIC_API_KEY": "sk-anthropic"}
        with FakeOmnigentServer() as srv:
            res = pi.invoke_board(_board(_opencode_seat("subscription")), "review",
                                  omnigent=srv.backing(), base_env=base_env)
            create = srv.session_requests()[0]
            snapshot = srv.created_snapshots[0]
        # On the wire: ZERO vendor-key headers. Reported lane derived from that.
        self.assertEqual(create["vendor_key_vars"], [])
        self.assertEqual(snapshot["metadata"]["auth_lane"], "subscription")
        self.assertEqual(res.legs[0].status, "OK")
        self.assertIn("lane=subscription", res.legs[0].detail or "")

    def test_api_key_optin_transmits_only_the_seat_vendor_key(self) -> None:
        # A stray other-vendor key MUST NOT ride along.
        base_env = {"OPENAI_API_KEY": "sk-open", "ANTHROPIC_API_KEY": "sk-anthropic"}
        board = _board(_opencode_seat("api_key"), allow_api_key_fallback=True)
        with FakeOmnigentServer() as srv:
            res = pi.invoke_board(board, "review", omnigent=srv.backing(), base_env=base_env)
            create = srv.session_requests()[0]
            snapshot = srv.created_snapshots[0]
        # Exactly the openai key var, nothing else — and the gateway DERIVED api_key.
        self.assertEqual(create["vendor_key_vars"], ["OPENAI_API_KEY"])
        self.assertEqual(snapshot["metadata"]["auth_lane"], "api_key")
        self.assertEqual(snapshot["metadata"]["received_vendor_key_vars"], ["OPENAI_API_KEY"])
        self.assertEqual(res.legs[0].status, "OK")
        self.assertIn("lane=api_key", res.legs[0].detail or "")

    def test_reported_lane_matches_what_arrived(self) -> None:
        # Cross-check: the lane the gateway reports equals the wire-derived truth.
        base_env = {"OPENAI_API_KEY": "sk-open"}
        board = _board(_opencode_seat("api_key"), allow_api_key_fallback=True)
        with FakeOmnigentServer() as srv:
            srv.backing().run_seat(board.seats[0], "review",
                                   base_env=base_env, allow_api_key_fallback=True)
            create = srv.session_requests()[0]
            snap = srv.created_snapshots[0]
        wire_lane = "api_key" if create["vendor_key_vars"] else "subscription"
        self.assertEqual(snap["metadata"]["auth_lane"], wire_lane)

    def test_api_key_seat_without_board_optin_is_never_silent(self) -> None:
        # run_seat enforces never-silent-key: an api-key seat without the opt-in
        # raises BEFORE any request — no session is ever created.
        base_env = {"OPENAI_API_KEY": "sk-open"}
        seat = _opencode_seat("api_key")
        with FakeOmnigentServer() as srv:
            with self.assertRaises(ValueError):
                srv.backing().run_seat(seat, "review", base_env=base_env,
                                       allow_api_key_fallback=False)
            self.assertEqual(srv.session_requests(), [])


# --- two distinct fail-closed skips ------------------------------------------


class GatewayDownTests(unittest.TestCase):
    def test_gateway_unreachable_skips_with_warning(self) -> None:
        # Point the backing at a closed port: the None gateway_available probe
        # fails -> select_backing degrades the seat skip-with-warning.
        backing = OmnigentBacking.from_config(base_url="http://127.0.0.1:1")
        res = pi.invoke_board(_board(_opencode_seat()), "review",
                              omnigent=backing, base_env={})
        self.assertEqual(res.legs[0].status, "UNAVAILABLE")
        self.assertIn("gateway unavailable", res.legs[0].detail or "")

    def test_gateway_probe_returns_false_when_down(self) -> None:
        self.assertFalse(OmnigentBacking.from_config(base_url="http://127.0.0.1:1").gateway_available())

    def test_gateway_down_degrades_omnigent_but_built3_unaffected(self) -> None:
        # The paired half of the exit criterion: gateway-down skips the omnigent
        # seat AND leaves the built-3 (codex homebrew) untouched.
        board = _board(
            Seat(model="gpt-5.5", effort="max", harness="codex", backing=BACKING_HOMEBREW),
            _opencode_seat(),
        )
        backing = OmnigentBacking.from_config(base_url="http://127.0.0.1:1")  # nothing listening
        res = pi.invoke_board(board, "review", omnigent=backing, base_env={},
                              spawn=lambda leg, art: ("OK", f"{leg}\nAGREE"))
        by_leg = {l.leg: l for l in res.legs}
        self.assertEqual(by_leg["codex"].status, "OK")
        self.assertEqual(by_leg["codex"].text, "codex\nAGREE")
        self.assertEqual(by_leg["opencode"].status, "UNAVAILABLE")
        self.assertIn("gateway unavailable", by_leg["opencode"].detail or "")


class DynamicCatalogGateTests(unittest.TestCase):
    """The dynamic per-seat catalog gate: an omnigent seat routes iff the LIVE
    catalog reports its harness — no code gate. Proven per-seat with opencode (a
    validation-passing lane) toggled in/out of the catalog, and at the catalog +
    transport level for cursor (the spec's named harness; it has no registered
    model yet — a contract-extension like amp — so it cannot form a bare board seat,
    but the gate mechanism and transport are harness-agnostic)."""

    def test_seat_absent_from_catalog_skips_with_distinct_reason(self) -> None:
        # opencode dropped from the live catalog → skip-with-warning, a DISTINCT
        # reason from gateway-down (both are fail-closed but they are not the same).
        with FakeOmnigentServer(harnesses=set()) as srv:
            res = pi.invoke_board(_board(_opencode_seat()), "review",
                                  omnigent=srv.backing(), base_env={})
        self.assertEqual(res.legs[0].status, "UNAVAILABLE")
        self.assertIn("not in live Omnigent catalog", res.legs[0].detail or "")
        self.assertNotIn("gateway unavailable", res.legs[0].detail or "")

    def test_seat_present_in_catalog_routes(self) -> None:
        with FakeOmnigentServer(harnesses={"opencode"}) as srv:
            res = pi.invoke_board(_board(_opencode_seat()), "review",
                                  omnigent=srv.backing(), base_env={})
        self.assertEqual(res.legs[0].status, "OK")

    def test_cursor_is_catalog_gated(self) -> None:
        # cursor routes only when the live catalog exposes it (no code gate).
        cursor_seat = Seat(model="gpt-5.5", effort="high", harness="cursor",
                           backing=BACKING_OMNIGENT)
        with FakeOmnigentServer(include_cursor=False) as srv:
            self.assertNotIn("cursor", srv.backing().catalog_harnesses())
        with FakeOmnigentServer(include_cursor=True) as srv:
            backing = srv.backing()
            self.assertIn("cursor", backing.catalog_harnesses())
            # And the transport is harness-agnostic once the catalog exposes cursor.
            outcome = backing.run_seat(cursor_seat, "review cursor", base_env={})
            self.assertEqual(outcome.status, "OK")
            self.assertEqual(srv.session_requests()[0]["body"]["targetHarness"], "cursor")


# --- built-3 + native host leg unaffected ------------------------------------


class BuiltThreeAndNativeUnaffectedTests(unittest.TestCase):
    def test_mixed_board_built3_homebrew_and_opencode_omnigent(self) -> None:
        board = _board(
            Seat(model="gpt-5.5", effort="max", harness="codex", backing=BACKING_HOMEBREW),
            _opencode_seat(),
        )
        with FakeOmnigentServer() as srv:
            res = pi.invoke_board(board, "review", omnigent=srv.backing(), base_env={},
                                  spawn=lambda leg, art: ("OK", f"{leg}\nAGREE"))
        by_leg = {l.leg: l for l in res.legs}
        self.assertEqual(by_leg["codex"].status, "OK")       # homebrew spawn
        self.assertEqual(by_leg["codex"].text, "codex\nAGREE")
        self.assertEqual(by_leg["opencode"].status, "OK")    # omnigent transport
        self.assertIn("Echo:", by_leg["opencode"].text)

    def test_native_host_leg_omnigent_still_rejected_loud(self) -> None:
        # Wiring an omnigent backing must not weaken the native-host-leg invariant.
        host = HostContext(host_harness="claude")
        board = _board(Seat(model="claude-sonnet-5", effort="max", harness="claude",
                            backing=BACKING_OMNIGENT, host_leg=True))
        with FakeOmnigentServer() as srv:
            with self.assertRaises(ValueError):
                pi.invoke_board(board, "review", host=host, omnigent=srv.backing(), base_env={})

    def test_default_board_stays_all_homebrew_even_with_omnigent_wired(self) -> None:
        from phase_loop_runtime.advisor_board import DEFAULT_BOARD
        with FakeOmnigentServer() as srv:
            res = pi.invoke_board(DEFAULT_BOARD, "review", omnigent=srv.backing(),
                                  spawn=lambda leg, art: ("OK", f"{leg}\nAGREE"))
            # No session was ever created on the gateway — the default board is
            # all-homebrew, so the omnigent transport is never consulted.
            self.assertEqual(srv.session_requests(), [])
        self.assertEqual(tuple(l.leg for l in res.legs), ("codex", "gemini", "claude"))
        self.assertTrue(all(l.status == "OK" for l in res.legs))


# --- a skipped seat never blocks a healthy one -------------------------------


class SkipDoesNotBlockTests(unittest.TestCase):
    def test_catalog_skipped_omnigent_seat_does_not_block_healthy_codex(self) -> None:
        # A codex homebrew seat stays OK while an opencode omnigent seat whose
        # harness the catalog omits skips-with-warning — the skip never blocks.
        board = _board(
            Seat(model="gpt-5.5", effort="max", harness="codex", backing=BACKING_HOMEBREW),
            _opencode_seat(),
        )
        with FakeOmnigentServer(harnesses=set()) as srv:
            res = pi.invoke_board(board, "review", omnigent=srv.backing(), base_env={},
                                  spawn=lambda leg, art: ("OK", f"{leg}\nAGREE"))
        by_leg = {l.leg: l for l in res.legs}
        self.assertEqual(by_leg["codex"].status, "OK")
        self.assertEqual(by_leg["opencode"].status, "UNAVAILABLE")


# --- failure mapping (reuse failure-mapper categories) -----------------------


class FailureMappingTests(unittest.TestCase):
    def test_http_failure_classification_mirrors_failure_mapper(self) -> None:
        self.assertEqual(classify_http_failure(429, "usage cap")[1], "rate_limit")
        self.assertEqual(classify_http_failure(403, "billing issue")[1], "billing")
        self.assertEqual(classify_http_failure(403, "auth required")[1], "auth")
        self.assertEqual(classify_http_failure(403, "policy blocked")[1], "policy_denied")
        self.assertEqual(classify_http_failure(500, "boom")[1], "backend_unavailable")
        # Every reached-but-refused rejection degrades the SEAT (never blocks the board).
        for code, body in [(429, "x"), (403, "billing"), (403, "auth"), (403, "policy"), (500, "x")]:
            self.assertEqual(classify_http_failure(code, body)[0], "DEGRADED")

    def _run_with_rejection(self, rejection: str) -> str:
        with FakeOmnigentServer(reject_next_turn_with=rejection) as srv:
            res = pi.invoke_board(_board(_opencode_seat()), "review",
                                  omnigent=srv.backing(), base_env={})
        return res.legs[0]

    def test_turn_rejections_degrade_the_seat(self) -> None:
        for rejection, category in [("auth", "auth"), ("billing", "billing"),
                                    ("policy", "policy_denied"), ("rate_limit", "rate_limit")]:
            with self.subTest(rejection=rejection):
                leg = self._run_with_rejection(rejection)
                self.assertEqual(leg.status, "DEGRADED")
                self.assertIn(category, leg.detail or "")


class SharedSeamTests(unittest.TestCase):
    """The omnigent backing routes through the SHARED provider seam, exactly as
    the homebrew backing does — not a bespoke side channel."""

    def test_provider_satisfies_agent_runtime_provider_protocol(self) -> None:
        from phase_loop_runtime.agent_runtime_provider import (
            AgentRuntimeProvider,
            OmnigentAgentRuntimeProvider,
        )
        with FakeOmnigentServer() as srv:
            provider = OmnigentAgentRuntimeProvider(srv.backing().client)
            self.assertIsInstance(provider, AgentRuntimeProvider)

    def test_provider_drives_a_full_session_lifecycle_over_the_seam(self) -> None:
        from phase_loop_runtime.agent_runtime_provider import (
            CreateSessionRequest,
            OmnigentAgentRuntimeProvider,
            RUNTIME_OMNIGENT,
            SendTurnRequest,
        )
        with FakeOmnigentServer() as srv:
            provider = OmnigentAgentRuntimeProvider(srv.backing().client)
            info = provider.create_session(CreateSessionRequest(
                target_harness="opencode", idempotency_key="k1",
                title="t", runtime=RUNTIME_OMNIGENT))
            self.assertEqual(info.runtime, RUNTIME_OMNIGENT)
            provider.send_turn(SendTurnRequest(
                session_id=info.id, idempotency_key="t1", message="hi"))
            history = provider.read_history(info.id)
            self.assertTrue(any(e.type == "response.completed" for e in history.events))
            self.assertTrue(provider.health().available)
            provider.close_session(info.id)


if __name__ == "__main__":
    unittest.main()
