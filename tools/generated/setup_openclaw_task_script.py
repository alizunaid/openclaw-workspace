#!/usr/bin/env python3

import os
import json

def main():
    script_content = """#!/bin/bash
cd /root/.openclaw/workspace || exit
result=$(python3 tools/oc_autonomous.py --goal "$*")
echo "$result"
"""

    script_path = "tools/openclaw_task.sh"
    with open(script_path, "w") as file:
        file.write(script_content)

    os.chmod(script_path, 0o755)

    output = {
        "script_created": script_path,
        "permissions_set": oct(0o755)
    }

    with open("logs/create_openclaw_task_sh.json", "w") as log_file:
        json.dump(output, log_file, indent=4)

if __name__ == "__main__":
    raise SystemExit(main())