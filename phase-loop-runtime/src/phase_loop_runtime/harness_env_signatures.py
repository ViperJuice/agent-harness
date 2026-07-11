"""Run-from harness detection (AUTOSEL IF-0-AUTOSEL-1).

When phase-loop is invoked *from inside* an agent harness (e.g. an operator runs
``phase-loop run`` from a Claude Code or Codex session), the surrounding process
leaks env markers that identify that harness. AUTOSEL's layer-2 default-executor
resolution reads those markers to prefer "the harness you're already in" before
falling back to a single-available scan.

Two hazards this module is built around:

* **Self-vs-child leakage.** Those same markers leak into any child process the
  runner spawns — including the executor children phase-loop itself launches. If
  the runner is *under* Claude Code and spawns a codex child, that codex child
  still sees ``CLAUDECODE=1``. Inbound heuristics (``_under_claude_code``) cannot
  tell "I am the operator's Claude Code session" from "I am a codex child of a
  phase-loop that was under Claude Code". So the runner stamps an OUTBOUND
  sentinel (``PHASE_LOOP_CHILD=1``, see :func:`child_executor_env`) on every child
  it spawns, and :func:`detect_run_from_harness` treats the presence of that
  sentinel as authoritative: markers are only adopted when the sentinel is ABSENT.

* **Non-vacuous verification.** A signature map where every harness is
  "unknown/degrade" is useless. Each entry carries a ``verification`` status;
  AUTOSEL requires markers verified for >=2 harnesses (claude-code live + >=1 of
  codex/agy verified in-lane).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

# Outbound self-vs-child sentinel. The runner stamps this on every executor child
# it spawns (:func:`child_executor_env`); its presence means "these host markers
# belong to my parent, not to a real host I should adopt as run-from".
PHASE_LOOP_CHILD_ENV = "PHASE_LOOP_CHILD"

# Claude Code's own session markers. These are the leak-prone ones (they confuse
# ``panel_invoker._under_claude_code`` when they reach a child), so the runner
# scrubs exactly these from every child env in addition to stamping the sentinel.
CLAUDE_CODE_SELF_MARKERS: tuple[str, ...] = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")

# Verification statuses for a signature's markers.
VERIFIED_LIVE = "verified_live"          # captured from a real session on this host
VERIFIED_IN_LANE = "verified_in_lane"    # captured empirically during AUTOSEL lane work
UNKNOWN = "unknown"                      # placeholder, not adoptable (degrades)

_VERIFICATION_STATUSES = frozenset({VERIFIED_LIVE, VERIFIED_IN_LANE, UNKNOWN})


@dataclass(frozen=True)
class HarnessEnvSignature:
    """The env fingerprint of one harness we may be *run from*.

    ``markers`` is OR'd: the signature matches iff ANY (var, expected) pair
    matches (mirroring ``_under_claude_code``'s ``CLAUDECODE`` *or*
    ``CLAUDE_CODE_ENTRYPOINT``). ``expected=None`` means "present and non-empty";
    a string means an exact match after ``strip()``.
    """

    executor: str
    markers: tuple[tuple[str, str | None], ...]
    verification: str
    discriminator_note: str = ""

    def __post_init__(self) -> None:
        if self.verification not in _VERIFICATION_STATUSES:
            raise ValueError(f"unknown verification status: {self.verification!r}")
        if not self.markers:
            raise ValueError(f"signature for {self.executor!r} has no markers")

    def matches(self, env: Mapping[str, str]) -> bool:
        for var, expected in self.markers:
            if _marker_matches(env, var, expected):
                return True
        return False


def _marker_matches(env: Mapping[str, str], var: str, expected: str | None) -> bool:
    raw = env.get(var)
    if raw is None:
        return False
    val = str(raw).strip()
    if expected is None:
        return val != ""
    return val == expected


# The signature map (IF-0-AUTOSEL-1). Ordered by preference: SESSION-SPECIFIC /
# non-leaky markers first (codex's per-session CODEX_THREAD_ID) before LEAKY host
# markers (claude-code's CLAUDECODE, which the module notes leak into children).
# :func:`detect_run_from_harness` returns every match in this order and the
# resolver adopts the first that passes its launch gate — so even if a leaky
# marker also matched, a self-eliminating candidate (e.g. tty-only claude) never
# masks the real run-from harness.
#
# Verified markers (as of AUTOSEL lane a, captured on this host):
#   * claude-code (VERIFIED_LIVE): CLAUDECODE=1, CLAUDE_CODE_ENTRYPOINT=cli,
#     AI_AGENT=claude-code_<ver>_agent. CLAUDECODE=1 is the primary marker; the
#     other two corroborate. These leak to children, so the sentinel matters.
#   * codex (VERIFIED_IN_LANE): CODEX_THREAD_ID=<uuid> is exported into the shell
#     codex runs commands in (i.e. exactly the run-from context — phase-loop
#     invoked from a codex tool-call) and is present for every codex session;
#     CODEX_SANDBOX corroborates on sandboxed hosts. Captured via a real
#     `codex exec 'env'` dump during this lane. (That same dump also showed the
#     host's CLAUDECODE/AI_AGENT markers leaking into the codex child — the live
#     proof that inbound markers cannot self-vs-child disambiguate.)
HARNESS_ENV_SIGNATURES: dict[str, HarnessEnvSignature] = {
    "codex": HarnessEnvSignature(
        executor="codex",
        markers=(("CODEX_THREAD_ID", None), ("CODEX_SANDBOX", None)),
        verification=VERIFIED_IN_LANE,
        discriminator_note=(
            "CODEX_THREAD_ID (per-session UUID) is set in the shell codex runs "
            "commands in (the run-from context); CODEX_SANDBOX corroborates on "
            "sandboxed hosts. Session-specific, so ordered before the leaky claude "
            "markers. Sentinel disambiguates a phase-loop child."
        ),
    ),
    "claude": HarnessEnvSignature(
        executor="claude",
        markers=(("CLAUDECODE", "1"), ("CLAUDE_CODE_ENTRYPOINT", None)),
        verification=VERIFIED_LIVE,
        discriminator_note=(
            "Leaks to children; PHASE_LOOP_CHILD sentinel disambiguates self from "
            "a phase-loop-spawned child. Ordered AFTER codex because these markers "
            "leak into codex children — evaluating codex first avoids adopting a "
            "leaked claude marker over the real run-from harness."
        ),
    ),
}


@dataclass(frozen=True)
class RunFromDetection:
    """Result of run-from detection, carrying provenance for the resolver log.

    ``candidates`` is every signature that matched, in map order (session-specific
    before leaky). The resolver adopts the first candidate that passes its launch
    gate, so a leaky-but-self-eliminating marker never masks the real harness.
    ``executor`` is the first candidate (or ``None``) as a convenience.
    """

    executor: str | None
    reason: str
    candidates: tuple[str, ...] = ()
    matched_verification: str | None = None


def verified_harness_count() -> int:
    """How many signatures carry non-``UNKNOWN`` (adoptable) markers. AUTOSEL's
    non-vacuous criterion requires >=2."""
    return sum(1 for sig in HARNESS_ENV_SIGNATURES.values() if sig.verification != UNKNOWN)


def detect_run_from_harness(env: Mapping[str, str] | None = None) -> RunFromDetection:
    """Detect which harness phase-loop is being run *from*, honoring the
    self-vs-child sentinel.

    Returns ``executor=None`` when we are a phase-loop-spawned child (sentinel
    present) or when no signature matches. Only markers with adoptable
    (non-``UNKNOWN``) verification are ever selected.
    """
    e = os.environ if env is None else env
    if str(e.get(PHASE_LOOP_CHILD_ENV, "")).strip() == "1":
        return RunFromDetection(executor=None, reason="phase_loop_child_sentinel")
    matches: list[tuple[str, str]] = []  # (executor, verification), in map order
    for name, sig in HARNESS_ENV_SIGNATURES.items():
        if sig.verification == UNKNOWN:
            continue
        if sig.matches(e):
            matches.append((sig.executor, sig.verification))
    if not matches:
        return RunFromDetection(executor=None, reason="no_signature_match")
    candidates = tuple(executor for executor, _ in matches)
    return RunFromDetection(
        executor=candidates[0],
        reason="run_from:" + ",".join(candidates),
        candidates=candidates,
        matched_verification=matches[0][1],
    )


def child_executor_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build the env for a phase-loop-owned child executor spawn.

    Scrubs Claude Code's self-markers (so a child never mis-reads the host harness
    as its own context) and stamps the ``PHASE_LOOP_CHILD`` sentinel (so
    :func:`detect_run_from_harness` refuses to adopt any leaked host markers).
    ``base`` is injectable for tests; defaults to the live environment.
    """
    e = dict(os.environ if base is None else base)
    for marker in CLAUDE_CODE_SELF_MARKERS:
        e.pop(marker, None)
    e[PHASE_LOOP_CHILD_ENV] = "1"
    return e
