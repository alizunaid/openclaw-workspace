#!/usr/bin/env python3

import json
import sys
from http.client import HTTPConnection

def main():
    if len(sys.argv) != 3 or sys.argv[1] != "--goal":
        print("Usage: python3 tools/oc_decompose.py --goal <goal>")
        return 1

    goal = sys.argv[2]
    prompt = f"Break down the following goal into a JSON list of smaller subtasks: {goal}"

    connection = HTTPConnection("localhost", 11434)
    headers = {
        "Content-type": "application/json",
        "Accept": "application/json"
    }
    body = json.dumps({
        "model": "qwen2.5-coder:32b",
        "prompt": prompt,
        "max_tokens": 100
    })

    connection.request("POST", "/v1/completions", body, headers)
    response = connection.getresponse()
    data = json.loads(response.read())

    if response.status == 200:
        try:
            subtasks = json.loads(data["choices"][0]["text"].strip())
            print(subtasks)
        except (KeyError, json.JSONDecodeError) as e:
            print(f"Error decoding JSON: {e}")
            return 1
    else:
        print(f"Error: {response.status} - {data}")
        return 1

if __name__ == "__main__":
    raise SystemExit(main())