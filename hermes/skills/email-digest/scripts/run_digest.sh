#!/usr/bin/env bash
# ============================================================================
# run_digest.sh — Hermes skill wrapper for the NexaDose email digest
# ============================================================================
# Thin dispatcher. It SHELLS OUT to the standalone digest module and nothing
# more:
#   skills/email_digest/digest.py   (read-only classifier + digest writer)
# It does NOT reimplement the digest, and it touches NO email — the module
# reads exported .eml/.txt files and writes a markdown digest, that is all.
#
# The module needs only PyYAML + stdlib. Prefer the workspace .venv if present,
# else fall back to system python3 (which has PyYAML here).
#
# Workspace root is fixed but overridable for portability:
#   OPENCLAW_WS=/path/to/workspace run_digest.sh ...
# ============================================================================
set -euo pipefail

WS="${OPENCLAW_WS:-/root/.openclaw/workspace}"
MOD="$WS/skills/email_digest/digest.py"

if [ -x "$WS/.venv/bin/python" ]; then
    PY="$WS/.venv/bin/python"
else
    PY="python3"
fi

usage() {
    cat >&2 <<EOF
run_digest.sh — generate the NexaDose morning email digest (read-only, local-only)

USAGE:
  run_digest.sh [--config PATH] [--input DIR] [--output DIR] [--date YYYY-MM-DD]

  All flags are optional and pass straight through to digest.py; with no flags
  it uses skills/email_digest/config/nexadose.yaml (input inbox_export/, output
  digests/, model qwen3-coder:30b, today's date).

ENV:
  OPENCLAW_WS   workspace root (default: /root/.openclaw/workspace)

The digest module is at \$OPENCLAW_WS/skills/email_digest/digest.py.
It reads exported email files and writes digests/<date>_digest.md. It never
connects to a mailbox and never sends, deletes, labels, or moves any email.
EOF
}

case "${1:-}" in
    -h|--help|help)
        usage; exit 0
        ;;
esac

[ -f "$MOD" ] || { echo "[wrapper] digest module not found at $MOD (set OPENCLAW_WS)" >&2; exit 2; }
cd "$WS" || { echo "[wrapper] cannot cd to workspace $WS" >&2; exit 2; }

echo "[wrapper] -> $PY -u skills/email_digest/digest.py $*" >&2
exec "$PY" -u "$MOD" "$@"
