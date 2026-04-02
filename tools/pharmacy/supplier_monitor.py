#!/usr/bin/env python3
"""
Nexadose Pharmacy – Supplier Monitor
=====================================
Reads the canonical work items register and reports on vendor/equipment
supplier status for the Nexadose pharmacy facility at 1360 S Main St,
Mansfield TX.

Deterministic: no external API calls, no network access.
Output: logs/supplier_monitor.json
"""

import csv
import json
from collections import defaultdict
from datetime import datetime, UTC
from pathlib import Path


# ---------------------------------------------------------------------------
# Known supplier definitions — seeded from workspace data
# Each entry: display name -> matching keywords (checked against canonical_key
# and representative_subject, case-insensitive).
# ---------------------------------------------------------------------------

KNOWN_SUPPLIERS: dict[str, dict] = {
    "DERSION Clean Room": {
        "category": "Cleanroom Equipment",
        "contact": "Eden",
        "notes": "Primary cleanroom equipment vendor. Multiple quotations Oct–Dec 2025. "
                 "Sales contract issued. Lead time quoted from receipt of deposit.",
        "keywords": ["dersion", "nexadose cleanroom"],
        "filing_docs": [
            "Nexadose USA Pharmacy Clean Rooom Design V3 -DERSION.pdf",
            "NEXADOSE CLEANROOM - DERSION OFFER UPDATED 20251113",
        ],
    },
    "Airkey": {
        "category": "Cleanroom Equipment",
        "contact": None,
        "notes": "Modular cleanroom supplier. Model NE25222-2501 (USP 797/800 compliant). "
                 "Quotation AKQO-NE25222-2501-Zunaid-Modular Cleanroom on file.",
        "keywords": ["airkey", "ne25222", "cleanroom panel specs"],
        "filing_docs": [
            "Airkey NE25222-2501 America USP797_800 modular cleanroom-2025-11-05V1.pdf",
            "Quotation-AKQO-NE25222-2501-Zunaid-Modular Cleanroom-1105-V01.pdf",
            "Catalog of Fume Hood AIRKEY2025.pdf",
        ],
    },
    "JS Sourcing (JSND)": {
        "category": "Lab Supplies & Consumables / Sourcing Consultant",
        "contact": "Florian Chauvin",
        "notes": "Sourcing consultant for lab supplies, consumables, and equipment procurement. "
                 "Invoices JSND-IN25001 and JSND-IN25002 on file. Factory visit scheduled.",
        "keywords": [
            "jsnd", "js sourcing", "lab supplies", "sourcing [supplies]",
            "sourcing update", "sourcing inquiry", "factory visit",
            "proforma invoice", "supplies + proforma",
        ],
        "filing_docs": [
            "JSND-IN25002.pdf",
            "Wire 1 Invoice.pdf",
        ],
    },
    "Alliance": {
        "category": "Vendors & Equipment (proposal stage)",
        "contact": None,
        "notes": "Submitted proposal #ARS-2025-2299. Nature of scope not fully specified in register.",
        "keywords": ["alliance", "ars-2025"],
        "filing_docs": [],
    },
    "Pharmacists Mutual": {
        "category": "Professional Liability Insurance",
        "contact": None,
        "notes": "Professional liability and pharmacy insurance quote. Summary on file.",
        "keywords": ["pharmacists mutual"],
        "filing_docs": [
            "Pharmacists Mutual Professonal Liabilty Summary.pdf",
            "Pharmacy Quote information 2025.xls",
        ],
    },
    "SRM United": {
        "category": "Banking / Deposit Account",
        "contact": None,
        "notes": "Deposit account used for sourcing consultant payment (account #3091).",
        "keywords": ["srm united", "deposit account"],
        "filing_docs": [],
    },
    "China 2 West / JustChinait": {
        "category": "Equipment Import (China) – Exploratory",
        "contact": None,
        "notes": "Two separate contacts explored for importing medical/cleanroom equipment from China. "
                 "Still in proposal/introduction stage as of last activity.",
        "keywords": ["china 2 west", "justchinait", "importing medical equipment from china",
                     "0078/25", "cleanroom source"],
        "filing_docs": [],
    },
}

