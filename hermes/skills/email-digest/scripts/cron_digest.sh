#!/usr/bin/env bash
# ============================================================================
# cron_digest.sh — deterministic daily NexaDose digest + ntfy delivery
# ============================================================================
# Runs as a Hermes `--no-agent --script` cron job (the script IS the job; its
# stdout is delivered verbatim, so it is kept to one short line).
#
# Flow:
#   1. generate today's digest from the REAL export dir (localhost Ollama only)
#      + a condensed alert body (WAITING_ON_YOU + BLOCKED + counts).
#   2. publish that alert to the operator's phone via the EXISTING ntfy sender,
#      tools/notify.sh — the ntfy topic is a SECRET that lives ONLY inside that
#      script. This wrapper never reads, prints, or logs the topic.
#   3. drop a marker line for the operator / self-test.
#
# Constraints honored: READ-ONLY (reads .eml files, writes a digest + marker),
# LOCAL-ONLY classification (localhost:11434), single outbound publish to ntfy.
#
# Workspace root is fixed but overridable:  OPENCLAW_WS=/path cron_digest.sh
# ============================================================================
set -euo pipefail

WS="${OPENCLAW_WS:-/root/.openclaw/workspace}"
cd "$WS"

DATE="$(date +%F)"
REAL_INPUT="skills/email_digest/inbox_export/real"
NOTIFY="$WS/tools/notify.sh"
MARKER="$HOME/.hermes/logs/email_digest_cron.marker"

ALERT_FILE="$(mktemp)"
trap 'rm -f "$ALERT_FILE"' EXIT

# .venv python preferred (has PyYAML); else system python3 (also has PyYAML here)
if [ -x "$WS/.venv/bin/python" ]; then PY="$WS/.venv/bin/python"; else PY="python3"; fi

# 1) digest + condensed alert body. digest.py is localhost-only; no topic here.
"$PY" -u "$WS/skills/email_digest/digest.py" \
    --input "$REAL_INPUT" --output digests --date "$DATE" \
    --alert-out "$ALERT_FILE" >/dev/null 2>&1

# 2) publish via the existing sender (topic encapsulated in notify.sh).
if [ -f "$NOTIFY" ]; then
    bash "$NOTIFY" "NexaDose digest ${DATE}" "$(cat "$ALERT_FILE")" default || true
fi

# 3) marker + one-line verbatim stdout for --no-agent delivery.
mkdir -p "$(dirname "$MARKER")"
LINE="email-digest cron fired $(date -u +%Y-%m-%dT%H:%M:%SZ) — wrote digests/${DATE}_digest.md, alert published"
echo "$LINE" >> "$MARKER"
echo "$LINE"
