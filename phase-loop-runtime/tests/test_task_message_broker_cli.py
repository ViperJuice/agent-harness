from __future__ import annotations

import io
import json
from email.message import Message
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from phase_loop_runtime.task_message_broker_client import TaskMessageBrokerClient
from phase_loop_runtime.task_message_resolver import TaskMessageResolverError
from phase_loop_runtime.cli import main


AUTHORITY = "codex-app-server://claw.test"
SHA = "b" * 40


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


def test_remote_plain_http_is_rejected() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        TaskMessageBrokerClient(
            broker_url="http://claw.test:8765",
            bearer_token="token",
            authority=AUTHORITY,
        )


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
    assert response.total_elapsed == 25


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


def test_user_service_is_loopback_digest_only_and_does_not_manage_codex() -> None:
    unit = (Path(__file__).resolve().parents[2] / "deploy" / "phase-loop-task-message-broker.service").read_text()
    assert "--host 127.0.0.1" in unit
    assert "--token-sha256 ${TASK_MESSAGE_TOKEN_SHA256}" in unit
    assert "${AGENT_HARNESS_SHA}" in unit
    assert "Bearer" not in unit
    assert "codex app-server" not in unit
    assert "tailscale" not in unit
