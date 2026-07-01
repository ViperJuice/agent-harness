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
# Input-scaled leg timeout (#36): a FIXED 600s under-ran frontier `xhigh` review on
# large artifacts (codex xhigh is ~900s on ~1.3k lines) — the leg timed out and the
# panel silently degraded to fewer legs (the exact failure mode observed across the
# cross-repo work). Scale the timeout by the staged review size, capped, so large
# reviews get the time they need while small ones stay snappy. Keep --add-dir /
# --output-last-message profile unchanged: the live smoke confirmed those work; the
# fixed timeout was the real regression, not the feeding mechanism.
_LEG_TIMEOUT_BASE_S = 600
_LEG_TIMEOUT_MAX_S = 1800
_LEG_TIMEOUT_PER_KB_S = 12
_LEG_TIMEOUT_S = _LEG_TIMEOUT_BASE_S  # floor / back-compat alias

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
    """Map a leg's exit code + outputs to a fail-closed status.

    Only a leg that ENDS with a conforming structured verdict (see
    ``terminal_verdict``) is a real review (`ok`) — a terse "DISAGREE" counts; a
    long review missing the terminal verdict, or junk that merely mentions the
    words, is NON-CONFORMING and fails closed (`degraded`), never a silent pass.
    """
    if rc == 124:  # `timeout` binary / our own timeout maps here
        return "timeout"
    if _AUTH_SIGNATURE.search(log_text or ""):
        return "degraded"
    body = (review_text or "").strip()
    if not body:
        return "empty"
    if terminal_verdict(body) is not None:
        return "ok"
    # Substantial text but no conforming terminal verdict → fail-closed, not a pass.
    return "degraded"


def _review_bytes(review_dir: Path) -> int:
    """Total byte size of the staged review material — the timeout-scaling input."""
    total = 0
    for path in review_dir.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                pass
    return total


def _leg_timeout_for(review_dir: Path) -> int:
    """Input-scaled per-leg timeout (#36): base + per-KB, capped. A large artifact
    review gets the wall-clock frontier `xhigh` reasoning needs (~900s+); a small one
    stays near the base. Replaces the fixed 600s that silently timed out big reviews."""
    kb = _review_bytes(review_dir) // 1024
    return min(_LEG_TIMEOUT_MAX_S, _LEG_TIMEOUT_BASE_S + kb * _LEG_TIMEOUT_PER_KB_S)


def _exec_leg(leg: str, review_dir: Path, out_dir: Path) -> tuple[int, str, str]:
    """Run one CLI leg against the staged review dir; return (rc, review_text, log_text).

    The single real-subprocess boundary — tests monkeypatch THIS, never spawn a
    frontier CLI. codex's clean review is its `--output-last-message` file (its
    stdout is a noisy transcript); agy's `-p` stdout is the clean response.
    """
    env = _subscription_env()
    timeout_s = _leg_timeout_for(review_dir)
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
                timeout=timeout_s, check=False,
            )
        except subprocess.TimeoutExpired:
            return 124, "", "timeout"
        review_text = out_file.read_text(encoding="utf-8") if out_file.exists() else ""
        return proc.returncode, review_text, (proc.stdout or "") + (proc.stderr or "")
    if leg == "gemini":
        cmd = [
            "agy", "--model", "Gemini 3.1 Pro (High)", "--add-dir", str(review_dir),
            "--print-timeout", f"{timeout_s}s", "-p", _LEG_PROMPT,
        ]
        try:
            proc = subprocess.run(
                cmd, cwd=str(review_dir), env=env, capture_output=True, text=True,
                timeout=timeout_s + 60, check=False,
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
