"""Omnigent provider backing (ABDOMNI, Phase 5).

The ``omnigent`` transport for breadth harnesses (opencode / pi, and cursor / amp
when the live catalog reports them). It routes a board seat through
omniagent-plus -> Omnigent **v0.4.0** over the frozen HTTP surface, opt-in and
fail-closed. It is the sibling of ABDHOME's ``homebrew`` backing (the built-3 +
native host leg): homebrew is hand-written and stays byte-for-byte the legacy
panel; omnigent is *not* hand-written per harness — it reuses the Omnigent
launcher, which is the whole point of the maintenance-offload (ABDHOME/ABDOMNI
non-goal: never hand-write a breadth adapter).

**We do not fork the transport.** This module mirrors the method set of
omniagent-plus ``core-contracts/src/provider.ts`` and issues requests ONLY against
the frozen v0.4.0 endpoint contract
(``fixtures/omnigent/discovery/http-surface.json``, vendored into the test tree at
``tests/fixtures/omnigent_contract/``). ``SESSION_ENDPOINTS`` / ``HARNESS_ENDPOINT``
below are this module's declared endpoint set;
``tests/test_advisor_board_backing_omnigent.py`` asserts every one appears in that
frozen contract and that the freeze target is ``0.4.0`` — so "faithful to v0.4.0"
is a checked invariant, not a claim.

**Auth = the same frozen no-silent-key contract as homebrew.** ``run_seat`` resolves
the seat's env through ``backing.resolve_seat_env`` (subscription scrubs EVERY
vendor key; an api-key seat, only behind the board opt-in, keeps ONLY the seat
vendor's key), then transmits vendor-key material to the gateway *derived from that
resolved env*. A subscription seat therefore puts zero vendor-key material on the
wire; an api-key opt-in seat transmits exactly the seat vendor's key. The gateway
**reports which auth lane it resolved** (``session.metadata.auth_lane``); ``run_seat``
reads it back, so no-silent-key is *observable* for omnigent seats — testable on the
recorded request, not merely asserted.

**Two distinct fail-closed skips** (they satisfy two different exit criteria):

* gateway unreachable -> ``select_backing`` degrades the seat skip-with-warning
  (``gateway_available=False``); ``OmnigentBacking.gateway_available()`` is the real
  probe the seam feeds it.
* harness absent from the live ``GET /v1/harnesses`` catalog -> a SEPARATE per-seat
  gate (``catalog_harnesses``). This is the dynamic cursor/amp gate: they route only
  when the live catalog exposes them, with no code gate.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Callable

from .backing import VENDOR_API_KEY_VARS, resolve_seat_env
from .schema import Seat, seat_vendor_family


# --- frozen v0.4.0 endpoint contract (checked against http-surface.json) ------

OMNIGENT_FREEZE_TARGET = "0.4.0"

# (method, path-template) set this client issues. Every entry MUST appear in the
# frozen ``http-surface.json`` (asserted by the conformance test). We use only the
# subset a single-turn advisory board leg needs.
HARNESS_ENDPOINT: tuple[str, str] = ("GET", "/v1/harnesses")
SESSION_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("POST", "/v1/sessions"),
    ("GET", "/v1/sessions/{session_id}"),
    ("GET", "/v1/sessions/{session_id}/items"),
    ("POST", "/v1/sessions/{session_id}/events"),
    ("DELETE", "/v1/sessions/{session_id}"),
)

# The per-seat-vendor key header prefix. A subscription seat sends NONE of these;
# an api-key opt-in seat sends exactly the seat vendor's key var(s). The gateway
# derives ``auth_lane`` from their presence (never from a client-asserted claim).
VENDOR_KEY_HEADER_PREFIX = "x-omnigent-key-"


# --- errors -----------------------------------------------------------------


class OmnigentHttpError(RuntimeError):
    """A non-2xx response from the Omnigent gateway. Mirrors
    ``http-client.ts``'s ``OmnigentHttpError`` (method/path/status/body)."""

    def __init__(self, *, method: str, path: str, status_code: int, body: Any) -> None:
        super().__init__(f"{method} {path} failed with {status_code}")
        self.method = method
        self.path = path
        self.status_code = status_code
        self.body = body


