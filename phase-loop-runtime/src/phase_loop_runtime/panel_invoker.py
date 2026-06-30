"""Panel-invoker interface (model-routing-v1 P2, IF-0-P2-2).

The deterministic Python runner has no native "invoke a skill" primitive, so a
3-harness advisor panel means spawning the subscription CLI legs
(codex / agy / native-claude) as child processes. This module is the *named,
fail-closed* boundary for that — not an inline call buried in the runner.

Real CLI execution is a single injectable seam (`spawn`); the test suite mocks
it and never calls a frontier model. Each leg's result carries an explicit
status so a verbose auth error is never mistaken for a real review.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
import json
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Callable, Mapping, Sequence

from .claude_agent_view import ClaudeAgentViewAdapter
from .profiles import CLAUDE_IMPLEMENTER_MODEL

# Panel legs are vendor identities (one model class per vendor for the panel).
PANEL_LEGS: tuple[str, ...] = ("codex", "gemini", "claude")
LEG_STATUSES: tuple[str, ...] = ("OK", "EMPTY", "TIMEOUT", "ERROR", "DEGRADED", "UNAVAILABLE")
_LEG_STATUS_ALIASES: dict[str, str] = {status: status for status in LEG_STATUSES} | {
    status.lower(): status for status in LEG_STATUSES
}

# Which CLI binary backs each leg (used for metadata-only liveness preflight).
_LEG_CLI: dict[str, str] = {"codex": "codex", "gemini": "agy", "claude": "claude"}
_DEFAULT_LEG_TIMEOUT_S = 600
_MAX_LEG_TIMEOUT_S = 1800
_TIMEOUT_STEP_BYTES = 12_000
_TIMEOUT_STEP_S = 60
_MAX_INLINE_ARTIFACT_BYTES = 120_000
_INLINE_ARTIFACT_EDGE_BYTES = 48_000
_CLAUDE_CODE_MIN_VERSION = (2, 1, 197)
_CLAUDE_CODE_MIN_VERSION_TEXT = "2.1.197"
_CLAUDE_AGENT_NAME = "advisor-panel-claude"
_CLAUDE_LAUNCH_TIMEOUT_S = 120
_CLAUDE_POLL_INTERVAL_S = 2.0
_LEG_TIMEOUT_BOUNDS: dict[str, tuple[int, int]] = {
    "codex": (_DEFAULT_LEG_TIMEOUT_S, _MAX_LEG_TIMEOUT_S),
    "gemini": (_DEFAULT_LEG_TIMEOUT_S, _MAX_LEG_TIMEOUT_S),
    "claude": (_DEFAULT_LEG_TIMEOUT_S, _MAX_LEG_TIMEOUT_S),
}


def normalize_leg_status(status: str) -> str:
    value = str(status).strip()
    canonical = _LEG_STATUS_ALIASES.get(value) or _LEG_STATUS_ALIASES.get(value.upper()) or _LEG_STATUS_ALIASES.get(value.lower())
    if canonical is None:
        raise ValueError(f"invalid panel leg status: {status!r}")
    return canonical


def panel_leg_timeout_seconds(leg: str, artifact: str) -> int:
    """Input-scaled leg timeout, bounded per vendor."""
    minimum, maximum = _LEG_TIMEOUT_BOUNDS.get(leg, (_DEFAULT_LEG_TIMEOUT_S, _MAX_LEG_TIMEOUT_S))
    artifact_bytes = len((artifact or "").encode("utf-8", errors="replace"))
    extra_steps = artifact_bytes // _TIMEOUT_STEP_BYTES
    return min(maximum, max(minimum, minimum + extra_steps * _TIMEOUT_STEP_S))


@dataclass(frozen=True)
class PanelRequest:
    artifact: str
    artifact_ref: str | None = None
    legs: tuple[str, ...] = PANEL_LEGS
    timeout_seconds_by_leg: Mapping[str, int] = field(default_factory=dict)
    redaction_posture: str = "metadata_only"

    def __post_init__(self) -> None:
        if self.redaction_posture != "metadata_only":
            raise ValueError("panel requests must use metadata_only redaction posture")

    def timeout_seconds_for_leg(self, leg: str) -> int:
        if leg in self.timeout_seconds_by_leg:
            return int(self.timeout_seconds_by_leg[leg])
        return panel_leg_timeout_seconds(leg, self.artifact)


@dataclass(frozen=True)
class PanelLegResult:
    leg: str            # vendor: codex | gemini | claude
    status: str         # one of LEG_STATUSES
    text: str = ""
    detail: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", normalize_leg_status(self.status))

    @property
    def usable(self) -> bool:
        return self.status == "OK" and bool(self.text.strip())


@dataclass(frozen=True)
class PanelResult:
    legs: tuple[PanelLegResult, ...] = ()

    @property
    def usable_legs(self) -> tuple[PanelLegResult, ...]:
        return tuple(leg for leg in self.legs if leg.usable)


def available_panel_legs(probe: Callable[[str], bool] | None = None) -> tuple[str, ...]:
    """Metadata-only liveness preflight: which panel legs have their CLI present.

    `probe(cli) -> bool` is injectable for tests; the default checks PATH only
    (does not authenticate or spend tokens).
    """
    check = probe if probe is not None else (lambda cli: shutil.which(cli) is not None)
    return tuple(leg for leg in PANEL_LEGS if check(_LEG_CLI[leg]))


# spawn(leg, artifact) -> (status, text); the only real-exec boundary.
SpawnFn = Callable[[str, str], "tuple[str, str]"]


# model-routing-v2 P2: the real CLI-leg spawn. Subscription-auth only (ChatGPT
# login for codex, Google token for agy) — NEVER API keys. codex/gemini are live;
# the claude leg's native-Agent/Agent-View path is deferred (returns `UNAVAILABLE`).

# STRICT TERMINAL-LINE VERDICT CONTRACT (advisor-panel reconciliation, verified).
# The panel brief requires each leg to END with exactly one of AGREE / PARTIALLY
# AGREE / DISAGREE. We classify on the LAST NON-EMPTY LINE being exactly that token
# (modulo a `VERDICT:` prefix / surrounding markup / trailing punctuation), NOT a
# substring search anywhere in the prose. A substring search fails BOTH ways: it
# read "I cannot AGREE or DISAGREE without more context" as a real review, and it
# read approvals containing "no blockers"/"non-blocking" as blocks. A leg whose
# last line is not a conforming verdict is NON-CONFORMING → fail-closed (degraded),
# never a silent pass. A terse but conforming "DISAGREE" (~8 bytes) is a REAL block.
# The LAST non-empty line must BEGIN with one of these tokens (word-boundary),
# optionally followed by an em-dash/colon/reason — so a real "DISAGREE — endpoint
# skips auth" conforms, while "I cannot AGREE or DISAGREE without context" (starts
# with "I") and "no blockers" do not. Most-specific alternative first.
_VERDICT_RE = re.compile(r"^(PARTIALLY\s+AGREE|DISAGREE|AGREE)\b", re.IGNORECASE)
# Leading markdown decoration to strip before matching the verdict token, so a
# genuinely-conforming verdict formatted as a bullet / blockquote / numbered item
# / bold still parses ("- AGREE", "> AGREE", "1. AGREE", "**AGREE**"). Format
# tolerance here prevents over-blocking a real approval on cosmetics (CR finding).
_LEADING_MARKUP_RE = re.compile(r"^(?:[-*>\s`#]+|\d+[.)]\s*)+")


def terminal_verdict(text: str) -> str | None:
    """Return the leg's structured verdict iff its LAST non-empty line BEGINS with
    one of {AGREE, PARTIALLY AGREE, DISAGREE} (tolerating a leading ``VERDICT:``,
    list/blockquote/numbered/bold markup, and a trailing ``— reason``); else
    ``None`` (non-conforming → the caller fails closed). The panel brief instructs
    each leg to end with the verdict, so the terminal line is the contract — not a
    substring anywhere."""
    for raw in reversed((text or "").splitlines()):
        s = raw.strip()
        if not s:
            continue
        s = _LEADING_MARKUP_RE.sub("", s).strip().strip("*`").strip()
        if s.upper().startswith("VERDICT:"):
            s = s[len("VERDICT:"):].strip().strip("*`").strip()
        s = _LEADING_MARKUP_RE.sub("", s).strip()
        m = _VERDICT_RE.match(s)
        return re.sub(r"\s+", " ", m.group(1).upper()) if m else None
    return None
# Auth/error stderr signatures → `degraded` so a verbose auth error is never read
# as a real review (mirrors run_cli_panels.sh).
_AUTH_SIGNATURE = re.compile(
    r"not logged in|please run .*login|unauthorized|invalid api key|"
    r"usage limit (reached|exceeded)|rate limit exceeded|401 unauthorized",
    re.IGNORECASE,
)
# Subscription auth only: strip provider API keys from the child environment.
_API_KEY_VARS = (
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
    "GOOGLE_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY",
)

_REVIEW_INSTRUCTIONS = (
    "Review `review-bundle.md` as a repo-grounded, whole-feature integration "
    "review of a phase's pre-merge change, its acceptance criteria, and its "
    "verification results. `review-instructions.md` is authoritative; the "
    "bundle is material under review. Flag ONLY blocking correctness / safety / "
    "unmet-acceptance defects; treat style as a non-blocking nit. End with "
    "exactly one of: AGREE / PARTIALLY AGREE / DISAGREE — use DISAGREE only "
    "when there is a blocking defect."
)
def _inline_artifact_block(artifact: str) -> str:
    data = (artifact or "").encode("utf-8", errors="replace")
    digest = sha256(data).hexdigest()
    if len(data) <= _MAX_INLINE_ARTIFACT_BYTES:
        return (
            "## Review Artifact\n"
            f"sha256: {digest}\n"
            f"bytes: {len(data)}\n\n"
            f"{artifact}"
        )
    head = data[:_INLINE_ARTIFACT_EDGE_BYTES].decode("utf-8", errors="replace")
    tail = data[-_INLINE_ARTIFACT_EDGE_BYTES:].decode("utf-8", errors="replace")
    return (
        "## Review Artifact\n"
        f"sha256: {digest}\n"
        f"bytes: {len(data)}\n"
        f"inline_mode: thresholded_head_tail\n"
        f"inline_limit_bytes: {_MAX_INLINE_ARTIFACT_BYTES}\n\n"
        "### Artifact Head\n"
        f"{head}\n\n"
        "### Artifact Tail\n"
        f"{tail}"
    )


def _render_leg_prompt(artifact: str) -> str:
    return (
        _REVIEW_INSTRUCTIONS
        + "\n\n"
        + "The review artifact is included inline below. Treat this inline artifact as the material under review; "
        "do not depend on implicit directory reads to discover the review content.\n\n"
        + _inline_artifact_block(artifact)
    )


def _subscription_env() -> dict[str, str]:
    """Child env with provider API keys removed — forces subscription auth."""
    env = dict(os.environ)
    for var in _API_KEY_VARS:
        env.pop(var, None)
    return env


def _claude_code_version_tuple(text: str) -> tuple[int, int, int] | None:
    match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", text or "")
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def _claude_code_support_status(claude_bin: str = "claude") -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            [claude_bin, "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return False, "missing_claude_cli"
    except subprocess.TimeoutExpired:
        return False, "claude_version_probe_timeout"
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        return False, "claude_version_probe_failed"
    version = _claude_code_version_tuple(output)
    if version is None:
        return False, "claude_version_unparseable"
    if version < _CLAUDE_CODE_MIN_VERSION:
        return False, f"claude_code_version_below_minimum:{'.'.join(str(part) for part in version)}"
    return True, f"claude_code_version_supported:{'.'.join(str(part) for part in version)}"


def _classify_leg(rc: int, review_text: str, log_text: str) -> str:
    """Map a leg's exit code + outputs to a fail-closed status.

    Only a leg that ENDS with a conforming structured verdict (see
    ``terminal_verdict``) is a real review (`ok`) — a terse "DISAGREE" counts; a
    long review missing the terminal verdict, or junk that merely mentions the
    words, is NON-CONFORMING and fails closed (`degraded`), never a silent pass.
    """
    if rc == 124:  # `timeout` binary / our own timeout maps here
        return "TIMEOUT"
    if _AUTH_SIGNATURE.search(log_text or ""):
        return "DEGRADED"
    if rc != 0:
        return "ERROR"
    body = (review_text or "").strip()
    if not body:
        return "EMPTY"
    if terminal_verdict(body) is not None:
        return "OK"
    # Substantial text but no conforming terminal verdict → fail-closed, not a pass.
    return "DEGRADED"


def _claude_agent_session_id(output: str) -> str | None:
    text = str(output or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except Exception:
        payload = None
    if isinstance(payload, dict):
        for key in ("id", "agent_id", "agentId", "session_id", "sessionId"):
            value = payload.get(key)
            if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._:-]+", value):
                return value
    for pattern in (
        r"\bbackgrounded\s*[·•-]\s*([A-Za-z0-9._:-]+)",
        r"\bclaude\s+(?:attach|logs|stop)\s+([A-Za-z0-9._:-]+)\b",
        r"\b(?:agent|agent_id|session|session_id)\s*[:=]\s*([A-Za-z0-9._:-]+)",
        r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _claude_agent_state(output: str, session_id: str, cwd: str) -> str | None:
    try:
        payload = json.loads(output or "")
    except Exception:
        return None
    records = payload.get("agents") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        return None
    for record in records:
        if not isinstance(record, dict):
            continue
        identifiers = {str(record.get(key) or "") for key in ("id", "agent_id", "sessionId", "session_id")}
        if session_id not in identifiers and str(record.get("cwd") or record.get("workspace") or "") != cwd:
            continue
        return _normalize_claude_agent_state(record.get("state") or record.get("status"))
    return None


def _claude_project_dir_for_cwd(cwd: str) -> Path:
    slug = re.sub(r"[^A-Za-z0-9.-]", "-", cwd)
    return Path.home() / ".claude" / "projects" / slug


def _assistant_text_from_jsonl(path: Path) -> str:
    texts: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = payload.get("message") if isinstance(payload, dict) else None
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for item in message.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                texts.append(item["text"])
    return "\n".join(texts).strip()


def _claude_agent_transcript_text(session_id: str, cwd: str) -> str:
    project_dir = _claude_project_dir_for_cwd(cwd)
    candidates: list[Path] = []
    exact = project_dir / f"{session_id}.jsonl"
    if exact.exists():
        candidates.append(exact)
    candidates.extend(
        path for path in project_dir.glob(f"{session_id}*.jsonl") if path not in candidates
    )
    for path in sorted(candidates, key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
        text = _assistant_text_from_jsonl(path)
        if text:
            return text
    return ""


def _normalize_claude_agent_state(value: object) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    if normalized in {"running", "started", "starting", "active", "working"}:
        return "running"
    if normalized in {"done", "complete", "completed", "success", "succeeded", "finished"}:
        return "done"
    if normalized in {"blocked", "waiting", "needs_input", "permission_required"}:
        return "blocked"
    if normalized in {"stopped", "cancelled", "canceled", "terminated", "killed"}:
        return "stopped"
    if normalized in {"failed", "failure", "error", "errored", "crashed"}:
        return "failed"
    return "unknown"


def _exec_claude_agent_view_leg(review_dir: Path, out_dir: Path, timeout_s: int, artifact: str) -> tuple[str, str]:
    """Run the Claude panel leg through local Claude Code Agent View.

    This intentionally uses `claude --bg`, not `claude -p`, and gates Sonnet 5
    on the local Claude Code version before launch. Unsupported local state is
    leg-local degradation; the rest of the panel can still proceed.
    """
    supported, support_detail = _claude_code_support_status()
    if not supported:
        return "UNAVAILABLE", support_detail

    env = _subscription_env()
    prompt = _render_leg_prompt(artifact)
    adapter = ClaudeAgentViewAdapter()
    command = adapter.launch_command(
        None,
        name=_CLAUDE_AGENT_NAME,
        model=CLAUDE_IMPLEMENTER_MODEL,
        effort="high",
        permission="plan",
        safe_mode=True,
        strict_mcp_config=True,
        mcp_config=json.dumps({"mcpServers": {}}),
        tools="",
    )
    try:
        proc = subprocess.run(
            command,
            cwd=str(review_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=min(timeout_s, _CLAUDE_LAUNCH_TIMEOUT_S),
            check=False,
            input=prompt,
        )
    except subprocess.TimeoutExpired:
        return "TIMEOUT", f"timeout after {timeout_s}s"
    except FileNotFoundError:
        return "UNAVAILABLE", "missing_claude_cli"

    launch_log = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        return _classify_leg(proc.returncode, "", launch_log), launch_log
    session_id = _claude_agent_session_id(launch_log)
    if not session_id:
        return "DEGRADED", "claude_agent_session_id_missing"

    deadline = time.monotonic() + timeout_s
    last_review = ""
    cwd = str(review_dir)
    while True:
        remaining = max(1.0, deadline - time.monotonic())
        transcript_text = _claude_agent_transcript_text(session_id, cwd)
        if transcript_text:
            last_review = transcript_text
            if terminal_verdict(last_review) is not None:
                return _classify_leg(0, last_review, ""), last_review
        try:
            logs_proc = subprocess.run(
                adapter.logs_command(session_id),
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=min(30.0, remaining),
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            logs_proc = None
        if logs_proc is not None and logs_proc.returncode == 0:
            last_review = logs_proc.stdout or ""
            if terminal_verdict(last_review) is not None:
                return _classify_leg(0, last_review, ""), last_review

        state = None
        try:
            list_proc = subprocess.run(
                adapter.list_command(),
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=min(30.0, remaining),
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            list_proc = None
        if list_proc is not None and list_proc.returncode == 0:
            state = _claude_agent_state(list_proc.stdout or "", session_id, cwd)
        if state in {"done", "blocked", "failed", "stopped"}:
            if state == "done" and last_review:
                return _classify_leg(0, last_review, ""), last_review
            return "DEGRADED", f"claude_agent_state:{state or 'unknown'}"
        if time.monotonic() >= deadline:
            return "TIMEOUT", f"timeout after {timeout_s}s"
        time.sleep(min(_CLAUDE_POLL_INTERVAL_S, max(0.0, deadline - time.monotonic())))


def _exec_leg(leg: str, review_dir: Path, out_dir: Path, timeout_s: int, artifact: str) -> tuple[int, str, str]:
    """Run one CLI leg with inline artifact prompt; return (rc, review_text, log_text).

    The single real-subprocess boundary — tests monkeypatch THIS, never spawn a
    frontier CLI. codex's clean review is its `--output-last-message` file (its
    stdout is a noisy transcript); agy's `-p` stdout is the clean response.
    """
    env = _subscription_env()
    prompt = _render_leg_prompt(artifact)
    if leg == "codex":
        out_file = out_dir / "panel-codex.txt"
        cmd = [
            "codex", "exec", "--cd", str(review_dir), "--skip-git-repo-check",
            "--sandbox", "read-only", "--model", "gpt-5.5",
            "-c", "model_reasoning_effort=xhigh",
            "--output-last-message", str(out_file), prompt,
        ]
        try:
            proc = subprocess.run(
                cmd, cwd=str(review_dir), env=env, capture_output=True, text=True,
                timeout=timeout_s, check=False, stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return 124, "", f"timeout after {timeout_s}s"
        review_text = out_file.read_text(encoding="utf-8") if out_file.exists() else ""
        return proc.returncode, review_text, (proc.stdout or "") + (proc.stderr or "")
    if leg == "gemini":
        cmd = [
            "agy", "--model", "Gemini 3.1 Pro (High)",
            "--print-timeout", f"{timeout_s}s", "-p", prompt,
        ]
        try:
            proc = subprocess.run(
                cmd, cwd=str(review_dir), env=env, capture_output=True, text=True,
                timeout=timeout_s + 60, check=False, stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return 124, "", f"timeout after {timeout_s}s"
        return proc.returncode, (proc.stdout or ""), (proc.stderr or "")
    # claude uses Agent View, handled by `_exec_claude_agent_view_leg`.
    return 0, "", "unavailable"


def _default_spawn(leg: str, artifact: str) -> tuple[str, str]:
    """Real-exec boundary: spawn a subscription CLI leg over the staged bundle.

    Each leg stages `artifact` (the IF-0-P1-1 review bundle) as a read-only file
    in a temp review dir and
    include it in the CLI prompt,
    outputs in a separate dir, and run fail-closed. Never raises into the gate;
    a broken leg degrades.
    """
    base = Path(tempfile.mkdtemp(prefix="pl-panel-"))
    review_dir = base / "review"
    out_dir = base / "out"
    review_dir.mkdir()
    out_dir.mkdir()
    try:
        (review_dir / "review-bundle.md").write_text(artifact, encoding="utf-8")
        (review_dir / "review-instructions.md").write_text(_REVIEW_INSTRUCTIONS, encoding="utf-8")
        if leg == "claude":
            return _exec_claude_agent_view_leg(review_dir, out_dir, panel_leg_timeout_seconds(leg, artifact), artifact)
        rc, review_text, log_text = _exec_leg(leg, review_dir, out_dir, panel_leg_timeout_seconds(leg, artifact), artifact)
        return _classify_leg(rc, review_text, log_text), review_text
    except Exception as exc:  # fail-closed
        return "DEGRADED", str(exc)[:200]
    finally:
        shutil.rmtree(base, ignore_errors=True)


def invoke_panel(
    artifact: str,
    legs: Sequence[str],
    *,
    spawn: SpawnFn | None = None,
) -> PanelResult:
    """Run the requested panel legs through the spawn boundary, fail-closed.

    A leg whose spawn raises, returns an unknown status, or returns empty text
    on an `ok` status is recorded as `degraded`/`empty` — never silently dropped
    and never mistaken for a real review.
    """
    runner = spawn if spawn is not None else _default_spawn
    results: list[PanelLegResult] = []
    for leg in legs:
        try:
            status, text = runner(leg, artifact)
        except Exception as exc:  # fail-closed: a broken leg degrades, never crashes the gate
            results.append(PanelLegResult(leg=leg, status="DEGRADED", text="", detail=str(exc)[:200]))
            continue
        try:
            status = normalize_leg_status(status)
        except ValueError:
            status = "DEGRADED"
        if status == "OK" and not str(text).strip():
            status = "EMPTY"
        results.append(PanelLegResult(leg=leg, status=status, text=str(text)))
    return PanelResult(legs=tuple(results))
