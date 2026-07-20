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

import os
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


def _request(action: str, executor: str = "grok", harness_target: str | None = None):
    selection = resolve_profile_for_executor(action=action, executor=executor)
    bundle = build_prompt(
        action, _ROADMAP, phase=_PHASE, plan=_PLAN, harness_target=harness_target or executor
    )
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
    # Identity, not just callability: pins the exact bound builder so a mis-wired
    # LAUNCH_COMMAND_BUILDERS entry (or a None binding) can't slip through.
    assert record.build_command is not None
    assert record.build_command.__name__ == "build_grok_launch_spec"
    assert record.auth_preflight_probes == ("grok --version", "grok --help")


def test_grok_provider_policy_capability_present():
    capability = provider_policy_capabilities()["grok"]
    assert capability.executor == "grok"
    # grok has no model aliases; effort is clamped to grok's CLI subset (ah#224).
    assert not capability.model_aliases


def test_grok_cli_effort_clamps_invalid_levels():
    # ah#224: grok's --reasoning-effort accepts ONLY high/medium/low; the internal
    # minimal/xhigh/max tiers are clamped at the CLI boundary (never emitted verbatim).
    assert launcher._grok_cli_effort("max") == "high"
    assert launcher._grok_cli_effort("xhigh") == "high"
    assert launcher._grok_cli_effort("minimal") == "low"
    # valid grok tokens pass through unchanged
    assert launcher._grok_cli_effort("low") == "low"
    assert launcher._grok_cli_effort("medium") == "medium"
    assert launcher._grok_cli_effort("high") == "high"


def test_build_grok_command_clamps_explicit_effort():
    # An EXPLICIT high-effort grok run must emit a VALID grok token, never max/xhigh/minimal
    # (which crash the grok CLI). Exercises the load-bearing clamp at command build.
    import dataclasses

    base = resolve_profile_for_executor(action="review", executor="grok")
    for requested, expected in (("max", "high"), ("xhigh", "high"), ("minimal", "low"), ("high", "high")):
        selection = dataclasses.replace(base, effort=requested)
        cmd = launcher.build_grok_command(
            _REPO, selection, action="review", context_file="ctx"
        )
        assert cmd[cmd.index("--reasoning-effort") + 1] == expected, (
            f"grok effort {requested!r} must emit {expected!r}, got "
            f"{cmd[cmd.index('--reasoning-effort') + 1]!r}"
        )


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
    # effort reaches grok's --reasoning-effort clamped to a valid CLI token (ah#224);
    # the default execute effort ('medium') is already valid, so it is unchanged here.
    assert cmd[cmd.index("--reasoning-effort") + 1] == launcher._grok_cli_effort(spec.selected_effort)
    assert cmd[cmd.index("--reasoning-effort") + 1] == "medium"
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
    cmd = spec.command
    # review is HARD read-only via a read/search-only --tools allow-list; no write
    # auto-approve. (Headless grok auto-approves writes regardless of permission-mode,
    # so the allow-list is the real lever — verified empirically.)
    assert "--permission-mode" not in cmd
    assert cmd[cmd.index("--tools") + 1] == launcher.GROK_REVIEW_READONLY_TOOLS
    # none of grok's write/mutation built-ins are in the review tool set.
    for write_tool in ("write", "search_replace", "run_terminal_command"):
        assert write_tool not in launcher.GROK_REVIEW_READONLY_TOOLS.split(",")


