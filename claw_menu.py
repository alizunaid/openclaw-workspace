#!/usr/bin/env python3
import os, datetime, base64

WS = os.path.expanduser("~/.openclaw/workspace")
NOTES_DIR = os.path.join(WS, "notes")
INBOX = os.path.join(NOTES_DIR, "inbox.txt")

def p(s=""):
    print(s, flush=True)

def ensure_paths():
    os.makedirs(NOTES_DIR, exist_ok=True)
    if not os.path.exists(INBOX):
        open(INBOX, "a", encoding="utf-8").close()

def tail_lines(path, n=10):
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        return "".join(lines[-n:]) if lines else "(empty)\n"
    except FileNotFoundError:
        return "(missing)\n"

ensure_paths()

while True:
    p("\n=== OpenClaw Assistant Menu (workspace only) ===")
    p("1) List workspace files")
    p("2) Capture note to notes/inbox.txt")
    p("3) Show last 10 inbox notes")
    p("4) Show current date/time")
    p("5) Exit (stops session)")
    p("6) Capture note (base64-safe)")
    p("------------------------------------------------")
    choice = input("Choose 1-6: ").strip()

    if choice == "1":
        for name in sorted(os.listdir(WS)):
            p(name)
    elif choice == "2":
        line = input("Note: ").strip()
        if not line:
            p("Skipped (blank).")
            continue
        with open(INBOX, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now().isoformat(timespec='seconds')}] {line}\n")
        p("Saved to notes/inbox.txt")
    elif choice == "6":
        b64 = input("Base64 note: ").strip()
        if not b64:
            p("Skipped (blank).")
            continue
        try:
            line = base64.b64decode(b64).decode("utf-8").strip()
        except Exception:
            p("Invalid base64.")
            continue
        if not line:
            p("Skipped (blank).")
            continue
        with open(INBOX, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now().isoformat(timespec='seconds')}] {line}\n")
        p("Saved to notes/inbox.txt (base64)")
    elif choice == "3":
        p("\n--- last 10 inbox notes ---")
        p(tail_lines(INBOX, 10))
        p("---------------------------")
    elif choice == "4":
        p(datetime.datetime.now().isoformat(sep=" ", timespec="seconds"))
    elif choice == "5":
        p("Bye.")
        break
    else:
        p("Invalid.")