# Categories that indicate vendor/equipment-related work items
VENDOR_CATEGORIES = {"VENDORS_EQUIPMENT", "CLEANROOM_USP"}

# Work item statuses — ordered from most urgent to least
STATUS_PRIORITY = {
    "WAITING_ON_YOU": 0,
    "REVIEW": 1,
    "OPEN": 2,
    "WAITING": 3,
    "DONE": 4,
    "CLOSED": 5,
}


def parse_isoish_datetime(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def load_work_items(csv_path: Path) -> list[dict]:
    if not csv_path.exists() or not csv_path.is_file():
        return []
    with csv_path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def is_vendor_related(row: dict) -> bool:
    """Return True if this work item belongs to a vendor/equipment category."""
    categories = {c.strip() for c in (row.get("categories_seen") or "").split(";") if c.strip()}
    return bool(categories & VENDOR_CATEGORIES)


def match_supplier(row: dict, supplier_keywords: list[str]) -> bool:
    """Return True if the work item matches any of the supplier's keywords."""
    haystack = " | ".join([
        row.get("canonical_key", ""),
        row.get("representative_subject", ""),
    ]).lower()
    return any(kw.lower() in haystack for kw in supplier_keywords)


def row_to_item_summary(row: dict) -> dict:
    return {
        "work_item_id": row.get("work_item_id", ""),
        "status": row.get("status_current", ""),
        "status_urgent": row.get("status_urgent", ""),
        "subject": row.get("representative_subject", ""),
        "next_step": row.get("next_step_current", ""),
        "last_activity_date": row.get("last_activity_date", ""),
        "owners_seen": row.get("owners_seen", ""),
        "categories": row.get("categories_seen", ""),
    }


def most_urgent_status(items: list[dict]) -> str:
    if not items:
        return "NO_ITEMS"
    return min(
        (i.get("status", "UNKNOWN") for i in items),
        key=lambda s: STATUS_PRIORITY.get(s, 99),
    )


def days_since(iso_date: str) -> int | None:
    dt = parse_isoish_datetime(iso_date)
    if dt is None:
        return None
    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    return max((now - dt).days, 0)


def build_supplier_report(
    supplier_name: str,
    supplier_def: dict,
    matched_items: list[dict],
    unmatched_vendor_items: list[dict] | None = None,
) -> dict:
    sorted_items = sorted(
        matched_items,
        key=lambda i: (STATUS_PRIORITY.get(i.get("status", ""), 99), i.get("last_activity_date", "")),
    )

    last_activity = max(
        (i.get("last_activity_date", "") for i in matched_items),
        default=None,
    ) if matched_items else None

    return {
        "supplier": supplier_name,
        "category": supplier_def.get("category", ""),
        "contact": supplier_def.get("contact"),
        "notes": supplier_def.get("notes", ""),
        "overall_status": most_urgent_status(sorted_items),
        "item_count": len(matched_items),
        "last_activity_date": last_activity,
        "days_since_last_activity": days_since(last_activity) if last_activity else None,
        "filing_docs_expected": supplier_def.get("filing_docs", []),
        "items": sorted_items,
    }


def check_filing_presence(workspace: Path, supplier_def: dict) -> list[str]:
    """Return a list of expected filing docs that were found in the workspace."""
    found = []
    for doc_name in supplier_def.get("filing_docs", []):
        # Search across all subdirectories of filing/ and organized_attachments/
        for search_root in [workspace / "filing", workspace / "organized_attachments"]:
            if not search_root.exists():
                continue
            for candidate in search_root.rglob("*"):
                if doc_name.lower() in candidate.name.lower():
                    found.append(str(candidate.relative_to(workspace)))
                    break
    return found


def main() -> int:
    workspace = Path(__file__).resolve().parents[2]
    logs_dir = workspace / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    output_path = logs_dir / "supplier_monitor.json"

    csv_path = workspace / "WORK_ITEMS_REGISTER.canonical.v4.csv"
    all_rows = load_work_items(csv_path)

    vendor_rows = [r for r in all_rows if is_vendor_related(r)]

    # Match each row to a known supplier
    assigned_ids: set[str] = set()
    supplier_reports: list[dict] = []

    for supplier_name, supplier_def in KNOWN_SUPPLIERS.items():
        keywords = supplier_def.get("keywords", [])
        matched = [r for r in vendor_rows if match_supplier(r, keywords)]
        assigned_ids.update(r.get("work_item_id", "") for r in matched)

        items = [row_to_item_summary(r) for r in matched]
        filing_found = check_filing_presence(workspace, supplier_def)

        report = build_supplier_report(supplier_name, supplier_def, items)
        report["filing_docs_found"] = filing_found
        report["filing_docs_missing"] = [
            d for d in supplier_def.get("filing_docs", [])
            if not any(d.lower() in f.lower() for f in filing_found)
        ]
        supplier_reports.append(report)

    # Unmatched vendor items (not attributed to a known supplier)
    unmatched = [
        row_to_item_summary(r)
        for r in vendor_rows
        if r.get("work_item_id", "") not in assigned_ids
    ]

    # Summary metrics
    all_vendor_items = [row_to_item_summary(r) for r in vendor_rows]
    waiting_on_you = [i for i in all_vendor_items if i.get("status") == "WAITING_ON_YOU"]
    review_needed = [i for i in all_vendor_items if i.get("status") == "REVIEW"]
    stale_suppliers = [
        s["supplier"] for s in supplier_reports
        if s.get("days_since_last_activity") is not None and s["days_since_last_activity"] > 30
    ]

    # Sort supplier reports: most urgent first, then by recency
    supplier_reports.sort(
        key=lambda s: (
            STATUS_PRIORITY.get(s.get("overall_status", ""), 99),
            -(s.get("days_since_last_activity") or 0),
        )
    )

    payload = {
        "task": "pharmacy_supplier_monitor",
        "facility": "Nexadose – 1360 S Main St, Mansfield TX",
        "status": "ok",
        "mode": "workspace_supplier_status",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "inputs": {
            "work_items_csv": str(csv_path.relative_to(workspace)),
            "csv_exists": csv_path.exists(),
            "total_work_items": len(all_rows),
            "vendor_related_items": len(vendor_rows),
        },
        "summary": {
            "known_suppliers_tracked": len(KNOWN_SUPPLIERS),
            "suppliers_with_open_items": sum(1 for s in supplier_reports if s["item_count"] > 0),
            "total_vendor_items": len(vendor_rows),
            "waiting_on_you_count": len(waiting_on_you),
            "review_needed_count": len(review_needed),
            "unmatched_vendor_items": len(unmatched),
            "stale_suppliers_30d": stale_suppliers,
            "attention_required": len(waiting_on_you) > 0 or len(review_needed) > 0,
        },
        "suppliers": supplier_reports,
        "unmatched_vendor_items": unmatched,
        "notes": [
            "Supplier matching uses keyword search against canonical_key and representative_subject fields.",
            "Statuses: WAITING_ON_YOU > REVIEW > OPEN > WAITING > DONE.",
            "filing_docs_found scans filing/ and organized_attachments/ subdirectories.",
            "Deterministic read-only — no external API calls.",
        ],
    }

    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")

    # Print brief console summary
    print(f"\n=== Nexadose Supplier Monitor ===")
    print(f"Vendor work items: {len(vendor_rows)} | WAITING_ON_YOU: {len(waiting_on_you)} | REVIEW: {len(review_needed)}")
    for s in supplier_reports:
        if s["item_count"] > 0:
            age = f"{s['days_since_last_activity']}d ago" if s["days_since_last_activity"] is not None else "unknown"
            print(f"  [{s['overall_status']:16s}] {s['supplier']} — {s['item_count']} items, last: {age}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
