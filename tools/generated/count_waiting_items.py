#!/usr/bin/env python3

import json
import csv

def main():
    input_file = 'WORK_ITEMS_REGISTER.upgraded.v4_1.csv'
    output_file = 'logs/waiting_count.json'
    
    waiting_count = 0
    
    with open(input_file, mode='r', newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            if 'STATUS' in row and row['STATUS'] == 'WAITING_ON_YOU':
                waiting_count += 1
    
    with open(output_file, mode='w', encoding='utf-8') as jsonfile:
        json.dump({'waiting_count': waiting_count}, jsonfile)

if __name__ == "__main__":
    raise SystemExit(main())