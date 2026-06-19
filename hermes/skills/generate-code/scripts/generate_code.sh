#!/usr/bin/env bash
# ============================================================================
# generate_code.sh — Hermes skill wrapper for the OpenClaw code-gen engine
# ============================================================================
# Thin dispatcher. It SHELLS OUT to the existing engine and nothing more:
#   Tier 1  ->  tools/oc_builder.py   (single-file builds; alias `ocb`)
#   Tier 2  ->  tools/oc2/__main__.py (multi-subsystem projects; alias `oc2`)
# It does NOT reimplement, patch, or work around the engine. The engine's
# stdout and exit code pass through unchanged (commands are `exec`'d).
#
# Workspace root is fixed but overridable for portability:
#   OPENCLAW_WS=/path/to/workspace generate_code.sh ...
# ============================================================================
set -euo pipefail

WS="${OPENCLAW_WS:-/root/.openclaw/workspace}"
OCB="$WS/tools/oc_builder.py"
OC2="$WS/tools/oc2/__main__.py"

usage() {
    cat >&2 <<EOF
generate_code.sh — wrap the OpenClaw engine (does not modify it)

USAGE:
  generate_code.sh tier1  "<spec>" [project_slug]      Tier 1: single-file build (ocb)
  generate_code.sh design "<spec>" <name>              Tier 2: design architecture from a spec
  generate_code.sh approve <name>                      Tier 2: validate + approve the architecture
  generate_code.sh build  <name> [--only X] [--resume] Tier 2: build subsystems in topo order
  generate_code.sh smoke  <name>                       Tier 2: run the integration smoke runner
  generate_code.sh status [name]                       Tier 2: show project/build status

ENV:
  OPENCLAW_WS   workspace root (default: /root/.openclaw/workspace)

Engine is at \$OPENCLAW_WS/tools/{oc_builder.py, oc2/__main__.py}.
EOF
}

[ -f "$OCB" ] || { echo "[wrapper] engine not found at $OCB (set OPENCLAW_WS)" >&2; exit 2; }
cd "$WS" || { echo "[wrapper] cannot cd to workspace $WS" >&2; exit 2; }

cmd="${1:-}"
[ -n "$cmd" ] && shift || { usage; exit 2; }

case "$cmd" in
    tier1)
        spec="${1:-}"; proj="${2:-}"
        [ -n "$spec" ] || { echo "[wrapper] tier1 needs a spec" >&2; usage; exit 2; }
        if [ -n "$proj" ]; then
            echo "[wrapper] -> python3 -u tools/oc_builder.py --project $proj <spec>" >&2
            exec python3 -u "$OCB" --project "$proj" "$spec"
        else
            echo "[wrapper] -> python3 -u tools/oc_builder.py <spec>" >&2
            exec python3 -u "$OCB" "$spec"
        fi
        ;;
    design)
        spec="${1:-}"; name="${2:-}"
        [ -n "$spec" ] && [ -n "$name" ] || { echo "[wrapper] design needs \"<spec>\" <name>" >&2; usage; exit 2; }
        echo "[wrapper] -> python3 tools/oc2/__main__.py design <spec> --name $name" >&2
        exec python3 "$OC2" design "$spec" --name "$name"
        ;;
    approve)
        name="${1:-}"
        [ -n "$name" ] || { echo "[wrapper] approve needs <name>" >&2; usage; exit 2; }
        echo "[wrapper] -> python3 tools/oc2/__main__.py approve $name" >&2
        exec python3 "$OC2" approve "$name"
        ;;
    build)
        name="${1:-}"
        [ -n "$name" ] || { echo "[wrapper] build needs <name>" >&2; usage; exit 2; }
        shift
        echo "[wrapper] -> python3 tools/oc2/__main__.py build $name $*" >&2
        exec python3 "$OC2" build "$name" "$@"
        ;;
    smoke)
        name="${1:-}"
        [ -n "$name" ] || { echo "[wrapper] smoke needs <name>" >&2; usage; exit 2; }
        echo "[wrapper] -> python3 tools/oc2/__main__.py smoke $name" >&2
        exec python3 "$OC2" smoke "$name"
        ;;
    status)
        echo "[wrapper] -> python3 tools/oc2/__main__.py status ${1:-}" >&2
        exec python3 "$OC2" status "$@"
        ;;
    -h|--help|help|"")
        usage; exit 0
        ;;
    *)
        echo "[wrapper] unknown subcommand: $cmd" >&2
        usage; exit 2
        ;;
esac
