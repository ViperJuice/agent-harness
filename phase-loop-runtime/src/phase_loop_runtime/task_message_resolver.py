"""Authenticated, read-only resolution of exact Codex task-message bytes."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlsplit

import rfc8785


FAILURE_CODES = frozenset(
    {
        "source_task_unavailable",
        "source_message_unavailable",
        "source_identity_mismatch",
        "source_bytes_unavailable",
        "approval_body_unavailable",
        "attestation_invalid",
        "source_stale",
    }
)


class TaskMessageResolverError(LookupError):
    """Fail-closed resolver result carrying only governed metadata."""

    def __init__(self, code: str, *, authority: str, thread_id: str | None = None, message_id: str | None = None) -> None:
        if code not in FAILURE_CODES:
            raise ValueError(f"unknown task-message resolver failure code: {code}")
        super().__init__(code)
        self.code = code
        self.authority = authority
        self.thread_id = thread_id
        self.message_id = message_id

    def metadata(self) -> dict[str, object]:
        return {
            "status": "blocked",
            "code": self.code,
            "authority": self.authority,
            "thread_id": self.thread_id,
            "message_id": self.message_id,
        }


@dataclass(frozen=True)
class TaskMessageProof:
    authority: str
    thread_id: str
    turn_id: str
    message_id: str
    message_bytes: bytes
    approval_body_bytes: bytes
    source_started_at: int
    resolved_at: int

    @property
    def message_sha256(self) -> str:
        return hashlib.sha256(self.message_bytes).hexdigest()

    @property
    def approval_body_sha256(self) -> str:
        return hashlib.sha256(self.approval_body_bytes).hexdigest()

    @property
    def approval_canonical_sha256(self) -> str:
        return hashlib.sha256(rfc8785.dumps(json.loads(self.approval_body_bytes))).hexdigest()

    def metadata(self) -> dict[str, object]:
        return {
            "status": "resolved",
            "authority": self.authority,
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
            "message_id": self.message_id,
            "message_sha256": self.message_sha256,
            "approval_body_sha256": self.approval_body_sha256,
            "approval_canonical_sha256": self.approval_canonical_sha256,
            "source_started_at": self.source_started_at,
            "resolved_at": self.resolved_at,
        }

    def payload(self) -> dict[str, object]:
        return {
            **self.metadata(),
            "message_bytes_b64": base64.b64encode(self.message_bytes).decode("ascii"),
            "approval_body_bytes_b64": base64.b64encode(self.approval_body_bytes).decode("ascii"),
        }


class JsonRpcConnection(Protocol):
    def request(self, method: str, params: Mapping[str, object]) -> Mapping[str, Any]: ...

    def notify(self, method: str, params: Mapping[str, object]) -> None: ...

    def close(self) -> None: ...


class _WebSocketJsonRpcConnection:
    def __init__(self, endpoint: str, bearer_token: str, timeout_seconds: float) -> None:
        try:
            from websockets.sync.client import connect

            self._socket = connect(
                endpoint,
                additional_headers={"Authorization": f"Bearer {bearer_token}"},
                open_timeout=timeout_seconds,
                close_timeout=timeout_seconds,
            )
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            code = "attestation_invalid" if status in {401, 403} else "source_task_unavailable"
            raise _ConnectionFailure(code) from exc
        self._timeout_seconds = timeout_seconds
        self._next_id = 1

    def request(self, method: str, params: Mapping[str, object]) -> Mapping[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self._socket.send(json.dumps({"id": request_id, "method": method, "params": dict(params)}, separators=(",", ":")))
        while True:
            try:
                raw_message = self._socket.recv(timeout=self._timeout_seconds)
            except TimeoutError as exc:
                raise _ConnectionFailure("source_task_unavailable") from exc
            try:
                message = json.loads(raw_message)
            except (TypeError, json.JSONDecodeError) as exc:
                raise _JsonRpcFailure({"code": -32700}) from exc
            if not isinstance(message, dict) or message.get("id") != request_id:
                continue
            if "error" in message:
                raise _JsonRpcFailure(message["error"])
            result = message.get("result")
            if not isinstance(result, dict):
                raise _JsonRpcFailure({"code": -32603})
            return result

    def notify(self, method: str, params: Mapping[str, object]) -> None:
        self._socket.send(json.dumps({"method": method, "params": dict(params)}, separators=(",", ":")))

    def close(self) -> None:
        self._socket.close()


class _ConnectionFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _JsonRpcFailure(RuntimeError):
    pass


ConnectionFactory = Callable[[str, str, float], JsonRpcConnection]


class CodexAppServerTaskMessageResolver:
    """Resolve one exact user-message item from an authenticated app-server."""

    def __init__(
        self,
        *,
        endpoint: str,
        bearer_token: str,
        authority: str,
        max_source_age_seconds: int = 900,
        timeout_seconds: float = 10.0,
        clock: Callable[[], float] = time.time,
        connection_factory: ConnectionFactory = _WebSocketJsonRpcConnection,
    ) -> None:
        if not endpoint.startswith(("ws://", "wss://")):
            raise ValueError("task-message endpoint must use ws:// or wss://")
        if not bearer_token:
            raise ValueError("task-message bearer token is required")
        endpoint_host = urlsplit(endpoint).hostname
        if not endpoint_host or authority != f"codex-app-server://{endpoint_host}":
            raise ValueError("task-message authority must exactly bind the endpoint hostname")
        if max_source_age_seconds <= 0 or timeout_seconds <= 0:
            raise ValueError("resolver age and timeout bounds must be positive")
        self._endpoint = endpoint
        self._bearer_token = bearer_token
        self._authority = authority
        self._max_source_age_seconds = max_source_age_seconds
        self._timeout_seconds = timeout_seconds
        self._clock = clock
        self._connection_factory = connection_factory

    def _connect(self, *, thread_id: str | None = None, message_id: str | None = None) -> JsonRpcConnection:
        connection: JsonRpcConnection | None = None
        try:
            connection = self._connection_factory(self._endpoint, self._bearer_token, self._timeout_seconds)
            connection.request(
                "initialize",
                {
                    "clientInfo": {"name": "phase-loop-task-message-resolver", "version": "1"},
                    "capabilities": {"experimentalApi": False},
                },
            )
            connection.notify("initialized", {})
            return connection
        except _ConnectionFailure as exc:
            raise TaskMessageResolverError(
                exc.code,
                authority=self._authority,
                thread_id=thread_id,
                message_id=message_id,
            ) from exc
        except Exception as exc:
            if connection is not None:
                connection.close()
            raise TaskMessageResolverError(
                "attestation_invalid" if connection is not None else "source_task_unavailable",
                authority=self._authority,
                thread_id=thread_id,
                message_id=message_id,
            ) from exc

    def probe(self) -> dict[str, object]:
        connection = self._connect()
        connection.close()
        return {"status": "ready", "authority": self._authority}

    def resolve(self, *, thread_id: str, message_id: str) -> TaskMessageProof:
        if not thread_id or not message_id:
            raise TaskMessageResolverError(
                "source_identity_mismatch",
                authority=self._authority,
                thread_id=thread_id or None,
                message_id=message_id or None,
            )
        connection = self._connect(thread_id=thread_id, message_id=message_id)
        try:
            try:
                result = connection.request("thread/read", {"threadId": thread_id, "includeTurns": True})
            except _JsonRpcFailure as exc:
                raise TaskMessageResolverError(
                    "source_task_unavailable",
                    authority=self._authority,
                    thread_id=thread_id,
                    message_id=message_id,
                ) from exc
            except _ConnectionFailure as exc:
                raise TaskMessageResolverError(
                    exc.code,
                    authority=self._authority,
                    thread_id=thread_id,
                    message_id=message_id,
                ) from exc
            except Exception as exc:
                raise TaskMessageResolverError(
                    "source_task_unavailable",
                    authority=self._authority,
                    thread_id=thread_id,
                    message_id=message_id,
                ) from exc
        finally:
            connection.close()
        thread = result.get("thread")
        if not isinstance(thread, dict):
            raise self._error("source_task_unavailable", thread_id, message_id)
        if thread.get("id") != thread_id:
            raise self._error("source_identity_mismatch", thread_id, message_id)
        turns = thread.get("turns")
        if not isinstance(turns, list):
            raise self._error("source_message_unavailable", thread_id, message_id)

        matches: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
        for turn in turns:
            if not isinstance(turn, dict) or not isinstance(turn.get("items"), list):
                continue
            for item in turn["items"]:
                if isinstance(item, dict) and item.get("id") == message_id:
                    matches.append((turn, item))
        if not matches:
            raise self._error("source_message_unavailable", thread_id, message_id)
        if len(matches) != 1:
            raise self._error("source_identity_mismatch", thread_id, message_id)
        turn, item = matches[0]
        if item.get("type") != "userMessage" or not isinstance(turn.get("id"), str):
            raise self._error("source_identity_mismatch", thread_id, message_id)
        content = item.get("content")
        if not isinstance(content, list) or len(content) != 2:
            raise self._error("source_bytes_unavailable", thread_id, message_id)
        source_item, approval_item = content
        if any(
            not isinstance(text_item, dict)
            or text_item.get("type") != "text"
            or not isinstance(text_item.get("text"), str)
            for text_item in (source_item, approval_item)
        ):
            raise self._error("source_bytes_unavailable", thread_id, message_id)
        try:
            message_bytes = source_item["text"].encode("utf-8", errors="strict")
            approval_body_bytes = approval_item["text"].encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise self._error("source_bytes_unavailable", thread_id, message_id) from exc
        try:
            approval = json.loads(approval_body_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise self._error("approval_body_unavailable", thread_id, message_id) from exc
        if not isinstance(approval, dict):
            raise self._error("approval_body_unavailable", thread_id, message_id)
        try:
            rfc8785.dumps(approval)
        except (TypeError, ValueError) as exc:
            raise self._error("approval_body_unavailable", thread_id, message_id) from exc
        required_claims = {
            "contract_version",
            "authorized",
            "source_thread_id",
            "source_message_id",
            "source_message_sha256",
        }
        if not required_claims.issubset(approval) or approval.get("authorized") is not True:
            raise self._error("approval_body_unavailable", thread_id, message_id)
        if approval.get("source_thread_id") != thread_id or approval.get("source_message_id") != message_id:
            raise self._error("source_identity_mismatch", thread_id, message_id)
        if approval.get("source_message_sha256") != hashlib.sha256(message_bytes).hexdigest():
            raise self._error("attestation_invalid", thread_id, message_id)

        started_at = turn.get("startedAt")
        resolved_at = int(self._clock())
        if not isinstance(started_at, int) or started_at > resolved_at + 30 or resolved_at - started_at > self._max_source_age_seconds:
            raise self._error("source_stale", thread_id, message_id)
        return TaskMessageProof(
            authority=self._authority,
            thread_id=thread_id,
            turn_id=turn["id"],
            message_id=message_id,
            message_bytes=message_bytes,
            approval_body_bytes=approval_body_bytes,
            source_started_at=started_at,
            resolved_at=resolved_at,
        )

    def _error(self, code: str, thread_id: str, message_id: str) -> TaskMessageResolverError:
        return TaskMessageResolverError(
            code,
            authority=self._authority,
            thread_id=thread_id,
            message_id=message_id,
        )
