import sys
import os

def write_markdown_report(markdown_content, output_path):
    try:
        with open(output_path, 'w') as file:
            file.write(markdown_content)
        print(f"Markdown report successfully written to {output_path}")
    except IOError as e:
        print(f"Failed to write markdown report to {output_path}: {e}")

def main():
    if len(sys.argv) != 3:
        if len(sys.argv) == 1:
            print("file-writer subsystem is ready.")
            sys.exit(0)
        else:
            print("Usage: python3 file-writer.py <markdown_content> <output_path>")
            sys.exit(1)

    markdown_content = sys.argv[1]
    output_path = sys.argv[2]

    write_markdown_report(markdown_content, output_path)

if __name__ == "__main__":
    main()