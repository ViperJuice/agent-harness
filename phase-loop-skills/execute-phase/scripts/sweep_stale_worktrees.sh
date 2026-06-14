#!/usr/bin/env bash
# sweep_stale_worktrees.sh — prune stale claude-execute-phase worktrees from prior sessions.
#
# For each worktree (excluding the orchestrator's own checkout), checks whether its
# HEAD commit has been incorporated into the current merge target (HEAD).  Worktrees
# whose work is already on the target are removed; worktrees with unmerged work are
# kept and reported.
#
# Usage:
#   sweep_stale_worktrees.sh [--dry-run]
#
#   --dry-run   Print PRUNE/KEEP decisions without removing anything.
#
# Exit code: 0 on success (even if nothing was pruned); non-zero on git errors.

set -euo pipefail

DRY_RUN=0
for arg in "$@"; do
  [[ "$arg" == "--dry-run" ]] && DRY_RUN=1
done

TOPLEVEL=$(git rev-parse --show-toplevel)
PRUNED=0
KEPT=0

# Collect worktree paths (skip the main checkout)
while IFS= read -r wt; do
  [[ "$wt" == "$TOPLEVEL" ]] && continue

  wt_sha=$(git -C "$wt" rev-parse HEAD 2>/dev/null) || {
    echo "WARN:  $wt — could not read HEAD, skipping" >&2
    continue
  }
  wt_branch=$(git -C "$wt" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "<detached>")

  if git merge-base --is-ancestor "$wt_sha" HEAD 2>/dev/null; then
    echo "PRUNE: $wt ($wt_sha, branch $wt_branch) — work incorporated into merge target"

    if [[ "$DRY_RUN" -eq 0 ]]; then
      git worktree unlock "$wt" 2>/dev/null || true
      git worktree remove -f -f "$wt"

      # Delete the branch only when it matches an auto-named pattern; leave
      # human-named branches (feature/*, fix/*, skills/*, etc.) intact.
      if [[ "$wt_branch" =~ ^(worktree-agent-|phase/.*/sl-) ]]; then
        git branch -D "$wt_branch" 2>/dev/null || true
      fi
    fi

    PRUNED=$(( PRUNED + 1 ))
  else
    echo "KEEP:  $wt ($wt_sha, branch $wt_branch) — unmerged work"
    KEPT=$(( KEPT + 1 ))
  fi
done < <(git worktree list --porcelain | awk '/^worktree / {print $2}')

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "Dry-run complete: $PRUNED would be pruned, $KEPT would be kept."
else
  echo "Sweep complete: $PRUNED pruned, $KEPT kept."
fi
