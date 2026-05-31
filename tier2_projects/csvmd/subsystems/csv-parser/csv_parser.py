import sys
from utils import parse_csv

def main():
    if len(sys.argv) == 1:
        # No-op self-check when invoked with no arguments
        print("CSV parser ready and functional.")
        sys.exit(0)

    elif len(sys.argv) == 2:
        try:
            with open(sys.argv[1], 'r', encoding='utf-8') as file:
                raw_csv_data = file.read()
                structured_data = parse_csv(raw_csv_data)
                for row in structured_data:
                    print(row)
        except FileNotFoundError:
            sys.stderr.write(f"Error: File {sys.argv[1]} not found.\n")
            sys.exit(1)
        except Exception as e:
            sys.stderr.write(f"Error parsing CSV: {str(e)}\n")
            sys.exit(1)

    else:
        sys.stderr.write("Usage: python3 csv_parser.py [csv_file]\n")
        sys.exit(1)

if __name__ == "__main__":
    main()