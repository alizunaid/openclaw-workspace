#!/usr/bin/env python3

import json
from datetime import datetime, UTC
from pathlib import Path


def main() -> int:
    workspace = Path(__file__).resolve().parents[2]
    logs_dir = workspace / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    output_path = logs_dir / "supplier_monitor.json"

    payload = {
        "task": "pharmacy_supplier_monitor",
        "status": "ok",
        "mode": "starter_noop",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "notes": [
            "Starter script created successfully.",
            "No supplier data sources connected yet.",
            "Safe no-op execution completed."
        ]
    }

    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
