import sys

def read_csv_file(file_path):
    try:
        with open(file_path, 'r') as file:
            return file.read()
    except FileNotFoundError:
        print(f"Error: The file {file_path} was not found.")
        return None
    except PermissionError:
        print(f"Error: Permission denied when trying to read {file_path}.")
        return None

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("file_reader.py is functioning correctly.")
        sys.exit(0)
    
    file_content = read_csv_file(sys.argv[1])
    if file_content is not None:
        print(file_content)