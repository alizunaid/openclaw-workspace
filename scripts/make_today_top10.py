#!/usr/bin/env python3
import csv
from pathlib import Path
from datetime import datetime

IN_CSV = Path.home() / ".openclaw/workspace/WORK_ITEMS_REGISTER.canonical.v2.csv"
OUT_MD = Path.home() / ".openclaw/workspace/TODAY_TOP10.md"

def parse_iso(dt: str):
    if not dt:
        return None
    try:
        return datetime.fromisoformat(dt)
    except Exception:
        return None

def main():
    with IN_CSV.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit("No work items found.")

    # Focus: WAITING_ON_YOU first, then REVIEW, then OPEN/WAITING
    rank = {"WAITING_ON_YOU": 5, "REVIEW": 4, "OPEN": 3, "WAITING": 2, "BLOCKED": 1}
    def sort_key(r):
        dt = parse_iso(r.get("last_activity_date",""))
        return (-rank.get(r.get("status_rollup",""), 0), -(dt.timestamp() if dt else 0), -int(r.get("email_count","0") or 0))

    rows.sort(key=sort_key)

    top = rows[:10]

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = []
    lines.append(f"# TODAY TOP 10 — {now}")
    lines.append("")
    lines.append("## Highest leverage actions (ordered)")
    lines.append("")
    for i, r in enumerate(top, 1):
        lines.append(f"### {i}) {r.get('representative_subject','').strip()}")
        lines.append(f"- **Status:** {r.get('status_rollup','')}")
        lines.append(f"- **Last activity:** {r.get('last_activity_date','')}")
        lines.append(f"- **Emails in thread:** {r.get('email_count','')}")
        lines.append(f"- **Owners seen:** {r.get('owners_seen','')}")
        lines.append(f"- **Categories:** {r.get('categories_seen','')}")
        lines.append(f"- **Next step:** {r.get('next_step_rollup','')}")
        lines.append(f"- **Work Item ID:** `{r.get('work_item_id','')}`")
        lines.append("")

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote: {OUT_MD}")
    print("--- Preview ---")
    print("\n".join(lines[:40]))

if __name__ == "__main__":
    main()
