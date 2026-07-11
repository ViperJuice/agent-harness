from __future__ import annotations

import hashlib
import io
import json
import sys
import threading
from contextlib import redirect_stdout
from pathlib import Path

import pytest
import rfc8785

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from phase_loop_runtime.task_message_resolver import (
    CodexAppServerTaskMessageResolver,
    TaskMessageResolverError,
)
from phase_loop_runtime.cli import main


NOW = 1_800_000_000
THREAD_ID = "019f4454-2012-7061-847d-1a9ab0e9ef00"
MESSAGE_ID = "provdeploy-approval-001"
AUTHORITY = "codex-app-server://claw.test"


def _approval(source: str, **changes: object) -> str:
    value: dict[str, object] = {
        "contract_version": "embedding_provenance_deploy_approval.v2",
        "authorized": True,
        "source_thread_id": THREAD_ID,
        "source_message_id": MESSAGE_ID,
        "source_message_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "run_id": "provdeploy-001",
    }
    value.update(changes)
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _thread(source: str = "FM approves exact PROVDEPLOY body.", approval: str | None = None, **turn_changes: object) -> dict[str, object]:
    turn: dict[str, object] = {
        "id": "turn-001",
        "startedAt": NOW - 30,
        "status": "completed",
        "items": [
            {
                "id": MESSAGE_ID,
                "type": "userMessage",
                "content": [
                    {"type": "text", "text": source},
                    {"type": "text", "text": approval if approval is not None else _approval(source)},
                ],
            }
        ],
    }
    turn.update(turn_changes)
    return {"thread": {"id": THREAD_ID, "turns": [turn]}}


class FakeConnection:
    def __init__(self, thread_result: dict[str, object]) -> None:
        self.thread_result = thread_result
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.notifications: list[tuple[str, dict[str, object]]] = []
        self.closed = False

    def request(self, method: str, params: dict[str, object]) -> dict[str, object]:
        self.calls.append((method, dict(params)))
        if method == "initialize":
            return {"userAgent": "codex", "platformFamily": "unix", "platformOs": "linux", "codexHome": "/redacted"}
        if method == "thread/read":
            return self.thread_result
        raise AssertionError(method)

    def notify(self, method: str, params: dict[str, object]) -> None:
        self.notifications.append((method, dict(params)))

    def close(self) -> None:
        self.closed = True


def _resolver(result: dict[str, object], **kwargs: object) -> tuple[CodexAppServerTaskMessageResolver, FakeConnection]:
    connection = FakeConnection(result)
    resolver = CodexAppServerTaskMessageResolver(
        endpoint="ws://claw.test:8765",
        bearer_token="test-token",
        authority=AUTHORITY,
        clock=lambda: NOW,
        connection_factory=lambda endpoint, token, timeout: connection,
        **kwargs,
    )
    return resolver, connection


def _code(exc: pytest.ExceptionInfo[TaskMessageResolverError]) -> str:
    return exc.value.code


def test_exact_bytes_and_canonical_digest_are_proven() -> None:
    source = "FM approves café PROVDEPLOY.\n"
    approval = _approval(source)
    resolver, connection = _resolver(_thread(source, approval))

    proof = resolver.resolve(thread_id=THREAD_ID, message_id=MESSAGE_ID)

    assert proof.message_bytes == source.encode("utf-8")
    assert proof.approval_body_bytes == approval.encode("utf-8")
    assert proof.message_sha256 == hashlib.sha256(source.encode("utf-8")).hexdigest()
    assert proof.approval_canonical_sha256 == hashlib.sha256(rfc8785.dumps(json.loads(approval))).hexdigest()
    assert connection.calls[-1] == ("thread/read", {"threadId": THREAD_ID, "includeTurns": True})
    assert connection.notifications == [("initialized", {})]
    assert connection.closed is True


def test_one_byte_semantic_changes_change_the_proof() -> None:
    first_source = "FM approves A"
    second_source = "FM approves B"
    first, _ = _resolver(_thread(first_source, _approval(first_source, run_id="run-a")))
    second, _ = _resolver(_thread(second_source, _approval(second_source, run_id="run-b")))

    first_proof = first.resolve(thread_id=THREAD_ID, message_id=MESSAGE_ID)
    second_proof = second.resolve(thread_id=THREAD_ID, message_id=MESSAGE_ID)

    assert first_proof.message_sha256 != second_proof.message_sha256
    assert first_proof.approval_canonical_sha256 != second_proof.approval_canonical_sha256


