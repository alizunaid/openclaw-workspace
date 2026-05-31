import os
import sys

def write_markdown_to_file(markdown_content: str, file_path: str) -> str:
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w') as file:
            file.write(markdown_content)
        return f"File written successfully to {file_path}"
    except PermissionError:
        return "Permission denied: unable to write to the specified path."
    except Exception as e:
        return f"An error occurred: {str(e)}"

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("No operation performed. This is a no-op self-check.")
        sys.exit(0)
    
    markdown_content = sys.argv[1]
    file_path = sys.argv[2]
    
    result = write_markdown_to_file(markdown_content, file_path)
    print(result)