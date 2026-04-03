#!/bin/bash
cd /root/.openclaw/workspace || exit
result=$(python3 tools/oc_autonomous.py --goal "$*")
echo "$result"
