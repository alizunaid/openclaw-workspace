#!/usr/bin/env bash
# Register the daily NexaDose email-digest cron job in Hermes. Idempotent.
#
# Hermes cron requires the --script to be a REAL file under ~/.hermes/scripts/
# (symlinks that escape the dir are rejected as traversal), so we install a thin
# shim there that execs the repo-tracked cron script (single source of truth).
#
# Delivery: the cron script publishes the digest's actionable sections to the
# operator's phone via tools/notify.sh, which encapsulates the ntfy topic. The
# topic is a SECRET and is NEVER written here or into the repo.
#
# Schedule: 06:00 daily. Hermes cron fires in the system local timezone; this
# host is America/Chicago, so this is 06:00 Central. Requires the gateway to be
# running: nohup hermes gateway run > ~/.hermes/logs/gateway.run.log 2>&1 &
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CRON_SRC="$REPO_ROOT/hermes/skills/email-digest/scripts/cron_digest.sh"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
SHIM="$HERMES_HOME/scripts/email_digest_cron.sh"
JOB_NAME="email-digest-daily"

[ -f "$CRON_SRC" ] || { echo "[install] cron script missing: $CRON_SRC" >&2; exit 1; }
mkdir -p "$HERMES_HOME/scripts"

# Install/refresh the shim (real file, delegates to the repo script).
cat > "$SHIM" <<EOF
#!/usr/bin/env bash
# Shim: Hermes cron needs a real script under ~/.hermes/scripts/. Job logic is
# the repo-tracked single source of truth; this just delegates.
exec bash "$CRON_SRC" "\$@"
EOF
chmod +x "$SHIM"
echo "[install] shim -> $SHIM"

if hermes cron list 2>/dev/null | grep -q "$JOB_NAME"; then
    echo "[install] cron job '$JOB_NAME' already registered — leaving as-is."
else
    hermes cron create '0 6 * * *' --name "$JOB_NAME" \
        --script email_digest_cron.sh --no-agent --deliver local
    echo "[install] created cron job '$JOB_NAME' (0 6 * * *, system tz)."
fi
