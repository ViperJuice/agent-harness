from __future__ import annotations

import io
import base64
import hashlib
import json
import threading
import time
from email.message import Message
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path

import pytest
import rfc8785

from phase_loop_runtime.task_message_broker_client import TaskMessageBrokerClient
from phase_loop_runtime.task_message_resolver import TaskMessageResolverError
from phase_loop_runtime.cli import main


AUTHORITY = "codex-app-server://claw.test"
SHA = "b" * 40
SOURCE_BYTES = b"governed source"


def _approval_bytes(**overrides: object) -> bytes:
    body: dict[str, object] = {
        "contract_version": "embedding_provenance_deploy_approval.v2",
        "authorized": True,
        "source_thread_id": "thread-1",
        "source_message_id": "message-1",
        "source_message_sha256": hashlib.sha256(SOURCE_BYTES).hexdigest(),
    }
    body.update(overrides)
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


class _Response(io.BytesIO):
    def __init__(self, frames: list[dict[str, object]]) -> None:
        super().__init__(b"".join(json.dumps(frame).encode() + b"\n" for frame in frames))
        self.headers = Message()
        self.headers["Content-Type"] = "application/x-ndjson"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class _TimedResponse(_Response):
    def __init__(self, frames: list[dict[str, object]], *, fail_after: int | None = None) -> None:
        super().__init__(frames)
        self.read_count = 0
        self.total_elapsed = 0
        self.fail_after = fail_after

    def readline(self, limit: int = -1) -> bytes:
        self.read_count += 1
        if self.fail_after is not None and self.read_count > self.fail_after:
            raise TimeoutError
        self.total_elapsed += 5
        return super().readline(limit)


class _RawResponse(_Response):
    def __init__(self, raw: bytes) -> None:
        super().__init__([])
        self.write(raw)
        self.seek(0)


class _TrickleResponse(_Response):
    def readline(self, limit: int = -1) -> bytes:
        for _ in range(10):
            time.sleep(0.01)
        return super().readline(limit)


def _resolved_payload() -> dict[str, object]:
    approval_bytes = _approval_bytes()
    return {
        "status": "resolved",
        "authority": AUTHORITY,
        "thread_id": "thread-1",
        "turn_id": "turn-1",
        "approval_turn_id": "turn-2",
        "message_id": "message-1",
        "approval_message_id": "message-1-approval",
        "source_item_id": "item-1",
        "approval_item_id": "item-2",
        "source_turn_index": 0,
        "source_item_index": 0,
        "approval_turn_index": 1,
        "approval_item_index": 0,
        "message_sha256": hashlib.sha256(SOURCE_BYTES).hexdigest(),
        "approval_body_sha256": hashlib.sha256(approval_bytes).hexdigest(),
        "approval_canonical_sha256": hashlib.sha256(rfc8785.dumps(json.loads(approval_bytes))).hexdigest(),
        "source_started_at": 1,
        "approval_started_at": 2,
        "resolved_at": 2,
        "message_bytes_b64": base64.b64encode(SOURCE_BYTES).decode(),
        "approval_body_bytes_b64": base64.b64encode(approval_bytes).decode(),
    }


def _client(frames: list[dict[str, object]]) -> TaskMessageBrokerClient:
    return TaskMessageBrokerClient(
        broker_url="https://claw.test:8765",
        bearer_token="token",
        authority=AUTHORITY,
        opener=lambda _request, timeout: _Response(frames),
    )


def test_client_accepts_monotonic_heartbeats_and_exact_result() -> None:
    result = _client([
        {"type": "heartbeat", "sequence": 1},
        {"type": "heartbeat", "sequence": 2},
        {"type": "result", "agent_harness_sha": SHA, "payload": {"status": "ready", "authority": AUTHORITY}},
    ]).probe()
    assert result == {"status": "ready", "authority": AUTHORITY, "agent_harness_sha": SHA}


