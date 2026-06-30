#!/usr/bin/env bash
# resolve_branch_from_sha.sh — map a commit SHA to exactly one local branch.
#
# Usage:
#     resolve_branch_from_sha.sh <commit-sha>
#
# Behavior:
#   - Lists local branches containing the SHA via `git branch --contains`.
#   - Exit 0 with the branch name on stdout if exactly one local branch matches.
#   - Exit 1 with a message on stderr if zero or multiple branches match.
#
# Rationale (from the claude-execute-phase SKILL):
#   "Never trust a branch name; resolve by commit SHA from the teammate's
#    reply envelope." This script enforces that rule mechanically so the
#    LLM doesn't have to re-derive branch-discovery logic per phase.

set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: resolve_branch_from_sha.sh <commit-sha>" >&2
    exit 2
fi

sha="$1"

# Validate the SHA exists first; otherwise `git branch --contains` prints
# a misleading error.
if ! git cat-file -e "${sha}^{commit}" 2>/dev/null; then
    echo "resolve_branch_from_sha: commit not found: $sha" >&2
    exit 1
fi

# `--contains` yields lines like "  main" or "* feature/x"; strip the marker.
mapfile -t candidates < <(
    git branch --contains "$sha" --format '%(refname:short)' 2>/dev/null | sort -u
)

case "${#candidates[@]}" in
    0)
        echo "resolve_branch_from_sha: no local branch contains $sha" >&2
        exit 1
        ;;
    1)
        echo "${candidates[0]}"
        exit 0
        ;;
    *)
        echo "resolve_branch_from_sha: multiple local branches contain $sha: ${candidates[*]}" >&2
        exit 1
        ;;
esac
