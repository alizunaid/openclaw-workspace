import csv
from typing import List

def rows_to_markdown(rows: List[List[str]]) -> str:
    if not rows:
        return ""
    
    header = rows[0]
    table = [("| " + " | ".join(header) + " |"), ("| " + " | ".join(["---"] * len(header)) + " |")]
    
    for row in rows[1:]:
        table.append("| " + " | ".join(row) + " |")
    
    return "\n".join(table)

def main():
    # No-op self-check
    print("Markdown formatter is ready to convert CSV rows to markdown format.")

if __name__ == "__main__":
    main()