from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
from typing import Any
from urllib import error, request
from urllib.parse import urlparse
import ipaddress


ACK_POLICY_TOOL_REQUIRED = "tool_ack_required"
CLAUDE_ROUTE_NAMES = {"claude_channel", "claude_agent_view", "claude_print"}
CLAUDE_ROUTE_STATUSES = {"received", "working", "needs_permission", "needs_input", "blocked", "done", "error", "stale"}
CLAUDE_AUTH_POSTURES = {"subscription_local", "api_key", "unknown"}
CLAUDE_BILLING_POSTURES = {"subscription_included", "api_key_billed", "usage_credit", "unknown"}
REPLY_STATUSES = {"received", "working", "blocked", "done", "error"}
FORBIDDEN_ATTACHMENT_FIELDS = {
    "content",
    "data",
    "text",
    "payload",
    "secret",
    "token",
    "api_key",
    "private_key",
    "oauth",
    "keychain",
    "local_env",
    "provider_payload",
    "terminal_transcript",
}
FORBIDDEN_ROUTE_RESULT_VALUE_MARKERS = (
    "authorization:",
    "bearer ",
    "api_key=",
    "secret=",
    "password=",
    "oauth",
    "keychain",
    "local env value",
    "provider payload",
    "raw provider payload",
    "terminal transcript",
    "raw terminal",
    "raw-terminal",
    "raw prompt",
    "raw tool input",
    "sk-ant-",
    "ghp_",
    "github_pat_",
)
FORBIDDEN_PERMISSION_FIELDS = {
    "args",
    "arguments",
    "content",
    "data",
    "input",
    "payload",
    "raw_input",
    "secret",
    "token",
    "api_key",
    "private_key",
    "oauth",
    "keychain",
    "local_env",
    "provider_payload",
    "terminal_transcript",
}
PERMISSION_VERDICTS = {"allow", "deny"}
SESSION_STATES = {"disconnected", "starting", "ready", "needs_permission", "needs_input", "blocked", "stopped", "stale"}
CHANNEL_HEALTH_STATES = {"disconnected", "starting", "ready", "needs_permission", "needs_input", "blocked", "stopped"}
CLIENT_READY_STATES = {"starting", "ready", "needs_permission", "needs_input"}
CLIENT_FINAL_REPLY_STATUSES = {"blocked", "done", "error"}


class LoopbackThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def server_bind(self) -> None:
        TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)


@dataclass(frozen=True)
class ChannelEventEnvelope:
    event_id: str
    session_id: str
    sender: str
    content: str
    attachments: tuple[dict[str, Any], ...]
    created_at: str
    ack_policy: str = ACK_POLICY_TOOL_REQUIRED

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["attachments"] = list(self.attachments)
        return data


@dataclass(frozen=True)
class ChannelReplyPayload:
    event_id: str
    status: str
    text: str = ""
    artifacts: tuple[dict[str, Any], ...] = ()
    error: str | None = None
    final: bool = False

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["artifacts"] = list(self.artifacts)
        return data


@dataclass(frozen=True)
class ClaudeRouteResult:
    route: str
    session_id: str
    event_id: str
    status: str
    text: str = ""
    artifacts: tuple[dict[str, Any], ...] = ()
    auth_posture: str = "unknown"
    billing_posture: str = "unknown"
    trust_state: dict[str, Any] = field(default_factory=dict)
    permission_state: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    evidence_refs: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        _require_literal(self.route, CLAUDE_ROUTE_NAMES, "Claude route")
        _require_literal(self.status, CLAUDE_ROUTE_STATUSES, "Claude route status")
        _require_literal(self.auth_posture, CLAUDE_AUTH_POSTURES, "Claude auth posture")
        _require_literal(self.billing_posture, CLAUDE_BILLING_POSTURES, "Claude billing posture")
        for field_name in ("session_id", "event_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field_name} is required")
        object.__setattr__(self, "text", _metadata_only_text(self.text, "text"))
        object.__setattr__(self, "artifacts", tuple(_metadata_only_route_objects(self.artifacts, "artifacts")))
        object.__setattr__(self, "trust_state", _metadata_summary(self.trust_state))
        object.__setattr__(self, "permission_state", _metadata_summary(self.permission_state))
        object.__setattr__(self, "warnings", tuple(_metadata_only_warnings(self.warnings)))
        object.__setattr__(self, "evidence_refs", tuple(_metadata_only_route_objects(self.evidence_refs, "evidence_refs")))

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["artifacts"] = list(self.artifacts)
        data["warnings"] = list(self.warnings)
        data["evidence_refs"] = list(self.evidence_refs)
        return data


