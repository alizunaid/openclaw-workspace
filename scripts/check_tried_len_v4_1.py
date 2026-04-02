import os, json

log = os.path.expanduser("~/.openclaw/workspace/logs/next_step_upgrades.v4_1.jsonl")
ids = {
    "9df0d1a11a","0917f03823","74e4df5937","0ba7d6758b",
    "8b19f57370","f0150f515f","a6177798b4"
}

seen = set()
for line in open(log, encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    obj = json.loads(line)
    wid = obj.get("work_item_id")
    if wid in ids:
        tried = obj.get("tried")
        tl = len(tried) if isinstance(tried, list) else tried
        print(
            wid,
            "status:", obj.get("status"),
            "root:", obj.get("root"),
            "thread_mode:", obj.get("thread_mode"),
            "tried_len:", tl
        )
        seen.add(wid)

missing = ids - seen
if missing:
    print("MISSING_LOG_ROWS_FOR:", ",".join(sorted(missing)))
