#!/usr/bin/env python3
import csv, hashlib, re
from pathlib import Path
from datetime import datetime

IN_CSV = Path.home() / ".openclaw/workspace/OPEN_ITEMS_REGISTER_PROJECT.open.threaded.csv"
OUT_CSV = Path.home() / ".openclaw/workspace/WORK_ITEMS_REGISTER.canonical.csv"

STATUS_RANK = {
    "WAITING_ON_YOU": 5,
    "REVIEW": 4,
    "OPEN": 3,
    "WAITING": 2,
    "BLOCKED": 1,
}

RE_PREFIX = re.compile(r"^\s*(re|fw|fwd)\s*:\s*", re.I)

def norm_subject(s: str) -> str:
    s = (s or "").strip()
    while True:
        new = RE_PREFIX.sub("", s).strip()
        if new == s:
            break
        s = new
    s = re.sub(r"\s+", " ", s)
    return s.lower()

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
        subj = norm_subject(r.get("subject",""))
        if not subj:
            subj = "no_subject"
        canon_key = subj  # canonical grouping key
        work_id = wid(canon_key)

        status = (r.get("status") or "OPEN").strip()
        rank = STATUS_RANK.get(status, 0)
        dt = parse_date(r.get("date",""))

        g = grouped.get(work_id)
        if not g:
            grouped[work_id] = {
                "work_item_id": work_id,
                "canonical_key": canon_key[:240],
                "status_rollup": status,
                "status_rank": rank,
                "last_activity_date": dt,
                "email_count": 1,
                "owners_seen": set([r.get("owner","")]),
                "categories_seen": set([r.get("category","")]),
                "representative_subject": r.get("subject",""),
                "next_step_rollup": r.get("next_step",""),
            }
        else:
            g["email_count"] += 1
            g["owners_seen"].add(r.get("owner",""))
            g["categories_seen"].add(r.get("category",""))

            if rank > g["status_rank"]:
                g["status_rank"] = rank
                g["status_rollup"] = status
                g["next_step_rollup"] = r.get("next_step","") or g["next_step_rollup"]

            if dt and (g["last_activity_date"] is None or dt > g["last_activity_date"]):
                g["last_activity_date"] = dt

            subj0 = r.get("subject","") or ""
            if len(subj0) > len(g["representative_subject"] or ""):
                g["representative_subject"] = subj0

    items = list(grouped.values())

    def sort_key(x):
        dt = x["last_activity_date"]
        return (-x["status_rank"], -(dt.timestamp() if dt else 0), -x["email_count"])

    items.sort(key=sort_key)

    # flatten sets + format
    for it in items:
        dt = it["last_activity_date"]
        it["last_activity_date"] = dt.isoformat() if dt else ""
        it["owners_seen"] = "; ".join(sorted([o for o in it["owners_seen"] if o])) or ""
        it["categories_seen"] = "; ".join(sorted([c for c in it["categories_seen"] if c])) or ""
        it.pop("status_rank", None)

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "work_item_id","status_rollup","last_activity_date","email_count",
        "owners_seen","categories_seen","representative_subject","next_step_rollup","canonical_key"
    ]
    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(items)

    print(f"Wrote: {OUT_CSV} ({len(items)} canonical work items)")
    print("Top 15 canonical work items:")
    for it in items[:15]:
        print(it["work_item_id"], it["status_rollup"], it["email_count"], it["representative_subject"])

if __name__ == "__main__":
    main()