class OmnigentGatewayUnavailable(RuntimeError):
    """The gateway could not be reached at all (connection refused / DNS / reset).

    Distinct from ``OmnigentHttpError`` (a reached-but-refusing gateway): this is
    the gateway-DOWN condition that degrades an omnigent seat skip-with-warning.
    """


# --- HTTP client (faithful, synchronous port of http-client.ts) --------------

# Injection seam for tests: a callable mirroring ``urlopen`` semantics enough for
# the client. Default is the stdlib opener.
Opener = Callable[[urllib.request.Request, float | None], Any]


@dataclass
class OmnigentHttpClient:
    """Synchronous Omnigent v0.4.0 HTTP client (stdlib only, no new deps).

    Mirrors the ``core-contracts`` request/response shapes used by a single-turn
    board leg. ``base_url`` is the gateway; ``headers`` carries the gateway-bearer
    (the transport auth, NOT a vendor key). ``opener`` is injectable so tests can
    exercise the mapping without a socket, but the shipped tests run a real
    in-process fake server so the wire path is genuinely exercised.
    """

    base_url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    timeout: float | None = 30.0
    opener: Opener | None = None

    def _url(self, path: str) -> str:
        return self.base_url.rstrip("/") + path

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> Any:
        url = self._url(path)
        data = None
        headers = {**self.headers, **(extra_headers or {})}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["content-type"] = "application/json"
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        opener = self.opener or (lambda req, timeout: urllib.request.urlopen(req, timeout=timeout))
        try:
            response = opener(request, self.timeout)
        except urllib.error.HTTPError as exc:  # reached the gateway; it refused
            raw = exc.read()
            try:
                parsed = json.loads(raw.decode("utf-8")) if raw else None
            except (ValueError, UnicodeDecodeError):
                parsed = raw.decode("utf-8", "replace") if raw else None
            raise OmnigentHttpError(
                method=method, path=path, status_code=exc.code, body=parsed
            ) from exc
        except (urllib.error.URLError, ConnectionError, OSError) as exc:  # gateway down
            raise OmnigentGatewayUnavailable(f"{method} {path}: {exc}") from exc
        with response:
            status = getattr(response, "status", 200)
            payload = response.read()
        if status == 204 or not payload:
            return None
        return json.loads(payload.decode("utf-8"))

    # -- endpoint methods (each anchored to an SESSION_ENDPOINTS/HARNESS_ENDPOINT) --

    def list_harnesses(self) -> Mapping[str, Any]:
        return self._request("GET", "/v1/harnesses") or {}

    def create_session(
        self,
        *,
        target_harness: str,
        idempotency_key: str,
        title: str,
        initial_message: str | None = None,
        vendor_key_headers: Mapping[str, str] | None = None,
    ) -> Mapping[str, Any]:
        body = {
            "idempotencyKey": idempotency_key,
            "targetHarness": target_harness,
            "targetProvider": "omnigent",
            "title": title,
        }
        if initial_message is not None:
            body["initialMessage"] = initial_message
        return self._request("POST", "/v1/sessions", body=body, extra_headers=vendor_key_headers)

    def send_turn(self, session_id: str, message: str) -> Mapping[str, Any]:
        path = f"/v1/sessions/{_quote(session_id)}/events"
        return self._request("POST", path, body={"type": "message", "data": {"message": message}})

    def interrupt(self, session_id: str, reason: str = "user_request") -> Mapping[str, Any]:
        path = f"/v1/sessions/{_quote(session_id)}/events"
        return self._request("POST", path, body={"type": "interrupt", "data": {"reason": reason}})

    def get_session(self, session_id: str) -> Mapping[str, Any]:
        return self._request("GET", f"/v1/sessions/{_quote(session_id)}")

    def get_history(self, session_id: str) -> list[Any]:
        return self._request("GET", f"/v1/sessions/{_quote(session_id)}/items") or []

    def delete_session(self, session_id: str) -> None:
        self._request("DELETE", f"/v1/sessions/{_quote(session_id)}")


