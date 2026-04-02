#!/usr/bin/env python3
import csv
from pathlib import Path

IN_CSV = Path.home() / ".openclaw/workspace/OPEN_ITEMS_REGISTER_PROJECT.enriched.csv"
OUT_OPEN = Path.home() / ".openclaw/workspace/OPEN_ITEMS_REGISTER_PROJECT.open.csv"
OUT_ACTIVITY = Path.home() / ".openclaw/workspace/OPEN_ITEMS_REGISTER_PROJECT.activity.csv"

OPEN_STATUSES = {"OPEN", "WAITING", "WAITING_ON_YOU", "REVIEW", "BLOCKED"}
ACTIVITY_STATUSES = {"INFO", "DONE"}

def main():
    with IN_CSV.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        raise SystemExit("No rows found in enriched CSV.")

    OUT_OPEN.parent.mkdir(parents=True, exist_ok=True)

    open_rows = [r for r in rows if r.get("status","") in OPEN_STATUSES]
    act_rows  = [r for r in rows if r.get("status","") in ACTIVITY_STATUSES]

    # Anything else (unknown statuses) stays in OPEN to avoid missing it
    unknown = [r for r in rows if r.get("status","") not in (OPEN_STATUSES | ACTIVITY_STATUSES)]
    open_rows.extend(unknown)

    with OUT_OPEN.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(open_rows)

    with OUT_ACTIVITY.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(act_rows)

    print(f"Wrote OPEN: {OUT_OPEN} ({len(open_rows)} rows)")
    print(f"Wrote ACTIVITY: {OUT_ACTIVITY} ({len(act_rows)} rows)")
    if unknown:
        print(f"WARNING: {len(unknown)} rows had unknown status and were kept in OPEN.")

if __name__ == "__main__":
    main()
