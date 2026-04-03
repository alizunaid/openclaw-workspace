#!/usr/bin/env python3

import json
import os

def main():
    greeting = {"message": "Hello from OpenClaw"}
    output_path = os.path.join("logs", f"{os.path.basename(__file__)}.json")
    
    with open(output_path, 'w') as file:
        json.dump(greeting, file, indent=4)

if __name__ == "__main__":
    raise SystemExit(main())