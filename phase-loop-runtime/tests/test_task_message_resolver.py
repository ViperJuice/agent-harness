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
    _ConnectionFailure,
    _ControlSocketJsonRpcConnection,
)
from phase_loop_runtime.cli import main


NOW = 1_800_000_000
THREAD_ID = "019f4454-2012-7061-847d-1a9ab0e9ef00"
MESSAGE_ID = "provdeploy-approval-001"
APPROVAL_MESSAGE_ID = f"{MESSAGE_ID}-approval"
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
    source_turn: dict[str, object] = {
        "id": "turn-001",
        "startedAt": NOW - 30,
        "status": "completed",
        "items": [
            {
                "id": "item-source",
                "clientId": MESSAGE_ID,
                "type": "userMessage",
                "content": [{"type": "text", "text": source}],
            }
        ],
    }
    source_turn.update(turn_changes)
    approval_turn: dict[str, object] = {
        "id": "turn-002",
        "startedAt": NOW - 20,
        "status": "completed",
        "items": [
            {
                "id": "item-approval",
                "clientId": APPROVAL_MESSAGE_ID,
                "type": "userMessage",
                "content": [{"type": "text", "text": approval if approval is not None else _approval(source)}],
            }
        ],
    }
    return {"thread": {"id": THREAD_ID, "turns": [source_turn, approval_turn]}}


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
    assert proof.message_id == MESSAGE_ID
    assert proof.approval_message_id == APPROVAL_MESSAGE_ID
    assert proof.source_item_id == "item-source"
    assert proof.approval_item_id == "item-approval"
    assert proof.turn_id == "turn-001"
    assert proof.approval_turn_id == "turn-002"
    assert proof.message_sha256 == hashlib.sha256(source.encode("utf-8")).hexdigest()
    assert proof.approval_canonical_sha256 == hashlib.sha256(rfc8785.dumps(json.loads(approval))).hexdigest()
    assert connection.calls[-1] == ("thread/read", {"threadId": THREAD_ID, "includeTurns": True})
    assert connection.notifications == [("initialized", {})]
    assert connection.closed is True


@pytest.mark.parametrize(
    "contract_version",
    (
        "embedding_provenance_deploy_approval.v2",
        "embedding_provenance_bootstrap_approval.v3",
    ),
)
def test_governed_deploy_and_bootstrap_contracts_are_accepted(contract_version: str) -> None:
    source = "FM approves the governed action."
    approval = _approval(source, contract_version=contract_version)
    resolver, _ = _resolver(_thread(source, approval))

    proof = resolver.resolve(thread_id=THREAD_ID, message_id=MESSAGE_ID)

    assert proof.approval_body_bytes == approval.encode("utf-8")


def test_one_byte_semantic_changes_change_the_proof() -> None:
    first_source = "FM approves A"
    second_source = "FM approves B"
    first, _ = _resolver(_thread(first_source, _approval(first_source, run_id="run-a")))
    second, _ = _resolver(_thread(second_source, _approval(second_source, run_id="run-b")))

    first_proof = first.resolve(thread_id=THREAD_ID, message_id=MESSAGE_ID)
    second_proof = second.resolve(thread_id=THREAD_ID, message_id=MESSAGE_ID)

    assert first_proof.message_sha256 != second_proof.message_sha256
    assert first_proof.approval_canonical_sha256 != second_proof.approval_canonical_sha256


def test_stored_item_id_is_not_a_substitute_for_client_identity() -> None:
    result = _thread()
    source_item = result["thread"]["turns"][0]["items"][0]
    source_item["id"] = MESSAGE_ID
    source_item.pop("clientId")
    resolver, _ = _resolver(result)

    with pytest.raises(TaskMessageResolverError) as exc:
        resolver.resolve(thread_id=THREAD_ID, message_id=MESSAGE_ID)

    assert _code(exc) == "source_message_unavailable"


def test_app_server_concatenated_single_item_is_rejected() -> None:
    source = "FM approves exact PROVDEPLOY body."
    result = _thread(source)
    source_turn = result["thread"]["turns"][0]
    source_turn["items"][0]["content"][0]["text"] = source + _approval(source)
    result["thread"]["turns"] = [source_turn]
    resolver, _ = _resolver(result)

    with pytest.raises(TaskMessageResolverError) as exc:
        resolver.resolve(thread_id=THREAD_ID, message_id=MESSAGE_ID)

    assert _code(exc) == "approval_body_unavailable"


def test_approval_message_must_follow_the_source_message() -> None:
    result = _thread()
    result["thread"]["turns"].reverse()
    resolver, _ = _resolver(result)

    with pytest.raises(TaskMessageResolverError) as exc:
        resolver.resolve(thread_id=THREAD_ID, message_id=MESSAGE_ID)

    assert _code(exc) == "source_identity_mismatch"


def test_approval_client_identity_must_be_unique() -> None:
    result = _thread()
    result["thread"]["turns"].append(result["thread"]["turns"][1])
    resolver, _ = _resolver(result)

    with pytest.raises(TaskMessageResolverError) as exc:
        resolver.resolve(thread_id=THREAD_ID, message_id=MESSAGE_ID)

    assert _code(exc) == "source_identity_mismatch"


