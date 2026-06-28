#!/usr/bin/env bash
# post_merge_import_smoke.sh — verify post-merge Python-package imports.
#
# Usage:
#     post_merge_import_smoke.sh <pkg.dotted.path> [<pkg.another> ...]
#
# For each package argument, runs `python -c "import <pkg>"` using the
# repo-local .venv (falls back to plain `python` if .venv is missing).
# Emits one line per package:
#
#     IMPORT_OK: <pkg>
#     IMPORT_FAIL: <pkg> — <first-line-of-error>
#
# Exit code:
#   0 — every package imports cleanly
#   1 — at least one package failed to import
#   2 — usage error
#
# Rationale: a lane that drops or renames a symbol re-exported by an
# `__init__.py` breaks package load silently — the ImportError only
# surfaces when some downstream code happens to trigger the import. A
# post-merge import smoke on every `__init__.py` the lane touched catches
# this in seconds, before the next wave builds on a broken base.
#
# Invocation pattern for the orchestrator (Step 7):
#     packages="$(git diff --name-only HEAD~..HEAD \
#         | grep '__init__\.py$' \
#         | sed 's|/__init__\.py$||; s|/|.|g' \
#         | sort -u)"
#     [[ -n "$packages" ]] && bash <script> $packages

set -uo pipefail

if [[ $# -lt 1 ]]; then
    echo "usage: post_merge_import_smoke.sh <pkg.dotted.path> [<pkg.another> ...]" >&2
    exit 2
fi

# Prefer repo-local venv if present.
python_bin="python"
if [[ -x ".venv/bin/python" ]]; then
    python_bin=".venv/bin/python"
elif [[ -x "venv/bin/python" ]]; then
    python_bin="venv/bin/python"
fi

any_fail=0
for pkg in "$@"; do
    # Run the import in a subshell; capture stderr.
    err="$("$python_bin" -c "import ${pkg}" 2>&1 >/dev/null)"
    rc=$?
    if [[ $rc -eq 0 ]]; then
        echo "IMPORT_OK: ${pkg}"
    else
        # First line of the error only.
        first_line="$(echo "$err" | tail -n 1)"
        echo "IMPORT_FAIL: ${pkg} — ${first_line}"
        any_fail=1
    fi
done

exit $any_fail