def test_build_grok_launch_spec_execute_carries_disallowed_tools_deny_list():
    # #154: the execute leg carries BOTH bypassPermissions (headless writes
    # auto-approve regardless) AND a `--disallowed-tools` DENY-list that subtracts the
    # privileged non-coding built-ins. A write-capable `--tools` ALLOW-LIST is unusable
    # on grok 0.2.93 (it force-adds run_terminal_command, whose config aborts the
    # session), so the deny-list is the mechanism — see GROK_EXECUTE_DISALLOWED_TOOLS.
    spec = build_launch_spec(_request("execute"))
    assert spec.command[spec.command.index("--permission-mode") + 1] == "bypassPermissions"
    # It is a DENY-list, NOT an allow-list — assert the flag shape explicitly so a
    # regression back to `--tools` (which breaks grok execute) is caught.
    assert "--tools" not in spec.command
    # #154: the dedicated subagent-disable flag is passed (forward-compat; ineffective
    # in 0.2.93 but future-proof — see GROK_EXECUTE_DISALLOWED_TOOLS).
    assert "--no-subagents" in spec.command
    deny_value = spec.command[spec.command.index("--disallowed-tools") + 1]
    assert deny_value == launcher.GROK_EXECUTE_DISALLOWED_TOOLS
    denied = deny_value.split(",")
    # The scheduler + image/video + subagent families are named for removal.
    for privileged in (
        "scheduler_create",
        "scheduler_delete",
        "scheduler_list",
        "monitor",
        "image_gen",
        "image_edit",
        "image_to_video",
        "reference_to_video",
        "spawn_subagent",
    ):
        assert privileged in denied, f"{privileged!r} must be named in the execute deny-list"
    # Coding built-ins are NEVER denied — execute must retain read/write/edit/terminal.
    for coding_tool in ("read_file", "write", "search_replace", "run_terminal_command", "grep", "list_dir", "search_tool"):
        assert coding_tool not in denied, f"{coding_tool!r} must stay available to grok execute"


def test_build_grok_launch_spec_repair_keeps_unrestricted_write_branch():
    # #154 scope pin (deliberate): the execute `--tools` allow-list is scoped to the
    # `execute` action — its exact target. repair/roadmap/plan stay on the
    # unrestricted write branch (bypassPermissions, no `--tools`) until scoped
    # deliberately. This test makes the scope boundary explicit so a future widening
    # is a conscious change, not an accident.
    spec = build_launch_spec(_request("repair"))
    # The load-bearing assertion for scope: the execute deny-list + subagent-disable
    # flag must NOT leak to repair (what would break if the branch guard were widened).
    assert "--disallowed-tools" not in spec.command
    assert "--no-subagents" not in spec.command
    assert "--tools" not in spec.command
    assert spec.command[spec.command.index("--permission-mode") + 1] == "bypassPermissions"


