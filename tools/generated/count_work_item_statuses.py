#!/usr/bin/env python3
import csv
import json
import os

def count_item_statuses(file_path):
    status_counts = {}
    
    with open(file_path, mode='r', newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            status = row.get('status_current', None)
            if status:
                if status in status_counts:
                    status_counts[status] += 1
                else:
                    status_counts[status] = 1

    return status_counts

def main():
    input_file = 'WORK_ITEMS_REGISTER.upgraded.v4_1.csv'
    output_file = 'logs/status_counts.json'
    
    if not os.path.exists(input_file):
        raise FileNotFoundError(f"The file {input_file} does not exist.")
    
    status_counts = count_item_statuses(input_file)

    with open(output_file, 'w') as jsonfile:
        json.dump(status_counts, jsonfile, indent=4)

if __name__ == "__main__":
    raise SystemExit(main())