@dataclass(frozen=True)
class PermissionRequestEnvelope:
    request_id: str
    session_id: str
    event_id: str | None
    tool_name: str
    description: str
    input_preview: str
    risk_class: str
    audit_ref: str
    requested_at: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PermissionVerdictPayload:
    request_id: str
    verdict: str
    actor: str
    reason: str
    decided_at: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PermissionAuditEntry:
    request_id: str
    session_id: str
    event_id: str | None
    verdict: str
    actor: str
    reason: str
    audit_ref: str
    decided_at: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SessionRegistryRecord:
    session_id: str
    adapter: str
    cwd: str
    state: str = "starting"
    auth_posture: dict[str, Any] = field(default_factory=dict)
    trust_state: dict[str, Any] = field(default_factory=dict)
    channel_health: str = "starting"
    last_event_id: str | None = None
    last_reply_at: str | None = None
    permission_state: dict[str, Any] = field(default_factory=lambda: {"pending": 0, "last_verdict": None})
    updated_at: str = field(default_factory=lambda: _utc_now())
    process_pid: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "adapter": self.adapter,
            "cwd": self.cwd,
            "state": self.state,
            "auth_posture": dict(self.auth_posture),
            "trust_state": dict(self.trust_state),
            "channel_health": self.channel_health,
            "last_event_id": self.last_event_id,
            "last_reply_at": self.last_reply_at,
            "permission_state": dict(self.permission_state),
        }


@dataclass
class ChannelEventState:
    envelope: ChannelEventEnvelope
    replies: list[ChannelReplyPayload] = field(default_factory=list)
    acknowledged: bool = False
    acknowledged_at: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            **self.envelope.to_json(),
            "acknowledged": self.acknowledged,
            "acknowledged_at": self.acknowledged_at,
            "replies": [reply.to_json() for reply in self.replies],
        }


