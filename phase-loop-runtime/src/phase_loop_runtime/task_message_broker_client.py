"""Strict streaming client for the task-message source broker."""

from __future__ import annotations

import base64
import hashlib
import json
import queue
import re
import threading
from typing import BinaryIO, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import rfc8785

from .task_message_broker import decode_strict_json
from .task_message_resolver import APPROVAL_CONTRACT_VERSION, FAILURE_CODES, TaskMessageResolverError


COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
MAX_FRAME_BYTES = 1_048_576
OpenFn = Callable[..., BinaryIO]
READY_KEYS = {"status", "authority"}
BLOCKED_KEYS = {"status", "code", "authority", "thread_id", "message_id"}
RESOLVED_KEYS = {
    "status", "authority", "thread_id", "turn_id", "approval_turn_id", "message_id",
    "approval_message_id", "source_item_id", "approval_item_id", "message_sha256",
    "source_turn_index", "source_item_index", "approval_turn_index", "approval_item_index",
    "approval_body_sha256", "approval_canonical_sha256", "source_started_at", "approval_started_at", "resolved_at",
    "message_bytes_b64", "approval_body_bytes_b64",
}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        return None


_NO_REDIRECT_OPENER = build_opener(_NoRedirect)


def _open_without_redirects(request: Request, *, timeout: float) -> BinaryIO:
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


