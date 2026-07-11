"""Executor availability + auth gates (EXECREG IF-0-EXECREG-1, lane c).

Provides the two boolean gates that hang off ``ExecutorCapabilityRecord`` as
``is_available`` / ``auth_ok`` callables:

* ``is_executor_available`` — a PATH probe (``shutil.which`` on the executor's
  CLI binary), mirroring the advisor-board availability pattern
  (``advisor_board/registries.py``). Metadata-only, no subprocess.
* ``auth_ok_for`` — derived from the record's existing ``auth_preflight_probes``
  (the same command tuples ``run_auth_preflight`` uses), **cached with a bounded
  TTL** so it is not re-run on every dispatch.

Both gates are **dormant** on the execute path until AUTOSEL wires them into
default-executor resolution — EXECREG only makes them present, correct, and
testable. The cache/memo lives here (module level, keyed by executor); the frozen
record only holds a thin closure, never mutable state.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from typing import Callable

# Executor -> availability-probe CLI binary. Mirrors the advisor-board harness
# registry (codex->codex, gemini->agy, grok->grok, ...). ``command`` and
# ``manual`` shell out to no named CLI, so they have no PATH-probe binary.
_EXECUTOR_CLI: dict[str, str] = {
    "codex": "codex",
    "claude": "claude",
    "gemini": "agy",
    "opencode": "opencode",
    "pi": "pi",
    "grok": "grok",
}


def executor_cli(executor: str) -> str | None:
    """The CLI binary an executor is PATH-probed for, or ``None`` (command/manual)."""
    return _EXECUTOR_CLI.get(executor)


def is_executor_available(executor: str, *, which: Callable[[str], object | None] = shutil.which) -> bool:
    """True iff the executor's CLI binary is on PATH. Executors with no external
    CLI (``command`` / ``manual``) report ``False`` (never a crash). ``which`` is
    injectable so a test can simulate an empty PATH."""
    cli = _EXECUTOR_CLI.get(executor)
    if cli is None:
        return False
    return which(cli) is not None


# --- auth_ok: cached, bounded probe gate -----------------------------------

_AUTH_TTL_SECONDS = 300.0
# (executor, probes) -> (captured_at_monotonic, ok). Keyed by the probe tuple too,
# so a call with a changed probe set never reuses a prior executor's verdict.
_auth_cache: dict[tuple[str, tuple[str, ...]], tuple[float, bool]] = {}


def _run_probe(probe: str) -> subprocess.CompletedProcess:
    return subprocess.run(probe, shell=True, text=True, capture_output=True, check=False)


def _probes_pass(executor: str, probes: tuple[str, ...], runner: Callable[[str], subprocess.CompletedProcess]) -> bool:
    """All probes must exit 0. For codex/claude the login-status probe must also
    report an authenticated session (mirroring ``run_auth_preflight``'s core).
    Deeper auth semantics stay in ``run_auth_preflight`` at launch — this is the
    cheap cached gate AUTOSEL scans with."""
    if not probes:
        # No probe surface (gemini/pi already only version+help; command/manual
        # have none) — nothing to fail, treat as authed-if-reachable.
        return True
    outputs: dict[str, str] = {}
    for probe in probes:
        completed = runner(probe)
        if completed.returncode != 0:
            return False
        outputs[probe] = ((completed.stdout or "") + " " + (completed.stderr or "")).strip().lower()
    if executor == "codex":
        return "logged in" in outputs.get("codex login status", "")
    if executor == "claude":
        status = outputs.get("claude auth status", "")
        return '"loggedin": true' in status.replace(" ", "") or '"loggedin":true' in status.replace(" ", "")
    return True


def auth_ok_for(
    executor: str,
    probes: tuple[str, ...],
    *,
    now: float | None = None,
    ttl_seconds: float = _AUTH_TTL_SECONDS,
    runner: Callable[[str], subprocess.CompletedProcess] = _run_probe,
) -> bool:
    """Cached, bounded auth gate for an executor. Re-runs the probes only after
    the TTL elapses; within the window returns the cached verdict."""
    stamp = time.monotonic() if now is None else now
    key = (executor, tuple(probes))
    cached = _auth_cache.get(key)
    if cached is not None and (stamp - cached[0]) < ttl_seconds:
        return cached[1]
    ok = _probes_pass(executor, probes, runner)
    _auth_cache[key] = (stamp, ok)
    return ok


def clear_auth_cache() -> None:
    """Test hook — drop the memoized auth verdicts."""
    _auth_cache.clear()
