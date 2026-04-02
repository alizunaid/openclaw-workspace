#!/usr/bin/env python3
"""
OpenClaw Autonomous Agent — oc_autonomous.py

Give it a goal in plain English. It will:
1. Write a Python script to accomplish it
2. Run the script
3. If it fails, feed the error back to GPT and rewrite
4. Repeat up to 6 times
5. Save the working script to tools/generated/
6. Log every iteration to logs/oc_autonomous.jsonl

Usage:
  python3 tools/oc_autonomous.py --goal "Add a staleness alert for items older than 90 days"
  python3 tools/oc_autonomous.py --goal "Fix the scoring" --file tools/analysis/build_priority_watch.py
  python3 tools/oc_autonomous.py --improve tools/analysis/build_priority_watch.py
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, UTC
from pathlib import Path


OPENAI_API_URL = "http://localhost:11434/v1/chat/completions"
MODEL = "llama3.1"
MAX_TOKENS = 4096
MAX_ITERATIONS = 6

IMMUTABLE_PATHS = {
    "WORK_ITEMS_REGISTER.canonical.v4.csv",
    "WORK_ITEMS_REGISTER.upgraded.v4_1.csv",
}

SYSTEM_PROMPT = """\
You are an autonomous Python engineer inside the OpenClaw workspace.
OpenClaw is a deterministic automation machine for managing a pharmacy build project
at 1360 S Main St, Mansfield TX.

Workspace layout:
  tools/analysis/     - analysis scripts
  tools/pharmacy/     - pharmacy domain scripts
  tools/generated/    - YOUR scripts go here
  logs/               - all JSON output goes here
  tasks/task_registry.yaml - task registry

Rules:
  - Never modify WORK_ITEMS_REGISTER.canonical.v4.csv or WORK_ITEMS_REGISTER.upgraded.v4_1.csv
  - All scripts must write JSON output to logs/<script_name>.json
  - Use only Python stdlib — no third-party packages
  - End every script with: if __name__ == "__main__": raise SystemExit(main())
  - Scripts must be runnable as: python3 <script_path>

When writing a new script:
  Return ONLY valid Python code. No markdown. No explanation.
  Start with #!/usr/bin/env python3

When fixing a failing script:
  Return ONLY the complete corrected Python file. No explanation.