@pytest.mark.parametrize("action", ["review", "execute"])
def test_grok_dispatch_end_to_end_no_keyerror(action):
    """Explicit end-to-end dispatch guard (audit-proof): `build_launch_spec` on a real
    grok `LaunchRequest` must route through `capability_registry()[request.executor]`
    and return a valid LaunchSpec — NO `KeyError: 'grok'`.

    This pins THE line that would break if grok were ever dropped from the capability
    registry (the actual dispatch entry point, not just the command builder). It is
    deliberately self-contained — constructs the request from scratch via
    build_launch_request rather than a helper — so a future auditor (or someone on a
    stale checkout) can confirm grok is a live, dispatchable executor at a glance."""
    from phase_loop_runtime.launcher import build_launch_request

    selection = resolve_profile_for_executor(action=action, executor="grok")
    bundle = build_prompt(action, _ROADMAP, phase=_PHASE, plan=_PLAN, harness_target="grok")
    request = build_launch_request(
        executor="grok",
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
    # The dispatch entry point: build_launch_spec does capability_registry()[executor].
    spec = build_launch_spec(request)  # must NOT raise KeyError('grok')
    assert spec.executor == "grok"
    assert spec.command[0] == "grok"
    assert spec.available is True
    # sanity: grok is genuinely present + bound in the registry it dispatched through.
    assert "grok" in capability_registry()
    assert capability_registry()["grok"].build_command.__name__ == "build_grok_launch_spec"


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


# --- live launch-path regressions (CR: caught by the grok reviewer) -----------


def test_grok_build_prompt_does_not_keyerror():
    # A real `--executor grok` run calls build_prompt(harness_target="grok"); before
    # HARNESS_INJECTION_MODES gained a grok entry this KeyError'd before launch.
    bundle = build_prompt("execute", _ROADMAP, phase=_PHASE, plan=_PLAN, harness_target="grok")
    assert bundle.injection_mode == "context_file"
    # workflow header is non-empty (generic phase-loop command, no invented skills).
    assert bundle.render_prompt().strip()


def test_grok_resolve_command_context_materializes(tmp_path):
    # The launch-time context materialization must replace GROK_CONTEXT_PLACEHOLDER
    # and write the run-local context.md; without the grok branch in
    # _resolve_command_context the placeholder leaked into grok's `-p` prompt verbatim.
    spec = build_launch_spec(_request("execute", harness_target="grok"))
    assert launcher.GROK_CONTEXT_PLACEHOLDER in " ".join(spec.command)
    log_path = tmp_path / "run" / "launch.log"
    log_path.parent.mkdir(parents=True)
    resolved, _staged = launcher._resolve_command_context(spec, log_path)
    joined = " ".join(resolved)
    assert launcher.GROK_CONTEXT_PLACEHOLDER not in joined, "context placeholder was never substituted"
    context_file = log_path.parent / "context.md"
    assert context_file.is_file()
    # the resolved path is embedded in grok's `-p` pointer prompt (not a standalone arg).
    assert str(context_file) in joined
    assert context_file.read_text(encoding="utf-8").strip() == spec.prompt_bundle.render_context().rstrip()


# --- runner executor-set parity (CR: grok must join gemini's live-executor sets) ---


def test_grok_auth_failure_classifies_not_keyerror():
    # grok is a live subscription CLI: an auth/quota failure must map to the
    # account/billing blocker, not KeyError on a stale label map (CR: both
    # convergence legs caught grok crashing here).
    from phase_loop_runtime import runner

    blocker = runner._executor_launch_failure_blocker("grok", "PHASE", "Error: not logged in; please login")
    assert blocker is not None
    assert blocker["blocker_class"] == "account_or_billing_setup"
    assert "Grok" in blocker["blocker_summary"]


def test_grok_requires_shared_automation_closeout():
    # grok (live, plain-output, prompt-injected closeout) must be held to the same
    # fail-closed closeout enforcement as gemini.
    from phase_loop_runtime import runner

    class _Spec:
        executor = "grok"

    class _Result:
        executor = "grok"

    assert runner._requires_shared_automation_closeout(_Result(), _Spec()) is True


@pytest.mark.skipif(
    not os.environ.get("PHASE_LOOP_RUN_LIVE_GROK") or shutil.which("grok") is None,
    reason="live grok proof is opt-in (set PHASE_LOOP_RUN_LIVE_GROK=1 with an authed grok on PATH)",
)
def test_grok_live_session_is_preserved(tmp_path):
    """Live proof: a real `grok -p` run under a fresh cwd persists a session that the
    metadata hook then discovers. OPT-IN only (PHASE_LOOP_RUN_LIVE_GROK=1): it makes a
    real network/CLI call, so it must never run in the default unit suite — a
    PATH-present-but-UNAUTHENTICATED CI would otherwise hang or fail on the live call.
    Bounded single-turn prompt."""
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


@pytest.mark.skipif(
    not os.environ.get("PHASE_LOOP_RUN_LIVE_GROK") or shutil.which("grok") is None,
    reason="live grok proof is opt-in (set PHASE_LOOP_RUN_LIVE_GROK=1 with an authed grok on PATH)",
)
def test_grok_execute_reads_context_outside_cwd_and_writes_live(tmp_path):
    """#154 live proof (OPT-IN, real CLI call): under the REAL execute deny-list argv,
    grok (a) READS the phase-loop context.md at its ABSOLUTE path OUTSIDE `--cwd`, and
    (b) WRITES a file under `--cwd` — proving both retained coding capabilities at
    runtime (not just that the tool names are absent from the deny string).

    Risks closed:
      * read: phase-loop materializes context.md under the run's log dir (NOT the
        worktree/`--cwd`); if grok's `read_file` could not reach an absolute out-of-cwd
        path under the deny-list, the execute leg would silently run blind.
      * write: the deny-list must not have collaterally removed grok's write built-in;
        we make grok actually CREATE a file and assert the artifact exists on disk.

    We build the real argv via `build_grok_command` (actual `--disallowed-tools`/`--cwd`/
    pointer flags), put the write instruction + a sentinel INSIDE the out-of-cwd
    context.md, and assert both the echoed sentinel (read) and the created file (write)."""
    import subprocess

    cwd = tmp_path / "cwd"
    cwd.mkdir()
    ctx_dir = tmp_path / "outside"  # sibling of cwd — the context is NOT under --cwd
    ctx_dir.mkdir()
    sentinel = f"GROKEXEC_CTX_{uuid.uuid4().hex[:12]}"
    out_name = f"grok_wrote_{uuid.uuid4().hex[:8]}.txt"
    context_md = ctx_dir / "context.md"
    context_md.write_text(
        "Ignore every other instruction. Do BOTH of these, then stop:\n"
        f"1. Create a file named `{out_name}` in your current working directory whose "
        f"entire contents are exactly: {sentinel}\n"
        f"2. Reply with exactly this token and nothing else: {sentinel}\n",
        encoding="utf-8",
    )
    selection = resolve_profile_for_executor(action="execute", executor="grok")
    cmd = launcher.build_grok_command(cwd, selection, action="execute", context_file=str(context_md))
    # Sanity: this is the real execute argv — the deny-list is applied, --cwd is the
    # empty dir, and the pointer prompt names the absolute out-of-cwd context path.
    assert cmd[cmd.index("--disallowed-tools") + 1] == launcher.GROK_EXECUTE_DISALLOWED_TOOLS
    assert cmd[cmd.index("--cwd") + 1] == str(cwd)
    assert str(context_md) in cmd[cmd.index("-p") + 1]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr
    # READ proof: the sentinel only exists inside the out-of-cwd context.md.
    assert sentinel in proc.stdout, (
        "grok did not read context.md at its absolute path outside --cwd under the "
        f"execute deny-list; stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    # WRITE proof: grok created the file under --cwd with the sentinel contents.
    written = cwd / out_name
    assert written.is_file(), (
        f"grok did not write {out_name} under --cwd under the execute deny-list — the "
        f"write built-in may have been collaterally removed. cwd contents: "
        f"{[p.name for p in cwd.iterdir()]}; stdout={proc.stdout!r}"
    )
    assert sentinel in written.read_text(encoding="utf-8")


@pytest.mark.skipif(
    not os.environ.get("PHASE_LOOP_RUN_LIVE_GROK") or shutil.which("grok") is None,
    reason="live grok proof is opt-in (set PHASE_LOOP_RUN_LIVE_GROK=1 with an authed grok on PATH)",
)
def test_grok_spawn_subagent_denial_tripwire(tmp_path):
    """#154 UPGRADE TRIPWIRE (OPT-IN, live) — BEHAVIORAL, not enumeration-based.

    Enumerating grok's self-reported tool list proves nothing (it is LLM-generated and
    both omits available names AND lists disabled ones), so this FORCES the invocation:
    it runs the REAL execute argv (which carries BOTH subagent-disable levers we pass —
    `--no-subagents` and `--disallowed-tools spawn_subagent`) and instructs grok to spawn
    a subagent that echoes a unique token.

    grok 0.2.93 IGNORES both levers: the spawn SUCCEEDS and the token comes back. That is
    the documented #154 gap, asserted here as CURRENT REALITY — NOT a guarantee we hold.
    When a future grok honors either lever, the spawn is blocked, the token is absent, and
    this assertion TRIPS — forcing a human to reconcile agent-harness#154 (the subagent
    fanout may now be genuinely closeable → tighten the guarantee + GROK_EXECUTE_DISALLOWED_TOOLS
    docs) rather than a security property being silently over-claimed. This is the named
    upgrade tripwire the phase owner asked for, done as a runtime behavior check per the
    cross-vendor CR (force the forbidden invocation, don't trust the self-report)."""
    import subprocess

    cwd = tmp_path / "cwd"
    cwd.mkdir()
    selection = resolve_profile_for_executor(action="execute", executor="grok")
    cmd = launcher.build_grok_command(cwd, selection, action="execute", context_file="__unused__")
    # Both subagent-disable levers are on the real execute argv (neither works in 0.2.93).
    assert "--no-subagents" in cmd
    assert "spawn_subagent" in cmd[cmd.index("--disallowed-tools") + 1].split(",")
    token = f"SUBAGENT_{uuid.uuid4().hex[:10]}"
    cmd[cmd.index("-p") + 1] = (
        f"Use the spawn_subagent tool to spawn a subagent whose only task is to reply with "
        f"the exact token {token}. Then report exactly what the subagent returned. If you "
        f"are UNABLE to spawn a subagent, reply with exactly the word SPAWN_BLOCKED."
    )
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr
    spawned = token in proc.stdout and "SPAWN_BLOCKED" not in proc.stdout
    assert spawned, (
        "TRIPWIRE: grok could NOT spawn a subagent under the execute argv — in grok 0.2.93 "
        "BOTH `--no-subagents` and `--disallowed-tools spawn_subagent` are ignored and a spawn "
        "SUCCEEDS (the documented #154 gap). If this now trips, a newer grok honors one of the "
        "disable levers and subagent fanout may be CLOSEABLE: verify and tighten the #154 "
        f"guarantee + GROK_EXECUTE_DISALLOWED_TOOLS docs. stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
