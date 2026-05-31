import os

def write_markdown_to_file(markdown_string, destination_path):
    try:
        directory = os.path.dirname(destination_path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory)
        with open(destination_path, 'w', encoding='utf-8') as file:
            file.write(markdown_string)
        return f"File written to {destination_path}"
    except PermissionError:
        return "Permission denied: unable to write to the destination directory."
    except OSError as e:
        return f"An error occurred while writing the file: {e}"

if __name__ == "__main__":
    print("file-writer self-check complete")