from __future__ import annotations

import hashlib
import json
import socket
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from phase_loop_runtime.task_message_broker import (
    BrokerConfig,
    TaskMessageBroker,
    build_server,
    make_handler,
    verified_installed_agent_harness_sha,
)
from phase_loop_runtime.task_message_resolver import TaskMessageResolverError


TOKEN = "test-capability"
AUTHORITY = "codex-app-server://claw.test"
SHA = "a" * 40


class _Proof:
    def payload(self) -> dict[str, object]:
        return {"status": "resolved", "authority": AUTHORITY, "thread_id": "thread-1", "message_id": "message-1"}


class _Resolver:
    def __init__(self, *, delay: float = 0.0) -> None:
        self.delay = delay

    def probe(self) -> dict[str, object]:
        time.sleep(self.delay)
        return {"status": "ready", "authority": AUTHORITY}

    def resolve(self, **_kwargs: object) -> _Proof:
        time.sleep(self.delay)
        return _Proof()


def _server(*, delay: float = 0.0, calls: list[int] | None = None):
    def factory(max_age: int):
        if calls is not None:
            calls.append(max_age)
        return _Resolver(delay=delay)

    broker = TaskMessageBroker(
        BrokerConfig(
            authority=AUTHORITY,
            token_sha256=hashlib.sha256(TOKEN.encode()).hexdigest(),
            agent_harness_sha=SHA,
            heartbeat_seconds=0.01,
        ),
        factory,
    )
    server = build_server("127.0.0.1", 0, broker)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _post(server, path: str, payload: dict[str, object], token: str = TOKEN):
    request = Request(
        f"http://127.0.0.1:{server.server_port}{path}",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    return urlopen(request, timeout=1)


def test_authentication_fails_before_resolver_construction() -> None:
    calls: list[int] = []
    server = _server(calls=calls)
    try:
        with pytest.raises(HTTPError) as exc:
            _post(server, "/v1/task-message/probe", {}, token="wrong")
        assert exc.value.code == 401
        assert calls == []
        assert TOKEN not in exc.value.read().decode()
    finally:
        server.shutdown()
        server.server_close()


def test_authenticated_empty_body_is_rejected_before_resolver_construction() -> None:
    calls: list[int] = []
    server = _server(calls=calls)
    try:
        request = Request(
            f"http://127.0.0.1:{server.server_port}/v1/task-message/probe",
            data=b"",
            method="POST",
            headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        )
        with pytest.raises(HTTPError) as exc:
            urlopen(request, timeout=1)
        assert exc.value.code == 400
        assert calls == []
    finally:
        server.shutdown()
        server.server_close()


def test_resolve_streams_exact_heartbeats_then_terminal_result() -> None:
    server = _server(delay=0.025)
    try:
        with _post(
            server,
            "/v1/task-message/resolve",
            {"thread_id": "thread-1", "message_id": "message-1", "max_source_age_seconds": 900},
        ) as response:
            frames = [json.loads(line) for line in response]
        assert frames[:-1] == [
            {"sequence": index, "type": "heartbeat"} for index in range(1, len(frames))
        ]
        assert frames[-1] == {
            "agent_harness_sha": SHA,
            "payload": {"authority": AUTHORITY, "message_id": "message-1", "status": "resolved", "thread_id": "thread-1"},
            "type": "result",
        }
    finally:
        server.shutdown()
        server.server_close()


def test_request_schema_and_loopback_bind_fail_closed() -> None:
    server = _server()
    try:
        with pytest.raises(HTTPError) as exc:
            _post(server, "/v1/task-message/resolve", {"thread_id": "thread-1", "extra": True})
        assert exc.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
    with pytest.raises(ValueError, match="loopback"):
        build_server("0.0.0.0", 0, TaskMessageBroker(
            BrokerConfig(AUTHORITY, hashlib.sha256(TOKEN.encode()).hexdigest(), SHA),
            lambda _age: _Resolver(),
        ))


@pytest.mark.parametrize("path,content_type", [("/v1/task-message/probe?extra=1", "application/json"), ("/v1/task-message/probe", "text/plain")])
def test_query_and_non_json_requests_are_rejected(path: str, content_type: str) -> None:
    server = _server()
    try:
        request = Request(
            f"http://127.0.0.1:{server.server_port}{path}",
            data=b"{}",
            method="POST",
            headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": content_type},
        )
        with pytest.raises(HTTPError) as exc:
            urlopen(request, timeout=1)
        assert exc.value.code == 400
    finally:
        server.shutdown()
        server.server_close()


def test_boolean_age_and_oversized_request_are_rejected() -> None:
    server = _server()
    try:
        with pytest.raises(HTTPError) as boolean_exc:
            _post(server, "/v1/task-message/resolve", {"thread_id": "thread-1", "message_id": "message-1", "max_source_age_seconds": True})
        assert boolean_exc.value.code == 400
        request = Request(
            f"http://127.0.0.1:{server.server_port}/v1/task-message/probe",
            data=b" " * 20_000,
            method="POST",
            headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        )
        with pytest.raises(HTTPError) as size_exc:
            urlopen(request, timeout=1)
        assert size_exc.value.code == 400
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("body", [b'{"thread_id":"one","thread_id":"two","message_id":"message-1","max_source_age_seconds":900}', b'{"value":NaN}'])
def test_duplicate_and_non_finite_request_json_is_rejected(body: bytes) -> None:
    server = _server()
    try:
        request = Request(
            f"http://127.0.0.1:{server.server_port}/v1/task-message/resolve",
            data=body,
            method="POST",
            headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        )
        with pytest.raises(HTTPError) as exc:
            urlopen(request, timeout=1)
        assert exc.value.code == 400
    finally:
        server.shutdown()
        server.server_close()


def test_installed_sha_must_match_exact_vcs_provenance(monkeypatch) -> None:
    class Distribution:
        def read_text(self, name: str) -> str:
            assert name == "direct_url.json"
            return json.dumps({
                "url": "https://github.com/Consiliency/agent-harness.git",
                "subdirectory": "phase-loop-runtime",
                "vcs_info": {"vcs": "git", "requested_revision": SHA, "commit_id": SHA},
            })

    monkeypatch.setattr("phase_loop_runtime.task_message_broker.importlib.metadata.distribution", lambda _name: Distribution())
    assert verified_installed_agent_harness_sha(SHA) == SHA
    with pytest.raises(ValueError, match="mismatch"):
        verified_installed_agent_harness_sha("b" * 40)


def test_blocked_resolver_result_is_metadata_only() -> None:
    class BlockedResolver(_Resolver):
        def probe(self):
            raise TaskMessageResolverError("source_task_unavailable", authority=AUTHORITY)

    broker = TaskMessageBroker(
        BrokerConfig(AUTHORITY, hashlib.sha256(TOKEN.encode()).hexdigest(), SHA, heartbeat_seconds=0.01),
        lambda _age: BlockedResolver(),
    )
    server = build_server("127.0.0.1", 0, broker)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with _post(server, "/v1/task-message/probe", {}) as response:
            result = json.loads(list(response)[-1])
        assert result["payload"]["status"] == "blocked"
        assert result["payload"]["code"] == "source_task_unavailable"
        assert TOKEN not in json.dumps(result)
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("mode,expected_code", [("exception", "source_task_unavailable"), ("oversized", "source_bytes_unavailable")])
def test_resolve_fallbacks_preserve_requested_identities(mode: str, expected_code: str) -> None:
    class FallbackProof:
        def payload(self) -> dict[str, object]:
            if mode == "exception":
                raise RuntimeError("unavailable")
            return {"oversized": "x" * 1_100_000}

    class FallbackResolver(_Resolver):
        def resolve(self, **_kwargs: object) -> FallbackProof:
            if mode == "exception":
                raise RuntimeError("unavailable")
            return FallbackProof()

    broker = TaskMessageBroker(
        BrokerConfig(AUTHORITY, hashlib.sha256(TOKEN.encode()).hexdigest(), SHA, heartbeat_seconds=0.01),
        lambda _age: FallbackResolver(),
    )
    server = build_server("127.0.0.1", 0, broker)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with _post(
            server,
            "/v1/task-message/resolve",
            {"thread_id": "thread-1", "message_id": "message-1", "max_source_age_seconds": 900},
        ) as response:
            result = json.loads(list(response)[-1])["payload"]
        assert result == {
            "status": "blocked",
            "code": expected_code,
            "authority": AUTHORITY,
            "thread_id": "thread-1",
            "message_id": "message-1",
        }
    finally:
        server.shutdown()
        server.server_close()


def test_disconnect_holds_single_flight_until_owner_socket_worker_finishes() -> None:
    server = _server(delay=0.12)
    try:
        raw = socket.create_connection(("127.0.0.1", server.server_port), timeout=1)
        body = b"{}"
        raw.sendall(
            b"POST /v1/task-message/probe HTTP/1.1\r\n"
            + f"Host: 127.0.0.1\r\nAuthorization: Bearer {TOKEN}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
        raw.close()
        time.sleep(0.03)
        with pytest.raises(HTTPError) as busy:
            _post(server, "/v1/task-message/probe", {})
        assert busy.value.code == 503
        time.sleep(0.12)
        with _post(server, "/v1/task-message/probe", {}) as response:
            assert json.loads(list(response)[-1])["payload"]["status"] == "ready"
    finally:
        server.shutdown()
        server.server_close()


def test_header_write_failure_joins_worker_and_releases_single_flight() -> None:
    broker = TaskMessageBroker(
        BrokerConfig(AUTHORITY, hashlib.sha256(TOKEN.encode()).hexdigest(), SHA, heartbeat_seconds=0.01),
        lambda _age: _Resolver(delay=0.02),
    )
    assert broker.acquire() is True
    handler = object.__new__(make_handler(broker))
    handler.send_response = lambda _status: (_ for _ in ()).throw(BrokenPipeError())
    handler.close_connection = False
    handler._stream(broker.probe)
    assert broker.acquire() is True
    broker.release()


def test_worker_start_failure_releases_single_flight(monkeypatch) -> None:
    broker = TaskMessageBroker(
        BrokerConfig(AUTHORITY, hashlib.sha256(TOKEN.encode()).hexdigest(), SHA),
        lambda _age: _Resolver(),
    )
    assert broker.acquire() is True
    handler = object.__new__(make_handler(broker))
    monkeypatch.setattr(threading.Thread, "start", lambda _self: (_ for _ in ()).throw(RuntimeError("unavailable")))
    with pytest.raises(RuntimeError, match="unavailable"):
        handler._stream(broker.probe)
    assert broker.acquire() is True
    broker.release()
