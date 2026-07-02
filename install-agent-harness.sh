#!/usr/bin/env bash
# install-agent-harness.sh — off-tailnet collaborator installer for the PUBLIC
# agent-harness (https://github.com/ViperJuice/agent-harness).
#
# Needs NO dotfiles clone, NO 1Password, NO Homebrew, NO tailnet. Cross-OS
# (macOS / Linux). Installs:
#   1. the phase-loop runtime CLI (pinned published release) via `uv tool`
#   2. the workflow skills for your harness, from the public skills bundle
# pulling everything from the public agent-harness repo.
#
# Usage:
#   ./install-agent-harness.sh [--harness claude|codex|gemini|opencode|all] [--ref vX.Y.Z]
#   (--harness all installs the skills for EVERY harness; the runtime is shared.)
# Env overrides:
#   AGENT_HARNESS_REPO   (default https://github.com/ViperJuice/agent-harness)
#   AGENT_HARNESS_REF    (default v0.1.12 — keep in sync with each release)
#   AGENT_HARNESS_HARNESS (default claude; use "all" for every harness)
#   AGENT_HARNESS_HOME   (persistent clone dir; default ~/.local/share/agent-harness)
#   AGENT_HARNESS_SKILL_DEST (override the harness skill root)
set -euo pipefail

REPO="${AGENT_HARNESS_REPO:-https://github.com/ViperJuice/agent-harness}"
REF="${AGENT_HARNESS_REF:-v0.1.12}"
HARNESS="${AGENT_HARNESS_HARNESS:-claude}"
HOME_DIR="${AGENT_HARNESS_HOME:-$HOME/.local/share/agent-harness}"

while [ $# -gt 0 ]; do
    case "$1" in
        --harness) HARNESS="$2"; shift 2 ;;
        --ref)     REF="$2"; shift 2 ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "unknown arg: $1 (see --help)" >&2; exit 2 ;;
    esac
done

# Resolve the harness list: "all" => every supported harness (the runtime is shared;
# only the skills are per-harness, installed into each harness's own skill root).
if [ "$HARNESS" = all ]; then
    HARNESSES="claude codex gemini opencode"
else
    HARNESSES="$HARNESS"
fi

# Per-harness default skill root (the documented user-local roots).
skill_dest() {
    case "$1" in
        claude)   printf '%s\n' "$HOME/.claude/skills" ;;
        codex)    printf '%s\n' "$HOME/.codex/skills" ;;
        gemini)   printf '%s\n' "$HOME/.gemini/skills" ;;
        opencode) printf '%s\n' "$HOME/.config/opencode/skills" ;;
        *) echo "unknown --harness: $1 (claude|codex|gemini|opencode|all)" >&2; return 2 ;;
    esac
}
# Validate every requested harness up front.
for _h in $HARNESSES; do skill_dest "$_h" >/dev/null || exit 2; done

say() { printf '\033[1;32m%s\033[0m\n' "$*"; }

# --- 1) uv (cross-OS official installer; no Homebrew dependency) -----------
if ! command -v uv >/dev/null 2>&1; then
    say "[1/3] installing uv (astral.sh official installer)…"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
command -v uv >/dev/null 2>&1 || { echo "ERROR: uv not on PATH after install; add ~/.local/bin to PATH and re-run." >&2; exit 1; }

# --- 2) phase-loop runtime CLI from the pinned PUBLIC release ---------------
say "[2/3] installing phase-loop-runtime ${REF} from ${REPO}…"
uv tool install --force "git+${REPO}@${REF}#subdirectory=phase-loop-runtime"
hash -r 2>/dev/null || true
export PATH="$HOME/.local/bin:$PATH"
phase-loop --version

# --- 3) workflow skills for each harness, from the public bundle -----------
# Persistent clone (NOT a temp dir) so the --symlink skill links never dangle,
# and so `git -C "$HOME_DIR" pull` + re-run is the update path.
say "[3/3] installing workflow skills (${HARNESSES}) from ${REF}…"
if [ -d "$HOME_DIR/.git" ]; then
    git -C "$HOME_DIR" fetch --depth 1 origin "$REF" >/dev/null 2>&1 || true
    git -C "$HOME_DIR" checkout -q "$REF" 2>/dev/null || git -C "$HOME_DIR" checkout -q "FETCH_HEAD"
else
    rm -rf "$HOME_DIR"; mkdir -p "$(dirname "$HOME_DIR")"
    git clone --depth 1 --branch "$REF" "$REPO" "$HOME_DIR"
fi
for _h in $HARNESSES; do
    # An explicit AGENT_HARNESS_SKILL_DEST override is only honored for a single harness.
    if [ "$HARNESS" != all ] && [ -n "${AGENT_HARNESS_SKILL_DEST:-}" ]; then
        _dest="$AGENT_HARNESS_SKILL_DEST"
    else
        _dest="$(skill_dest "$_h")"
    fi
    mkdir -p "$_dest"
    phase-loop --repo "$HOME_DIR" install --harness "$_h" \
        --source "$HOME_DIR/phase-loop-skills" --destination "$_dest" --symlink --apply
    echo "  ✓ ${_h} skills → ${_dest}"
done

say "Done — phase-loop CLI + skills (${HARNESSES}) installed from public agent-harness ${REF}."
echo "  runtime : $(command -v phase-loop)  ($(phase-loop --version 2>/dev/null))"
echo "  bundle  : ${HOME_DIR}/phase-loop-skills"
echo "  update  : re-run this script (it fetches ${REF} and re-applies)."
echo "No fleet / 1Password / tailnet / dotfiles clone required."
