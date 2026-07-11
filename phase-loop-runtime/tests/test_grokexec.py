"""GROKEXEC (EXECDISPATCH Phase 1) — grok as a pure registry-entry addition.

grok joins ``EXECUTORS`` as an additive capability record + build fn + provider
policy + session-preservation hook, with ZERO dispatch/if-branch edits (that is
EXECREG's zero-dispatch-edit proof, guarded separately in
``test_execreg_launch_registry``). These tests pin:

  * grok is a registered, build_command-bound executor;
  * ``build_grok_launch_spec`` renders the expected headless ``grok -p`` argv
    (context-file delivery, effort passthrough, write-vs-review permission mode);
  * the metadata-only session-preservation hook resolves grok's
    ``~/.grok/sessions/<url-encoded-cwd>/<uuid>/events.jsonl`` layout to a
    path + event_count (never raw bodies), including a live grok run when the CLI
    is on PATH.
"""
from __future__ import annotations

import shutil
import urllib.parse
import uuid
from pathlib import Path

import pytest

from phase_loop_runtime import launcher
from phase_loop_runtime.capability_registry import (
    capability_registry,
    provider_policy_capabilities,
)
from phase_loop_runtime.launcher import (
    build_launch_request,
    build_launch_spec,
    grok_session_transcript,
)
from phase_loop_runtime.models import EXECUTORS
from phase_loop_runtime.profiles import GROK_DEFAULT_MODEL, resolve_profile_for_executor
from phase_loop_runtime.prompts import build_prompt

_REPO = Path("/repo")
_ROADMAP = Path("/repo/specs/phase-plans-v1.md")
_PLAN = Path("/repo/plans/phase-plan-v1-ADAPTER.md")
_PHASE = "ADAPTER"


def _request(action: str, executor: str = "grok"):
    selection = resolve_profile_for_executor(action=action, executor=executor)
    bundle = build_prompt(action, _ROADMAP, phase=_PHASE, plan=_PLAN)
    return build_launch_request(
        executor=executor,
        action=action,
        repo=_REPO,
        roadmap=_ROADMAP,
        phase=_PHASE,
        plan=_PLAN,
        model_selection=selection,
        prompt_bundle=bundle,
        json_output=True,
        bypass_approvals=False,
        launch_timeout_seconds=1800,
    )


# --- registry-entry addition ------------------------------------------------


def test_grok_is_a_registered_executor():
    assert "grok" in EXECUTORS


def test_grok_capability_record_present_and_bound():
    record = capability_registry()["grok"]
    assert record.executor == "grok"
    assert record.injection_mode == "context_file"
    assert record.build_command is not None and callable(record.build_command)
    assert record.auth_preflight_probes == ("grok --version", "grok --help")


def test_grok_provider_policy_capability_present():
    capability = provider_policy_capabilities()["grok"]
    assert capability.executor == "grok"
    # grok's CLI accepts the full normalized effort set — no clamp/aliases.
    assert not capability.model_aliases


def test_grok_default_model_resolves_from_profiles():
    selection = resolve_profile_for_executor(action="execute", executor="grok")
    assert selection.model == GROK_DEFAULT_MODEL == "grok-4.5"


# --- build_grok_launch_spec argv --------------------------------------------


def test_build_grok_launch_spec_write_action_argv():
    spec = build_launch_spec(_request("execute"))
    assert spec.executor == "grok"
    cmd = spec.command
    assert cmd[0] == "grok"
    assert "-p" in cmd
    assert cmd[cmd.index("--output-format") + 1] == "plain"
    assert cmd[cmd.index("--cwd") + 1] == str(_REPO)
    assert cmd[cmd.index("-m") + 1] == GROK_DEFAULT_MODEL
    # effort passes straight through to grok's --reasoning-effort (no clamp).
    assert cmd[cmd.index("--reasoning-effort") + 1] == spec.selected_effort
    # write action auto-approves.
    assert cmd[cmd.index("--permission-mode") + 1] == "bypassPermissions"
    # context-file delivery: the prompt points at the staged bundle placeholder.
    assert launcher.GROK_CONTEXT_PLACEHOLDER in cmd[cmd.index("-p") + 1]
    # The record declares context_file injection (capability level); the per-request
    # delivery_mode follows the prompt bundle, exactly like the gemini executor.
    assert capability_registry()["grok"].injection_mode == "context_file"
    assert spec.delivery_mode == spec.injection_metadata.injection_mode


def test_build_grok_launch_spec_review_is_read_only():
    spec = build_launch_spec(_request("review"))
    # review omits the write auto-approve permission mode (read-only, like gemini).
    assert "--permission-mode" not in spec.command


# --- session-preservation hook (metadata only) ------------------------------


def _fabricate_grok_session(root: Path, cwd: Path, events: list[str]) -> Path:
    encoded = urllib.parse.quote(str(cwd), safe="")
    session_dir = root / encoded / str(uuid.uuid4())
    session_dir.mkdir(parents=True)
    events_path = session_dir / "events.jsonl"
    events_path.write_text("\n".join(events) + "\n", encoding="utf-8")
    return events_path


def test_grok_session_transcript_reads_metadata_only(tmp_path):
    root = tmp_path / "sessions"
    events_path = _fabricate_grok_session(
        root, _REPO, ['{"type":"user"}', '{"type":"assistant"}', '{"type":"result"}']
    )
    result = grok_session_transcript(_request("execute"), sessions_root=root)
    assert result is not None
    assert result["executor"] == "grok"
    assert result["events_path"] == str(events_path)
    assert result["session_dir"] == str(events_path.parent)
    assert result["event_count"] == 3
    # METADATA ONLY: no raw event body is surfaced.
    assert set(result) == {"executor", "session_dir", "events_path", "event_count"}


def test_grok_session_transcript_none_when_absent(tmp_path):
    assert grok_session_transcript(_request("execute"), sessions_root=tmp_path) is None


def test_grok_session_transcript_picks_newest(tmp_path):
    root = tmp_path / "sessions"
    old = _fabricate_grok_session(root, _REPO, ['{"a":1}'])
    new = _fabricate_grok_session(root, _REPO, ['{"a":1}', '{"b":2}'])
    # Make `new` unambiguously newer (the hook sorts on the events.jsonl mtime).
    import os

    os.utime(old, (1, 1))
    os.utime(new, (10_000_000, 10_000_000))
    result = grok_session_transcript(_request("execute"), sessions_root=root)
    assert result["events_path"] == str(new)
    assert result["event_count"] == 2


def test_grok_session_transcript_hook_bound_on_record():
    record = capability_registry()["grok"]
    assert record.get_session_transcript is grok_session_transcript
    # No other executor registers a transcript hook yet.
    for name in EXECUTORS:
        if name != "grok":
            assert capability_registry()[name].get_session_transcript is None


@pytest.mark.skipif(shutil.which("grok") is None, reason="grok CLI not on PATH")
def test_grok_live_session_is_preserved(tmp_path):
    """Live proof: a real `grok -p` run under a fresh cwd persists a session that the
    metadata hook then discovers. Bounded single-turn prompt; skipped where grok is
    absent so CI without the CLI stays green."""
    import subprocess

    cwd = tmp_path / "live"
    cwd.mkdir()
    proc = subprocess.run(
        ["grok", "-p", "Reply with the single word OK.", "--output-format", "plain", "--cwd", str(cwd)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr

    class _Req:
        repo = cwd

    result = grok_session_transcript(_Req())
    assert result is not None, "grok live run left no discoverable session under ~/.grok/sessions"
    assert result["executor"] == "grok"
    assert result["event_count"] >= 1
    assert Path(result["events_path"]).is_file()
