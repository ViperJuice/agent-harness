#!/usr/bin/env python3
"""
state.py — JSON CRUD for claude-execute-phase's state file.

Usage:
    state.py read
    state.py init <phase-id> <merge-target>
    state.py set-lane <lane-id> <field>=<value> [<field>=<value> ...]
    state.py transition <lane-id> <from-status> <to-status>
    state.py set-gate <gate-id> <status>
    state.py delete

State file path (env-overridable, default is $PWD/.claude/claude-execute-phase-state.json):
    STATE_FILE=/path/to/file state.py read

Schema:
    {
      "phase_id":      "PHASE-1-repo-identity-default-branch-pinning",
      "merge_target":  "main",
      "started_at":    "2026-04-18T00:00:00Z",
      "updated_at":    "2026-04-18T00:15:00Z",
      "status":        "running",         # running | complete | halted
      "lanes": {
        "SL-1": {
          "status":        "pending",      # pending | running | verify-ok | merged | failed
          "commit_sha":    null,
          "branch":        null,
          "worktree_path": null,
          "retries":       0,
          "notes":         []
        }
      },
      "gates": {
        "IF-0-P1-1": { "status": "open" }  # open | closed
      }
    }

Design notes:
- `transition` is atomic — it fails if the current status doesn't match <from>.
  This is the state-machine contract the orchestrator relies on to avoid
  double-running a lane through a wave.
- `set-lane` is a permissive write; use it to add commit_sha / branch /
  worktree_path after dispatch-and-return; do NOT use it for status changes
  (use `transition` for those).
- All mutations update `updated_at`. `started_at` is set once by `init`.
- The file is written atomically (tmp + rename) so concurrent readers never
  see a truncated file.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_STATE_PATH = ".claude/claude-execute-phase-state.json"
LANE_STATUSES = {"pending", "running", "verify-ok", "merged", "failed"}
GATE_STATUSES = {"open", "closed"}
PHASE_STATUSES = {"running", "complete", "halted"}


def _state_path() -> Path:
    return Path(os.environ.get("STATE_FILE", DEFAULT_STATE_PATH))


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load() -> Dict[str, Any]:
    p = _state_path()
    if not p.exists():
        _fail(f"state file not found: {p}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        _fail(f"state file unreadable: {exc}")
    return {}  # unreachable


def _save(state: Dict[str, Any]) -> None:
    state["updated_at"] = _utc_now()
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    tmp.replace(p)


def _fail(msg: str, exit_code: int = 2) -> None:
    print(f"state.py: {msg}", file=sys.stderr)
    sys.exit(exit_code)


def _coerce_value(raw: str) -> Any:
    """Convert CLI string -> JSON value. Supports bare null/true/false/integer/float;
    otherwise treats as string."""
    if raw == "null":
        return None
    if raw == "true":
        return True
    if raw == "false":
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


# ---------------------------------------------------------------------------
# Subcommand handlers

def cmd_init(phase_id: str, merge_target: str) -> int:
    if _state_path().exists():
        _fail(f"state file already exists at {_state_path()} — use `delete` first or `transition`")
    state = {
        "phase_id": phase_id,
        "merge_target": merge_target,
        "started_at": _utc_now(),
        "updated_at": _utc_now(),
        "status": "running",
        "lanes": {},
        "gates": {},
    }
    _save(state)
    print(f"state.py: initialized {_state_path()} for {phase_id}")
    return 0


def cmd_read() -> int:
    state = _load()
    print(json.dumps(state, indent=2, sort_keys=False))
    return 0


def cmd_set_lane(lane_id: str, assignments: List[str]) -> int:
    state = _load()
    lane = state["lanes"].setdefault(
        lane_id,
        {
            "status": "pending",
            "commit_sha": None,
            "branch": None,
            "worktree_path": None,
            "retries": 0,
            "notes": [],
        },
    )
    for assignment in assignments:
        if "=" not in assignment:
            _fail(f"expected `<field>=<value>`, got {assignment!r}")
        key, _, raw = assignment.partition("=")
        key = key.strip()
        value: Any = _coerce_value(raw.strip())
        if key == "status":
            _fail("use `transition` to change status, not `set-lane`")
        if key == "notes" and isinstance(value, str):
            # Append to notes list instead of replacing.
            lane.setdefault("notes", []).append(value)
        else:
            lane[key] = value
    _save(state)
    return 0


def cmd_transition(lane_id: str, frm: str, to: str) -> int:
    if frm not in LANE_STATUSES:
        _fail(f"from-status {frm!r} not in {sorted(LANE_STATUSES)}")
    if to not in LANE_STATUSES:
        _fail(f"to-status {to!r} not in {sorted(LANE_STATUSES)}")
    state = _load()
    lane = state["lanes"].get(lane_id)
    if lane is None:
        # Creating on first transition is fine if `from` is "pending".
        if frm != "pending":
            _fail(f"lane {lane_id!r} not found and from-status is not `pending`")
        lane = {
            "status": "pending",
            "commit_sha": None,
            "branch": None,
            "worktree_path": None,
            "retries": 0,
            "notes": [],
        }
        state["lanes"][lane_id] = lane
    if lane["status"] != frm:
        _fail(
            f"{lane_id} is in status {lane['status']!r}, not {frm!r}; "
            f"refusing transition to {to!r}",
            exit_code=1,
        )
    lane["status"] = to
    _save(state)
    return 0


def cmd_set_gate(gate_id: str, status: str) -> int:
    if status not in GATE_STATUSES:
        _fail(f"gate status {status!r} not in {sorted(GATE_STATUSES)}")
    state = _load()
    state["gates"][gate_id] = {"status": status}
    _save(state)
    return 0


def cmd_set_phase(status: str) -> int:
    if status not in PHASE_STATUSES:
        _fail(f"phase status {status!r} not in {sorted(PHASE_STATUSES)}")
    state = _load()
    state["status"] = status
    _save(state)
    return 0


def cmd_delete() -> int:
    p = _state_path()
    if not p.exists():
        return 0  # idempotent
    p.unlink()
    print(f"state.py: deleted {p}")
    return 0


# ---------------------------------------------------------------------------

def main(argv: List[str]) -> int:
    if len(argv) < 2:
        _fail("usage: state.py <read|init|set-lane|transition|set-gate|set-phase|delete> [args]")
    cmd = argv[1]
    rest = argv[2:]
    try:
        if cmd == "read":
            return cmd_read()
        if cmd == "init":
            if len(rest) != 2:
                _fail("usage: state.py init <phase-id> <merge-target>")
            return cmd_init(rest[0], rest[1])
        if cmd == "set-lane":
            if len(rest) < 2:
                _fail("usage: state.py set-lane <lane-id> <field>=<value> [<field>=<value> ...]")
            return cmd_set_lane(rest[0], rest[1:])
        if cmd == "transition":
            if len(rest) != 3:
                _fail("usage: state.py transition <lane-id> <from-status> <to-status>")
            return cmd_transition(rest[0], rest[1], rest[2])
        if cmd == "set-gate":
            if len(rest) != 2:
                _fail("usage: state.py set-gate <gate-id> <open|closed>")
            return cmd_set_gate(rest[0], rest[1])
        if cmd == "set-phase":
            if len(rest) != 1:
                _fail("usage: state.py set-phase <running|complete|halted>")
            return cmd_set_phase(rest[0])
        if cmd == "delete":
            return cmd_delete()
        _fail(f"unknown subcommand: {cmd!r}")
    except SystemExit:
        raise
    except Exception as exc:
        _fail(f"unexpected error: {exc!r}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
