#!/usr/bin/env python3

import csv
import json
from collections import Counter
from datetime import datetime, UTC
from pathlib import Path


BUILD_KEYWORDS = [
    "1360",
    "main st",
    "mansfield",
    "clean room",
    "cleanroom",
    "usp",
    "engineering",
    "permit",
    "permits",
    "sewer",
    "septic",
    "survey",
    "construction",
    "vendor",
    "equipment",
    "fire marshall",
    "fire marshal",
]

CATEGORY_WEIGHTS = {
    "PERMITS_INSPECTIONS": 50,
    "ENGINEERING": 40,
    "VENDORS_EQUIPMENT": 30,
    "CLEANROOM_USP": 25,
    "FINANCE": 20,
    "LEGAL": 10,
    "UNCLASSIFIED": -10,
}


def parse_isoish_datetime(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def compute_priority_details(row: dict) -> dict:
    category_points = 0
    owner_points = 0
    staleness_points = 0

    categories_seen = (row.get("categories_seen") or "").strip()
    category_list = [c.strip() for c in categories_seen.split(";") if c.strip()]
    for category in category_list:
        category_points += CATEGORY_WEIGHTS.get(category, 0)

    dt = parse_isoish_datetime(row.get("last_activity_date", ""))
    if dt is not None:
        now = datetime.now(dt.tzinfo) if dt.tzinfo is not None else datetime.now()
        age_days = max((now - dt).days, 0)
        staleness_points = min(age_days, 60)

    owners_seen = (row.get("owners_seen") or "").strip()
    if "Engineer/Architect" in owners_seen:
        owner_points += 10
    if "Vendor/Sourcing" in owners_seen:
        owner_points += 8
    if "City/Authority" in owners_seen:
        owner_points += 12

    total_priority_score = category_points + staleness_points + owner_points

    return {
        "priority_score": total_priority_score,
        "priority_reason": {
            "category_points": category_points,
            "staleness_points": staleness_points,
            "owner_points": owner_points,
        },
    }


def safe_read_lines(path: Path) -> list[str]:
    if not path.exists() or not path.is_file():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def extract_top10_titles(lines: list[str]) -> list[str]:
    titles: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("### "):
            titles.append(stripped.removeprefix("### ").strip())

    return titles[:10]


def looks_build_related(row: dict) -> bool:
    haystacks = [
        row.get("representative_subject", ""),
        row.get("canonical_key", ""),
        row.get("categories_seen", ""),
        row.get("owners_seen", ""),
        row.get("next_step_current", ""),
    ]
    text = " | ".join(haystacks).lower()
    return any(keyword in text for keyword in BUILD_KEYWORDS)


def summarize_work_items(csv_path: Path) -> dict:
    if not csv_path.exists() or not csv_path.is_file():
        return {
            "exists": False,
            "row_count": 0,
            "column_names": [],
            "status_counts": {},
            "category_counts": {},
            "owner_counts": {},
            "oldest_last_activity_date": None,
            "blocker_summary": {
                "waiting_on_you_count": 0,
                "oldest_waiting_on_you_first_5": [],
                "waiting_on_you_category_counts": {},
                "waiting_on_you_owner_counts": {},
            },

        "build_focus_summary": {
            "summary": "Top current pressure is in permits/inspections and engineering, with vendor-sourcing also contributing meaningful blocker load.",
            "build_related_waiting_on_you_count": len(build_waiting_rows),
            "oldest_build_related_waiting_on_you_first_5": build_waiting_rows_by_oldest[:5],
            "highest_priority_build_related_first_5": build_waiting_rows_by_priority[:5],
            "build_related_category_counts": dict(build_waiting_category_counter),
            "build_related_owner_counts": dict(build_waiting_owner_counter),
            "do_these_first": build_waiting_rows_by_priority[:5],
        },
    }
    with csv_path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    status_counter: Counter[str] = Counter()
    category_counter: Counter[str] = Counter()
    owner_counter: Counter[str] = Counter()
    last_activity_dates: list[str] = []

    waiting_on_you_rows: list[dict] = []
    waiting_category_counter: Counter[str] = Counter()
    waiting_owner_counter: Counter[str] = Counter()

    build_waiting_rows: list[dict] = []
    build_waiting_category_counter: Counter[str] = Counter()
    build_waiting_owner_counter: Counter[str] = Counter()

    for row in rows:
        status = (row.get("status_current") or "").strip()
        categories_seen = (row.get("categories_seen") or "").strip()
        owners_seen = (row.get("owners_seen") or "").strip()
        last_activity = (row.get("last_activity_date") or "").strip()

        if status:
            status_counter[status] += 1

        if categories_seen:
            for category in [c.strip() for c in categories_seen.split(";") if c.strip()]:
                category_counter[category] += 1

        if owners_seen:
            for owner in [o.strip() for o in owners_seen.split(";") if o.strip()]:
                owner_counter[owner] += 1

        if last_activity:
            last_activity_dates.append(last_activity)

        if status == "WAITING_ON_YOU":
            blocker = {
                "work_item_id": row.get("work_item_id", ""),
                "representative_subject": row.get("representative_subject", ""),
                "next_step_current": row.get("next_step_current", ""),
                "last_activity_date": row.get("last_activity_date", ""),
                "owners_seen": row.get("owners_seen", ""),
                "categories_seen": row.get("categories_seen", ""),
            }
            waiting_on_you_rows.append(blocker)

            if categories_seen:
                for category in [c.strip() for c in categories_seen.split(";") if c.strip()]:
                    waiting_category_counter[category] += 1

            if owners_seen:
                for owner in [o.strip() for o in owners_seen.split(";") if o.strip()]:
                    waiting_owner_counter[owner] += 1

            if looks_build_related(row):
                priority_details = compute_priority_details(row)
                build_blocker = {
                    **blocker,
                    **priority_details,
                }
                build_waiting_rows.append(build_blocker)

                if categories_seen:
                    for category in [c.strip() for c in categories_seen.split(";") if c.strip()]:
                        build_waiting_category_counter[category] += 1

                if owners_seen:
                    for owner in [o.strip() for o in owners_seen.split(";") if o.strip()]:
                        build_waiting_owner_counter[owner] += 1

    oldest_last_activity_date = min(last_activity_dates) if last_activity_dates else None

    waiting_on_you_rows_sorted = sorted(
        waiting_on_you_rows,
        key=lambda r: r.get("last_activity_date", "")
    )

    build_waiting_rows_by_priority = sorted(
        build_waiting_rows,
        key=lambda r: (-int(r.get("priority_score", 0)), r.get("last_activity_date", ""))
    )

    build_waiting_rows_by_oldest = sorted(
        build_waiting_rows,
        key=lambda r: r.get("last_activity_date", "")
    )

    return {
        "exists": True,
        "row_count": len(rows),
        "column_names": fieldnames,
        "status_counts": dict(status_counter),
        "category_counts": dict(category_counter),
        "owner_counts": dict(owner_counter),
        "oldest_last_activity_date": oldest_last_activity_date,
        "blocker_summary": {
            "waiting_on_you_count": len(waiting_on_you_rows),
            "oldest_waiting_on_you_first_5": waiting_on_you_rows_sorted[:5],
            "waiting_on_you_category_counts": dict(waiting_category_counter),
            "waiting_on_you_owner_counts": dict(waiting_owner_counter),
        },

        "build_focus_summary": {
            "build_related_waiting_on_you_count": len(build_waiting_rows),
            "oldest_build_related_waiting_on_you_first_5": build_waiting_rows_by_oldest[:5],
            "highest_priority_build_related_first_5": build_waiting_rows_by_priority[:5],
            "build_related_category_counts": dict(build_waiting_category_counter),
            "build_related_owner_counts": dict(build_waiting_owner_counter),
            "do_these_first": build_waiting_rows_by_priority[:5],
        },
    }


def main() -> int:
    workspace = Path(__file__).resolve().parents[2]
    logs_dir = workspace / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    top10_path = workspace / "TODAY_TOP10.md"
    work_items_path = workspace / "WORK_ITEMS_REGISTER.upgraded.v4_1.csv"
    output_path = logs_dir / "build_priority_watch.json"

    top10_lines = safe_read_lines(top10_path)
    top10_titles = extract_top10_titles(top10_lines)
    work_items_summary = summarize_work_items(work_items_path)

    payload = {
        "task": "build_priority_watch",
        "status": "ok",
        "mode": "workspace_build_priority_summary",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "inputs": {
            "today_top10_exists": top10_path.exists(),
            "work_items_register_exists": work_items_path.exists(),
        },
        "today_top10": {
            "line_count": len(top10_lines),
            "detected_titles": top10_titles,
            "detected_title_count": len(top10_titles),
        },
        "work_items_register": work_items_summary,
        "notes": [
            "This watcher is deterministic and read-only.",
            "It extracts top item headings from TODAY_TOP10 and summarizes the upgraded work items register.",
            "It includes both a general blocker summary and a build-focused blocker summary."
        ]
    }

    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