@pytest.mark.parametrize(
    ("result", "thread_id", "message_id", "expected"),
    [
        ({"thread": {"id": "wrong", "turns": []}}, THREAD_ID, MESSAGE_ID, "source_identity_mismatch"),
        ({"thread": {"id": THREAD_ID, "turns": []}}, THREAD_ID, MESSAGE_ID, "source_message_unavailable"),
        (_thread(approval="not-json"), THREAD_ID, MESSAGE_ID, "approval_body_unavailable"),
        (_thread(approval=json.dumps({"source_message_sha256": "0" * 64})), THREAD_ID, MESSAGE_ID, "approval_body_unavailable"),
        (_thread(approval=_approval("different source")), THREAD_ID, MESSAGE_ID, "attestation_invalid"),
        (_thread(approval=_approval("FM approves exact PROVDEPLOY body.", source_message_id="wrong")), THREAD_ID, MESSAGE_ID, "source_identity_mismatch"),
        (_thread(startedAt=NOW - 901), THREAD_ID, MESSAGE_ID, "source_stale"),
    ],
)
def test_resolution_failures_are_typed_and_metadata_only(
    result: dict[str, object], thread_id: str, message_id: str, expected: str
) -> None:
    resolver, _ = _resolver(result)
    with pytest.raises(TaskMessageResolverError) as exc:
        resolver.resolve(thread_id=thread_id, message_id=message_id)
    assert _code(exc) == expected
    serialized = json.dumps(exc.value.metadata())
    assert "FM approves" not in serialized
    assert "test-token" not in serialized


def test_unavailable_remote_authority_fails_closed() -> None:
    def unavailable(endpoint: str, token: str, timeout: float) -> FakeConnection:
        raise OSError("host unavailable with secret payload")

    resolver = CodexAppServerTaskMessageResolver(
        endpoint="ws://claw.test:8765",
        bearer_token="test-token",
        authority=AUTHORITY,
        clock=lambda: NOW,
        connection_factory=unavailable,
    )
    with pytest.raises(TaskMessageResolverError) as exc:
        resolver.resolve(thread_id=THREAD_ID, message_id=MESSAGE_ID)
    assert _code(exc) == "source_task_unavailable"
    assert "secret payload" not in json.dumps(exc.value.metadata())


def test_probe_is_metadata_only() -> None:
    resolver, connection = _resolver(_thread())
    assert resolver.probe() == {"status": "ready", "authority": AUTHORITY}
    assert connection.calls == [
        (
            "initialize",
            {
                "clientInfo": {"name": "phase-loop-task-message-resolver", "version": "1"},
                "capabilities": {"experimentalApi": False},
            },
        )
    ]


def test_authenticated_loopback_authority_resolves_from_separate_client_context() -> None:
    websockets_server = pytest.importorskip("websockets.sync.server")
    expected_token = "loopback-capability-token"
    source = "FM loopback approval"
    result = _thread(source)

    def handler(socket: object) -> None:
        assert socket.request.headers["Authorization"] == f"Bearer {expected_token}"
        initialize = json.loads(socket.recv())
        socket.send(json.dumps({"id": initialize["id"], "result": {"codexHome": "/redacted", "platformFamily": "unix", "platformOs": "linux", "userAgent": "codex"}}))
        initialized = json.loads(socket.recv())
        assert initialized == {"method": "initialized", "params": {}}
        read_request = json.loads(socket.recv())
        assert read_request["method"] == "thread/read"
        socket.send(json.dumps({"id": read_request["id"], "result": result}))

    with websockets_server.serve(handler, "127.0.0.1", 0) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.socket.getsockname()[1]
        resolver = CodexAppServerTaskMessageResolver(
            endpoint=f"ws://127.0.0.1:{port}",
            bearer_token=expected_token,
            authority="codex-app-server://127.0.0.1",
            clock=lambda: NOW,
        )
        proof = resolver.resolve(thread_id=THREAD_ID, message_id=MESSAGE_ID)
        server.shutdown()
        thread.join(timeout=5)

    assert proof.message_bytes == source.encode("utf-8")
    assert proof.authority == "codex-app-server://127.0.0.1"


def test_authority_must_bind_the_endpoint_hostname() -> None:
    with pytest.raises(ValueError, match="exactly bind"):
        CodexAppServerTaskMessageResolver(
            endpoint="ws://claw.test:8765",
            bearer_token="test-token",
            authority="codex-app-server://impostor.test",
        )


def test_cli_missing_token_is_typed_and_metadata_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TASK_MESSAGE_TEST_TOKEN", raising=False)
    output = io.StringIO()
    with redirect_stdout(output):
        code = main(
            [
                "task-message-probe",
                "--endpoint",
                "ws://claw.test:8765",
                "--authority",
                AUTHORITY,
                "--token-env",
                "TASK_MESSAGE_TEST_TOKEN",
            ]
        )
    assert code == 2
    assert json.loads(output.getvalue()) == {
        "status": "blocked",
        "code": "attestation_invalid",
        "authority": AUTHORITY,
        "thread_id": None,
        "message_id": None,
    }