"""


def call_llm(messages: list[dict]) -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set.", file=sys.stderr)
        sys.exit(1)

    payload = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
    }

    req = urllib.request.Request(
        OPENAI_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"API error {e.code}: {body[:400]}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Request failed: {e}", file=sys.stderr)
        sys.exit(1)


def clean_code(raw: str) -> str:
    lines = raw.splitlines()
    cleaned = []
    in_fence = False
    for line in lines:
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            cleaned.append(line)
    return "\n".join(cleaned).strip()


def run_script(script_path: Path, workspace: Path) -> tuple[int, str, str]:
    result = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def get_script_name(goal: str) -> str:
    messages = [{"role": "user", "content": f"Give me a short snake_case filename (no .py) for a script that: {goal}. Return ONLY the filename."}]
    system_override = "Return ONLY a short snake_case filename without .py. No explanation. Max 5 words."
    payload = {
        "model": MODEL,
        "max_tokens": 30,
        "messages": [{"role": "system", "content": system_override}] + messages,
    }
    api_key = os.environ.get("OPENAI_API_KEY", "")
    req = urllib.request.Request(
        OPENAI_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            slug = (data.get("choices") or [{}])[0].get("message", {}).get("content", "generated_script").strip()
            slug = slug.replace(".py", "").strip()
            slug = "".join(c if c.isalnum() or c == "_" else "_" for c in slug)[:40]
            return slug
    except Exception:
        return "generated_script"


def autonomous_loop(
    goal: str,
    workspace: Path,
    existing_file: Path | None = None,
    allow_overwrite: bool = False,
) -> int:
    logs_dir = workspace / "logs"
    generated_dir = workspace / "tools" / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "oc_autonomous.jsonl"

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    print(f"\n{'='*60}")
    print(f"OpenClaw Autonomous Agent — {run_id}")
    print(f"Goal: {goal}")
    if existing_file:
        print(f"File: {existing_file}")
    print(f"{'='*60}\n")

    if existing_file and allow_overwrite:
        script_path = existing_file
    else:
        slug = get_script_name(goal)
        script_path = generated_dir / f"{slug}.py"

    print(f"Target: {script_path}")

    if existing_file and existing_file.exists():
        code = existing_file.read_text(encoding="utf-8")
        initial = (
            f"Existing file ({existing_file.name}):\n\n{code}\n\n"
            f"Goal: {goal}\n\nRewrite the file to accomplish the goal. Return the full file."
        )
    else:
        initial = f"Goal: {goal}\n\nWrite a new Python script to accomplish this."

    messages: list[dict] = [{"role": "user", "content": initial}]

    success = False
    last_error = None

    for iteration in range(1, MAX_ITERATIONS + 1):
        print(f"\n--- Iteration {iteration}/{MAX_ITERATIONS} ---")
        print("Calling GPT...", flush=True)

        raw = call_llm(messages)
        code = clean_code(raw)

        script_path.write_text(code, encoding="utf-8")
        print(f"Wrote {script_path} ({len(code)} chars)")

        print("Running...", flush=True)
        try:
            rc, stdout, stderr = run_script(script_path, workspace)
        except subprocess.TimeoutExpired:
            rc, stdout, stderr = 1, "", "Timed out after 60s."

        print(f"Return code: {rc}")
        if stdout:
            print(f"stdout: {stdout[:300]}")
        if stderr:
            print(f"stderr: {stderr[:300]}")

        append_jsonl(log_path, {
            "run_id": run_id,
            "iteration": iteration,
            "goal": goal,
            "script_path": str(script_path),
            "returncode": rc,
            "stdout": stdout[:500],
            "stderr": stderr[:500],
            "timestamp_utc": datetime.now(UTC).isoformat(),
        })

        if rc == 0:
            print(f"\n✓ Success on iteration {iteration}!")
            success = True
            break

        last_error = stderr or stdout or "Non-zero exit, no output."
        print(f"\nFailed. Retrying with error feedback...")
        messages.append({"role": "assistant", "content": raw})
        messages.append({
            "role": "user",
            "content": (
                f"The script failed (exit code {rc}).\n\n"
                f"Error:\n{last_error[:2000]}\n\n"
                "Fix it. Return the complete corrected Python file only."
            ),
        })

    if not success:
        print(f"\n✗ Failed after {MAX_ITERATIONS} iterations.")
        print(f"Last error: {last_error}")
        return 1

    summary = {
        "run_id": run_id,
        "goal": goal,
        "script_path": str(script_path),
        "iterations": iteration,
        "success": True,
        "timestamp_utc": datetime.now(UTC).isoformat(),
    }
    summary_path = logs_dir / "oc_autonomous_last_run.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"\nSaved: {script_path}")
    print(f"Log:   {log_path}")
    return 0


def improve_mode(file_path: Path, workspace: Path) -> int:
    if not file_path.exists():
        print(f"File not found: {file_path}", file=sys.stderr)
        return 1
    code = file_path.read_text(encoding="utf-8")
    review_msg = f"Review this script and list all bugs and improvements needed:\n\n{code}"
    print(f"Reviewing {file_path.name}...")
    review = call_llm([{"role": "user", "content": review_msg}])
    print(f"\nReview:\n{review}\n")
    goal = f"Fix all issues found in the review of {file_path.name}."
    return autonomous_loop(goal=goal, workspace=workspace, existing_file=file_path, allow_overwrite=True)


def main() -> int:
    workspace = Path(__file__).resolve().parents[1]

    parser = argparse.ArgumentParser(description="OpenClaw Autonomous Agent")
    parser.add_argument("--goal", help="Goal for a new or modified script")
    parser.add_argument("--file", help="Existing script to modify toward the goal")
    parser.add_argument("--improve", help="Script to auto-review and fix")
    parser.add_argument("--allow-overwrite", action="store_true")
    args = parser.parse_args()

    if args.improve:
        return improve_mode(Path(args.improve), workspace)
    if not args.goal:
        parser.print_help()
        return 1

    return autonomous_loop(
        goal=args.goal,
        workspace=workspace,
        existing_file=Path(args.file) if args.file else None,
        allow_overwrite=args.allow_overwrite,
    )


if __name__ == "__main__":
    raise SystemExit(main())
