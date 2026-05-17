#!/usr/bin/env python3
import csv, re
from pathlib import Path

IN_MD = Path.home() / ".openclaw/workspace/OPEN_ITEMS_REGISTER_PROJECT.md"
OUT_CSV = Path.home() / ".openclaw/workspace/OPEN_ITEMS_REGISTER_PROJECT.csv"

CATEGORY_RULES = [
    ("FINANCE", re.compile(r"\b(ach|wire|payment|disbursement|invoice|reimbursement|bank|sba|loan)\b", re.I)),
    ("ENGINEERING", re.compile(r"\b(mep|civil|survey|geotech|boring|site plan|cad|drainage|grading|utility)\b", re.I)),
    ("PERMITS_INSPECTIONS", re.compile(r"\b(permit|inspection|fire marshal|city|plan review|dr(c)?|certificate of occupancy|co\b)\b", re.I)),
    ("CLEANROOM_USP", re.compile(r"\b(usp\s*797|usp\s*800|cleanroom|iso\s*5|iso\s*7|pec|sec|hazardous)\b", re.I)),
    ("VENDORS_EQUIPMENT", re.compile(r"\b(quote|proposal|vendor|equipment|purchase order|pi\b|proforma)\b", re.I)),
    ("LEGAL", re.compile(r"\b(contract|agreement|lease|attorney|nda)\b", re.I)),
]

OWNER_RULES = [
    ("Teresa/Bank", re.compile(r"\bffb\b|tcraig@ffb1\.com|teresa", re.I)),
    ("GC/Construction", re.compile(r"construction|demolition|afconstruction|aponte", re.I)),
    ("Engineer/Architect", re.compile(r"engineer|mep|civil|survey|bannister|ssbdesigns|sheri|landtec|gtaeng", re.I)),
    ("Vendor/Sourcing", re.compile(r"js-sourcing|proforma|quote|vendor|florian|jenny", re.I)),
    ("City/Authority", re.compile(r"mansfieldtexas\.gov|fire marshal|permit|plan review|drc", re.I)),
]

def guess_category(text: str) -> str:
    for name, rx in CATEGORY_RULES:
        if rx.search(text):
            return name
    return "UNCLASSIFIED"

def guess_owner(text: str) -> str:
    for name, rx in OWNER_RULES:
        if rx.search(text):
            return name
    return "You"

def parse_md_rows(md: str):
    rows = []
    for line in md.splitlines():
        if not line.startswith("|"):
            continue
        if line.startswith("|---"):
            continue
        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        if len(parts) != 5:
            continue
        email_id, date, frm, subject, ask = parts
        if not email_id.isdigit():
            continue
        rows.append({
            "email_id": int(email_id),
            "date": date,
            "from": frm,
            "subject": subject,
            "ask": ask,
        })
    return rows

def main():
    md = IN_MD.read_text(encoding="utf-8", errors="ignore")
    rows = parse_md_rows(md)

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["email_id","date","from","subject","ask","category","owner","deadline","status","next_step"]
        )
        w.writeheader()
        for r in rows:
            blob = " ".join([r["from"], r["subject"], r["ask"]])
            r["category"] = guess_category(blob)
            r["owner"] = guess_owner(blob)
            r["deadline"] = ""
            r["status"] = "OPEN"
            r["next_step"] = r["ask"]
            w.writerow(r)

    print(f"Wrote: {OUT_CSV} ({len(rows)} rows)")

if __name__ == "__main__":
    main()
