#!/usr/bin/env bash
# sweep_fleet_worktrees.sh — fleet-wide worktree detection + prune backstop.
#
# The per-run closeout sweep (prune_merged_worktrees.sh) only sees the ONE repo
# the current run is in. Across the whole fleet, worktrees for MANY owning repos
# accumulate under the shared workspace-volume base and nothing sweeps the ones
# whose run never came back. This script is that backstop: a periodic sweep of
# EVERY directory under the base, across ALL owning repos, that reuses the exact
# ironclad safety of prune_merged_worktrees.sh.
#
# SAFE to prune (either):
#   MERGED+CLEAN  — the dir is a live linked worktree whose branch MERGED and
#                   whose tree is CLEAN (same criterion as prune_merged_worktrees.sh).
#     MERGED = `git merge-base --is-ancestor <branch> origin/main`  (fetch first)
#              OR  `gh pr view <branch>` reports state MERGED.
#     CLEAN  = `git -C <path> status --porcelain` is empty.
#   ORPHANED      — the dir is a git worktree whose administrative link is GONE:
#                   the gitdir the worktree points at (its owning repo's
#                   .git/worktrees/<id>) no longer exists, so no repo owns it and
#                   it can never be checked out again. This is deliberately narrow:
#                   "can't reach origin" or "unknown branch" is NOT orphaned.
# KEEP           — anything else: unmerged, dirty, a primary checkout, not a git
#                  worktree at all, or a worktree whose owner is still present.
#
# SAFETY (mirrors prune_merged_worktrees.sh; the fleet scan makes two of these
# genuinely broader — see (a) and (d)):
#   (a) EVERY owning repo's PRIMARY checkout is skipped, never classified. The
#       single-repo sweep excludes one primary; the fleet sweep resolves each
#       candidate's owning repo and excludes THAT repo's primary. The current
#       worktree (this script's own repo) is also skipped.
#   (b) The `sudo rm -rf` fallback is CONFINED: it runs only for a path strictly
#       under the approved base ($PHASE_LOOP_WORKTREES_BASE). A path outside the
#       base is never sudo-rm'd. Every candidate is under the base by construction,
#       but the guard is re-checked at the removal site, defence-in-depth.
#   (c) The fallback escalates ONLY on a genuine PERMISSION-denied git error
#       (foreign-uid build output from CI-offload / rootless-docker). Any other
#       failure → skip + warn, never sudo.
#   (d) ORPHAN classification requires the owning gitdir to be genuinely ABSENT,
#       never merely unreachable. An orphan carries no recoverable branch ref, so
#       even a dirty orphan is dead storage — but we still only rm within the base.
#
# Usage:
#   sweep_fleet_worktrees.sh [--dry-run] [--prune] [--alert-threshold N]
#     --dry-run            (DEFAULT) Report PRUNE/KEEP decisions, remove nothing.
#     --prune              Actually remove the SAFE dirs. Opt-in; never the default.
#     --alert-threshold N  Exit non-zero (2) if N or more prunable dirs accumulate,
#                          so a scheduler can alert. 0 (default) disables the alert.
#
# Env:
#   PHASE_LOOP_WORKTREES_BASE   Base dir to scan. Default /mnt/workspace/worktrees.
#
# SCHEDULING (do NOT install a cron from this script — leave that to the
# dotfiles/fleet owner). Recommended: a daily dry-run alert, e.g. cron line
#     17 6 * * *  PHASE_LOOP_WORKTREES_BASE=/mnt/workspace/worktrees /path/to/sweep_fleet_worktrees.sh --alert-threshold 25 >> ~/.cache/fleet-worktree-sweep.log 2>&1
# or a systemd timer running the same command; add --prune once the dry-run
# output has been reviewed and trusted.
#
# Idempotent. Exit 0 on success (under threshold); 2 if the alert threshold is met.

set -euo pipefail

WORKTREES_BASE="${PHASE_LOOP_WORKTREES_BASE:-/mnt/workspace/worktrees}"

# --- pure predicates (extracted so the self-test can exercise them directly) ---

