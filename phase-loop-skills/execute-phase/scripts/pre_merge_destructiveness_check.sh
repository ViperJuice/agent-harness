#!/usr/bin/env bash
# pre_merge_destructiveness_check.sh — classify a lane's merge before
# performing it, to prevent stale-base destruction of peer-lane work.
#
# Usage:
#     pre_merge_destructiveness_check.sh <lane-sha> <merge-target> <whitelist-path>
#
#     <lane-sha>          Commit SHA produced by the lane.
#     <merge-target>      Branch the lane will be merged into (typically main).
#     <whitelist-path>    File with one path per line listing destructive
#                         changes the plan EXPECTS for this lane. Supports
#                         shell-glob patterns. Use /dev/null if the lane
#                         is purely additive.
#
# Behavior: emits EXACTLY one token on stdout, exit 0 always (verdict is
# on stdout for the orchestrator to act on).
#
#   SAFE
#     - The ancestor-diff deletion list is empty OR every deletion is
#       explicitly whitelisted. Proceed with `git merge --no-ff`.
#
#   STALE_BASE_DETECTED
#     - The lane's diff-against-target shows deletions of files that
#       WEREN'T in the lane's ancestor; i.e. peer lanes merged additions
#       between the lane's base and target, and `git merge --no-ff` would
#       revert them. Orchestrator should salvage (cherry-pick the lane's
#       owned paths) rather than retry.
#
#   CONFLICT
#     - Some other destructive signature that doesn't match either of the
#       above. Orchestrator should surface to the teammate for manual
#       resolution.
#
# Rationale: Step 7's MANDATORY pre-merge check. A lane that committed
# against a stale base can wipe peer-lane work on `--no-ff` merge; the
# naive `git diff main..lane` can't distinguish that from a parallel-
# branch false positive. Extracting the 3-part diff (naive + ancestor +
# whitelist) into a script makes the verdict deterministic.

set -uo pipefail

if [[ $# -ne 3 ]]; then
    echo "usage: pre_merge_destructiveness_check.sh <lane-sha> <merge-target> <whitelist-path>" >&2
    exit 2
fi

sha="$1"
target="$2"
whitelist_path="$3"

# Load whitelist patterns (one per line; '#' comments OK; blank lines skipped).
whitelist=()
if [[ -r "$whitelist_path" && "$whitelist_path" != "/dev/null" ]]; then
    while IFS= read -r line; do
        line="${line%%#*}"
        line="${line//[[:space:]]/}"
        [[ -z "$line" ]] && continue
        whitelist+=("$line")
    done < "$whitelist_path"
fi

# Primary: deletions in target..lane-sha (naïve diff).
mapfile -t naive_deletions < <(
    git diff --diff-filter=D --name-only "$target..$sha" 2>/dev/null
)

# Disambiguator: deletions in ancestor..lane-sha (what the lane actually
# did, ignoring what target gained since).
ancestor="$(git merge-base "$target" "$sha" 2>/dev/null)"
mapfile -t ancestor_deletions < <(
    [[ -n "$ancestor" ]] \
        && git diff --diff-filter=D --name-only "$ancestor..$sha" 2>/dev/null \
        || true
)

# Helper: test whether a path matches any whitelist glob.
is_whitelisted() {
    local path="$1"
    local pat
    for pat in "${whitelist[@]+"${whitelist[@]}"}"; do
        # shellcheck disable=SC2053
        if [[ "$path" == $pat ]]; then
            return 0
        fi
    done
    return 1
}

# Case 1: the lane itself did zero deletions (ancestor..lane is additive-only
# OR all lane-side deletions are whitelisted). The naive deletion list may
# still show peer-lane additions — which is the stale-base signature.
lane_did_unwhitelisted_delete=false
for d in "${ancestor_deletions[@]+"${ancestor_deletions[@]}"}"; do
    [[ -z "$d" ]] && continue
    if ! is_whitelisted "$d"; then
        lane_did_unwhitelisted_delete=true
        break
    fi
done

# Compute naive-but-not-ancestor deletions (files target has that lane
# never had -> peer lanes added them after lane branched off).
declare -A ancestor_set=()
for d in "${ancestor_deletions[@]+"${ancestor_deletions[@]}"}"; do
    [[ -z "$d" ]] && continue
    ancestor_set["$d"]=1
done
stale_base_deletions=()
for d in "${naive_deletions[@]+"${naive_deletions[@]}"}"; do
    [[ -z "$d" ]] && continue
    if [[ -z "${ancestor_set[$d]+set}" ]]; then
        stale_base_deletions+=("$d")
    fi
done

if [[ "$lane_did_unwhitelisted_delete" == "false" && ${#stale_base_deletions[@]} -eq 0 ]]; then
    echo "SAFE"
    exit 0
fi
if [[ "$lane_did_unwhitelisted_delete" == "false" && ${#stale_base_deletions[@]} -gt 0 ]]; then
    # Lane is additive but diff-against-target shows deletions from peer-lane
    # additions — classic stale base.
    echo "STALE_BASE_DETECTED"
    exit 0
fi
echo "CONFLICT"
exit 0
