#!/usr/bin/env python3
import csv, hashlib
from pathlib import Path
from datetime import datetime

IN_CSV = Path.home() / ".openclaw/workspace/OPEN_ITEMS_REGISTER_PROJECT.open.threaded.csv"
OUT_CSV = Path.home() / ".openclaw/workspace/WORK_ITEMS_REGISTER.csv"

# Higher number = higher priority in rollup
STATUS_RANK = {
    "WAITING_ON_YOU": 5,
    "REVIEW": 4,
    "OPEN": 3,
    "WAITING": 2,
    "BLOCKED": 1,
}

def parse_date(s: str):
    if not s:
        return None
    try:
        # Example: "Thu, 05 Feb 2026 19:24:42 +0000"
        return datetime.strptime(s.strip(), "%a, %d %b %Y %H:%M:%S %z")
    except Exception:
        return None

def wid(thread_key: str) -> str:
    return hashlib.sha1(thread_key.encode("utf-8", errors="ignore")).hexdigest()[:10]

def main():
    with IN_CSV.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit(f"No rows found in {IN_CSV}")

    grouped = {}
    for r in rows:
        tk = (r.get("thread_key") or "").strip()
        if not tk:
            continue

        work_id = wid(tk)
        status = (r.get("status") or "OPEN").strip()
        rank = STATUS_RANK.get(status, 0)
        dt = parse_date(r.get("date",""))

        g = grouped.get(work_id)
        if not g:
            grouped[work_id] = {
                "work_item_id": work_id,
                "thread_key": tk,
                "status_rollup": status,
                "status_rank": rank,
                "last_activity_date": dt,
                "email_count": 1,
                "owner": r.get("owner",""),
                "category": r.get("category",""),
                "representative_subject": r.get("subject",""),
                "next_step_rollup": r.get("next_step",""),
            }
        else:
            g["email_count"] += 1

            # roll up strongest status
            if rank > g["status_rank"]:
                g["status_rank"] = rank
                g["status_rollup"] = status
                g["next_step_rollup"] = r.get("next_step","") or g["next_step_rollup"]

            # roll up latest date
            if dt and (g["last_activity_date"] is None or dt > g["last_activity_date"]):
                g["last_activity_date"] = dt

            # choose most specific subject (longer)
            subj = r.get("subject","") or ""
            if len(subj) > len(g["representative_subject"] or ""):
                g["representative_subject"] = subj

            if not g["owner"]:
                g["owner"] = r.get("owner","")
            if not g["category"]:
                g["category"] = r.get("category","")

    items = list(grouped.values())

    def sort_key(x):
        dt = x["last_activity_date"]
        return (-x["status_rank"], -(dt.timestamp() if dt else 0), -x["email_count"])

    items.sort(key=sort_key)

    # stringify date + drop internal rank
    for it in items:
        dt = it["last_activity_date"]
        it["last_activity_date"] = dt.isoformat() if dt else ""
        it.pop("status_rank", None)

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "work_item_id","status_rollup","last_activity_date","email_count",
        "owner","category","representative_subject","next_step_rollup","thread_key"
    ]
    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(items)

    print(f"Wrote: {OUT_CSV} ({len(items)} work items)")
    print("Top 15 work items:")
    for it in items[:15]:
        print(it["work_item_id"], it["status_rollup"], it["email_count"], it["representative_subject"])

if __name__ == "__main__":
    main()
