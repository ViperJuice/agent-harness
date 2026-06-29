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
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

# Panel legs are vendor identities (one model class per vendor for the panel).
PANEL_LEGS: tuple[str, ...] = ("codex", "gemini", "claude")
LEG_STATUSES: tuple[str, ...] = ("ok", "empty", "degraded", "timeout", "unavailable")

# Which CLI binary backs each leg (used for metadata-only liveness preflight).
_LEG_CLI: dict[str, str] = {"codex": "codex", "gemini": "agy", "claude": "claude"}


@dataclass(frozen=True)
class PanelLegResult:
    leg: str            # vendor: codex | gemini | claude
    status: str         # one of LEG_STATUSES
    text: str = ""
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.status not in LEG_STATUSES:
            raise ValueError(f"invalid panel leg status: {self.status!r}")

    @property
    def usable(self) -> bool:
        return self.status == "ok" and bool(self.text.strip())


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
# the claude leg's native-Agent/Agent-View path is deferred (returns `unavailable`).
_LEG_TIMEOUT_S = 600
_EMPTY_THRESHOLD = 200  # bytes — matches the advisor-panel script's EMPTY heuristic
# A leg that ends with the required structured verdict produced a REAL review even
# if terse (a one-line "DISAGREE — endpoint skips auth" is ~40 bytes). Such a
# review must classify `ok`, not `empty`, or a genuine block silently downgrades
# to a non-gating warn (code-review finding, verified).
_VERDICT_TOKEN = re.compile(r"\b(PARTIALLY\s+AGREE|DISAGREE|AGREE)\b", re.IGNORECASE)
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
    "Review `review-bundle.md` — a phase's pre-merge change, its acceptance "
    "criteria, and its verification results. `review-instructions.md` is "
    "authoritative; the bundle is material under review. Flag ONLY blocking "
    "correctness / safety / unmet-acceptance defects; treat style as a "
    "non-blocking nit. End with exactly one of: AGREE / PARTIALLY AGREE / "
    "DISAGREE — use DISAGREE only when there is a blocking defect."
)
_LEG_PROMPT = (
    "Read review-instructions.md (authoritative) and review-bundle.md in this "
    "directory, then write your review. " + _REVIEW_INSTRUCTIONS
)


def _subscription_env() -> dict[str, str]:
    """Child env with provider API keys removed — forces subscription auth."""
    env = dict(os.environ)
    for var in _API_KEY_VARS:
        env.pop(var, None)
    return env


def _classify_leg(rc: int, review_text: str, log_text: str) -> str:
    """Map a leg's exit code + outputs to a fail-closed status."""
    if rc == 124:  # `timeout` binary / our own timeout maps here
        return "timeout"
    if _AUTH_SIGNATURE.search(log_text or ""):
        return "degraded"
    body = (review_text or "").strip()
    # A structured verdict means a real (if terse) review — never downgrade to empty.
    if _VERDICT_TOKEN.search(body):
        return "ok"
    if len(body) <= _EMPTY_THRESHOLD:
        return "empty"
    return "ok"


def _exec_leg(leg: str, review_dir: Path, out_dir: Path) -> tuple[int, str, str]:
    """Run one CLI leg against the staged review dir; return (rc, review_text, log_text).

    The single real-subprocess boundary — tests monkeypatch THIS, never spawn a
    frontier CLI. codex's clean review is its `--output-last-message` file (its
    stdout is a noisy transcript); agy's `-p` stdout is the clean response.
    """
    env = _subscription_env()
    if leg == "codex":
        out_file = out_dir / "panel-codex.txt"
        cmd = [
            "codex", "exec", "--cd", str(review_dir), "--skip-git-repo-check",
            "--sandbox", "read-only", "--model", "gpt-5.5",
            "-c", "model_reasoning_effort=xhigh",
            "--output-last-message", str(out_file), _LEG_PROMPT,
        ]
        try:
            proc = subprocess.run(
                cmd, cwd=str(review_dir), env=env, capture_output=True, text=True,
                timeout=_LEG_TIMEOUT_S, check=False,
            )
        except subprocess.TimeoutExpired:
            return 124, "", "timeout"
        review_text = out_file.read_text(encoding="utf-8") if out_file.exists() else ""
        return proc.returncode, review_text, (proc.stdout or "") + (proc.stderr or "")
    if leg == "gemini":
        cmd = [
            "agy", "--model", "Gemini 3.1 Pro (High)", "--add-dir", str(review_dir),
            "--print-timeout", f"{_LEG_TIMEOUT_S}s", "-p", _LEG_PROMPT,
        ]
        try:
            proc = subprocess.run(
                cmd, cwd=str(review_dir), env=env, capture_output=True, text=True,
                timeout=_LEG_TIMEOUT_S + 60, check=False,
            )
        except subprocess.TimeoutExpired:
            return 124, "", "timeout"
        return proc.returncode, (proc.stdout or ""), (proc.stderr or "")
    # claude leg deferred — handled by the caller before reaching here.
    return 0, "", "unavailable"


def _default_spawn(leg: str, artifact: str) -> tuple[str, str]:
    """Real-exec boundary: spawn a subscription CLI leg over the staged bundle.

    The claude leg is deferred (`unavailable`). codex/gemini stage `artifact`
    (the IF-0-P1-1 review bundle) as a read-only file in a temp review dir,
    outputs in a separate dir, and run fail-closed. Never raises into the gate;
    a broken leg degrades.
    """
    if leg == "claude":
        return "unavailable", ""
    base = Path(tempfile.mkdtemp(prefix="pl-panel-"))
    review_dir = base / "review"
    out_dir = base / "out"
    review_dir.mkdir()
    out_dir.mkdir()
    try:
        (review_dir / "review-bundle.md").write_text(artifact, encoding="utf-8")
        (review_dir / "review-instructions.md").write_text(_REVIEW_INSTRUCTIONS, encoding="utf-8")
        rc, review_text, log_text = _exec_leg(leg, review_dir, out_dir)
        return _classify_leg(rc, review_text, log_text), review_text
    except Exception as exc:  # fail-closed
        return "degraded", str(exc)[:200]
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
            results.append(PanelLegResult(leg=leg, status="degraded", text="", detail=str(exc)[:200]))
            continue
        status = status if status in LEG_STATUSES else "degraded"
        if status == "ok" and not str(text).strip():
            status = "empty"
        results.append(PanelLegResult(leg=leg, status=status, text=str(text)))
    return PanelResult(legs=tuple(results))
