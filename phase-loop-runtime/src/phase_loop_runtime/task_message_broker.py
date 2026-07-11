"""Loopback-only authenticated broker for exact task-message proofs."""

from __future__ import annotations

import hashlib
import hmac
import json
import queue
import re
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import urlsplit

from .task_message_resolver import CodexAppServerTaskMessageResolver, TaskMessageResolverError


PROBE_PATH = "/v1/task-message/probe"
RESOLVE_PATH = "/v1/task-message/resolve"
MAX_REQUEST_BYTES = 16_384
MAX_RESPONSE_BYTES = 1_048_576
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
SHA256 = re.compile(r"[0-9a-f]{64}")
COMMIT_SHA = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class BrokerConfig:
    authority: str
    token_sha256: str
    agent_harness_sha: str
    heartbeat_seconds: float = 5.0
    max_source_age_seconds: int = 900

    def __post_init__(self) -> None:
        if not self.authority.startswith("codex-app-server://"):
            raise ValueError("invalid broker authority")
        if SHA256.fullmatch(self.token_sha256) is None:
            raise ValueError("invalid broker token digest")
        if COMMIT_SHA.fullmatch(self.agent_harness_sha) is None:
            raise ValueError("invalid Agent Harness SHA")
        if self.heartbeat_seconds <= 0 or not 0 < self.max_source_age_seconds <= 900:
            raise ValueError("invalid broker timing configuration")


ResolverFactory = Callable[[int], CodexAppServerTaskMessageResolver]


class TaskMessageBroker:
    def __init__(self, config: BrokerConfig, resolver_factory: ResolverFactory) -> None:
        self.config = config
        self._resolver_factory = resolver_factory
        self._single_flight = threading.Lock()

    def authenticated(self, authorization: str | None) -> bool:
        if not authorization or not authorization.startswith("Bearer "):
            return False
        token = authorization.removeprefix("Bearer ")
        candidate = hashlib.sha256(token.encode("utf-8", errors="strict")).hexdigest()
        return hmac.compare_digest(candidate, self.config.token_sha256)

    def probe(self) -> dict[str, object]:
        return self._resolver_factory(self.config.max_source_age_seconds).probe()

    def resolve(self, *, thread_id: str, message_id: str, max_source_age_seconds: int) -> dict[str, object]:
        return self._resolver_factory(max_source_age_seconds).resolve(
            thread_id=thread_id,
            message_id=message_id,
        ).payload()

    def acquire(self) -> bool:
        return self._single_flight.acquire(blocking=False)

    def release(self) -> None:
        self._single_flight.release()


class LoopbackThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True


def _is_loopback(host: str) -> bool:
    return host in {"127.0.0.1", "::1", "localhost"}


def make_handler(broker: TaskMessageBroker) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "PhaseLoopTaskMessageBroker/1"

        def do_POST(self) -> None:  # noqa: N802
            if not broker.authenticated(self.headers.get("Authorization")):
                self._write_json({"status": "blocked", "code": "attestation_invalid"}, HTTPStatus.UNAUTHORIZED)
                return
            path = urlsplit(self.path).path
            if path not in {PROBE_PATH, RESOLVE_PATH}:
                self._write_json({"status": "blocked", "code": "source_task_unavailable"}, HTTPStatus.NOT_FOUND)
                return
            try:
                payload = self._read_payload()
                operation = self._operation(path, payload)
            except ValueError:
                self._write_json({"status": "blocked", "code": "attestation_invalid"}, HTTPStatus.BAD_REQUEST)
                return
            if not broker.acquire():
                self._write_json({"status": "blocked", "code": "source_task_unavailable"}, HTTPStatus.SERVICE_UNAVAILABLE)
                return
            self._stream(operation)

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _read_payload(self) -> dict[str, object]:
            raw_length = self.headers.get("Content-Length", "")
            if not raw_length.isdigit() or not 0 <= int(raw_length) <= MAX_REQUEST_BYTES:
                raise ValueError
            raw = self.rfile.read(int(raw_length))
            value = json.loads(raw or b"{}")
            if not isinstance(value, dict):
                raise ValueError
            return value

        def _operation(self, path: str, payload: dict[str, object]) -> Callable[[], dict[str, object]]:
            if path == PROBE_PATH:
                if payload:
                    raise ValueError
                return broker.probe
            if set(payload) != {"thread_id", "message_id", "max_source_age_seconds"}:
                raise ValueError
            thread_id = payload["thread_id"]
            message_id = payload["message_id"]
            max_age = payload["max_source_age_seconds"]
            if (
                not isinstance(thread_id, str)
                or SAFE_ID.fullmatch(thread_id) is None
                or not isinstance(message_id, str)
                or SAFE_ID.fullmatch(message_id) is None
                or type(max_age) is not int
                or not 0 < max_age <= broker.config.max_source_age_seconds
            ):
                raise ValueError
            return lambda: broker.resolve(
                thread_id=thread_id,
                message_id=message_id,
                max_source_age_seconds=max_age,
            )

        def _stream(self, operation: Callable[[], dict[str, object]]) -> None:
            outcome: queue.Queue[dict[str, object]] = queue.Queue(maxsize=1)

            def run() -> None:
                try:
                    payload = operation()
                except TaskMessageResolverError as exc:
                    payload = exc.metadata()
                except Exception:
                    payload = {
                        "status": "blocked",
                        "code": "source_task_unavailable",
                        "authority": broker.config.authority,
                        "thread_id": None,
                        "message_id": None,
                    }
                outcome.put(payload)

            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            sequence = 0
            try:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                while True:
                    try:
                        payload = outcome.get(timeout=broker.config.heartbeat_seconds)
                        frame = {
                            "type": "result",
                            "agent_harness_sha": broker.config.agent_harness_sha,
                            "payload": payload,
                        }
                        encoded = _encode_frame(frame)
                        if len(encoded) > MAX_RESPONSE_BYTES:
                            encoded = _encode_frame(
                                {
                                    "type": "result",
                                    "agent_harness_sha": broker.config.agent_harness_sha,
                                    "payload": {
                                        "status": "blocked",
                                        "code": "source_bytes_unavailable",
                                        "authority": broker.config.authority,
                                        "thread_id": None,
                                        "message_id": None,
                                    },
                                }
                            )
                        self.wfile.write(encoded)
                        self.wfile.flush()
                        return
                    except queue.Empty:
                        sequence += 1
                        self.wfile.write(_encode_frame({"type": "heartbeat", "sequence": sequence}))
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            finally:
                worker.join()
                broker.release()
                self.close_connection = True

        def _write_json(self, payload: dict[str, object], status: HTTPStatus) -> None:
            body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

    return Handler


def _encode_frame(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def build_server(host: str, port: int, broker: TaskMessageBroker) -> ThreadingHTTPServer:
    if not _is_loopback(host):
        raise ValueError("task-message broker must bind loopback")
    return LoopbackThreadingHTTPServer((host, port), make_handler(broker))
