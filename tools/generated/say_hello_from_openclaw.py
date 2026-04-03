#!/usr/bin/env python3

import json
import os

def main():
    output = {"message": "Hello from OpenClaw"}
    with open(os.path.join("logs", "hello_from_openclaw.json"), "w") as f:
        json.dump(output, f)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())