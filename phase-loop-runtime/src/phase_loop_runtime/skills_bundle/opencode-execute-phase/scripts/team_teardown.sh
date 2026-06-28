#!/usr/bin/env bash
# team_teardown.sh — filesystem fallback for TeamDelete.
#
# Usage:
#     team_teardown.sh <team-name>
#
# Behavior:
#   - Removes ~/.claude/teams/<team-name>/ and ~/.claude/tasks/<team-name>/.
#   - Idempotent (no-op if dirs don't exist).
#   - Never fails (exit 0 always).
#
# Rationale: in-process teammates ignore `shutdown_request`, so `TeamDelete`
# blocks on active members. After the phase has verified green, removing
# the team directory and its task directory on the filesystem is the
# accepted tear-down path. The orchestrator should call TeamDelete first;
# fall back to this script when TeamDelete fails on active members.

set -uo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: team_teardown.sh <team-name>" >&2
    exit 2
fi

team="$1"
team_dir="$HOME/.claude/teams/$team"
task_dir="$HOME/.claude/tasks/$team"

rm -rf "$team_dir" "$task_dir"

echo "team_teardown: removed (or already absent): $team_dir $task_dir"
exit 0
