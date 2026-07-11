"""EXECREG (IF-0-EXECREG-1) — registry-driven launch, no hardcoded branch.

Covers the structural half of the exit criteria:
  * ``ExecutorCapabilityRecord`` carries the new callable fields, all optional.
  * ``capability_registry()`` binds ``build_command`` for every executor.
  * ``build_launch_spec`` delegates to ``record.build_command`` and contains **no**
    ``if request.executor == "<literal>"`` runnable-command branch (AST lint, with
    an explicit — currently empty — allowlist).
  * adding/replacing an executor's ``build_command`` drives the launch with no edit
    to the delegator (the record IS the dispatch surface — GROKEXEC's zero-edit
    proof rests on this).
"""
from __future__ import annotations

import ast
import dataclasses
import json
from pathlib import Path

import pytest

from phase_loop_runtime.capability_registry import capability_registry
from phase_loop_runtime.models import EXECUTORS, ExecutorCapabilityRecord
from phase_loop_runtime import launcher

_LAUNCHER_SRC = Path(launcher.__file__)

# Functions whose bodies must never branch on a literal executor name to build the
# runnable command. Empty allowlist: no exempt literal is permitted in the
# command-construction surface. (Launch-time ``spec.executor`` branches in
# launch_with_spec / run_auth_preflight are a separate surface — post-build launch
# behavior, not runnable-command selection — and are out of this lint's scope.)
_COMMAND_BUILD_FUNCTIONS = {
    "build_launch_spec",
    "build_codex_launch_spec",
    "build_claude_launch_spec",
    "build_gemini_launch_spec",
    "build_grok_launch_spec",
    "build_opencode_launch_spec",
    "build_pi_launch_spec",
    "build_command_launch_spec",
    "build_manual_launch_spec",
}
_ALLOWLISTED_LITERAL_BRANCHES: set[str] = set()


def _request_executor_literal_compares(fn: ast.FunctionDef) -> list[str]:
    """Return the string literals any ``request.executor == "<lit>"`` compare in
    ``fn`` compares against (either operand order)."""
    hits: list[str] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        has_request_executor = any(
            isinstance(op, ast.Attribute)
            and op.attr == "executor"
            and isinstance(op.value, ast.Name)
            and op.value.id == "request"
            for op in operands
        )
        if not has_request_executor:
            continue
        for op in operands:
            if isinstance(op, ast.Constant) and isinstance(op.value, str):
                hits.append(op.value)
    return hits


def test_build_launch_spec_has_no_request_executor_literal_branch():
    tree = ast.parse(_LAUNCHER_SRC.read_text(encoding="utf-8"))
    offenders: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in _COMMAND_BUILD_FUNCTIONS:
            literals = [
                lit
                for lit in _request_executor_literal_compares(node)
                if lit not in _ALLOWLISTED_LITERAL_BRANCHES
            ]
            if literals:
                offenders[node.name] = literals
    assert not offenders, (
        "runnable-command selection must be registry-driven, not a hardcoded "
        f"`if request.executor == \"...\"` branch. Offending functions: {offenders}. "
        "Add an executor by registering its capability record + build fn, not a branch."
    )


def test_every_executor_binds_build_command():
    registry = capability_registry()
    for executor in EXECUTORS:
        record = registry[executor]
        assert record.build_command is not None, f"{executor} has no bound build_command"
        assert callable(record.build_command)


def test_record_carries_optional_execreg_fields():
    # All new fields exist and default to None on a bare construction.
    bare = ExecutorCapabilityRecord(executor="manual", supported_actions=(), capabilities=())
    for field_name in (
        "build_command",
        "is_available",
        "auth_ok",
        "provider_backing",
        "get_session_transcript",
    ):
        assert hasattr(bare, field_name)
        assert getattr(bare, field_name) is None


def test_build_launch_spec_delegates_to_record_build_command(monkeypatch):
    # Replacing a record's build_command drives the launch with NO delegator edit:
    # build_launch_spec must call exactly record.build_command(request, record).
    sentinel = object()
    seen: dict[str, object] = {}

    def fake_build(request, record):
        seen["request"] = request
        seen["record"] = record
        return sentinel

    real = capability_registry()
    patched = dict(real)
    patched["codex"] = real["codex"].bind_runtime(build_command=fake_build)
    monkeypatch.setattr(launcher, "capability_registry", lambda: patched)

    class _Req:
        executor = "codex"

    result = launcher.build_launch_spec(_Req())
    assert result is sentinel
    assert seen["record"] is patched["codex"]
    assert seen["request"].executor == "codex"


def test_build_launch_spec_raises_when_build_command_unbound(monkeypatch):
    real = capability_registry()
    patched = dict(real)
    patched["codex"] = real["codex"].bind_runtime(build_command=None)
    monkeypatch.setattr(launcher, "capability_registry", lambda: patched)

    class _Req:
        executor = "codex"

    with pytest.raises(ValueError, match="no build_command"):
        launcher.build_launch_spec(_Req())


def test_bind_runtime_partial_rebind_preserves_other_bindings():
    # CR re-review (codex minor): re-binding one runtime attr on an already-bound
    # record must NOT drop the others.
    record = capability_registry()["codex"]
    assert record.build_command is not None and record.auth_ok is not None

    def new_is_available():
        return True

    rebound = record.bind_runtime(is_available=new_is_available)
    assert rebound.is_available is new_is_available          # overridden
    assert rebound.build_command is record.build_command      # preserved
    assert rebound.auth_ok is record.auth_ok                  # preserved
    assert rebound.provider_backing == record.provider_backing
    # And unknown binding names are rejected loudly.
    with pytest.raises(ValueError, match="unknown runtime binding"):
        record.bind_runtime(not_a_binding=1)


def test_asdict_and_to_json_never_carry_callables_on_bound_records():
    # CR guard: the runtime callables are ClassVar bindings, not dataclass fields,
    # so asdict() / to_json() on a BOUND record stay JSON-serializable (no function
    # objects leak). Both must round-trip through json.dumps.
    record = capability_registry()["codex"]
    assert record.build_command is not None  # it IS bound
    for payload in (dataclasses.asdict(record), record.to_json()):
        assert "build_command" not in payload
        assert "is_available" not in payload
        json.dumps(payload)  # must not raise


def test_capability_registry_reflects_patched_default_registry():
    # CR regression (both reviewers): the bound-registry cache must NOT freeze a
    # stale snapshot. Warming the cache, then replacing DEFAULT_CAPABILITY_REGISTRY,
    # must be reflected on the next capability_registry() call (the mixedrun fallback
    # tests rely on exactly this patch pattern).
    from unittest.mock import patch

    import phase_loop_runtime.capability_registry as cr

    warmed = cr.capability_registry()  # warm the cache first
    assert warmed["claude"].live_available == cr.DEFAULT_CAPABILITY_REGISTRY["claude"].live_available

    patched = dict(cr.DEFAULT_CAPABILITY_REGISTRY)
    patched["claude"] = dataclasses.replace(patched["claude"], live_available=False)
    with patch("phase_loop_runtime.capability_registry.DEFAULT_CAPABILITY_REGISTRY", patched):
        got = cr.capability_registry()
        assert got["claude"].live_available is False, "patched DEFAULT_CAPABILITY_REGISTRY was ignored (stale cache)"
        assert got["claude"].build_command is not None  # still fully bound
    # After the patch is undone the registry rebinds back to the real source.
    assert cr.capability_registry()["claude"].live_available is True
