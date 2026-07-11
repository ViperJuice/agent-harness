"""EXECREG lane (c) — is_available (PATH probe) + auth_ok (cached, bounded)."""
from __future__ import annotations

import subprocess

from phase_loop_runtime import executor_availability as ea
from phase_loop_runtime.capability_registry import capability_registry


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args="probe", returncode=returncode, stdout=stdout, stderr=stderr)


# --- is_available ----------------------------------------------------------

def test_is_available_false_when_binary_absent():
    assert ea.is_executor_available("codex", which=lambda _cli: None) is False


def test_is_available_true_when_binary_present():
    assert ea.is_executor_available("codex", which=lambda _cli: "/usr/bin/codex") is True


def test_is_available_false_for_executors_without_cli():
    # command / manual shell out to no named CLI -> False, never a crash.
    assert ea.is_executor_available("command", which=lambda _cli: "/anything") is False
    assert ea.is_executor_available("manual", which=lambda _cli: "/anything") is False


def test_is_available_probes_the_right_binary():
    seen: list[str] = []

    def which(cli):
        seen.append(cli)
        return None

    ea.is_executor_available("gemini", which=which)  # gemini -> agy
    assert seen == ["agy"]


def test_record_is_available_callable_bound_and_safe():
    # The bound record closure never crashes when the binary is absent.
    record = capability_registry()["pi"]
    assert callable(record.is_available)
    assert isinstance(record.is_available(), bool)


# --- auth_ok ---------------------------------------------------------------

def test_auth_ok_true_when_all_probes_pass():
    ea.clear_auth_cache()
    probes = ("agy --version", "agy --help")
    assert ea.auth_ok_for("gemini", probes, runner=lambda _p: _completed(0)) is True


def test_auth_ok_false_when_a_probe_fails():
    ea.clear_auth_cache()
    probes = ("agy --version", "agy --help")

    def runner(probe):
        return _completed(0) if probe.endswith("--version") else _completed(1)

    assert ea.auth_ok_for("gemini", probes, runner=runner) is False


def test_auth_ok_codex_requires_logged_in_status():
    ea.clear_auth_cache()
    probes = ("codex --version", "codex --help", "codex login status")

    def logged_in(probe):
        return _completed(0, stdout="Logged in as user") if "login status" in probe else _completed(0)

    def logged_out(probe):
        return _completed(0, stdout="Not authenticated") if "login status" in probe else _completed(0)

    assert ea.auth_ok_for("codex", probes, runner=logged_in) is True
    ea.clear_auth_cache()
    assert ea.auth_ok_for("codex", probes, runner=logged_out) is False


def test_auth_ok_honors_cache_bound():
    ea.clear_auth_cache()
    probes = ("agy --version",)
    calls = {"n": 0}

    def runner(_probe):
        calls["n"] += 1
        return _completed(0)

    # First call at t=0 runs the probe; a second within TTL reuses the cache.
    assert ea.auth_ok_for("gemini", probes, now=0.0, ttl_seconds=300.0, runner=runner) is True
    assert ea.auth_ok_for("gemini", probes, now=100.0, ttl_seconds=300.0, runner=runner) is True
    assert calls["n"] == 1, "auth_ok re-ran probes inside the TTL window (cache not honored)"

    # After the TTL elapses the probe re-runs.
    assert ea.auth_ok_for("gemini", probes, now=400.0, ttl_seconds=300.0, runner=runner) is True
    assert calls["n"] == 2, "auth_ok did not re-run probes after the TTL bound elapsed"


def test_auth_ok_cache_keyed_by_probes_not_just_executor():
    # CR regression (codex minor): a call with a DIFFERENT probe tuple must not
    # reuse a prior verdict cached under the same executor.
    ea.clear_auth_cache()
    passing = ("agy --version",)
    failing = ("agy --version", "agy login status")

    assert ea.auth_ok_for("gemini", passing, now=0.0, runner=lambda _p: _completed(0)) is True
    # Same executor, different probes that fail -> must re-evaluate, not reuse True.
    def runner(probe):
        return _completed(0) if probe.endswith("--version") else _completed(1)

    assert ea.auth_ok_for("gemini", failing, now=1.0, runner=runner) is False


def test_record_auth_ok_callable_bound():
    record = capability_registry()["codex"]
    assert callable(record.auth_ok)
