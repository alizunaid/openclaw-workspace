#!/usr/bin/env bash
# Install the repo-tracked `email-digest` skill into Hermes's skill tree.
# Source of truth stays in this repo; Hermes loads it via a symlink so the two
# never drift. Idempotent. Mirrors install_generate_code_skill.sh.
set -euo pipefail

REPO_SKILL="$(cd "$(dirname "${BASH_SOURCE[0]}")/skills/email-digest" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
DEST_DIR="$HERMES_HOME/skills/ops"
DEST="$DEST_DIR/email-digest"

mkdir -p "$DEST_DIR"

if [ -L "$DEST" ]; then
    rm -f "$DEST"
elif [ -e "$DEST" ]; then
    echo "[install] $DEST exists and is not a symlink; refusing to clobber" >&2
    exit 1
fi

ln -s "$REPO_SKILL" "$DEST"
echo "[install] linked $DEST -> $REPO_SKILL"
echo "[install] Hermes will load 'email-digest' on its next session (loader is cached per session)."