def test_stored_item_identities_must_be_nonempty_and_distinct() -> None:
    duplicate = _thread()
    duplicate["thread"]["turns"][1]["items"][0]["id"] = "item-source"
    duplicate_resolver, _ = _resolver(duplicate)
    with pytest.raises(TaskMessageResolverError) as duplicate_exc:
        duplicate_resolver.resolve(thread_id=THREAD_ID, message_id=MESSAGE_ID)
    assert _code(duplicate_exc) == "source_identity_mismatch"

    empty = _thread()
    empty["thread"]["turns"][0]["items"][0]["id"] = ""
    empty_resolver, _ = _resolver(empty)
    with pytest.raises(TaskMessageResolverError) as empty_exc:
        empty_resolver.resolve(thread_id=THREAD_ID, message_id=MESSAGE_ID)
    assert _code(empty_exc) == "source_identity_mismatch"


@pytest.mark.parametrize(
    ("result", "thread_id", "message_id", "expected"),
    [
        ({"thread": {"id": "wrong", "turns": []}}, THREAD_ID, MESSAGE_ID, "source_identity_mismatch"),
        ({"thread": {"id": THREAD_ID, "turns": []}}, THREAD_ID, MESSAGE_ID, "source_message_unavailable"),
        (_thread(approval="not-json"), THREAD_ID, MESSAGE_ID, "approval_body_unavailable"),
        (_thread(approval=json.dumps({"source_message_sha256": "0" * 64})), THREAD_ID, MESSAGE_ID, "approval_body_unavailable"),
        (_thread(approval=_approval("FM approves exact PROVDEPLOY body.", contract_version="wrong")), THREAD_ID, MESSAGE_ID, "approval_body_unavailable"),
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


@pytest.mark.parametrize(
    "approval",
    (
        _approval("FM approves exact PROVDEPLOY body.").replace(
            '"contract_version":"embedding_provenance_deploy_approval.v2"',
            '"contract_version":"wrong","contract_version":"embedding_provenance_deploy_approval.v2"',
        ),
        _approval(
            "FM approves exact PROVDEPLOY body.",
            contract_version=["embedding_provenance_deploy_approval.v2"],
        ),
    ),
)
def test_duplicate_or_non_string_contract_version_fails_closed(approval: str) -> None:
    resolver, _ = _resolver(_thread(approval=approval))

    with pytest.raises(TaskMessageResolverError) as exc:
        resolver.resolve(thread_id=THREAD_ID, message_id=MESSAGE_ID)

    assert _code(exc) == "approval_body_unavailable"


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


def test_initialize_transport_failure_closes_the_open_connection() -> None:
    connection = FakeConnection(_thread())

    def fail_initialize(method: str, params: dict[str, object]) -> dict[str, object]:
        raise _ConnectionFailure("source_task_unavailable")

    connection.request = fail_initialize  # type: ignore[method-assign]
    resolver = CodexAppServerTaskMessageResolver(
        endpoint="ws://claw.test:8765",
        bearer_token="test-token",
        authority=AUTHORITY,
        connection_factory=lambda endpoint, token, timeout: connection,
    )

    with pytest.raises(TaskMessageResolverError) as exc:
        resolver.probe()

    assert _code(exc) == "source_task_unavailable"
    assert connection.closed is True


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


def test_local_control_socket_mode_uses_local_transport_without_bearer() -> None:
    connection = FakeConnection(_thread())
    observed: list[tuple[str, str, float]] = []

    def factory(target: str, credential: str, timeout: float) -> FakeConnection:
        observed.append((target, credential, timeout))
        return connection

    resolver = CodexAppServerTaskMessageResolver(
        control_socket="/home/test/.codex/app-server-control/app-server-control.sock",
        authority=AUTHORITY,
        clock=lambda: NOW,
        connection_factory=factory,
    )

    proof = resolver.resolve(thread_id=THREAD_ID, message_id=MESSAGE_ID)

    assert proof.message_id == MESSAGE_ID
    assert observed == [("/home/test/.codex/app-server-control/app-server-control.sock", "", 10.0)]


def test_control_socket_disables_websocket_compression(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    class Socket:
        def close(self) -> None:
            observed["closed"] = True

    def unix_connect(**kwargs: object) -> Socket:
        observed.update(kwargs)
        return Socket()

    monkeypatch.setattr("websockets.sync.client.unix_connect", unix_connect)
    connection = _ControlSocketJsonRpcConnection("/tmp/app-server.sock", "", 7.0)
    connection.close()

    assert observed == {
        "path": "/tmp/app-server.sock",
        "uri": "ws://localhost",
        "compression": None,
        "open_timeout": 7.0,
        "close_timeout": 7.0,
        "closed": True,
    }


def test_task_message_transport_is_exactly_one_of_websocket_or_control_socket() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        CodexAppServerTaskMessageResolver(authority=AUTHORITY)
    with pytest.raises(ValueError, match="exactly one"):
        CodexAppServerTaskMessageResolver(
            endpoint="ws://claw.test:8765",
            bearer_token="test-token",
            control_socket="/tmp/app-server.sock",
            authority=AUTHORITY,
        )


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


def test_cli_control_socket_rejects_token_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TASK_MESSAGE_TEST_TOKEN", "must-not-be-read")
    output = io.StringIO()
    with redirect_stdout(output):
        code = main(
            [
                "task-message-probe",
                "--control-socket",
                "/tmp/app-server.sock",
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
    assert "must-not-be-read" not in output.getvalue()
