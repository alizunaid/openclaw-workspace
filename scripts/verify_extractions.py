#!/usr/bin/env python3
import os, sqlite3, sys

DB=os.path.expanduser("~/.openclaw/workspace/email_db/email.sqlite")

def main():
    if len(sys.argv) < 3:
        print("Usage: verify_extractions.py <email_id> <phrase>")
        sys.exit(2)

    email_id=int(sys.argv[1])
    phrase=" ".join(sys.argv[2:])

    con=sqlite3.connect(DB)
    con.row_factory=sqlite3.Row
    row=con.execute("SELECT body_text_path FROM emails WHERE id=?", (email_id,)).fetchone()
    con.close()

    if not row or not row["body_text_path"]:
        print("NO_BODY")
        sys.exit(1)

    path=row["body_text_path"]
    print("PATH:", path)
    if not os.path.exists(path):
        print("MISSING_FILE")
        sys.exit(1)

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for i,line in enumerate(f, 1):
            if phrase in line:
                print(f"FOUND line {i}: {line.strip()}")
                return

    print("NOT_FOUND")

if __name__ == "__main__":
    main()
