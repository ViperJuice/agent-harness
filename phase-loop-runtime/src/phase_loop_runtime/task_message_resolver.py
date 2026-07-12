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
APPROVAL_CLIENT_ID_SUFFIX = "-approval"
TOP_LEVEL_SOURCE_CONTRACT_VERSIONS = frozenset({
    "embedding_provenance_deploy_approval.v2",
    "embedding_provenance_bootstrap_approval.v3",
})
NESTED_SOURCE_CONTRACT_VERSIONS = frozenset({
    "gpu0_unit_install_approval.v1",
    "gpu0_prov_fence_approval.v1",
    "gpu0_fence_fm_ack.v1",
})
APPROVAL_CONTRACT_VERSIONS = TOP_LEVEL_SOURCE_CONTRACT_VERSIONS | NESTED_SOURCE_CONTRACT_VERSIONS
SOURCE_IDENTITY_KEYS = frozenset({
    "source_thread_id",
    "source_message_id",
    "source_message_sha256",
})
NESTED_SOURCE_IDENTITY_KEYS = SOURCE_IDENTITY_KEYS | {"approval_message_id"}


def _decode_strict_json(value: bytes) -> Any:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        decoded: dict[str, Any] = {}
        for key, item in pairs:
            if key in decoded:
                raise ValueError("duplicate JSON member")
            decoded[key] = item
        return decoded

    def reject_constant(_value: str) -> Any:
        raise ValueError("non-finite JSON number")

    return json.loads(
        value,
        object_pairs_hook=object_pairs,
        parse_constant=reject_constant,
    )


