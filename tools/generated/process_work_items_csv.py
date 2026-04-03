#!/usr/bin/env python3

import csv
import json
from collections import defaultdict

def main():
    categories_seen_summary = defaultdict(int)
    status_current_summary = defaultdict(int)

    with open('WORK_ITEMS_REGISTER.upgraded.v4_1.csv', mode='r', newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            categories_seen_summary[row['categories_seen']] += 1
            status_current_summary[row['status_current']] += 1

    summary_data = {
        'categories_seen': dict(categories_seen_summary),
        'status_current': dict(status_current_summary)
    }

    with open('logs/work_items_summary.json', mode='w', encoding='utf-8') as jsonfile:
        json.dump(summary_data, jsonfile, indent=4)

if __name__ == "__main__":
    raise SystemExit(main())