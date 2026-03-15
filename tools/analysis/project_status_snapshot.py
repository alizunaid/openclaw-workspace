#!/usr/bin/env python3

import json
from datetime import datetime, UTC
from pathlib import Path


def count_lines(path: Path) -> int | None:
    if not path.exists() or not path.is_file():
        return None
    return len(path.read_text(encoding="utf-8", errors="replace").splitlines())


def main() -> int:
    workspace = Path(__file__).resolve().parents[2]
    logs_dir = workspace / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    mission_path = workspace / "MISSION.md"
    registry_path = workspace / "tasks" / "task_registry.yaml"
    work_items_path = workspace / "WORK_ITEMS_REGISTER.upgraded.v4_1.csv"
    top10_path = workspace / "TODAY_TOP10.md"
    open_items_path = workspace / "OPEN_ITEMS_REGISTER.md"
    output_path = logs_dir / "project_status_snapshot.json"

    tools_dir = workspace / "tools"
    pharmacy_dir = tools_dir / "pharmacy"
    analysis_dir = tools_dir / "analysis"

    pharmacy_scripts = sorted([p.name for p in pharmacy_dir.glob("*.py")]) if pharmacy_dir.exists() else []
    analysis_scripts = sorted([p.name for p in analysis_dir.glob("*.py")]) if analysis_dir.exists() else []

    checks = {
        "mission_exists": mission_path.exists(),
        "task_registry_exists": registry_path.exists(),
        "work_items_register_exists": work_items_path.exists(),
        "today_top10_exists": top10_path.exists(),
        "open_items_register_exists": open_items_path.exists(),
    }

    score = sum(1 for value in checks.values() if value)
    max_score = len(checks)

    if score == max_score:
        health_status = "green"
    elif score >= 3:
        health_status = "yellow"
    else:
        health_status = "red"

    payload = {
        "task": "project_status_snapshot",
        "status": "ok",
        "mode": "workspace_baseline",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "health": {
            "score": score,
            "max_score": max_score,
            "status": health_status,
        },
        "checks": checks,
        "counts": {
            "mission_lines": count_lines(mission_path),
            "task_registry_lines": count_lines(registry_path),
            "work_items_register_lines": count_lines(work_items_path),
            "today_top10_lines": count_lines(top10_path),
            "open_items_register_lines": count_lines(open_items_path),
            "pharmacy_script_count": len(pharmacy_scripts),
            "analysis_script_count": len(analysis_scripts),
        },
        "discovered_scripts": {
            "pharmacy": pharmacy_scripts,
            "analysis": analysis_scripts,
        },
        "notes": [
            "Snapshot now inspects baseline workspace signals.",
            "This is still deterministic and read-only.",
            "Health score is a simple baseline presence check."
        ]
    }

    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
