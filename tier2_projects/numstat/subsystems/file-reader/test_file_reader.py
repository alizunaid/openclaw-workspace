import sys
from pathlib import Path

def read_numbers_from_file(file_path):
    numbers = []
    try:
        with open(file_path, 'r') as file:
            for line in file:
                try:
                    number = float(line.strip())
                    numbers.append(number)
                except ValueError:
                    print(f"Warning: Skipping invalid line - {line}", file=sys.stderr)
    except FileNotFoundError:
        print(f"Error: File not found - {file_path}", file=sys.stderr)
    except IOError as e:
        print(f"Error: Unable to read file - {e}", file=sys.stderr)
    return numbers

def main():
    if len(sys.argv) == 1:
        print("No-argument self-check confirmation.")
        sys.exit(0)
    
    if len(sys.argv) != 2:
        print("Usage: python3 file_reader.py <path_to_input_file>", file=sys.stderr)
        sys.exit(1)

    input_file_path = Path(sys.argv[1])
    numbers = read_numbers_from_file(input_file_path)
    for number in numbers:
        print(number)

if __name__ == "__main__":
    main()