def _quote(value: str) -> str:
    return urllib.parse.quote(str(value), safe="")


# --- outcome + status mapping ------------------------------------------------


@dataclass(frozen=True)
class SeatRunOutcome:
    """Result of running one seat through Omnigent, in ``PanelLegResult`` terms.

    ``auth_lane`` is the lane the GATEWAY reported it resolved (read back from the
    session snapshot), so no-silent-key is testable for omnigent seats.
    """

    status: str          # a panel LEG_STATUS: OK | EMPTY | ERROR | DEGRADED | UNAVAILABLE
    text: str = ""
    detail: str = ""
    auth_lane: str | None = None


# response-stream event families (types.ts ``omnigentStreamEventTypes``).
_TEXT_DELTA = "response.output_text.delta"
_COMPLETED = {"response.completed", "turn.completed"}
_FAILED = {"response.failed", "turn.failed"}
_CANCELLED = {"response.cancelled", "turn.cancelled"}


def _extract_text_and_status(events: "tuple[Any, ...]") -> tuple[str, str]:
    """Fold a seam ``SessionHistory.events`` stream into ``(text, leg_status)``.

    Operates on the ``AgentRuntimeProvider`` history (``RuntimeEvent`` with ``.type``
    + ``.payload``), so the mapping runs on what the SEAM returns — not raw HTTP.
    Text is the concatenation of ``response.output_text.delta`` deltas plus any
    ``response.completed`` ``outputText`` (mirrors event-mapper.ts's text handling).
    Status keys on the terminal marker: completed -> OK/EMPTY, failed -> ERROR,
    cancelled -> DEGRADED, none -> ERROR (no terminal event, treated as a broken turn).
    """
    parts: list[str] = []
    completed = failed = cancelled = False
    for event in events:
        etype = event.type
        payload = event.payload
        if etype == _TEXT_DELTA and payload.get("delta"):
            parts.append(str(payload["delta"]))
        elif etype == "response.completed" and payload.get("outputText"):
            parts.append(str(payload["outputText"]))
        if etype in _COMPLETED:
            completed = True
        elif etype in _FAILED:
            failed = True
        elif etype in _CANCELLED:
            cancelled = True
    text = _dedupe_join(parts)
    if failed:
        return text, "ERROR"
    if cancelled:
        return text, "DEGRADED"
    if completed:
        return text, ("OK" if text.strip() else "EMPTY")
    return text, "ERROR"


def _dedupe_join(parts: list[str]) -> str:
    """Join text fragments, collapsing an exact delta/outputText duplicate (the
    fake emits the same ``Echo: …`` as both a delta and the completed outputText)."""
    out: list[str] = []
    for part in parts:
        if not out or out[-1] != part:
            out.append(part)
    return "\n".join(out)


def classify_http_failure(status_code: int, body_text: str) -> tuple[str, str]:
    """Map a gateway HTTP rejection to ``(leg_status, category)``.

    Mirrors omniagent-plus ``failure-mapper.ts``: 429 -> rate_limit, 403 with
    auth/billing in the body -> auth/billing, other 403 -> policy_denied, else
    backend_unavailable. Every reached-but-refused rejection degrades the SEAT
    (DEGRADED) — it never blocks the board and never silently downgrades auth.
    """
    text = (body_text or "").lower()
    if status_code == 429:
        return "DEGRADED", "rate_limit"
    if status_code == 403 and ("billing" in text or "quota" in text):
        return "DEGRADED", "billing"
    if status_code == 403 and "auth" in text:
        return "DEGRADED", "auth"
    if status_code == 403:
        return "DEGRADED", "policy_denied"
    return "DEGRADED", "backend_unavailable"


# --- the backing ------------------------------------------------------------


