"""Strict streaming client for the task-message source broker."""

from __future__ import annotations

import json
import re
from typing import BinaryIO, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .task_message_resolver import TaskMessageResolverError


COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
MAX_FRAME_BYTES = 1_048_576
OpenFn = Callable[..., BinaryIO]


class TaskMessageBrokerClient:
    def __init__(
        self,
        *,
        broker_url: str,
        bearer_token: str,
        authority: str,
        heartbeat_timeout_seconds: float = 15.0,
        opener: OpenFn = urlopen,
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
        )

    def _request(
        self,
        path: str,
        payload: dict[str, object],
        *,
        thread_id: str | None = None,
        message_id: str | None = None,
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
                    raw = response.readline(MAX_FRAME_BYTES + 1)
                    if not raw or len(raw) > MAX_FRAME_BYTES:
                        raise ValueError
                    frame = json.loads(raw)
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
                    return {**result, "agent_harness_sha": sha}
        except HTTPError as exc:
            code = "attestation_invalid" if exc.code in {401, 403} else "source_task_unavailable"
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
