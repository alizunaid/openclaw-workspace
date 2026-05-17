#!/usr/bin/env python3
import csv, hashlib, re
from pathlib import Path
from datetime import datetime

IN_CSV = Path.home() / ".openclaw/workspace/OPEN_ITEMS_REGISTER_PROJECT.open.threaded.csv"
OUT_CSV = Path.home() / ".openclaw/workspace/WORK_ITEMS_REGISTER.canonical.v3.csv"

STATUS_RANK = {
    "WAITING_ON_YOU": 5,
    "REVIEW": 4,
    "OPEN": 3,
    "WAITING": 2,
    "BLOCKED": 1,
}

RE_PREFIX = re.compile(r"^\s*(re|fw|fwd)\s*:\s*", re.I)

def normalize_text(s: str) -> str:
    s = (s or "").strip().lower()
    while True:
        new = RE_PREFIX.sub("", s).strip()
        if new == s:
            break
        s = new
    s = s.replace("’", "'")
    s = s.replace("yesterdays", "yesterday's")
    s = s.replace("yesterday’s", "yesterday's")
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

def ts(dt):
    return dt.timestamp() if dt else 0

def main():
    with IN_CSV.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit(f"No rows found in {IN_CSV}")

    grouped = {}

    for r in rows:
        subj_raw = r.get("subject","") or ""
        canon_key = normalize_text(subj_raw) or "no_subject"
        work_id = wid(canon_key)

        status = (r.get("status") or "OPEN").strip()
        rank = STATUS_RANK.get(status, 0)
        dt = parse_date(r.get("date",""))
        nxt = (r.get("next_step") or "").strip()

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
                "representative_subject": subj_raw,
                "_best_overall": (dt, nxt),
                "_best_per_status": {status: (dt, nxt)},
            }
        else:
            g["email_count"] += 1
            g["owners_seen"].add(r.get("owner",""))
            g["categories_seen"].add(r.get("category",""))

            if dt and (g["last_activity_date"] is None or dt > g["last_activity_date"]):
                g["last_activity_date"] = dt

            if len(subj_raw) > len(g["representative_subject"] or ""):
                g["representative_subject"] = subj_raw

            if rank > g["status_rank"]:
                g["status_rank"] = rank
                g["status_rollup"] = status

            prev_dt, _ = g["_best_overall"]
            if dt and (prev_dt is None or dt > prev_dt):
                g["_best_overall"] = (dt, nxt)

            prev = g["_best_per_status"].get(status)
            if prev is None or (dt and ts(dt) > ts(prev[0])):
                g["_best_per_status"][status] = (dt, nxt)

    items = list(grouped.values())

    # choose next_step_rollup based on: rolled-up status, most recent within that status
    for it in items:
        roll = it["status_rollup"]
        pick = it["_best_per_status"].get(roll)
        if pick and pick[1]:
            it["next_step_rollup"] = pick[1]
        else:
            it["next_step_rollup"] = (it["_best_overall"][1] if it["_best_overall"] else "")

    # sort
    def sort_key(x):
        dt = x["last_activity_date"]
        return (-x["status_rank"], -(dt.timestamp() if dt else 0), -x["email_count"])

    items.sort(key=sort_key)

    # flatten + format + drop internals
    for it in items:
        dt = it["last_activity_date"]
        it["last_activity_date"] = dt.isoformat() if dt else ""
        it["owners_seen"] = "; ".join(sorted([o for o in it["owners_seen"] if o])) or ""
        it["categories_seen"] = "; ".join(sorted([c for c in it["categories_seen"] if c])) or ""
        it.pop("status_rank", None)
        it.pop("_best_overall", None)
        it.pop("_best_per_status", None)

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "work_item_id","status_rollup","last_activity_date","email_count",
        "owners_seen","categories_seen","representative_subject","next_step_rollup","canonical_key"
    ]
    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(items)

    print(f"Wrote: {OUT_CSV} ({len(items)} canonical v3 work items)")
    print("Top 10 canonical v3 work items:")
    for it in items[:10]:
        print(it["work_item_id"], it["status_rollup"], it["email_count"], it["representative_subject"], "=>", it["next_step_rollup"])

if __name__ == "__main__":
    main()