@pytest.mark.parametrize(
    "frame",
    [
        {"type": "heartbeat", "sequence": 2},
        {"type": "heartbeat", "sequence": 1, "extra": True},
        {"type": "result", "agent_harness_sha": "draft", "payload": {}},
        {"type": "result", "agent_harness_sha": SHA, "payload": {}, "extra": True},
    ],
)
def test_client_rejects_malformed_frames(frame: dict[str, object]) -> None:
    with pytest.raises(TaskMessageResolverError) as exc:
        _client([frame]).probe()
    assert exc.value.code == "attestation_invalid"


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "ready", "authority": "codex-app-server://wrong"},
        {"status": "ready", "authority": AUTHORITY, "extra": True},
        {"status": "blocked", "code": "unknown", "authority": AUTHORITY, "thread_id": None, "message_id": None},
    ],
)
def test_client_rejects_non_exact_resolver_payload(payload: dict[str, object]) -> None:
    with pytest.raises(TaskMessageResolverError) as exc:
        _client([{"type": "result", "agent_harness_sha": SHA, "payload": payload}]).probe()
    assert exc.value.code == "attestation_invalid"


def test_remote_plain_http_is_rejected() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        TaskMessageBrokerClient(
            broker_url="http://claw.test:8765",
            bearer_token="token",
            authority=AUTHORITY,
        )


def test_redirect_is_rejected_without_forwarding_bearer() -> None:
    received_authorization: list[str | None] = []

    class TargetHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            received_authorization.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()

        do_POST = do_GET

        def log_message(self, _format: str, *_args: object) -> None:
            return

    target = ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler)

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            self.send_response(307)
            self.send_header("Location", f"http://127.0.0.1:{target.server_port}/stolen")
            self.end_headers()

        def log_message(self, _format: str, *_args: object) -> None:
            return

    redirect = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (target, redirect)]
    for thread in threads:
        thread.start()
    try:
        client = TaskMessageBrokerClient(
            broker_url=f"http://127.0.0.1:{redirect.server_port}",
            bearer_token="secret",
            authority=AUTHORITY,
        )
        with pytest.raises(TaskMessageResolverError) as exc:
            client.probe()
        assert exc.value.code == "attestation_invalid"
        assert received_authorization == []
    finally:
        redirect.shutdown()
        target.shutdown()
        redirect.server_close()
        target.server_close()


def test_fresh_heartbeats_outlive_total_timeout_without_deadline() -> None:
    response = _TimedResponse(
        [
            {"type": "heartbeat", "sequence": 1},
            {"type": "heartbeat", "sequence": 2},
            {"type": "heartbeat", "sequence": 3},
            {"type": "heartbeat", "sequence": 4},
            {"type": "result", "agent_harness_sha": SHA, "payload": {"status": "ready", "authority": AUTHORITY}},
        ]
    )
    seen_timeout: list[float] = []
    client = TaskMessageBrokerClient(
        broker_url="https://claw.test:8765",
        bearer_token="token",
        authority=AUTHORITY,
        heartbeat_timeout_seconds=15.0,
        opener=lambda _request, timeout: seen_timeout.append(timeout) or response,
    )
    assert client.probe()["status"] == "ready"
    assert seen_timeout == [15.0]
    assert response.total_elapsed == 30


def test_heartbeat_silence_fails_closed() -> None:
    response = _TimedResponse([{"type": "heartbeat", "sequence": 1}], fail_after=1)
    client = TaskMessageBrokerClient(
        broker_url="https://claw.test:8765",
        bearer_token="token",
        authority=AUTHORITY,
        heartbeat_timeout_seconds=15.0,
        opener=lambda _request, timeout: response,
    )
    with pytest.raises(TaskMessageResolverError) as exc:
        client.probe()
    assert exc.value.code == "source_task_unavailable"