@dataclass
class OmnigentBacking:
    """The ``omnigent`` backing the board seam (``invoke_board``) routes through.

    Construct with a ready :class:`OmnigentHttpClient`, or via :meth:`from_env` /
    :meth:`from_config` so a production caller (ABDVERIFY) can wire it without
    reshaping this API. ``title`` is the session title prefix for board legs.
    """

    client: OmnigentHttpClient
    title_prefix: str = "advisor-board"

    # -- availability probes ---------------------------------------------------

    def catalog_harnesses(self) -> frozenset[str]:
        """The set of harness names the LIVE ``GET /v1/harnesses`` catalog reports.

        The v0.4.0 catalog is ``{category: [ {name, ...}, ... ]}`` (types.ts
        ``OmnigentHarnessCatalogResponse``); we flatten the ``name`` of every entry.
        This is the dynamic gate: cursor/amp route only when they appear here.
        """
        catalog = self.client.list_harnesses()
        names: set[str] = set()
        for entries in catalog.values():
            if not isinstance(entries, (list, tuple)):
                continue
            for entry in entries:
                if isinstance(entry, Mapping) and entry.get("name"):
                    names.add(str(entry["name"]).lower())
        return frozenset(names)

    def gateway_available(self) -> bool:
        """Real gateway-reachability probe the seam feeds ``select_backing``.

        A reachable gateway (even one returning a catalog) is available; a
        connection failure (gateway down) is not. A gateway that is up but returns
        an HTTP error is still 'reachable' — availability is about the transport,
        not the catalog contents (harness presence is the SEPARATE catalog gate)."""
        try:
            self.client.list_harnesses()
            return True
        except OmnigentGatewayUnavailable:
            return False
        except OmnigentHttpError:
            return True

    # -- run one seat ----------------------------------------------------------

    def run_seat(
        self,
        seat: Seat,
        artifact: str,
        *,
        base_env: Mapping[str, str],
        allow_api_key_fallback: bool = False,
    ) -> SeatRunOutcome:
        """Route one seat through Omnigent: create session -> send one turn ->
        read history -> map to a leg result, enforcing no-silent-key on the wire.

        Raises :class:`OmnigentGatewayUnavailable` if the gateway drops mid-run
        (the caller degrades it skip-with-warning) and ``ValueError`` for a
        never-silent-key violation (an api-key seat without the board opt-in — the
        caller maps it to DEGRADED, exactly like the homebrew path).

        Routes through the SHARED provider seam: it drives an
        :class:`~phase_loop_runtime.agent_runtime_provider.OmnigentAgentRuntimeProvider`
        (an ``AgentRuntimeProvider``) via ``create_session`` -> ``send_turn`` ->
        ``get_session_info`` -> ``read_history`` -> ``close_session`` — exactly as
        the homebrew path drives ``HomebrewAgentRuntimeProvider``.
        """
        # Import here to avoid a module-load cycle (agent_runtime_provider TYPE-imports
        # this module's client for annotations only).
        from ..agent_runtime_provider import (
            OMNIGENT_VENDOR_KEY_HEADERS_META,
            RUNTIME_OMNIGENT,
            CreateSessionRequest,
            OmnigentAgentRuntimeProvider,
            SendTurnRequest,
        )

        # 1) Frozen no-silent-key contract: the SAME env-scrub that governs the
        #    homebrew subprocess env governs what rides to the gateway. A
        #    subscription seat ends with zero vendor keys; an api-key opt-in seat
        #    keeps ONLY the seat vendor's key. (ValueError propagates: never-silent.)
        seat_env = resolve_seat_env(seat, base_env, allow_api_key_fallback=allow_api_key_fallback)
        vendor = seat_vendor_family(seat)
        vendor_key_headers = {
            f"{VENDOR_KEY_HEADER_PREFIX}{var}": seat_env[var]
            for var in VENDOR_API_KEY_VARS.get(vendor, ())
            if var in seat_env
        }

        provider = OmnigentAgentRuntimeProvider(self.client)
        title = f"{self.title_prefix}:{seat.seat_key}"
        metadata: dict[str, Any] = (
            {OMNIGENT_VENDOR_KEY_HEADERS_META: vendor_key_headers} if vendor_key_headers else {}
        )
        try:
            info = provider.create_session(
                CreateSessionRequest(
                    target_harness=seat.harness or "",
                    idempotency_key=uuid.uuid4().hex,
                    title=title,
                    runtime=RUNTIME_OMNIGENT,
                    metadata=metadata,
                )
            )
            provider.send_turn(
                SendTurnRequest(
                    session_id=info.id, idempotency_key=uuid.uuid4().hex, message=artifact
                )
            )
            # Re-read the snapshot through the seam: the gateway reports the resolved
            # auth lane in session metadata (so no-silent-key is observable).
            info = provider.get_session_info(info.id)
            history = provider.read_history(info.id)
        except OmnigentHttpError as exc:
            status, category = classify_http_failure(exc.status_code, _body_text(exc.body))
            return SeatRunOutcome(status=status, detail=f"omnigent {category}: HTTP {exc.status_code}")

        auth_lane = _reported_auth_lane(info.metadata)
        text, status = _extract_text_and_status(history.events)
        detail = f"omnigent v{OMNIGENT_FREEZE_TARGET} lane={auth_lane}" if auth_lane else ""
        # Best-effort close through the seam; never let cleanup change the verdict.
        try:
            provider.close_session(info.id)
        except (OmnigentHttpError, OmnigentGatewayUnavailable):
            pass
        return SeatRunOutcome(status=status, text=text, detail=detail, auth_lane=auth_lane)

    # -- factories -------------------------------------------------------------

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str],
        *,
        opener: Opener | None = None,
        title_prefix: str = "advisor-board",
    ) -> "OmnigentBacking | None":
        """Build from ``OMNIGENT_GATEWAY_URL`` (+ optional ``OMNIGENT_GATEWAY_TOKEN``
        gateway-bearer). Returns ``None`` when no gateway URL is configured, so the
        seam degrades omnigent seats skip-with-warning rather than erroring."""
        base_url = env.get("OMNIGENT_GATEWAY_URL")
        if not base_url:
            return None
        headers: dict[str, str] = {}
        token = env.get("OMNIGENT_GATEWAY_TOKEN")
        if token:
            headers["authorization"] = f"Bearer {token}"
        client = OmnigentHttpClient(base_url=base_url, headers=headers, opener=opener)
        return cls(client=client, title_prefix=title_prefix)

    @classmethod
    def from_config(
        cls,
        *,
        base_url: str,
        gateway_token: str | None = None,
        opener: Opener | None = None,
        title_prefix: str = "advisor-board",
    ) -> "OmnigentBacking":
        headers = {"authorization": f"Bearer {gateway_token}"} if gateway_token else {}
        client = OmnigentHttpClient(base_url=base_url, headers=headers, opener=opener)
        return cls(client=client, title_prefix=title_prefix)


def _body_text(body: Any) -> str:
    if isinstance(body, str):
        return body
    if body is None:
        return ""
    try:
        return json.dumps(body)
    except (TypeError, ValueError):
        return str(body)


def _reported_auth_lane(metadata: Mapping[str, Any] | None) -> str | None:
    """The auth lane the gateway reports it resolved (``metadata.auth_lane``)."""
    if isinstance(metadata, Mapping):
        lane = metadata.get("auth_lane")
        return str(lane) if lane is not None else None
    return None


__all__ = [
    "OMNIGENT_FREEZE_TARGET",
    "HARNESS_ENDPOINT",
    "SESSION_ENDPOINTS",
    "VENDOR_KEY_HEADER_PREFIX",
    "OmnigentHttpError",
    "OmnigentGatewayUnavailable",
    "OmnigentHttpClient",
    "SeatRunOutcome",
    "OmnigentBacking",
    "classify_http_failure",
]
