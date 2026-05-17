#!/usr/bin/env python3
import csv, re
from pathlib import Path
from collections import Counter

IN_CSV = Path.home() / ".openclaw/workspace/OPEN_ITEMS_REGISTER_PROJECT.open.csv"
OUT_CSV = Path.home() / ".openclaw/workspace/OPEN_ITEMS_REGISTER_PROJECT.open.threaded.csv"

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

def sender_domain(frm: str) -> str:
    frm = frm or ""
    m = re.search(r"@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})", frm)
    return (m.group(1).lower() if m else "unknown")

def main():
    with IN_CSV.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit(f"No rows found in {IN_CSV}")

    for r in rows:
        subj = norm_subject(r.get("subject", ""))
        dom = sender_domain(r.get("from", ""))
        owner = (r.get("owner", "") or "unknown").strip().lower().replace(" ", "_")
        r["thread_key"] = f"{owner}|{dom}|{subj}"[:240]

    fieldnames = list(rows[0].keys())
    if "thread_key" not in fieldnames:
        fieldnames.append("thread_key")

    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    print(f"Wrote: {OUT_CSV} ({len(rows)} rows)")
    c = Counter(r["thread_key"] for r in rows)
    print("Top 10 threads:")
    for k, n in c.most_common(10):
        print(n, k)

if __name__ == "__main__":
    main()