# path_under_base <path> <base> — true iff <path> is strictly under <base>, using a
# trailing-slash boundary so `/base-evil` does NOT match base `/base`. Both are
# realpath-normalized. An empty path or base is always false (never confine nothing).
path_under_base() {
  local path="$1" base="$2"
  [[ -n "$path" && -n "$base" ]] || return 1
  local rp rb
  rp=$(realpath -m -- "$path" 2>/dev/null) || return 1
  rb=$(realpath -m -- "$base" 2>/dev/null) || return 1
  [[ "$rp" == "$rb" ]] && return 1          # equal to base ≠ strictly under
  [[ "$rp" == "$rb"/* ]]
}

# owning_primary <path> — the PRIMARY (main) checkout of the repo that owns the
# worktree at <path>: the FIRST `worktree ` record of `git -C <path> worktree list
# --porcelain`. Git always lists the main tree first, and lists the SAME set from
# any linked worktree of the repo. Empty if <path> is not inside a git worktree.
owning_primary() {
  local path="$1"
  git -C "$path" worktree list --porcelain 2>/dev/null \
    | awk '/^worktree /{sub(/^worktree /,""); print; exit}'
}

# is_orphan_worktree <path> — true iff <path> looks like a git worktree (has a
# `.git` file pointing at a gitdir) whose owning gitdir is GONE. A live linked
# worktree's `.git` is a file `gitdir: <repo>/.git/worktrees/<id>`; when the
# owning repo (or that admin entry) is deleted, the gitdir target vanishes but the
# checkout dir lingers. Absence of the target — not unreachability — is the test.
is_orphan_worktree() {
  local path="$1"
  local dotgit="$path/.git"
  # A primary checkout has a `.git` DIRECTORY, never a gitdir-pointer file → not an
  # orphan (and never classified here). Only the pointer-file form can be orphaned.
  [[ -f "$dotgit" ]] || return 1
  local gitdir
  gitdir=$(sed -n 's/^gitdir: //p' "$dotgit" 2>/dev/null | head -n1)
  [[ -n "$gitdir" ]] || return 1
  # Relative gitdir → resolve against the worktree dir.
  [[ "$gitdir" == /* ]] || gitdir="$path/$gitdir"
  # Orphan iff the owning admin dir is genuinely ABSENT. `-e` alone conflates
  # absence (ENOENT) with inaccessibility (EACCES anywhere in the ancestor chain),
  # which would misread an existing-but-unreadable gitdir as absent and delete
  # recoverable work. `stat`'s error text is the reliable discriminator: only a
  # genuine "no such file" means orphan; ANY other stat failure → cannot confirm
  # absence → KEEP.
  local st
  st=$(stat -- "$gitdir" 2>&1) && return 1          # exists → not an orphan
  grep -qiE 'no such file|not found' <<<"$st" && return 0   # genuinely absent → orphan
  return 1                                           # EACCES / other → KEEP (fail-safe)
}

# Guard: only source the predicates (for the self-test) without running the sweep.
[[ "${SWEEP_FLEET_WORKTREES_LIB:-0}" == "1" ]] && return 0 2>/dev/null || true

# --- sweep ---

DRY_RUN=1            # default: report only, remove nothing
PRUNE=0
ALERT_THRESHOLD=0
_want_prune=0
_want_dryrun=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) _want_dryrun=1 ;;
    --prune) _want_prune=1 ;;
    --alert-threshold) shift; ALERT_THRESHOLD="${1:-0}" ;;
    --alert-threshold=*) ALERT_THRESHOLD="${1#*=}" ;;
    *) echo "WARN:  ignoring unknown arg: $1" >&2 ;;
  esac
  shift
done
# Safety: --dry-run ALWAYS wins. Prune only when --prune was requested AND
# --dry-run was NOT — so `--prune --dry-run` (a cautious operator) never deletes,
# regardless of argument order.
if [[ "$_want_prune" -eq 1 && "$_want_dryrun" -eq 0 ]]; then
  PRUNE=1; DRY_RUN=0
else
  PRUNE=0; DRY_RUN=1
fi

if [[ ! -d "$WORKTREES_BASE" ]]; then
  echo "sweep-fleet-worktrees: base does not exist ($WORKTREES_BASE); nothing to sweep"
  exit 0
fi

SELF_WT=$(git rev-parse --show-toplevel 2>/dev/null || echo "")   # this script's own repo, if any
SELF_WT=$(realpath -m -- "$SELF_WT" 2>/dev/null || echo "$SELF_WT")  # normalize; base may be a symlink

PRUNABLE=0
PRUNED=0
KEPT=0

is_merged() {
  local repo="$1" branch="$2"
  # Best-effort refresh so the ancestor check is accurate. Non-fatal.
  git -C "$repo" fetch --quiet origin main 2>/dev/null || true
  # (1) branch tip is an ancestor of origin/main → its PR merged (or fast-forwarded).
  if git -C "$repo" merge-base --is-ancestor "$branch" origin/main 2>/dev/null; then
    return 0
  fi
  # (2) gh reports the PR for this branch as MERGED (squash/rebase merges are not
  #     ancestors, so this catches them). gh absent / no PR → not merged.
  if command -v gh >/dev/null 2>&1; then
    local state
    state=$(cd "$repo" && gh pr view "$branch" --json state -q .state 2>/dev/null || true)
    [[ "$state" == "MERGED" ]] && return 0
  fi
  return 1
}

# remove_dir <path> — returns 0 iff <path> is gone afterward. Tries the git-native
# worktree removal first (for live linked worktrees); escalates to a CONFINED,
# PERMISSION-ONLY sudo fallback. For orphans (no owning repo) git removal is a
# no-op, so a plain rm -rf is attempted before the confined sudo fallback.
remove_dir() {
  local path="$1"
  [[ -n "$path" ]] || { echo "WARN:  refusing removal of empty path" >&2; return 1; }
  # Defence-in-depth: never remove anything outside the approved base.
  if ! path_under_base "$path" "$WORKTREES_BASE"; then
    echo "WARN:  $path — OUTSIDE approved base ($WORKTREES_BASE); refusing removal" >&2
    return 1
  fi

  local err rc=0
  # Prefer git-native removal via the owning repo (drops the admin entry too).
  # `|| owner=""` — an orphan has no owner (git 128); must not abort under `set -e`.
  local owner
  owner=$(owning_primary "$path") || owner=""
  if [[ -n "$owner" ]]; then
    err=$(git -C "$owner" worktree remove --force "$path" 2>&1) || rc=$?
  else
    # Orphan: no owner to ask; try a plain rm first.
    err=$(rm -rf -- "$path" 2>&1) || rc=$?
  fi
  if [[ "$rc" -eq 0 && ! -e "$path" ]]; then
    [[ -n "$owner" ]] && git -C "$owner" worktree prune 2>/dev/null || true
    return 0
  fi

  # Escalate ONLY on a genuine permission lock (foreign-uid build output). Strip the
  # candidate path from the error first, so a worktree path that itself contains the
  # literal "permission denied" cannot coincidentally trigger a sudo escalation.
  # BOUNDED: even a residual false match (a descendant filename literally containing
  # "permission denied") can only `sudo rm` a path that has ALREADY passed
  # path_under_base (base-confined), is NOT a primary checkout, and was classified
  # prunable — i.e. it merely force-completes an intended removal, never escapes.
  if ! grep -qi 'permission denied' <<<"${err//"$path"/}"; then
    echo "WARN:  $path — removal failed (not a permission lock): ${err%%$'\n'*}; skipping" >&2
    return 1
  fi
  echo "WARN:  $path — permission-locked (foreign uid); using confined 'sudo -n rm -rf'" >&2
  local src=0
  sudo -n rm -rf -- "$path" || src=$?
  if [[ "$src" -ne 0 ]]; then
    echo "WARN:  $path — 'sudo -n rm -rf' failed (rc=$src; no non-interactive sudo?); skipping" >&2
    return 1
  fi
  [[ -n "$owner" ]] && git -C "$owner" worktree prune 2>/dev/null || true
  [[ ! -e "$path" ]]
}

# Enumerate immediate child directories of the base. Each is a candidate worktree.
shopt -s nullglob
for wt in "$WORKTREES_BASE"/*/; do
  wt="${wt%/}"                                    # strip trailing slash
  [[ -d "$wt" ]] || continue
  # Compare on realpath-normalized forms. The base may be a symlink (e.g.
  # /mnt/workspace -> /mnt/HC_Volume_...), so git's canonical paths would not
  # string-match the symlinked candidate path — an aliased primary/self could
  # otherwise slip the guards below.
  wt_real=$(realpath -m -- "$wt" 2>/dev/null || echo "$wt")
  [[ -n "$SELF_WT" && "$wt_real" == "$SELF_WT" ]] && continue   # never this run's own repo

  # (a) never touch any owning repo's PRIMARY checkout.
  # NB: owning_primary exits non-zero (git 128) for a non-git / orphaned dir; the
  # `|| primary=""` keeps `set -e` from aborting the sweep on the first such child.
  primary=$(owning_primary "$wt") || primary=""
  primary_real=$(realpath -m -- "$primary" 2>/dev/null || echo "$primary")
  if [[ -n "$primary" && "$wt_real" == "$primary_real" ]]; then
    echo "KEEP:  $wt — primary checkout of its repo" >&2
    KEPT=$((KEPT + 1))
    continue
  fi

  # ORPHAN branch — owning admin dir genuinely gone. Dead storage; safe to remove.
  if is_orphan_worktree "$wt"; then
    echo "PRUNE: $wt — ORPHANED (owning gitdir absent)"
    PRUNABLE=$((PRUNABLE + 1))
    if [[ "$PRUNE" -eq 1 ]]; then
      if remove_dir "$wt"; then PRUNED=$((PRUNED + 1)); else
        echo "WARN:  $wt — orphan removal did not complete" >&2; fi
    fi
    continue
  fi

  # Not a git worktree at all (no owner, not an orphan pointer) → leave it alone.
  if [[ -z "$primary" ]]; then
    echo "KEEP:  $wt — not a git worktree (no owning repo); left untouched" >&2
    KEPT=$((KEPT + 1))
    continue
  fi

  wt_branch=$(git -C "$wt" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "<detached>")
  if [[ "$wt_branch" == "<detached>" || "$wt_branch" == "HEAD" ]]; then
    echo "KEEP:  $wt (detached HEAD) — no branch to evaluate/merge-check" >&2
    KEPT=$((KEPT + 1))
    continue
  fi

  if [[ -n "$(git -C "$wt" status --porcelain 2>/dev/null)" ]]; then
    echo "KEEP:  $wt (branch $wt_branch) — dirty tree" >&2
    KEPT=$((KEPT + 1))
    continue
  fi

  if ! is_merged "$primary" "$wt_branch"; then
    echo "KEEP:  $wt (branch $wt_branch) — unmerged (not on origin/main, no merged PR)" >&2
    KEPT=$((KEPT + 1))
    continue
  fi

  echo "PRUNE: $wt (branch $wt_branch) — MERGED and CLEAN"
  PRUNABLE=$((PRUNABLE + 1))
  if [[ "$PRUNE" -eq 1 ]]; then
    if remove_dir "$wt"; then
      git -C "$primary" branch -D "$wt_branch" 2>/dev/null || true
      PRUNED=$((PRUNED + 1))
    else
      echo "WARN:  $wt — removal did not complete; leaving branch $wt_branch intact" >&2
    fi
  fi
done

echo "sweep-fleet-worktrees: prunable=$PRUNABLE pruned=$PRUNED kept=$KEPT dry_run=$DRY_RUN base=$WORKTREES_BASE"

if [[ "$ALERT_THRESHOLD" -gt 0 && "$PRUNABLE" -ge "$ALERT_THRESHOLD" ]]; then
  echo "sweep-fleet-worktrees: ALERT — $PRUNABLE prunable dirs >= threshold $ALERT_THRESHOLD" >&2
  exit 2
fi
exit 0
