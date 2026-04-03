#!/usr/bin/env python3

import csv
import json

def main():
    input_file_path = 'tasks/task_registry.yaml'
    output_file_path = 'logs/work_items_summary.json'

    with open(input_file_path, 'r', newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        data = list(reader)

    category_count = {}
    status_count = {}

    for item in data:
        category = item.get('category', 'Unknown')
        status = item.get('status', 'Unknown')

        if category in category_count:
            category_count[category] += 1
        else:
            category_count[category] = 1

        if status in status_count:
            status_count[status] += 1
        else:
            status_count[status] = 1

    summary_report = {
        'total_items_per_category': category_count,
        'total_items_per_status': status_count
    }

    with open(output_file_path, 'w') as jsonfile:
        json.dump(summary_report, jsonfile, indent=4)

if __name__ == "__main__":
    raise SystemExit(main())