def test_partial_frame_trickle_cannot_extend_heartbeat_deadline() -> None:
    response = _TrickleResponse([
        {"type": "heartbeat", "sequence": 1},
        {"type": "result", "agent_harness_sha": SHA, "payload": {"status": "ready", "authority": AUTHORITY}},
    ])
    client = TaskMessageBrokerClient(
        broker_url="https://claw.test:8765",
        bearer_token="token",
        authority=AUTHORITY,
        heartbeat_timeout_seconds=0.03,
        opener=lambda _request, timeout: response,
    )
    with pytest.raises(TaskMessageResolverError) as exc:
        client.probe()
    assert exc.value.code == "source_task_unavailable"


@pytest.mark.parametrize("raw", [b'{"type":"heartbeat","sequence":1,"sequence":2}\n', b'{"type":"heartbeat","sequence":NaN}\n'])
def test_duplicate_and_non_finite_frame_json_is_rejected(raw: bytes) -> None:
    client = TaskMessageBrokerClient(
        broker_url="https://claw.test:8765",
        bearer_token="token",
        authority=AUTHORITY,
        opener=lambda _request, timeout: _RawResponse(raw),
    )
    with pytest.raises(TaskMessageResolverError) as exc:
        client.probe()
    assert exc.value.code == "attestation_invalid"


@pytest.mark.parametrize(
    "field,value",
    [
        ("message_sha256", "0" * 64),
        ("approval_body_bytes_b64", "***"),
        ("source_item_id", 7),
        ("approval_item_id", "item-1"),
        ("approval_turn_id", "turn-1"),
        ("resolved_at", 902),
        ("approval_started_at", 0),
        ("approval_turn_index", 0),
        ("approval_item_index", -1),
        ("resolved_at", "2"),
    ],
)
def test_resolved_proof_fields_are_cryptographically_validated(field: str, value: object) -> None:
    payload = _resolved_payload()
    payload[field] = value
    client = _client([{"type": "result", "agent_harness_sha": SHA, "payload": payload}])
    with pytest.raises(TaskMessageResolverError) as exc:
        client.resolve(thread_id="thread-1", message_id="message-1", max_source_age_seconds=900)
    assert exc.value.code == "attestation_invalid"


def test_valid_resolved_proof_is_accepted() -> None:
    client = _client([{"type": "result", "agent_harness_sha": SHA, "payload": _resolved_payload()}])
    result = client.resolve(thread_id="thread-1", message_id="message-1", max_source_age_seconds=900)
    assert result["message_sha256"] == hashlib.sha256(SOURCE_BYTES).hexdigest()


def test_trailing_frame_or_junk_after_terminal_result_is_rejected() -> None:
    first = json.dumps({"type": "result", "agent_harness_sha": SHA, "payload": {"status": "ready", "authority": AUTHORITY}}).encode() + b"\n"
    for trailing in (b"junk\n", first):
        client = TaskMessageBrokerClient(
            broker_url="https://claw.test:8765",
            bearer_token="token",
            authority=AUTHORITY,
            opener=lambda _request, timeout, raw=first + trailing: _RawResponse(raw),
        )
        with pytest.raises(TaskMessageResolverError) as exc:
            client.probe()
        assert exc.value.code == "attestation_invalid"


@pytest.mark.parametrize(
    "overrides",
    [
        {"authorized": False},
        {"contract_version": "wrong"},
        {"source_thread_id": "thread-2"},
        {"source_message_id": "message-2"},
        {"source_message_sha256": "0" * 64},
    ],
)
def test_approval_claims_must_authorize_and_bind_source(overrides: dict[str, object]) -> None:
    payload = _resolved_payload()
    approval_bytes = _approval_bytes(**overrides)
    payload["approval_body_bytes_b64"] = base64.b64encode(approval_bytes).decode()
    payload["approval_body_sha256"] = hashlib.sha256(approval_bytes).hexdigest()
    payload["approval_canonical_sha256"] = hashlib.sha256(rfc8785.dumps(json.loads(approval_bytes))).hexdigest()
    client = _client([{"type": "result", "agent_harness_sha": SHA, "payload": payload}])
    with pytest.raises(TaskMessageResolverError) as exc:
        client.resolve(thread_id="thread-1", message_id="message-1", max_source_age_seconds=900)
    assert exc.value.code == "attestation_invalid"


