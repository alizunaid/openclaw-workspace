#!/usr/bin/env python3
import csv
import hashlib
import re
from pathlib import Path
from datetime import datetime

IN_CSV = Path.home() / ".openclaw/workspace/OPEN_ITEMS_REGISTER_PROJECT.open.threaded.csv"
OUT_CSV = Path.home() / ".openclaw/workspace/WORK_ITEMS_REGISTER.canonical.v4.csv"

STALE_DAYS = 7  # Demote stale WAITING_ON_YOU if thread moved on

STATUS_RANK = {
    "WAITING_ON_YOU": 5,
    "REVIEW": 4,
    "OPEN": 3,
    "WAITING": 2,
    "BLOCKED": 1,
}

RE_PREFIX = re.compile(r"^\s*(re|fw|fwd)\s*:\s*", re.I)

def normalize_subject(s: str) -> str:
    s = (s or "").strip()
    while True:
        new = RE_PREFIX.sub("", s).strip()
        if new == s:
            break
        s = new
    s = s.replace("’", "'").lower()
    s = re.sub(r"\s+", " ", s)
    return s

def parse_date(s: str):
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%a, %d %b %Y %H:%M:%S %z")
    except Exception:
        return None

def wid(key: str) -> str:
    return hashlib.sha1(key.encode("utf-8", errors="ignore")).hexdigest()[:10]

def main():
    with IN_CSV.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        raise SystemExit(f"No rows found in {IN_CSV}")

    grouped = {}

    for r in rows:
        subj_raw = r.get("subject","") or ""
        canon = normalize_subject(subj_raw) or "no_subject"
        work_item_id = wid(canon)

        status = (r.get("status") or "OPEN").strip()
        dt = parse_date(r.get("date",""))
        nxt = (r.get("next_step") or "").strip()
        owner = (r.get("owner") or "").strip()
        cat = (r.get("category") or "").strip()

        if work_item_id not in grouped:
            grouped[work_item_id] = {
                "work_item_id": work_item_id,
                "canonical_key": canon[:240],
                "representative_subject": subj_raw,
                "email_count": 0,
                "owners_seen": set(),
                "categories_seen": set(),
                "_current_dt": None,
                "status_current": "OPEN",
                "next_step_current": "",
                "_best_urgent_rank": -1,
                "status_urgent": "OPEN",
                "next_step_urgent": "",
                "_last_woy_dt": None,
            }

        g = grouped[work_item_id]

        g["email_count"] += 1

        if owner:
            g["owners_seen"].add(owner)
        if cat:
            g["categories_seen"].add(cat)

        if len(subj_raw) > len(g["representative_subject"] or ""):
            g["representative_subject"] = subj_raw

        # CURRENT STATE (most recent email)
        if dt and (g["_current_dt"] is None or dt > g["_current_dt"]):
            g["_current_dt"] = dt
            g["status_current"] = status
            g["next_step_current"] = nxt

        # Track most recent WAITING_ON_YOU
        if status == "WAITING_ON_YOU" and dt:
            if g["_last_woy_dt"] is None or dt > g["_last_woy_dt"]:
                g["_last_woy_dt"] = dt

        # URGENT STATE (highest rank seen historically)
        rank = STATUS_RANK.get(status, 0)
        if rank > g["_best_urgent_rank"]:
            g["_best_urgent_rank"] = rank
            g["status_urgent"] = status
            g["next_step_urgent"] = nxt

    items = list(grouped.values())

    for it in items:
        cur_dt = it.get("_current_dt")
        woy_dt = it.get("_last_woy_dt")

        it["last_activity_date"] = cur_dt.isoformat() if cur_dt else ""
        it["owners_seen"] = "; ".join(sorted(it["owners_seen"])) if it["owners_seen"] else ""
        it["categories_seen"] = "; ".join(sorted(it["categories_seen"])) if it["categories_seen"] else ""

        # Stale demotion
        if cur_dt and woy_dt and it.get("status_current") != "WAITING_ON_YOU":
            age_days = (cur_dt - woy_dt).total_seconds() / 86400.0
            if age_days >= STALE_DAYS and it.get("status_urgent") == "WAITING_ON_YOU":
                it["status_urgent"] = it.get("status_current","")
                it["next_step_urgent"] = it.get("next_step_current","")

        it.pop("_current_dt", None)
        it.pop("_best_urgent_rank", None)
        it.pop("_last_woy_dt", None)

    def sort_key(x):
        try:
            dt = datetime.fromisoformat(x.get("last_activity_date","")) if x.get("last_activity_date") else None
        except Exception:
            dt = None
        return (
            -STATUS_RANK.get(x.get("status_current",""), 0),
            -(dt.timestamp() if dt else 0),
            -int(x.get("email_count","0") or 0)
        )

    items.sort(key=sort_key)

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "work_item_id",
        "status_current","next_step_current",
        "status_urgent","next_step_urgent",
        "last_activity_date","email_count",
        "owners_seen","categories_seen",
        "representative_subject","canonical_key",
    ]

    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(items)

    print(f"Wrote: {OUT_CSV} ({len(items)} canonical v4 work items)")
    print("\nSanity check for W9:")
    for it in items:
        if "w9 af construction" in (it.get("canonical_key","") or ""):
            print(
                "W9:",
                it["work_item_id"],
                "current=", it["status_current"],
                "urgent=", it["status_urgent"],
                "next_current=", it["next_step_current"],
                "next_urgent=", it["next_step_urgent"]
            )
            break

if __name__ == "__main__":
    main()
