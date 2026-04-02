#!/usr/bin/env python3

import json
output = {"message": "Hello World"}
with open("logs/print_hello_world.json", "w") as f:
    json.dump(output, f)

def main():
    print("Hello World")

if __name__ == "__main__":
    raise SystemExit(main())