def test_approval_missing_required_claim_is_rejected() -> None:
    body = json.loads(_approval_bytes())
    del body["authorized"]
    approval_bytes = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    payload = _resolved_payload()
    payload["approval_body_bytes_b64"] = base64.b64encode(approval_bytes).decode()
    payload["approval_body_sha256"] = hashlib.sha256(approval_bytes).hexdigest()
    payload["approval_canonical_sha256"] = hashlib.sha256(rfc8785.dumps(body)).hexdigest()
    client = _client([{"type": "result", "agent_harness_sha": SHA, "payload": payload}])
    with pytest.raises(TaskMessageResolverError) as exc:
        client.resolve(thread_id="thread-1", message_id="message-1", max_source_age_seconds=900)
    assert exc.value.code == "attestation_invalid"


def test_oversized_frame_fails_closed() -> None:
    oversized = _Response([])
    oversized.write(b"{" + b"x" * 1_048_576 + b"}\n")
    oversized.seek(0)
    client = TaskMessageBrokerClient(
        broker_url="https://claw.test:8765",
        bearer_token="token",
        authority=AUTHORITY,
        opener=lambda _request, timeout: oversized,
    )
    with pytest.raises(TaskMessageResolverError) as exc:
        client.probe()
    assert exc.value.code == "attestation_invalid"


def test_cli_broker_mode_uses_token_environment_and_preserves_release_sha(monkeypatch) -> None:
    calls: list[tuple[str, str, str, float]] = []

    class FakeClient:
        def __init__(self, *, broker_url, bearer_token, authority, heartbeat_timeout_seconds):
            calls.append((broker_url, bearer_token, authority, heartbeat_timeout_seconds))

        def probe(self):
            return {"status": "ready", "authority": AUTHORITY, "agent_harness_sha": SHA}

    monkeypatch.setattr("phase_loop_runtime.task_message_broker_client.TaskMessageBrokerClient", FakeClient)
    monkeypatch.setenv("BROKER_TOKEN", "secret")
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        code = main([
            "task-message-probe",
            "--broker-url", "https://claw.test:8765",
            "--authority", AUTHORITY,
            "--token-env", "BROKER_TOKEN",
        ])
    assert code == 0
    assert calls == [("https://claw.test:8765", "secret", AUTHORITY, 15.0)]
    assert json.loads(stdout.getvalue())["agent_harness_sha"] == SHA


def test_cli_broker_mode_fails_closed_without_token(monkeypatch) -> None:
    monkeypatch.delenv("MISSING_TOKEN", raising=False)
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        code = main([
            "task-message-probe",
            "--broker-url", "https://claw.test:8765",
            "--authority", AUTHORITY,
            "--token-env", "MISSING_TOKEN",
        ])
    assert code == 2
    assert json.loads(stdout.getvalue())["code"] == "attestation_invalid"


def test_valid_broker_blocked_result_preserves_error_contract() -> None:
    payload = {
        "status": "blocked",
        "code": "source_task_unavailable",
        "authority": AUTHORITY,
        "thread_id": None,
        "message_id": None,
    }
    with pytest.raises(TaskMessageResolverError) as exc:
        _client([{"type": "result", "agent_harness_sha": SHA, "payload": payload}]).probe()
    assert exc.value.metadata() == payload


