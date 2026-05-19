#!/usr/bin/env bash
# notify.sh — send a push notification via ntfy to user's phone.
# Usage: notify.sh "title" "body" [priority]
# Priority: default|high|urgent (default = "default")

TOPIC="openclaw-zunaid786786"
TITLE="${1:-OpenClaw}"
BODY="${2:-(no body)}"
PRIORITY="${3:-default}"

# Fail silently — notification failure should never break a workflow
curl -sS \
  -H "Title: ${TITLE}" \
  -H "Priority: ${PRIORITY}" \
  -d "${BODY}" \
  "https://ntfy.sh/${TOPIC}" \
  > /dev/null 2>&1 || true