class TaskMessageBrokerClient:
    def __init__(
        self,
        *,
        broker_url: str,
        bearer_token: str,
        authority: str,
        heartbeat_timeout_seconds: float = 15.0,
        opener: OpenFn = _open_without_redirects,
    ) -> None:
        parsed = urlsplit(broker_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("invalid task-message broker URL")
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("remote task-message broker must use HTTPS")
        if not bearer_token or not authority.startswith("codex-app-server://") or heartbeat_timeout_seconds <= 0:
            raise ValueError("invalid task-message broker configuration")
        self._base_url = broker_url.rstrip("/")
        self._token = bearer_token
        self._authority = authority
        self._heartbeat_timeout_seconds = heartbeat_timeout_seconds
        self._opener = opener

    def probe(self) -> dict[str, object]:
        return self._request("/v1/task-message/probe", {})

    def resolve(self, *, thread_id: str, message_id: str, max_source_age_seconds: int) -> dict[str, object]:
        return self._request(
            "/v1/task-message/resolve",
            {
                "thread_id": thread_id,
                "message_id": message_id,
                "max_source_age_seconds": max_source_age_seconds,
            },
            thread_id=thread_id,
            message_id=message_id,
            max_source_age_seconds=max_source_age_seconds,
        )

    def _request(
        self,
        path: str,
        payload: dict[str, object],
        *,
        thread_id: str | None = None,
        message_id: str | None = None,
        max_source_age_seconds: int | None = None,
    ) -> dict[str, object]:
        request = Request(
            f"{self._base_url}{path}",
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "application/x-ndjson",
            },
        )
        try:
            response = self._opener(request, timeout=self._heartbeat_timeout_seconds)
            with response:
                content_type = response.headers.get_content_type()
                if content_type != "application/x-ndjson":
                    raise ValueError
                sequence = 0
                while True:
                    raw = self._readline_with_deadline(response)
                    if not raw or len(raw) > MAX_FRAME_BYTES:
                        raise ValueError
                    frame = decode_strict_json(raw)
                    if not isinstance(frame, dict):
                        raise ValueError
                    if frame.get("type") == "heartbeat":
                        if set(frame) != {"type", "sequence"} or type(frame["sequence"]) is not int or frame["sequence"] != sequence + 1:
                            raise ValueError
                        sequence = frame["sequence"]
                        continue
                    if set(frame) != {"type", "agent_harness_sha", "payload"} or frame.get("type") != "result":
                        raise ValueError
                    sha = frame["agent_harness_sha"]
                    result = frame["payload"]
                    if not isinstance(sha, str) or COMMIT_SHA.fullmatch(sha) is None or not isinstance(result, dict):
                        raise ValueError
                    if not self._valid_payload(
                        result,
                        path=path,
                        thread_id=thread_id,
                        message_id=message_id,
                        max_source_age_seconds=max_source_age_seconds,
                    ):
                        raise ValueError
                    if self._readline_with_deadline(response) != b"":
                        raise ValueError
                    if result.get("status") == "blocked":
                        raise TaskMessageResolverError(
                            result["code"],
                            authority=self._authority,
                            thread_id=thread_id,
                            message_id=message_id,
                        )
                    return {**result, "agent_harness_sha": sha}
        except HTTPError as exc:
            code = "attestation_invalid" if exc.code in {401, 403} or 300 <= exc.code < 400 else "source_task_unavailable"
            raise TaskMessageResolverError(code, authority=self._authority, thread_id=thread_id, message_id=message_id) from exc
        except (TimeoutError, URLError, OSError):
            raise TaskMessageResolverError(
                "source_task_unavailable",
                authority=self._authority,
                thread_id=thread_id,
                message_id=message_id,
            ) from None
        except (TypeError, ValueError, json.JSONDecodeError):
            raise TaskMessageResolverError(
                "attestation_invalid",
                authority=self._authority,
                thread_id=thread_id,
                message_id=message_id,
            ) from None

    def _readline_with_deadline(self, response: BinaryIO) -> bytes:
        completed: queue.Queue[bytes | BaseException] = queue.Queue(maxsize=1)

        def read() -> None:
            try:
                completed.put(response.readline(MAX_FRAME_BYTES + 1))
            except BaseException as exc:
                completed.put(exc)

        threading.Thread(target=read, daemon=True).start()
        try:
            result = completed.get(timeout=self._heartbeat_timeout_seconds)
        except queue.Empty as exc:
            raise TimeoutError from exc
        if isinstance(result, BaseException):
            raise result
        return result

    def _valid_payload(
        self,
        payload: dict[str, object],
        *,
        path: str,
        thread_id: str | None,
        message_id: str | None,
        max_source_age_seconds: int | None,
    ) -> bool:
        if payload.get("authority") != self._authority:
            return False
        if payload.get("status") == "blocked":
            return (
                set(payload) == BLOCKED_KEYS
                and payload.get("code") in FAILURE_CODES
                and isinstance(payload.get("authority"), str)
                and payload.get("thread_id") == thread_id
                and payload.get("message_id") == message_id
            )
        if path.endswith("/probe"):
            return set(payload) == READY_KEYS and payload.get("status") == "ready" and isinstance(payload.get("authority"), str)
        if not (
            set(payload) == RESOLVED_KEYS
            and payload.get("status") == "resolved"
            and payload.get("thread_id") == thread_id
            and payload.get("message_id") == message_id
        ):
            return False
        identity_keys = {
            "thread_id", "turn_id", "approval_turn_id", "message_id",
            "approval_message_id", "source_item_id", "approval_item_id",
        }
        if any(not isinstance(payload.get(key), str) or SAFE_ID.fullmatch(payload[key]) is None for key in identity_keys):
            return False
        if payload.get("approval_message_id") != f"{message_id}-approval":
            return False
        if (
            payload.get("source_item_id") == payload.get("approval_item_id")
            or payload.get("turn_id") == payload.get("approval_turn_id")
        ):
            return False
        digest_keys = {"message_sha256", "approval_body_sha256", "approval_canonical_sha256"}
        if any(not isinstance(payload.get(key), str) or SHA256.fullmatch(payload[key]) is None for key in digest_keys):
            return False
        source_started_at = payload.get("source_started_at")
        approval_started_at = payload.get("approval_started_at")
        resolved_at = payload.get("resolved_at")
        source_position = (payload.get("source_turn_index"), payload.get("source_item_index"))
        approval_position = (payload.get("approval_turn_index"), payload.get("approval_item_index"))
        if (
            type(source_started_at) is not int
            or type(approval_started_at) is not int
            or type(resolved_at) is not int
            or type(max_source_age_seconds) is not int
            or any(type(index) is not int or index < 0 for index in (*source_position, *approval_position))
            or approval_position <= source_position
            or source_started_at <= 0
            or approval_started_at < source_started_at
            or resolved_at < approval_started_at
            or resolved_at - source_started_at > max_source_age_seconds
            or resolved_at - approval_started_at > max_source_age_seconds
        ):
            return False
        try:
            message_bytes = _decode_canonical_base64(payload.get("message_bytes_b64"))
            approval_body_bytes = _decode_canonical_base64(payload.get("approval_body_bytes_b64"))
            approval_document = decode_strict_json(approval_body_bytes)
            canonical = rfc8785.dumps(approval_document)
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        return (
            isinstance(approval_document, dict)
            and approval_document.get("contract_version") == APPROVAL_CONTRACT_VERSION
            and approval_document.get("authorized") is True
            and approval_document.get("source_thread_id") == thread_id
            and approval_document.get("source_message_id") == message_id
            and approval_document.get("source_message_sha256") == hashlib.sha256(message_bytes).hexdigest()
            and hashlib.sha256(message_bytes).hexdigest() == payload["message_sha256"]
            and hashlib.sha256(approval_body_bytes).hexdigest() == payload["approval_body_sha256"]
            and hashlib.sha256(canonical).hexdigest() == payload["approval_canonical_sha256"]
        )


def _decode_canonical_base64(value: object) -> bytes:
    if not isinstance(value, str):
        raise ValueError("base64 value must be text")
    decoded = base64.b64decode(value, validate=True)
    if base64.b64encode(decoded).decode("ascii") != value:
        raise ValueError("base64 value is not canonical")
    return decoded
