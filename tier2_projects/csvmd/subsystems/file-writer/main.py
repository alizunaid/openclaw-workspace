import os
import sys

from file_writer import write_markdown_to_file

def main():
    if len(sys.argv) != 3:
        print("No operation performed. This is a no-op self-check.")
        sys.exit(0)
    
    markdown_content = sys.argv[1]
    file_path = sys.argv[2]
    
    result = write_markdown_to_file(markdown_content, file_path)
    print(result)

if __name__ == "__main__":
    main()