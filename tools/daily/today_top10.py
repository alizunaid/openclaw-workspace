from datetime import datetime, timezone
import argparse
import csv
from operator import itemgetter

# Priority weights — tunable without editing logic.
PERMITS_INSPECTIONS_WEIGHT = 3
ENGINEERING_OR_CLEANROOM_WEIGHT = 2
STUCK_OVER_SEVEN_DAYS_WEIGHT = 2
VENDORS_EQUIPMENT_FINANCE_LEGAL_WEIGHT = 1

# Columns the script needs from the CSV. Names are read from the header at
# parse time; if any are missing the script aborts with a clear error rather
# than crashing later on a KeyError.
REQUIRED_COLUMNS = (
    'status_current', 'categories_seen', 'last_activity_date',
    'representative_subject', 'next_step_current', 'work_item_id',
)


def parse_csv_file(file_path):
    with open(file_path, mode='r', newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        header = reader.fieldnames or []
        missing = [c for c in REQUIRED_COLUMNS if c not in header]
        if missing:
            raise SystemExit(
                f"ERROR: CSV {file_path!r} is missing required columns: {missing}. "
                f"Available columns: {header}"
            )
        return list(reader)

def calculate_days_stuck(row, current_datetime):
    last_activity_date = row.get('last_activity_date')
    if last_activity_date:
        try:
            last_activity_timestamp = datetime.fromisoformat(last_activity_date)
            days_stuck = (current_datetime - last_activity_timestamp).days
        except ValueError:
            return 0
    else:
        days_stuck = 0
    return max(days_stuck, 0)

def rank_items_by_priority(items):
    current_datetime = datetime.now(timezone.utc)
    ranked_items = []
    for item in items:
        score = 0
        # Split on `'; '` (semicolon-space) — CSV stores multi-category as
        # e.g. 'UNCLASSIFIED; VENDORS_EQUIPMENT'. Splitting on bare `;` would
        # leave leading whitespace in tokens and silently miss membership tests.
        categories_seen = {c.strip() for c in item.get('categories_seen', '').split(';') if c.strip()}
        if 'PERMITS_INSPECTIONS' in categories_seen:
            score += PERMITS_INSPECTIONS_WEIGHT
        if any(cat in categories_seen for cat in ['ENGINEERING', 'CLEANROOM_USP']):
            score += ENGINEERING_OR_CLEANROOM_WEIGHT
        days_stuck = calculate_days_stuck(item, current_datetime)
        if days_stuck > 7:
            score += STUCK_OVER_SEVEN_DAYS_WEIGHT
        if any(cat in categories_seen for cat in ['VENDORS_EQUIPMENT', 'FINANCE', 'LEGAL']):
            score += VENDORS_EQUIPMENT_FINANCE_LEGAL_WEIGHT
        
        ranked_items.append({
            **item,
            'score': score,
            'days_stuck': days_stuck
        })
    
    # Sort by score descending, then by stuck-duration descending as tiebreaker
    ranked_items.sort(key=itemgetter('score', 'days_stuck'), reverse=True)
    return ranked_items

def generate_markdown_report(ranked_items):
    report_lines = []
    current_datetime = datetime.now(timezone.utc).isoformat()
    report_lines.append(f'Generated {current_datetime} — {len(ranked_items)} items WAITING_ON_YOU, showing top 10 by priority.\n')
    report_lines.append('| Rank | Score | Item ID/Title | Categories | Days Stuck | Next Step |\n')
    report_lines.append('|------|-------|---------------|------------|------------|-----------|\n')
    
    for rank, item in enumerate(ranked_items[:10], start=1):
        truncated_title = item.get('representative_subject', 'No Title')[:40]
        categories = '; '.join(item['categories_seen'].split(';'))
        next_step = item['next_step_current']
        report_lines.append(f'| {rank} | {item["score"]} | {truncated_title} | {categories} | {item["days_stuck"]} | {next_step} |\n')
    
    # report_lines entries already end with `\n`; `''.join` preserves that
    # without inserting blank lines between table rows.
    return ''.join(report_lines)

def main():
    parser = argparse.ArgumentParser(description='Generate a daily top-10 report of high-priority work items.')
    parser.add_argument('--csv-path', type=str, default='/root/.openclaw/workspace/WORK_ITEMS_REGISTER.upgraded.v4_1.csv',
                        help='Path to the CSV file containing work items (default: /root/.openclaw/workspace/WORK_ITEMS_REGISTER.upgraded.v4_1.csv)')
    
    args = parser.parse_args()
    csv_path = args.csv_path
    
    work_items = parse_csv_file(csv_path)
    filtered_work_items = [item for item in work_items if item['status_current'] == 'WAITING_ON_YOU']
    ranked_work_items = rank_items_by_priority(filtered_work_items)
    
    report_md = generate_markdown_report(ranked_work_items)
    with open('/tmp/today_top10.md', mode='w', encoding='utf-8') as f:
        f.write(report_md)

if __name__ == "__main__":
    main()