@pytest.mark.parametrize("code", ["source_task_unavailable", "source_bytes_unavailable"])
def test_broker_resolve_failure_preserves_requested_identities(code: str) -> None:
    payload = {
        "status": "blocked",
        "code": code,
        "authority": AUTHORITY,
        "thread_id": "thread-1",
        "message_id": "message-1",
    }
    with pytest.raises(TaskMessageResolverError) as exc:
        _client([{"type": "result", "agent_harness_sha": SHA, "payload": payload}]).resolve(
            thread_id="thread-1", message_id="message-1", max_source_age_seconds=900
        )
    assert exc.value.metadata() == payload


def test_cli_broker_blocked_result_exits_two_with_exact_metadata(monkeypatch) -> None:
    class BlockedClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def probe(self) -> dict[str, object]:
            raise TaskMessageResolverError("source_task_unavailable", authority=AUTHORITY)

    monkeypatch.setattr("phase_loop_runtime.task_message_broker_client.TaskMessageBrokerClient", BlockedClient)
    monkeypatch.setenv("BROKER_TOKEN", "secret")
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        code = main([
            "task-message-probe",
            "--broker-url", "https://claw.test:8765",
            "--authority", AUTHORITY,
            "--token-env", "BROKER_TOKEN",
        ])
    assert code == 2
    assert json.loads(stdout.getvalue()) == {
        "status": "blocked",
        "code": "source_task_unavailable",
        "authority": AUTHORITY,
        "thread_id": None,
        "message_id": None,
    }


def test_cli_broker_resolve_failure_exits_two_with_requested_identities(monkeypatch) -> None:
    class BlockedClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def resolve(self, *, thread_id: str, message_id: str, max_source_age_seconds: int) -> dict[str, object]:
            raise TaskMessageResolverError(
                "source_bytes_unavailable",
                authority=AUTHORITY,
                thread_id=thread_id,
                message_id=message_id,
            )

    monkeypatch.setattr("phase_loop_runtime.task_message_broker_client.TaskMessageBrokerClient", BlockedClient)
    monkeypatch.setenv("BROKER_TOKEN", "secret")
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        code = main([
            "task-message-resolve",
            "--broker-url", "https://claw.test:8765",
            "--authority", AUTHORITY,
            "--token-env", "BROKER_TOKEN",
            "--thread-id", "thread-1",
            "--message-id", "message-1",
            "--max-source-age-seconds", "900",
        ])
    assert code == 2
    assert json.loads(stdout.getvalue()) == {
        "status": "blocked",
        "code": "source_bytes_unavailable",
        "authority": AUTHORITY,
        "thread_id": "thread-1",
        "message_id": "message-1",
    }


def test_user_service_is_loopback_digest_only_and_does_not_manage_codex() -> None:
    unit = files("phase_loop_runtime").joinpath("deploy/phase-loop-task-message-broker.service").read_text()
    assert "--host 127.0.0.1" in unit
    assert "--token-sha256 ${TASK_MESSAGE_TOKEN_SHA256}" in unit
    assert "${AGENT_HARNESS_SHA}" in unit
    assert "Bearer" not in unit
    assert "codex app-server" not in unit
    assert "tailscale" not in unit
    assert "ProtectHome=tmpfs" in unit
    assert "BindReadOnlyPaths=%h/.local/share/phase-loop-task-message-broker" in unit
    assert "\nBindReadOnlyPaths=%h/.local\n" not in unit
    assert "BindReadOnlyPaths=%h/.codex/app-server-control/app-server-control.sock" in unit
    assert "\nBindReadOnlyPaths=%h/.codex/app-server-control\n" not in unit
    assert "PrivateDevices=" not in unit
    assert "ProtectKernelModules=" not in unit
    assert "IPAddressDeny=" not in unit
    assert "IPAddressAllow=" not in unit
    assert "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6" in unit


@pytest.mark.dotfiles_integration
def test_packaged_user_service_matches_deploy_source() -> None:
    packaged = files("phase_loop_runtime").joinpath("deploy/phase-loop-task-message-broker.service").read_text()
    source = (Path(__file__).resolve().parents[2] / "deploy" / "phase-loop-task-message-broker.service").read_text()
    assert packaged == source
