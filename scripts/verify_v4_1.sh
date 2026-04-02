#!/bin/sh
set -e

python3 -m py_compile scripts/upgrade_next_steps_from_bodies_v4_1.py
python3 scripts/upgrade_next_steps_from_bodies_v4_1.py
# AUTOPILOT_RESTORE_TEST