class ChannelSidecar:
    def __init__(
        self,
        *,
        bearer_token: str | None = None,
        allowed_senders: set[str] | None = None,
        allowed_verdict_actors: set[str] | None = None,
    ) -> None:
        if bearer_token == "":
            raise ValueError("bearer_token must not be empty")
        self._bearer_token = bearer_token
        self._allowed_senders = set(allowed_senders or set())
        self._allowed_verdict_actors = set(allowed_verdict_actors or set())
        self._events_by_session: dict[str, list[str]] = {}
        self._events_by_id: dict[str, ChannelEventState] = {}
        self._permission_requests_by_session: dict[str, list[str]] = {}
        self._permission_requests_by_id: dict[str, PermissionRequestEnvelope] = {}
        self._permission_audit_by_session: dict[str, list[PermissionAuditEntry]] = {}
        self._sessions_by_id: dict[str, SessionRegistryRecord] = {}
        self._hook_events_by_session: dict[str, list[dict[str, Any]]] = {}
        self._lock = threading.RLock()

    @property
    def bearer_token_configured(self) -> bool:
        return self._bearer_token is not None

    def authenticate(self, authorization: str | None) -> HTTPStatus | None:
        if self._bearer_token is None:
            return None
        if not authorization:
            return HTTPStatus.UNAUTHORIZED
        if authorization != f"Bearer {self._bearer_token}":
            return HTTPStatus.FORBIDDEN
        return None

    def create_message(
        self,
        session_id: str,
        *,
        sender: str,
        content: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> ChannelEventEnvelope:
        if not session_id:
            raise ValueError("session_id is required")
        if not isinstance(sender, str) or not sender:
            raise ValueError("sender is required")
        if self._allowed_senders and sender not in self._allowed_senders:
            raise PermissionError("sender is not allowed")
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        clean_attachments = tuple(_metadata_only_attachments(attachments or []))
        envelope = ChannelEventEnvelope(
            event_id=str(uuid.uuid4()),
            session_id=session_id,
            sender=sender,
            content=content,
            attachments=clean_attachments,
            created_at=_utc_now(),
        )
        with self._lock:
            self._events_by_id[envelope.event_id] = ChannelEventState(envelope=envelope)
            self._events_by_session.setdefault(session_id, []).append(envelope.event_id)
            if session_id in self._sessions_by_id:
                record = self._sessions_by_id[session_id]
                record.last_event_id = envelope.event_id
                record.channel_health = "starting" if record.channel_health == "disconnected" else record.channel_health
                record.updated_at = _utc_now()
        return envelope

    def list_events(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            event_ids = list(self._events_by_session.get(session_id, []))
            return [self._events_by_id[event_id].to_json() for event_id in event_ids]

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        with self._lock:
            state = self._events_by_id.get(event_id)
            return state.to_json() if state else None

    def record_reply(self, payload: ChannelReplyPayload | dict[str, Any]) -> dict[str, Any]:
        reply = payload if isinstance(payload, ChannelReplyPayload) else _reply_payload(payload)
        with self._lock:
            state = self._events_by_id.get(reply.event_id)
            if state is None:
                raise KeyError(f"unknown event_id: {reply.event_id}")
            state.replies.append(reply)
            self._update_session_from_reply(state.envelope.session_id, reply)
            if reply.final:
                state.acknowledged = True
                state.acknowledged_at = _utc_now()
            return state.to_json()

    def record_status(self, payload: ChannelReplyPayload | dict[str, Any]) -> dict[str, Any]:
        return self.record_reply(payload)

    def create_permission_request(self, session_id: str, payload: dict[str, Any]) -> PermissionRequestEnvelope:
        with self._lock:
            event_id = self._sessions_by_id.get(session_id).last_event_id if session_id in self._sessions_by_id else None
            request_payload = _permission_request_payload(session_id, payload, event_id=event_id)
            self._permission_requests_by_id[request_payload.request_id] = request_payload
            self._permission_requests_by_session.setdefault(session_id, []).append(request_payload.request_id)
            self._update_permission_state(session_id)
        return request_payload

    def list_permission_requests(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            request_ids = list(self._permission_requests_by_session.get(session_id, []))
            return [self._permission_requests_by_id[request_id].to_json() for request_id in request_ids]

    def record_permission_verdict(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        verdict = _permission_verdict_payload(payload)
        with self._lock:
            request_payload = self._permission_requests_by_id.get(verdict.request_id)
            if request_payload is None or request_payload.session_id != session_id:
                raise KeyError(f"unknown request_id: {verdict.request_id}")
            if self._allowed_verdict_actors and verdict.actor not in self._allowed_verdict_actors:
                raise PermissionError("verdict actor is not allowed")
            audit_ref = f"claude_permission:{session_id}:{verdict.request_id}"
            audit_entry = PermissionAuditEntry(
                request_id=verdict.request_id,
                session_id=session_id,
                event_id=request_payload.event_id,
                verdict=verdict.verdict,
                actor=verdict.actor,
                reason=verdict.reason,
                audit_ref=audit_ref,
                decided_at=verdict.decided_at,
            )
            self._permission_audit_by_session.setdefault(session_id, []).append(audit_entry)
            self._update_permission_state(session_id)
            if session_id in self._sessions_by_id:
                if verdict.verdict == "deny":
                    self._sessions_by_id[session_id].state = "blocked"
                    self._sessions_by_id[session_id].channel_health = "blocked"
                elif self._sessions_by_id[session_id].permission_state.get("pending") == 0:
                    self._sessions_by_id[session_id].state = "ready"
                    self._sessions_by_id[session_id].channel_health = "ready"
        return audit_entry.to_json()

    def list_permission_audit(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [entry.to_json() for entry in self._permission_audit_by_session.get(session_id, [])]

    def register_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = _required_string(payload, "session_id")
        adapter = _required_string(payload, "adapter")
        cwd = _required_string(payload, "cwd")
        state = str(payload.get("state") or "starting")
        channel_health = str(payload.get("channel_health") or "starting")
        if state not in SESSION_STATES:
            raise ValueError("state must be one of: " + ", ".join(sorted(SESSION_STATES)))
        if channel_health not in CHANNEL_HEALTH_STATES:
            raise ValueError("channel_health must be one of: " + ", ".join(sorted(CHANNEL_HEALTH_STATES)))
        record = SessionRegistryRecord(
            session_id=session_id,
            adapter=adapter,
            cwd=cwd,
            state=state,
            auth_posture=_metadata_summary(payload.get("auth_posture") or {}),
            trust_state=_metadata_summary(payload.get("trust_state") or {}),
            channel_health=channel_health,
            process_pid=payload.get("process_pid") if isinstance(payload.get("process_pid"), int) else None,
        )
        with self._lock:
            self._sessions_by_id[session_id] = record
            self._update_permission_state(session_id)
            return record.to_json()

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            return [record.to_json() for record in self._sessions_by_id.values()]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._sessions_by_id.get(session_id)
            return record.to_json() if record else None

    def update_session_state(self, session_id: str, *, state: str | None = None, channel_health: str | None = None) -> dict[str, Any]:
        with self._lock:
            record = self._sessions_by_id.get(session_id)
            if record is None:
                raise KeyError(f"unknown session_id: {session_id}")
            if state is not None:
                if state not in SESSION_STATES:
                    raise ValueError("state must be one of: " + ", ".join(sorted(SESSION_STATES)))
                record.state = state
            if channel_health is not None:
                if channel_health not in CHANNEL_HEALTH_STATES:
                    raise ValueError("channel_health must be one of: " + ", ".join(sorted(CHANNEL_HEALTH_STATES)))
                record.channel_health = channel_health
            record.updated_at = _utc_now()
            return record.to_json()

    def mark_stale_sessions(self, *, older_than_seconds: int, live_pids: set[int] | None = None) -> list[dict[str, Any]]:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
        stale: list[dict[str, Any]] = []
        with self._lock:
            for record in self._sessions_by_id.values():
                updated_at = _parse_utc(record.updated_at)
                pid_missing = record.process_pid is not None and live_pids is not None and record.process_pid not in live_pids
                if record.state not in {"stopped", "stale"} and (updated_at < cutoff or pid_missing):
                    record.state = "stale"
                    record.channel_health = "stopped"
                    record.updated_at = _utc_now()
                    stale.append(record.to_json())
        return stale

    def record_hook_event(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        hook_event = {
            "event_id": str(uuid.uuid4()),
            "session_id": session_id,
            "hook": _required_string(payload, "hook"),
            "cwd": str(payload.get("cwd") or ""),
            "permission_mode": str(payload.get("permission_mode") or ""),
            "received_at": _utc_now(),
        }
        with self._lock:
            self._hook_events_by_session.setdefault(session_id, []).append(hook_event)
            if session_id in self._sessions_by_id:
                record = self._sessions_by_id[session_id]
                if hook_event["hook"] == "Notification":
                    if record.permission_state.get("pending", 0) > 0:
                        record.state = "needs_permission"
                        record.channel_health = "needs_permission"
                    else:
                        record.state = "needs_input"
                        record.channel_health = "needs_input"
                record.updated_at = hook_event["received_at"]
        return hook_event

    def _update_session_from_reply(self, session_id: str, reply: ChannelReplyPayload) -> None:
        record = self._sessions_by_id.get(session_id)
        if record is None:
            return
        record.last_event_id = reply.event_id
        record.last_reply_at = _utc_now()
        if reply.status in {"blocked", "error"}:
            record.state = "blocked"
            record.channel_health = "blocked"
        elif reply.status in {"received", "working", "done"}:
            record.state = "ready"
            record.channel_health = "ready"
        record.updated_at = record.last_reply_at

    def _update_permission_state(self, session_id: str) -> None:
        record = self._sessions_by_id.get(session_id)
        if record is None:
            return
        request_ids = self._permission_requests_by_session.get(session_id, [])
        audit = self._permission_audit_by_session.get(session_id, [])
        decided = {entry.request_id for entry in audit}
        record.permission_state = {
            "pending": len([request_id for request_id in request_ids if request_id not in decided]),
            "last_verdict": audit[-1].verdict if audit else None,
            "last_request_id": request_ids[-1] if request_ids else None,
            "last_audit_ref": audit[-1].audit_ref if audit else None,
        }
        if record.permission_state["pending"] > 0 and record.state not in {"blocked", "stopped", "stale"}:
            record.state = "needs_permission"
            record.channel_health = "needs_permission"
        record.updated_at = _utc_now()


class ChannelSidecarClientError(RuntimeError):
    def __init__(self, reason: str, *, status_code: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


class ChannelSidecarClient:
    def __init__(
        self,
        *,
        base_url: str,
        session_id: str,
        sender: str = "phase-loop",
        bearer_token: str | None = None,
        timeout_seconds: float = 60,
        poll_interval_seconds: float = 0.25,
        opener: Any | None = None,
    ) -> None:
        if not is_loopback_http_url(base_url):
            raise ValueError("claude channel client requires a loopback http base_url")
        if not session_id:
            raise ValueError("session_id is required")
        if not sender:
            raise ValueError("sender is required")
        self.base_url = base_url.rstrip("/")
        self.session_id = session_id
        self.sender = sender
        self._bearer_token = bearer_token
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self._opener = opener or request.urlopen

    def preflight(self) -> dict[str, Any]:
        session = self._request_json("GET", f"/sessions/{self.session_id}")
        state = str(session.get("state") or "")
        health = str(session.get("channel_health") or "")
        if state not in CLIENT_READY_STATES or health not in CLIENT_READY_STATES:
            raise ChannelSidecarClientError(f"channel session is not ready: state={state or 'unknown'} health={health or 'unknown'}")
        return session

    def send_and_wait(self, content: str, *, attachments: list[dict[str, Any]] | None = None) -> ClaudeRouteResult:
        session = self.preflight()
        envelope = self._request_json(
            "POST",
            f"/sessions/{self.session_id}/message",
            {"sender": self.sender, "content": content, "attachments": attachments or []},
        )
        event_id = str(envelope.get("event_id") or "")
        if not event_id:
            raise ChannelSidecarClientError("channel sidecar returned no event id")
        final_event = self._wait_for_final_event(event_id)
        latest_session = session
        if final_event is None:
            try:
                latest_session = self._request_json("GET", f"/sessions/{self.session_id}")
            except ChannelSidecarClientError:
                latest_session = session
        return self._event_result(latest_session, final_event, event_id)

    def _wait_for_final_event(self, event_id: str) -> dict[str, Any] | None:
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() <= deadline:
            events_payload = self._request_json("GET", f"/sessions/{self.session_id}/events")
            for event_payload in events_payload.get("events", []):
                if event_payload.get("event_id") != event_id:
                    continue
                replies = event_payload.get("replies") or []
                if event_payload.get("acknowledged") or any(reply.get("final") for reply in replies):
                    return event_payload
            time.sleep(self.poll_interval_seconds)
        return None

    def _event_result(self, session: dict[str, Any], event_payload: dict[str, Any] | None, event_id: str) -> ClaudeRouteResult:
        if event_payload is None:
            pending = (session.get("permission_state") or {}).get("pending")
            session_state = str(session.get("state") or "")
            if isinstance(pending, (int, float)) and pending > 0:
                status = "needs_permission"
                text = "channel permission required"
                warnings = ("channel permission request is pending",)
            elif session_state == "needs_input":
                status = "needs_input"
                text = "channel session needs input"
                warnings = ("channel hook reported needs input",)
            else:
                status = "stale"
                text = "channel reply timed out"
                warnings = ("channel reply/status acknowledgement timed out",)
            return ClaudeRouteResult(
                route="claude_channel",
                session_id=self.session_id,
                event_id=event_id,
                status=status,
                text=text,
                auth_posture=_client_auth_posture(session),
                billing_posture=_client_billing_posture(session),
                trust_state=session.get("trust_state") or {},
                permission_state=session.get("permission_state") or {},
                warnings=warnings,
                evidence_refs=({"kind": "claude_channel_event", "event_id": event_id},),
            )
        final_reply = _final_reply(event_payload)
        status = str(final_reply.get("status") or "working") if final_reply else "working"
        if status not in CLAUDE_ROUTE_STATUSES:
            status = "error"
        return ClaudeRouteResult(
            route="claude_channel",
            session_id=self.session_id,
            event_id=event_id,
            status=status,
            text=str(final_reply.get("text") or "") if final_reply else "",
            artifacts=tuple(final_reply.get("artifacts") or []) if final_reply else (),
            auth_posture=_client_auth_posture(session),
            billing_posture=_client_billing_posture(session),
            trust_state=session.get("trust_state") or {},
            permission_state=session.get("permission_state") or {},
            warnings=tuple(_client_warnings(event_payload)),
            evidence_refs=({"kind": "claude_channel_event", "event_id": event_id},),
        )

    def _request_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload, sort_keys=True).encode("utf-8")
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self._bearer_token:
            headers["Authorization"] = f"Bearer {self._bearer_token}"
        req = request.Request(f"{self.base_url}{path}", data=body, headers=headers, method=method)
        try:
            with self._opener(req, timeout=max(self.timeout_seconds, 1)) as response:
                decoded = response.read().decode("utf-8")
        except error.HTTPError as exc:
            raise ChannelSidecarClientError(_client_http_reason(exc), status_code=exc.code) from exc
        except error.URLError as exc:
            raise ChannelSidecarClientError(f"channel sidecar unreachable: {exc.reason.__class__.__name__}") from exc
        data = json.loads(decoded or "{}")
        if not isinstance(data, dict):
            raise ChannelSidecarClientError("channel sidecar returned non-object JSON")
        return data


def is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    if normalized == "localhost":
        return True
    # Accept the full loopback ranges (127.0.0.0/8, ::1), not just 127.0.0.1, so a
    # sidecar bound to e.g. 127.0.0.2 is correctly classified as loopback. Anything
    # that is not a parseable loopback IP (or `localhost`) is non-loopback.
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def is_loopback_http_url(url: str) -> bool:
    """True iff `url` is an http URL bound to a loopback host.

    Single source of truth for the Channel sidecar transport posture: a Channel
    route must point at a loopback sidecar (no remote/non-loopback transport).
    Used by both the build-time route preflight (launcher) and the sidecar client.
    """

    if not url:
        return False
    parsed = urlparse(url)
    return parsed.scheme == "http" and bool(parsed.hostname) and is_loopback_host(parsed.hostname)


def make_handler(sidecar: ChannelSidecar) -> type[BaseHTTPRequestHandler]:
    class ChannelSidecarHandler(BaseHTTPRequestHandler):
        server_version = "PhaseLoopChannelSidecar/0.1"

        def do_POST(self) -> None:  # noqa: N802
            try:
                self._handle_post()
            except ValueError as exc:
                self._write_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except KeyError as exc:
                self._write_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            except PermissionError as exc:
                self._write_json({"error": str(exc)}, HTTPStatus.FORBIDDEN)

        def do_GET(self) -> None:  # noqa: N802
            if self._path_parts()[:1] != ["sessions"]:
                self._write_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            parts = self._path_parts()
            if len(parts) == 1:
                self._write_json({"sessions": sidecar.list_sessions()})
                return
            if len(parts) == 2:
                session = sidecar.get_session(parts[1])
                if session is None:
                    self._write_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                    return
                self._write_json(session)
                return
            if len(parts) == 4 and parts[2] == "permission" and parts[3] in {"requests", "audit"}:
                payload_key = "requests" if parts[3] == "requests" else "audit"
                payload = (
                    sidecar.list_permission_requests(parts[1])
                    if parts[3] == "requests"
                    else sidecar.list_permission_audit(parts[1])
                )
                self._write_json({payload_key: payload})
                return
            if len(parts) != 3 or parts[2] != "events":
                self._write_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            events = sidecar.list_events(parts[1])
            if "text/event-stream" in self.headers.get("Accept", ""):
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for event in events:
                    self.wfile.write(f"data: {json.dumps(event, sort_keys=True)}\n\n".encode("utf-8"))
                return
            self._write_json({"events": events})

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _handle_post(self) -> None:
            parts = self._path_parts()
            if len(parts) not in {3, 4} or parts[0] != "sessions":
                self._write_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            payload = self._read_json()
            session_id = parts[1]
            if parts[2] == "register":
                payload.setdefault("session_id", session_id)
                self._write_json(sidecar.register_session(payload), HTTPStatus.CREATED)
                return
            if parts[2] == "state":
                self._write_json(sidecar.update_session_state(session_id, state=payload.get("state"), channel_health=payload.get("channel_health")))
                return
            if parts[2] == "hook":
                self._write_json(sidecar.record_hook_event(session_id, payload), HTTPStatus.CREATED)
                return
            if parts[2] == "message":
                if auth_status := sidecar.authenticate(self.headers.get("Authorization")):
                    self._write_json({"error": "authentication required"}, auth_status)
                    return
                envelope = sidecar.create_message(
                    session_id,
                    sender=payload.get("sender", ""),
                    content=payload.get("content", ""),
                    attachments=payload.get("attachments", []),
                )
                self._write_json(envelope.to_json(), HTTPStatus.CREATED)
                return
            if len(parts) == 4 and parts[2] == "permission" and parts[3] == "request":
                request_payload = sidecar.create_permission_request(session_id, payload)
                self._write_json(request_payload.to_json(), HTTPStatus.CREATED)
                return
            if len(parts) == 4 and parts[2] == "permission" and parts[3] == "verdict":
                if auth_status := sidecar.authenticate(self.headers.get("Authorization")):
                    self._write_json({"error": "authentication required"}, auth_status)
                    return
                audit_entry = sidecar.record_permission_verdict(session_id, payload)
                self._write_json(audit_entry, HTTPStatus.CREATED)
                return
            if parts[2] in {"reply", "status"}:
                if auth_status := sidecar.authenticate(self.headers.get("Authorization")):
                    self._write_json({"error": "authentication required"}, auth_status)
                    return
                payload.setdefault("event_id", payload.get("eventId"))
                state = sidecar.record_reply(payload) if parts[2] == "reply" else sidecar.record_status(payload)
                self._write_json(state)
                return
            self._write_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def _path_parts(self) -> list[str]:
            return [part for part in urlparse(self.path).path.split("/") if part]

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                return {}
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("payload must be a JSON object")
            return data

        def _write_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

    return ChannelSidecarHandler


def build_server(
    host: str = "127.0.0.1",
    port: int = 0,
    sidecar: ChannelSidecar | None = None,
    *,
    bearer_token: str | None = None,
    allowed_senders: set[str] | None = None,
    allowed_verdict_actors: set[str] | None = None,
) -> ThreadingHTTPServer:
    if not is_loopback_host(host):
        raise ValueError("claude channel sidecar binds to loopback hosts only")
    return LoopbackThreadingHTTPServer(
        (host, port),
        make_handler(sidecar or ChannelSidecar(bearer_token=bearer_token, allowed_senders=allowed_senders, allowed_verdict_actors=allowed_verdict_actors)),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local phase-loop Claude Channel sidecar.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    server = build_server(args.host, args.port)
    sys.stderr.write(f"phase-loop channel sidecar listening on http://{args.host}:{args.port}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 130
    finally:
        server.server_close()
    return 0


def _metadata_only_attachments(attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(attachments, list):
        raise ValueError("attachments must be a list")
    clean: list[dict[str, Any]] = []
    for attachment in attachments:
        if not isinstance(attachment, dict):
            raise ValueError("attachments must contain metadata objects")
        forbidden = FORBIDDEN_ATTACHMENT_FIELDS.intersection({str(key).lower() for key in attachment})
        if forbidden:
            raise ValueError(f"attachment contains non-metadata fields: {', '.join(sorted(forbidden))}")
        clean.append(dict(attachment))
    return clean


def _reply_payload(payload: dict[str, Any]) -> ChannelReplyPayload:
    event_id = payload.get("event_id")
    status = payload.get("status")
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("event_id is required")
    if status not in REPLY_STATUSES:
        raise ValueError("status must be one of: " + ", ".join(sorted(REPLY_STATUSES)))
    artifacts = payload.get("artifacts") or []
    if not isinstance(artifacts, list):
        raise ValueError("artifacts must be a list")
    return ChannelReplyPayload(
        event_id=event_id,
        status=status,
        text=str(payload.get("text") or ""),
        artifacts=tuple(_metadata_only_attachments(artifacts)),
        error=payload.get("error"),
        final=bool(payload.get("final", False)),
    )


def _final_reply(event_payload: dict[str, Any]) -> dict[str, Any] | None:
    replies = event_payload.get("replies") or []
    for reply in reversed(replies):
        if reply.get("final") or reply.get("status") in CLIENT_FINAL_REPLY_STATUSES:
            return reply
    return replies[-1] if replies else None


def _client_warnings(event_payload: dict[str, Any]) -> list[str]:
    warnings = []
    if event_payload.get("ack_policy") == ACK_POLICY_TOOL_REQUIRED and not event_payload.get("acknowledged"):
        warnings.append("channel reply tool acknowledgement missing")
    return warnings


def _client_auth_posture(session: dict[str, Any]) -> str:
    posture = session.get("auth_posture") or {}
    method = str(posture.get("method") or posture.get("authMethod") or "").lower()
    status = str(posture.get("status") or "").lower()
    if method == "subscription" and status in {"authenticated", "ok", "ready"}:
        return "subscription_local"
    if method in {"api_key", "apikey", "key"}:
        return "api_key"
    return "unknown"


def _client_billing_posture(session: dict[str, Any]) -> str:
    auth_posture = _client_auth_posture(session)
    if auth_posture == "subscription_local":
        return "subscription_included"
    if auth_posture == "api_key":
        return "api_key_billed"
    return "unknown"


def _client_http_reason(exc: error.HTTPError) -> str:
    if exc.code in {HTTPStatus.UNAUTHORIZED.value, HTTPStatus.FORBIDDEN.value}:
        return "channel sidecar authentication failed"
    if exc.code == HTTPStatus.NOT_FOUND.value:
        return "channel session not found"
    return f"channel sidecar http error: {exc.code}"


def _permission_request_payload(session_id: str, payload: dict[str, Any], *, event_id: str | None = None) -> PermissionRequestEnvelope:
    if not session_id:
        raise ValueError("session_id is required")
    forbidden = FORBIDDEN_PERMISSION_FIELDS.intersection({str(key).lower() for key in payload})
    if forbidden:
        raise ValueError(f"permission request contains raw or secret-like fields: {', '.join(sorted(forbidden))}")
    allowed = {"tool_name", "description", "input_preview", "risk_class"}
    extra = set(payload) - allowed
    if extra:
        raise ValueError(f"permission request contains unsupported fields: {', '.join(sorted(extra))}")
    tool_name = payload.get("tool_name")
    description = payload.get("description")
    input_preview = payload.get("input_preview")
    risk_class = payload.get("risk_class")
    for field_name, value in (
        ("tool_name", tool_name),
        ("description", description),
        ("input_preview", input_preview),
        ("risk_class", risk_class),
    ):
        if not isinstance(value, str) or not value:
            raise ValueError(f"{field_name} is required")
        _raise_if_secret_like_value(value, field_name)
    request_id = str(uuid.uuid4())
    return PermissionRequestEnvelope(
        request_id=request_id,
        session_id=session_id,
        event_id=event_id,
        tool_name=tool_name,
        description=description,
        input_preview=input_preview,
        risk_class=risk_class,
        audit_ref=f"claude_permission:{session_id}:{request_id}",
        requested_at=_utc_now(),
    )


def _permission_verdict_payload(payload: dict[str, Any]) -> PermissionVerdictPayload:
    request_id = payload.get("request_id")
    verdict = payload.get("verdict")
    actor = payload.get("actor")
    reason = payload.get("reason")
    if not isinstance(request_id, str) or not request_id:
        raise ValueError("request_id is required")
    if verdict not in PERMISSION_VERDICTS:
        raise ValueError("verdict must be one of: " + ", ".join(sorted(PERMISSION_VERDICTS)))
    if not isinstance(actor, str) or not actor:
        raise ValueError("actor is required")
    if not isinstance(reason, str) or not reason:
        raise ValueError("reason is required")
    return PermissionVerdictPayload(
        request_id=request_id,
        verdict=verdict,
        actor=actor,
        reason=reason,
        decided_at=_utc_now(),
    )


def _require_literal(value: str, allowed: set[str], label: str) -> None:
    if value not in allowed:
        raise ValueError(f"{label} must be one of: " + ", ".join(sorted(allowed)))


def _required_string(payload: dict[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} is required")
    return value


def _metadata_only_text(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    _raise_if_secret_like_value(value, field_name)
    return value


def _metadata_only_warnings(warnings: tuple[str, ...] | list[str]) -> list[str]:
    if not isinstance(warnings, (tuple, list)):
        raise ValueError("warnings must be a list")
    clean: list[str] = []
    for warning in warnings:
        if not isinstance(warning, str):
            raise ValueError("warnings must contain strings")
        _raise_if_secret_like_value(warning, "warnings")
        clean.append(warning)
    return clean


def _metadata_only_route_objects(items: tuple[dict[str, Any], ...] | list[dict[str, Any]], field_name: str) -> list[dict[str, Any]]:
    if not isinstance(items, (tuple, list)):
        raise ValueError(f"{field_name} must be a list")
    clean: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"{field_name} must contain metadata objects")
        clean.append(_metadata_summary(item))
    return clean


def _metadata_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("metadata summaries must be objects")
    forbidden = FORBIDDEN_ATTACHMENT_FIELDS.union(FORBIDDEN_PERMISSION_FIELDS)
    clean: dict[str, Any] = {}
    for key, item in value.items():
        normalized = str(key).lower()
        if normalized in forbidden:
            raise ValueError(f"metadata summary contains secret-like field: {key}")
        if isinstance(item, (str, int, float, bool)) or item is None:
            if isinstance(item, str):
                _raise_if_secret_like_value(item, str(key))
            clean[str(key)] = item
        elif isinstance(item, list):
            clean[str(key)] = [
                _metadata_list_value(entry, str(key))
                for entry in item
                if isinstance(entry, (str, int, float, bool)) or entry is None
            ]
        else:
            rendered = str(item)
            _raise_if_secret_like_value(rendered, str(key))
            clean[str(key)] = rendered
    return clean


def _metadata_list_value(value: str | int | float | bool | None, field_name: str) -> str | int | float | bool | None:
    if isinstance(value, str):
        _raise_if_secret_like_value(value, field_name)
    return value


def _raise_if_secret_like_value(value: str, field_name: str) -> None:
    normalized = value.lower()
    for marker in FORBIDDEN_ROUTE_RESULT_VALUE_MARKERS:
        if marker in normalized:
            raise ValueError(f"{field_name} contains secret-like or raw metadata")


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
