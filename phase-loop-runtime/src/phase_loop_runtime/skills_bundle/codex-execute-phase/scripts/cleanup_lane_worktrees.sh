#!/usr/bin/env bash
# cleanup_lane_worktrees.sh — remove lane worktrees and their auto-named
# branches after a phase merges.
#
# Usage:
#     cleanup_lane_worktrees.sh <branch-pattern>
#
#     <branch-pattern>    Glob applied to each worktree's branch name (the
#                         short name, not refs/heads/…). Defaults to
#                         'worktree-*' so the common case works with no arg.
#
# Behavior:
#   - Enumerates every worktree via `git worktree list --porcelain`.
#   - Skips the orchestrator's own worktree (identified by matching the
#     `git rev-parse --show-toplevel` of the current cwd).
#   - For each remaining worktree whose branch matches <branch-pattern>:
#       * `git worktree remove --force <path>`
#       * If the branch also matches 'worktree-*' or 'phase/*/sl-*',
#         `git branch -D <branch>` (salvage footgun: -D, not -d).
#   - Never blocks the phase: exit 0 even on individual cleanup failures;
#     each failure is logged to stderr.
#
# Rationale: Step 7 post-merge cleanup. Keeps human-named branches intact
# (skills/*, feature/*, fix/*) even when accidentally matched by glob.

set -uo pipefail

pattern="${1:-worktree-*}"

# Resolve orchestrator's anchored worktree so we don't try to delete it.
orch_root=""
if orch_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
    :
fi

removed=0
skipped=0

# Parse `git worktree list --porcelain`. Each record is separated by a blank line:
#     worktree <path>
#     HEAD <sha>
#     branch refs/heads/<name>
while IFS= read -r block; do
    # `git worktree list --porcelain` actually yields one line per key; we
    # collapse them back into records here.
    [[ -z "$block" ]] && continue
    key="${block%% *}"
    value="${block#* }"
    case "$key" in
        worktree)
            wt_path="$value"
            wt_branch=""
            ;;
        branch)
            # value looks like 'refs/heads/worktree-sl-1'
            wt_branch="${value#refs/heads/}"
            ;;
        HEAD) ;;   # ignore
        detached) wt_branch="" ;;
    esac
    # When we hit a new `worktree` line, the previous record is complete —
    # but the block-mode loop above feeds us line-by-line. We process each
    # line and finalize when we hit a record boundary below.
done < <(git worktree list --porcelain)

# Process each worktree block (re-read cleanly; the above was an artifact).
while IFS= read -r path; do
    [[ -z "$path" ]] && continue
    # Skip orchestrator's anchor.
    if [[ "$path" == "$orch_root" ]]; then
        skipped=$((skipped + 1))
        continue
    fi
    # Resolve this worktree's branch.
    branch="$(git -C "$path" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '')"
    if [[ -z "$branch" || "$branch" == "HEAD" ]]; then
        # Detached HEAD worktree — skip.
        skipped=$((skipped + 1))
        continue
    fi
    # Apply the branch pattern.
    # shellcheck disable=SC2053
    if [[ "$branch" != $pattern ]]; then
        skipped=$((skipped + 1))
        continue
    fi
    # Remove the worktree.
    if git worktree remove --force "$path" 2>/dev/null; then
        removed=$((removed + 1))
    else
        echo "cleanup_lane_worktrees: failed to remove worktree $path (branch=$branch)" >&2
        continue
    fi
    # Delete the branch only if it's an auto-named pattern.
    if [[ "$branch" == worktree-* || "$branch" == phase/*/sl-* ]]; then
        if ! git branch -D "$branch" 2>/dev/null; then
            echo "cleanup_lane_worktrees: failed to delete branch $branch" >&2
        fi
    fi
done < <(git worktree list --porcelain | awk '$1 == "worktree" { print $2 }')

echo "cleanup_lane_worktrees: removed $removed, skipped $skipped"
exit 0
