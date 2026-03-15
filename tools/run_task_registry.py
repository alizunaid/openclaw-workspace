#!/usr/bin/env python3

import json
import subprocess
import sys
from datetime import datetime, UTC
from pathlib import Path


def parse_tasks(registry_path: Path) -> list[dict]:
    tasks: list[dict] = []
    current: dict = {}

    for raw_line in registry_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped or stripped == "tasks:":
            continue

        if stripped.startswith("- name:"):
            if current:
                tasks.append(current)
            current = {"name": stripped.split(":", 1)[1].strip()}
            continue

        if ":" in stripped and current:
            key, value = stripped.split(":", 1)
            value = value.strip()

            if value.lower() == "true":
                parsed_value = True
            elif value.lower() == "false":
                parsed_value = False
            else:
                parsed_value = value

            current[key.strip()] = parsed_value

    if current:
        tasks.append(current)

    return tasks


def main() -> int:
    workspace = Path(__file__).resolve().parents[1]
    registry_path = workspace / "tasks" / "task_registry.yaml"
    logs_dir = workspace / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    if not registry_path.exists():
        print(f"Registry not found: {registry_path}", file=sys.stderr)
        return 1

    tasks = parse_tasks(registry_path)
    run_utc = datetime.now(UTC).isoformat()

    summary_lines = [
        f"run_utc={run_utc}",
        f"registry={registry_path}",
        f"task_count={len(tasks)}",
        "",
    ]

    summary_json: dict = {
        "run_utc": run_utc,
        "registry": str(registry_path),
        "task_count": len(tasks),
        "results": [],
    }

    overall_rc = 0

    for task in tasks:
        name = str(task.get("name", "unknown"))
        script_rel = task.get("script")
        enabled = bool(task.get("enabled", False))

        task_result = {
            "name": name,
            "enabled": enabled,
            "script": str(script_rel) if script_rel is not None else None,
        }

        summary_lines.append(f"[task] {name}")
        summary_lines.append(f"enabled={enabled}")
        summary_lines.append(f"script={script_rel}")

        if not enabled:
            summary_lines.append("result=skipped_disabled")
            summary_lines.append("")
            task_result["result"] = "skipped_disabled"
            summary_json["results"].append(task_result)
            continue

        if not script_rel:
            summary_lines.append("result=error_missing_script")
            summary_lines.append("")
            task_result["result"] = "error_missing_script"
            summary_json["results"].append(task_result)
            overall_rc = 1
            continue

        script_path = workspace / str(script_rel)
        task_result["resolved_script"] = str(script_path)

        if not script_path.exists():
            summary_lines.append(f"result=error_script_not_found:{script_path}")
            summary_lines.append("")
            task_result["result"] = "error_script_not_found"
            summary_json["results"].append(task_result)
            overall_rc = 1
            continue

        proc = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(workspace),
            capture_output=True,
            text=True,
        )

        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()

        summary_lines.append(f"returncode={proc.returncode}")

        if stdout:
            summary_lines.append("stdout:")
            summary_lines.extend(stdout.splitlines())

        if stderr:
            summary_lines.append("stderr:")
            summary_lines.extend(stderr.splitlines())

        result_label = "ok" if proc.returncode == 0 else "failed"
        summary_lines.append(f"result={result_label}")
        summary_lines.append("")

        task_result["returncode"] = proc.returncode
        task_result["stdout"] = stdout
        task_result["stderr"] = stderr
        task_result["result"] = result_label
        summary_json["results"].append(task_result)

        if proc.returncode != 0:
            overall_rc = 1

    summary_json["overall_rc"] = overall_rc
    summary_json["overall_result"] = "ok" if overall_rc == 0 else "failed"

    summary_path = logs_dir / "task_registry_run.log"
    summary_json_path = logs_dir / "task_registry_run.json"

    summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    summary_json_path.write_text(json.dumps(summary_json, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {summary_path}")
    print(f"Wrote {summary_json_path}")

    return overall_rc


if __name__ == "__main__":
    raise SystemExit(main())
