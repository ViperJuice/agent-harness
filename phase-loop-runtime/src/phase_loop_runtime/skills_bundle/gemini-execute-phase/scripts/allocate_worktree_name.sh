#!/usr/bin/env bash
# allocate_worktree_name.sh — emit a collision-impossible worktree name
# for a swim-lane teammate.
#
# Usage:
#     allocate_worktree_name.sh <sl-id>
#
#     <sl-id>   Lane identifier, e.g. "sl-1" or "sl-2b".
#
# Emits exactly one line to stdout:
#     lane-<sl-id>-<UTC-YYYYMMDDHHMMSS>-<4char-random>
#
# Rationale: the default `lane-<sl-id>` naming is not unique across rapid
# consecutive dispatch of in-process teammates — two teammates spawned in
# the same message can race on the same EnterWorktree slot. The 4-char
# random suffix + UTC timestamp eliminates the race by construction.

set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: allocate_worktree_name.sh <sl-id>" >&2
    exit 2
fi

sl_id="$1"
ts="$(date -u +%Y%m%dT%H%M%S)"
rand="$(tr -dc 'a-z0-9' </dev/urandom | head -c 4 || true)"
if [[ -z "$rand" ]]; then
    # Extremely unlikely; fall back to $RANDOM-derived 4 chars.
    rand="$(printf "%04x" "$((RANDOM & 0xFFFF))")"
fi

echo "lane-${sl_id}-${ts}-${rand}"
