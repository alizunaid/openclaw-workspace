import os, json, sqlite3

LOG = os.path.expanduser("~/.openclaw/workspace/logs/next_step_upgrades.v4_1.jsonl")
DB  = os.path.expanduser("~/.openclaw/workspace/email_db/email.sqlite")

TARGET = "9df0d1a11a"  # change to another wid if needed

# find the latest log row for this work_item_id (last occurrence wins)
last = None
for line in open(LOG, encoding="utf-8"):
    line=line.strip()
    if not line: 
        continue
    obj = json.loads(line)
    if obj.get("work_item_id") == TARGET:
        last = obj

if not last:
    raise SystemExit(f"No log rows found for {TARGET}")

tried = last.get("tried") or []
print("work_item_id:", TARGET)
print("status:", last.get("status"))
print("representative_subject:", last.get("representative_subject"))
print("thread_mode:", last.get("thread_mode"))
print("root:", last.get("root"))
print("tried_len:", len(tried))
print("tried_ids:", tried)

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()

for eid in tried:
    r = cur.execute(
        "SELECT id, date, subject, from_addr, body_text_path FROM emails WHERE id=?",
        (eid,)
    ).fetchone()
    if not r:
        print(eid, "NOT_FOUND_IN_DB")
        continue
    p = r["body_text_path"]
    exists = False
    size = None
    if p:
        fp = os.path.expanduser(p)
        exists = os.path.exists(fp)
        if exists:
            size = os.path.getsize(fp)
    print(
        f'{r["id"]} | {r["date"]} | {r["from_addr"]} | body_text_path={"YES" if p else "NO"} | exists={exists} | size={size} | subj={r["subject"]}'
    )