def approval_source_identity(approval: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Select the exact source-identity location for a governed contract."""
    contract_version = approval.get("contract_version")
    if contract_version in TOP_LEVEL_SOURCE_CONTRACT_VERSIONS:
        if "source" in approval or not SOURCE_IDENTITY_KEYS.issubset(approval):
            return None
        return approval
    if contract_version not in NESTED_SOURCE_CONTRACT_VERSIONS:
        return None
    if any(key in approval for key in NESTED_SOURCE_IDENTITY_KEYS):
        return None
    source = approval.get("source")
    if not isinstance(source, dict) or set(source) != NESTED_SOURCE_IDENTITY_KEYS:
        return None
    return source


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
    approval_turn_id: str
    message_id: str
    approval_message_id: str
    source_item_id: str
    approval_item_id: str
    source_turn_index: int
    source_item_index: int
    approval_turn_index: int
    approval_item_index: int
    message_bytes: bytes
    approval_body_bytes: bytes
    source_started_at: int
    approval_started_at: int
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
            "approval_turn_id": self.approval_turn_id,
            "message_id": self.message_id,
            "approval_message_id": self.approval_message_id,
            "source_item_id": self.source_item_id,
            "approval_item_id": self.approval_item_id,
            "source_turn_index": self.source_turn_index,
            "source_item_index": self.source_item_index,
            "approval_turn_index": self.approval_turn_index,
            "approval_item_index": self.approval_item_index,
            "message_sha256": self.message_sha256,
            "approval_body_sha256": self.approval_body_sha256,
            "approval_canonical_sha256": self.approval_canonical_sha256,
            "source_started_at": self.source_started_at,
            "approval_started_at": self.approval_started_at,
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


class _ControlSocketJsonRpcConnection:
    def __init__(self, control_socket: str, _unused_token: str, timeout_seconds: float) -> None:
        try:
            from websockets.sync.client import unix_connect

            self._socket = unix_connect(
                path=control_socket,
                uri="ws://localhost",
                compression=None,
                max_size=None,
                open_timeout=timeout_seconds,
                close_timeout=timeout_seconds,
            )
        except Exception as exc:
            raise _ConnectionFailure("source_task_unavailable") from exc
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
    """Resolve one exact two-message approval envelope from an authenticated app-server."""

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        bearer_token: str | None = None,
        control_socket: str | None = None,
        authority: str,
        max_source_age_seconds: int = 900,
        timeout_seconds: float = 10.0,
        clock: Callable[[], float] = time.time,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        if (endpoint is None) == (control_socket is None):
            raise ValueError("exactly one task-message transport is required")
        if endpoint is not None:
            if not endpoint.startswith(("ws://", "wss://")):
                raise ValueError("task-message endpoint must use ws:// or wss://")
            if not bearer_token:
                raise ValueError("task-message bearer token is required")
            endpoint_host = urlsplit(endpoint).hostname
            if not endpoint_host or authority != f"codex-app-server://{endpoint_host}":
                raise ValueError("task-message authority must exactly bind the endpoint hostname")
            connection_target = endpoint
            connection_credential = bearer_token
            selected_factory = connection_factory or _WebSocketJsonRpcConnection
        else:
            if not control_socket or not control_socket.startswith("/"):
                raise ValueError("task-message control socket must be absolute")
            if not authority.startswith("codex-app-server://"):
                raise ValueError("task-message control-socket authority is invalid")
            connection_target = control_socket
            connection_credential = ""
            selected_factory = connection_factory or _ControlSocketJsonRpcConnection
        if max_source_age_seconds <= 0 or timeout_seconds <= 0:
            raise ValueError("resolver age and timeout bounds must be positive")
        self._connection_target = connection_target
        self._connection_credential = connection_credential
        self._authority = authority
        self._max_source_age_seconds = max_source_age_seconds
        self._timeout_seconds = timeout_seconds
        self._clock = clock
        self._connection_factory = selected_factory

    def _connect(self, *, thread_id: str | None = None, message_id: str | None = None) -> JsonRpcConnection:
        connection: JsonRpcConnection | None = None
        try:
            connection = self._connection_factory(
                self._connection_target,
                self._connection_credential,
                self._timeout_seconds,
            )
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
            if connection is not None:
                connection.close()
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

        approval_message_id = f"{message_id}{APPROVAL_CLIENT_ID_SUFFIX}"
        source_matches: list[tuple[int, int, Mapping[str, Any], Mapping[str, Any]]] = []
        approval_matches: list[tuple[int, int, Mapping[str, Any], Mapping[str, Any]]] = []
        for turn_index, turn in enumerate(turns):
            if not isinstance(turn, dict) or not isinstance(turn.get("items"), list):
                continue
            for item_index, item in enumerate(turn["items"]):
                if not isinstance(item, dict) or item.get("type") != "userMessage":
                    continue
                match = (turn_index, item_index, turn, item)
                if item.get("clientId") == message_id:
                    source_matches.append(match)
                elif item.get("clientId") == approval_message_id:
                    approval_matches.append(match)
        if not source_matches:
            raise self._error("source_message_unavailable", thread_id, message_id)
        if len(source_matches) != 1:
            raise self._error("source_identity_mismatch", thread_id, message_id)
        if not approval_matches:
            raise self._error("approval_body_unavailable", thread_id, message_id)
        if len(approval_matches) != 1:
            raise self._error("source_identity_mismatch", thread_id, message_id)

        source_turn_index, source_item_index, source_turn, source_item = source_matches[0]
        approval_turn_index, approval_item_index, approval_turn, approval_item = approval_matches[0]
        if (
            (approval_turn_index, approval_item_index) <= (source_turn_index, source_item_index)
            or not isinstance(source_turn.get("id"), str)
            or not isinstance(approval_turn.get("id"), str)
            or not isinstance(source_item.get("id"), str)
            or not isinstance(approval_item.get("id"), str)
            or not source_item["id"]
            or not approval_item["id"]
            or source_item["id"] == approval_item["id"]
        ):
            raise self._error("source_identity_mismatch", thread_id, message_id)

        source_content = source_item.get("content")
        if not isinstance(source_content, list) or len(source_content) != 1:
            raise self._error("source_bytes_unavailable", thread_id, message_id)
        approval_content = approval_item.get("content")
        if not isinstance(approval_content, list) or len(approval_content) != 1:
            raise self._error("approval_body_unavailable", thread_id, message_id)
        source_text = source_content[0]
        approval_text = approval_content[0]
        if (
            not isinstance(source_text, dict)
            or source_text.get("type") != "text"
            or not isinstance(source_text.get("text"), str)
        ):
            raise self._error("source_bytes_unavailable", thread_id, message_id)
        if (
            not isinstance(approval_text, dict)
            or approval_text.get("type") != "text"
            or not isinstance(approval_text.get("text"), str)
        ):
            raise self._error("approval_body_unavailable", thread_id, message_id)
        try:
            message_bytes = source_text["text"].encode("utf-8", errors="strict")
            approval_body_bytes = approval_text["text"].encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise self._error("source_bytes_unavailable", thread_id, message_id) from exc
        try:
            approval = _decode_strict_json(approval_body_bytes)
        except (UnicodeDecodeError, ValueError) as exc:
            raise self._error("approval_body_unavailable", thread_id, message_id) from exc
        if not isinstance(approval, dict):
            raise self._error("approval_body_unavailable", thread_id, message_id)
        try:
            rfc8785.dumps(approval)
        except (TypeError, ValueError) as exc:
            raise self._error("approval_body_unavailable", thread_id, message_id) from exc
        if (
            not isinstance(approval.get("contract_version"), str)
            or approval.get("contract_version") not in APPROVAL_CONTRACT_VERSIONS
            or approval.get("authorized") is not True
        ):
            raise self._error("approval_body_unavailable", thread_id, message_id)
        source_identity = approval_source_identity(approval)
        if source_identity is None:
            raise self._error("approval_body_unavailable", thread_id, message_id)
        if (
            source_identity.get("source_thread_id") != thread_id
            or source_identity.get("source_message_id") != message_id
            or (
                "approval_message_id" in source_identity
                and source_identity.get("approval_message_id") != approval_message_id
            )
        ):
            raise self._error("source_identity_mismatch", thread_id, message_id)
        if source_identity.get("source_message_sha256") != hashlib.sha256(message_bytes).hexdigest():
            raise self._error("attestation_invalid", thread_id, message_id)

        source_started_at = source_turn.get("startedAt")
        approval_started_at = approval_turn.get("startedAt")
        resolved_at = int(self._clock())
        if (
            not isinstance(source_started_at, int)
            or not isinstance(approval_started_at, int)
            or approval_started_at < source_started_at
            or source_started_at > resolved_at + 30
            or approval_started_at > resolved_at + 30
            or resolved_at - source_started_at > self._max_source_age_seconds
            or resolved_at - approval_started_at > self._max_source_age_seconds
        ):
            raise self._error("source_stale", thread_id, message_id)
        return TaskMessageProof(
            authority=self._authority,
            thread_id=thread_id,
            turn_id=source_turn["id"],
            approval_turn_id=approval_turn["id"],
            message_id=message_id,
            approval_message_id=approval_message_id,
            source_item_id=source_item["id"],
            approval_item_id=approval_item["id"],
            source_turn_index=source_turn_index,
            source_item_index=source_item_index,
            approval_turn_index=approval_turn_index,
            approval_item_index=approval_item_index,
            message_bytes=message_bytes,
            approval_body_bytes=approval_body_bytes,
            source_started_at=source_started_at,
            approval_started_at=approval_started_at,
            resolved_at=resolved_at,
        )

    def _error(self, code: str, thread_id: str, message_id: str) -> TaskMessageResolverError:
        return TaskMessageResolverError(
            code,
            authority=self._authority,
            thread_id=thread_id,
            message_id=message_id,
        )
