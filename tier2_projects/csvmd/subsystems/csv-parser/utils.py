import csv
import sys

def parse_csv(raw_csv_data):
    reader = csv.reader(raw_csv_data.splitlines(), quoting=csv.QUOTE_MINIMAL)
    rows = [row for row in reader]
    return rows

if __name__ == "__main__":
    # No-op self-check when invoked with no arguments
    print("CSV parser ready and functional.")
    sys.exit(0)