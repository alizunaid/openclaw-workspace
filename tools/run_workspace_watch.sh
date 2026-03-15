#!/usr/bin/env bash
set -euo pipefail

cd /root/.openclaw/workspace
python3 tools/run_task_registry.py

echo
echo "===== task_registry_run.json ====="
sed -n '1,240p' logs/task_registry_run.json

echo
echo "===== build_priority_watch.json ====="
sed -n '1,240p' logs/build_priority_watch.json

echo
echo "===== project_status_snapshot.json ====="
sed -n '1,240p' logs/project_status_snapshot.json
