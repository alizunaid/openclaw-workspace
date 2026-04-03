#!/usr/bin/env python3
import argparse
import json
import urllib.request

def decompose(goal: str) -> list:
    payload = json.dumps({
        "model": "qwen2.5-coder:32b",
        "messages": [
            {"role": "system", "content": "You are a task decomposer. Break the given goal into a JSON array of short, specific subtasks. Return ONLY a JSON array of strings, nothing else."},
            {"role": "user", "content": f"Break this into subtasks: {goal}"}
        ],
        "stream": False
    }).encode()
    req = urllib.request.Request(
        "http://localhost:11434/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read())
    text = data["choices"][0]["message"]["content"].strip()
    return json.loads(text)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--goal", required=True)
    args = parser.parse_args()
    subtasks = decompose(args.goal)
    for i, task in enumerate(subtasks, 1):
        print(f"{i}. {task}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
