import sys
import os

def read_csv_file(file_path):
    try:
        with open(file_path, 'r') as file:
            return file.read()
    except FileNotFoundError:
        return f"Error: The file at {file_path} was not found."
    except PermissionError:
        return f"Error: Permission denied when trying to read the file at {file_path}."

def main():
    if len(sys.argv) == 1:
        print("File reader subsystem is operational.")
        sys.exit(0)
    
    if len(sys.argv) != 2:
        print("Usage: python3 file_reader.py <path_to_csv>")
        sys.exit(1)
    
    csv_path = sys.argv[1]
    csv_data = read_csv_file(csv_path)
    print(csv_data)

if __name__ == "__main__":
    main()